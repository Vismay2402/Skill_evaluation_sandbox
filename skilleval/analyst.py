"""Analyst pass: Claude reads the results and writes the report's prose.

Division of labour: code computes every number, table and the verdict; the analyst only writes
narrative (headline, where the skill helped / did not, per-case notes, fixes) and assigns a value
category to checks that the eval author left uncategorised. It is told the numbers and may open
transcripts and outputs to explain them, but its text never replaces a computed figure.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from .config import VENDOR_DIR
from .matrix import case_score, check_rate, config_summary
from .report_pdf import model_label

SCHEMA = """{
  "headline": "2-3 sentences: the uplift on each model and the single most important caveat",
  "made_difference": ["3-6 bullets: concrete things the skill changed, citing case ids"],
  "does_not_help": ["bullets: cases where the skill scored lower or added nothing, each with a concrete fix"],
  "case_notes": {"<case id>": "One or two sentences: 'With skill: ... Without: ...' naming the observable difference"},
  "check_categories": {"<case id>|<exact check text>": "asset | org_knowledge | safety | behavior | quality"},
  "fix_suggestions": ["specific SKILL.md or eval changes, most valuable first"],
  "extra_limits": ["limitations of this evaluation you noticed in the transcripts (e.g. a run that could not find a file)"]
}"""


def build_digest(matrix: dict, primary: str, baseline: str | None) -> dict:
    cases = []
    for c in matrix["cases"]:
        entry = {"id": c["id"], "description": c["description"], "type": c["type"],
                 "prompt": c["prompt"][:700], "checks": [], "runs": {}}
        for i, chk in enumerate(c["checks"]):
            row = {"text": chk["text"], "category": chk.get("category")}
            for m in matrix["models"]:
                for conf in filter(None, (primary, baseline)):
                    r = check_rate(matrix, m, conf, c["id"], i)
                    row[f"{m}/{conf}"] = None if r is None else round(r, 2)
            entry["checks"].append(row)
        for m in matrix["models"]:
            for conf in filter(None, (primary, baseline)):
                runs = matrix["results"][m][conf][str(c["id"])]
                if runs:
                    sc, tot = case_score(matrix, m, conf, c["id"])
                    entry["runs"][f"{m}/{conf}"] = {
                        "score": f"{sc:.1f}/{tot}", "files": runs[0]["files"],
                        "final_response_excerpt": runs[0]["final_response"][:900],
                        "run_dir": runs[0]["run_dir"]}
        cases.append(entry)
    totals = {f"{m}/{conf}": config_summary(matrix, m, conf)
              for m in matrix["models"] for conf in filter(None, (primary, baseline))}
    return {"models": matrix["models"], "configs": [primary, baseline], "totals": totals, "cases": cases}


def run_analyst(out_dir: Path, matrix: dict, primary: str, baseline: str | None, skill_name: str,
                cfg: dict) -> dict:
    digest = build_digest(matrix, primary, baseline)
    (out_dir / "analyst_input.json").write_text(json.dumps(digest, indent=2))
    if cfg["execution"]["executor"] == "mock" or not cfg["report"].get("analyst", True):
        result = _fallback(matrix, primary, baseline)
    else:
        analyzer = (VENDOR_DIR / "agents" / "analyzer.md").read_text()
        prompt = (
            f"You are the analyst for an evaluation of the Claude skill '{skill_name}'. Each test case "
            f"ran with the skill ('{primary}') and against a baseline ('{baseline}') on these models: "
            f"{', '.join(matrix['models'])}.\n\n"
            f"The results digest is in {out_dir / 'analyst_input.json'}. Run folders (transcript.md, "
            f"outputs/, grading.json) are under {out_dir}; open them when the digest does not explain a "
            "difference.\n\nFor background, here is skill-creator's analyzer guidance (use its "
            "'Analyzing Benchmark Results' section):\n\n" + analyzer[:6000] +
            "\n\n---\nRefer to models by these names: " + ", ".join(
                f"{m} = {model_label(m)}" for m in matrix["models"]) + ". Write plain, specific prose for a busy engineering lead: name what was observably "
            "different, cite case ids, no hype, no hedging. Do NOT restate or recompute aggregate numbers - "
            "the report prints those from code. For check_categories, categorise EVERY check whose "
            "category is null: asset (needs a file bundled with the skill), org_knowledge (a rule or fact "
            "only this organisation knows), safety (refusing or preventing something prohibited), "
            "behavior (workflow: asking first, staying out of the way), quality (general output quality "
            "any good model should reach).\n\n"
            f"Write ONLY a JSON object with this shape to {out_dir / 'analyst.json'}:\n{SCHEMA}")
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        target = out_dir / "analyst.json"
        target.unlink(missing_ok=True)
        proc = subprocess.run(["claude", "-p", prompt, "--model", cfg["execution"]["grader_model"],
                               "--output-format", "text", "--dangerously-skip-permissions",
                               "--setting-sources", "project"],
                              cwd=out_dir, env=env, capture_output=True, text=True, timeout=1800,
                              stdin=subprocess.DEVNULL)
        result = None
        for text in ([target.read_text()] if target.exists() else []) + [proc.stdout or ""]:
            m = re.search(r"\{[\s\S]*\}", text)
            if m:
                try:
                    result = json.loads(m.group(0))
                    break
                except json.JSONDecodeError:
                    continue
        if result is None:
            result = _fallback(matrix, primary, baseline)
            result["extra_limits"].append("The analyst pass failed; narrative sections were generated "
                                          "from the numbers only.")
    for k, default in (("headline", ""), ("made_difference", []), ("does_not_help", []), ("case_notes", {}),
                       ("check_categories", {}), ("fix_suggestions", []), ("extra_limits", [])):
        result.setdefault(k, default)
    result["case_notes"] = {str(k): v for k, v in result["case_notes"].items()}
    (out_dir / "analyst.json").write_text(json.dumps(result, indent=2))
    return result


def _fallback(matrix: dict, primary: str, baseline: str | None) -> dict:
    """Numbers-only narrative used for mock runs or if the analyst call fails."""
    from .report_pdf import model_label as _l
    notes, helped, nohelp = {}, [], []
    for c in matrix["cases"]:
        parts = []
        for m in matrix["models"]:
            a, tot = case_score(matrix, m, primary, c["id"])
            if baseline:
                b, _ = case_score(matrix, m, baseline, c["id"])
                parts.append(f"{_l(m)}: {a:.0f}/{tot} with skill vs {b:.0f}/{tot} without")
                if a > b:
                    helped.append((a - b, f"{c['id']} ({c['description']}) on {_l(m)}: +{a - b:.0f} checks"))
                elif a < b:
                    nohelp.append(f"{c['id']} ({c['description']}) scores lower with the skill on {_l(m)} "
                                  f"({a:.0f} vs {b:.0f}).")
            else:
                parts.append(f"{_l(m)}: {a:.0f}/{tot}")
        notes[str(c["id"])] = "; ".join(parts) + "."
    helped.sort(reverse=True)
    return {"headline": "", "made_difference": [h for _, h in helped[:6]], "does_not_help": nohelp,
            "case_notes": notes, "check_categories": {}, "fix_suggestions": [], "extra_limits": []}
