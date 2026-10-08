"""Derived report content: the skill profile, token usage breakdown, specific failures, the
Skills 2.0 step-by-step record and re-benchmark guidance. Everything here is computed from files the
run produced - no model calls."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import usage as U
from .evals import skill_identity
from .matrix import config_summary

DESCRIPTION_LIMIT = 1024  # skill-creator quick_validate limit


def skill_profile(skill_path: Path) -> dict:
    name, description = skill_identity(skill_path)
    body = (skill_path / "SKILL.md").read_text()
    bundled = sorted(str(p.relative_to(skill_path)) for p in skill_path.rglob("*")
                     if p.is_file() and p.name != "SKILL.md" and "evals" not in p.relative_to(skill_path).parts
                     and "__pycache__" not in p.parts)
    d = description.lower()
    return {
        "name": name, "description": description, "description_chars": len(description),
        "description_limit": DESCRIPTION_LIMIT,
        "says_when_to_use": any(k in d for k in ("use when", "use this when", "use this whenever", "whenever")),
        "says_when_not_to_use": any(k in d for k in ("not for", "do not use", "don't use", "not when", "instead")),
        "skill_md_lines": body.count("\n") + 1, "skill_md_tokens_est": len(body) // 4,
        "bundled_files": bundled,
    }


def other_skill_descriptions(skill_path: Path) -> dict:
    out = {}
    for p in sorted(skill_path.parent.iterdir()):
        if p != skill_path and (p / "SKILL.md").exists():
            try:
                n, d = skill_identity(p)
                out[n] = d
            except Exception:  # a broken sibling must not break this evaluation
                continue
    return out


def usage_breakdown(out: Path, matrix: dict) -> dict:
    """Tokens and cost per stage. Skill runs are split by model and configuration."""
    it = out / "iteration"
    runs = {}
    for m in matrix["models"]:
        for conf in matrix["configs"]:
            acc = U.empty()
            for t in (it / m).glob(f"*/{conf}/run-*/timing.json"):
                d = json.loads(t.read_text())
                u = d.get("usage") or {"total": d.get("total_tokens", 0), "cost_usd": d.get("cost_usd") or 0,
                                       "calls": 1}
                acc = U.add(acc, u)
            runs[f"{m}|{conf}"] = acc
    stages = {
        "Skill runs (executor)": U.total_of([]),
        "Grading (grader agent)": U.total_of(it.rglob("grader_usage.json")),
        "Blind A/B comparison (comparator agent)": U.total_of((out / "comparisons").rglob("usage.json"))
        if (out / "comparisons").exists() else U.empty(),
        "Analysis (analyzer agent)": U.total_of([out / "analyst_usage.json"]),
    }
    for v in runs.values():
        stages["Skill runs (executor)"] = U.add(stages["Skill runs (executor)"], v)
    total = U.empty()
    for v in stages.values():
        total = U.add(total, v)
    return {"by_run_config": runs, "by_stage": stages, "total": total,
            "not_metered": ["Trigger evals (run_eval.py streams events and stops early; usage is not reported)"]}


def specific_failures(matrix: dict, primary: str, limit: int = 40) -> list[dict]:
    """Checks the skill configuration failed, with the grader's evidence (Skills 2.0: 'specific failures')."""
    out = []
    for m in matrix["models"]:
        for c in matrix["cases"]:
            for n, r in enumerate(matrix["results"].get(m, {}).get(primary, {}).get(str(c["id"]), []), 1):
                for i, ok in enumerate(r["passed"]):
                    if not ok:
                        out.append({"model": m, "case": c["id"], "run": n, "check": c["checks"][i]["text"],
                                    "evidence": (r["evidence"][i] if i < len(r["evidence"]) else "")[:400]})
    return out[:limit]


def target_for(cfg: dict) -> tuple[str, float]:
    t = cfg.get("targets", {})
    kind = t.get("skill_kind", "preference")
    return kind, float(t.get("critical_pass_rate", 0.9) if kind == "critical" else t.get("preference_pass_rate", 0.8))


def rebenchmark(cfg: dict, last_approved: dict | None) -> dict:
    days = int(cfg.get("targets", {}).get("rebenchmark_days", 90))
    now = datetime.now(timezone.utc)
    return {"last_approved": (last_approved or {}).get("approved_at", "")[:10] or None,
            "next_due": (now + timedelta(days=days)).strftime("%Y-%m-%d"), "every_days": days,
            "when": ["after a model update (add the model to execution.models, or the monthly review)",
                     "after significant edits to the skill (pull requests touching skills/ run automatically)",
                     f"at least every {days} days"]}


def steps(ctx: dict) -> list[dict]:
    """The Skills 2.0 eval / benchmark / trigger workflow, as actually run, with each step's result."""
    mx, primary, baseline = ctx["matrix"], ctx["primary"], ctx["baseline"]
    models = mx["models"]
    n_runs = sum(len(r) for m in mx["results"].values() for conf in m.values() for r in conf.values())
    rates = {m: config_summary(mx, m, primary)["pass_rate"] for m in models}
    kind, target = ctx["target"]
    comp, trig, an = ctx.get("comparison"), ctx.get("trigger"), ctx["analyst"]
    fails = ctx["failures"]
    src = sorted({c.get("source", "evals/evals.json") for c in ctx["case_meta"]})
    rows = [
        {"stage": "Build", "step": "Validate the skill",
         "component": "scripts/quick_validate.py",
         "result": f"SKILL.md valid; description {ctx['profile']['description_chars']}/{ctx['profile']['description_limit']} chars",
         "status": "done"},
        {"stage": "Eval", "step": "1. Define test cases", "component": "evals/evals.json (skill-creator schema)",
         "result": f"{len(mx['cases'])} cases, {sum(len(c['checks']) for c in mx['cases'])} checks; "
                   f"types: {', '.join(sorted({c['type'] for c in mx['cases']}))}; source: {', '.join(src)}",
         "status": "done" if 5 <= len(mx["cases"]) <= 10 else "warn",
         "note": "" if 5 <= len(mx["cases"]) <= 10 else "Skills 2.0 guidance: 5-10 cases including edge cases"},
        {"stage": "Eval", "step": "2. Run the eval (parallel, clean contexts)",
         "component": "headless Claude Code, one isolated agent per run",
         "result": f"{n_runs} runs on {', '.join(ctx['labels'][m] for m in models)}; up to "
                   f"{ctx['max_parallel']} in parallel, each in an empty folder", "status": "done"},
        {"stage": "Eval", "step": "Grade outputs", "component": "agents/grader.md + check scripts",
         "result": "pass rate " + ", ".join(f"{ctx['labels'][m]} {rates[m]:.0%}" for m in models)
                   + f"; {len(fails)} failed check(s) with the skill", "status": "done"},
        {"stage": "Eval", "step": "Metrics: response time and tokens per case",
         "component": "scripts/aggregate_benchmark.py -> benchmark.json",
         "result": f"{ctx['usage']['total']['total']:,} tokens metered in total, "
                   f"${ctx['usage']['total']['cost_usd']:.2f}", "status": "done"},
        {"stage": "Eval", "step": "3. Fix failures", "component": "analyzer agent (agents/analyzer.md)",
         "result": f"{len(an.get('fix_suggestions', []))} fix suggestion(s), {len(an.get('does_not_help', []))} "
                   "weak spot(s) listed in the report", "status": "done"},
        {"stage": "Eval", "step": "4. Re-run until the threshold is met",
         "component": f"target for a {kind} skill: {target:.0%}",
         "result": ", ".join(f"{ctx['labels'][m]} {'meets' if rates[m] >= target else 'below'} ({rates[m]:.0%})"
                             for m in models),
         "status": "done" if all(rates[m] >= target for m in models) else "warn",
         "note": "" if all(rates[m] >= target for m in models) else "fix the failures and re-run the workflow"},
    ]
    if baseline:
        if comp:
            r = ", ".join(f"{ctx['labels'][m]} {v['win_rate']:.0%} ({v['interpretation']})"
                          for m, v in comp["by_model"].items() if v["win_rate"] is not None)
            rows.append({"stage": "A/B benchmark", "step": "Blind comparison vs " + baseline.replace("_", " "),
                         "component": "agents/comparator.md (blind A/B)", "result": "win rate " + (r or "n/a"),
                         "status": "done"})
        else:
            rows.append({"stage": "A/B benchmark", "step": "Blind comparison", "component": "agents/comparator.md",
                         "result": "disabled in config", "status": "skipped"})
        rows.append({"stage": "A/B benchmark", "step": "Interpret: keep, refine or remove",
                     "component": "usefulness verdict (checks) + win rate", "result": ctx["decision"]["overall"],
                     "status": "done"})
    else:
        rows.append({"stage": "A/B benchmark", "step": "Compare with raw Claude", "component": "-",
                     "result": "no baseline in this run", "status": "skipped"})
    dr = an.get("description_review") or {}
    if trig:
        s = trig["summary"]
        rows.append({"stage": "Trigger optimization", "step": "Test borderline prompts",
                     "component": "scripts/run_eval.py (trigger evals)",
                     "result": f"{s['passed']}/{s['total']} correct, recall "
                               f"{'-' if s['recall'] is None else format(s['recall'], '.0%')}, false triggers "
                               f"{'-' if s['false_trigger_rate'] is None else format(s['false_trigger_rate'], '.0%')}",
                     "status": "done"})
    else:
        rows.append({"stage": "Trigger optimization", "step": "Test borderline prompts",
                     "component": "scripts/run_eval.py", "result": "no evals/trigger_evals.json or disabled",
                     "status": "skipped"})
    rows.append({"stage": "Trigger optimization", "step": "Diagnose and revise the description",
                 "component": "description review (analyzer)",
                 "result": f"{dr.get('assessment', 'n/a')}" + ("; rewrite suggested" if dr.get("suggested_description") else ""),
                 "status": "done"})
    rows.append({"stage": "Review", "step": "Inspect every output", "component": "eval-viewer/generate_review.py",
                 "result": "review.html in the run artifact", "status": "done"})
    rows.append({"stage": "Maintain", "step": "Version in git with results",
                 "component": "human gate -> eval-results/, history.json, registry",
                 "result": f"skill version {ctx['version'][:12]}; stored after approval", "status": "pending"})
    rows.append({"stage": "Maintain", "step": "Re-benchmark", "component": "monthly review / PR trigger",
                 "result": f"next due {ctx['rebench']['next_due']}", "status": "scheduled"})
    return rows
