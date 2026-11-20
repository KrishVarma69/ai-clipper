#!/usr/bin/env python3
"""End-to-end test for AI Clipper. Not part of the plugin runtime.

    python3 tests/e2e_test.py [--keep] [--lite] [--workdir "/tmp/clipper test"]

Creates a 3-minute synthetic video (test pattern + tone with a loud section), a fake
transcript.json with word timestamps, then runs prepare -> transcript -> render for all
three reframe modes, heuristic mode, batch mode and the LLM JSON parser.
The work folder name contains spaces on purpose.
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
import tempfile
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "clipper_cli.py"
sys.path.insert(0, str(ROOT / "scripts"))
import setup_env  # noqa: E402

FF = setup_env.ffmpeg_bin("ffmpeg")
FP = setup_env.ffmpeg_bin("ffprobe")
PY = sys.executable

SENTENCES = [
    "Welcome back to the show everybody.", "Today we have a crazy story for you.",
    "I never thought this would happen to me.", "So I walk into the office on my first day.",
    "And my boss looks at me and says you're fired.", "I was shocked honestly.",
    "Nobody had ever told me the truth about that company.", "That is the biggest mistake I ever made.",
    "Let me tell you about the money.", "We made a million dollars in one week.",
    "Then we lost it all the next month.", "Do you know how that feels?",
    "This episode is brought to you by our sponsor.", "Use code CLIP for ten percent off.",
    "Anyway back to the story.", "My friend said this is insane.", "We started laughing so hard.",
    "Haha that was the best day ever.", "But then everything went wrong.", "I lied to my family about it.",
    "That was the worst decision of my life.", "Would you have done the same thing?",
]

failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  PASS " if cond else "  FAIL ") + msg, flush=True)
    if not cond:
        failures.append(msg)


def run(args: list[str], cwd: Path, expect_ok: bool = True, env=None) -> subprocess.CompletedProcess:
    print("  $ clipper_cli.py " + " ".join(args), flush=True)
    p = subprocess.run([PY, str(CLI)] + args, cwd=str(cwd), capture_output=True, text=True, env=env)
    if expect_ok and p.returncode != 0:
        print(p.stdout[-2000:])
        print(p.stderr[-2000:])
        failures.append(f"command failed: {' '.join(args)}")
    return p


def make_video(path: Path, seconds: int = 180, size: str = "1920x1080", color: str | None = None) -> None:
    src = f"testsrc2=size={size}:rate=30:duration={seconds}" if not color else \
        f"color=c={color}:size={size}:rate=30:duration={seconds}"
    # Tone is quiet except 60-70 s (loud), so loudness peaks are marked there.
    subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", src,
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
                    "-filter:a", "volume='if(between(t,60,70),1.0,0.08)':eval=frame",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-shortest", str(path)], check=True)


def fake_transcript(seconds: int = 180) -> dict:
    segs, t, i = [], 1.0, 0
    while t < seconds - 4:
        sent = SENTENCES[i % len(SENTENCES)]
        words, ws = [], t
        for w in sent.split():
            dur = 0.25 + 0.03 * len(w)
            words.append({"start": round(ws, 3), "end": round(ws + dur, 3), "word": w, "p": 0.95})
            ws += dur + 0.08
        segs.append({"start": words[0]["start"], "end": words[-1]["end"], "text": sent, "words": words})
        t = ws + (0.9 if i % 4 == 3 else 0.35)
        i += 1
    return {"model": "fake", "language": "en", "offset": 0.0, "duration": seconds, "segments": segs}


def probe(path: Path) -> dict:
    out = subprocess.run([FP, "-v", "error", "-print_format", "json", "-show_streams", "-show_format",
                          str(path)], capture_output=True, text=True)
    return json.loads(out.stdout)


def check_clip(path: Path, label: str) -> None:
    check(path.exists() and path.stat().st_size > 10000, f"{label}: {path.name} exists")
    if not path.exists():
        return
    info = probe(path)
    v = [s for s in info["streams"] if s["codec_type"] == "video"]
    a = [s for s in info["streams"] if s["codec_type"] == "audio"]
    check(bool(v) and v[0]["width"] == 1080 and v[0]["height"] == 1920, f"{label}: 1080x1920")
    check(bool(a), f"{label}: has audio")
    fr = v[0].get("r_frame_rate", "0/1") if v else "0/1"
    n, d = fr.split("/")
    check(abs(float(n) / float(d) - 30) < 0.1, f"{label}: 30 fps ({fr})")


class _HTMLCheck(HTMLParser):
    def __init__(self):
        super().__init__()
        self.videos = 0
        self.json_ok = False
        self._in_data = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "script" and a.get("id") == "data":
            self._in_data = True

    def handle_data(self, data):
        if self._in_data:
            try:
                d = json.loads(data)
                self.json_ok = True
                self.videos = len(d["clips"])
            except ValueError:
                pass
            self._in_data = False


def check_outputs(folder: Path, n: int, label: str) -> list[dict]:
    clips = sorted((folder / "clips").glob("*.mp4"))
    check(len(clips) == n, f"{label}: {n} clips rendered (got {len(clips)})")
    for c in clips:
        check_clip(c, label)
    with open(folder / "post_queue.csv", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    check(len(rows) == n * 3, f"{label}: post_queue.csv has {n}x3 rows (got {len(rows)})")
    check(all(r["file"] and r["caption"] and r["suggested_post_time"] for r in rows), f"{label}: csv rows filled")
    for r in rows:
        check((folder / r["file"]).exists(), f"{label}: csv file path resolves ({r['file']})")
        break
    caps = [r["caption"] + r["mentions_hashtags"] for r in rows]
    check(len(set(caps)) == len(caps), f"{label}: no two posts read the same")
    hp = _HTMLCheck()
    hp.feed((folder / "review.html").read_text(encoding="utf-8"))
    check(hp.json_ok and hp.videos == n, f"{label}: review.html data valid with {n} clips")
    check((folder / "captions.txt").exists() and (folder / "run_log.txt").exists(), f"{label}: captions.txt + run_log.txt")
    return json.loads((folder / "clips.json").read_text(encoding="utf-8"))["clips"]


def frame(video: Path, t: float, out: Path) -> None:
    subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-y", "-ss", str(t), "-i", str(video),
                    "-frames:v", "1", "-vf", "scale=360:640", str(out)], check=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--workdir")
    ap.add_argument("--lite", action="store_true", help="run everything with performance: lite")
    a = ap.parse_args()
    base = Path(a.workdir) if a.workdir else Path(tempfile.mkdtemp()) / "clipper test (spaces) dir"
    if base.exists():
        shutil.rmtree(base)
    base.mkdir(parents=True)
    print(f"Work dir: {base}")
    if a.lite:
        (base / "clipper.yaml").write_text("performance: lite\n", encoding="utf-8")

    print("\n[1] check")
    p = run(["check"], base, expect_ok=False)
    print(p.stdout)
    check("STATUS: READY" in p.stdout, "dependency check is READY")

    print("\n[2] synthetic video + fake transcript")
    vid = base / "my test video.mp4"
    make_video(vid)
    folder = base / "clips-output" / "test-video"
    folder.mkdir(parents=True)
    (folder / "transcript.json").write_text(json.dumps(fake_transcript()), encoding="utf-8")

    print("\n[3] prepare (local file, cached transcript)")
    p = run(["prepare", str(vid), "--out", str(folder)], base)
    check("READY:" in p.stdout, "prepare finished")
    loud = json.loads((folder / "loudness.json").read_text())
    check(any(60 <= x < 70 for x in loud["peaks"]) and not any(x < 55 for x in loud["peaks"]),
          f"loudness peaks marked in 60-70 s ({len(loud['peaks'])} peaks)")

    print("\n[4] transcript")
    p = run(["transcript", str(folder), "--rules", "--minutes", "1"], base)
    check("=== CHUNK 1/" in p.stdout and "LOUD" in p.stdout and "SELECTION RULES" in p.stdout,
          "chunked transcript with LOUD markers and rules")
    p2 = run(["transcript", str(folder), "--chunk", "2", "--minutes", "1"], base)
    check("=== CHUNK 2/" in p2.stdout and "CHUNK 1/" not in p2.stdout, "single chunk printing")

    moments = [
        {"start": 62.4, "end": 95.0, "title": "We made a million", "score": 9, "reason": "loud + money story",
         "captions": ["They made a MILLION in a week", "\"Then we lost it all\"", "Would you risk it?"]},
        {"start": 10.0, "end": 41.0, "title": "Fired on day one", "score": 8, "reason": "story hook",
         "captions": ["Fired before lunch 😭", "\"You're fired\" on day ONE", "What would you do?"]},
        {"start": 30.0, "end": 55.0, "title": "Overlapping pick", "score": 3, "reason": "should be dropped",
         "captions": ["x", "y", "z"]},
        {"start": "2:10", "end": "2:58", "title": "Worst decision", "score": 7, "reason": "emotional",
         "captions": ["Worst decision of his life", "He lied to his family", "Would you do the same?"]},
    ]
    mpath = folder / "moments.json"
    mpath.write_text(json.dumps(moments), encoding="utf-8")

    print("\n[5] render: face mode (test pattern has no face -> blur fallback)")
    p = run(["render", str(folder), "--moments", str(mpath), "--reframe", "face"], base)
    check("overlaps" in (p.stdout + (folder / "run_log.txt").read_text()), "overlap warning logged")
    clips = check_outputs(folder, 3, "face")
    check(all(c["reframe"] == "blur" for c in clips), "face mode fell back to blur when no faces")
    check(all(20 <= c["duration"] <= 60 for c in clips), "all clip lengths within 20-60 s")
    first = clips[0]
    words = [w for s in fake_transcript()["segments"] for w in s["words"]]
    starts = [w["start"] for w in words]
    check(any(abs(first["start"] + 0.3 - s) < 0.05 for s in starts) or any(
        abs(first["start"] - (w["end"] + 0.02)) < 0.05 for w in words), "start snapped to a word boundary")

    # caption timing: a frame during a word should have caption pixels; compare to a gap frame.
    cfile = folder / first["file"]
    shots = base / "frames"
    shots.mkdir()
    in_word = next(w for w in words if w["start"] > first["start"] + 2)
    t_word = (in_word["start"] + in_word["end"]) / 2 - first["start"]
    frame(cfile, t_word, shots / "face_with_caption.png")
    frame(cfile, 0.05, shots / "face_start.png")
    print(f"  frames saved in {shots} (word '{in_word['word']}' at clip time {t_word:.2f}s)")

    print("\n[6] render: blur mode")
    p = run(["render", str(folder), "--moments", str(mpath), "--reframe", "blur", "--force"], base)
    check_outputs(folder, 3, "blur")
    frame(folder / json.loads((folder / "clips.json").read_text())["clips"][0]["file"], t_word, shots / "blur.png")

    print("\n[7] render: split mode")
    p = run(["render", str(folder), "--moments", str(mpath), "--reframe", "split",
             "--facecam-box", "0.70,0.0,0.30,0.35", "--force"], base)
    check_outputs(folder, 3, "split")
    frame(folder / json.loads((folder / "clips.json").read_text())["clips"][0]["file"], t_word, shots / "split.png")

    print("\n[8] dry run + frame grid")
    p = run(["render", str(folder), "--moments", str(mpath), "--dry-run"], base)
    check(p.stdout.count("\n") >= 3, "dry run lists snapped times")
    p = run(["frame", str(folder), "--at", "30"], base)
    check((folder / "frame_grid.jpg").exists(), "frame grid screenshot saved")

    print("\n[9] campaign mode")
    (base / "campaigns").mkdir()
    (base / "campaigns" / "acme.yaml").write_text(
        "name: acme\nbrief: Focus on money stories.\nrequired_mentions: ['@acmepod']\n"
        "required_hashtags: ['#acme', 'ad']\nextra_hashtags: ['#podcast', '#money', '#story']\n"
        "banned_words: ['sponsor']\nmax_seconds: 45\n", encoding="utf-8")
    mp2 = folder / "moments_campaign.json"
    mp2.write_text(json.dumps([moments[0], moments[1]]), encoding="utf-8")
    p = run(["render", str(folder), "--moments", str(mp2), "--campaign", "acme", "--reframe", "blur", "--force"], base)
    rows = list(csv.DictReader(open(folder / "post_queue.csv", encoding="utf-8-sig")))
    check(all("@acmepod" in r["mentions_hashtags"] and "#acme" in r["mentions_hashtags"] and "#ad" in r["mentions_hashtags"]
              for r in rows), "campaign mentions + hashtags in every post")
    cc = json.loads((folder / "clips.json").read_text())["clips"]
    check(all(c["duration"] <= 45 for c in cc), "campaign max_seconds respected")

    print("\n[10] heuristic selection (no AI)")
    p = run(["select", str(folder), "--ai", "heuristic", "--clips", "3", "--campaign", "acme"], base)
    hm = json.loads((folder / "moments.json").read_text())
    check(len(hm) >= 2, f"heuristic picked {len(hm)} moments")
    text_all = " ".join(w["word"] for w in words)
    check(not any("sponsor" in (m["captions"][0].lower() + m["title"].lower()) for m in hm), "heuristic skipped banned word / ad read")
    p = run(["render", str(folder), "--reframe", "blur", "--force", "--campaign", "acme"], base)
    check("DONE:" in p.stdout, "render from heuristic moments")

    print("\n[11] LLM JSON parsing with malformed responses (mocked)")
    sys.path.insert(0, str(ROOT / "scripts"))
    from clipper.select import llm_pick, parse_llm_json
    bad = [
        'Sure! Here are the clips:\n```json\n[{"start": 12.5, "end": 40, "title": "A", "score": 9, "reason": "r", "captions": ["a","b","c"],},]\n```\nHope it helps!',
        "<think>hmm</think>{\"clips\": [{start: '1:05', end: '1:40', title: 'B', score: 8, captions: 'one caption'}]}",
        'garbage {"start": 100, "end": 130, "title": "C"} more garbage {"start": 140, "end": 170, "title": "D"}',
        "“[{“start”: 5, “end”: 30, “title”: “Smart quotes”}]”",
    ]
    for i, b in enumerate(bad):
        try:
            r = parse_llm_json(b)
            check(len(r) >= 1 and all(isinstance(x["start"], float) for x in r), f"malformed reply {i + 1} parsed ({len(r)} moments)")
        except ValueError as e:
            check(False, f"malformed reply {i + 1} parsed ({e})")
    try:
        parse_llm_json("I can't help with that.")
        check(False, "hopeless reply raises")
    except ValueError:
        check(True, "hopeless reply raises ValueError")
    calls = []

    def fake_caller(prompt):
        calls.append(prompt)
        return "not json at all" if len(calls) == 1 else '[{"start": 20, "end": 50, "title": "Retry worked", "score": 7}]'
    from clipper.config import load_config
    cfg = load_config(base)
    tr = json.loads((folder / "transcript.json").read_text())
    got = llm_pick(tr, loud, cfg, 2, "groq", caller=fake_caller)
    check(len(calls) == 2 and got[0]["title"] == "Retry worked", "LLM retry after unreadable reply")
    check("SELECTION RULES" in calls[0] and "Strong hook" in calls[0], "LLM prompt contains selection rules")

    print("\n[12] batch mode (.txt with two local files)")
    vid2 = base / "second video.mp4"
    make_video(vid2, seconds=90, color="navy")
    links = base / "links.txt"
    links.write_text(f"# my batch\n{vid}\n{vid2}\n", encoding="utf-8")
    # Pre-seed transcripts so Whisper isn't needed on test tones.
    for v, secs in ((vid, 180), (vid2, 90)):
        d = base / "clips-output" / v.stem.replace(" ", "-")
        d.mkdir(parents=True, exist_ok=True)
        (d / "transcript.json").write_text(json.dumps(fake_transcript(secs)), encoding="utf-8")
    p = run(["run", str(links), "--clips", "2", "--ai", "heuristic", "--reframe", "blur", "--model", "tiny"], base)
    for v in (vid, vid2):
        d = base / "clips-output" / v.stem.replace(" ", "-")
        n = len(list((d / "clips").glob("*.mp4"))) if (d / "clips").exists() else 0
        check(n == 2, f"batch: 2 clips for '{v.name}' (got {n})")

    print("\n[13] friendly errors")
    p = run(["prepare", str(base / "nope.mp4")], base, expect_ok=False)
    check(p.returncode != 0 and "File not found" in p.stderr, "missing file -> friendly error")
    p = run(["render", str(folder), "--campaign", "doesnotexist"], base, expect_ok=False)
    check("not found" in p.stderr, "missing campaign -> friendly error")

    print(f"\nFrames for visual check: {shots}")
    print(f"RESULT: {'ALL PASSED' if not failures else str(len(failures)) + ' FAILED'}")
    for f in failures:
        print("  - " + f)
    if not a.keep and not failures:
        pass  # keep artifacts for inspection by default when run by hand
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
