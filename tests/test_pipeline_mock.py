"""End-to-end test of the pipeline with the mock executor (no model calls).

Run: pip install pytest pyyaml && pytest -q tests/
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def sk(*args, cwd=ROOT):
    return subprocess.run([sys.executable, "-m", "skilleval", *args], cwd=cwd, capture_output=True, text=True)


def test_validate_sample_skill():
    r = sk("validate", "--skill", "csv-data-profiler")
    assert r.returncode == 0, r.stdout + r.stderr


def test_run_produces_skill_creator_artifacts(tmp_path):
    ws = tmp_path / "ws"
    r = sk("run", "--skill", "csv-data-profiler", "--executor", "mock", "--runs", "2",
           "--workspace", str(ws), "--allow-failed-gate")
    assert r.returncode == 0, r.stderr
    out = ws / "csv-data-profiler"
    for f in ("benchmark.json", "benchmark.md", "gate.json", "summary.md", "review.html", "run.json",
              "report.pdf", "verdict.json", "matrix.json", "analyst.json", "comparison.json", "usage.json",
              "steps.json", "test_cases.json"):
        assert (out / f).exists(), f
    bench = json.loads((out / "benchmark.json").read_text())
    assert set(bench["run_summary"]) >= {"with_skill", "without_skill", "delta"}
    assert len(bench["runs"]) == 3 * 2 * 2  # evals x configs x runs (first model)
    verdict = json.loads((out / "verdict.json").read_text())
    assert verdict["overall"] in {"KEEP", "KEEP, SLIM DOWN", "RETIRE CANDIDATE", "HARMFUL"}
    assert [p["model"] for p in verdict["models"]] == json.loads((out / "run.json").read_text())["models"]
    for g in out.rglob("grading.json"):
        for e in json.loads(g.read_text())["expectations"]:
            assert {"text", "passed", "evidence"} <= set(e)
    status = json.loads((ws / "status.json").read_text())
    assert status["evaluated"] == ["csv-data-profiler"]
    usage = json.loads((out / "usage.json").read_text())
    assert usage["total"]["total"] > 0 and usage["by_stage"]["Grading (grader agent)"]["calls"] == 3 * 2 * 2 * 2
    comp = json.loads((out / "comparison.json").read_text())
    assert all(v["interpretation"] in {"KEEP", "REFINE", "REMOVE OR REWRITE"} for v in comp["by_model"].values())
    summary = (out / "summary.md").read_text()
    for heading in ("Skill under test", "Skills 2.0", "Token usage", "A/B benchmark", "Test cases used",
                    "Trigger optimization", "Specific failures"):
        assert heading in summary, heading


def test_custom_test_cases(tmp_path):
    cases = tmp_path / "mine.csv"
    cases.write_text('prompt,type,expectations,files\n'
                     '"profile inputs/orders.csv",standard,"[quality] profile.md exists | reports 8 rows",'
                     'skills/csv-data-profiler/evals/files/orders.csv\n')
    assert sk("validate", "--skill", "csv-data-profiler", "--test-cases", str(cases)).returncode == 0
    ws = tmp_path / "ws"
    r = sk("run", "--skill", "csv-data-profiler", "--executor", "mock", "--workspace", str(ws),
           "--test-cases", str(cases), "--test-cases-mode", "append", "--allow-failed-gate", "--no-trigger")
    assert r.returncode == 0, r.stderr
    used = json.loads((ws / "csv-data-profiler" / "test_cases.json").read_text())
    assert len(used) == 4 and used[-1]["source"].startswith("custom:")
    assert used[-1]["eval_id"] == 4  # renumbered after the skill's own cases


def test_verdict_rules():
    from skilleval.verdict import decide
    def mx(with_, without):
        cases = [{"id": 1, "description": "d", "type": "standard",
                  "checks": [{"text": f"c{i}", "category": "quality"} for i in range(len(with_))]}]
        run = lambda bits: [{"passed": bits, "tokens": 100, "seconds": 1.0}]
        return {"models": ["m"], "cases": cases,
                "results": {"m": {"with_skill": {"1": run(with_)}, "without_skill": {"1": run(without)}}}}
    d = lambda w, wo: decide(mx(w, wo), "with_skill", "without_skill", {}, {})["overall"]
    assert d([1] * 10, [0] * 10) == "KEEP"
    assert d([1] * 10, [1] * 10) == "RETIRE CANDIDATE"
    assert d([0] + [1] * 9, [1] * 10) == "HARMFUL"
    assert d([1] * 10, [0] * 1 + [1] * 9) == "KEEP, SLIM DOWN"   # +10 pts
