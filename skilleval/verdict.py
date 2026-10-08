"""Is the skill still useful, or are models making it obsolete?

Decided per model from three signals, all computed from the results matrix:

  uplift         with-skill pass rate minus without-skill pass rate (percentage points)
  unique checks  checks the model passes only with the skill   -> the value the skill still adds
  absorbed       checks the model now passes without the skill  -> guidance the model no longer needs

Per-model verdict
  HARMFUL           uplift < 0                         the skill makes outputs worse
  KEEP              uplift >= keep_min_uplift_pts, or a unique check in a never-retire category
  KEEP, SLIM DOWN   uplift >= slim_min_uplift_pts      worth keeping; drop guidance for absorbed checks
  RETIRE CANDIDATE  below that and unique checks <= retire_max_unique_checks
  KEEP, SLIM DOWN   (below slim but still several unique checks - narrow but real value)

Overall: the best verdict across production models (the skill stays while any of them needs it),
plus an obsolescence outlook from (a) uplift falling as models get more capable within this run and
(b) uplift falling for the same model versus previously approved evaluations.
"""
from __future__ import annotations

from collections import Counter, defaultdict

from .matrix import case_score, check_rate, config_summary


def _l(m: str) -> str:
    from .report_pdf import model_label
    return model_label(m)

RANK = {"HARMFUL": 0, "RETIRE CANDIDATE": 1, "KEEP, SLIM DOWN": 2, "KEEP": 3}
LONG_LIVED = {"asset", "org_knowledge"}   # value models cannot learn from public training data


def _defaults(v: dict) -> dict:
    return {"keep_min_uplift_pts": 15, "slim_min_uplift_pts": 5, "retire_max_unique_checks": 1,
            "never_retire_categories": ["safety"], "trend_drop_alert_pts": 10,
            "production_models": [], **(v or {})}


def model_analysis(matrix: dict, model: str, primary: str, baseline: str, categories: dict,
                   vcfg: dict) -> dict:
    w = config_summary(matrix, model, primary)
    wo = config_summary(matrix, model, baseline)
    uplift = (w["pass_rate"] - wo["pass_rate"]) * 100
    unique, absorbed, harmful_checks, both_fail = [], [], [], []
    harmful_cases = []
    for c in matrix["cases"]:
        for i, chk in enumerate(c["checks"]):
            a = check_rate(matrix, model, primary, c["id"], i)
            b = check_rate(matrix, model, baseline, c["id"], i)
            if a is None or b is None:
                continue
            cat = chk.get("category") or categories.get(f"{c['id']}|{chk['text']}") or "uncategorized"
            item = {"case": c["id"], "check": chk["text"], "category": cat, "with": a, "without": b}
            if a >= 0.5 and b < 0.5:
                unique.append(item)
            elif a >= 0.5 and b >= 0.5:
                absorbed.append(item)
            elif a < 0.5 and b >= 0.5:
                harmful_checks.append(item)
            else:
                both_fail.append(item)
        sw, _ = case_score(matrix, model, primary, c["id"])
        sb, _ = case_score(matrix, model, baseline, c["id"])
        if sw < sb:
            harmful_cases.append({"case": c["id"], "description": c["description"], "with": sw, "without": sb})

    unique_cats = Counter(u["category"] for u in unique)
    never_retire = [u for u in unique if u["category"] in set(vcfg["never_retire_categories"])]
    if uplift < 0:
        verdict = "HARMFUL"
    elif uplift >= vcfg["keep_min_uplift_pts"] or never_retire:
        verdict = "KEEP"
    elif uplift >= vcfg["slim_min_uplift_pts"] or len(unique) > vcfg["retire_max_unique_checks"]:
        verdict = "KEEP, SLIM DOWN"
    else:
        verdict = "RETIRE CANDIDATE"
    token_overhead = (w["tokens"] / wo["tokens"] - 1) * 100 if wo["tokens"] else 0.0
    time_overhead = (w["seconds"] / wo["seconds"] - 1) * 100 if wo["seconds"] else 0.0
    return {"model": model, "with": w, "without": wo, "uplift_pts": uplift, "verdict": verdict,
            "unique": unique, "absorbed": absorbed, "harmful_checks": harmful_checks,
            "both_fail": both_fail, "harmful_cases": harmful_cases,
            "unique_by_category": dict(unique_cats),
            "long_lived_share": (sum(unique_cats[c] for c in LONG_LIVED) / len(unique)) if unique else 0.0,
            "token_overhead_pct": token_overhead, "time_overhead_pct": time_overhead}


def decide(matrix: dict, primary: str, baseline: str | None, categories: dict, vcfg: dict,
           history: list[dict] | None = None) -> dict:
    vcfg = _defaults(vcfg)
    if not baseline:
        return {"overall": "NOT ASSESSED", "reason": "No baseline configuration was run, so the skill's "
                "contribution cannot be measured. Run with baseline without_skill.", "models": []}
    per_model = [model_analysis(matrix, m, primary, baseline, categories, vcfg) for m in matrix["models"]]
    prod = [p for p in per_model if not vcfg["production_models"] or p["model"] in vcfg["production_models"]]
    prod = prod or per_model
    best = max(prod, key=lambda p: (RANK[p["verdict"]], p["uplift_pts"]))
    overall = best["verdict"]

    outlook: list[str] = []
    # (a) across models in this run, ordered least -> most capable
    if len(per_model) > 1:
        first, last = per_model[0], per_model[-1]
        diff = last["uplift_pts"] - first["uplift_pts"]
        if diff < -3:
            outlook.append(f"Uplift shrinks as capability rises: {first['uplift_pts']:+.0f} pts on "
                           f"{_l(first['model'])} vs {last['uplift_pts']:+.0f} pts on {_l(last['model'])}. The more "
                           f"capable model already passes {len(last['absorbed'])} checks unaided.")
        elif diff > 3:
            outlook.append(f"Uplift grows with capability ({first['uplift_pts']:+.0f} -> "
                           f"{last['uplift_pts']:+.0f} pts): stronger models make better use of the skill.")
        else:
            outlook.append("Uplift is about the same across models in this run.")
    # (b) against previous approved evaluations of the same model
    trend: dict[str, list] = defaultdict(list)
    for h in history or []:
        for m, s in (h.get("models") or {}).items():
            trend[m].append({"date": h.get("approved_at", "")[:10], "uplift_pts": s.get("uplift_pts"),
                             "with": s.get("with"), "without": s.get("without"),
                             "skill_version": h.get("skill_version")})
    alerts = []
    for p in per_model:
        past = [t for t in trend.get(p["model"], []) if t["uplift_pts"] is not None]
        if past:
            prev = past[-1]
            drop = prev["uplift_pts"] - p["uplift_pts"]
            if drop >= vcfg["trend_drop_alert_pts"]:
                alerts.append(f"{_l(p['model'])}: uplift fell {drop:.0f} pts since {prev['date']} "
                              f"({prev['uplift_pts']:+.0f} -> {p['uplift_pts']:+.0f}).")
    # (c) what the remaining value is made of
    if best["unique"]:
        share = best["long_lived_share"]
        if share >= 0.6:
            outlook.append(f"{share:.0%} of the checks only the skill gets right on {_l(best['model'])} depend on "
                           "bundled assets or organisation-specific rules, which model upgrades will not "
                           "supply. Expect the skill to stay useful; expect its generic guidance to become "
                           "redundant.")
        elif share <= 0.3:
            outlook.append(f"Most of the remaining value on {_l(best['model'])} is generic behaviour or quality "
                           "that newer models tend to learn. Re-evaluate on each new model release.")

    reason = _reason(best, overall, vcfg)
    return {"overall": overall, "reason": reason, "decided_on": best["model"], "outlook": outlook,
            "trend_alerts": alerts, "trend": {m: v for m, v in trend.items()}, "models": per_model}


def _reason(best: dict, overall: str, vcfg: dict) -> str:
    u, n_u, n_a = best["uplift_pts"], len(best["unique"]), len(best["absorbed"])
    m = _l(best["model"])
    if overall == "HARMFUL":
        return (f"On {m} the skill lowers the pass rate by {-u:.0f} pts. Fix the cases listed under "
                "'Where it does not help' or retire it.")
    if overall == "KEEP":
        why = (f"raises the pass rate by {u:+.0f} pts" if u >= vcfg["keep_min_uplift_pts"] else
               "prevents outcomes the model otherwise gets wrong in a never-retire category")
        return (f"Still useful. On {m} the skill {why}; {n_u} checks pass only with the skill. "
                f"{n_a} checks pass with or without it.")
    if overall == "KEEP, SLIM DOWN":
        return (f"Useful but shrinking. On {m} the uplift is {u:+.0f} pts from {n_u} checks; the model "
                f"handles {n_a} checks unaided. Trim guidance for the absorbed checks to cut the token "
                f"overhead ({best['token_overhead_pct']:+.0f}%).")
    return (f"Likely obsolete. On {m} the uplift is only {u:+.0f} pts and {n_u} checks depend on the skill. "
            "Consider retiring it, or keep only its bundled assets and organisation-specific facts.")


def history_entry(decision: dict) -> dict:
    return {p["model"]: {"with": round(p["with"]["pass_rate"], 4), "without": round(p["without"]["pass_rate"], 4),
                         "uplift_pts": round(p["uplift_pts"], 1), "verdict": p["verdict"]}
            for p in decision.get("models", [])}
