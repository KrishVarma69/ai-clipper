# AI Clipper for Claude Code (and Antigravity / Gemini CLI)

Turn long videos (podcasts, interviews, Twitch / Kick / YouTube stream VODs) into
**finished, captioned 9:16 clips** for TikTok, Instagram Reels and YouTube Shorts with
one command:

```
/ai-clipper:clip https://www.youtube.com/watch?v=... 5
```

Claude reads the transcript and picks the moments itself, so there's **no API key and no
subscription**. Everything else runs free on your own computer: yt-dlp downloads,
Whisper transcribes with word-level timing (mlx-whisper on the GPU of Apple Silicon Macs,
faster-whisper elsewhere), OpenCV finds faces, and ffmpeg renders.

You get:

- `clips/01_<title>.mp4`, ... at 1080x1920, 30 fps, with word-by-word animated captions
- `review.html`: double-click it to watch every clip, see why it was picked, copy captions,
  and tick a pre-posting checklist
- `post_queue.csv`: file, caption, hashtags, platform and a suggested post time for each post
  (import it into a scheduler like Postiz or Buffer)
- `captions.txt`: all captions, copy-paste friendly
- `run_log.txt`: what happened

> **Only clip content you have permission to use**, such as videos from a clipping
> campaign (Whop Content Rewards, Vyro, Discord clipping agencies) or your own content.

---

## Requirements

| | Mac | Windows | Linux |
|---|---|---|---|
| [Claude Code](https://code.claude.com) (or Antigravity CLI, see below) | yes | yes | yes |
| Python 3.10+ | `brew install python@3.12` | `winget install Python.Python.3.12` | `sudo apt install python3 python3-venv` |
| ffmpeg **with libass** (captions) | `brew install ffmpeg-full` | `winget install Gyan.FFmpeg` | `sudo apt install ffmpeg` |
| deno (YouTube downloads, optional) | `brew install deno` | `winget install DenoLand.Deno` | `curl -fsSL https://deno.land/install.sh \| sh` |

Mac note: Homebrew's plain `ffmpeg` formula is built without libass and can't burn in
captions. Install `ffmpeg-full`; AI Clipper finds it automatically even though Homebrew
doesn't put it on your PATH.

Apple Silicon Macs (M1 and newer) transcribe on the Mac's GPU with mlx-whisper, about 4x
faster than the CPU. Setup installs it automatically. An NVIDIA GPU on Windows/Linux is
also detected automatically (CUDA, float16). Otherwise Whisper runs on the CPU (int8).
To force the CPU, set `whisper_device: cpu` in `clipper.yaml`.

Disk: the Python environment is ~1 GB, the "small" Whisper model ~500 MB (downloaded once).

## Install

In Claude Code:

```
/plugin marketplace add Mocdew/ai-clipper
/plugin install ai-clipper@ai-clipper
```

Then run the one-time setup, which checks Python and ffmpeg, tells you the exact install
command for anything missing, and installs the Python packages into a private environment
in your user data folder (not inside the plugin, so updates don't wipe it):

```
/ai-clipper:setup
```

### Antigravity CLI (Google's Gemini agent)

Gemini CLI was replaced by Antigravity CLI (`agy`). The same repo installs there as a
native plugin:

```
agy plugin install https://github.com/Mocdew/ai-clipper
```

Then, in an `agy` session, run the setup and clip:

```
/ai-clipper-setup
/ai-clipping https://www.youtube.com/watch?v=... 5
/ai-clipper-campaign acme-podcast
```

Everything else in this README applies; where it says `/ai-clipper:clip`, use
`/ai-clipping` (or just ask: *"make 5 shorts from <link>"*), and Gemini picks the moments
instead of Claude.

### Gemini CLI (legacy)

If you still have Gemini CLI (e.g. through Gemini Code Assist for enterprise):

```
gemini extensions install https://github.com/Mocdew/ai-clipper
```

Then ask *"set up AI Clipper"* once, and *"make 5 shorts from <link>"*.

## Usage

```
/ai-clipper:clip <video-url-or-file> [number-of-clips] [anything else in plain English]
```

Run it from the folder where you want the clips; output goes to `./clips-output/<video-title>/`.

**1. Podcast or interview** (follows the speaker's face):

```
/ai-clipper:clip https://www.youtube.com/watch?v=XXXXXXXXXXX 6
```

**2. Gaming stream with a facecam** (facecam on top, gameplay below):

```
/ai-clipper:clip https://www.twitch.tv/videos/123456789 8 gaming split screen, facecam is top right, only 1:00:00 to 2:30:00
```

**3. Campaign mode** (follows a brief, adds required tags, avoids banned words):

```
/ai-clipper:campaign acme-podcast
```

Paste the campaign brief when asked. It's saved to `./campaigns/acme-podcast.yaml`. Then:

```
/ai-clipper:clip https://youtu.be/XXXXXXXXXXX 5 campaign: acme-podcast, 45 seconds max
```

You can also just ask: *"clip this video into 5 shorts: <url>"*. The skill triggers on its own.

More extras Claude understands: `blur` (full frame over a blurred background),
`fast` (smaller Whisper model), `at least 30 seconds`, a local file path instead of a URL,
or a `.txt` file with one link per line to clip several videos in one go.

### Finding the facecam box numbers (split mode)

Boxes are `[left, top, width, height]` as fractions of the frame (0 to 1). The easy way:
say *"facecam is top right"* and Claude takes a screenshot with a grid and reads the numbers
off it. To do it yourself:

```
python3 "<plugin>/scripts/clipper_cli.py" frame "clips-output/<name>" --at 10:00
```

Open `frame_grid.jpg` (lines every 0.05, labels every 0.1) and read the facecam's left
edge, top edge, width and height. Example for a cam in the top-right corner:
`0.72, 0.02, 0.27, 0.30`. Save it in a `clipper.yaml` in your working folder so you don't
need it again:

```yaml
reframe: split
split:
  facecam_box: [0.72, 0.02, 0.27, 0.30]
  gameplay_box: [0.0, 0.0, 1.0, 1.0]
  facecam_share: 0.35
```

## Settings

Defaults live in [`config.default.yaml`](config.default.yaml), every setting commented
in plain English. To change one, create `clipper.yaml` in the folder you work from and copy
just the lines you want to change. Campaign files override both.

## Weak or older computers (lite mode)

AI Clipper checks the computer on every run. On a machine without an Apple Silicon or
NVIDIA GPU that has 4 or fewer CPU threads (or 8 GB RAM or less with 4 or fewer cores), it
switches to **lite** mode automatically:

- Whisper `base` with greedy decoding (beam 1) and one CPU core left free
- Whisper and ffmpeg run at low priority, so the computer stays usable
- 720p downloads, and face tracking checks one frame per second
- Clips are encoded on the graphics chip (Intel Quick Sync, AMD or NVIDIA) when it works,
  otherwise with a faster x264 preset

The trade-off: captions can have a few more transcription mistakes, and face-tracked clips
are a little softer (cropped from 720p). `probe` and `check` say which mode is active. To
choose it yourself, say "lite mode" / "my PC is slow" to Claude, pass `--performance lite`
or `--performance normal`, or put `performance: lite` (or `normal`) in `clipper.yaml`. Any
setting in `clipper.yaml` (for example `max_height: 1080`) overrides the lite value.

## Speed tips

- **Long VODs:** use a time range (`only 1:00:00 to 2:30:00`): only that part is
  downloaded and transcribed.
- **Smaller model:** say `fast` (uses Whisper `base`). `small` is the default; `medium`
  is more accurate but ~3x slower.
- **NVIDIA GPU:** detected automatically, often 5-10x faster transcription.
- **Slow computer:** see lite mode above (`--performance lite`).
- **Re-runs are cached:** download, transcription and finished clips are reused. Ask for
  different clips from the same video and only the rendering runs again.
- Rough timing (Apple M2, `small`, measured): on the Mac's GPU, transcription takes about a
  tenth of the video's length (a 34-min video: 3 min). On a CPU it's roughly a third to
  two thirds (34-min video: 11-24 min, slower when the computer is low on memory). The
  first GPU run also downloads the model once (~480 MB). Rendering takes ~10-30 s per clip.
  Downloads depend on your connection and the site.

## Running without Claude (standalone)

```
python3 scripts/clipper_cli.py run "<url-or-file>" --clips 5 --ai heuristic
python3 scripts/clipper_cli.py run "<url-or-file>" --clips 5 --ai groq      # needs GROQ_API_KEY
python3 scripts/clipper_cli.py run "<url-or-file>" --clips 5 --ai gemini    # needs GEMINI_API_KEY
python3 scripts/clipper_cli.py run "<url-or-file>" --clips 5 --ai openai    # needs OPENAI_API_KEY
```

`heuristic` uses loudness, laughter, strong words and speech density, with no AI.
The LLM modes use any OpenAI-compatible endpoint through plain HTTP; set `ai.model` or
`ai.base_url` in `clipper.yaml` to use another model or a local server (Ollama, LM Studio).
Step by step: `prepare`, `transcript`, `select`, `render`. See `clipper_cli.py --help`.

## Why AI Clipper never posts for you

AI Clipper does **not** upload, post, log into accounts, or drive a browser, and it won't.

- Bot or automated posting is the fastest way to get clipping accounts flagged, shadowbanned
  or banned, and campaigns don't pay for views on banned accounts.
- The official posting APIs (TikTok Content Posting API, Instagram Graph API, YouTube Data
  API) need an approved developer app, and unapproved apps can only post privately.

Post manually from your phone, or import `post_queue.csv` into a scheduler such as
[Postiz](https://postiz.com) or [Buffer](https://buffer.com) that posts through approved
integrations.

## Rules that keep you paid

1. **Follow the brief.** Use the campaign's required @mentions and hashtags, length and
   topics. Save the brief with `/ai-clipper:campaign` so every clip follows it.
2. **Only clip content you're permitted to clip.** Campaign videos or your own content.
   Unpermitted clips get copyright strikes and aren't paid.
3. **Don't post the same file to multiple accounts.** Duplicate uploads get flagged as spam
   and often disqualified. Render separate versions if a campaign allows several accounts.
4. **Vary your captions.** Every clip gets 3 caption variants, slightly different caption
   size/position and a tiny zoom, and hashtags in a different order on each post. Keep it
   that way (`variation.enabled: true`).
5. **Check before posting.** Open `review.html`: hook in the first 2 seconds, no
   watermark, required tags present, follows the brief.

## Troubleshooting

| Problem | Fix |
|---|---|
| `STATUS: NOT READY` | Run `/ai-clipper:setup` and follow the steps it prints. |
| "can't burn in captions (built without libass)" | Mac: `brew install ffmpeg-full`. Windows: the Gyan build has it. Linux: distro ffmpeg has it. |
| Download failed | Update yt-dlp: re-run `/ai-clipper:setup`. YouTube also needs `deno`. Check the video isn't private, members-only or still live. |
| Out of memory while transcribing | Say `fast` or use `--model base` (or `tiny`). |
| Computer freezes or is very slow while clipping | Use lite mode: `--performance lite` or `performance: lite` in `clipper.yaml`. |
| Captions in the wrong language | Set `language: en` (or your language code) in `clipper.yaml`. |
| Face mode shows a blurred background | Faces were found in under 30% of frames (gameplay, B-roll), so it fell back to blur. Use split mode for streams. |
| Clip cut too early or too late | Ask Claude to adjust it, or edit `moments.json` and render again. Times snap to word and sentence boundaries. |
| Windows: path with spaces | Supported; keep quotes around paths if you run commands yourself. |
| Plugin updated, packages outdated | `check` says "re-run setup"; run `/ai-clipper:setup`. |

Where things live: outputs in `./clips-output/`; the Python environment in
`~/Library/Application Support/ai-clipper` (Mac), `%LOCALAPPDATA%\ai-clipper` (Windows) or
`~/.local/share/ai-clipper` (Linux); set `AI_CLIPPER_HOME` to move it.

## Development

```
claude plugin validate .
agy plugin validate --strict .     # Antigravity manifest (.antigravity-plugin/plugin.json)
python3 tests/e2e_test.py          # run with the venv python, see the file header
```

## License

MIT, see [LICENSE](LICENSE). Uses yt-dlp, faster-whisper, OpenCV and ffmpeg under their
own licenses.
