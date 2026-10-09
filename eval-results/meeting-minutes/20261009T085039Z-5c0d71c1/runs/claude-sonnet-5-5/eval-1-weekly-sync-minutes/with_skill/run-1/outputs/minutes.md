# Data platform weekly sync - 2026-10-06
Confidential - internal distribution only

**Attendees:** Anita (eng manager), Rahul (data engineer), Meera (analyst), Tom (Victaulic, product owner)   **Absent:** Sam

## Decisions
1. The new dashboard refresh moves from every 5 minutes to hourly, to reduce the Snowflake cost spike.
2. The old S3 bucket will be retired on 2026-10-31 (previously agreed, reconfirmed).

## Action items
| # | Action | Owner | Due |
|---|---|---|---|
| 1 | Change the dashboard refresh to hourly | Rahul | 2026-10-09 |
| 2 | Write up the Power BI effort estimate for the churn report | Meera | 2026-10-16 |
| 3 | Check the existing dbt macros for compatibility with dbt 1.9 | UNASSIGNED | TBD |

## Open questions
- Upgrade to dbt 1.9? Not decided until the macro check is done - owner of the macro check needed (action 3).
- Who will check the dbt macros, and by when? - Anita
- Add the churn report to Power BI as well as Tableau? Capacity is uncertain - decide after Meera's estimate (Anita, Tom)
