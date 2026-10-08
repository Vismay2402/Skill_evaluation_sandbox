"""Renders the evaluation report PDF (reportlab).

Sections follow the eval report template: headline and model table, where the skill helped / did
not, per-case side by side, tokens and time, what differed per case, check-by-check results, test
cases and method, limits - plus a verdict page on whether the skill is still useful as models evolve.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

from .matrix import case_cost, case_score, check_rate, config_summary, runs_per_config

INK = colors.HexColor("#1F2328")
MUTED = colors.HexColor("#59636E")
RULE = colors.HexColor("#D0D7DE")
HEAD_BG = colors.HexColor("#F3F4F6")
PASS = colors.HexColor("#1A7F37")
FAIL = colors.HexColor("#CF222E")
VERDICT_COLORS = {"KEEP": "#1A7F37", "KEEP, SLIM DOWN": "#9A6700", "RETIRE CANDIDATE": "#BC4C00",
                  "HARMFUL": "#CF222E", "NOT ASSESSED": "#59636E"}

_ss = getSampleStyleSheet()
TITLE = ParagraphStyle("t", parent=_ss["Title"], fontName="Helvetica-Bold", fontSize=17, leading=21,
                       alignment=TA_LEFT, textColor=INK, spaceAfter=2)
SUB = ParagraphStyle("s", fontName="Helvetica", fontSize=9.5, leading=13, textColor=MUTED, spaceAfter=8)
H2 = ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=12.5, leading=16, textColor=INK,
                    spaceBefore=10, spaceAfter=5)
BODY = ParagraphStyle("b", fontName="Helvetica", fontSize=9.5, leading=13, textColor=INK, spaceAfter=4)
BULLET = ParagraphStyle("bl", parent=BODY, leftIndent=12, bulletIndent=2, spaceAfter=3)
CELL = ParagraphStyle("c", fontName="Helvetica", fontSize=7.8, leading=9.6, textColor=INK)
CELL_B = ParagraphStyle("cb", parent=CELL, fontName="Helvetica-Bold")
CELL_H = ParagraphStyle("ch", parent=CELL, fontName="Helvetica-Bold", textColor=MUTED)


def model_label(m: str) -> str:
    """claude-sonnet-5-5 -> Sonnet 5.5"""
    parts = m.replace("claude-", "").split("-")
    words = [p for p in parts if not p.isdigit()]
    nums = [p for p in parts if p.isdigit() and len(p) < 4]
    if not words:
        return m
    return " ".join(w.capitalize() for w in words) + (" " + ".".join(nums) if nums else "")


def _p(text, style=CELL) -> Paragraph:
    return Paragraph(escape(str(text)), style)


def _fmt_checks(x: float, runs: int) -> str:
    return f"{x:.0f}" if runs <= 1 or abs(x - round(x)) < 0.05 else f"{x:.1f}"


def _table(rows, widths, header_rows=1, align_right_from=None, zebra=False):
    data = [[c if isinstance(c, Paragraph) else _p(c, CELL_H if r < header_rows else CELL)
             for c in row] for r, row in enumerate(rows)]
    t = Table(data, colWidths=widths, repeatRows=header_rows)
    style = [("VALIGN", (0, 0), (-1, -1), "TOP"),
             ("LINEBELOW", (0, header_rows - 1), (-1, header_rows - 1), 0.8, RULE),
             ("LINEBELOW", (0, header_rows), (-1, -1), 0.3, RULE),
             ("BACKGROUND", (0, 0), (-1, header_rows - 1), HEAD_BG),
             ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
             ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4)]
    if zebra:
        for r in range(header_rows, len(rows)):
            if (r - header_rows) % 2:
                style.append(("BACKGROUND", (0, r), (-1, r), colors.HexColor("#FAFBFC")))
    t.setStyle(TableStyle(style))
    return t


def _passfail(rate: float | None, runs: int) -> Paragraph:
    if rate is None:
        return _p("-")
    if runs <= 1:
        ok = rate >= 0.5
        return Paragraph(f'<font color="{PASS.hexval() if ok else FAIL.hexval()}"><b>{"PASS" if ok else "FAIL"}'
                         f'</b></font>', CELL)
    n = round(rate * runs)
    col = PASS if rate >= 0.5 else FAIL
    return Paragraph(f'<font color="{col.hexval()}"><b>{n}/{runs}</b></font>', CELL)


def _bullets(items) -> list:
    return [Paragraph(escape(i), BULLET, bulletText="•") for i in items if i]


def _footer(canvas, doc, label):
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(14 * mm, 9 * mm, label)
    canvas.drawRightString(landscape(A4)[0] - 14 * mm, 9 * mm, f"Page {doc.page}")
    canvas.restoreState()


def render_pdf(path: Path, ctx: dict) -> None:
    mx, dec, an = ctx["matrix"], ctx["decision"], ctx["analyst"]
    primary, baseline, models = ctx["primary"], ctx["baseline"], mx["models"]
    runs = runs_per_config(mx)
    W = landscape(A4)[0] - 28 * mm
    n_checks = sum(len(c["checks"]) for c in mx["cases"])
    mlabels = {m: model_label(m) for m in models}
    story: list = []

    # ---- Title ---------------------------------------------------------------------------------
    story.append(Paragraph(f"{escape(ctx['skill'])} skill: evaluation and benchmark results", TITLE))
    base_txt = {"without_skill": "with and without the skill", "old_skill": "with the new and the previous "
                "version of the skill"}.get(baseline or "", "with the skill")
    story.append(Paragraph(
        f"{len(mx['cases'])} test cases, each run {base_txt}, on {', '.join(mlabels.values())}"
        f"{f', {runs} runs each' if runs > 1 else ''}. Graded against {n_checks} checks. "
        f"Skill version {ctx['version'][:8]} &middot; {ctx['date']}"
        + (" &middot; <font color='#CF222E'><b>MOCK DATA - dry run, no model calls</b></font>"
           if ctx["executor"] == "mock" else ""), SUB))

    # ---- Verdict -------------------------------------------------------------------------------
    v = dec["overall"]
    vcol = colors.HexColor(VERDICT_COLORS.get(v, "#59636E"))
    box = Table([[Paragraph(f'<font color="white"><b>{escape(v)}</b></font>',
                            ParagraphStyle("v", parent=BODY, fontSize=13, leading=16)),
                  Paragraph(f"<b>Is the skill still useful?</b> {escape(dec['reason'])}", BODY)]],
                colWidths=[48 * mm, W - 48 * mm])
    box.setStyle(TableStyle([("BACKGROUND", (0, 0), (0, 0), vcol), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                             ("BOX", (0, 0), (-1, -1), 0.8, vcol), ("LEFTPADDING", (0, 0), (-1, -1), 8),
                             ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7)]))
    story += [box, Spacer(1, 6)]
    if dec.get("models"):
        rows = [["Model", "With skill", "Without skill", "Uplift", "Checks only the skill gets right",
                 "Checks the model passes unaided", "Checks worse with skill", "Token overhead", "Verdict"]]
        for p in dec["models"]:
            cats = ", ".join(f"{k.replace('_', ' ')} {n}" for k, n in sorted(p["unique_by_category"].items()))
            rows.append([mlabels[p["model"]], f"{p['with']['pass_rate']:.0%}", f"{p['without']['pass_rate']:.0%}",
                         f"{p['uplift_pts']:+.0f} pts", f"{len(p['unique'])}" + (f" ({cats})" if cats else ""),
                         str(len(p["absorbed"])), str(len(p["harmful_checks"])),
                         f"{p['token_overhead_pct']:+.0f}%",
                         Paragraph(f'<font color="{VERDICT_COLORS[p["verdict"]]}"><b>{p["verdict"]}</b></font>',
                                   CELL)])
        story.append(_table(rows, [30*mm, 20*mm, 22*mm, 18*mm, 52*mm, 34*mm, 26*mm, 22*mm, W - 224*mm]))
    outlook = dec.get("outlook", []) + dec.get("trend_alerts", [])
    if outlook:
        story += [Paragraph("Outlook as models evolve", H2)] + _bullets(outlook)

    # ---- Headline ------------------------------------------------------------------------------
    story.append(Paragraph("Headline", H2))
    if an.get("headline"):
        story.append(Paragraph(escape(an["headline"]), BODY))
    rows = [["Model", "Config", "Checks passed", "Pass rate", "Cases fully passed", "Total tokens",
             "Total seconds"]]
    confs = [c for c in (primary, baseline) if c]
    label = {"with_skill": "With skill", "without_skill": "Without skill", "new_skill": "New version",
             "old_skill": "Previous version"}
    for m in models:
        s = {c: config_summary(mx, m, c) for c in confs}
        for c in confs:
            x = s[c]
            rows.append([mlabels[m], label.get(c, c), f"{_fmt_checks(x['checks_passed'], runs)} / {x['checks_total']}",
                         f"{x['pass_rate']:.0%}", f"{x['cases_fully_passed']} / {x['cases_total']}",
                         f"{x['tokens']:,.0f}", f"{x['seconds']:,.1f}"])
        if baseline:
            a, b = s[primary], s[baseline]
            rows.append([Paragraph(f"<b>{mlabels[m]}</b>", CELL), Paragraph("<b>Uplift</b>", CELL),
                         Paragraph(f"<b>{a['checks_passed'] - b['checks_passed']:+.{0 if runs <= 1 else 1}f}</b>", CELL),
                         Paragraph(f"<b>{(a['pass_rate'] - b['pass_rate']) * 100:+.0f} pts</b>", CELL),
                         Paragraph(f"<b>{a['cases_fully_passed'] - b['cases_fully_passed']:+d}</b>", CELL),
                         f"{(a['tokens'] / b['tokens'] - 1) * 100 if b['tokens'] else 0:+.0f}% tokens",
                         f"{(a['seconds'] / b['seconds'] - 1) * 100 if b['seconds'] else 0:+.0f}% time"])
    story.append(_table(rows, [34*mm, 30*mm, 32*mm, 26*mm, 34*mm, 34*mm, W - 190*mm]))

    if an.get("made_difference"):
        story += [Paragraph("Where the skill made the difference", H2)] + _bullets(an["made_difference"])
    if an.get("does_not_help"):
        story += [Paragraph("Where it does not help", H2)] + _bullets(an["does_not_help"])

    # ---- Obsolescence detail -------------------------------------------------------------------
    if dec.get("models"):
        story.append(PageBreak())
        story.append(Paragraph("What still needs the skill, and what models now do unaided", H2))
        story.append(Paragraph("A check counts for a model when it passes in at least half the runs. "
                               "<b>Only with skill</b> is the value the skill still adds; <b>unaided</b> means "
                               "the model passes it without the skill, so that guidance can be trimmed.", BODY))
        rows = [["Case", "Check", "Category"] + [f"{mlabels[m]}" for m in models]]
        status_of = {}
        for p in dec["models"]:
            for kind, items in (("Only with skill", p["unique"]), ("Unaided", p["absorbed"]),
                                ("Worse with skill", p["harmful_checks"]), ("Fails both", p["both_fail"])):
                for it in items:
                    status_of[(it["case"], it["check"], p["model"])] = (kind, it["category"])
        keys = sorted({(k[0], k[1]) for k in status_of}, key=lambda k: (str(k[0]), k[1]))
        colour = {"Only with skill": PASS, "Unaided": MUTED, "Worse with skill": FAIL, "Fails both": FAIL}
        order = {"Only with skill": 0, "Worse with skill": 1, "Fails both": 2, "Unaided": 3}
        keys.sort(key=lambda k: (min(order[status_of.get((k[0], k[1], m), ("Unaided", ""))[0]] for m in models),
                                 str(k[0])))
        for case_id, check in keys:
            cat = next((status_of[(case_id, check, m)][1] for m in models if (case_id, check, m) in status_of), "")
            cells = []
            for m in models:
                kind = status_of.get((case_id, check, m), ("-", ""))[0]
                col = colour.get(kind, MUTED)
                cells.append(Paragraph(f'<font color="{col.hexval()}">{kind}</font>', CELL))
            rows.append([str(case_id), check, cat.replace("_", " ")] + cells)
        story.append(_table(rows, [16*mm, W - 46*mm - 32*mm * len(models), 30*mm] + [32*mm] * len(models),
                            zebra=True))
        trend = dec.get("trend") or {}
        if any(trend.values()):
            story.append(Paragraph("Trend across approved evaluations", H2))
            rows = [["Model", "Date", "Skill version", "With skill", "Without skill", "Uplift"]]
            for m, pts in trend.items():
                for t in pts:
                    rows.append([model_label(m), t["date"], (t.get("skill_version") or "")[:8],
                                 f"{t['with']:.0%}" if t.get("with") is not None else "-",
                                 f"{t['without']:.0%}" if t.get("without") is not None else "-",
                                 f"{t['uplift_pts']:+.0f} pts" if t.get("uplift_pts") is not None else "-"])
            for p in dec["models"]:
                rows.append([Paragraph(f"<b>{mlabels[p['model']]}</b>", CELL), Paragraph("<b>this run</b>", CELL),
                             ctx["version"][:8], f"{p['with']['pass_rate']:.0%}", f"{p['without']['pass_rate']:.0%}",
                             Paragraph(f"<b>{p['uplift_pts']:+.0f} pts</b>", CELL)])
            story.append(_table(rows, [40*mm, 30*mm, 30*mm, 30*mm, 30*mm, W - 160*mm]))

    # ---- Side by side per case -----------------------------------------------------------------
    story.append(PageBreak())
    story.append(Paragraph("Side by side, per case", H2))
    head = ["Case", "Description", "Checks"]
    for m in models:
        head += [f"{mlabels[m]} with"] + ([f"{mlabels[m]} without", f"{mlabels[m]} uplift"] if baseline else [])
    rows = [head]
    for c in mx["cases"]:
        row = [str(c["id"]), c["description"], str(len(c["checks"]))]
        for m in models:
            a, tot = case_score(mx, m, primary, c["id"])
            row.append(f"{_fmt_checks(a, runs)}/{tot}")
            if baseline:
                b, _ = case_score(mx, m, baseline, c["id"])
                d = a - b
                col = PASS if d > 0.05 else FAIL if d < -0.05 else MUTED
                row += [f"{_fmt_checks(b, runs)}/{tot}",
                        Paragraph(f'<font color="{col.hexval()}"><b>{d:+.{0 if runs <= 1 else 1}f}</b></font>', CELL)]
        rows.append(row)
    per = (W - 16*mm - 70*mm - 14*mm) / (len(head) - 3)
    story.append(_table(rows, [16*mm, 70*mm, 14*mm] + [per] * (len(head) - 3), zebra=True))

    # ---- Tokens and time -----------------------------------------------------------------------
    story.append(Paragraph("Tokens and time per case", H2))
    head = ["Case"] + [f"{mlabels[m]} tokens" + (" with / without" if baseline else "") for m in models] + \
           [f"{mlabels[m]} seconds" + (" with / without" if baseline else "") for m in models]
    rows = [head]
    for c in mx["cases"]:
        tk, sc = [], []
        for m in models:
            ta, sa = case_cost(mx, m, primary, c["id"])
            if baseline:
                tb, sb = case_cost(mx, m, baseline, c["id"])
                tk.append(f"{ta:,.0f} / {tb:,.0f}")
                sc.append(f"{sa:.1f} / {sb:.1f}")
            else:
                tk.append(f"{ta:,.0f}")
                sc.append(f"{sa:.1f}")
        rows.append([str(c["id"])] + tk + sc)
    per = (W - 16*mm) / (len(head) - 1)
    story.append(_table(rows, [16*mm] + [per] * (len(head) - 1), zebra=True))

    # ---- What differed -------------------------------------------------------------------------
    if an.get("case_notes"):
        story.append(Paragraph("What differed in each case", H2))
        rows = [["Case", "Description", "Notes"]]
        for c in mx["cases"]:
            rows.append([str(c["id"]), c["description"], an["case_notes"].get(str(c["id"]), "")])
        story.append(_table(rows, [16*mm, 60*mm, W - 76*mm], zebra=True))

    # ---- Check by check ------------------------------------------------------------------------
    story.append(PageBreak())
    story.append(Paragraph("Check-by-check results", H2))
    story.append(Paragraph(("PASS/FAIL for each check" if runs <= 1 else
                            f"Runs passed out of {runs} for each check") + " on every model. Category marks "
                           "where the value comes from (asset, org knowledge, safety, behavior, quality).", BODY))
    head = ["Case", "Check", "Category"]
    for m in models:
        head += [f"{mlabels[m]} with"] + ([f"{mlabels[m]} without"] if baseline else [])
    rows = [head]
    cats = an.get("check_categories", {})
    for c in mx["cases"]:
        for i, chk in enumerate(c["checks"]):
            cat = chk.get("category") or cats.get(f"{c['id']}|{chk['text']}") or ""
            row = [str(c["id"]), chk["text"] + (" [script]" if chk.get("deterministic") else ""),
                   cat.replace("_", " ")]
            for m in models:
                row.append(_passfail(check_rate(mx, m, primary, c["id"], i), runs))
                if baseline:
                    row.append(_passfail(check_rate(mx, m, baseline, c["id"], i), runs))
            rows.append(row)
    n_res = len(head) - 3
    story.append(_table(rows, [16*mm, W - 16*mm - 26*mm - 21*mm * n_res, 26*mm] + [21*mm] * n_res, zebra=True))

    # ---- Trigger evals -------------------------------------------------------------------------
    trig = ctx.get("trigger")
    if trig:
        s = trig["summary"]
        story.append(Paragraph("Description triggering", H2))
        story.append(Paragraph(
            f"{s['passed']} of {s['total']} trigger queries behaved correctly ({s['accuracy']:.0%}) on "
            f"{mlabels[models[0]]}. Recall on should-trigger queries "
            f"{'-' if s['recall'] is None else format(s['recall'], '.0%')}; false triggers on near-miss "
            f"queries {'-' if s['false_trigger_rate'] is None else format(s['false_trigger_rate'], '.0%')}.", BODY))
        misses = [r for r in trig["results"] if not r["pass"]]
        if misses:
            rows = [["Query", "Should trigger", "Trigger rate"]] + [
                [r["query"], "yes" if r["should_trigger"] else "no", f"{r['trigger_rate']:.0%}"] for r in misses]
            story.append(_table(rows, [W - 60*mm, 30*mm, 30*mm]))

    # ---- Recommendations -----------------------------------------------------------------------
    recs = list(an.get("fix_suggestions", []))
    for p in dec.get("models", []):
        if p["absorbed"] and p["verdict"] in ("KEEP, SLIM DOWN", "RETIRE CANDIDATE"):
            recs.append(f"{mlabels[p['model']]} passes {len(p['absorbed'])} checks unaided: remove or shorten "
                        "the SKILL.md guidance behind them to reduce the token overhead.")
    # Analyst notes about the eval set itself: keep the few that change what to do next.
    notes = ctx.get("notes", [])
    eval_notes = [n for n in notes if n.startswith(("Non-discriminating", "Assertion never"))]
    other = [n for n in notes if n not in eval_notes]
    recs += other
    if eval_notes:
        nd = sum(n.startswith("Non-discriminating") for n in eval_notes)
        nv = len(eval_notes) - nd
        recs.append(f"Tighten the eval set: {nd} check(s) pass with and without the skill on some model, so they "
                    f"cannot show value; {nv} check(s) never pass with the skill on some model - fix the skill "
                    "or the check. Details are in the check-by-check table and summary.md.")
    if recs:
        story += [Paragraph("Recommendations", H2)] + _bullets(dict.fromkeys(recs))

    # ---- Method and limits ---------------------------------------------------------------------
    story.append(PageBreak())
    story.append(Paragraph("Test cases and method", H2))
    rows = [["Case", "Type", "Prompt (short)"]]
    for c in mx["cases"]:
        rows.append([str(c["id"]), c["type"].replace("_", " "), re.sub(r"\s+", " ", c["prompt"])[:220]])
    story.append(_table(rows, [16*mm, 34*mm, W - 50*mm], zebra=True))
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        f"Each run used headless Claude Code (<font name='Courier'>claude -p</font>) in its own empty folder, "
        f"with the skill installed under .claude/skills/ for the skill configuration and absent (or the "
        f"previous version) for the baseline. Script checks ran deterministically; other checks were graded "
        f"by {mlabels.get(ctx['grader_model'], model_label(ctx['grader_model']))} using skill-creator's "
        f"grader. Narrative sections were written by the analyst pass; all numbers, the verdict and the "
        f"tables are computed from the graded runs. Total model cost ${ctx['cost_usd']:.2f}.", BODY))
    story.append(Paragraph("Limits of this evaluation", H2))
    story += _bullets(ctx.get("limits", []) + an.get("extra_limits", []))

    doc = SimpleDocTemplate(str(path), pagesize=landscape(A4), leftMargin=14*mm, rightMargin=14*mm,
                            topMargin=12*mm, bottomMargin=15*mm, title=f"{ctx['skill']} evaluation",
                            author="skilleval")
    label = f"{ctx['skill']} - evaluation report - {ctx['date']}"
    doc.build(story, onFirstPage=lambda c, d: _footer(c, d, label),
              onLaterPages=lambda c, d: _footer(c, d, label))


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")
