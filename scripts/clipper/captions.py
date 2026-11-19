"""Build an .ass subtitle file per clip from word timestamps."""
from __future__ import annotations

import re
from pathlib import Path

W, H = 1080, 1920


def _ass_color(hex_rgb: str, alpha: int = 0) -> str:
    h = (hex_rgb or "#FFFFFF").lstrip("#")
    if len(h) != 6:
        h = "FFFFFF"
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _ts(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _clean(word: str, upper: bool) -> str:
    w = word.replace("\\", "").replace("{", "(").replace("}", ")").strip()
    w = re.sub(r"\s+", " ", w)
    return w.upper() if upper else w


def group_lines(words: list[dict], per_line: int, pause: float) -> list[list[dict]]:
    lines, cur = [], []
    for w in words:
        if cur and (len(cur) >= per_line or w["start"] - cur[-1]["end"] > pause
                    or re.search(r"[.!?]$", cur[-1]["word"])):
            lines.append(cur)
            cur = []
        cur.append(w)
    if cur:
        lines.append(cur)
    return lines


def build_ass(words: list[dict], clip_start: float, clip_end: float, style: dict,
              font_size: float | None = None, position_y: float | None = None) -> str:
    """words: absolute-time words; output events are relative to clip_start."""
    st = style or {}
    size = int(round(font_size or st.get("font_size", 82)))
    pos_y = float(position_y if position_y is not None else st.get("position_y", 0.70))
    margin_v = int(round(max(0.03, min(0.95, 1.0 - pos_y)) * H))
    upper = bool(st.get("uppercase", True))
    base_c = _ass_color(st.get("color", "#FFFFFF"))
    hi_c = _ass_color(st.get("highlight_color", "#FFE600"))
    out_c = _ass_color(st.get("outline_color", "#000000"))
    back_c = _ass_color("#000000", 0x80)
    bold = -1 if st.get("bold", True) else 0
    font = st.get("font", "Arial")
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{font},{size},{base_c},{hi_c},{out_c},{back_c},{bold},0,0,0,100,100,0,0,1,{st.get('outline', 7)},{st.get('shadow', 2)},2,60,60,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    inside = [w for w in words if w["end"] > clip_start and w["start"] < clip_end]
    lines = group_lines(inside, int(st.get("words_per_line", 3)), float(st.get("pause_break", 0.6)))
    dur = clip_end - clip_start
    events = []
    for li, ln in enumerate(lines):
        a = max(0.0, ln[0]["start"] - clip_start)
        nxt = lines[li + 1][0]["start"] - clip_start if li + 1 < len(lines) else dur
        b = min(dur, ln[-1]["end"] - clip_start)
        if nxt - b < 0.35:  # close small gaps so captions don't flicker
            b = min(nxt, dur)
        else:
            b = min(b + 0.15, nxt, dur)
        if b <= a:
            continue
        toks = [_clean(w["word"], upper) for w in ln]
        if not st.get("karaoke", True):
            events.append((a, b, " ".join(toks)))
            continue
        for wi, w in enumerate(ln):
            wa = a if wi == 0 else max(a, w["start"] - clip_start)
            wb = b if wi == len(ln) - 1 else max(wa, ln[wi + 1]["start"] - clip_start)
            if wb - wa < 0.01:
                continue
            parts = []
            for tj, tok in enumerate(toks):
                if tj == wi:
                    parts.append(r"{\c" + hi_c + r"\fscx108\fscy108}" + tok + r"{\c" + base_c + r"\fscx100\fscy100}")
                else:
                    parts.append(tok)
            events.append((wa, wb, " ".join(parts)))
    body = "".join(f"Dialogue: 0,{_ts(a)},{_ts(b)},Cap,,0,0,0,,{txt}\n" for a, b, txt in events)
    return header + body


def write_ass(path: Path, *args, **kwargs) -> Path:
    Path(path).write_text(build_ass(*args, **kwargs), encoding="utf-8")
    return Path(path)
