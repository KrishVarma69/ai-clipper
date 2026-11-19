"""Reframe to 1080x1920 and encode each clip in one ffmpeg pass."""
from __future__ import annotations

import random
import statistics
from pathlib import Path

from .captions import write_ass
from .util import ClipperError, log, need_ffmpeg, run_ffmpeg, slugify

OUT_W, OUT_H = 1080, 1920


def _even(x: float) -> int:
    v = int(round(x))
    return v - (v % 2)


# ------------------------------------------------------------- face detection
def face_track(video: str, start: float, end: float, src_w: int, src_h: int, cfg: dict) -> dict:
    """Sample frames, return {'ratio': share of samples with a face, 'xs': [(t_rel, cx_px)]}."""
    import cv2
    every = float((cfg.get("face") or {}).get("sample_every", 0.5))
    if not hasattr(cv2, "CascadeClassifier") or not hasattr(cv2, "data"):
        log("This OpenCV build has no Haar face detector (re-run setup to install OpenCV 4.x); using blur.")
        return {"ratio": 0.0, "xs": []}
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    cap = cv2.VideoCapture(video)
    if not cap.isOpened():
        return {"ratio": 0.0, "xs": []}
    det_w = 640
    scale = det_w / max(1, src_w)
    samples, hits = 0, []
    t = start
    cap.set(cv2.CAP_PROP_POS_MSEC, start * 1000)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    step_frames = max(1, int(round(every * fps)))
    frame_i = 0
    while t < end:
        ok, frame = cap.read()
        if not ok:
            break
        if frame_i % step_frames == 0:
            samples += 1
            small = cv2.resize(frame, (det_w, max(2, int(frame.shape[0] * scale))))
            gray = cv2.equalizeHist(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
            min_side = max(24, int(small.shape[0] * 0.08))
            faces = cascade.detectMultiScale(gray, scaleFactor=1.15, minNeighbors=6, minSize=(min_side, min_side))
            if len(faces):
                x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
                hits.append((t - start, (x + w / 2) / scale))
        frame_i += 1
        t = start + frame_i / fps
    cap.release()
    return {"ratio": (len(hits) / samples) if samples else 0.0, "xs": hits}


def face_crop_x_expr(track: dict, src_w: int, crop_w: int, dur: float, min_shot: float) -> str:
    """Piecewise-constant crop x (a 'camera cut' when the main face moves a lot)."""
    xs = track["xs"]
    if not xs:
        return str(max(0, (src_w - crop_w) // 2))
    # Median filter (window 5) to smooth jitter.
    vals = [x for _, x in xs]
    sm = [statistics.median(vals[max(0, i - 2):i + 3]) for i in range(len(vals))]
    times = [t for t, _ in xs]
    # Build shots: a new shot when the face center moves > 15% of the width and stays there.
    tol = src_w * 0.15
    shots = [[times[0], [sm[0]]]]
    for t, x in zip(times[1:], sm[1:]):
        cur_med = statistics.median(shots[-1][1])
        if abs(x - cur_med) > tol:
            shots.append([t, [x]])
        else:
            shots[-1][1].append(x)
    # Merge shots shorter than min_shot into the previous one.
    merged = []
    for i, (t0, pts) in enumerate(shots):
        t1 = shots[i + 1][0] if i + 1 < len(shots) else dur
        if merged and (t1 - t0) < min_shot:
            merged[-1][1].extend(pts)
        else:
            merged.append([t0, list(pts)])
    if merged:
        merged[0][0] = 0.0

    def clamp(cx):
        return int(max(0, min(src_w - crop_w, cx - crop_w / 2)))

    xs_px = [(t0, clamp(statistics.median(pts))) for t0, pts in merged]
    # Nested if(): if(lt(t,T1),X0,if(lt(t,T2),X1,...X_last))
    expr = str(xs_px[-1][1])
    for i in range(len(xs_px) - 2, -1, -1):
        expr = f"if(lt(t\\,{xs_px[i + 1][0]:.2f})\\,{xs_px[i][1]}\\,{expr})"
    return expr


# --------------------------------------------------------------- filtergraphs
def _fill(label_in: str, w: int, h: int, label_out: str) -> str:
    return (f"[{label_in}]scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h},setsar=1[{label_out}]")


def build_filter(mode: str, src_w: int, src_h: int, cfg: dict, zoom: float, crop_x_expr: str | None,
                 ass_name: str | None, fps: int) -> str:
    chains = []
    if mode == "face":
        crop_h = src_h
        crop_w = _even(min(src_w, src_h * 9 / 16))
        chains.append(f"[0:v]crop={crop_w}:{crop_h}:x={crop_x_expr or (src_w - crop_w) // 2}:y=0,"
                      f"scale={OUT_W}:{OUT_H},setsar=1[base]")
    elif mode == "blur":
        strength = int(cfg.get("blur_strength", 25))
        chains.append("[0:v]split=2[bgsrc][fgsrc]")
        chains.append(f"[bgsrc]scale={OUT_W // 4}:{OUT_H // 4}:force_original_aspect_ratio=increase,"
                      f"crop={OUT_W // 4}:{OUT_H // 4},boxblur={max(1, strength // 4)}:2,"
                      f"scale={OUT_W}:{OUT_H},setsar=1[bg]")
        chains.append(f"[fgsrc]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=decrease,setsar=1[fg]")
        chains.append("[bg][fg]overlay=(W-w)/2:(H-h)/2[base]")
    elif mode == "split":
        sp = cfg.get("split") or {}
        share = float(sp.get("facecam_share", 0.35))
        top_h = _even(OUT_H * min(0.8, max(0.15, share)))
        bot_h = OUT_H - top_h

        def box(b):
            l, t, w, h = [float(v) for v in b]
            w, h = max(0.02, min(1 - l, w)), max(0.02, min(1 - t, h))
            return (_even(src_w * w), _even(src_h * h), int(src_w * l), int(src_h * t))

        fw, fh, fx, fy = box(sp.get("facecam_box", [0, 0, 0.25, 0.3]))
        gw, gh, gx, gy = box(sp.get("gameplay_box", [0, 0, 1, 1]))
        chains.append("[0:v]split=2[cam][game]")
        chains.append(f"[cam]crop={fw}:{fh}:{fx}:{fy}[camc]")
        chains.append(_fill("camc", OUT_W, top_h, "top"))
        chains.append(f"[game]crop={gw}:{gh}:{gx}:{gy}[gamec]")
        chains.append(_fill("gamec", OUT_W, bot_h, "bot"))
        chains.append("[top][bot]vstack=inputs=2[base]")
    else:
        raise ClipperError(f"Unknown reframe mode {mode}")
    last = "base"
    if zoom > 1.001:
        zw, zh = _even(OUT_W * zoom), _even(OUT_H * zoom)
        chains.append(f"[{last}]scale={zw}:{zh},crop={OUT_W}:{OUT_H}[zoomed]")
        last = "zoomed"
    tail = f"[{last}]fps={fps},format=yuv420p"
    if ass_name:
        tail += f",subtitles={ass_name}"
    chains.append(tail + "[v]")
    return ";".join(chains)


# ----------------------------------------------------------------------- main
def render_clips(meta: dict, moments: list[dict], words: list[dict], folder: Path, cfg: dict,
                 force: bool = False) -> list[dict]:
    video = meta["video"]
    src_w, src_h = int(meta.get("width") or 1920), int(meta.get("height") or 1080)
    clips_dir = folder / "clips"
    work = folder / "work"
    clips_dir.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    vcfg = cfg.get("video") or {}
    fps = int(vcfg.get("fps", 30))
    cap_cfg = cfg.get("captions") or {}
    var = cfg.get("variation") or {}
    use_var = bool(var.get("enabled", True))
    mode_cfg = cfg.get("reframe", "face")
    need_ffmpeg(captions=bool(cap_cfg.get("enabled", True)))
    results = []
    for i, m in enumerate(moments, 1):
        rng = random.Random(f"{slugify(meta.get('title', ''))}-{i}-{m['start']}")
        name = f"{i:02d}_{slugify(m['title'], 40)}"
        out = clips_dir / f"{name}.mp4"
        dur = m["end"] - m["start"]
        mode = mode_cfg
        note = ""
        crop_expr = None
        if mode == "face" or (mode != "split" and src_w / max(1, src_h) <= 9 / 16 + 0.01):
            if src_w / max(1, src_h) <= 9 / 16 + 0.01:
                mode, note = "blur", "source is already vertical"
            else:
                track = face_track(video, m["start"], m["end"], src_w, src_h, cfg)
                min_ratio = float((cfg.get("face") or {}).get("min_face_ratio", 0.3))
                if track["ratio"] < min_ratio:
                    mode = "blur"
                    note = f"faces in only {track['ratio']:.0%} of frames -> blur fallback"
                else:
                    crop_w = _even(min(src_w, src_h * 9 / 16))
                    crop_expr = face_crop_x_expr(track, src_w, crop_w, dur,
                                                 float((cfg.get("face") or {}).get("min_shot_seconds", 2.0)))
                    note = f"face tracked in {track['ratio']:.0%} of frames"
        zoom = 1.0 + rng.uniform(0, max(0.0, float(var.get("zoom_max", 1.04)) - 1.0)) if use_var else 1.0
        size = float(cap_cfg.get("font_size", 82))
        pos_y = float(cap_cfg.get("position_y", 0.70))
        if use_var:
            size *= 1 + rng.uniform(-1, 1) * float(var.get("font_size_jitter", 0.08))
            pos_y += rng.uniform(-1, 1) * float(var.get("position_jitter", 0.03))
        ass_name = None
        if cap_cfg.get("enabled", True):
            ass_name = f"{name}.ass"
            write_ass(work / ass_name, words, m["start"], m["end"], cap_cfg, font_size=size, position_y=pos_y)
        if out.exists() and not force and out.stat().st_size > 1000:
            log(f"[{i}/{len(moments)}] {out.name} exists (cached)")
        else:
            log(f"[{i}/{len(moments)}] Rendering {out.name} ({dur:.0f}s, {mode}{', ' + note if note else ''})")
            fg = build_filter(mode, src_w, src_h, cfg, zoom, crop_expr, ass_name, fps)
            # Run with the work folder as cwd and the .ass name relative: avoids the
            # Windows drive-letter escaping problem in the subtitles filter.
            run_ffmpeg(["-ss", f"{m['start']:.3f}", "-i", video, "-t", f"{dur:.3f}",
                        "-filter_complex", fg, "-map", "[v]", "-map", "0:a:0?",
                        "-c:v", "libx264", "-preset", str(vcfg.get("preset", "veryfast")),
                        "-crf", str(vcfg.get("crf", 20)), "-profile:v", "high", "-r", str(fps),
                        "-c:a", "aac", "-b:a", str(vcfg.get("audio_bitrate", "160k")), "-ar", "48000",
                        "-movflags", "+faststart", str(out.resolve())],
                       cwd=work, what=f"Rendering clip {i}")
            log.progress("render", 100.0 * i / len(moments), out.name)
        results.append({**m, "file": f"clips/{out.name}", "reframe": mode, "reframe_note": note,
                        "zoom": round(zoom, 3), "caption_size": round(size), "caption_y": round(pos_y, 3)})
    return results
