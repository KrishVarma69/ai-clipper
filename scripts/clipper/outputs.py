"""post_queue.csv, captions.txt and review.html."""
from __future__ import annotations

import csv
import datetime as dt
import html
import json
import random
from pathlib import Path

from .util import fmt_time

PLATFORM_TAGS = {"youtube": ["#shorts"], "tiktok": [], "instagram": ["#reels"]}


def _norm_tag(t: str, prefix: str) -> str:
    t = str(t).strip()
    if not t:
        return ""
    return t if t.startswith(prefix) else prefix + t.lstrip("#@")


def tags_for(clip_i: int, platform: str, cfg: dict, rng: random.Random) -> tuple[str, list[str]]:
    camp = cfg.get("campaign") or {}
    mentions = [_norm_tag(m, "@") for m in camp.get("required_mentions") or []]
    required = [_norm_tag(h, "#") for h in camp.get("required_hashtags") or []]
    extra = [_norm_tag(h, "#") for h in camp.get("extra_hashtags") or []]
    rng.shuffle(extra)
    tags = required + extra[:4] + PLATFORM_TAGS.get(platform, [])
    seen, uniq = set(), []
    for t in tags:
        if t and t.lower() not in seen:
            seen.add(t.lower())
            uniq.append(t)
    rng.shuffle(uniq)
    text = " ".join([m for m in mentions if m] + uniq)
    return text, [m for m in mentions if m] + required


def schedule_times(n: int, cfg: dict) -> list[str]:
    sc = cfg.get("schedule") or {}
    per_day = max(1, int(sc.get("posts_per_day", 3)))
    first = int(sc.get("first_post_hour", 10))
    gap = float(sc.get("hours_between_posts", 4))
    day0 = dt.date.today() + dt.timedelta(days=1)
    out = []
    for i in range(n):
        d = day0 + dt.timedelta(days=i // per_day)
        when = dt.datetime.combine(d, dt.time(0, 0)) + dt.timedelta(hours=first + (i % per_day) * gap)
        out.append(when.strftime("%Y-%m-%d %H:%M"))
    return out


def write_outputs(folder: Path, meta: dict, clips: list[dict], cfg: dict, warnings: list[str]) -> dict:
    platforms = (cfg.get("schedule") or {}).get("platforms") or ["tiktok", "instagram", "youtube"]
    times = schedule_times(len(clips), cfg)
    rows = []
    for i, c in enumerate(clips):
        rng = random.Random(f"{c['file']}-tags")
        c["posts"] = []
        for p_i, platform in enumerate(platforms):
            cap = c["captions"][(i + p_i) % len(c["captions"])]
            tag_text, required = tags_for(i, platform, cfg, rng)
            post = {"platform": platform, "caption": cap, "tags": tag_text}
            c["posts"].append(post)
            rows.append({"file": c["file"], "title": c["title"], "caption": cap,
                         "mentions_hashtags": tag_text, "platform": platform,
                         "suggested_post_time": times[i]})
        c["required_tags"] = required if platforms else []
    with open(folder / "post_queue.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["file", "title", "caption", "mentions_hashtags", "platform",
                                          "suggested_post_time"])
        w.writeheader()
        w.writerows(rows)

    lines = [f"AI Clipper captions - {meta.get('title', '')}", "=" * 60, ""]
    for c in clips:
        lines += [f"{c['file']}  |  {c['title']}  |  score {c['score']}  |  {fmt_time(c['start'])}-{fmt_time(c['end'])}",
                  ""]
        for p in c["posts"]:
            lines += [f"[{p['platform']}]", f"{p['caption']}", f"{p['tags']}".strip(), ""]
        lines += ["-" * 60, ""]
    (folder / "captions.txt").write_text("\n".join(lines), encoding="utf-8")

    data = {"title": meta.get("title"), "source": meta.get("source"), "campaign": (cfg.get("campaign") or {}).get("name"),
            "brief": (cfg.get("campaign") or {}).get("brief"), "clips": clips, "warnings": warnings}
    (folder / "clips.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    (folder / "review.html").write_text(review_html(data), encoding="utf-8")
    return data


def review_html(data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    title = html.escape(data.get("title") or "Clips")
    return REVIEW_TEMPLATE.replace("__TITLE__", title).replace("__DATA__", payload)


REVIEW_TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Review: __TITLE__</title>
<style>
:root{--bg:#f6f6f4;--card:#fff;--ink:#1b1b1b;--muted:#666;--line:#e3e3df;--accent:#0a7cff;--warn:#b54708;--ok:#027a48}
@media (prefers-color-scheme:dark){:root{--bg:#141414;--card:#1f1f1f;--ink:#eee;--muted:#9a9a9a;--line:#333;--accent:#4aa3ff;--warn:#f5a25d;--ok:#4ade80}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
header{padding:20px 16px 8px;max-width:1200px;margin:auto}h1{font-size:22px;margin:0 0 4px}.sub{color:var(--muted)}
.warns{max-width:1200px;margin:8px auto;padding:0 16px}.warn{color:var(--warn);font-size:14px}
.brief{max-width:1200px;margin:8px auto;padding:0 16px;color:var(--muted);white-space:pre-wrap}
main{max-width:1200px;margin:auto;padding:16px;display:grid;gap:16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px;display:grid;grid-template-columns:minmax(200px,300px) 1fr;gap:16px}
@media (max-width:700px){.card{grid-template-columns:1fr}}
video{width:100%;aspect-ratio:9/16;background:#000;border-radius:8px}
h2{font-size:18px;margin:0 0 4px}.meta{color:var(--muted);font-size:13px;margin-bottom:8px}
.score{display:inline-block;background:var(--accent);color:#fff;border-radius:6px;padding:1px 8px;font-weight:600;margin-right:6px}
.reason{margin:6px 0 12px}.cap{border:1px solid var(--line);border-radius:8px;padding:8px 10px;margin:6px 0;display:flex;gap:10px;align-items:flex-start}
.cap .t{flex:1;white-space:pre-wrap}.cap .p{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
button{font:inherit;border:1px solid var(--line);background:transparent;color:var(--ink);border-radius:6px;padding:4px 10px;cursor:pointer}
button:hover{border-color:var(--accent)}.done{color:var(--ok);border-color:var(--ok)}
.check{margin-top:10px;display:grid;gap:4px}.check label{display:flex;gap:8px;align-items:center}
.auto{font-size:12px;color:var(--ok)}.bad{color:var(--warn)}
</style></head><body>
<header><h1>Review: __TITLE__</h1><div class="sub" id="sub"></div></header>
<div class="brief" id="brief"></div>
<div class="warns" id="warns"></div>
<main id="list"></main>
<script id="data" type="application/json">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
const store = (()=>{try{localStorage.setItem('_t','1');localStorage.removeItem('_t');return localStorage}catch(e){return null}})();
const key = 'ai-clipper:' + location.pathname;
let state = {}; try{ state = JSON.parse((store && store.getItem(key)) || '{}') }catch(e){}
const save = ()=>{ try{ store && store.setItem(key, JSON.stringify(state)) }catch(e){} };
const fmt = s => { s=Math.round(s); const h=Math.floor(s/3600), m=Math.floor(s%3600/60), x=s%60; return (h? h+':'+String(m).padStart(2,'0') : String(m).padStart(2,'0'))+':'+String(x).padStart(2,'0') };
const esc = s => String(s ?? '').replace(/[&<>"]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function copy(text, btn){
  const ok=()=>{btn.textContent='Copied';btn.classList.add('done');setTimeout(()=>{btn.textContent='Copy';btn.classList.remove('done')},1400)};
  if (navigator.clipboard && window.isSecureContext) { navigator.clipboard.writeText(text).then(ok, ()=>fallback()) } else fallback();
  function fallback(){ const t=document.createElement('textarea'); t.value=text; t.style.position='fixed'; t.style.opacity='0'; document.body.appendChild(t); t.select(); try{document.execCommand('copy'); ok()}catch(e){alert(text)} t.remove() }
}
document.getElementById('sub').textContent = `${D.clips.length} clips` + (D.campaign? ` · campaign: ${D.campaign}`:'') + ' · check every clip before posting';
if (D.brief) document.getElementById('brief').textContent = 'Brief: ' + D.brief;
document.getElementById('warns').innerHTML = (D.warnings||[]).map(w=>`<div class="warn">⚠ ${esc(w)}</div>`).join('');
const list = document.getElementById('list');
D.clips.forEach((c, i) => {
  const el = document.createElement('section'); el.className='card';
  const req = c.required_tags || [];
  const allTags = (c.posts||[]).map(p=>p.tags).join(' ').toLowerCase();
  const reqOk = req.every(t => allTags.includes(t.toLowerCase()));
  const banned = (c.banned_hits||[]);
  const checks = [
    ['tags', 'Required mentions/hashtags present', reqOk ? (req.length? 'auto: all present' : 'auto: none required') : 'auto: MISSING'],
    ['hook', 'Hook lands in the first 2 seconds', ''],
    ['wm', 'No watermark / other creator\'s logo', ''],
    ['brief', 'Follows the campaign brief', banned.length ? 'banned words: ' + banned.join(', ') : ''],
  ];
  el.innerHTML = `
   <div><video controls preload="metadata" src="${esc(c.file)}"></video></div>
   <div>
     <h2>${i+1}. ${esc(c.title)}</h2>
     <div class="meta"><span class="score">${esc(c.score)}/10</span>${fmt(c.start)}–${fmt(c.end)} · ${Math.round(c.duration)}s · ${esc(c.reframe)}${c.reframe_note? ' ('+esc(c.reframe_note)+')':''} · <code>${esc(c.file)}</code></div>
     <div class="reason">${esc(c.reason)}</div>
     ${(c.posts||[]).map((p,j)=>`<div class="cap"><div class="t"><div class="p">${esc(p.platform)}</div>${esc(p.caption)}\n${esc(p.tags)}</div><button data-c="${i}" data-p="${j}">Copy</button></div>`).join('')}
     <div class="check">${checks.map(([k,label,auto])=>{const id=`c${i}-${k}`; const on = state[id] ?? (k==='tags' && reqOk); return `<label><input type="checkbox" id="${id}" ${on?'checked':''}> ${esc(label)} ${auto?`<span class="auto ${auto.includes('MISSING')||auto.includes('banned')?'bad':''}">${esc(auto)}</span>`:''}</label>`}).join('')}</div>
   </div>`;
  list.appendChild(el);
});
list.addEventListener('click', e => { const b=e.target.closest('button[data-c]'); if(!b) return; const p=D.clips[b.dataset.c].posts[b.dataset.p]; copy((p.caption+'\n\n'+p.tags).trim(), b) });
list.addEventListener('change', e => { if(e.target.type==='checkbox'){ state[e.target.id]=e.target.checked; save() } });
</script></body></html>
"""
