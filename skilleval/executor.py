"""Executes one eval run in an isolated project directory.

`claude-code` executor: runs headless Claude Code (`claude -p`) with the skill installed
under <workdir>/.claude/skills/<name>/ (or not installed, for the without_skill baseline),
captures the stream-json transcript, and writes the files skill-creator's aggregator and
viewer expect:

  run-N/transcript.md        human-readable transcript (viewer + grader input)
  run-N/transcript.jsonl     raw stream-json events
  run-N/timing.json          duration, tokens, cost
  run-N/outputs/             files the run produced + final_response.md
  run-N/outputs/metrics.json tool-call counts, errors, skill_triggered

`mock` executor: writes plausible fake results without calling any model. Use it to test
the pipeline, the workflow, and the review/publish flow at zero cost.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .evals import EvalCase

HARNESS_NOTE = (
    "\n\n---\nEvaluation harness note: any input files are in ./inputs/. "
    "Save every file you produce into ./outputs/ in the current directory."
)


@dataclass
class RunSpec:
    case: EvalCase
    config: str                 # with_skill | without_skill | new_skill | old_skill
    run_number: int
    run_dir: Path
    skill_name: str
    skill_src: Path | None      # None = run without the skill
    skill_root: Path            # the skill under test (for resolving eval input files)
    model: str
    timeout: int
    budget_usd: float | None


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def _prepare_workdir(spec: RunSpec) -> Path:
    work = spec.run_dir / "workdir"
    if work.exists():
        shutil.rmtree(work)
    (work / "outputs").mkdir(parents=True)
    (work / "inputs").mkdir()
    if spec.skill_src is not None:
        shutil.copytree(spec.skill_src, work / ".claude" / "skills" / spec.skill_name,
                        ignore=shutil.ignore_patterns("evals", "__pycache__", "*-workspace"))
    for f in spec.case.files:
        src = spec.skill_root / f
        if src.is_dir():
            shutil.copytree(src, work / "inputs" / src.name)
        else:
            shutil.copy2(src, work / "inputs" / src.name)
    return work


def _render_transcript(prompt: str, events: list[dict]) -> tuple[str, Counter, int, bool, str, dict]:
    """Turn stream-json events into markdown and extract metrics."""
    lines = ["## Eval Prompt", "", prompt, "", "## Transcript", ""]
    tools: Counter = Counter()
    errors = 0
    final_text = ""
    result_evt: dict = {}
    triggered_markers: list[dict] = []
    for ev in events:
        etype = ev.get("type")
        if etype == "assistant":
            for block in ev.get("message", {}).get("content", []) or []:
                if block.get("type") == "text" and block.get("text", "").strip():
                    lines += [f"**Assistant:** {block['text'].strip()}", ""]
                elif block.get("type") == "tool_use":
                    tools[block.get("name", "?")] += 1
                    inp = json.dumps(block.get("input", {}))[:1500]
                    lines += [f"**Tool call — {block.get('name')}:** `{inp}`", ""]
                    triggered_markers.append(block)
        elif etype == "user":
            content = ev.get("message", {}).get("content", [])
            for block in content if isinstance(content, list) else []:
                if block.get("type") == "tool_result":
                    if block.get("is_error"):
                        errors += 1
                    body = block.get("content")
                    if isinstance(body, list):
                        body = " ".join(b.get("text", "") for b in body if isinstance(b, dict))
                    lines += [f"<details><summary>Tool result{' (error)' if block.get('is_error') else ''}"
                              f"</summary>\n\n```\n{str(body)[:3000]}\n```\n</details>", ""]
        elif etype == "result":
            result_evt = ev
            final_text = ev.get("result") or ""
    lines += ["## Final Response", "", final_text or "(none)"]
    return "\n".join(lines), tools, errors, bool(result_evt.get("is_error")), final_text, result_evt


def _skill_triggered(events: list[dict], skill_name: str) -> bool:
    needle = f".claude/skills/{skill_name}"
    for ev in events:
        if ev.get("type") != "assistant":
            continue
        for b in ev.get("message", {}).get("content", []) or []:
            if b.get("type") != "tool_use":
                continue
            inp = json.dumps(b.get("input", {}))
            if b.get("name") == "Skill" and skill_name in inp:
                return True
            if needle in inp:
                return True
    return False


def run_claude_code(spec: RunSpec) -> dict:
    spec.run_dir.mkdir(parents=True, exist_ok=True)
    work = _prepare_workdir(spec)
    prompt = spec.case.prompt + HARNESS_NOTE
    cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose",
           "--model", spec.model, "--dangerously-skip-permissions",
           "--setting-sources", "project"]
    if spec.budget_usd:
        cmd += ["--max-budget-usd", str(spec.budget_usd)]
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

    start = time.time()
    timed_out = False
    try:
        proc = subprocess.run(cmd, cwd=work, env=env, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=spec.timeout)
        stdout, stderr, rc = proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired as e:
        timed_out = True
        stdout = (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
        stderr, rc = f"timed out after {spec.timeout}s", -1
    wall = time.time() - start

    (spec.run_dir / "transcript.jsonl").write_text(stdout)
    events = []
    for line in stdout.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    md, tools, tool_errors, is_error, final_text, result_evt = _render_transcript(spec.case.prompt, events)
    if rc != 0 or timed_out:
        md += f"\n\n## Harness Error\n\nexit code {rc}\n\n```\n{stderr[-4000:]}\n```\n"
    (spec.run_dir / "transcript.md").write_text(md)

    out_dir = spec.run_dir / "outputs"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    shutil.copytree(work / "outputs", out_dir)
    (out_dir / "final_response.md").write_text(final_text or "(no final response)")

    usage = result_evt.get("usage", {}) or {}
    total_tokens = sum(int(usage.get(k, 0) or 0) for k in
                       ("input_tokens", "output_tokens", "cache_creation_input_tokens",
                        "cache_read_input_tokens"))
    files_created = sorted(str(p.relative_to(out_dir)) for p in out_dir.rglob("*")
                           if p.is_file() and p.name != "final_response.md")
    harness_error = timed_out or rc != 0 or is_error
    metrics = {
        "tool_calls": dict(tools),
        "total_tool_calls": sum(tools.values()),
        "total_steps": int(result_evt.get("num_turns", 0) or 0),
        "files_created": files_created,
        "errors_encountered": tool_errors + (1 if harness_error else 0),
        "harness_error": harness_error,
        "output_chars": sum(p.stat().st_size for p in out_dir.rglob("*") if p.is_file()),
        "transcript_chars": len(md),
        "skill_triggered": _skill_triggered(events, spec.skill_name) if spec.skill_src else None,
    }
    _write_json(out_dir / "metrics.json", metrics)
    duration_ms = int(result_evt.get("duration_ms") or wall * 1000)
    _write_json(spec.run_dir / "timing.json", {
        "total_tokens": total_tokens,
        "duration_ms": duration_ms,
        "total_duration_seconds": round(duration_ms / 1000, 1),
        "cost_usd": result_evt.get("total_cost_usd"),
        "executor_model": spec.model,
    })
    shutil.rmtree(work, ignore_errors=True)
    return metrics


def run_mock(spec: RunSpec) -> dict:
    """Deterministic fake run: with-skill runs look better than baselines."""
    spec.run_dir.mkdir(parents=True, exist_ok=True)
    seed = int(hashlib.md5(f"{spec.case.id}{spec.config}{spec.run_number}".encode()).hexdigest()[:8], 16)
    has_skill = spec.skill_src is not None and spec.config in ("with_skill", "new_skill")
    out = spec.run_dir / "outputs"
    out.mkdir(parents=True, exist_ok=True)
    text = (f"[mock] {'Used skill ' + spec.skill_name if has_skill else 'No skill'} for: "
            f"{spec.case.prompt[:200]}")
    (out / "final_response.md").write_text(text)
    (spec.run_dir / "transcript.md").write_text(
        f"## Eval Prompt\n\n{spec.case.prompt}\n\n## Transcript\n\n**Assistant:** {text}\n\n"
        f"## Final Response\n\n{text}\n")
    tokens = 3000 + seed % 2000 + (1500 if has_skill else 0)
    secs = 20 + seed % 25 + (8 if has_skill else 0)
    metrics = {"tool_calls": {"Read": 2 + seed % 3, "Write": 1}, "total_tool_calls": 3 + seed % 3,
               "total_steps": 3, "files_created": [], "errors_encountered": 0, "harness_error": False,
               "output_chars": len(text), "transcript_chars": len(text) * 2,
               "skill_triggered": (spec.case.type != "should_not_trigger") if has_skill else None}
    _write_json(out / "metrics.json", metrics)
    _write_json(spec.run_dir / "timing.json", {"total_tokens": tokens, "duration_ms": secs * 1000,
                                                "total_duration_seconds": float(secs), "cost_usd": 0.0,
                                                "executor_model": "mock"})
    return metrics


EXECUTORS = {"claude-code": run_claude_code, "mock": run_mock}
