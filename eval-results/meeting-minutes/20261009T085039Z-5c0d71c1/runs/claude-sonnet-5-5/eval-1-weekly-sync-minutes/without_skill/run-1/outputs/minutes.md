# Data Platform Weekly Sync – Minutes

**Date:** Tuesday 6 October 2026
**Present:** Anita (Engineering Manager), Rahul (Data Engineer), Meera (Analyst), Tom (Victaulic, Product Owner)
**Apologies:** Sam

## 1. Snowflake cost spike
- Cause: the new dashboard was refreshing every 5 minutes.
- Tom confirmed an hourly refresh is fine for Victaulic.
- **Decision:** dashboard moves to an hourly refresh from now on.

## 2. dbt upgrade
- Meera recommends moving to dbt 1.9 but is unsure whether the existing macros will work. They need to be checked first.
- **Decision:** none today. The upgrade is deferred until the macro check is done.

## 3. Churn report in Power BI (in addition to Tableau)
- Tom asked for the churn report to be available in Power BI as well as Tableau.
- Anita is unsure there is capacity; no decision made.
- Meera will produce an effort estimate to inform the decision.

## 4. Retiring the old S3 bucket
- Retirement was agreed last month; reconfirmed in this meeting.
- **Decision:** the old S3 bucket is retired on the 31st (October, assuming the month from context). Rahul had no objection.

## Actions

| # | Action | Owner | Due |
|---|--------|-------|-----|
| 1 | Change the dashboard refresh from 5 minutes to hourly | Rahul | Fri 9 Oct 2026 |
| 2 | Write up the Power BI effort estimate for the churn report | Meera | End of next week (Fri 16 Oct 2026) |
| 3 | Check the dbt macros for compatibility with dbt 1.9 | **Unassigned** | TBC |
| 4 | Retire the old S3 bucket | Not named (Rahul presumably) | 31 Oct 2026 |

## Open items
- No one was assigned the dbt macro check, which blocks the dbt 1.9 decision. Needs an owner.
- Power BI request is undecided pending Meera's estimate.
