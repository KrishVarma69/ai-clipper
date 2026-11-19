"""AI Clipper CLI (runs inside the venv)."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import __version__
from .util import ClipperError, fmt_time, is_url, log, parse_time, read_json, write_json


# ------------------------------------------------------------------ helpers
def _sources(src: str) -> list[str]:
    p = Path(src).expanduser()
    if not is_url(src) and p.suffix.lower() == ".txt" and p.exists():
        items = [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines()]
        items = [x for x in items if x and not x.startswith("#")]
        if not items:
            raise ClipperError(f"{src} has no links in it.")
        return items
    return [src]


def _cfg(a, folder: Path | None = None) -> dict:
    from .config import load_config
    over = {}
    if getattr(a, "model", None):
        over["whisper_model"] = a.model
    if getattr(a, "reframe", None):
        over["reframe"] = a.reframe
    if getattr(a, "min_seconds", None):
        over["min_seconds"] = a.min_seconds
    if getattr(a, "max_seconds", None):
        over["max_seconds"] = a.max_seconds
    if getattr(a, "language", None):
        over["language"] = a.language
    if getattr(a, "no_variation", False):
        over["variation"] = {"enabled": False}
    if getattr(a, "facecam_box", None):
        over.setdefault("split", {})["facecam_box"] = [float(x) for x in a.facecam_box.split(",")]
    if getattr(a, "gameplay_box", None):
        over.setdefault("split", {})["gameplay_box"] = [float(x) for x in a.gameplay_box.split(",")]
    campaign = getattr(a, "campaign", None)
    if not campaign and folder:
        campaign = (read_json(folder / "meta.json", {}) or {}).get("campaign")
    return load_config(Path.cwd(), campaign=campaign, overrides=over)


def _folder(path: str) -> Path:
    f = Path(path).expanduser().resolve()
    if not (f / "meta.json").exists():
        raise ClipperError(f"{f} isn't a prepared clip folder (no meta.json). Run `prepare` first.")
    return f


def _estimate_text(duration: float, cfg: dict) -> str:
    from .transcribe import device_label, estimate_seconds
    est = estimate_seconds(duration, cfg)
    t = est["transcribe_s"]
    msg = (f"Video length {fmt_time(duration)}. Transcribing with '{est['model']}' on {device_label(est['device'])} "
           f"takes roughly {max(1, round(t / 60))} min (plus download time).")
    if duration > 3600 and est["model"] not in ("tiny", "base") and est["device"] == "cpu":
        msg += (" Tip: for videos over 1 hour use --model base, or a time range like "
                "--start 1:00:00 --end 2:30:00, to go much faster.")
    return msg


# ----------------------------------------------------------------- commands
def cmd_check(a) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import setup_env
    st = setup_env.report()
    setup_env.print_report(st)
    return 0 if st["ready"] else 1


def cmd_probe(a) -> int:
    from .download import default_out_dir, probe
    cfg = _cfg(a)
    for src in _sources(a.source):
        info = probe(src)
        dur = info["duration"]
        s, e = parse_time(a.start), parse_time(a.end)
        if s is not None or e is not None:
            dur = (e if e is not None else dur) - (s or 0)
        print(json.dumps({"source": src, "title": info["title"], "duration": round(info["duration"], 1),
                          "duration_text": fmt_time(info["duration"]),
                          "folder": str(default_out_dir(info, Path.cwd())),
                          "estimate": _estimate_text(dur, cfg)}, ensure_ascii=False))
    return 0


def prepare_one(src: str, a, cfg: dict, out: Path | None) -> Path:
    from .audio import compute_loudness, extract_wav
    from .download import default_out_dir, get_video, probe
    from .transcribe import transcribe
    start, end = parse_time(a.start), parse_time(a.end)
    if out is None:
        info = probe(src)
        out = default_out_dir(info, Path.cwd())
        dur = info["duration"]
        if start is not None or end is not None:
            dur = (end if end is not None else dur) - (start or 0)
        print(_estimate_text(dur, cfg), flush=True)
    out = out.resolve()
    log.set_folder(out)
    print(f"OUTPUT_FOLDER: {out}", flush=True)
    log(f"=== prepare {src} (ai-clipper {__version__}) ===", echo=False)
    t0 = time.time()
    meta = get_video(src, out, cfg, start, end, force=a.force)
    if getattr(a, "campaign", None):
        meta["campaign"] = a.campaign
        write_json(out / "meta.json", meta)
    wav = extract_wav(meta, out, force=a.force)
    offset = meta["window"][0]
    log.progress("loudness", 0)
    loud = compute_loudness(wav, out, offset, force=a.force)
    trans = transcribe(wav, out, cfg, offset, meta["window"][1] - meta["window"][0], force=a.force)
    n_words = sum(len(s.get("words", [])) for s in trans["segments"])
    log.progress("done", 100, f"prepared in {int(time.time() - t0)}s")
    log(f"Prepared: {n_words} words, {len(loud['peaks'])} loud seconds. Folder: {out}")
    print(f"READY: {out}", flush=True)
    return out


def cmd_prepare(a) -> int:
    srcs = _sources(a.source)
    cfg = _cfg(a)
    folders = []
    for src in srcs:
        out = Path(a.out).expanduser() if a.out else None
        if out and len(srcs) > 1:
            from .download import probe
            from .util import slugify
            out = out / slugify(probe(src)["title"])
        try:
            folders.append(prepare_one(src, a, cfg, out))
        except ClipperError as e:
            if len(srcs) == 1:
                raise
            print(f"FAILED: {src}\n{e}", flush=True)
    if len(srcs) > 1:
        print("BATCH_READY: " + json.dumps([str(f) for f in folders]))
    return 0 if folders else 1


def cmd_status(a) -> int:
    f = Path(a.folder).expanduser()
    p = read_json(f / "progress.json")
    if not p:
        print("No progress yet (still starting, or wrong folder).")
        return 0
    age = int(time.time() - p.get("time", time.time()))
    print(f"{p['stage']} {p['percent']}% {p.get('note', '')} (updated {age}s ago)")
    return 0


def cmd_transcript(a) -> int:
    from .select import SELECTION_RULES, chunk_lines, format_line, transcript_lines
    f = _folder(a.folder)
    cfg = _cfg(a, f)
    trans = read_json(f / "transcript.json")
    loud = read_json(f / "loudness.json", {"peaks": []})
    meta = read_json(f / "meta.json", {})
    if not trans:
        raise ClipperError("No transcript yet. Run prepare first (it may still be running).")
    lines = transcript_lines(trans, loud)
    if not lines:
        raise ClipperError("The transcript is empty (no speech detected).")
    hours = lines[-1]["end"] >= 3600
    chunks = chunk_lines(lines, a.minutes)
    want = a.chunk
    if want in (None, "", "all"):
        idx = list(range(len(chunks)))
    else:
        k = int(want)
        if not 1 <= k <= len(chunks):
            raise ClipperError(f"Chunk {k} doesn't exist (there are {len(chunks)}).")
        idx = [k - 1]
    camp = cfg.get("campaign") or {}
    if want in (None, "", "all", "1"):
        print(f"# VIDEO: {meta.get('title')}")
        print(f"# Window: {fmt_time(meta['window'][0], hours)} - {fmt_time(meta['window'][1], hours)} | "
              f"{len(lines)} lines | {len(chunks)} chunk(s) of ~{a.minutes:g} min | "
              f"clip length {cfg['min_seconds']}-{cfg['max_seconds']}s | niche: {cfg.get('niche')}")
        if camp.get("name"):
            print(f"# CAMPAIGN: {camp['name']}")
        if camp.get("brief"):
            print("# BRIEF: " + " ".join(str(camp["brief"]).split()))
        if camp.get("banned_words"):
            print("# BANNED WORDS: " + ", ".join(camp["banned_words"]))
        if a.rules:
            print("#\n" + "\n".join("# " + r for r in SELECTION_RULES.format(
                min_s=cfg["min_seconds"], max_s=cfg["max_seconds"]).splitlines()))
        print("# Times are seconds in the video as [mm:ss] / [h:mm:ss]. 🔊 LOUD = loudness peak, 😂 LAUGH = laughter.")
    for i in idx:
        ch = chunks[i]
        print(f"\n=== CHUNK {i + 1}/{len(chunks)}  [{fmt_time(ch[0]['start'], hours)} - {fmt_time(ch[-1]['end'], hours)}] ===")
        for ln in ch:
            print(format_line(ln, hours))
    return 0


def _finish_render(f: Path, moments: list[dict], cfg: dict, a, extra_warn: list[str] | None = None) -> dict:
    from .outputs import write_outputs
    from .render import render_clips
    from .select import snap_moments
    from .transcribe import all_words
    meta = read_json(f / "meta.json")
    trans = read_json(f / "transcript.json")
    if not trans:
        raise ClipperError("No transcript.json in the folder. Run prepare first.")
    words = all_words(trans)
    n = getattr(a, "clips", None)
    clean, warns = snap_moments(moments, words, cfg, meta["window"], n=n)
    warns = (extra_warn or []) + warns
    for w in warns:
        log("WARNING: " + w)
    if not clean:
        raise ClipperError("No usable moments after checking lengths and overlaps.")
    write_json(f / "moments_final.json", clean)
    if getattr(a, "dry_run", False):
        for m in clean:
            print(f"{fmt_time(m['start'])}-{fmt_time(m['end'])} ({m['duration']:.0f}s) {m['score']} {m['title']}")
        return {"clips": clean}
    if getattr(a, "force", False):
        for old in (f / "clips").glob("*.mp4"):
            old.unlink()
    else:
        # Drop stale numbered clips from an earlier render with different picks.
        from .util import slugify
        names = {f"{i:02d}_{slugify(m['title'], 40)}.mp4" for i, m in enumerate(clean, 1)}
        for old in (f / "clips").glob("*.mp4") if (f / "clips").exists() else []:
            if old.name not in names:
                old.unlink()
    rendered = render_clips(meta, clean, words, f, cfg, force=getattr(a, "force", False))
    data = write_outputs(f, meta, rendered, cfg, warns)
    print(f"\nDONE: {len(rendered)} clips in {f / 'clips'}")
    for i, c in enumerate(rendered, 1):
        print(f"  {i:02d}. [{c['score']}] {c['title']}  ({fmt_time(c['start'])}-{fmt_time(c['end'])}, "
              f"{c['duration']:.0f}s, {c['reframe']}) - {c['reason']}")
    print(f"Review: {f / 'review.html'}")
    print(f"Queue:  {f / 'post_queue.csv'}")
    return data


def cmd_render(a) -> int:
    from .select import load_moments
    f = _folder(a.folder)
    log.set_folder(f)
    if getattr(a, "campaign", None):
        meta = read_json(f / "meta.json")
        meta["campaign"] = a.campaign
        write_json(f / "meta.json", meta)
    cfg = _cfg(a, f)
    mp = Path(a.moments) if a.moments else f / "moments.json"
    if not mp.is_absolute() and not mp.exists():
        mp = f / mp
    log(f"=== render {mp} (reframe {cfg['reframe']}) ===", echo=False)
    _finish_render(f, load_moments(mp), cfg, a)
    return 0


def cmd_select(a) -> int:
    from .select import pick_moments
    f = _folder(a.folder)
    log.set_folder(f)
    cfg = _cfg(a, f)
    n = a.clips or int(cfg.get("clips_per_video", 5))
    mode = a.ai or (cfg.get("ai") or {}).get("provider") or "heuristic"
    trans = read_json(f / "transcript.json")
    loud = read_json(f / "loudness.json", {"peaks": []})
    moments = pick_moments(trans, loud, cfg, n, mode)
    out = f / "moments.json"
    write_json(out, moments)
    print(f"Wrote {len(moments)} moments to {out}")
    return 0


def cmd_run(a) -> int:
    from .select import pick_moments
    srcs = _sources(a.source)
    cfg = _cfg(a)
    n = a.clips or int(cfg.get("clips_per_video", 5))
    mode = a.ai or (cfg.get("ai") or {}).get("provider") or "heuristic"
    ok = 0
    for src in srcs:
        try:
            out = Path(a.out).expanduser() if (a.out and len(srcs) == 1) else None
            f = prepare_one(src, a, cfg, out)
            trans = read_json(f / "transcript.json")
            loud = read_json(f / "loudness.json", {"peaks": []})
            moments = pick_moments(trans, loud, cfg, n * 2 if mode != "heuristic" else n, mode)
            write_json(f / "moments.json", moments)
            a.clips = n
            _finish_render(f, moments, cfg, a)
            ok += 1
        except ClipperError as e:
            if len(srcs) == 1:
                raise
            print(f"FAILED: {src}\n{e}", flush=True)
    return 0 if ok else 1


def cmd_frame(a) -> int:
    import cv2
    f = _folder(a.folder)
    meta = read_json(f / "meta.json")
    t = parse_time(a.at) if a.at else (meta["window"][0] + meta["window"][1]) / 2
    cap = cv2.VideoCapture(meta["video"])
    cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
    ok, img = cap.read()
    cap.release()
    if not ok:
        raise ClipperError("Couldn't read a frame at that time.")
    h, w = img.shape[:2]
    for k in range(1, 20):
        frac = k / 20
        major = k % 2 == 0
        col = (0, 255, 255) if major else (0, 160, 160)
        th = 2 if major else 1
        x, y = int(w * frac), int(h * frac)
        cv2.line(img, (x, 0), (x, h), col, th)
        cv2.line(img, (0, y), (w, y), col, th)
        if major:
            lab = f"{frac:.1f}"
            cv2.putText(img, lab, (x + 4, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 4)
            cv2.putText(img, lab, (x + 4, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
            cv2.putText(img, lab, (4, y - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 4)
            cv2.putText(img, lab, (4, y - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
    out = f / "frame_grid.jpg"
    cv2.imwrite(str(out), img)
    print(f"Saved {out} (frame at {fmt_time(t)}). Gridlines every 0.05; labels every 0.1.")
    print("Read off the facecam's left, top, width and height as fractions, e.g. --facecam-box 0.70,0.02,0.28,0.30")
    return 0


# -------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="clipper_cli.py", description="AI Clipper: long video -> captioned 9:16 clips")
    ap.add_argument("--version", action="version", version=f"ai-clipper {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, clips=True):
        p.add_argument("--campaign", help="campaign name (./campaigns/<name>.yaml)")
        p.add_argument("--model", help="whisper model: tiny, base, small, medium, large-v3")
        p.add_argument("--language", help="spoken language code, e.g. en")
        p.add_argument("--reframe", choices=["face", "blur", "split"])
        p.add_argument("--facecam-box", help="split mode: left,top,width,height fractions")
        p.add_argument("--gameplay-box", help="split mode: left,top,width,height fractions")
        p.add_argument("--min-seconds", type=float)
        p.add_argument("--max-seconds", type=float)
        p.add_argument("--no-variation", action="store_true", help="turn off anti-spam variation")
        p.add_argument("--force", action="store_true", help="redo cached steps")
        if clips:
            p.add_argument("--clips", type=int, help="number of clips")

    p = sub.add_parser("check", help="check dependencies")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("probe", help="show title, duration and time estimate without downloading")
    p.add_argument("source")
    p.add_argument("--start")
    p.add_argument("--end")
    common(p)
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("prepare", help="download, extract audio, transcribe, loudness")
    p.add_argument("source", help="URL, local video file, or a .txt file with one link per line")
    p.add_argument("--out", help="output folder (default ./clips-output/<title>)")
    p.add_argument("--start", help="only this part: start time (e.g. 1:00:00)")
    p.add_argument("--end", help="only this part: end time (e.g. 2:30:00)")
    common(p)
    p.set_defaults(func=cmd_prepare)

    p = sub.add_parser("status", help="show progress of a running prepare/render")
    p.add_argument("folder")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("transcript", help="print the compact transcript for picking moments")
    p.add_argument("folder")
    p.add_argument("--chunk", help="chunk number to print (1-based) or 'all' (default)")
    p.add_argument("--minutes", type=float, default=10.0, help="chunk size in minutes (default 10)")
    p.add_argument("--rules", action="store_true", help="also print the selection rules")
    common(p)
    p.set_defaults(func=cmd_transcript)

    p = sub.add_parser("select", help="pick moments without Claude (heuristic or external LLM)")
    p.add_argument("folder")
    p.add_argument("--ai", help="heuristic, groq, gemini or openai")
    common(p)
    p.set_defaults(func=cmd_select)

    p = sub.add_parser("render", help="render clips from moments.json")
    p.add_argument("folder")
    p.add_argument("--moments", help="moments JSON file (default <folder>/moments.json)")
    p.add_argument("--dry-run", action="store_true", help="only show the snapped times")
    common(p)
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("run", help="standalone: prepare + select + render in one go")
    p.add_argument("source")
    p.add_argument("--out")
    p.add_argument("--start")
    p.add_argument("--end")
    p.add_argument("--ai", help="heuristic, groq, gemini or openai")
    common(p)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("frame", help="save a screenshot with a grid (to find split-screen boxes)")
    p.add_argument("folder")
    p.add_argument("--at", help="time, e.g. 12:30")
    p.set_defaults(func=cmd_frame)
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    a = ap.parse_args(argv)
    try:
        return a.func(a) or 0
    except ClipperError as e:
        print(f"\nERROR: {e}", file=sys.stderr, flush=True)
        log(f"ERROR: {e}", echo=False)
        return 2
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
        return 130
