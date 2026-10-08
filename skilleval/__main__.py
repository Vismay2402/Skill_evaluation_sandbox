"""skilleval CLI.

  python -m skilleval list                          skills in the repo
  python -m skilleval changed --base origin/main    skills changed vs. a base ref
  python -m skilleval validate --skill NAME|--all
  python -m skilleval draft-evals --skill NAME      let Claude draft evals.json + trigger_evals.json
  python -m skilleval run --skill NAME [--skill N2] | --changed BASE | --all
  python -m skilleval publish --workspace DIR --approver USER [--commit]
  python -m skilleval verify --changed BASE         merge check: approved result matches current skill
  python -m skilleval changes --workspace DIR       skills whose usefulness verdict changed
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import evals as ev
from .config import REPO_ROOT, VENDOR_DIR, load_config
from .discover import all_skills, changed_skills, snapshot_from_ref


def _paths(cfg):
    return (REPO_ROOT / cfg["skills_dir"], REPO_ROOT / cfg["results_dir"], REPO_ROOT / cfg["registry_file"])


def _targets(args, skills_dir) -> list[str]:
    if getattr(args, "all", False):
        return all_skills(skills_dir)
    if getattr(args, "changed", None):
        return changed_skills(skills_dir, args.changed)
    return args.skill or []


def cmd_list(args, cfg):
    skills_dir, results_dir, _ = _paths(cfg)
    for s in all_skills(skills_dir):
        latest = results_dir / s / "latest.json"
        info = json.loads(latest.read_text()) if latest.exists() else None
        has_evals = (skills_dir / s / "evals" / "evals.json").exists()
        status = (f"approved {info['pass_rate']:.0%} ({info['approved_at'][:10]})" if info else "never approved")
        print(f"{s:40} evals={'yes' if has_evals else 'NO ':3}  {status}")


def cmd_changed(args, cfg):
    skills_dir, _, _ = _paths(cfg)
    names = changed_skills(skills_dir, args.base)
    print(json.dumps(names) if args.json else "\n".join(names))


def cmd_validate(args, cfg):
    skills_dir, _, _ = _paths(cfg)
    bad = 0
    for s in _targets(args, skills_dir):
        problems = ev.validate(skills_dir / s)
        print(f"{'OK  ' if not problems else 'FAIL'} {s}")
        for p in problems:
            print(f"     - {p}")
        bad += bool(problems)
    sys.exit(1 if bad else 0)


DRAFT_PROMPT = """You are preparing an evaluation set for the Claude skill in {skill_dir}.
Read {skill_dir}/SKILL.md and any bundled files it references. Then read the "Test Cases" and
"Description Optimization" sections of {creator} for how good evals are written.

Write two files:

1. {skill_dir}/evals/evals.json using this schema:
   {{"skill_name": "<name>", "evals": [{{"id": 1, "name": "short-kebab-name", "prompt": "...",
     "expected_output": "...", "files": [], "expectations": ["objectively verifiable statement", ...]}}]}}
   Write 3-5 realistic prompts a real user would send (concrete details, file names, context), covering
   the main path and at least one edge case. Give each 3-6 expectations that are objectively checkable from
   the transcript or output files and that a run WITHOUT the skill would plausibly fail. If an eval needs an
   input file, create a small realistic one under {skill_dir}/evals/files/ and reference it relative to
   the skill folder.

2. {skill_dir}/evals/trigger_evals.json: a JSON list of 16-20 {{"query": "...", "should_trigger": true|false}}
   items - about half should trigger; the should-not-trigger ones must be near-misses, not unrelated topics.

These are drafts for a human to review, so favour coverage and clarity over volume."""


def cmd_draft_evals(args, cfg):
    skills_dir, _, _ = _paths(cfg)
    for s in args.skill:
        sd = skills_dir / s
        (sd / "evals").mkdir(exist_ok=True)
        if (sd / "evals" / "evals.json").exists() and not args.force:
            print(f"{s}: evals/evals.json exists (use --force to redraft)")
            continue
        if cfg["execution"]["executor"] == "mock":
            name, _ = ev.skill_identity(sd)
            (sd / "evals" / "evals.json").write_text(json.dumps({"skill_name": name, "evals": [
                {"id": 1, "name": "draft", "prompt": f"TODO realistic prompt for {name}",
                 "expected_output": "TODO", "files": [], "expectations": ["TODO verifiable statement"]}]},
                indent=2))
        else:
            env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
            subprocess.run(["claude", "-p", DRAFT_PROMPT.format(skill_dir=sd, creator=VENDOR_DIR / "SKILL.md"),
                            "--model", cfg["execution"]["grader_model"], "--dangerously-skip-permissions"],
                           cwd=REPO_ROOT, env=env, check=True, stdin=subprocess.DEVNULL)
        print(f"{s}: drafted {sd / 'evals'} - review and edit before running")


def cmd_run(args, cfg_unused):
    overrides = {"execution": {k: v for k, v in {
        "executor": args.executor, "models": args.models, "runs_per_config": args.runs,
        "baseline": args.baseline, "max_parallel": args.parallel}.items() if v is not None}}
    if args.no_trigger:
        overrides["trigger_eval"] = {"enabled": False}
    base_cfg = load_config(overrides=overrides)
    skills_dir, results_dir, _ = _paths(base_cfg)
    targets = _targets(args, skills_dir)
    if not targets:
        print("No skills to evaluate.")
        return
    from .pipeline import evaluate_skill  # imported late: pulls in vendored modules
    workspace = Path(args.workspace).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    results, failed = [], []
    with tempfile.TemporaryDirectory() as snap:
        for s in targets:
            cfg = load_config(skills_dir / s, overrides=overrides)
            old = snapshot_from_ref(skills_dir, s, args.base_ref, Path(snap)) if args.base_ref else None
            try:
                m = evaluate_skill(skills_dir / s, workspace, cfg, old_snapshot=old, results_dir=results_dir,
                                   eval_ids=args.evals)
                results.append(m)
                if m["gate_status"] != "pass":
                    failed.append(s)
            except ValueError as e:
                print(f"[skilleval] {s}: {e}", file=sys.stderr)
                (workspace / f"{s}.validation-error.txt").write_text(str(e))
                failed.append(s)
    combined = []
    for m in results:
        combined.append((workspace / m["skill"] / "summary.md").read_text())
    for s in failed:
        err = workspace / f"{s}.validation-error.txt"
        if err.exists():
            combined.append(f"## ❌ `{s}` failed validation\n\n```\n{err.read_text()}\n```\n")
    (workspace / "SUMMARY.md").write_text("\n\n---\n\n".join(combined) or "No results.\n")
    (workspace / "status.json").write_text(json.dumps({"evaluated": [m["skill"] for m in results],
                                                        "failed_gate": failed}, indent=2))
    print((workspace / "SUMMARY.md").read_text())
    sys.exit(1 if failed and not args.allow_failed_gate else 0)


def cmd_publish(args, cfg):
    from .publish import publish
    _, results_dir, registry = _paths(cfg)
    ws = Path(args.workspace)
    status = json.loads((ws / "status.json").read_text())
    for s in status["evaluated"]:
        if s in status["failed_gate"] and not args.include_failed:
            print(f"skip {s}: gate failed (use --include-failed to store anyway)")
            continue
        dest = publish(ws / s, results_dir, registry, args.approver, args.note or "", args.run_url or "",
                       commit=args.commit)
        print(f"stored {s} -> {dest.relative_to(REPO_ROOT)}")


def cmd_verify(args, cfg):
    """Merge check: every targeted skill must have an approved eval for its exact current content."""
    from .discover import tree_hash
    skills_dir, results_dir, _ = _paths(cfg)
    bad = []
    for s in _targets(args, skills_dir):
        latest = results_dir / s / "latest.json"
        current = tree_hash(skills_dir / s)
        if not latest.exists():
            bad.append(f"{s}: no approved evaluation (run the Skill evaluation workflow)")
            continue
        info = json.loads(latest.read_text())
        if info["skill_version"] != current:
            bad.append(f"{s}: approved version {info['skill_version']} != current {current} "
                       "(skill changed after approval - re-run the evaluation)")
        else:
            print(f"OK   {s}: version {current} approved by {info['approved_by']} at {info['pass_rate']:.0%}")
    for b in bad:
        print(f"FAIL {b}")
    sys.exit(1 if bad else 0)


def cmd_changes(args, cfg):
    """Markdown list of skills whose usefulness verdict changed or needs attention (for alerts)."""
    _, results_dir, _ = _paths(cfg)
    ws = Path(args.workspace)
    status = json.loads((ws / "status.json").read_text())
    lines = []
    for s in status["evaluated"]:
        run = json.loads((ws / s / "run.json").read_text())
        vf = ws / s / "verdict.json"
        dec = json.loads(vf.read_text()) if vf.exists() else {}
        latest = results_dir / s / "latest.json"
        prev = json.loads(latest.read_text()).get("verdict") if latest.exists() else None
        new = run.get("verdict")
        reasons = []
        if new in ("RETIRE CANDIDATE", "HARMFUL"):
            reasons.append(f"verdict is **{new}**")
        if prev and new and new != prev and new != "NOT ASSESSED":
            reasons.append(f"verdict changed {prev} -> {new}")
        reasons += dec.get("trend_alerts", [])
        if reasons:
            lines.append(f"### `{s}`\n\n- " + "\n- ".join(reasons) + f"\n\n> {dec.get('reason', '')}\n")
    out = "\n".join(lines)
    if args.output:
        Path(args.output).write_text(out)
    print(out or "No verdict changes.")


def main():
    p = argparse.ArgumentParser(prog="skilleval")
    p.add_argument("--config", default=None)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list")
    c = sub.add_parser("changed"); c.add_argument("--base", required=True); c.add_argument("--json", action="store_true")
    for name in ("validate", "run", "verify"):
        sp = sub.add_parser(name)
        sp.add_argument("--skill", action="append")
        sp.add_argument("--all", action="store_true")
        sp.add_argument("--changed", metavar="BASE_REF")
    d = sub.add_parser("draft-evals"); d.add_argument("--skill", action="append", required=True)
    d.add_argument("--force", action="store_true")
    r = sub.choices["run"]
    r.add_argument("--workspace", default="skilleval-workspace")
    r.add_argument("--base-ref", default=None, help="git ref holding the previous version (old_skill baseline)")
    r.add_argument("--executor", choices=["claude-code", "mock"])
    r.add_argument("--models", help="comma-separated, least to most capable (default: config)")
    r.add_argument("--runs", type=int); r.add_argument("--parallel", type=int)
    r.add_argument("--baseline", choices=["auto", "without_skill", "old_skill", "none"])
    r.add_argument("--evals", type=int, nargs="*", help="only these eval ids")
    r.add_argument("--allow-failed-gate", action="store_true")
    r.add_argument("--no-trigger", action="store_true", help="skip description-trigger evals")
    ch = sub.add_parser("changes", help="verdict changes / retire candidates in a workspace")
    ch.add_argument("--workspace", default="skilleval-workspace"); ch.add_argument("--output")
    pb = sub.add_parser("publish")
    pb.add_argument("--workspace", default="skilleval-workspace")
    pb.add_argument("--approver", required=True); pb.add_argument("--note"); pb.add_argument("--run-url")
    pb.add_argument("--include-failed", action="store_true"); pb.add_argument("--commit", action="store_true")

    args = p.parse_args()
    cfg = load_config(config_path=Path(args.config) if args.config else None)
    {"list": cmd_list, "changed": cmd_changed, "validate": cmd_validate, "draft-evals": cmd_draft_evals,
     "run": cmd_run, "publish": cmd_publish, "verify": cmd_verify,
     "changes": cmd_changes}[args.cmd](args, cfg)


if __name__ == "__main__":
    main()
