---
name: ai-clipper-setup
description: Install and check everything AI Clipper needs (Python, ffmpeg, Whisper, GPU). Use when the user asks to set up, install, repair or check AI Clipper.
disable-model-invocation: true
---

# AI Clipper setup

The AI Clipper plugin root is the folder three levels above this file
(`antigravity/skills/ai-clipper-setup/SKILL.md`). When installed globally it is usually
`~/.gemini/config/plugins/ai-clipper` (Windows: `%USERPROFILE%\.gemini\config\plugins\ai-clipper`).

Read `<plugin root>/commands/setup.md` and follow it exactly, with these substitutions:

- `${CLAUDE_PLUGIN_ROOT}` means the absolute plugin root path above.
- "restart Claude Code" means restart this CLI session.
- When it finishes, tell the user they can now run `/ai-clipping <video-url> <number>`
  or just ask "make 5 shorts from <link>".
