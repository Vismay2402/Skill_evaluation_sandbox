---
name: pii-redactor
description: Redacts personal data from text, CSV or log files before they are shared, following the org's data-handling policy - emails, phone numbers, Indian government IDs (Aadhaar, PAN), card numbers and customer names - and writes a redaction log with counts only. Use this whenever someone asks to anonymise, mask, scrub, sanitise or remove PII/personal data from a file or text, or to make data safe to share with a vendor or a model. Not for finding or extracting personal data, and not for encrypting files.
---

# PII redactor (data-handling policy DH-4)

Produce two files in the outputs folder:

1. `<original name>.redacted.<ext>` - same structure as the input (same columns, rows, line breaks).
2. `redaction_log.json` - `{"file": ..., "counts": {"EMAIL": n, "PHONE": n, "GOV_ID": n, "CARD": n, "NAME": n}, "policy": "DH-4"}`.
   **The log must never contain the original values** - counts only.

## Replacement tokens (exact)

| Data | Token | Notes |
|---|---|---|
| Email address | `[EMAIL]` | |
| Phone number (any format, incl. +91) | `[PHONE]` | |
| Aadhaar (12 digits, often 4-4-4) or PAN (`AAAAA9999A`) | `[GOV_ID]` | |
| Payment card number (13-19 digits) | `[CARD]` | |
| Customer / person names | `[NAME]` | Employee IDs like `EMP-1234` and company names are **kept** |

## Rules

- Redact in free text too (comments, notes columns), not just obvious columns.
- Keep non-personal values (order ids, amounts, cities, dates) unchanged so the data stays useful.
- If asked to "keep the last 4 digits" of something, refuse for GOV_ID and keep only for CARD (`[CARD-1234]`).
- Never print the original values in the reply either.
