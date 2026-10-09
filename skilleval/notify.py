"""Email notifications for the human gate.

Sends reviewers an email when evaluation results are waiting for approval (with each skill's report.pdf
attached) and when results have been approved and stored. Uses plain SMTP, so it works with Office 365,
Gmail (app password), SendGrid, Amazon SES SMTP, etc.

Secrets / env:  SMTP_HOST, SMTP_PORT (587 STARTTLS, 465 SSL), SMTP_USERNAME, SMTP_PASSWORD, SMTP_FROM
Recipients:     workflow input reviewer_email, else review.notify_emails in skilleval.config.yaml
"""
from __future__ import annotations

import json
import os
import smtplib
import ssl
from email.message import EmailMessage
from html import escape
from pathlib import Path


def recipients(cli_value: str | None, cfg: dict) -> list[str]:
    raw = cli_value or ",".join(cfg.get("review", {}).get("notify_emails", []) or [])
    return [e.strip() for e in raw.replace(";", ",").split(",") if "@" in e]


def _skill_rows(ws: Path) -> list[dict]:
    status = json.loads((ws / "status.json").read_text())
    rows = []
    for s in status["evaluated"]:
        run = json.loads((ws / s / "run.json").read_text())
        gate = json.loads((ws / s / "gate.json").read_text())
        dec = json.loads((ws / s / "verdict.json").read_text()) if (ws / s / "verdict.json").exists() else {}
        rows.append({"skill": s, "verdict": run.get("verdict"), "gate": gate["status"],
                     "pass_rate": run.get("pass_rate"), "models": ", ".join(run.get("models", [])),
                     "uplift": ", ".join(f"{p['uplift_pts']:+.0f} pts" for p in dec.get("models", [])) or "-",
                     "win_rate": ", ".join(f"{v:.0%}" for v in (run.get("win_rate_by_model") or {}).values()
                                           if v is not None) or "-",
                     "tokens": run.get("tokens_total", 0), "cost": run.get("cost_usd", 0),
                     "failed": gate.get("failed", []), "reason": dec.get("reason", ""),
                     "report": ws / s / "report.pdf"})
    return rows


def build_review_email(ws: Path, run_url: str, repo: str, kind: str = "pending", pr_url: str = "",
                       approver: str = "") -> tuple[str, str, str, list[Path]]:
    rows = _skill_rows(ws)
    names = ", ".join(r["skill"] for r in rows)
    if kind == "pending":
        subject = f"[Skill eval] Approval needed: {names}"
        intro = ("Evaluation results are waiting for your approval. Open the run, read the summary and the "
                 "attached report, then click <b>Review deployments</b> &rarr; <b>skill-eval-approval</b> &rarr; "
                 "<b>Approve and deploy</b> (or <b>Reject</b>). Your comment is stored with the results.")
        button = "Review and approve on GitHub"
    else:
        subject = f"[Skill eval] Approved by {approver}: {names}"
        intro = (f"<b>{escape(approver)}</b> approved these results. They are stored in git under "
                 "<code>eval-results/</code>" + (f" in <a href='{pr_url}'>this pull request</a>" if pr_url else "") + ".")
        button = "Open the run"
    trs = "".join(
        f"<tr><td><b>{escape(r['skill'])}</b></td><td>{escape(str(r['verdict']))}</td>"
        f"<td>{'PASS' if r['gate'] == 'pass' else 'FAIL'}</td><td>{(r['pass_rate'] or 0):.0%}</td>"
        f"<td>{escape(r['uplift'])}</td><td>{escape(r['win_rate'])}</td><td>{r['tokens']:,}</td>"
        f"<td>${r['cost']:.2f}</td></tr>"
        + (f"<tr><td></td><td colspan='7' style='color:#CF222E'>Gate failed: {escape('; '.join(r['failed']))}</td></tr>"
           if r["failed"] else "")
        for r in rows)
    html = f"""<div style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#1F2328">
<p>{intro}</p>
<p><a href="{run_url}" style="background:#1A7F37;color:#fff;padding:8px 14px;border-radius:6px;text-decoration:none">{button}</a></p>
<table cellpadding="6" style="border-collapse:collapse;border:1px solid #D0D7DE">
<tr style="background:#F3F4F6"><th align=left>Skill</th><th align=left>Verdict</th><th align=left>Gate</th>
<th align=left>Pass rate</th><th align=left>Uplift</th><th align=left>A/B win rate</th><th align=left>Tokens</th><th align=left>Cost</th></tr>
{trs}</table>
<p>{''.join(f'<p><b>{escape(r["skill"])}:</b> {escape(r["reason"])}</p>' for r in rows)}</p>
<p style="color:#59636E">Repository {escape(repo)} &middot; evaluated with Skills 2.0 (skill-creator) tooling.
Only GitHub users listed as required reviewers of the <i>skill-eval-approval</i> environment can approve.</p></div>"""
    text = (f"{subject}\n\n{run_url}\n\n" + "\n".join(
        f"- {r['skill']}: verdict {r['verdict']}, gate {r['gate']}, pass rate {(r['pass_rate'] or 0):.0%}" for r in rows))
    attachments = [r["report"] for r in rows if r["report"].exists()] if kind == "pending" else []
    return subject, html, text, attachments


def send(to: list[str], subject: str, html: str, text: str, attachments: list[Path] = ()) -> bool:
    host = os.environ.get("SMTP_HOST")
    if not host or not to:
        print("[skilleval] email not sent: " + ("no SMTP_HOST secret" if not host else "no recipients"))
        return False
    port = int(os.environ.get("SMTP_PORT") or 587)
    msg = EmailMessage()
    msg["Subject"], msg["To"] = subject, ", ".join(to)
    msg["From"] = os.environ.get("SMTP_FROM") or os.environ.get("SMTP_USERNAME", "skill-eval@localhost")
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    for a in attachments:
        msg.add_attachment(a.read_bytes(), maintype="application", subtype="pdf",
                           filename=f"{a.parent.name}-report.pdf")
    ctx = ssl.create_default_context()
    if port == 465:
        server = smtplib.SMTP_SSL(host, port, context=ctx, timeout=30)
    else:
        server = smtplib.SMTP(host, port, timeout=30)
        server.starttls(context=ctx)
    with server:
        if os.environ.get("SMTP_USERNAME"):
            server.login(os.environ["SMTP_USERNAME"], os.environ.get("SMTP_PASSWORD", ""))
        server.send_message(msg)
    print(f"[skilleval] emailed {', '.join(to)}: {subject}")
    return True
