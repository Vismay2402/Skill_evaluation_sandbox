"""skilleval CLI.

  python -m skilleval list                          skills in the repo
  python -m skilleval changed --base origin/main    skills changed vs. a base ref
  python -m skilleval validate --skill NAME|--all
  python -m skilleval draft-evals --skill NAME      let Claude draft evals.json + trigger_evals.json
  python -m skilleval run --skill NAME [--skill N2] | --changed BASE | --all
  python -m skilleval publish --workspace DIR --approver USER [--commit]
  python -m skilleval run --skill NAME --test-cases my-cases.csv [--test-cases-mode append]
  python -m skilleval import-cases --skill NAME --file my-cases.json   save them into evals/evals.json
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
    custom = Path(args.test_cases).resolve() if getattr(args, "test_cases", None) else None
    for s in _targets(args, skills_dir):
        use = custom if custom and ev.read_test_case_file(custom, s) is not None else None
        problems = ev.validate(skills_dir / s, use, getattr(args, "test_cases_mode", "replace"))
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
        "baseline": args.baseline, "max_parallel": args.parallel,
        "grader_model": args.grader_model}.items() if v is not None}}
    if args.no_trigger:
        overrides["trigger_eval"] = {"enabled": False}
    if args.no_compare:
        overrides["comparison"] = {"enabled": False}
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
    custom = Path(args.test_cases).resolve() if args.test_cases else None
    if custom and not custom.exists():
        print(f"[skilleval] test-case file not found: {args.test_cases}", file=sys.stderr)
        sys.exit(2)
    with tempfile.TemporaryDirectory() as snap:
        for s in targets:
            cfg = load_config(skills_dir / s, overrides=overrides)
            old = snapshot_from_ref(skills_dir, s, args.base_ref, Path(snap)) if args.base_ref else None
            use_custom = custom
            if custom and ev.read_test_case_file(custom, s) is None:
                use_custom = None  # the file has no cases for this skill: use the skill's own evals.json
                print(f"[skilleval] {s}: {custom.name} has no test cases for this skill; using evals/evals.json",
                      file=sys.stderr)
            try:
                m = evaluate_skill(skills_dir / s, workspace, cfg, old_snapshot=old, results_dir=results_dir,
                                   eval_ids=args.evals, custom_cases=use_custom, custom_mode=args.test_cases_mode)
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


def cmd_import_cases(args, cfg):
    """Make user-supplied test cases permanent: write them into the skill's evals/evals.json."""
    skills_dir, _, _ = _paths(cfg)
    sd = skills_dir / args.skill
    cases = ev.load_evals(sd, Path(args.file).resolve(), args.mode)
    out = []
    for c in cases:
        exps = []
        for e in c.expectations:
            if e.script == ev.AUTO_NOT_TRIGGERED:
                continue
            exps.append({"text": e.text, **({"script": e.script} if e.script else {}),
                         **({"category": e.category} if e.category else {})} if (e.script or e.category) else e.text)
        files = []
        for f in c.files:
            p = Path(f)
            if p.is_absolute():  # copy custom input files into the skill's evals/files/
                dest = sd / "evals" / "files" / p.name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, dest)
                f = str(dest.relative_to(sd))
            files.append(f)
        out.append({"id": c.id, "name": c.name, "type": c.type, "description": c.description,
                    "prompt": c.prompt, "expected_output": c.expected_output, "files": files, "expectations": exps})
    name, _ = ev.skill_identity(sd)
    (sd / "evals").mkdir(exist_ok=True)
    (sd / "evals" / "evals.json").write_text(json.dumps({"skill_name": name, "evals": out}, indent=2) + "\n")
    print(f"{args.skill}: wrote {len(out)} test cases to {(sd / 'evals' / 'evals.json').relative_to(REPO_ROOT)}")


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


def cmd_due(args, cfg):
    """Skills that need an evaluation now (frequency, changes, models they were never evaluated on)."""
    from .schedule import due_skills
    extra = [m.strip() for m in (args.models or "").split(",") if m.strip()]
    items = due_skills(extra)
    if args.json:
        print(json.dumps(items, indent=2))
        return
    if args.names:
        print(",".join(i["skill"] for i in (items[:args.limit] if args.limit else items)))
        return
    lines = [f"- **`{i['skill']}`** ({i['verdict'] or 'not approved'}, last approved {i['last_approved'] or 'never'}): "
             + "; ".join(i["reasons"]) for i in items]
    print("\n".join(lines) if lines else "No skills are due.")


def cmd_models_check(args, cfg):
    """Newly released Claude models (Anthropic Models API or Bedrock)."""
    from .schedule import new_models
    items = new_models(args.since)
    print(json.dumps(items, indent=2) if args.json else
          ("\n".join(f"{m['id']}  ({m['name']}, released {m['released']})" for m in items) or "No new models."))


WORKFLOW = REPO_ROOT / ".github" / "workflows" / "skill-eval.yml"
BEGIN, END = "# BEGIN skill-options", "# END skill-options"


def cmd_sync_workflow(args, cfg):
    """Regenerate the skill dropdown in the Run workflow form from the folders under skills/."""
    skills_dir, _, _ = _paths(cfg)
    names = all_skills(skills_dir)
    lines = WORKFLOW.read_text().splitlines(keepends=True)
    try:
        b = next(i for i, l in enumerate(lines) if l.strip().startswith(BEGIN))
        e = next(i for i, l in enumerate(lines) if l.strip().startswith(END))
    except StopIteration:
        sys.exit(f"markers '{BEGIN}' / '{END}' not found in {WORKFLOW}")
    indent = lines[b][: len(lines[b]) - len(lines[b].lstrip())]
    new = lines[: b + 1] + [f"{indent}- {n}\n" for n in names] + lines[e:]
    text = "".join(new)
    # keep the default valid: first skill in the list
    import re as _re
    text = _re.sub(r"(\n\s*default: )\S+(\n\s*skills_other:)", lambda m: m.group(1) + (names[0] if names else "all")
                   + m.group(2), text, count=1)
    if args.check:
        if text != WORKFLOW.read_text():
            print("The skill dropdown in .github/workflows/skill-eval.yml is out of date. Run:\n"
                  "  python -m skilleval sync-workflow\nand commit the result.")
            sys.exit(1)
        print(f"dropdown lists all {len(names)} skills")
        return
    WORKFLOW.write_text(text)
    print(f"dropdown now lists {len(names)} skills: {', '.join(names)}")


def cmd_catalog(args, cfg):
    from .catalog import write_all
    print(f"wrote {write_all().relative_to(REPO_ROOT)} and eval-results/<skill>/EVALUATIONS.md")


def cmd_history(args, cfg):
    """How many times each skill has been evaluated and approved, with timestamps."""
    from .catalog import evaluations, _ts
    skills_dir, results_dir, _ = _paths(cfg)
    for s in (args.skill or all_skills(skills_dir)):
        rows = evaluations(results_dir, s)
        print(f"{s}: {len(rows)} approved evaluation(s)")
        for i, r in enumerate(rows):
            print(f"  #{len(rows) - i}  {_ts(r['approved_at'])}  v{r['version'][:8]}  {','.join(r['models']):28} "
                  f"pass {(r['pass_rate'] or 0):.0%}  {r['verdict'] or '-':18} by {r['approved_by']}")


def cmd_notify_review(args, cfg):
    """Email reviewers: results waiting for approval (with report.pdf), or approved and stored."""
    from .notify import build_review_email, recipients, send
    to = recipients(args.to, cfg)
    subject, html, text, att = build_review_email(Path(args.workspace), args.run_url, args.repo, args.kind,
                                                  args.pr_url or "", args.approver or "")
    try:
        send(to, subject, html, text, att)
    except Exception as e:  # never fail the pipeline because of email
        print(f"::warning::Could not send the review email: {e}")


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
    r.add_argument("--grader-model", help="model that grades, judges A/B and writes the narrative")
    r.add_argument("--baseline", choices=["auto", "without_skill", "old_skill", "none"])
    r.add_argument("--evals", type=int, nargs="*", help="only these eval ids")
    r.add_argument("--allow-failed-gate", action="store_true")
    r.add_argument("--no-trigger", action="store_true", help="skip description-trigger evals")
    r.add_argument("--no-compare", action="store_true", help="skip the blind A/B comparison")
    for sp in (r, sub.choices["validate"]):
        sp.add_argument("--test-cases", metavar="FILE",
                        help="your own test cases (JSON in skill-creator schema, or CSV - see templates/)")
        sp.add_argument("--test-cases-mode", choices=["replace", "append"], default="replace",
                        help="replace the skill's evals.json for this run, or add to it")
    im = sub.add_parser("import-cases", help="save a test-case file into a skill's evals/evals.json")
    im.add_argument("--skill", required=True); im.add_argument("--file", required=True)
    im.add_argument("--mode", choices=["replace", "append"], default="append")
    ch = sub.add_parser("changes", help="verdict changes / retire candidates in a workspace")
    ch.add_argument("--workspace", default="skilleval-workspace"); ch.add_argument("--output")
    du = sub.add_parser("due", help="skills that need an evaluation now")
    du.add_argument("--json", action="store_true"); du.add_argument("--names", action="store_true")
    du.add_argument("--models", help="also count these models (e.g. newly released) as required")
    du.add_argument("--limit", type=int, default=0)
    mc = sub.add_parser("models-check", help="Claude models released since schedule.models_known_before")
    mc.add_argument("--since"); mc.add_argument("--json", action="store_true")
    sw = sub.add_parser("sync-workflow", help="regenerate the skill dropdown in the Run workflow form")
    sw.add_argument("--check", action="store_true", help="fail if the dropdown is out of date")
    sub.add_parser("catalog", help="regenerate EVALUATIONS.md from eval-results/")
    hi = sub.add_parser("history", help="evaluation count and timestamps per skill")
    hi.add_argument("--skill", action="append")
    nr = sub.add_parser("notify-review", help="email reviewers about results")
    nr.add_argument("--workspace", default="skilleval-workspace"); nr.add_argument("--to")
    nr.add_argument("--run-url", default=""); nr.add_argument("--repo", default="")
    nr.add_argument("--kind", choices=["pending", "approved"], default="pending")
    nr.add_argument("--pr-url"); nr.add_argument("--approver")
    pb = sub.add_parser("publish")
    pb.add_argument("--workspace", default="skilleval-workspace")
    pb.add_argument("--approver", required=True); pb.add_argument("--note"); pb.add_argument("--run-url")
    pb.add_argument("--include-failed", action="store_true"); pb.add_argument("--commit", action="store_true")

    args = p.parse_args()
    cfg = load_config(config_path=Path(args.config) if args.config else None)
    {"list": cmd_list, "changed": cmd_changed, "validate": cmd_validate, "draft-evals": cmd_draft_evals,
     "run": cmd_run, "publish": cmd_publish, "import-cases": cmd_import_cases,
     "catalog": cmd_catalog, "history": cmd_history, "notify-review": cmd_notify_review,
     "due": cmd_due, "sync-workflow": cmd_sync_workflow, "models-check": cmd_models_check, "verify": cmd_verify,
     "changes": cmd_changes}[args.cmd](args, cfg)


if __name__ == "__main__":
    main()
