"""Grades a run's outputs against its expectations, producing skill-creator's grading.json.

- Script expectations are run deterministically (exit 0 = pass).
- Text expectations go to the skill-creator grader agent (agents/grader.md) via `claude -p`.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from .config import VENDOR_DIR
from .evals import AUTO_NOT_TRIGGERED, EvalCase
from .usage import claude_json

GRADER_MD = (VENDOR_DIR / "agents" / "grader.md").read_text()


def _run_script(skill_root: Path, script: str, run_dir: Path) -> tuple[bool, str]:
    try:
        p = subprocess.run([sys.executable, str(skill_root / script), str(run_dir / "outputs"), str(run_dir)],
                           cwd=skill_root, capture_output=True, text=True, timeout=120)
        evidence = (p.stdout.strip() or p.stderr.strip() or f"exit code {p.returncode}")[-800:]
        return p.returncode == 0, f"[script {script}] {evidence}"
    except subprocess.TimeoutExpired:
        return False, f"[script {script}] timed out"


def _llm_grade(texts: list[str], run_dir: Path, model: str, timeout: int) -> dict:
    prompt = (
        f"{GRADER_MD}\n\n---\n\n## Your inputs\n\n"
        f"- expectations: {json.dumps(texts, indent=2)}\n"
        f"- transcript_path: {run_dir / 'transcript.md'}\n"
        f"- outputs_dir: {run_dir / 'outputs'}\n\n"
        f"Write your result as JSON to {run_dir / 'grading.json'} following the grading.json "
        "schema (expectations[] with fields text, passed, evidence; summary; claims; "
        "user_notes_summary; eval_feedback). Grade every expectation, in the same order, "
        "with the exact expectation text."
    )
    gfile = run_dir / "grading.json"
    gfile.unlink(missing_ok=True)
    text, stderr, _ = claude_json(prompt, model, run_dir, timeout, usage_file=run_dir / "grader_usage.json")
    if gfile.exists():
        try:
            return json.loads(gfile.read_text())
        except json.JSONDecodeError:
            pass
    m = re.search(r"\{[\s\S]*\}", text or "")
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return {"expectations": [{"text": t, "passed": False, "evidence": "grader produced no parseable result"}
                             for t in texts], "grader_error": (stderr or "")[-2000:]}


def _mock_grade(texts: list[str], run_dir: Path, config: str) -> dict:
    exps = []
    for t in texts:
        h = int(hashlib.md5(f"{t}{run_dir}".encode()).hexdigest()[:6], 16) % 100
        passed = h < (88 if config in ("with_skill", "new_skill") else 45)
        exps.append({"text": t, "passed": passed, "evidence": f"[mock] {'found' if passed else 'missing'}"})
    return {"expectations": exps}


def grade_run(case: EvalCase, run_dir: Path, config: str, skill_root: Path, executor: str,
              grader_model: str, timeout: int = 600) -> dict:
    script_exps = [e for e in case.expectations if e.script]
    text_exps = [e.text for e in case.expectations if not e.script]

    if not text_exps:
        grading = {"expectations": []}
    elif executor == "mock":
        grading = _mock_grade(text_exps, run_dir, config)
        (run_dir / "grader_usage.json").write_text(json.dumps(
            {"input": 900, "output": 250, "cache_read": 3000, "cache_write": 0, "total": 4150,
             "cost_usd": 0.0, "calls": 1}))
    else:
        grading = _llm_grade(text_exps, run_dir, grader_model, timeout)

    by_text = {e.get("text"): e for e in grading.get("expectations", [])}
    ordered = []
    metrics_file = run_dir / "outputs" / "metrics.json"
    metrics = json.loads(metrics_file.read_text()) if metrics_file.exists() else {}
    for e in case.expectations:
        if e.script == AUTO_NOT_TRIGGERED:
            loaded = metrics.get("skill_triggered")
            ordered.append({"text": e.text, "passed": loaded is not True,
                            "evidence": "skill was loaded" if loaded else
                            ("skill not loaded" if loaded is False else "skill not installed in this run")})
        elif e.script:
            passed, ev = _run_script(skill_root, e.script, run_dir)
            ordered.append({"text": e.text, "passed": passed, "evidence": ev})
        else:
            got = by_text.get(e.text) or {"text": e.text, "passed": False,
                                          "evidence": "not graded (grader omitted this expectation)"}
            ordered.append({"text": e.text, "passed": bool(got.get("passed")),
                            "evidence": got.get("evidence", "")})
    passed = sum(1 for e in ordered if e["passed"])
    grading["expectations"] = ordered
    grading["summary"] = {"passed": passed, "failed": len(ordered) - passed, "total": len(ordered),
                          "pass_rate": round(passed / len(ordered), 4) if ordered else 0.0}
    if metrics:
        grading["execution_metrics"] = metrics
    # Leave timing to timing.json so the vendored aggregator picks up both time and tokens.
    grading.pop("timing", None)
    (run_dir / "grading.json").write_text(json.dumps(grading, indent=2))
    return grading
