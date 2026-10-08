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
              "report.pdf", "verdict.json", "matrix.json", "analyst.json"):
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
