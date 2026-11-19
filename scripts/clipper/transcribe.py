"""faster-whisper transcription with word timestamps."""
from __future__ import annotations

import os
import time
from pathlib import Path

from .util import ClipperError, log, read_json, write_json

# Rough real-time factors (processing seconds per audio second) used for estimates.
# Measured: "small" int8 on an Apple M2 CPU took 170 s for 355 s of speech (~0.48).
CPU_FACTOR = {"tiny": 0.1, "base": 0.2, "small": 0.5, "medium": 1.3, "large-v3": 2.6,
              "large-v2": 2.6, "distil-large-v3": 0.9, "turbo": 1.0, "large-v3-turbo": 1.0}
GPU_DIVISOR = 8.0


def cuda_available() -> bool:
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def pick_device(cfg: dict) -> tuple[str, str]:
    dev = (cfg.get("whisper_device") or "auto").lower()
    if dev == "auto":
        dev = "cuda" if cuda_available() else "cpu"
    compute = "float16" if dev == "cuda" else "int8"
    return dev, compute


def estimate_seconds(duration: float, cfg: dict) -> dict:
    model = cfg.get("whisper_model") or "small"
    dev, _ = pick_device(cfg)
    f = CPU_FACTOR.get(model, 0.3)
    if dev == "cuda":
        f /= GPU_DIVISOR
    trans = duration * f + 20
    return {"transcribe_s": trans, "device": dev, "model": model}


def transcribe(wav: Path, folder: Path, cfg: dict, offset: float, duration: float,
               force: bool = False) -> dict:
    out = folder / "transcript.json"
    model_name = cfg.get("whisper_model") or "small"
    cached = read_json(out)
    if cached and not force and cached.get("offset") == offset and cached.get("segments") is not None:
        log(f"Transcript already exists (cached, model {cached.get('model')})")
        return cached

    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise ClipperError("faster-whisper isn't installed. Run /ai-clipper:setup.")

    device, compute = pick_device(cfg)
    log(f"Loading Whisper '{model_name}' on {device} ({compute}). First run downloads the model...")
    try:
        model = WhisperModel(model_name, device=device, compute_type=compute,
                             cpu_threads=max(1, (os.cpu_count() or 4)))
    except Exception as e:
        msg = str(e)
        if device == "cuda":
            log(f"CUDA failed ({msg[:120]}); falling back to CPU.")
            device, compute = "cpu", "int8"
            model = WhisperModel(model_name, device="cpu", compute_type="int8")
        else:
            raise ClipperError(f"Couldn't load Whisper model '{model_name}': {msg[-300:]}")

    lang = cfg.get("language") or None
    t0 = time.time()
    segments_out = []
    try:
        # Pass decoded samples (not the path): avoids PyAV, whose newer releases broke
        # faster-whisper's file decoding.
        from .audio import read_wav
        samples = read_wav(wav)
        segments, info = model.transcribe(samples, language=lang, word_timestamps=True,
                                          vad_filter=True, vad_parameters={"min_silence_duration_ms": 500},
                                          beam_size=5, condition_on_previous_text=False)
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
    except (MemoryError, RuntimeError) as e:
        msg = str(e).lower()
        if isinstance(e, MemoryError) or "out of memory" in msg or "alloc" in msg:
            raise ClipperError(f"Ran out of memory with the '{model_name}' model. Try a smaller one: "
                               "--model base (or tiny), or set whisper_model: base in clipper.yaml.")
        raise
    log.progress("transcribe", 100, f"done in {int(time.time() - t0)}s")
    data = {"model": model_name, "language": info.language, "offset": offset,
            "duration": duration, "segments": segments_out}
    write_json(out, data)
    log(f"Transcript: {len(segments_out)} segments, language '{info.language}'")
    return data


def all_words(transcript: dict) -> list[dict]:
    return [w for s in transcript.get("segments", []) for w in s.get("words", [])]
