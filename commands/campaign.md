---
description: Save a clipping campaign's brief and rules so /ai-clipper:clip follows them
argument-hint: "<campaign-name>"
disable-model-invocation: true
---

The user wants to save a clipping campaign named: `$ARGUMENTS`

If the name is empty, ask for a short name (e.g. `acme-podcast`).

1. Ask the user to paste the campaign brief and requirements exactly as the campaign
   (Whop Content Rewards, Vyro, Discord agency, etc.) gives them. Wait for the paste.
2. Read it and extract:
   - `brief`: a short summary of what clips should be about, tone, what to feature/avoid
   - `required_mentions`: @handles that must be in every caption
   - `required_hashtags`: hashtags that must be in every caption
   - `extra_hashtags`: optional tags that fit (5-10), rotated per post
   - `banned_words`: words/topics that must not appear in a clip (also competitor names,
     slurs, anything the brief forbids)
   - `min_seconds` / `max_seconds`: clip length rules (defaults 20 / 60 if not stated)
   - `reframe`: `face` for podcasts/interviews, `split` for gaming streams with a facecam,
     `blur` if the brief wants the full frame
   - `notes`: payout rules, view minimums, deadlines, posting rules (one line each)
3. Write `./campaigns/<name>.yaml` in the current working directory (create the folder) with
   the Write tool, using this layout and plain-English comments:

```yaml
# Campaign: <name>  (saved <date>)
name: <name>
brief: >
  <summary>
required_mentions: ["@creator"]
required_hashtags: ["#brand"]
extra_hashtags: ["#podcast", "#clips"]
banned_words: ["competitor"]
min_seconds: 20
max_seconds: 60
reframe: face
notes:
  - "Paid per 1,000 views, minimum 10k views"
```

4. Show the user a short summary of what you saved and anything that was unclear in the
   brief (ask about it only if it changes the clips). Remind them: use it with
   `/ai-clipper:clip <url> <number> campaign: <name>`. Captions will automatically carry
   the required mentions and hashtags; moments with banned words are skipped.
