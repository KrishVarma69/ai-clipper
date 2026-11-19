"""Get the source video: yt-dlp for URLs, or use a local file in place."""
from __future__ import annotations

from pathlib import Path

from .util import ClipperError, is_url, log, media_info, read_json, slugify, write_json

YTDLP_HELP = (
    "The download failed. Most of the time this is fixed by updating yt-dlp:\n"
    "    python3 clipper_cli.py setup     (or /ai-clipper:setup inside Claude Code)\n"
    "YouTube: recent yt-dlp versions need a JavaScript runtime such as deno "
    "(Mac: brew install deno, Windows: winget install DenoLand.Deno, Linux: curl -fsSL https://deno.land/install.sh | sh).\n"
    "Also check the link opens in a browser, isn't private/members-only, and isn't a live stream that's still running."
)


def _ydl_base_opts() -> dict:
    return {"quiet": True, "no_warnings": True, "noplaylist": True, "noprogress": True}


def probe(source: str) -> dict:
    """Title + duration without downloading."""
    if is_url(source):
        try:
            import yt_dlp
        except ImportError:
            raise ClipperError("yt-dlp isn't installed. Run /ai-clipper:setup.")
        try:
            with yt_dlp.YoutubeDL({**_ydl_base_opts(), "skip_download": True}) as ydl:
                info = ydl.extract_info(source, download=False)
        except Exception as e:  # yt_dlp.utils.DownloadError and friends
            raise ClipperError(f"{YTDLP_HELP}\n\nDetails: {str(e)[-400:]}")
        if info.get("_type") == "playlist":
            entries = [e for e in (info.get("entries") or []) if e]
            if not entries:
                raise ClipperError("That link is a playlist. Paste a link to a single video.")
            info = entries[0]
        return {"source": source, "title": info.get("title") or info.get("id") or "video",
                "duration": float(info.get("duration") or 0), "uploader": info.get("uploader"),
                "id": info.get("id"), "is_live": bool(info.get("is_live"))}
    p = Path(source).expanduser()
    if not p.exists():
        raise ClipperError(f"File not found: {source}")
    mi = media_info(p)
    return {"source": str(p.resolve()), "title": p.stem, "duration": mi["duration"], "uploader": None,
            "id": None, "is_live": False}


def default_out_dir(info: dict, cwd: Path) -> Path:
    return cwd / "clips-output" / slugify(info["title"])


def get_video(source: str, folder: Path, cfg: dict, start: float | None, end: float | None,
              force: bool = False) -> dict:
    """Returns meta: {video, title, duration, window:[a,b], ...}. Cached in meta.json."""
    folder.mkdir(parents=True, exist_ok=True)
    meta_path = folder / "meta.json"
    meta = read_json(meta_path, {}) or {}
    want = {"source": source, "start": start, "end": end}
    if not force and meta.get("request") == want and Path(meta.get("video", "")).exists():
        log(f"Video already prepared: {meta['video']} (cached)")
        return meta

    if is_url(source):
        info = probe(source)
        if info.get("is_live"):
            raise ClipperError("That stream is still live. Wait until it ends and the VOD is available.")
        out = folder / "source.mp4"
        for old in folder.glob("source.*"):
            old.unlink()
        _download(source, folder, cfg, start, end)
        if not out.exists():
            cands = sorted(folder.glob("source.*"))
            if not cands:
                raise ClipperError(YTDLP_HELP)
            out = cands[0]
        mi = media_info(out)
        window = [0.0, mi["duration"]]
        offset = float(start or 0) if (start is not None or end is not None) else 0.0
        meta = {"request": want, "source": source, "video": str(out.resolve()), "title": info["title"],
                "uploader": info.get("uploader"), "duration": mi["duration"], "window": window,
                "source_offset": offset, "width": mi.get("width"), "height": mi.get("height"),
                "has_audio": mi["has_audio"]}
    else:
        p = Path(source).expanduser().resolve()
        if not p.exists():
            raise ClipperError(f"File not found: {source}")
        mi = media_info(p)
        if not mi.get("has_video"):
            raise ClipperError(f"{p.name} has no video stream.")
        a = max(0.0, float(start or 0))
        b = min(mi["duration"], float(end)) if end is not None else mi["duration"]
        if b - a < 5:
            raise ClipperError("The time range is too short (or start is after the end of the video).")
        meta = {"request": want, "source": str(p), "video": str(p), "title": p.stem, "uploader": None,
                "duration": mi["duration"], "window": [a, b], "source_offset": 0.0,
                "width": mi.get("width"), "height": mi.get("height"), "has_audio": mi["has_audio"]}
        log(f"Using local file: {p}")
    if not meta["has_audio"]:
        raise ClipperError("This video has no audio track, so there's nothing to transcribe.")
    write_json(meta_path, meta)
    return meta


def _download(url: str, folder: Path, cfg: dict, start, end) -> None:
    import yt_dlp

    h = int(cfg.get("max_height") or 1080)
    fmt = (f"bv*[height<={h}][ext=mp4]+ba[ext=m4a]/b[height<={h}][ext=mp4]/"
           f"bv*[height<={h}]+ba/b[height<={h}]/b")

    def hook(d):
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            if total:
                log.progress("download", 100.0 * done / total, d.get("_speed_str", "").strip())
        elif d.get("status") == "finished":
            log.progress("download", 100.0, "merging")

    opts = {**_ydl_base_opts(), "format": fmt, "merge_output_format": "mp4",
            "outtmpl": str(folder / "source.%(ext)s"), "progress_hooks": [hook],
            "concurrent_fragment_downloads": 4, "retries": 5}
    if start is not None or end is not None:
        from yt_dlp.utils import download_range_func
        opts["download_ranges"] = download_range_func(None, [(float(start or 0), float(end) if end else float("inf"))])
        opts["force_keyframes_at_cuts"] = True
    log(f"Downloading {url} (max {h}p)...")
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
    except Exception as e:
        raise ClipperError(f"{YTDLP_HELP}\n\nDetails: {str(e)[-400:]}")
