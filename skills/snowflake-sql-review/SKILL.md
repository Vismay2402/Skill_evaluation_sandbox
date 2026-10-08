---
name: snowflake-sql-review
description: Reviews Snowflake SQL against Acme's data-platform rules and writes review.md with BLOCKER / MAJOR / MINOR findings, the rule ID for each, and a corrected query. Use this whenever someone asks to review, check, approve or sanity-check a Snowflake query, view, dbt model or migration script before it runs or merges. Not for writing new queries from scratch or explaining SQL concepts.
---

# Snowflake SQL review (Acme data platform)

Read `references/rules.md` - it is the source of truth. Then write `review.md` to the outputs folder:

```
# SQL review: <file or short title>
Verdict: APPROVE | CHANGES REQUESTED | BLOCKED

| # | Severity | Rule | Line | Finding | Fix |
|---|---|---|---|---|---|

## Corrected query
```sql
...
```
```

- Every finding cites a rule ID from `references/rules.md` (e.g. `SF-03`).
- Verdict is BLOCKED if there is any BLOCKER, CHANGES REQUESTED if any MAJOR, else APPROVE.
- Never offer to run, or say it is safe to run, a statement with a BLOCKER finding.
- The corrected query must fix every BLOCKER and MAJOR finding.
