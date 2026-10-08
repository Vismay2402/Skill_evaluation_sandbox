---
name: incident-postmortem
description: Writes a blameless incident postmortem (postmortem.md) in Acme's house format from chat logs, alerts or notes - severity per Acme's SEV scale, UTC timeline table, root cause, contributing factors and OPS action items. Use this whenever someone asks for a postmortem, incident review, RCA or incident write-up after an outage or incident has ended. Not for live status-page updates or customer emails during an ongoing incident.
---

# Incident postmortem (Acme house format)

Produce `postmortem.md` in the outputs folder, following `assets/postmortem-template.md` exactly.

## Severity (Acme SEV scale - always state which rule applied)

| SEV | Rule |
|---|---|
| SEV1 | Customer-facing outage of any duration that loses or corrupts customer data, OR full outage longer than 60 minutes |
| SEV2 | Customer-facing outage or major degradation (error rate above 5%) lasting 15-60 minutes |
| SEV3 | Customer-facing degradation under 15 minutes, or an internal-only outage |
| SEV4 | Near miss: caught before customers were affected |

## Rules

1. **Blameless.** Never name individuals in Root cause, Contributing factors or Action items. Use roles
   ("the on-call engineer", "the release manager"). Names may appear only in the timeline's "Who" column.
2. **UTC.** Convert every timestamp to UTC (`YYYY-MM-DD HH:MM UTC`). Chat exports from the India office
   are IST (UTC+05:30).
3. **Timeline** is a markdown table: `| Time (UTC) | Event | Who |`, oldest first, and must include
   detection, escalation, mitigation and resolution.
4. **Impact** states duration in minutes (first customer impact to resolution) and the affected service.
5. **Action items** table: `| Action | Owner (role) | Due | Ticket |`. Ticket is `OPS-TBD` until filed.
   Every action item must be preventive, detective or mitigating - say which in the Action column.
6. **Unknowns:** if the input doesn't say something (cause, duration), write `TBD - <question to ask>`
   rather than guessing.
7. End with a one-line `Status: Draft - needs review by incident commander`.
