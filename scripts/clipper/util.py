"""Shared helpers: logging, progress, time parsing, ffmpeg wrappers."""
from __future__ import annotations

import datetime as _dt
import json
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from pathlib import Path


class ClipperError(Exception):
    """An error with a friendly message for the user."""


# ------------------------------------------------------------------ logging
class RunLog:
    def __init__(self, folder: Path | None = None):
        self.folder = Path(folder) if folder else None

    def set_folder(self, folder: Path) -> None:
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)

    def __call__(self, msg: str, echo: bool = True) -> None:
        line = f"[{_dt.datetime.now().strftime('%H:%M:%S')}] {msg}"
        if echo:
            print(line, flush=True)
        if self.folder:
            try:
                with open(self.folder / "run_log.txt", "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError:
                pass

    def progress(self, stage: str, pct: float, note: str = "") -> None:
        """Write progress.json (read by `clipper_cli.py status`) and print a line."""
        pct = max(0.0, min(100.0, pct))
        if self.folder:
            try:
                (self.folder / "progress.json").write_text(json.dumps(
                    {"stage": stage, "percent": round(pct, 1), "note": note, "time": time.time()}),
                    encoding="utf-8")
            except OSError:
                pass
        print(f"PROGRESS {stage} {pct:5.1f}% {note}".rstrip(), flush=True)


log = RunLog()


# ------------------------------------------------------------------- time
def parse_time(value) -> float | None:
    """'1:02:03', '62:03', '3723', '90s', 3723 -> seconds."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().lower().rstrip("s")
    if not s:
        return None
    if ":" in s:
        parts = [float(p) for p in s.split(":")]
        total = 0.0
        for p in parts:
            total = total * 60 + p
        return total
    return float(s)


def fmt_time(sec: float, force_hours: bool = False) -> str:
    sec = max(0, int(round(sec)))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h or force_hours:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def slugify(text: str, max_len: int = 50) -> str:
    text = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    text = text[:max_len].strip("-")
    return text or "video"


def is_url(s: str) -> bool:
    return bool(re.match(r"^https?://", str(s).strip(), re.I))


# ----------------------------------------------------------------- ffmpeg
def _bin(name: str) -> str | None:
    try:
        import setup_env  # scripts/ is on sys.path (stdlib-only module)
        return setup_env.ffmpeg_bin(name)
    except ImportError:
        return shutil.which(name)


def need_ffmpeg(captions: bool = False) -> None:
    if not _bin("ffmpeg") or not _bin("ffprobe"):
        raise ClipperError("ffmpeg isn't installed or isn't on PATH. Run /ai-clipper:setup "
                           "(or: Mac `brew install ffmpeg-full`, Windows `winget install Gyan.FFmpeg`, "
                           "Linux `sudo apt install ffmpeg`).")
    if captions:
        import setup_env
        if not setup_env.ffmpeg_libass_ok():
            raise ClipperError(f"Your ffmpeg ({_bin('ffmpeg')}) can't burn in captions (built without libass). "
                               "Fix: " + setup_env.install_hint_ffmpeg() +
                               "  - or set captions.enabled: false in clipper.yaml.")


def run_ffmpeg(args: list[str], cwd: Path | None = None, what: str = "ffmpeg") -> None:
    need_ffmpeg()
    cmd = [_bin("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y"] + args
    proc = subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-12:])
        raise ClipperError(f"{what} failed:\n{tail}")


def ffprobe(path: Path) -> dict:
    need_ffmpeg()
    proc = subprocess.run([_bin("ffprobe"), "-v", "error", "-print_format", "json", "-show_format",
                           "-show_streams", str(path)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise ClipperError(f"Couldn't read the video file {path}: {proc.stderr.strip()[-300:]}")
    return json.loads(proc.stdout)


def media_info(path: Path) -> dict:
    info = ffprobe(path)
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    a = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)
    dur = float(info.get("format", {}).get("duration") or 0)
    out = {"duration": dur, "has_audio": a is not None, "has_video": v is not None}
    if v:
        w, h = int(v.get("width", 0)), int(v.get("height", 0))
        rot = 0
        for sd in v.get("side_data_list", []) or []:
            if "rotation" in sd:
                rot = int(sd["rotation"])
        if abs(rot) in (90, 270):
            w, h = h, w
        out.update({"width": w, "height": h})
    return out


def read_json(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path: Path, data) -> None:
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def eprint(*a) -> None:
    print(*a, file=sys.stderr, flush=True)
