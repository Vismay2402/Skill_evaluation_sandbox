"""Skill distribution: release checks, packaging, channels and the catalog.

State lives in git, next to the evaluations:

  distribution.yaml                     org settings and release policy
  distribution/<skill>.yaml             release manifest per skill (version, owner, status, channels, changelog)
  releases/<skill>/<version>.json       one record per release (what, when, who, checksum, evaluation, channels)
  marketplace/plugins/<skill>/          frozen copy of the released version, as a Claude Code plugin
  .claude-plugin/marketplace.json       the Claude Code marketplace (released, non-retired skills only)
  distribution/claude-code-settings.json  managed-settings snippet that adds the marketplace for everyone
  CATALOG.md                            human catalog: what is released, where, which version, how to install
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import yaml

from skilleval.config import REPO_ROOT, load_config
from skilleval.discover import all_skills, tree_hash
from skilleval.evals import skill_identity

DIST_CONFIG = REPO_ROOT / "distribution.yaml"
MANIFEST_DIR = REPO_ROOT / "distribution"
RELEASES_DIR = REPO_ROOT / "releases"
MARKET_DIR = REPO_ROOT / "marketplace" / "plugins"
MARKET_FILE = REPO_ROOT / ".claude-plugin" / "marketplace.json"
CATALOG = REPO_ROOT / "CATALOG.md"
SETTINGS_SNIPPET = MANIFEST_DIR / "claude-code-settings.json"
STATUSES = ("active", "deprecated", "retired")
EXCLUDE_DIRS = {"evals", "__pycache__", ".git"}
EXCLUDE_FILES = {".DS_Store", "Thumbs.db"}
SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


# ---- config and manifests -------------------------------------------------------------------------
def dist_config() -> dict:
    cfg = yaml.safe_load(DIST_CONFIG.read_text()) or {}
    cfg.setdefault("marketplace", {}).setdefault("bundles", {})
    cfg.setdefault("channels", {"claude_code": True, "claude_ai": True, "api": False})
    cfg.setdefault("policy", {})
    cfg.setdefault("notify", {})
    return cfg


def skills_dir() -> Path:
    return REPO_ROOT / load_config()["skills_dir"]


def results_dir() -> Path:
    return REPO_ROOT / load_config()["results_dir"]


def manifest(skill: str) -> dict | None:
    f = MANIFEST_DIR / f"{skill}.yaml"
    if not f.exists():
        return None
    m = yaml.safe_load(f.read_text()) or {}
    m["version"] = str(m.get("version", ""))
    m.setdefault("status", "active")
    m.setdefault("channels", [c for c, on in dist_config()["channels"].items() if on])
    m.setdefault("audience", "all")
    m["changelog"] = {str(k): v for k, v in (m.get("changelog") or {}).items()}
    return m


def save_manifest_field(skill: str, field: str, value) -> None:
    """Change one top-level field in distribution/<skill>.yaml, keeping comments."""
    f = MANIFEST_DIR / f"{skill}.yaml"
    text = f.read_text()
    rendered = "null" if value is None else (value if isinstance(value, str) else json.dumps(value))
    pat = re.compile(rf"^{field}:[ \t]*[^\n#]*?([ \t]*#[^\n]*)?$", re.M)
    if pat.search(text):
        text = pat.sub(lambda mm: f"{field}: {rendered}" + (("  " + mm.group(1).strip()) if mm.group(1) else ""),
                       text, count=1)
    else:
        text = text.rstrip("\n") + f"\n{field}: {rendered}\n"
    f.write_text(text)


def vtuple(v: str) -> tuple:
    m = SEMVER.match(v or "")
    return tuple(int(x) for x in m.groups()) if m else (-1,)


def releases(skill: str) -> list[dict]:
    d = RELEASES_DIR / skill
    out = [json.loads(p.read_text()) for p in d.glob("*.json")] if d.exists() else []
    return sorted(out, key=lambda r: vtuple(r["version"]), reverse=True)


def latest_release(skill: str) -> dict | None:
    rs = releases(skill)
    return rs[0] if rs else None


def approved_evaluation(skill: str) -> dict | None:
    f = results_dir() / skill / "latest.json"
    return json.loads(f.read_text()) if f.exists() else None


# ---- release check ------------------------------------------------------------------------------
def check(skill: str, action: str = "release") -> dict:
    """Everything a release reviewer needs to know. errors block; warnings are shown to the reviewer."""
    cfg, pol = dist_config(), dist_config()["policy"]
    sd = skills_dir() / skill
    errors, warnings, info = [], [], {}
    m = manifest(skill)
    if not (sd / "SKILL.md").exists():
        errors.append(f"skills/{skill}/SKILL.md not found")
        return {"skill": skill, "ok": False, "errors": errors, "warnings": warnings, "info": info}
    if m is None:
        errors.append(f"distribution/{skill}.yaml is missing - copy another skill's manifest and edit it")
        return {"skill": skill, "ok": False, "errors": errors, "warnings": warnings, "info": info}
    name, description = skill_identity(sd)
    info.update({"name": name, "description": description, "version": m["version"], "owner": m.get("owner"),
                 "status": m["status"], "channels": m["channels"], "audience": m["audience"],
                 "content_hash": tree_hash(sd)})
    if m["status"] not in STATUSES:
        errors.append(f"status must be one of {', '.join(STATUSES)}")
    if name != skill:
        errors.append(f"SKILL.md name '{name}' must equal the folder name '{skill}' (the API and plugins key on it)")
    if not m.get("owner"):
        errors.append("owner is missing")
    unknown = [c for c in m["channels"] if c not in cfg["channels"]]
    if unknown:
        errors.append(f"unknown channels: {', '.join(unknown)}")
    off = [c for c in m["channels"] if c in cfg["channels"] and not cfg["channels"][c]]
    if off:
        warnings.append(f"channels switched off in distribution.yaml are skipped: {', '.join(off)}")

    if action != "release":
        return {"skill": skill, "ok": not errors, "errors": errors, "warnings": warnings, "info": info}

    if m["status"] != "active":
        errors.append(f"status is '{m['status']}' - only active skills are released")
    if not SEMVER.match(m["version"]):
        errors.append(f"version '{m['version']}' is not semver (e.g. 1.2.0)")
    prev = latest_release(skill)
    info["previous_version"] = prev["version"] if prev else None
    if prev and vtuple(m["version"]) <= vtuple(prev["version"]):
        errors.append(f"version {m['version']} must be higher than the last release {prev['version']}")
    if prev and prev.get("content_hash") == info["content_hash"]:
        warnings.append("the skill content is identical to the last release (only metadata changed)")
    if pol.get("require_changelog", True) and not m["changelog"].get(m["version"]):
        errors.append(f"changelog has no entry for {m['version']}")

    # evaluation evidence
    ev = approved_evaluation(skill)
    info["evaluation"] = ev
    if pol.get("require_approved_evaluation", True):
        if not ev:
            errors.append("no approved evaluation - run Actions > Skill evaluation for this skill and approve it")
        else:
            if ev.get("skill_version") != info["content_hash"]:
                errors.append(f"the approved evaluation is for content {ev.get('skill_version')}, but the skill is now "
                              f"{info['content_hash']} - re-evaluate the current version")
            if pol.get("require_gate_pass", True) and ev.get("gate_status") != "pass":
                errors.append(f"the approved evaluation did not pass the automated gate ({ev.get('gate_status')})")
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(ev["approved_at"])).days
            info["evaluation_age_days"] = age
            if age > int(pol.get("max_evaluation_age_days", 90)):
                errors.append(f"the approved evaluation is {age} days old (max {pol['max_evaluation_age_days']})")
            if "unreviewed" in str(ev.get("approved_by", "")):
                errors.append("the evaluation was stored without a human approval (no required reviewers were set)")
            v = ev.get("verdict")
            if v in pol.get("block_verdicts", ["HARMFUL"]):
                errors.append(f"usefulness verdict is {v}")
            elif v in pol.get("warn_verdicts", []):
                warnings.append(f"usefulness verdict is {v} - newer models may not need this skill")

    # package safety
    files = package_files(sd)
    info["files"] = [str(p.relative_to(sd)) for p in files]
    size = sum(p.stat().st_size for p in files)
    info["package_kb"] = round(size / 1024, 1)
    if size > float(pol.get("max_package_mb", 25)) * 1024 * 1024:
        errors.append(f"package is {size / 1048576:.1f} MB (max {pol['max_package_mb']} MB)")
    bad_ext = [str(p.relative_to(sd)) for p in files if p.suffix.lower() in pol.get("forbidden_extensions", [])]
    if bad_ext:
        errors.append("forbidden file types: " + ", ".join(bad_ext))
    pats = [re.compile(p) for p in pol.get("secret_patterns", [])]
    urls, scripts = set(), []
    for p in files:
        try:
            text = p.read_text(errors="ignore")
        except OSError:
            continue
        for pat in pats:
            if pat.search(text):
                errors.append(f"possible secret in {p.relative_to(sd)} (pattern {pat.pattern[:30]}...)")
                break
        urls.update(re.findall(r"https?://[^\s)\"'>]+", text))
        if p.suffix in (".py", ".sh", ".js", ".ts", ".ps1", ".rb"):
            scripts.append(str(p.relative_to(sd)))
    if scripts:
        warnings.append("contains scripts Claude may run - review them: " + ", ".join(scripts))
    if urls:
        info["external_urls"] = sorted(urls)[:20]
        warnings.append(f"references {len(urls)} external URL(s) - check they are approved")
    return {"skill": skill, "ok": not errors, "errors": errors, "warnings": warnings, "info": info}


def render_check(results: list[dict]) -> str:
    lines = []
    for r in results:
        i = r["info"]
        ev = i.get("evaluation") or {}
        icon = "✅" if r["ok"] else "❌"
        lines += [f"### {icon} `{r['skill']}` {i.get('version', '')}"
                  + (f" (previous {i['previous_version']})" if i.get("previous_version") else " (first release)"), "",
                  f"> {i.get('description', '')}", "",
                  f"Owner `{i.get('owner')}` · status `{i.get('status')}` · channels "
                  f"{', '.join(f'`{c}`' for c in i.get('channels', []))} · audience `{i.get('audience')}` · "
                  f"content `{i.get('content_hash')}` · {i.get('package_kb', '?')} KB, {len(i.get('files', []))} file(s)", ""]
        if ev:
            lines.append(f"Evaluation: verdict **{ev.get('verdict') or 'n/a'}**, pass rate {ev.get('pass_rate', 0):.0%}, "
                         f"gate {ev.get('gate_status')}, approved by {ev.get('approved_by')} on "
                         f"{str(ev.get('approved_at', ''))[:10]} ({ev.get('results_path')})")
            lines.append("")
        lines += [f"- ❌ {e}" for e in r["errors"]] + [f"- ⚠️ {w}" for w in r["warnings"]]
        if not r["errors"] and not r["warnings"]:
            lines.append("- All release checks passed.")
        lines.append("")
    return "\n".join(lines)


# ---- packaging ----------------------------------------------------------------------------------
def package_files(sd: Path) -> list[Path]:
    return sorted(p for p in sd.rglob("*") if p.is_file() and p.name not in EXCLUDE_FILES
                  and not (EXCLUDE_DIRS & set(p.relative_to(sd).parts)))


def package(skill: str, out_dir: Path) -> tuple[Path, str]:
    """<skill>-<version>.zip with one top-level folder <skill>/ holding SKILL.md - the format Claude.ai
    (Organization settings > Plugins & skills > Upload) and the Skills API both accept."""
    sd, m = skills_dir() / skill, manifest(skill)
    out_dir.mkdir(parents=True, exist_ok=True)
    zp = out_dir / f"{skill}-{m['version']}.zip"
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
        for p in package_files(sd):
            info = zipfile.ZipInfo(f"{skill}/{p.relative_to(sd).as_posix()}", date_time=(2020, 1, 1, 0, 0, 0))
            info.external_attr = 0o644 << 16
            z.writestr(info, p.read_bytes(), zipfile.ZIP_DEFLATED)
    return zp, hashlib.sha256(zp.read_bytes()).hexdigest()


# ---- Claude Code marketplace --------------------------------------------------------------------
def write_plugin(skill: str, version: str, description: str, owner: str, category: str | None,
                 tags: list | None, deprecated_note: str = "") -> Path:
    """Frozen copy of the released skill as a plugin: marketplace/plugins/<skill>/skills/<skill>/SKILL.md."""
    root = MARKET_DIR / skill
    if root.exists():
        shutil.rmtree(root)
    dest = root / "skills" / skill
    dest.mkdir(parents=True)
    sd = skills_dir() / skill
    for p in package_files(sd):
        t = dest / p.relative_to(sd)
        t.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, t)
    (root / ".claude-plugin").mkdir()
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({
        "name": skill, "version": version, "description": (deprecated_note + description)[:1000],
        "author": {"name": str(owner)}, **({"keywords": list(tags)} if tags else {})}, indent=2) + "\n")
    return root


def rebuild_marketplace() -> dict:
    """.claude-plugin/marketplace.json from the released, non-retired skills (+ bundles)."""
    cfg = dist_config()
    mk = cfg["marketplace"]
    plugins = []
    for skill in sorted(p.name for p in MARKET_DIR.iterdir()) if MARKET_DIR.exists() else []:
        if skill.startswith("bundle-"):
            continue
        m, rel = manifest(skill), latest_release(skill)
        if not m or not rel or m["status"] == "retired":
            continue
        pj = json.loads((MARKET_DIR / skill / ".claude-plugin" / "plugin.json").read_text())
        entry = {"name": skill, "source": f"./marketplace/plugins/{skill}", "description": pj["description"],
                 "version": pj["version"]}
        if m.get("category"):
            entry["category"] = str(m["category"])
        if m.get("tags"):
            entry["tags"] = [str(t) for t in m["tags"]]
        plugins.append(entry)
    released = {p["name"] for p in plugins}
    for bname, b in (mk.get("bundles") or {}).items():
        members = [s for s in b.get("skills", []) if s in released]
        if not members:
            continue
        root = MARKET_DIR / f"bundle-{bname}"
        if root.exists():
            shutil.rmtree(root)
        for s in members:
            shutil.copytree(MARKET_DIR / s / "skills" / s, root / "skills" / s)
        version = "+".join(f"{s}-{latest_release(s)['version']}" for s in members)
        (root / ".claude-plugin").mkdir(parents=True)
        (root / ".claude-plugin" / "plugin.json").write_text(json.dumps(
            {"name": bname, "version": hashlib.sha256(version.encode()).hexdigest()[:8],
             "description": b.get("description", f"Bundle: {', '.join(members)}"),
             "author": {"name": str(mk.get("owner", {}).get("name", "AI Platform"))}}, indent=2) + "\n")
        plugins.append({"name": bname, "source": f"./marketplace/plugins/bundle-{bname}",
                        "description": b.get("description", ""), "category": "Bundle"})
    data = {"name": mk["name"], "owner": mk.get("owner", {"name": "AI Platform"}),
            "description": mk.get("description", ""), "plugins": plugins}
    if mk.get("remove_retired_from_users"):
        data["forceRemoveDeletedPlugins"] = True
    MARKET_FILE.parent.mkdir(parents=True, exist_ok=True)
    MARKET_FILE.write_text(json.dumps(data, indent=2) + "\n")
    return data


def write_settings_snippet(repo: str | None = None) -> Path:
    """Managed-settings snippet IT can roll out so every Claude Code user gets the marketplace and skills."""
    mk = dist_config()["marketplace"]
    repo = repo or _repo_slug()
    # Individual skills only: bundles repeat the same skills, so enabling both would load them twice.
    enabled = {f"{p['name']}@{mk['name']}": True for p in json.loads(MARKET_FILE.read_text())["plugins"]
               if p.get("category") != "Bundle"} if MARKET_FILE.exists() else {}
    SETTINGS_SNIPPET.write_text(json.dumps({
        "extraKnownMarketplaces": {mk["name"]: {"source": {"source": "github", "repo": repo}}},
        "enabledPlugins": enabled}, indent=2) + "\n")
    return SETTINGS_SNIPPET


def _repo_slug() -> str:
    import os
    import subprocess
    if os.environ.get("GITHUB_REPOSITORY"):
        return os.environ["GITHUB_REPOSITORY"]
    url = subprocess.run(["git", "remote", "get-url", "origin"], cwd=REPO_ROOT, capture_output=True,
                         text=True).stdout.strip()
    m = re.search(r"github\.com[:/](.+?)(\.git)?$", url)
    return m.group(1) if m else "OWNER/REPO"


# ---- catalog ------------------------------------------------------------------------------------
def _channels_cell(rel: dict | None) -> str:
    if not rel:
        return "-"
    out = []
    for c, v in (rel.get("channels_done") or {}).items():
        out.append(f"{c} (pending)" if isinstance(v, str) and v.startswith("pending") else c)
    return ", ".join(out) or "-"


def write_catalog() -> Path:
    cfg = dist_config()
    mk, repo = cfg["marketplace"]["name"], _repo_slug()
    rows, details = [], []
    for skill in all_skills(skills_dir()):
        m, rel, ev = manifest(skill), latest_release(skill), approved_evaluation(skill)
        _, desc = skill_identity(skills_dir() / skill)
        status = (m or {}).get("status", "-")
        if not m:
            state = "no release manifest"
        elif not rel:
            state = "not released yet"
        elif status == "retired":
            state = f"retired (last release {rel['version']})"
        elif rel.get("content_hash") != tree_hash(skills_dir() / skill):
            state = f"{rel['version']} released - newer content waiting for release"
        else:
            state = f"{rel['version']} released"
        rows.append(f"| [`{skill}`](#{skill}) | {status} | {state} | {(rel or {}).get('released_at', '-')[:10]} | "
                    f"{(ev or {}).get('verdict') or '-'} | {(m or {}).get('owner', '-')} | "
                    f"{_channels_cell(rel)} |")
        lines = [f"### {skill}", "", f"> {desc}", ""]
        if m and m.get("status") == "deprecated":
            lines += [f"**Deprecated**" + (f" - use `{m['replaced_by']}` instead." if m.get("replaced_by") else "."), ""]
        if rel:
            cd = rel.get("channels_done", {})
            lines += [f"- **Version** {rel['version']} · released {rel['released_at'][:10]} by {rel['approved_by']} · "
                      f"[evaluation]({rel.get('evaluation_path', '')}) · sha256 `{rel['sha256'][:12]}…`"]
            if "claude_code" in cd:
                lines.append(f"- **Claude Code:** `/plugin marketplace add {repo}` once, then "
                             f"`/plugin install {skill}@{mk}`")
            if "claude_ai" in cd:
                lines.append(f"- **Claude.ai:** provisioned by an Owner in Organization settings > Plugins & skills "
                             f"({cd['claude_ai']})")
            if "api" in cd:
                lines.append(f"- **Claude API:** skill id `{cd['api'].get('skill_id')}` "
                             f"(version `{cd['api'].get('version')}`)")
            if m and m.get("changelog"):
                lines.append("- **Changelog:** " + "; ".join(f"{k}: {v}" for k, v in sorted(
                    m["changelog"].items(), key=lambda kv: vtuple(kv[0]), reverse=True)[:5]))
        details += lines + [""]
    text = ["# Skill catalog", "",
            "Approved Claude skills and where to get them. Generated by `python -m skilldist catalog` on every "
            "release - do not edit by hand.", "",
            "| Skill | Status | Release | Released | Usefulness verdict | Owner | Channels |",
            "|---|---|---|---|---|---|---|", *rows, "",
            "## How to get the skills", "",
            f"- **Claude Code:** `/plugin marketplace add {repo}`, then `/plugin install <skill>@{mk}`. "
            f"IT can add it for everyone with [distribution/claude-code-settings.json](distribution/claude-code-settings.json) "
            "in managed settings.",
            "- **Claude.ai (Team / Enterprise):** released skills are provisioned for the organization by an Owner and "
            "appear under Settings > Capabilities > Skills, switched on.",
            "- **Claude API:** use the skill id listed for the skill.", "", "## Skills", "", *details]
    CATALOG.write_text("\n".join(text))
    return CATALOG


# ---- release / deprecate / retire ---------------------------------------------------------------
def release(skill: str, approver: str, out_dir: Path, run_url: str = "", note: str = "",
            api_result: dict | None = None) -> dict:
    r = check(skill)
    if not r["ok"]:
        raise SystemExit(f"{skill}: release check failed:\n" + "\n".join(r["errors"]))
    m, info = manifest(skill), r["info"]
    zp, sha = package(skill, out_dir)
    cfg = dist_config()
    channels = [c for c in m["channels"] if cfg["channels"].get(c)]
    done = {}
    if "claude_code" in channels:
        write_plugin(skill, m["version"], info["description"], m["owner"], m.get("category"), m.get("tags"))
        done["claude_code"] = f"{skill}@{cfg['marketplace']['name']}"
    if "claude_ai" in channels:
        done["claude_ai"] = "pending upload by an Owner"
    if "api" in channels and api_result:
        done["api"] = api_result
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ev = info.get("evaluation") or {}
    rec = {"skill": skill, "version": m["version"], "content_hash": info["content_hash"], "sha256": sha,
           "package": zp.name, "released_at": now, "approved_by": approver, "note": note, "run_url": run_url,
           "owner": m["owner"], "channels": channels, "channels_done": done, "audience": m["audience"],
           "changelog": m["changelog"].get(m["version"], ""), "evaluation_path": ev.get("results_path"),
           "evaluation_verdict": ev.get("verdict"), "evaluation_pass_rate": ev.get("pass_rate"),
           "warnings": r["warnings"], "files": info["files"]}
    (RELEASES_DIR / skill).mkdir(parents=True, exist_ok=True)
    (RELEASES_DIR / skill / f"{m['version']}.json").write_text(json.dumps(rec, indent=2) + "\n")
    _update_registry(skill, rec)
    rebuild_marketplace()
    write_settings_snippet()
    write_catalog()
    return rec


def change_status(skill: str, status: str, approver: str, reason: str = "", replaced_by: str | None = None) -> dict:
    assert status in ("deprecated", "retired", "active")
    m = manifest(skill)
    if m is None:
        raise SystemExit(f"distribution/{skill}.yaml not found")
    save_manifest_field(skill, "status", status)
    if replaced_by is not None:
        save_manifest_field(skill, "replaced_by", replaced_by or None)
    rel = latest_release(skill)
    if rel and status == "deprecated" and (MARKET_DIR / skill).exists():
        note = f"[DEPRECATED{' - use ' + replaced_by if replaced_by else ''}] "
        _, desc = skill_identity(skills_dir() / skill)
        pj = MARKET_DIR / skill / ".claude-plugin" / "plugin.json"
        d = json.loads(pj.read_text())
        d["description"] = (note + desc)[:1000]
        pj.write_text(json.dumps(d, indent=2) + "\n")
    if status == "retired" and (MARKET_DIR / skill).exists():
        shutil.rmtree(MARKET_DIR / skill)
    if status == "active" and rel and "claude_code" in rel.get("channels_done", {}):
        if rel.get("content_hash") == tree_hash(skills_dir() / skill):
            _, desc = skill_identity(skills_dir() / skill)   # restore the released version as it was
            write_plugin(skill, rel["version"], desc, m["owner"], m.get("category"), m.get("tags"))
        else:
            print(f"{skill}: the skill changed since {rel['version']} - release a new version to put it back "
                  "in the marketplace")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _update_registry(skill, None, {"status": status, "status_changed_at": now, "status_changed_by": approver,
                                   "status_reason": reason, "replaced_by": replaced_by})
    rebuild_marketplace()
    write_settings_snippet()
    write_catalog()
    return {"skill": skill, "status": status, "reason": reason, "replaced_by": replaced_by, "at": now,
            "by": approver, "version": (rel or {}).get("version")}


def _update_registry(skill: str, rec: dict | None, extra: dict | None = None) -> None:
    reg_file = REPO_ROOT / load_config()["registry_file"]
    reg = json.loads(reg_file.read_text()) if reg_file.exists() else {"skills": {}}
    e = reg["skills"].setdefault(skill, {})
    if rec:
        e["release"] = {k: rec[k] for k in ("version", "released_at", "approved_by", "sha256", "channels_done",
                                             "content_hash")}
        e["distribution_status"] = "active"
    if extra:
        e["distribution_status"] = extra["status"]
        e["release_status_change"] = extra
    reg["updated_at"] = datetime.now(timezone.utc).isoformat()
    reg_file.parent.mkdir(parents=True, exist_ok=True)
    reg_file.write_text(json.dumps(reg, indent=2, sort_keys=True) + "\n")


def pending_releases() -> list[str]:
    """Active skills whose manifest version is newer than their last release."""
    out = []
    for skill in all_skills(skills_dir()):
        m, rel = manifest(skill), latest_release(skill)
        if m and m["status"] == "active" and SEMVER.match(m["version"]) and \
                (not rel or vtuple(m["version"]) > vtuple(rel["version"])):
            out.append(skill)
    return out


def lint() -> list[str]:
    """Cheap PR check: every skill has a valid manifest; no hand edits to generated files are needed."""
    problems = []
    for skill in all_skills(skills_dir()):
        m = manifest(skill)
        if m is None:
            problems.append(f"{skill}: distribution/{skill}.yaml is missing")
            continue
        if m["status"] not in STATUSES:
            problems.append(f"{skill}: status '{m['status']}' is not one of {STATUSES}")
        if not SEMVER.match(m["version"]):
            problems.append(f"{skill}: version '{m['version']}' is not semver")
        if not m.get("owner"):
            problems.append(f"{skill}: owner is missing")
        unknown = [c for c in m["channels"] if c not in dist_config()["channels"]]
        if unknown:
            problems.append(f"{skill}: unknown channels {unknown}")
        rel = latest_release(skill)
        if rel and vtuple(m["version"]) < vtuple(rel["version"]):
            problems.append(f"{skill}: version {m['version']} is lower than released {rel['version']}")
    for f in MANIFEST_DIR.glob("*.yaml"):
        if not (skills_dir() / f.stem / "SKILL.md").exists():
            problems.append(f"distribution/{f.name} has no matching skills/{f.stem}/")
    return problems
