"""Analyst pass: Claude reads the results and writes the report's prose.

Division of labour: code computes every number, table and the verdict; the analyst only writes
narrative (headline, where the skill helped / did not, per-case notes, fixes) and assigns a value
category to checks that the eval author left uncategorised. It is told the numbers and may open
transcripts and outputs to explain them, but its text never replaces a computed figure.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .config import VENDOR_DIR
from .usage import claude_json
from .matrix import case_score, check_rate, config_summary
from .report_pdf import model_label

SCHEMA = """{
  "headline": "2-3 sentences: the uplift on each model and the single most important caveat",
  "made_difference": ["3-6 bullets: concrete things the skill changed, citing case ids"],
  "does_not_help": ["bullets: cases where the skill scored lower or added nothing, each with a concrete fix"],
  "case_notes": {"<case id>": "One or two sentences: 'With skill: ... Without: ...' naming the observable difference"},
  "check_categories": {"<case id>|<exact check text>": "asset | org_knowledge | safety | behavior | quality"},
  "fix_suggestions": ["specific SKILL.md or eval changes, most valuable first"],
  "extra_limits": ["limitations of this evaluation you noticed in the transcripts (e.g. a run that could not find a file)"],
  "description_review": {
    "assessment": "good | too broad | too narrow | overlaps another skill",
    "reason": "1-2 sentences grounded in the trigger results and the other skills' descriptions",
    "overlaps_with": ["names of other skills whose descriptions compete for the same requests"],
    "suggested_description": "a rewritten description (<= 1024 chars) that says what the skill does, WHEN to use it and when NOT to; empty if the current one is good"
  }
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
                cfg: dict, description: str = "", other_skills: dict | None = None,
                trigger: dict | None = None) -> dict:
    digest = build_digest(matrix, primary, baseline)
    digest["skill_description"] = description
    digest["other_skills_in_repo"] = other_skills or {}
    if trigger:
        digest["trigger_results"] = {"summary": trigger.get("summary"),
                                     "misfires": [r for r in trigger["results"] if not r["pass"]][:20]}
    (out_dir / "analyst_input.json").write_text(json.dumps(digest, indent=2))
    if cfg["execution"]["executor"] == "mock" or not cfg["report"].get("analyst", True):
        result = _fallback(matrix, primary, baseline)
        result["description_review"] = review_description(description, trigger, other_skills or {})
        (out_dir / "analyst_usage.json").write_text(json.dumps(
            {"input": 4000, "output": 1500, "cache_read": 20000, "cache_write": 2000, "total": 27500,
             "cost_usd": 0.0, "calls": 1}))
    else:
        analyzer = (VENDOR_DIR / "agents" / "analyzer.md").read_text()
        prompt = (
            f"You are the analyst for an evaluation of the Claude skill '{skill_name}'. Each test case "
            f"ran with the skill ('{primary}') and against a baseline ('{baseline}') on these models: "
            f"{', '.join(matrix['models'])}.\n\n"
            f"The results digest is in {out_dir / 'analyst_input.json'}. Run folders (transcript.md, "
            f"outputs/, grading.json) are under {out_dir}; open them when the digest does not explain a "
            "difference.\n\nThe digest also holds the skill's description, the trigger-eval misfires and the "
            "descriptions of the other skills in the repo: review the description as skill-creator's "
            "'Description Optimization' section and the trigger guidance (too broad, too narrow, overlapping; "
            "it must say when to use the skill AND when not to).\n\nFor background, here is skill-creator's analyzer guidance (use its "
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
        target = out_dir / "analyst.json"
        target.unlink(missing_ok=True)
        stdout, _, _ = claude_json(prompt, cfg["execution"]["grader_model"], out_dir, 1800,
                                   usage_file=out_dir / "analyst_usage.json")
        result = None
        for text in ([target.read_text()] if target.exists() else []) + [stdout or ""]:
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
        if not isinstance(result.get("description_review"), dict) or not result["description_review"].get("assessment"):
            result["description_review"] = review_description(description, trigger, other_skills or {})
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


def review_description(description: str, trigger: dict | None, others: dict) -> dict:
    """Rule-based description review (used for mock runs or when the analyst fails)."""
    d = description.lower()
    reasons, assessment = [], "good"
    s = (trigger or {}).get("summary") or {}
    if s.get("recall") is not None and s["recall"] < 0.6:
        assessment = "too narrow"
        reasons.append(f"only {s['recall']:.0%} of should-trigger queries loaded the skill")
    if s.get("false_trigger_rate") and s["false_trigger_rate"] > 0.2:
        assessment = "too broad"
        reasons.append(f"{s['false_trigger_rate']:.0%} of near-miss queries loaded it")
    if not any(k in d for k in ("use when", "use this when", "use this whenever", "whenever")):
        reasons.append("it does not say when to use the skill")
    if not any(k in d for k in ("not for", "do not use", "don't use", "not when", "instead")):
        reasons.append("it does not say when NOT to use the skill")
    words = set(re.findall(r"[a-z]{5,}", d))
    overlaps = [n for n, od in others.items()
                if len(words & set(re.findall(r"[a-z]{5,}", od.lower()))) >= max(6, len(words) // 3)]
    if overlaps and assessment == "good":
        assessment = "overlaps another skill"
    if overlaps:
        reasons.append("its wording overlaps " + ", ".join(overlaps))
    if assessment == "good" and reasons:
        assessment = "could be clearer"
    return {"assessment": assessment, "reason": ("; ".join(reasons) or "states what it does and when to use it")
            .capitalize() + ".", "overlaps_with": overlaps, "suggested_description": ""}
