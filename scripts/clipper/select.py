"""Moment selection helpers.

* transcript_lines / format_chunk: the compact transcript Claude reads.
* snap_moments: clean up any moments.json (word-boundary snapping, length, overlaps).
* heuristic_pick: no-AI selection.
* llm_pick + parse_llm_json: standalone selection through an OpenAI-compatible API.
"""
from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path

from .audio import loud_seconds
from .util import ClipperError, fmt_time, log, parse_time

SELECTION_RULES = """\
SELECTION RULES
- Strong hook in the first 2 seconds: start AT the most interesting line or punchline, not the slow setup.
- Self-contained: makes sense with no context. Has a clear payoff or ending.
- Length between {min_s} and {max_s} seconds.
- Never start or end mid-sentence (times are snapped to word boundaries automatically, with 0.3 s padding before and 0.5 s after).
- Avoid ad reads/sponsor segments, dead air, repeated content, and anything containing banned words. Follow the campaign brief.
- Loud peaks (marked LOUD), laughter, strong opinions, surprising facts, conflict, emotional moments and story moments score high.
- No overlapping clips. Spread picks across the whole video, not all from one section.
- Captions: 3 distinct variants per clip (different wording and angle: e.g. a curiosity hook, a bold quote/claim, a question to the viewer). Do NOT add hashtags or @mentions yourself; the required ones are added automatically.
"""

MOMENT_SCHEMA = """\
Output a JSON array, best clip first:
[{"start": 754.2, "end": 801.0, "title": "Short punchy title", "score": 9,
  "reason": "why this will hold attention", "captions": ["variant 1", "variant 2", "variant 3"]}]
start/end are seconds in the video (decimals ok). score is 1-10."""

LAUGH_RE = re.compile(r"\b(ha(ha)+|lol|lmao|\(laugh\w*\)|\[laugh\w*\]|laughing|laughter)\b", re.I)
SENT_END_RE = re.compile(r"[.!?…][\"')\]]*$")
AD_RE = re.compile(r"(sponsor|promo code|use code|discount code|brought to you by|link in (the )?description|"
                   r"check out .* (dot com|\.com)|smash (that|the) like|subscribe)", re.I)
HOT_WORDS = re.compile(r"\b(never|always|crazy|insane|secret|truth|nobody|everyone|worst|best|million|"
                       r"billion|money|fight|hate|love|died|dead|shocked|honestly|actually|wrong|lie|"
                       r"lied|scared|biggest|craziest|wild|unbelievable|mistake|regret|fired|broke|rich)\b", re.I)


# ---------------------------------------------------------------- transcript
def transcript_lines(transcript: dict, loud: dict, max_line_s: float = 14.0) -> list[dict]:
    """Group words into readable lines of up to ~max_line_s seconds, broken at sentence ends."""
    lines, cur = [], []
    for seg in transcript.get("segments", []):
        words = seg.get("words") or []
        if not words:
            continue
        for w in words:
            if cur and (w["start"] - cur[-1]["end"] > 1.5 or w["end"] - cur[0]["start"] > max_line_s):
                lines.append(cur)
                cur = []
            cur.append(w)
            if SENT_END_RE.search(w["word"]) and w["end"] - cur[0]["start"] > 4:
                lines.append(cur)
                cur = []
    if cur:
        lines.append(cur)
    out = []
    for ws in lines:
        text = " ".join(w["word"] for w in ws).strip()
        a, b = ws[0]["start"], ws[-1]["end"]
        marks = []
        n_loud = loud_seconds(loud, math.floor(a), math.ceil(b))
        if n_loud:
            marks.append("🔊 LOUD" + ("x%d" % n_loud if n_loud > 1 else ""))
        if LAUGH_RE.search(text):
            marks.append("😂 LAUGH")
        out.append({"start": a, "end": b, "text": text, "marks": marks})
    return out


def chunk_lines(lines: list[dict], minutes: float) -> list[list[dict]]:
    if not lines:
        return []
    size = max(60.0, minutes * 60)
    chunks, cur, base = [], [], lines[0]["start"]
    for ln in lines:
        if cur and ln["start"] - base >= size:
            chunks.append(cur)
            cur, base = [], ln["start"]
        cur.append(ln)
    if cur:
        chunks.append(cur)
    return chunks


def format_line(ln: dict, hours: bool) -> str:
    m = ("  " + " ".join(ln["marks"])) if ln["marks"] else ""
    return f"[{fmt_time(ln['start'], hours)}] {ln['text']}{m}"


# ------------------------------------------------------------------ snapping
def _is_sentence_start(words: list[dict], i: int) -> bool:
    if i == 0:
        return True
    prev = words[i - 1]
    return bool(SENT_END_RE.search(prev["word"])) or words[i]["start"] - prev["end"] > 0.6


def _is_sentence_end(words: list[dict], i: int) -> bool:
    if i == len(words) - 1:
        return True
    return bool(SENT_END_RE.search(words[i]["word"])) or words[i + 1]["start"] - words[i]["end"] > 0.6


def _start_index(words, t, search=2.0):
    idx = next((i for i, w in enumerate(words) if w["end"] > t), len(words) - 1)
    best = None
    for i in range(max(0, idx - 40), min(len(words), idx + 40)):
        if abs(words[i]["start"] - t) <= search and _is_sentence_start(words, i):
            if best is None or abs(words[i]["start"] - t) < abs(words[best]["start"] - t):
                best = i
    return best if best is not None else idx


def _end_index(words, t, search=2.0):
    idx = max((i for i, w in enumerate(words) if w["start"] < t), default=0)
    best = None
    for i in range(max(0, idx - 40), min(len(words), idx + 40)):
        if abs(words[i]["end"] - t) <= search and _is_sentence_end(words, i):
            if best is None or abs(words[i]["end"] - t) < abs(words[best]["end"] - t):
                best = i
    return best if best is not None else idx


def _bounds(words, si, ei, pad_before=0.3, pad_after=0.5):
    start = words[si]["start"] - pad_before
    if si > 0:
        start = max(start, words[si - 1]["end"] + 0.02)
    end = words[ei]["end"] + pad_after
    if ei + 1 < len(words):
        end = min(end, max(words[ei + 1]["start"] - 0.05, words[ei]["end"] + 0.12))
    return max(0.0, start), end


def snap_moments(moments: list[dict], words: list[dict], cfg: dict, window: list[float] | None = None,
                 n: int | None = None) -> tuple[list[dict], list[str]]:
    """Snap to word/sentence boundaries, enforce length, drop overlaps. Returns (clean, warnings)."""
    warnings: list[str] = []
    if not words:
        raise ClipperError("The transcript has no words (silent video or no speech found).")
    min_s, max_s = float(cfg["min_seconds"]), float(cfg["max_seconds"])
    banned = [b.lower() for b in (cfg.get("campaign") or {}).get("banned_words") or [] if b]
    lo, hi = (window or [words[0]["start"], words[-1]["end"]])
    cleaned = []
    for k, m in enumerate(moments):
        try:
            s, e = parse_time(m.get("start")), parse_time(m.get("end"))
        except (TypeError, ValueError):
            s = e = None
        if s is None or e is None:
            warnings.append(f"Moment {k + 1} has no valid start/end; skipped.")
            continue
        if e < s:
            s, e = e, s
        s, e = max(s, lo), min(e, hi)
        if e - s < 2:
            warnings.append(f"Moment {k + 1} ({fmt_time(s)}) is outside the video/time range; skipped.")
            continue
        si, ei = _start_index(words, s), _end_index(words, e)
        if ei < si:
            ei = si
        a, b = _bounds(words, si, ei)
        # Too long: cut at the last sentence end that fits, else the last word that fits.
        if b - a > max_s:
            fit = [i for i in range(si, ei + 1) if words[i]["end"] + 0.5 - a <= max_s]
            ends = [i for i in fit if _is_sentence_end(words, i) and words[i]["end"] - a >= min_s]
            ei = (ends[-1] if ends else (fit[-1] if fit else si))
            a, b = _bounds(words, si, ei)
        # Too short: extend to following sentence ends while it fits.
        if b - a < min_s:
            j = ei
            while j + 1 < len(words) and words[j + 1]["end"] + 0.5 - a <= max_s and words[j + 1]["end"] <= hi:
                j += 1
                if _is_sentence_end(words, j) and words[j]["end"] + 0.5 - a >= min_s:
                    break
            ei = j
            a, b = _bounds(words, si, ei)
            if b - a < min_s:
                warnings.append(f"Moment {k + 1} ({fmt_time(a)}) is only {b - a:.0f}s (min {min_s:.0f}s); kept anyway.")
        text = " ".join(w["word"] for w in words[si:ei + 1])
        hits = [bw for bw in banned if re.search(r"\b" + re.escape(bw) + r"\b", text.lower())]
        caps = [str(c).strip() for c in (m.get("captions") or []) if str(c).strip()]
        title = str(m.get("title") or text[:60]).strip()
        while len(caps) < 3:
            caps.append([title, text[:120], f"{title} 👀"][len(caps)])
        try:
            score = float(m.get("score", 5))
        except (TypeError, ValueError):
            score = 5.0
        cleaned.append({"start": round(a, 2), "end": round(b, 2), "duration": round(b - a, 2),
                        "title": title, "score": round(score, 1), "reason": str(m.get("reason") or "").strip(),
                        "captions": caps[:3], "text": text, "banned_hits": hits})
        if hits:
            warnings.append(f"'{title}' contains banned word(s): {', '.join(hits)}.")
    cleaned.sort(key=lambda x: -x["score"])
    chosen: list[dict] = []
    for m in cleaned:
        clash = next((c for c in chosen if m["start"] < c["end"] + 1 and c["start"] < m["end"] + 1), None)
        if clash:
            warnings.append(f"'{m['title']}' overlaps '{clash['title']}'; dropped the lower-scored one.")
            continue
        chosen.append(m)
    if n:
        chosen = chosen[:n]
    return chosen, warnings


# ----------------------------------------------------------------- heuristic
def _sentences(words: list[dict]) -> list[tuple[int, int]]:
    out, s = [], 0
    for i in range(len(words)):
        if _is_sentence_end(words, i):
            out.append((s, i))
            s = i + 1
    return out


def heuristic_pick(transcript: dict, loud: dict, cfg: dict, n: int) -> list[dict]:
    from .transcribe import all_words
    words = all_words(transcript)
    if not words:
        raise ClipperError("No speech found in the transcript.")
    min_s, max_s = float(cfg["min_seconds"]), float(cfg["max_seconds"])
    target = (min_s + max_s) / 2
    banned = [b.lower() for b in (cfg.get("campaign") or {}).get("banned_words") or [] if b]
    sents = _sentences(words)
    cands = []
    for k, (si, _) in enumerate(sents):
        a = words[si]["start"]
        best_end = None
        for (_, ej) in sents[k:]:
            d = words[ej]["end"] - a
            if d > max_s:
                break
            if d >= min_s:
                best_end = ej
                if d >= target:
                    break
        if best_end is None:
            continue
        b = words[best_end]["end"]
        text = " ".join(w["word"] for w in words[si:best_end + 1])
        low = text.lower()
        if any(re.search(r"\b" + re.escape(bw) + r"\b", low) for bw in banned):
            continue
        dur = b - a
        n_words = best_end - si + 1
        first = " ".join(w["word"] for w in words[si:min(si + 12, best_end + 1)])
        score = 0.0
        score += 1.6 * loud_seconds(loud, a, b) / max(1.0, dur / 10)
        score += 1.2 * len(LAUGH_RE.findall(low))
        score += 0.6 * len(HOT_WORDS.findall(low))
        score += 0.4 * low.count("?") + 0.5 * low.count("!")
        score += 0.4 * len(re.findall(r"\d", low)) ** 0.5
        score += 1.5 if (HOT_WORDS.search(first) or "?" in first) else 0
        wps = n_words / max(dur, 1)
        score -= 2.5 if wps < 1.4 else 0  # dead air
        score -= 8 if AD_RE.search(low) else 0
        cands.append({"start": a, "end": b, "si": si, "ei": best_end, "raw": score, "text": text})
    if not cands:
        raise ClipperError(f"No stretch of speech between {min_s:.0f} and {max_s:.0f} seconds was found. "
                           "Try a shorter min_seconds.")
    total = words[-1]["end"] - words[0]["start"]
    spread = max(30.0, total / max(n, 1) * 0.5)
    picked: list[dict] = []
    pool = sorted(cands, key=lambda c: -c["raw"])
    while pool and len(picked) < n:
        best, best_v = None, -1e9
        for c in pool[:400]:
            if any(c["start"] < p["end"] + 1 and p["start"] < c["end"] + 1 for p in picked):
                continue
            near = sum(1 for p in picked if abs(c["start"] - p["start"]) < spread)
            v = c["raw"] - 2.0 * near
            if v > best_v:
                best, best_v = c, v
        if not best:
            break
        picked.append(best)
        pool.remove(best)
    hi = max(c["raw"] for c in picked) or 1
    lo = min(c["raw"] for c in picked)
    out = []
    for c in picked:
        sc = 5 + 4.5 * ((c["raw"] - lo) / (hi - lo) if hi > lo else 1)
        sents_txt = re.split(r"(?<=[.!?])\s+", c["text"])
        hook = max(sents_txt[:3], key=lambda s: len(HOT_WORDS.findall(s)) + ("?" in s), default=c["text"])
        title = _title(hook)
        out.append({"start": c["start"], "end": c["end"], "title": title, "score": round(sc, 1),
                    "reason": "heuristic: " + _reason(c, loud),
                    "captions": [hook[:140], f"Wait for the end... {title.lower()} 😳",
                                 f"Do you agree? \"{_title(sents_txt[-1], 10)}\""]})
    return out


def _title(text: str, n_words: int = 7) -> str:
    words = re.sub(r"[^\w\s'?!]", "", text).split()
    t = " ".join(words[:n_words])
    return (t[:1].upper() + t[1:]) if t else "Clip"


def _reason(c: dict, loud: dict) -> str:
    bits = []
    nl = loud_seconds(loud, c["start"], c["end"])
    if nl:
        bits.append(f"{nl} loud seconds")
    if LAUGH_RE.search(c["text"]):
        bits.append("laughter")
    hw = len(HOT_WORDS.findall(c["text"]))
    if hw:
        bits.append(f"{hw} strong words")
    if "?" in c["text"]:
        bits.append("questions")
    return ", ".join(bits) or "steady talking segment"


# ----------------------------------------------------------------------- LLM
PROVIDERS = {
    "groq": {"base_url": "https://api.groq.com/openai/v1", "model": "openai/gpt-oss-120b",
             "key_env": "GROQ_API_KEY"},
    "gemini": {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
               "model": "gemini-flash-latest", "key_env": "GEMINI_API_KEY"},
    "openai": {"base_url": "https://api.openai.com/v1", "model": "gpt-5.4-mini", "key_env": "OPENAI_API_KEY"},
}


def parse_llm_json(text: str) -> list[dict]:
    """Pull a list of moments out of a messy LLM reply. Raises ValueError if hopeless."""
    if not text or not text.strip():
        raise ValueError("empty response")
    t = text.strip()
    t = re.sub(r"<think>.*?</think>", "", t, flags=re.S)
    fence = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if fence:
        t = fence.group(1).strip()
    t = t.replace("“", '"').replace("”", '"').replace("’", "'").replace("‘", "'")

    def attempt(s: str):
        try:
            return json.loads(s)
        except ValueError:
            pass
        s2 = re.sub(r",\s*([\]}])", r"\1", s)                      # trailing commas
        s2 = re.sub(r"//[^\n]*", "", s2)                            # line comments
        s2 = re.sub(r"([{,]\s*)([A-Za-z_]\w*)\s*:", r'\1"\2":', s2)  # unquoted keys
        try:
            return json.loads(s2)
        except ValueError:
            pass
        # JS/Python-style single-quoted strings: 'text' -> "text" (only around JSON punctuation).
        s3 = re.sub(r"(?<=[\[{:,])\s*'((?:[^'\\]|\\.)*)'\s*(?=[,}\]:])",
                    lambda mo: json.dumps(mo.group(1)), s2)
        try:
            return json.loads(s3)
        except ValueError:
            return None

    data = attempt(t)
    if data is None:
        for open_c, close_c in (("[", "]"), ("{", "}")):
            i, j = t.find(open_c), t.rfind(close_c)
            if i != -1 and j > i:
                data = attempt(t[i:j + 1])
                if data is not None:
                    break
    if data is None:
        # Last resort: collect every {...} object that parses on its own.
        objs = []
        for mo in re.finditer(r"\{[^{}]*\}", t, re.S):
            o = attempt(mo.group(0))
            if isinstance(o, dict):
                objs.append(o)
        data = objs or None
    if data is None:
        raise ValueError("no JSON found in response")
    if isinstance(data, dict):
        for key in ("moments", "clips", "results", "data", "items"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            data = [data]
    out = []
    for item in data:
        if not isinstance(item, dict):
            continue
        s = item.get("start", item.get("start_time", item.get("from")))
        e = item.get("end", item.get("end_time", item.get("to")))
        try:
            s, e = parse_time(s), parse_time(e)
        except (TypeError, ValueError):
            continue
        if s is None or e is None:
            continue
        caps = item.get("captions") or item.get("caption") or []
        if isinstance(caps, str):
            caps = [caps]
        out.append({"start": s, "end": e, "title": item.get("title") or "", "score": item.get("score", 5),
                    "reason": item.get("reason") or item.get("why") or "", "captions": caps})
    if not out:
        raise ValueError("JSON had no usable moments")
    return out


def build_prompt(lines: list[dict], cfg: dict, n: int, hours: bool) -> str:
    camp = cfg.get("campaign") or {}
    parts = [f"You are an expert short-form video editor picking the {n} best viral clips from a "
             f"{cfg.get('niche') or 'video'} transcript for TikTok, Reels and Shorts.",
             SELECTION_RULES.format(min_s=cfg["min_seconds"], max_s=cfg["max_seconds"])]
    if camp.get("brief"):
        parts.append("CAMPAIGN BRIEF (follow it):\n" + str(camp["brief"]).strip())
    if camp.get("banned_words"):
        parts.append("BANNED WORDS (never pick a moment containing these): " + ", ".join(camp["banned_words"]))
    parts.append(MOMENT_SCHEMA)
    parts.append("Reply with ONLY the JSON array.\n\nTRANSCRIPT ([time] text, LOUD = loudness peak):")
    parts.append("\n".join(format_line(ln, hours) for ln in lines))
    return "\n\n".join(parts)


def call_llm(prompt: str, cfg: dict, provider: str) -> str:
    import requests
    ai = cfg.get("ai") or {}
    p = PROVIDERS.get(provider, {})
    base = (ai.get("base_url") or p.get("base_url") or "").rstrip("/")
    model = ai.get("model") or p.get("model")
    key_env = ai.get("api_key_env") or p.get("key_env") or ""
    key = os.environ.get(key_env, "") if key_env else ""
    if not base or not model:
        raise ClipperError(f"Unknown AI provider '{provider}'. Use groq, gemini, openai, or set ai.base_url + ai.model.")
    if not key and provider in PROVIDERS:
        raise ClipperError(f"Set your API key first: environment variable {key_env}.")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    body = {"model": model, "temperature": float(ai.get("temperature", 0.4)),
            "messages": [{"role": "system", "content": "You return only valid JSON."},
                         {"role": "user", "content": prompt}]}
    try:
        r = requests.post(f"{base}/chat/completions", headers=headers, json=body, timeout=180)
    except requests.RequestException as e:
        raise ClipperError(f"Couldn't reach {provider}: {e}")
    if r.status_code == 404:
        raise ClipperError(f"{provider} says model '{model}' doesn't exist. Set ai.model in clipper.yaml "
                           "to a current model name from the provider's model list.")
    if r.status_code >= 400:
        raise ClipperError(f"{provider} returned HTTP {r.status_code}: {r.text[:300]}")
    try:
        return r.json()["choices"][0]["message"]["content"] or ""
    except (ValueError, KeyError, IndexError):
        return r.text


def llm_pick(transcript: dict, loud: dict, cfg: dict, n: int, provider: str,
             caller=None) -> list[dict]:
    """caller(prompt) -> str can be injected for testing."""
    lines = transcript_lines(transcript, loud)
    hours = (lines[-1]["end"] if lines else 0) >= 3600
    # Keep each request well under typical context limits (~45k chars of transcript).
    chunks, cur, size = [], [], 0
    for ln in lines:
        L = len(ln["text"]) + 16
        if cur and size + L > 45000:
            chunks.append(cur)
            cur, size = [], 0
        cur.append(ln)
        size += L
    if cur:
        chunks.append(cur)
    caller = caller or (lambda pr: call_llm(pr, cfg, provider))
    found: list[dict] = []
    per = n if len(chunks) == 1 else max(2, math.ceil(n * 1.5 / len(chunks)))
    for i, ch in enumerate(chunks):
        log(f"Asking {provider} for moments (part {i + 1}/{len(chunks)})...")
        reply = caller(build_prompt(ch, cfg, per, hours))
        try:
            found += parse_llm_json(reply)
        except ValueError as e:
            log(f"Couldn't read the AI reply ({e}); retrying once with a stricter request.")
            reply = caller(build_prompt(ch, cfg, per, hours) + "\n\nIMPORTANT: output must be a valid JSON array only.")
            try:
                found += parse_llm_json(reply)
            except ValueError as e2:
                log(f"Still unreadable ({e2}); skipping this part.")
    if not found:
        raise ValueError("the AI returned no usable moments")
    return found


def pick_moments(transcript: dict, loud: dict, cfg: dict, n: int, mode: str) -> list[dict]:
    mode = (mode or "heuristic").lower()
    if mode == "heuristic":
        return heuristic_pick(transcript, loud, cfg, n)
    try:
        return llm_pick(transcript, loud, cfg, n, mode)
    except ValueError as e:
        log(f"AI selection failed ({e}); using the no-AI heuristic instead.")
        return heuristic_pick(transcript, loud, cfg, n)


def load_moments(path: Path) -> list[dict]:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        raise ClipperError(f"Can't read {path}.")
    try:
        return parse_llm_json(text)
    except ValueError as e:
        raise ClipperError(f"{path} isn't valid moments JSON ({e}).")
