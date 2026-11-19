"""Load settings: config.default.yaml < ./clipper.yaml < ./campaigns/<name>.yaml < CLI flags."""
from __future__ import annotations

import copy
from pathlib import Path

import yaml

from .util import ClipperError, slugify

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PLUGIN_ROOT / "config.default.yaml"

# Campaign-file keys that override top-level settings.
CAMPAIGN_TOP_LEVEL = {"min_seconds", "max_seconds", "reframe", "clips_per_video", "niche",
                      "language", "split", "captions", "variation", "schedule"}
CAMPAIGN_FIELDS = {"name", "brief", "required_mentions", "required_hashtags", "extra_hashtags",
                   "banned_words", "notes"}


def deep_merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _load_yaml(path: Path) -> dict:
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ClipperError(f"{path} has a formatting error: {e}")
    if not isinstance(data, dict):
        raise ClipperError(f"{path} should contain settings like `key: value`.")
    return data


def find_campaign(name: str, cwd: Path | None = None) -> Path | None:
    cwd = Path(cwd or Path.cwd())
    p = Path(name)
    if p.suffix in (".yaml", ".yml") and p.exists():
        return p
    for cand in (cwd / "campaigns" / f"{name}.yaml", cwd / "campaigns" / f"{name}.yml",
                 cwd / "campaigns" / f"{slugify(name)}.yaml"):
        if cand.exists():
            return cand
    return None


def apply_campaign(cfg: dict, campaign: dict) -> dict:
    top = {k: v for k, v in campaign.items() if k in CAMPAIGN_TOP_LEVEL and v not in (None, "")}
    camp = {k: v for k, v in campaign.items() if k in CAMPAIGN_FIELDS}
    nested = campaign.get("campaign") if isinstance(campaign.get("campaign"), dict) else {}
    cfg = deep_merge(cfg, top)
    cfg["campaign"] = deep_merge(cfg.get("campaign") or {}, {**nested, **camp})
    for key in ("required_mentions", "required_hashtags", "extra_hashtags", "banned_words"):
        val = cfg["campaign"].get(key) or []
        if isinstance(val, str):
            val = [x.strip() for x in val.replace(",", " ").split() if x.strip()]
        cfg["campaign"][key] = list(val)
    return cfg


def load_config(cwd: Path | None = None, campaign: str | None = None,
                overrides: dict | None = None) -> dict:
    cwd = Path(cwd or Path.cwd())
    cfg = _load_yaml(DEFAULT_CONFIG)
    user = cwd / "clipper.yaml"
    if user.exists():
        cfg = deep_merge(cfg, _load_yaml(user))
    cfg = apply_campaign(cfg, {})
    if campaign:
        path = find_campaign(campaign, cwd)
        if not path:
            raise ClipperError(f"Campaign '{campaign}' not found. Expected ./campaigns/{campaign}.yaml "
                               f"(create it with /ai-clipper:campaign {campaign}).")
        data = _load_yaml(path)
        data.setdefault("name", campaign)
        cfg = apply_campaign(cfg, data)
    if overrides:
        cfg = deep_merge(cfg, {k: v for k, v in overrides.items() if v is not None})
    if cfg.get("reframe") not in ("face", "blur", "split"):
        raise ClipperError(f"reframe must be face, blur or split (got {cfg.get('reframe')!r}).")
    if float(cfg["min_seconds"]) > float(cfg["max_seconds"]):
        cfg["min_seconds"], cfg["max_seconds"] = cfg["max_seconds"], cfg["min_seconds"]
    return cfg
