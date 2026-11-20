"""Whisper transcription with word timestamps.

Backends:
  * mlx-whisper on Apple Silicon Macs (runs on the Mac's GPU, several times faster)
  * faster-whisper everywhere else (NVIDIA GPU with CUDA, or the CPU with int8)
"""
from __future__ import annotations

import importlib.util
import os
import platform
import time
import types
from pathlib import Path

from .util import ClipperError, log, read_json, write_json

# Rough real-time factors (processing seconds per audio second) used for estimates.
# Measured on an Apple M2 (8 GB), "small", 2020 s gaming video:
#   faster-whisper int8 CPU: 656 s (0.32), 1432 s (0.71) when the Mac was swapping;
#   mlx-whisper on the GPU: 176 s (0.087).
CPU_FACTOR = {"tiny": 0.1, "base": 0.2, "small": 0.5, "medium": 1.3, "large-v3": 2.6,
              "large-v2": 2.6, "distil-large-v3": 0.9, "turbo": 1.0, "large-v3-turbo": 1.0}
GPU_DIVISOR = 8.0
# Greedy decoding (beam 1) vs the default beam 5. Rough guess, not measured here.
BEAM1_FACTOR = 0.65
MLX_FACTOR = {"tiny": 0.03, "base": 0.045, "small": 0.09, "medium": 0.22, "large-v3": 0.4,
              "large-v2": 0.4, "distil-large-v3": 0.15, "turbo": 0.15, "large-v3-turbo": 0.15}
MLX_REPOS = {"tiny": "mlx-community/whisper-tiny-mlx", "base": "mlx-community/whisper-base-mlx",
             "small": "mlx-community/whisper-small-mlx", "medium": "mlx-community/whisper-medium-mlx",
             "large-v2": "mlx-community/whisper-large-v2-mlx", "large-v3": "mlx-community/whisper-large-v3-mlx",
             "turbo": "mlx-community/whisper-large-v3-turbo", "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
             "distil-large-v3": "mlx-community/distil-whisper-large-v3"}


def apple_silicon() -> bool:
    return platform.system() == "Darwin" and platform.machine() == "arm64"


def mlx_available() -> bool:
    return apple_silicon() and importlib.util.find_spec("mlx_whisper") is not None


def cuda_available() -> bool:
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def pick_device(cfg: dict) -> tuple[str, str]:
    """Returns (device, compute): ("mlx", "float16"), ("cuda", "float16") or ("cpu", "int8")."""
    dev = (cfg.get("whisper_device") or "auto").lower()
    if dev == "auto":
        if mlx_available():
            dev = "mlx"
        else:
            dev = "cuda" if cuda_available() else "cpu"
    elif dev == "mlx" and not mlx_available():
        log("whisper_device is 'mlx' but mlx-whisper isn't available (Apple Silicon only); using the CPU.")
        dev = "cpu"
    if dev == "mlx":
        return dev, "float16"
    return dev, ("float16" if dev == "cuda" else "int8")


def device_label(dev: str) -> str:
    return {"mlx": "the Mac's GPU (mlx)", "cuda": "the NVIDIA GPU", "cpu": "the CPU"}.get(dev, dev)


def estimate_seconds(duration: float, cfg: dict) -> dict:
    model = cfg.get("whisper_model") or "small"
    dev, _ = pick_device(cfg)
    if dev == "mlx":
        f = MLX_FACTOR.get(model, 0.15)
    else:
        f = CPU_FACTOR.get(model, 0.5)
        if dev == "cuda":
            f /= GPU_DIVISOR
        if int(cfg.get("whisper_beam_size") or 5) <= 1:
            f *= BEAM1_FACTOR
    trans = duration * f + 20
    return {"transcribe_s": trans, "device": dev, "model": model}


class _Progress:
    """Stands in for tqdm.tqdm inside mlx_whisper so we can report progress through log.progress."""

    def __init__(self, total=None, **_kw):
        self.total = max(1, int(total or 1))
        self.n = 0
        self.t0 = time.time()
        self.last = -5.0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def update(self, k=1):
        self.n += k
        pct = min(100.0, 100.0 * self.n / self.total)
        if pct - self.last >= 2:
            el = time.time() - self.t0
            eta = el / max(pct, 0.1) * (100 - pct)
            log.progress("transcribe", pct, f"~{int(eta // 60)}m{int(eta % 60):02d}s left")
            self.last = pct

    def close(self):
        pass


def _transcribe_mlx(samples, model_name: str, lang, offset: float) -> tuple[list[dict], str]:
    import sys
    import mlx_whisper
    # The package re-exports the transcribe() function under the submodule's name, so
    # fetch the module itself to swap its progress bar.
    mt = sys.modules["mlx_whisper.transcribe"]

    repo = MLX_REPOS.get(model_name) or (model_name if "/" in model_name
                                         else f"mlx-community/whisper-{model_name}-mlx")
    log(f"Loading Whisper '{model_name}' on {device_label('mlx')}. First run downloads the model...")
    orig = mt.tqdm
    mt.tqdm = types.SimpleNamespace(tqdm=_Progress)
    try:
        res = mlx_whisper.transcribe(samples, path_or_hf_repo=repo, language=lang, verbose=None,
                                     word_timestamps=True, condition_on_previous_text=False,
                                     hallucination_silence_threshold=2.0)
    finally:
        mt.tqdm = orig
    segments_out = []
    for seg in res.get("segments") or []:
        words = [{"start": round(float(w["start"]) + offset, 3), "end": round(float(w["end"]) + offset, 3),
                  "word": str(w["word"]).strip(), "p": round(float(w.get("probability", 1.0)), 3)}
                 for w in (seg.get("words") or []) if str(w.get("word", "")).strip()]
        text = str(seg.get("text", "")).strip()
        if not words and not text:
            continue
        segments_out.append({"start": round(float(seg["start"]) + offset, 3),
                             "end": round(float(seg["end"]) + offset, 3), "text": text, "words": words})
    return segments_out, res.get("language") or lang or ""


def _transcribe_fw(samples, model_name: str, device: str, compute: str, lang, offset: float,
                   duration: float, beam: int = 5, threads: int | None = None) -> tuple[list[dict], str]:
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise ClipperError("faster-whisper isn't installed. Run /ai-clipper:setup.")

    threads = max(1, threads or (os.cpu_count() or 4))
    log(f"Loading Whisper '{model_name}' on {device_label(device)} ({compute}"
        f"{f', {threads} threads' if device == 'cpu' else ''}, beam {beam}). First run downloads the model...")
    try:
        model = WhisperModel(model_name, device=device, compute_type=compute, cpu_threads=threads)
    except Exception as e:
        msg = str(e)
        if device == "cuda":
            log(f"CUDA failed ({msg[:120]}); falling back to CPU.")
            model = WhisperModel(model_name, device="cpu", compute_type="int8", cpu_threads=threads)
        else:
            raise ClipperError(f"Couldn't load Whisper model '{model_name}': {msg[-300:]}")

    t0 = time.time()
    segments_out = []
    segments, info = model.transcribe(samples, language=lang, word_timestamps=True,
                                      vad_filter=True, vad_parameters={"min_silence_duration_ms": 500},
                                      beam_size=beam, condition_on_previous_text=False)
    total = info.duration or duration or 1
    last_pct = -5.0
    for seg in segments:
        words = [{"start": round(w.start + offset, 3), "end": round(w.end + offset, 3),
                  "word": w.word.strip(), "p": round(w.probability, 3)}
                 for w in (seg.words or []) if w.word.strip()]
        segments_out.append({"start": round(seg.start + offset, 3), "end": round(seg.end + offset, 3),
                             "text": seg.text.strip(), "words": words})
        pct = 100.0 * seg.end / total
        if pct - last_pct >= 2:
            el = time.time() - t0
            eta = el / max(pct, 0.1) * (100 - pct)
            log.progress("transcribe", pct, f"~{int(eta // 60)}m{int(eta % 60):02d}s left")
            last_pct = pct
    return segments_out, info.language


def transcribe(wav: Path, folder: Path, cfg: dict, offset: float, duration: float,
               force: bool = False) -> dict:
    out = folder / "transcript.json"
    model_name = cfg.get("whisper_model") or "small"
    cached = read_json(out)
    if cached and not force and cached.get("offset") == offset and cached.get("segments") is not None:
        log(f"Transcript already exists (cached, model {cached.get('model')})")
        return cached

    device, compute = pick_device(cfg)
    lang = cfg.get("language") or None
    # Pass decoded samples (not the path): avoids PyAV, whose newer releases broke
    # faster-whisper's file decoding.
    from .audio import read_wav
    samples = read_wav(wav)
    t0 = time.time()
    try:
        segments_out = None
        if device == "mlx":
            try:
                segments_out, language = _transcribe_mlx(samples, model_name, lang, offset)
            except (MemoryError, ClipperError):
                raise
            except Exception as e:
                log(f"Mac GPU transcription failed ({str(e)[:160]}); falling back to the CPU (slower).")
                device, compute = "cpu", "int8"
        if segments_out is None:
            from .perf import whisper_threads
            beam = max(1, int(cfg.get("whisper_beam_size") or 5))
            segments_out, language = _transcribe_fw(samples, model_name, device, compute, lang, offset,
                                                    duration, beam=beam, threads=whisper_threads(cfg))
    except (MemoryError, RuntimeError) as e:
        msg = str(e).lower()
        if isinstance(e, MemoryError) or "out of memory" in msg or "alloc" in msg:
            raise ClipperError(f"Ran out of memory with the '{model_name}' model. Try a smaller one: "
                               "--model base (or tiny), or set whisper_model: base in clipper.yaml.")
        raise
    log.progress("transcribe", 100, f"done in {int(time.time() - t0)}s")
    data = {"model": model_name, "backend": device, "language": language, "offset": offset,
            "duration": duration, "segments": segments_out}
    write_json(out, data)
    log(f"Transcript: {len(segments_out)} segments, language '{language}' "
        f"({device_label(device)}, {int(time.time() - t0)}s)")
    return data


def all_words(transcript: dict) -> list[dict]:
    return [w for s in transcript.get("segments", []) for w in s.get("words", [])]
