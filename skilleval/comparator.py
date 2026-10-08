"""Blind A/B benchmark (Skills 2.0 "A/B benchmarking: is your skill helping?").

For every model, case and run, the with-skill output and the baseline output are shown to
skill-creator's blind comparator agent (agents/comparator.md) as "A" and "B" in a random but
reproducible order. The comparator does not know which one used the skill. Win rate =
(wins + half the ties) / comparisons.

Interpretation (from the Skills 2.0 guide): >= 70% keep, 50-70% refine or question it, < 50% remove
or rewrite. This complements the check-based verdict: checks measure what the eval author asked for,
the comparator judges overall quality.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .config import VENDOR_DIR
from .usage import claude_json

COMPARATOR_MD = (VENDOR_DIR / "agents" / "comparator.md").read_text()


def interpret(win_rate: float | None) -> str:
    if win_rate is None:
        return "NOT RUN"
    if win_rate >= 0.70:
        return "KEEP"
    if win_rate >= 0.50:
        return "REFINE"
    return "REMOVE OR REWRITE"


def _swap(key: str) -> bool:
    return int(hashlib.md5(key.encode()).hexdigest()[:4], 16) % 2 == 1


def _stage(run_dir: Path, dest: Path) -> None:
    """Copy only what the comparator may see: the output files and the final response."""
    if dest.exists():
        shutil.rmtree(dest)
    src = run_dir / "outputs"
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("metrics.json")) if src.exists() else dest.mkdir(parents=True)


def _compare_one(job: dict, model: str, executor: str, timeout: int) -> dict:
    cdir: Path = job["dir"]
    cdir.mkdir(parents=True, exist_ok=True)
    swapped = _swap(job["key"])
    a_run, b_run = (job["baseline_run"], job["primary_run"]) if swapped else (job["primary_run"], job["baseline_run"])
    _stage(a_run, cdir / "A")
    _stage(b_run, cdir / "B")
    if executor == "mock":
        h = int(hashlib.md5(job["key"].encode()).hexdigest()[:4], 16) % 100
        skill_wins = h < 72
        tie = 72 <= h < 78
        winner = "TIE" if tie else (("B" if swapped else "A") if skill_wins else ("A" if swapped else "B"))
        res = {"winner": winner, "reasoning": "[mock] comparison without model calls",
               "output_quality": {"A": {"score": 7}, "B": {"score": 6}}}
        (cdir / "usage.json").write_text(json.dumps({"input": 1200, "output": 400, "cache_read": 5000,
                                                     "cache_write": 0, "total": 6600, "cost_usd": 0.0,
                                                     "calls": 1}))
    else:
        prompt = (f"{COMPARATOR_MD}\n\n---\n\n## Your inputs\n\n- output_a_path: {cdir / 'A'}\n"
                  f"- output_b_path: {cdir / 'B'}\n- eval_prompt: {json.dumps(job['prompt'])}\n"
                  f"- expectations: {json.dumps(job['expectations'])}\n\n"
                  f"Each folder holds the files produced plus final_response.md (the chat reply). "
                  f"Write your result JSON to {cdir / 'comparison.json'}.")
        (cdir / "comparison.json").unlink(missing_ok=True)
        text, _, _ = claude_json(prompt, model, cdir, timeout, usage_file=cdir / "usage.json")
        res = None
        for t in ([(cdir / "comparison.json").read_text()] if (cdir / "comparison.json").exists() else []) + [text]:
            m = re.search(r"\{[\s\S]*\}", t or "")
            if m:
                try:
                    res = json.loads(m.group(0))
                    break
                except json.JSONDecodeError:
                    continue
        res = res or {"winner": "ERROR", "reasoning": "comparator produced no parseable result"}
    w = str(res.get("winner", "")).upper()
    skill_label = "B" if swapped else "A"
    outcome = "error" if w not in ("A", "B", "TIE") else "tie" if w == "TIE" else \
        ("skill" if w == skill_label else "baseline")
    q = res.get("output_quality") or {}
    out = {"model": job["model"], "case": job["case"], "run": job["run"], "skill_was": skill_label,
           "winner": w, "outcome": outcome, "reasoning": str(res.get("reasoning", ""))[:600],
           "skill_score": (q.get(skill_label) or {}).get("score"),
           "baseline_score": (q.get("A" if skill_label == "B" else "B") or {}).get("score")}
    (cdir / "comparison.json").write_text(json.dumps({**res, "_unblinded": out}, indent=2))
    return out


def run_comparisons(out_dir: Path, matrix: dict, cases, primary: str, baseline: str | None, cfg: dict) -> dict | None:
    ccfg = cfg.get("comparison", {})
    if not baseline or not ccfg.get("enabled", True):
        return None
    it = out_dir / "iteration"
    jobs = []
    for model in matrix["models"]:
        for case in cases:
            if case.type == "should_not_trigger" and not ccfg.get("include_should_not_trigger", False):
                continue
            for p_run in sorted((it / model / case.dirname / primary).glob("run-*")):
                b_run = it / model / case.dirname / baseline / p_run.name
                if not b_run.exists():
                    continue
                jobs.append({"model": model, "case": case.id, "run": p_run.name, "primary_run": p_run,
                             "baseline_run": b_run, "prompt": case.prompt,
                             "expectations": [e.text for e in case.expectations],
                             "key": f"{model}|{case.id}|{p_run.name}",
                             "dir": out_dir / "comparisons" / model / case.dirname / p_run.name})
    if not jobs:
        return None
    ex = cfg["execution"]
    judge = ccfg.get("model") or ex["grader_model"]
    with ThreadPoolExecutor(max_workers=ex["max_parallel"]) as pool:
        results = list(pool.map(lambda j: _compare_one(j, judge, ex["executor"], ccfg.get("timeout_seconds", 900)),
                                jobs))
    by_model = {}
    for m in matrix["models"]:
        rs = [r for r in results if r["model"] == m and r["outcome"] != "error"]
        wins = sum(r["outcome"] == "skill" for r in rs)
        ties = sum(r["outcome"] == "tie" for r in rs)
        rate = (wins + 0.5 * ties) / len(rs) if rs else None
        by_model[m] = {"comparisons": len(rs), "skill_wins": wins, "ties": ties,
                       "baseline_wins": sum(r["outcome"] == "baseline" for r in rs),
                       "errors": sum(1 for r in results if r["model"] == m and r["outcome"] == "error"),
                       "win_rate": None if rate is None else round(rate, 4), "interpretation": interpret(rate)}
    out = {"judge_model": judge, "baseline": baseline, "by_model": by_model, "results": results}
    (out_dir / "comparison.json").write_text(json.dumps(out, indent=2))
    return out
