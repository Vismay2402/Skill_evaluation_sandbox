"""Automated quality gate + analyst notes computed from benchmark.json.

The gate decides whether a run is *eligible* for human review; the human approval in
CI is what actually lets results and the skill into the repo.
"""
from __future__ import annotations

import json
import statistics
from collections import defaultdict
from pathlib import Path

PRIMARY = ("with_skill", "new_skill")


def split_configs(benchmark: dict) -> tuple[str | None, str | None]:
    configs = [c for c in benchmark["run_summary"] if c != "delta"]
    primary = next((c for c in configs if c in PRIMARY), configs[0] if configs else None)
    baseline = next((c for c in configs if c != primary), None)
    return primary, baseline


def analyst_notes(benchmark: dict, cfg: dict) -> list[str]:
    """Deterministic version of skill-creator's analyzer pass (agents/analyzer.md)."""
    notes: list[str] = []
    primary, baseline = split_configs(benchmark)
    by_assert: dict[str, dict[str, list[bool]]] = defaultdict(lambda: defaultdict(list))
    by_eval: dict[tuple, list[float]] = defaultdict(list)
    for r in benchmark["runs"]:
        by_eval[(r["eval_id"], r["configuration"])].append(r["result"]["pass_rate"])
        for e in r.get("expectations", []):
            by_assert[e["text"]][r["configuration"]].append(bool(e["passed"]))

    if baseline:
        for text, per in by_assert.items():
            if "(automatic check)" not in text and per.get(primary) and per.get(baseline) and all(per[primary]) and all(per[baseline]):
                notes.append(f"Non-discriminating assertion (passes with and without the skill): '{text[:90]}'")
    for text, per in by_assert.items():
        if per.get(primary) and not any(per[primary]):
            notes.append(f"Assertion never passes with the skill: '{text[:90]}'")
    for (eid, conf), rates in sorted(by_eval.items()):
        if len(rates) > 1 and statistics.stdev(rates) > cfg["gate"]["warn_stddev_above"]:
            notes.append(f"Eval {eid} ({conf}) is high-variance "
                         f"({statistics.mean(rates):.0%} ± {statistics.stdev(rates):.0%}) - possibly flaky")
    rs = benchmark["run_summary"]
    if primary and baseline and rs[baseline]["tokens"]["mean"]:
        pct = (rs[primary]["tokens"]["mean"] / rs[baseline]["tokens"]["mean"] - 1) * 100
        if pct > cfg["gate"]["warn_token_increase_pct"]:
            notes.append(f"Skill increases token usage by {pct:.0f}% vs {baseline}")
    return notes


def evaluate_gate(benchmarks: dict[str, dict], trigger: dict | None, last_approved: dict | None,
                  cfg: dict, rates: dict[str, dict] | None = None) -> dict:
    """benchmarks: one skill-creator benchmark per model.
    rates: optional {model: {config: pooled pass rate}} from the results matrix, so the gate uses the
    same numbers as the report (pooled checks rather than the mean of per-run rates)."""
    g = cfg["gate"]
    checks = []

    def check(name, ok, value, threshold, hard=True):
        checks.append({"name": name, "status": "pass" if ok else ("fail" if hard else "warn"),
                       "value": value, "threshold": threshold})

    primary_rates = {}
    for model, benchmark in benchmarks.items():
        primary, baseline = split_configs(benchmark)
        rs = benchmark["run_summary"]
        tag = f" [{model}]" if len(benchmarks) > 1 else ""
        total_assertions = sum(r["result"]["total"] for r in benchmark["runs"] if r["configuration"] == primary)
        check("has_assertions" + tag, total_assertions > 0, total_assertions, "> 0")
        pooled = (rates or {}).get(model, {})
        pr = pooled.get(primary, rs[primary]["pass_rate"]["mean"] if primary else 0.0)
        primary_rates[model] = pr
        check("pass_rate" + tag, pr >= g["min_pass_rate"], round(pr, 4), g["min_pass_rate"])
        if baseline:
            delta = pr - pooled.get(baseline, rs[baseline]["pass_rate"]["mean"])
            check(f"delta_vs_{baseline}" + tag, delta >= g["min_delta_vs_baseline"], round(delta, 4),
                  g["min_delta_vs_baseline"])
        errs = benchmark["metadata"].get("harness_error_runs", 0)
        check("harness_errors" + tag, errs <= g["max_error_runs"], errs, g["max_error_runs"])
        prev = ((last_approved or {}).get("models") or {}).get(model, {}).get("with")
        if prev is None and last_approved and model == next(iter(benchmarks)):
            prev = last_approved.get("pass_rate")
        if prev is not None:
            reg = prev - pr
            check("regression_vs_last_approved" + tag, reg <= g["max_regression_vs_last_approved"],
                  round(-reg, 4), f">= -{g['max_regression_vs_last_approved']}")
    if trigger:
        acc = trigger["summary"]["accuracy"]
        check("trigger_accuracy", acc >= g["min_trigger_accuracy"], acc, g["min_trigger_accuracy"])
    elif cfg["trigger_eval"]["enabled"]:
        check("trigger_evals_present", False, "none", "evals/trigger_evals.json", hard=False)

    status = "fail" if any(c["status"] == "fail" for c in checks) else "pass"
    first = next(iter(benchmarks))
    return {"status": status, "pass_rate": round(primary_rates.get(first, 0.0), 4),
            "pass_rate_by_model": {m: round(v, 4) for m, v in primary_rates.items()}, "checks": checks}


def load_last_approved(results_dir: Path, skill: str) -> dict | None:
    f = results_dir / skill / "latest.json"
    return json.loads(f.read_text()) if f.exists() else None


def load_history(results_dir: Path, skill: str) -> list[dict]:
    f = results_dir / skill / "history.json"
    return json.loads(f.read_text()) if f.exists() else []
