"""Stores an approved evaluation in the repo: results folder, latest pointer, registry entry."""
from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .config import REPO_ROOT

MAX_OUTPUT_FILE_BYTES = 5 * 1024 * 1024  # skip huge generated files; review.html still embeds them


def publish(run_out: Path, results_dir: Path, registry_file: Path, approver: str,
            decision_note: str = "", run_url: str = "", commit: bool = False) -> Path:
    run_out = Path(run_out)
    m = json.loads((run_out / "run.json").read_text())
    gate = json.loads((run_out / "gate.json").read_text())
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    dest = results_dir / m["skill"] / f"{stamp}-{m['skill_version'][:8]}"
    dest.mkdir(parents=True)

    for f in ("run.json", "benchmark.json", "benchmark.md", "gate.json", "summary.md", "review.html",
              "trigger_results.json", "report.pdf", "verdict.json", "matrix.json", "analyst.json",
              "comparison.json", "usage.json", "steps.json", "test_cases.json",
              *[p.name for p in run_out.glob("benchmark-*.json")]):
        if (run_out / f).exists():
            shutil.copy2(run_out / f, dest / f)

    def ignore(dirpath, names):
        skip = {"transcript.jsonl", "workdir"}
        return [n for n in names if n in skip or
                ((Path(dirpath) / n).is_file() and (Path(dirpath) / n).stat().st_size > MAX_OUTPUT_FILE_BYTES)]
    shutil.copytree(run_out / "iteration", dest / "runs", ignore=ignore)

    approval = {"approved_by": approver, "approved_at": now.isoformat(), "note": decision_note,
                "workflow_run": run_url, "gate_status": gate["status"]}
    (dest / "approval.json").write_text(json.dumps(approval, indent=2))

    verdict = json.loads((run_out / "verdict.json").read_text()) if (run_out / "verdict.json").exists() else {}
    from .verdict import history_entry
    per_model = history_entry(verdict)
    latest = {"skill": m["skill"], "skill_version": m["skill_version"], "pass_rate": m["pass_rate"],
              "gate_status": gate["status"], "model": m["model"], "models": per_model,
              "verdict": verdict.get("overall"), "approved_by": approver,
              "approved_at": now.isoformat(), "results_path": str(dest.relative_to(REPO_ROOT))}
    (results_dir / m["skill"] / "latest.json").write_text(json.dumps(latest, indent=2))
    hist_file = results_dir / m["skill"] / "history.json"
    hist = json.loads(hist_file.read_text()) if hist_file.exists() else []
    if per_model:   # only evaluations with a no-skill baseline feed the usefulness trend
        hist.append({"approved_at": now.isoformat(), "skill_version": m["skill_version"],
                     "verdict": verdict.get("overall"), "models": per_model,
                     "results_path": latest["results_path"]})
        hist_file.write_text(json.dumps(hist, indent=2))

    registry_file.parent.mkdir(parents=True, exist_ok=True)
    reg = json.loads(registry_file.read_text()) if registry_file.exists() else {"skills": {}}
    entry = reg["skills"].get(m["skill"], {"history": []})
    entry.update({"description": m["description"], "current_version": m["skill_version"],
                  "pass_rate": m["pass_rate"], "model": m["model"], "approved_by": approver,
                  "verdict": verdict.get("overall") or entry.get("verdict"), "verdict_by_model": per_model,
                  "approved_at": now.isoformat(), "results_path": latest["results_path"],
                  "status": "approved"})
    entry["history"] = ([{"version": m["skill_version"], "pass_rate": m["pass_rate"],
                          "approved_at": now.isoformat(), "approved_by": approver}]
                        + entry.get("history", []))[:20]
    reg["skills"][m["skill"]] = entry
    reg["updated_at"] = now.isoformat()
    registry_file.write_text(json.dumps(reg, indent=2, sort_keys=True))

    if commit:
        subprocess.run(["git", "add", str(dest), str(results_dir / m["skill"]), str(registry_file)],
                       cwd=REPO_ROOT, check=True)
        msg = (f"eval({m['skill']}): approve {m['skill_version'][:8]} at {m['pass_rate']:.0%} pass rate"
               f" - verdict {verdict.get('overall', 'n/a')}\n\n"
               f"Approved-by: {approver}\nGate: {gate['status']}\n" + (f"Run: {run_url}\n" if run_url else ""))
        subprocess.run(["git", "commit", "-m", msg], cwd=REPO_ROOT, check=True)
    return dest
