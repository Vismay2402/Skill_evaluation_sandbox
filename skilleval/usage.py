"""Token accounting.

Every `claude -p` call the pipeline makes (skill runs, grading, blind comparison, analyst) records its
usage, so the report can show exactly where tokens and money went.

usage dict: {"input": n, "output": n, "cache_read": n, "cache_write": n, "total": n, "cost_usd": x, "calls": n}
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

KEYS = ("input", "output", "cache_read", "cache_write", "total", "cost_usd", "calls")


def empty() -> dict:
    return {k: 0 for k in KEYS} | {"cost_usd": 0.0}


def from_result_event(evt: dict) -> dict:
    """Usage from the final `result` event of `claude -p --output-format json|stream-json`."""
    u = (evt or {}).get("usage", {}) or {}
    out = {"input": int(u.get("input_tokens", 0) or 0), "output": int(u.get("output_tokens", 0) or 0),
           "cache_read": int(u.get("cache_read_input_tokens", 0) or 0),
           "cache_write": int(u.get("cache_creation_input_tokens", 0) or 0),
           "cost_usd": float(evt.get("total_cost_usd") or 0.0) if evt else 0.0, "calls": 1 if evt else 0}
    out["total"] = out["input"] + out["output"] + out["cache_read"] + out["cache_write"]
    return out


def add(a: dict, b: dict) -> dict:
    out = empty()
    for k in KEYS:
        out[k] = (a or {}).get(k, 0) + (b or {}).get(k, 0)
    out["cost_usd"] = round(out["cost_usd"], 6)
    return out


def total_of(paths) -> dict:
    acc = empty()
    for p in paths:
        try:
            acc = add(acc, json.loads(Path(p).read_text()))
        except (OSError, json.JSONDecodeError):
            continue
    return acc


def claude_json(prompt: str, model: str, cwd: Path, timeout: int, usage_file: Path | None = None,
                extra: list[str] | None = None) -> tuple[str, str, dict]:
    """Run `claude -p` with JSON output. Returns (result text, stderr, usage) and writes usage_file."""
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    cmd = ["claude", "-p", prompt, "--model", model, "--output-format", "json",
           "--dangerously-skip-permissions", "--setting-sources", "project"] + (extra or [])
    try:
        proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL)
        stdout, stderr = proc.stdout or "", proc.stderr or ""
    except subprocess.TimeoutExpired:
        stdout, stderr = "", f"timed out after {timeout}s"
    evt: dict = {}
    try:
        evt = json.loads(stdout)
        if isinstance(evt, list):  # some versions print the whole event list
            evt = next((e for e in reversed(evt) if isinstance(e, dict) and e.get("type") == "result"), {})
    except json.JSONDecodeError:
        evt = {}
    usage = from_result_event(evt)
    if usage_file is not None:
        usage_file.write_text(json.dumps(usage, indent=2))
    return (evt.get("result") or stdout), stderr, usage
