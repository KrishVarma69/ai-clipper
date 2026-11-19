---
description: Install and check everything AI Clipper needs (Python, ffmpeg, Whisper, GPU)
disable-model-invocation: true
---

Set up AI Clipper for the user. Explain each step in plain, non-technical language
(one or two sentences each). The tool is:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/clipper_cli.py"
```

On Windows use `python` instead of `python3` (try `py -3` if `python` isn't found).
Keep the quotes: the path may contain spaces.

1. **Check.** Run `<tool> check`. It reports Python 3.10+, ffmpeg (and whether it can burn
   in captions, which needs libass), the Python environment, packages, and whether an
   NVIDIA GPU was found. If it ends with `STATUS: READY`, say everything is ready and stop.
   If `python3`/`python` itself isn't found, go to step 2 first.

2. **Python 3.10+** (only if missing). Tell the user the command for their OS and offer to
   run it (ask first; it changes their system):
   - Mac: `brew install python@3.12` (no Homebrew? `/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"` from https://brew.sh)
   - Windows: `winget install --id Python.Python.3.12 -e`, then restart Claude Code
   - Linux (Debian/Ubuntu): `sudo apt-get install -y python3 python3-venv python3-pip`
   Commands that need `sudo` or a password: give the command and let the user run it.

3. **ffmpeg** (only if missing or "no libass"). This is the video engine. Offer to run:
   - Mac: `brew install ffmpeg-full` (the plain `ffmpeg` formula can't burn in captions;
     AI Clipper finds `ffmpeg-full` automatically even though Homebrew doesn't put it on PATH)
   - Windows: `winget install --id Gyan.FFmpeg -e`, then restart Claude Code so it's on PATH
   - Linux: `sudo apt-get install -y ffmpeg` (user runs it), or dnf/pacman equivalents

4. **Python packages.** Run `<tool> setup` (takes a few minutes the first time; run it in the
   background if your Bash tool might time out). It creates a private Python environment in
   the user's data folder (not inside the plugin, so updates don't wipe it) and installs
   yt-dlp, faster-whisper, OpenCV, numpy, PyYAML and requests. On Apple Silicon Macs it also
   installs mlx-whisper so Whisper runs on the Mac's GPU (several times faster). If an NVIDIA
   GPU is found on Windows/Linux it installs the CUDA libraries so Whisper runs on the GPU
   (float16); otherwise Whisper runs on the CPU (int8).

5. **YouTube (optional).** Recent yt-dlp versions need a JavaScript runtime for YouTube.
   If `deno --version` fails, suggest: Mac `brew install deno`, Windows
   `winget install DenoLand.Deno`, Linux `curl -fsSL https://deno.land/install.sh | sh`.

6. **Re-check.** Run `<tool> check` again and show the final status. Tell the user that the
   first transcription also downloads the Whisper model once (~500 MB for "small").

Finish with one line: they can now run `/ai-clipper:clip <video-url> <number>`.
