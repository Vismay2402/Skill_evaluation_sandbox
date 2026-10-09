# Data Platform Sync: Agenda

**Date/time:** [week of 12 Oct 2026, day/time TBC]
**Duration:** 45 minutes
**Attendees:** [add names]
**Facilitator / note-taker:** [TBC]

| # | Time | Topic | Lead | Outcome needed |
|---|------|-------|------|----------------|
| 1 | 0:00–0:05 (5 min) | Kick-off and review of last sync's actions | Facilitator | Confirm status of open items |
| 2 | 0:05–0:20 (15 min) | **Cost review** | [TBC] | Agree 1–3 cost actions with owners |
| 3 | 0:20–0:33 (13 min) | **dbt upgrade** | [TBC] | Go/no-go on approach and target date |
| 4 | 0:33–0:42 (9 min) | **Power BI request** | [TBC] | Accept, decline or defer; name an owner |
| 5 | 0:42–0:45 (3 min) | Wrap-up: recap decisions, actions, owners, due dates | Facilitator | Actions read back |

## 2. Cost review (15 min)
- Spend vs. budget and vs. last period (warehouse compute, storage, orchestration, BI licences)
- Biggest drivers and any anomalies or spikes
- Quick-win options (warehouse sizing, auto-suspend, query or model optimisation, retention)
- **Decision:** which optimisations to pursue, who owns them, and by when

## 3. dbt upgrade (13 min)
- Current version vs. target version; reason for upgrading (support end, features, fixes)
- Known breaking changes, deprecated packages and adapter compatibility
- Testing and rollout plan (dev → staging → prod), rollback plan
- Effort estimate and impact on other work
- **Decision:** go/no-go, target date, owner

## 4. Power BI request (9 min)
- Requester, business need and users affected
- Data sources: do the required models already exist in the platform, or is new modelling needed?
- Effort, licensing or capacity impact, and cost implications (links to item 2)
- **Decision:** accept, decline or defer; owner and rough timeline

## Pre-reads / prep
- Cost: latest cost dashboard or export, with a one-paragraph summary from the lead
- dbt: upgrade notes and a list of impacted models/packages
- Power BI: the request ticket or description

## Notes
- If a topic overruns, park it for a follow-up thread rather than cutting into the wrap-up.
- Time is weighted to cost review as the broadest topic. Adjust if the Power BI request is more urgent.
