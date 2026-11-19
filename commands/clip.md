---
description: Turn a long video into captioned 9:16 clips for TikTok, Reels and Shorts
argument-hint: '<video-url-or-file> [number-of-clips] [extras, e.g. "gaming split screen", "campaign: acme", "only 1:00:00 to 2:30:00", "60 seconds max"]'
disable-model-invocation: true
---

The user ran `/ai-clipper:clip` with these arguments:

```
$ARGUMENTS
```

Load the `ai-clipper:ai-clipping` skill with the Skill tool and follow its workflow
(Step 0 check -> Step 1 prepare -> Step 2 you pick the moments -> Step 3 render ->
Step 4 report), using the arguments above. If the Skill tool isn't available, Read
`${CLAUDE_PLUGIN_ROOT}/skills/ai-clipping/SKILL.md` and follow it (the plugin folder is
`${CLAUDE_PLUGIN_ROOT}`; use that path wherever the file mentions the plugin root).

Parse the arguments flexibly:
- the first URL or file path is the source (required; if missing, ask for it).
  A `.txt` file means a batch of links, one per line.
- a bare number is the number of clips (default 5).
- everything else is plain-English extras (reframe mode, time range, campaign name,
  clip length, speed). Map them as described in the skill's Inputs table.

The tool is:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/clipper_cli.py"
```

(on Windows: `python` instead of `python3`). Run it from the current working directory,
so clips land in `./clips-output/`.
