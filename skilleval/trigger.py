"""Description-trigger evaluation using skill-creator's scripts/run_eval.py (claude -p)."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from .config import VENDOR_DIR


def run_trigger_eval(skill_path: Path, eval_set: list[dict], out_file: Path, cfg: dict) -> dict:
    t = cfg["trigger_eval"]
    if cfg["execution"]["executor"] == "mock":
        results = []
        for item in eval_set:
            h = int(hashlib.md5(item["query"].encode()).hexdigest()[:4], 16) % 100
            ok = h < 95
            rate = (0.67 if ok else 0.0) if item["should_trigger"] else (0.0 if ok else 0.67)
            results.append({"query": item["query"], "should_trigger": item["should_trigger"],
                            "trigger_rate": rate, "triggers": round(rate * 3), "runs": 3, "pass": ok})
        out = {"skill_name": skill_path.name, "results": results}
    else:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".claude").mkdir()   # isolated project root for run_eval's command file
            set_file = Path(tmp) / "trigger_evals.json"
            set_file.write_text(json.dumps(eval_set))
            env = {**{k: v for k, v in os.environ.items() if k != "CLAUDECODE"},
                   "PYTHONPATH": str(VENDOR_DIR)}
            proc = subprocess.run(
                [sys.executable, "-m", "scripts.run_eval", "--eval-set", str(set_file),
                 "--skill-path", str(Path(skill_path).resolve()),
                 "--runs-per-query", str(t["runs_per_query"]),
                 "--trigger-threshold", str(t["threshold"]),
                 "--model", t.get("model") or cfg["execution"]["models"][-1],
                 "--timeout", str(t.get("timeout_seconds", 120)), "--max-turns", str(t.get("max_turns", 4)),
                 "--verbose", "--num-workers", str(cfg["execution"]["max_parallel"])]
                + (["--full-scan"] if t.get("full_scan", True) else []),
                cwd=tmp, env=env, capture_output=True, text=True, timeout=3600)
            if proc.returncode != 0:
                raise RuntimeError(f"trigger eval failed: {proc.stderr[-2000:]}")
            out = json.loads(proc.stdout)
            out["stderr_tail"] = (proc.stderr or "")[-3000:]
    res = out["results"]
    passed = sum(1 for r in res if r["pass"])
    pos = [r for r in res if r["should_trigger"]]
    neg = [r for r in res if not r["should_trigger"]]
    out["summary"] = {
        "total": len(res), "passed": passed, "failed": len(res) - passed,
        "accuracy": round(passed / len(res), 4) if res else 0.0,
        "recall": round(sum(r["pass"] for r in pos) / len(pos), 4) if pos else None,
        "false_trigger_rate": round(sum(not r["pass"] for r in neg) / len(neg), 4) if neg else None,
    }
    out_file.write_text(json.dumps(out, indent=2))
    return out
