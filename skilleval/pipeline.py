"""End-to-end evaluation of one skill.

validate -> run every eval with the skill and against a baseline on every configured model ->
grade -> benchmark per model -> trigger evals -> results matrix -> analyst narrative ->
usefulness verdict -> gate -> summary.md, review.html, report.pdf

Output layout (<workspace>/<skill>/):

  run.json                  manifest: skill, version hash, models, configs, verdict, cost
  iteration/<model>/eval-<id>-<name>/<config>/run-<n>/   skill-creator run layout
  benchmark.json            skill-creator benchmark for the first model (viewer's Benchmark tab)
  benchmark-<model>.json    one benchmark per model
  matrix.json               every graded run (source of all report numbers)
  analyst.json              narrative written by the analyst pass
  verdict.json              usefulness verdict and the evidence behind it
  gate.json                 automated gate
  trigger_results.json      description-trigger evals (if present)
  comparison.json           blind A/B comparison (comparator agent) and win rates
  usage.json                tokens and cost per model/config and per stage
  steps.json                the Skills 2.0 steps as run, with results
  test_cases.json           every test case used (prompt, expected output, checks, source)
  summary.md                PR comment / job summary
  review.html               skill-creator eval viewer for reading every output
  report.pdf                the evaluation report
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from . import evals as ev
from .analyst import run_analyst
from .comparator import run_comparisons
from . import insights
from .config import VENDOR_DIR
from .discover import tree_hash
from .executor import EXECUTORS, RunSpec
from .gate import analyst_notes, evaluate_gate, load_history, load_last_approved
from .grader import grade_run
from .matrix import build_matrix, runs_per_config
from .report_pdf import model_label, render_pdf, today
from .trigger import run_trigger_eval
from .verdict import decide

from scripts.aggregate_benchmark import generate_benchmark, generate_markdown  # noqa: E402 (vendored)


def log(msg: str) -> None:
    print(f"[skilleval] {msg}", file=sys.stderr, flush=True)


def resolve_configs(cfg: dict, skill_path: Path, old_snapshot: Path | None) -> list[tuple[str, Path | None]]:
    mode = cfg["execution"]["baseline"]
    if mode == "old_skill" and old_snapshot is None:
        log("baseline old_skill requested but no previous version exists; using without_skill")
        mode = "without_skill"
    if mode == "auto":
        mode = "old_skill" if old_snapshot is not None else "without_skill"
    if mode == "old_skill":
        return [("new_skill", skill_path), ("old_skill", old_snapshot)]
    if mode == "without_skill":
        return [("with_skill", skill_path), ("without_skill", None)]
    return [("with_skill", skill_path)]


def evaluate_skill(skill_path: Path, workspace: Path, cfg: dict, old_snapshot: Path | None = None,
                   results_dir: Path | None = None, eval_ids: list[int] | None = None,
                   custom_cases: Path | None = None, custom_mode: str = "replace") -> dict:
    skill_path = Path(skill_path).resolve()
    problems = ev.validate(skill_path, custom_cases, custom_mode)
    if problems:
        raise ValueError("Skill failed validation:\n  - " + "\n  - ".join(problems))
    name, description = ev.skill_identity(skill_path)
    if old_snapshot is not None and tree_hash(old_snapshot) == tree_hash(skill_path):
        old_snapshot = None  # base branch has the identical skill; comparing to itself says nothing
    cases = ev.load_evals(skill_path, custom_cases, custom_mode)
    if eval_ids:
        cases = [c for c in cases if c.id in eval_ids]

    out = workspace / name
    if out.exists():
        shutil.rmtree(out)
    it = out / "iteration"
    it.mkdir(parents=True)
    ex = cfg["execution"]
    models = ex["models"]
    started = datetime.now(timezone.utc)
    configs = resolve_configs(cfg, skill_path, old_snapshot)
    conf_names = [c for c, _ in configs]
    primary, baseline = conf_names[0], (conf_names[1] if len(conf_names) > 1 else None)
    log(f"{name}: {len(cases)} evals x {len(configs)} configs x {len(models)} models x "
        f"{ex['runs_per_config']} runs ({ex['executor']}: {', '.join(models)})")

    specs: list[RunSpec] = []
    for model in models:
        for case in cases:
            (it / model / case.dirname).mkdir(parents=True)
            (it / model / case.dirname / "eval_metadata.json").write_text(json.dumps(case.metadata(), indent=2))
            for conf, src in configs:
                for n in range(1, ex["runs_per_config"] + 1):
                    run_dir = it / model / case.dirname / conf / f"run-{n}"
                    run_dir.mkdir(parents=True)
                    # The viewer looks for eval_metadata.json in the run dir or its parent.
                    (run_dir / "eval_metadata.json").write_text(json.dumps(case.metadata(), indent=2))
                    specs.append(RunSpec(case=case, config=conf, run_number=n, run_dir=run_dir,
                                         skill_name=name, skill_src=src, skill_root=skill_path,
                                         model=model, timeout=ex["run_timeout_seconds"],
                                         budget_usd=ex.get("max_budget_usd_per_run")))

    execute = EXECUTORS[ex["executor"]]
    harness_errors = {m: 0 for m in models}
    error_messages: list[str] = []
    not_triggered = {m: 0 for m in models}

    def work(spec: RunSpec):
        metrics = execute(spec)
        grade_run(spec.case, spec.run_dir, spec.config, skill_path, ex["executor"], ex["grader_model"])
        return spec, metrics

    with ThreadPoolExecutor(max_workers=ex["max_parallel"]) as pool:
        futures = [pool.submit(work, s) for s in specs]
        for i, fut in enumerate(as_completed(futures), 1):
            spec, metrics = fut.result()
            harness_errors[spec.model] += int(bool(metrics.get("harness_error")))
            if metrics.get("harness_error_message"):
                error_messages.append(f"{spec.model}: {metrics['harness_error_message']}")
            if metrics.get("skill_triggered") is False and spec.case.type != "should_not_trigger":
                not_triggered[spec.model] += 1
            log(f"  [{i}/{len(specs)}] {spec.model}/{spec.case.dirname}/{spec.config}/run-{spec.run_number}")

    # ---- skill-creator benchmark, one per model -------------------------------------------------
    eval_names = {c.id: c.name for c in cases}
    benchmarks: dict[str, dict] = {}
    for model in models:
        b = generate_benchmark(it / model, skill_name=name, skill_path=str(skill_path))
        b["metadata"].update({
            "executor_model": model, "analyzer_model": ex["grader_model"], "grader_model": ex["grader_model"],
            "runs_per_configuration": ex["runs_per_config"], "configurations": conf_names,
            "harness_error_runs": harness_errors[model],
            "skill_runs_where_skill_not_loaded": not_triggered[model]})
        for r in b["runs"]:
            r["eval_name"] = eval_names.get(r["eval_id"], str(r["eval_id"]))
        b["notes"] = analyst_notes(b, cfg)
        if not_triggered[model]:
            b["notes"].insert(0, f"In {not_triggered[model]} skill runs on {model} Claude never loaded the "
                                 "skill - check the description before trusting the uplift")
        benchmarks[model] = b
        (out / f"benchmark-{model}.json").write_text(json.dumps(b, indent=2))
    shutil.copy2(out / f"benchmark-{models[0]}.json", out / "benchmark.json")
    (out / "benchmark.md").write_text("\n\n".join(f"# {m}\n\n{generate_markdown(b)}" for m, b in benchmarks.items()))

    # ---- blind A/B benchmark (comparator agent) --------------------------------------------------
    matrix = build_matrix(it, models, conf_names, cases)
    comparison = None
    if baseline:
        log(f"{name}: blind A/B comparison")
        comparison = run_comparisons(out, matrix, cases, primary, baseline, cfg)

    trigger = None
    trigger_set = ev.load_trigger_evals(skill_path)
    if cfg["trigger_eval"]["enabled"] and trigger_set:
        log(f"{name}: running {len(trigger_set)} trigger evals")
        trigger = run_trigger_eval(skill_path, trigger_set, out / "trigger_results.json", cfg)

    # ---- matrix, analyst, verdict ---------------------------------------------------------------
    (out / "matrix.json").write_text(json.dumps(matrix, indent=2))
    log(f"{name}: analyst pass")
    profile = insights.skill_profile(skill_path)
    analyst = run_analyst(out, matrix, primary, baseline, name, cfg, description,
                          insights.other_skill_descriptions(skill_path), trigger)
    history = load_history(results_dir, name) if results_dir else []
    # A "useful vs obsolete" verdict only makes sense against a no-skill baseline.
    decision = decide(matrix, primary, baseline if baseline == "without_skill" else None,
                      analyst["check_categories"], cfg["verdict"], history)
    if baseline == "old_skill":
        decision["reason"] = ("This run compared the new version with the previous one, which shows whether the "
                              "change is an improvement but not whether the skill is still needed. Run with "
                              "baseline without_skill (the scheduled review does) for the usefulness verdict.")
    failed_runs = sum(harness_errors.values())
    if failed_runs:
        # A verdict computed from runs that never executed would be meaningless (and misleading).
        top = sorted(set(error_messages), key=error_messages.count, reverse=True)[:3]
        decision = {**decision, "overall": "NOT ASSESSED", "models": [], "outlook": [], "trend_alerts": [],
                    "harness_errors": top,
                    "reason": f"{failed_runs} of {len(specs)} runs failed to execute, so no verdict was "
                              f"computed. Error: {top[0] if top else 'unknown'}"}
        log(f"{name}: {failed_runs}/{len(specs)} runs failed - {top[0] if top else ''}")
    (out / "verdict.json").write_text(json.dumps(decision, indent=2))

    last = load_last_approved(results_dir, name) if results_dir else None
    from .matrix import config_summary
    rates = {m: {c: config_summary(matrix, m, c)["pass_rate"] for c in conf_names} for m in models}
    gate = evaluate_gate(benchmarks, trigger, last, cfg, rates)
    notes = list(dict.fromkeys(n for b in benchmarks.values() for n in b["notes"]))
    gate["warnings"] = notes
    (out / "gate.json").write_text(json.dumps(gate, indent=2))

    usage = insights.usage_breakdown(out, matrix)
    (out / "usage.json").write_text(json.dumps(usage, indent=2))
    cost = round(usage["total"]["cost_usd"], 4)
    failures = insights.specific_failures(matrix, primary)
    labels = {m: model_label(m) for m in models}
    rebench = insights.rebenchmark(cfg, last)
    case_meta = [c.metadata() | {"files": c.files} for c in cases]
    (out / "test_cases.json").write_text(json.dumps(case_meta, indent=2))
    step_rows = insights.steps({
        "matrix": matrix, "primary": primary, "baseline": baseline, "target": insights.target_for(cfg),
        "comparison": comparison, "trigger": trigger, "analyst": analyst, "failures": failures,
        "case_meta": case_meta, "profile": profile, "labels": labels, "max_parallel": ex["max_parallel"],
        "usage": usage, "decision": decision, "version": tree_hash(skill_path), "rebench": rebench})
    (out / "steps.json").write_text(json.dumps(step_rows, indent=2))
    manifest = {
        "skill": name, "description": description, "skill_version": tree_hash(skill_path),
        "baseline_version": tree_hash(old_snapshot) if old_snapshot else None,
        "executor": ex["executor"], "models": models, "model": models[0], "grader_model": ex["grader_model"],
        "configurations": conf_names, "evals": [c.id for c in cases], "runs_per_config": ex["runs_per_config"],
        "started_at": started.isoformat(), "finished_at": datetime.now(timezone.utc).isoformat(),
        "gate_status": gate["status"], "pass_rate": gate["pass_rate"],
        "pass_rate_by_model": gate["pass_rate_by_model"],
        "verdict": decision["overall"], "verdict_by_model": {p["model"]: p["verdict"] for p in decision["models"]},
        "cost_usd": cost, "tokens_total": usage["total"]["total"],
        "test_case_source": sorted({c.source for c in cases}),
        "win_rate_by_model": {m: v["win_rate"] for m, v in (comparison or {}).get("by_model", {}).items()},
    }
    (out / "run.json").write_text(json.dumps(manifest, indent=2))
    extra = {"profile": profile, "usage": usage, "comparison": comparison, "failures": failures,
             "steps": step_rows, "cases": case_meta, "analyst": analyst, "target": insights.target_for(cfg),
             "rebench": rebench}
    (out / "summary.md").write_text(render_summary(manifest, matrix, decision, gate, trigger, primary, baseline,
                                                   extra))

    subprocess.run([sys.executable, str(VENDOR_DIR / "eval-viewer" / "generate_review.py"), str(it),
                    "--skill-name", name, "--benchmark", str(out / "benchmark.json"),
                    "--static", str(out / "review.html")], check=True, capture_output=True)
    if cfg["report"].get("pdf", True):
        render_pdf(out / "report.pdf", {
            "skill": name, "version": manifest["skill_version"], "date": today(), "executor": ex["executor"],
            "grader_model": ex["grader_model"], "primary": primary, "baseline": baseline, "matrix": matrix,
            "decision": decision, "analyst": analyst, "trigger": trigger, "notes": notes, "cost_usd": cost,
            "limits": limits(matrix, skill_path, ex, analyst, decision), **extra,
            "trigger_model": cfg["trigger_eval"].get("model") or models[-1]})
    log(f"{name}: verdict {decision['overall']} - gate {gate['status'].upper()} -> {out}")
    return manifest


def limits(matrix: dict, skill_path: Path, ex: dict, analyst: dict, decision: dict) -> list[str]:
    """Honest caveats, generated from what this run actually did."""
    out = []
    runs = runs_per_config(matrix)
    if runs <= 1:
        out.append("Each case was run once per configuration, so there is no variance data; single-check "
                   "differences may be noise.")
    n = sum(len(c["checks"]) for c in matrix["cases"])
    det = sum(1 for c in matrix["cases"] for k in c["checks"] if k.get("deterministic"))
    out.append(f"{det} of {n} checks are deterministic scripts; the rest were graded by "
               f"{model_label(ex['grader_model'])}, which can be lenient or strict on wording.")
    bundled = [p for p in skill_path.rglob("*") if p.is_file() and p.name != "SKILL.md"
               and "evals" not in p.relative_to(skill_path).parts]
    if bundled:
        out.append(f"The skill bundles {len(bundled)} file(s) (assets, templates, scripts) that the baseline "
                   "cannot reach, so part of the uplift is asset access rather than guidance.")
    if any(c["type"] in ("ambiguous", "edge") for c in matrix["cases"]):
        out.append("Runs are headless: when a skill asks the user a question and waits, no one answers, so "
                   "pause-and-confirm behaviour is only partly tested.")
    files = {f.rsplit(".", 1)[-1].lower() for m in matrix["results"].values() for conf in m.values()
             for runs_ in conf.values() for r in runs_ for f in r["files"] if "." in f}
    if files & {"docx", "pptx", "xlsx", "pdf", "png", "html"}:
        out.append("Outputs were checked from their content, not rendered visually, so layout and page "
                   "counts are only as reliable as the checks that inspect them.")
    if ex["executor"] == "mock":
        out.insert(0, "MOCK DATA: this report was produced by the dry-run executor with no model calls.")
    if decision.get("overall") == "NOT ASSESSED":
        out.append("No without-skill baseline was run, so the usefulness verdict is not assessed.")
    out.append("The eval set defines what 'useful' means here; a skill can be valuable in ways no check "
               "covers, so review the cases before retiring anything.")
    return out


def _pct(x) -> str:
    return "–" if x is None else f"{x:.0%}"


STATUS_ICON = {"done": "✅", "warn": "⚠️", "skipped": "⏭️", "pending": "⏳", "scheduled": "🗓️"}


def _md(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def render_summary(m: dict, matrix: dict, dec: dict, gate: dict, trigger: dict | None,
                   primary: str, baseline: str | None, x: dict | None = None) -> str:
    from .matrix import case_cost, case_score, config_summary
    x = x or {}
    icon = {"pass": "✅", "fail": "❌", "warn": "⚠️"}
    vicon = {"KEEP": "🟢", "KEEP, SLIM DOWN": "🟡", "RETIRE CANDIDATE": "🟠", "HARMFUL": "🔴", "NOT ASSESSED": "⚪"}
    confs = list(filter(None, (primary, baseline)))
    lines = [f"## {icon[gate['status']]} Skill eval: `{m['skill']}` — gate {gate['status'].upper()} · "
             f"verdict {vicon.get(dec['overall'], '')} **{dec['overall']}**", "",
             "<sub>Evaluated with the **Skills 2.0** eval & benchmark tooling from Anthropic's skill-creator "
             "(evals.json, grader / comparator / analyzer agents, aggregate_benchmark, run_eval, eval viewer).</sub>",
             "", f"> {dec['reason']}", "",
             *(["**❌ Why the automated gate failed** (results are not sent to human review unless the run "
                "is started with *allow_failed_gate*):", ""] + [f"- {f}" for f in gate.get("failed", [])] + [""]
               if gate.get("failed") else []),
             *(["**⚠️ Warnings:** " + " · ".join(gate["warned"]), ""] if gate.get("warned") else []),
             *([f"**Run errors:** " + "; ".join(f"`{e}`" for e in dec["harness_errors"]), ""]
               if dec.get("harness_errors") else [])]
    pr = x.get("profile")
    if pr:
        lines += ["### Skill under test", "",
                  f"**`{pr['name']}`** — SKILL.md {pr['skill_md_lines']} lines (~{pr['skill_md_tokens_est']:,} tokens)"
                  f", {len(pr['bundled_files'])} bundled file(s), version `{m['skill_version']}`", "",
                  "**Description** (what Claude reads to decide whether to load the skill):", "",
                  f"> {pr['description']}", "",
                  f"{pr['description_chars']}/{pr['description_limit']} chars · says when to use: "
                  f"{'✅' if pr['says_when_to_use'] else '❌'} · says when not to use: "
                  f"{'✅' if pr['says_when_not_to_use'] else '❌'}", ""]
    lines += [f"Models {', '.join(f'`{y}`' for y in m['models'])} · grader `{m['grader_model']}` · "
              f"{len(m['evals'])} test cases × {len(m['configurations'])} configs × {m['runs_per_config']} runs · "
              f"test cases from {', '.join(f'`{s}`' for s in m.get('test_case_source', []))} · "
              f"**{m.get('tokens_total', 0):,} tokens · ${m['cost_usd']:.2f}**", "",
              "### Results", "",
              "| Model | Config | Checks passed | Pass rate | Cases fully passed | Tokens | Seconds |",
              "|---|---|---|---|---|---|---|"]
    for model in m["models"]:
        for conf in confs:
            s = config_summary(matrix, model, conf)
            lines.append(f"| {model} | {conf} | {s['checks_passed']:.1f}/{s['checks_total']} | {s['pass_rate']:.0%} | "
                         f"{s['cases_fully_passed']}/{s['cases_total']} | {s['tokens']:,.0f} | {s['seconds']:,.1f} |")
    if dec.get("models"):
        lines += ["", "| Model | Uplift | Only with skill | Unaided | Worse with skill | Verdict |", "|---|---|---|---|---|---|"]
        for p in dec["models"]:
            lines.append(f"| {p['model']} | {p['uplift_pts']:+.0f} pts | {len(p['unique'])} | {len(p['absorbed'])} | "
                         f"{len(p['harmful_checks'])} | {p['verdict']} |")
        for o in dec.get("outlook", []) + dec.get("trend_alerts", []):
            lines.append(f"- {o}")
    if x.get("target"):
        kind, tgt = x["target"]
        lines += ["", f"**Threshold ({kind} skill): {tgt:.0%}** — " + ", ".join(
            f"{mm} {'✅ met' if config_summary(matrix, mm, primary)['pass_rate'] >= tgt else '❌ not met'}"
            for mm in m["models"])]

    comp = x.get("comparison")
    if comp:
        lines += ["", f"### A/B benchmark — blind comparison vs `{baseline}`", "",
                  f"Judge `{comp['judge_model']}` sees both outputs as A/B without knowing which used the skill. "
                  "Skills 2.0 reading: ≥70% keep · 50–70% refine · <50% remove or rewrite.", "",
                  "| Model | Comparisons | Skill wins | Ties | Baseline wins | Win rate | Reading |", "|---|---|---|---|---|---|---|"]
        for mm, v in comp["by_model"].items():
            lines.append(f"| {mm} | {v['comparisons']} | {v['skill_wins']} | {v['ties']} | {v['baseline_wins']} | "
                         f"{'–' if v['win_rate'] is None else format(v['win_rate'], '.0%')} | {v['interpretation']} |")

    us = x.get("usage")
    if us:
        lines += ["", "### Token usage", "", "| Model | Config | Input | Output | Cache read | Cache write | Total | Cost |",
                  "|---|---|---|---|---|---|---|---|"]
        for key, u in us["by_run_config"].items():
            mm, conf = key.split("|")
            lines.append(f"| {mm} | {conf} | {u['input']:,} | {u['output']:,} | {u['cache_read']:,} | "
                         f"{u['cache_write']:,} | {u['total']:,} | ${u['cost_usd']:.3f} |")
        lines += ["", "| Stage | Calls | Total tokens | Cost |", "|---|---|---|---|"]
        for stage, u in us["by_stage"].items():
            lines.append(f"| {stage} | {u['calls']} | {u['total']:,} | ${u['cost_usd']:.3f} |")
        t = us["total"]
        lines.append(f"| **Total** | **{t['calls']}** | **{t['total']:,}** | **${t['cost_usd']:.3f}** |")
        lines += ["", f"<sub>Not metered: {'; '.join(us['not_metered'])}</sub>"]

    lines += ["", "### Per case (checks passed · tokens · seconds)", "", "| Case | " + " | ".join(
        f"{y} {c}" for y in m["models"] for c in confs) + " |", "|---|" + "---|" * (len(m["models"]) * len(confs))]
    for c in matrix["cases"]:
        cells = []
        for model in m["models"]:
            for conf in confs:
                sc, tot = case_score(matrix, model, conf, c["id"])
                tk, sec = case_cost(matrix, model, conf, c["id"])
                cells.append(f"{sc:.1f}/{tot} · {tk:,.0f} · {sec:.0f}s")
        lines.append(f"| {c['id']} · {_md(c['description'][:50])} | " + " | ".join(cells) + " |")

    fails = x.get("failures")
    if fails is not None:
        lines += ["", f"<details><summary><b>Specific failures with the skill ({len(fails)})</b></summary>", ""]
        if fails:
            lines += ["| Model | Case | Run | Check | Grader evidence |", "|---|---|---|---|---|"]
            lines += [f"| {f['model']} | {f['case']} | {f['run']} | {_md(f['check'])} | {_md(f['evidence'][:200])} |"
                      for f in fails]
        else:
            lines.append("None — every check passed with the skill.")
        lines += ["", "</details>"]

    dr = (x.get("analyst") or {}).get("description_review") or {}
    if trigger or dr:
        lines += ["", "### Trigger optimization", ""]
        if trigger:
            s = trigger["summary"]
            lines.append(f"**Trigger evals:** {s['passed']}/{s['total']} correct ({_pct(s['accuracy'])}) · "
                         f"recall {_pct(s['recall'])} · false triggers {_pct(s['false_trigger_rate'])}")
        if dr:
            lines += ["", f"**Description review:** {dr.get('assessment', '')} — {dr.get('reason', '')}"]
            if dr.get("overlaps_with"):
                lines.append(f"Overlaps with: {', '.join(f'`{o}`' for o in dr['overlaps_with'])}")
            if dr.get("suggested_description"):
                lines += ["", "**Suggested description:**", "", f"> {dr['suggested_description']}"]
        if trigger:
            lines += ["", "<details><summary>All trigger queries</summary>", "",
                      "| Query | Should trigger | Trigger rate | Result |", "|---|---|---|---|"]
            lines += [f"| {_md(r['query'])} | {'yes' if r['should_trigger'] else 'no'} | {r['trigger_rate']:.0%} | "
                      f"{'✅' if r['pass'] else '❌'} |" for r in trigger["results"]]
            lines += ["", "</details>"]

    if x.get("steps"):
        lines += ["", "### Skills 2.0 evaluation steps", "", "| | Stage | Step | Skills 2.0 component | Result |",
                  "|---|---|---|---|---|"]
        for r in x["steps"]:
            lines.append(f"| {STATUS_ICON.get(r['status'], '')} | {r['stage']} | {r['step']} | {_md(r['component'])} | "
                         f"{_md(r['result'])}{(' — ' + _md(r['note'])) if r.get('note') else ''} |")

    if x.get("cases"):
        lines += ["", f"<details><summary><b>Test cases used ({len(x['cases'])})</b></summary>", ""]
        for c in x["cases"]:
            lines += [f"**{c['eval_id']} · {c['description']}** — `{c['type']}` · source `{c['source']}`", "",
                      "```text", c["prompt"].strip(), "```"]
            if c.get("expected_output"):
                lines += [f"Expected: {c['expected_output']}", ""]
            if c.get("files"):
                lines += ["Input files: " + ", ".join(f"`{Path(f).name}`" for f in c["files"]), ""]
            lines += [f"- [ ] {a}" for a in c["assertions"]] + [""]
        lines += ["</details>"]

    lines += ["", "### Gate checks", "", "| Check | Result | Value | Threshold |", "|---|---|---|---|"]
    for c in gate["checks"]:
        lines.append(f"| {c['name']} | {icon[c['status']]} | {c['value']} | {c['threshold']} |")
    if gate.get("warnings"):
        lines += ["", "<details><summary>Analyst notes</summary>", ""] + [f"- {w}" for w in gate["warnings"]] + \
                 ["", "</details>"]
    if x.get("rebench"):
        rb = x["rebench"]
        lines += ["", f"**Re-benchmark** next by {rb['next_due']}, and " + "; ".join(rb["when"][:2]) + "."]
    lines += ["", "_Full report: `report.pdf`; every output and grade: `review.html` (run artifacts)._"]
    return "\n".join(lines) + "\n"
