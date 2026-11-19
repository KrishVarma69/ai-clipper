"""Audio extraction (mono 16 kHz wav) and per-second loudness."""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

from .util import log, read_json, run_ffmpeg, write_json

SR = 16000


def extract_wav(meta: dict, folder: Path, force: bool = False) -> Path:
    """audio.wav covers meta['window']; its t=0 is window[0] in the video."""
    out = folder / "audio.wav"
    stamp = folder / "audio.json"
    want = {"video": meta["video"], "window": meta["window"]}
    if out.exists() and not force and read_json(stamp) == want:
        log("Audio already extracted (cached)")
        return out
    a, b = meta["window"]
    log("Extracting audio (mono 16 kHz)...")
    run_ffmpeg(["-ss", f"{a:.3f}", "-i", meta["video"], "-t", f"{b - a:.3f}", "-vn", "-ac", "1",
                "-ar", str(SR), "-c:a", "pcm_s16le", str(out)], what="Audio extraction")
    write_json(stamp, want)
    return out


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        n = w.getnframes()
        raw = w.readframes(n)
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def compute_loudness(wav: Path, folder: Path, offset: float, peak_db: float = 6.0,
                     force: bool = False) -> dict:
    """Per-second RMS in dB. Times in the result are video times (offset added)."""
    out = folder / "loudness.json"
    cached = read_json(out)
    if cached and not force and cached.get("offset") == offset:
        return cached
    x = read_wav(wav)
    secs = int(np.ceil(len(x) / SR)) if len(x) else 0
    db = []
    for i in range(secs):
        seg = x[i * SR:(i + 1) * SR]
        rms = float(np.sqrt(np.mean(seg ** 2))) if len(seg) else 0.0
        db.append(round(20 * np.log10(max(rms, 1e-5)), 1))
    arr = np.array(db) if db else np.array([-100.0])
    voiced = arr[arr > -50]
    mean = float(voiced.mean()) if len(voiced) else float(arr.mean())
    threshold = mean + peak_db
    # Also count the top 5% loudest seconds as peaks if they clear mean + 3 dB.
    top = float(np.percentile(arr, 95)) if len(arr) > 20 else threshold
    thr = min(threshold, max(top, mean + 3.0))
    peaks = [round(offset + i, 1) for i, v in enumerate(db) if v >= thr]
    data = {"offset": offset, "db": db, "mean_db": round(mean, 1), "peak_threshold_db": round(thr, 1),
            "peaks": peaks}
    write_json(out, data)
    log(f"Loudness: average {mean:.1f} dB, {len(peaks)} loud seconds marked")
    return data


def loud_seconds(loud: dict, start: float, end: float) -> int:
    """How many peak seconds fall inside [start, end] (video time)."""
    return sum(1 for p in loud.get("peaks", []) if start <= p < end)


def db_at(loud: dict, t: float) -> float:
    i = int(t - loud.get("offset", 0))
    db = loud.get("db", [])
    return db[i] if 0 <= i < len(db) else -100.0
