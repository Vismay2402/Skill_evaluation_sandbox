"""Collects every graded run into one results matrix: model x configuration x case x run.

The report, the verdict and the PR summary are all computed from this one structure, so the
numbers in every output agree with each other.
"""
from __future__ import annotations

import json
from pathlib import Path
from statistics import mean

from .evals import EvalCase


def _read(p: Path) -> dict:
    return json.loads(p.read_text()) if p.exists() else {}


def build_matrix(iteration: Path, models: list[str], configs: list[str], cases: list[EvalCase]) -> dict:
    results: dict = {}
    for m in models:
        for conf in configs:
            for case in cases:
                runs = []
                for run_dir in sorted((iteration / m / case.dirname / conf).glob("run-*")):
                    g = _read(run_dir / "grading.json")
                    t = _read(run_dir / "timing.json")
                    met = _read(run_dir / "outputs" / "metrics.json")
                    final = run_dir / "outputs" / "final_response.md"
                    runs.append({
                        "passed": [bool(e.get("passed")) for e in g.get("expectations", [])],
                        "evidence": [e.get("evidence", "") for e in g.get("expectations", [])],
                        "tokens": t.get("total_tokens", 0) or 0,
                        "seconds": t.get("total_duration_seconds", 0.0) or 0.0,
                        "cost_usd": t.get("cost_usd") or 0.0,
                        "skill_triggered": met.get("skill_triggered"),
                        "harness_error": bool(met.get("harness_error")),
                        "files": met.get("files_created", []),
                        "final_response": final.read_text()[:1500] if final.exists() else "",
                        "run_dir": str(run_dir.relative_to(iteration.parent)),
                    })
                results.setdefault(m, {}).setdefault(conf, {})[str(case.id)] = runs
    return {
        "models": models,
        "configs": configs,
        "cases": [{"id": c.id, "name": c.name, "description": c.description, "type": c.type,
                   "prompt": c.prompt,
                   "checks": [{"text": e.text, "category": e.category, "deterministic": bool(e.script)}
                              for e in c.expectations]} for c in cases],
        "results": results,
    }


# ---- derived numbers -------------------------------------------------------------------------

def check_rate(matrix: dict, model: str, conf: str, case_id, idx: int) -> float | None:
    runs = matrix["results"].get(model, {}).get(conf, {}).get(str(case_id), [])
    vals = [r["passed"][idx] for r in runs if idx < len(r["passed"])]
    return mean(vals) if vals else None


def case_score(matrix: dict, model: str, conf: str, case_id) -> tuple[float, int]:
    """(mean checks passed per run, total checks) for one case."""
    runs = matrix["results"].get(model, {}).get(conf, {}).get(str(case_id), [])
    total = len(next(c for c in matrix["cases"] if str(c["id"]) == str(case_id))["checks"])
    if not runs:
        return 0.0, total
    return mean(sum(r["passed"]) for r in runs), total


def case_cost(matrix: dict, model: str, conf: str, case_id) -> tuple[float, float]:
    runs = matrix["results"].get(model, {}).get(conf, {}).get(str(case_id), [])
    if not runs:
        return 0.0, 0.0
    return mean(r["tokens"] for r in runs), mean(r["seconds"] for r in runs)


def config_summary(matrix: dict, model: str, conf: str) -> dict:
    passed = total = full = 0.0
    tokens = seconds = 0.0
    for c in matrix["cases"]:
        p, t = case_score(matrix, model, conf, c["id"])
        passed += p
        total += t
        runs = matrix["results"].get(model, {}).get(conf, {}).get(str(c["id"]), [])
        if runs and mean(float(all(r["passed"])) for r in runs) >= 0.5:
            full += 1
        tk, sec = case_cost(matrix, model, conf, c["id"])
        tokens += tk
        seconds += sec
    return {"checks_passed": passed, "checks_total": int(total),
            "pass_rate": passed / total if total else 0.0, "cases_fully_passed": int(full),
            "cases_total": len(matrix["cases"]), "tokens": tokens, "seconds": seconds}


def runs_per_config(matrix: dict) -> int:
    for m in matrix["results"].values():
        for conf in m.values():
            for runs in conf.values():
                return len(runs)
    return 0
