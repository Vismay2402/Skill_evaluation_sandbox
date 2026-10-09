"""Which skills need evaluating, and which new Claude models have been released.

Used by .github/workflows/skill-eval-scheduler.yml (daily). A skill is DUE when:
  - it has never been approved, or
  - its content changed since the approved evaluation, or
  - the approved evaluation is older than schedule.every_days (per-skill override possible), or
  - it was never evaluated on a model in execution.models (e.g. you just added a new model), or
  - a newly released model (see new_models) has not been evaluated yet.
"""
from __future__ import annotations

import json
import os
import subprocess
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .config import REPO_ROOT, load_config
from .discover import all_skills, tree_hash


def _age_days(iso: str | None) -> int | None:
    if not iso:
        return None
    t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return (datetime.now(timezone.utc) - t).days


def _gh(path: str):
    p = subprocess.run(["gh", "api", path], capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        raise RuntimeError(p.stderr[-300:])
    return json.loads(p.stdout)


def pending_results(repo: str | None = None) -> dict:
    """Approved results that sit in open results PRs (approved, not merged yet): {skill: {pr, version, url}}.
    Uses the GitHub REST API via `gh`; returns {} when gh or the API is not available."""
    repo = repo or os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        return {}
    import base64
    out = {}
    try:
        for pr in _gh(f"repos/{repo}/pulls?state=open&per_page=50"):
            files = _gh(f"repos/{repo}/pulls/{pr['number']}/files?per_page=100")
            for f in files:
                parts = f["filename"].split("/")
                if len(parts) == 3 and parts[0] == "eval-results" and parts[2] == "latest.json":
                    c = _gh(f"repos/{repo}/contents/{f['filename']}?ref={pr['head']['ref']}")
                    latest = json.loads(base64.b64decode(c["content"]))
                    out[parts[1]] = {"pr": pr["number"], "url": pr["html_url"],
                                     "version": latest.get("skill_version"),
                                     "approved_at": latest.get("approved_at", ""),
                                     "models": latest.get("evaluated_models") or [latest.get("model")]}
    except Exception as e:  # never break the scheduler on API problems
        print(f"[skilleval] could not check open results PRs: {e}")
    return out


def due_skills(extra_models: list[str] | None = None, pending: dict | None = None) -> list[dict]:
    """Skills needing an evaluation. Skills whose approved results wait in an open PR for the current
    version are returned with due=False and a note, so they are reported but not re-run."""
    base = load_config()
    skills_dir, results_dir = REPO_ROOT / base["skills_dir"], REPO_ROOT / base["results_dir"]
    out = []
    for s in all_skills(skills_dir):
        cfg = load_config(skills_dir / s)
        sch = cfg.get("schedule", {})
        every = int(sch.get("every_days", 30))
        latest_f = results_dir / s / "latest.json"
        latest = json.loads(latest_f.read_text()) if latest_f.exists() else None
        reasons = []
        pend = (pending or {}).get(s)
        current = tree_hash(skills_dir / s)
        if pend and pend.get("version") == current:
            missing = [m for m in list(cfg["execution"]["models"]) + list(extra_models or [])
                       if m not in (pend.get("models") or [])]
            out.append({"skill": s, "due": bool(missing), "last_approved": pend["approved_at"][:10],
                        "verdict": None, "reasons": [f"approved results are waiting to be merged in PR #{pend['pr']}"]
                        + (["not yet evaluated on " + ", ".join(dict.fromkeys(missing))] if missing else []),
                        "pr": pend["pr"]})
            continue
        if not latest:
            reasons.append("no approved evaluation on the main branch yet")
        else:
            if latest.get("skill_version") != current:
                reasons.append("skill changed since its approved evaluation")
            age = _age_days(latest.get("approved_at"))
            if age is not None and age >= every:
                reasons.append(f"last approved evaluation is {age} days old (every {every} days)")
            done = set(latest.get("evaluated_models") or [latest.get("model")])
            missing = [m for m in list(cfg["execution"]["models"]) + list(extra_models or []) if m not in done]
            if missing:
                reasons.append("not yet evaluated on " + ", ".join(dict.fromkeys(missing)))
        if reasons:
            out.append({"skill": s, "due": True, "reasons": reasons, "last_approved": (latest or {}).get("approved_at", "")[:10],
                        "verdict": (latest or {}).get("verdict")})
    return out


def list_models() -> list[dict]:
    """Models available to this account: Anthropic Models API, or Bedrock when CLAUDE_CODE_USE_BEDROCK=1."""
    if os.environ.get("CLAUDE_CODE_USE_BEDROCK") == "1":
        p = subprocess.run(["aws", "bedrock", "list-foundation-models", "--by-provider", "anthropic",
                            "--output", "json"], capture_output=True, text=True, check=True)
        return [{"id": m["modelId"], "display_name": m.get("modelName", m["modelId"]), "created_at": None}
                for m in json.loads(p.stdout).get("modelSummaries", [])]
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    models, after = [], None
    while True:
        url = "https://api.anthropic.com/v1/models?limit=100" + (f"&after_id={after}" if after else "")
        req = urllib.request.Request(url, headers={"x-api-key": key, "anthropic-version": "2023-06-01"})
        with urllib.request.urlopen(req, timeout=30) as r:
            page = json.loads(r.read())
        models += page.get("data", [])
        if not page.get("has_more"):
            return models
        after = page.get("last_id")


def new_models(since: str | None = None) -> list[dict]:
    """Models released on/after `since` (schedule.models_known_before) - candidates to evaluate skills on."""
    since = since or str(load_config().get("schedule", {}).get("models_known_before", "1970-01-01"))
    out = []
    for m in list_models():
        created = (m.get("created_at") or "")[:10]
        if created and created >= since and m["id"].startswith(("claude-", "anthropic.claude")):
            out.append({"id": m["id"], "name": m.get("display_name", m["id"]), "released": created})
    return sorted(out, key=lambda m: m["released"])


def webhook(text: str) -> None:
    """Optional chat notification (Slack incoming webhook, or a Teams/Power Automate flow accepting {text})."""
    url = os.environ.get("SKILL_EVAL_WEBHOOK_URL")
    if not url:
        return
    req = urllib.request.Request(url, data=json.dumps({"text": text}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=20).read()
    except Exception as e:  # a broken webhook must not fail the scheduler
        print(f"[skilleval] webhook failed: {e}")
