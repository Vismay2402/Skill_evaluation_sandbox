"""Configuration loading: repo defaults merged with per-skill overrides."""
from __future__ import annotations

import copy
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
VENDOR_DIR = Path(__file__).resolve().parent / "vendor" / "skill_creator"
DEFAULT_CONFIG = REPO_ROOT / "skilleval.config.yaml"


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(skill_path: Path | None = None, config_path: Path | None = None,
                overrides: dict | None = None) -> dict:
    cfg = yaml.safe_load((config_path or DEFAULT_CONFIG).read_text()) or {}
    if skill_path is not None:
        skill_cfg = Path(skill_path) / "evals" / "eval-config.yaml"
        if skill_cfg.exists():
            cfg = _deep_merge(cfg, yaml.safe_load(skill_cfg.read_text()) or {})
    if overrides:
        cfg = _deep_merge(cfg, overrides)
    ex = cfg["execution"]
    if isinstance(ex.get("models"), str):
        ex["models"] = [m.strip() for m in ex["models"].split(",") if m.strip()]
    if not ex.get("models"):
        ex["models"] = [ex.get("model", "claude-sonnet-5-5")]
    ex["model"] = ex["models"][0]
    cfg.setdefault("verdict", {})
    cfg.setdefault("report", {"analyst": True, "pdf": True})
    return cfg
