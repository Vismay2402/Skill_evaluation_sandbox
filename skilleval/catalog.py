"""Evaluation history, built from the approved result folders in eval-results/.

Every approved evaluation is a folder eval-results/<skill>/<UTC timestamp>-<version>/ holding run.json and
approval.json, so the history is derived from git itself - nothing to keep in sync. This writes:

  EVALUATIONS.md                         every skill: how many evaluations, first / last, current verdict
  eval-results/<skill>/EVALUATIONS.md    one row per evaluation with timestamp, version, models, results
"""
from __future__ import annotations

import json
from pathlib import Path

from .config import REPO_ROOT, load_config
from .discover import all_skills


def _j(p: Path) -> dict:
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def evaluations(results_dir: Path, skill: str) -> list[dict]:
    rows = []
    for d in sorted((results_dir / skill).glob("*-*/")):
        run, appr = _j(d / "run.json"), _j(d / "approval.json")
        if not run:
            continue
        rows.append({
            "approved_at": appr.get("approved_at", ""), "evaluated_at": run.get("finished_at", ""),
            "version": run.get("skill_version", ""), "models": run.get("models", []),
            "baseline": next((c for c in run.get("configurations", [])[1:]), "none"),
            "cases": len(run.get("evals", [])), "runs": run.get("runs_per_config"),
            "test_cases": ", ".join(run.get("test_case_source", ["evals/evals.json"])),
            "pass_rate": run.get("pass_rate"), "verdict": run.get("verdict"), "gate": run.get("gate_status"),
            "win_rate": run.get("win_rate_by_model") or {}, "tokens": run.get("tokens_total"),
            "cost": run.get("cost_usd"), "approved_by": appr.get("approved_by", ""), "note": appr.get("note", ""),
            "run_url": appr.get("workflow_run", ""), "path": str(d.relative_to(REPO_ROOT)),
        })
    return sorted(rows, key=lambda r: r["approved_at"] or r["evaluated_at"], reverse=True)


def _ts(iso: str) -> str:
    return iso[:16].replace("T", " ") + " UTC" if iso else "-"


def skill_page(skill: str, rows: list[dict], description: str) -> str:
    out = [f"# `{skill}` - evaluation history", "", f"> {description}", "",
           f"**{len(rows)} approved evaluation(s)**" + (f", latest {_ts(rows[0]['approved_at'])}" if rows else ""), "",
           "| # | Approved (UTC) | Version | Models | Baseline | Test cases | Pass rate | A/B win | Verdict | Gate | "
           "Tokens | Cost | Approved by | Results |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(rows):
        win = ", ".join(f"{v:.0%}" for v in r["win_rate"].values() if v is not None) or "-"
        link = f"[report]({Path(r['path']).name}/report.pdf) · [summary]({Path(r['path']).name}/summary.md)"
        if r["run_url"]:
            link += f" · [run]({r['run_url']})"
        out.append(f"| {len(rows) - i} | {_ts(r['approved_at'])} | `{r['version'][:8]}` | {', '.join(r['models'])} | "
                   f"{r['baseline'].replace('_', ' ')} | {r['cases']} ({r['test_cases']}) | "
                   f"{(r['pass_rate'] or 0):.0%} | {win} | {r['verdict'] or '-'} | {r['gate'] or '-'} | "
                   f"{(r['tokens'] or 0):,} | ${(r['cost'] or 0):.2f} | {r['approved_by']} | {link} |")
    notes = [f"- {_ts(r['approved_at'])} - {r['approved_by']}: {r['note']}" for r in rows if r["note"]]
    if notes:
        out += ["", "## Reviewer notes", ""] + notes
    return "\n".join(out) + "\n"


def write_all() -> Path:
    cfg = load_config()
    skills_dir, results_dir = REPO_ROOT / cfg["skills_dir"], REPO_ROOT / cfg["results_dir"]
    from .evals import skill_identity
    index = ["# Skill evaluations", "",
             "Generated from `eval-results/` on every approval (`python -m skilleval catalog`). "
             "Each row links to the skill's full history.", "",
             "| Skill | Evaluations | First | Latest | Current verdict | Pass rate | Models (latest) | Approved by |",
             "|---|---|---|---|---|---|---|---|"]
    names = sorted(set(all_skills(skills_dir)) | {p.name for p in results_dir.iterdir() if p.is_dir()}
                   if results_dir.exists() else all_skills(skills_dir))
    for s in names:
        rows = evaluations(results_dir, s)
        try:
            _, desc = skill_identity(skills_dir / s)
        except Exception:
            desc = "(skill folder no longer exists)"
        if rows:
            (results_dir / s / "EVALUATIONS.md").write_text(skill_page(s, rows, desc))
            r = rows[0]
            index.append(f"| [`{s}`]({results_dir.name}/{s}/EVALUATIONS.md) | {len(rows)} | {_ts(rows[-1]['approved_at'])[:10]} | "
                         f"{_ts(r['approved_at'])} | {r['verdict'] or '-'} | {(r['pass_rate'] or 0):.0%} | "
                         f"{', '.join(r['models'])} | {r['approved_by']} |")
        else:
            index.append(f"| `{s}` | 0 | - | - | not evaluated | - | - | - |")
    out = REPO_ROOT / "EVALUATIONS.md"
    out.write_text("\n".join(index) + "\n")
    return out
