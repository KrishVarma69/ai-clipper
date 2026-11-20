---
name: ai-clipper-campaign
description: Save a clipping campaign's brief and rules (required mentions, hashtags, banned words, clip length) so AI Clipper follows them. Use when the user wants to save or update a clipping campaign.
disable-model-invocation: true
---

# Save an AI Clipper campaign

The AI Clipper plugin root is the folder three levels above this file
(`antigravity/skills/ai-clipper-campaign/SKILL.md`). When installed globally it is usually
`~/.gemini/config/plugins/ai-clipper` (Windows: `%USERPROFILE%\.gemini\config\plugins\ai-clipper`).

Read `<plugin root>/commands/campaign.md` and follow it exactly, with these substitutions:

- `$ARGUMENTS` is the campaign name the user gave (if none, ask for one).
- "the Write tool" means your file-writing tool.
- Where it says `/ai-clipper:clip <url> <number> campaign: <name>`, tell the user
  `/ai-clipping <url> <number> campaign: <name>` instead.
