---
name: ai-clipping
description: Turns a long video (podcast, interview, Twitch/Kick/YouTube stream VOD, or a local file) into finished, captioned 9:16 vertical clips for TikTok, Instagram Reels and YouTube Shorts, with Claude picking the best moments. Use when the user asks to clip a video, make shorts/reels/TikToks from a video or link, find viral moments, or cut a podcast or stream into clips.
when_to_use: 'Examples: "clip this video", "make 5 shorts from this podcast", "cut this stream into TikToks", "find the best moments in this link", "make reels from this file".'
argument-hint: "<url-or-file> [number] [extras]"
---

# AI Clipping workflow

You turn one long video into short, captioned vertical clips. A local Python tool
does the heavy lifting (download, transcription, rendering). **You** pick the moments
by reading the transcript: no API key is needed.

**Permission reminder (say this once, briefly, at the start):** only clip content the
user has permission to use, such as videos from a clipping campaign (Whop Content
Rewards, Vyro, an agency brief) or their own content.

**Never** upload or post anything, log into social accounts, or automate a browser.
Posting stays manual (or through a scheduler like Postiz/Buffer using `post_queue.csv`).

## The tool

```
CLI = python3 "${CLAUDE_PLUGIN_ROOT}/scripts/clipper_cli.py"
```

- On Windows use `python` instead of `python3` (or `py -3` if `python` isn't found).
- Always keep the quotes around the script path and around every folder/file path:
  paths can contain spaces.
- Run every command from the user's current working directory. Outputs go to
  `./clips-output/<video-title>/`. Never write anything inside `${CLAUDE_PLUGIN_ROOT}`.

## Inputs

From the user's request (or `$ARGUMENTS` when invoked as a command) work out:

| What | Default | Examples of how users say it |
|---|---|---|
| source (required) | - | a URL, a local file path, or a `.txt` with one link per line (batch) |
| number of clips | `clips_per_video` from config (5) | "8 clips", a bare number like `8` |
| time range | whole video | "only 1:00:00 to 2:30:00" -> `--start 1:00:00 --end 2:30:00` |
| reframe | config (`face`) | "gaming", "split screen", "facecam" -> `--reframe split`; "blur", "keep full frame" -> `--reframe blur`; "podcast", "follow the speaker" -> `--reframe face` |
| campaign | none | "campaign: acme" -> `--campaign acme` (file `./campaigns/acme.yaml`) |
| length | 20-60 s | "60 seconds max" -> `--max-seconds 60`; "at least 30s" -> `--min-seconds 30` |
| whisper model | `small` | "fast", "quick" -> `--model base`; "most accurate" -> `--model medium` |
| facecam box | config | "facecam is top right" -> see Split mode below |

If no source was given, ask for it. Otherwise don't ask questions: choose sensible
defaults and say what you chose.

## Step 0: check

Run `CLI check`. If it ends with `STATUS: READY`, continue. If not, run the
`/ai-clipper:setup` flow (read `${CLAUDE_PLUGIN_ROOT}/commands/setup.md` and follow it),
explaining each step in plain language, then come back here.

## Step 1: prepare (download, audio, transcript, loudness)

1. `CLI probe "<source>" [--start .. --end ..] [--model ..]` prints JSON with the title,
   duration, output `folder` and a time `estimate`. Tell the user the estimate. If the
   video is over 1 hour and they didn't pick a model or range, suggest `--model base` or a
   time range, then continue with what they asked for (don't wait unless they asked you to).
   For a local file this is instant. For a `.txt` batch, probe prints one line per link.
2. Run prepare **in the background** (Bash tool with `run_in_background: true`) so it
   can't hit the command timeout:
   `CLI prepare "<source>" --out "<folder from probe>" [--start ..] [--end ..] [--model ..] [--campaign ..]`
   For a batch `.txt`, run `CLI prepare "links.txt"` without `--out`; it prints one
   `OUTPUT_FOLDER:` line per video and `BATCH_READY: [...]` at the end.
3. While it runs, check progress with `CLI status "<folder>"` about once a minute and tell
   the user the stage and percentage (download -> transcribe -> done). Don't spam.
4. It's finished when the output shows `READY: <folder>`. If it shows `ERROR:`, explain the
   message in plain words and the fix it suggests (usually updating yt-dlp, installing deno
   for YouTube, or a smaller model when out of memory).

Every stage is cached, so re-running prepare on the same source skips finished work.
Add `--force` only to redo everything.

## Step 2: you pick the moments

1. `CLI transcript "<folder>" --rules [--campaign ..]` prints a header (duration, chunk
   count, clip length limits, campaign brief and banned words), the selection rules, and
   chunk 1..N of `[mm:ss] text` lines. `🔊 LOUD` marks loudness peaks, `😂 LAUGH` laughter.
   If the output is too long to read at once, print chunks one by one with `--chunk 1`,
   `--chunk 2`, ... **Read every chunk before choosing.** Note candidates as you go.
2. Choose the best N moments following these rules:

### Selection rules
- **Strong hook in the first 2 seconds:** start AT the most interesting line or the
  punchline, not the slow setup. The first sentence must make people stop scrolling.
- **Self-contained:** makes sense with zero context. Has a clear payoff or ending.
- **Length** within the min/max from the header (default 20-60 s).
- **Never start or end mid-sentence.** The renderer snaps to word boundaries (0.3 s padding
  before, 0.5 s after) and nearby sentence boundaries, but aim for clean sentence edges.
- **Avoid** ad reads/sponsor segments, dead air, repeated content, and anything that contains
  a banned word. Follow the campaign brief exactly (topics, tone, what to feature).
- **Score high:** loud peaks, laughter, strong opinions, surprising facts, conflict,
  emotional moments, story moments ("so this one time...").
- **No overlapping clips. Spread picks across the whole video,** not all from one section.
- **Captions:** 3 distinct variants per clip, each with a different wording and angle
  (e.g. curiosity hook / bold quote or claim / question to the viewer). Short, natural,
  no clickbait lies. Don't add hashtags or @mentions: the campaign's required mentions and
  hashtags are added automatically, rotated per platform.

3. Write `<folder>/moments.json` (Write tool), best first:

```json
[
  {"start": 754.2, "end": 801.0, "title": "He got fired on day one",
   "score": 9, "reason": "Loud reaction + clear story payoff, hook in first line",
   "captions": ["He didn't last ONE day 😭", "\"I got fired before lunch\"", "Would you have quit too?"]}
]
```

`start`/`end` are seconds in the video (convert `[1:02:03]` to 3723). `score` is 1-10.
Titles are short (they become file names). Pick exactly N unless the video doesn't have N
good moments; then pick fewer and say why.

## Step 3: render

`CLI render "<folder>" --moments "<folder>/moments.json" [--reframe face|blur|split] [--campaign ..] [--facecam-box l,t,w,h]`

Rendering takes roughly 10-40 s per clip; for more than ~6 clips run it in the background
too. It prints `WARNING:` lines when it had to adjust a moment (too long, overlapping,
banned word). If a moment was dropped and the user wanted N clips, pick a replacement,
update moments.json and render again (finished clips are cached).

Use `--dry-run` to see the snapped start/end times without rendering.

### Split mode (gaming / streams with a facecam)
The facecam and gameplay boxes are `[left, top, width, height]` as fractions of the frame.
If the user doesn't know them: run `CLI frame "<folder>" --at <time>`, then open the saved
`frame_grid.jpg` with the Read tool, look at where the facecam is, and read the fractions
off the grid (lines every 0.05). Pass `--facecam-box 0.72,0.03,0.26,0.28`. Suggest saving
it in `./clipper.yaml` under `split:` for next time.

## Step 4: report

Keep it short:
- clip count and the folder path (`clips-output/<name>/clips/`)
- one line per clip: `01. Title (0:45) - why it's good`
- any warnings that matter (fallbacks to blur, dropped clips, banned words)
- "Open `review.html` (double-click it) to watch every clip and copy the captions before
  posting. `post_queue.csv` has a suggested schedule for a scheduler like Postiz or Buffer."
- one line: post each clip manually, follow the campaign brief, and don't post the same file
  to several accounts.

## Standalone (no Claude)
Users can also run `CLI run "<source>" --clips 5 --ai heuristic` (or `--ai groq|gemini|openai`
with an API key in `GROQ_API_KEY` / `GEMINI_API_KEY` / `OPENAI_API_KEY`). Mention this only
if asked.
