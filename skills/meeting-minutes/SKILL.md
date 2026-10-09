---
name: meeting-minutes
description: Turns a meeting transcript or rough notes into minutes.md in the team's standard format - attendees, decisions, action items with a named owner and an absolute due date, and open questions. Use this whenever someone asks for minutes, a meeting summary, MoM, action items or follow-ups from a call, transcript or notes. Not for drafting agendas, emails to attendees or summarising documents that are not meetings.
---

# Meeting minutes (team format)

Write `minutes.md` to the outputs folder:

```
# <Meeting title> - <YYYY-MM-DD>
**Attendees:** name (role), ...   **Absent:** ...

## Decisions
1. <decision, one line, past tense>

## Action items
| # | Action | Owner | Due |
|---|---|---|---|

## Open questions
- <question> - <who will answer>
```

## Rules

1. **Decisions are only things that were agreed.** Discussion that did not end in agreement goes to Open
   questions, never to Decisions.
2. **Every action item has exactly one owner** (a person, not "team") and a **due date as YYYY-MM-DD**.
   Convert relative dates ("next Friday", "end of month") using the meeting date. If nobody took it, the
   owner is `UNASSIGNED` and it is also listed under Open questions.
3. No verbatim quotes and no side chatter (jokes, small talk, technical difficulties).
4. If a client or customer is named, add the line `Confidential - internal distribution only` under the title.
5. Keep it under one page: max 8 decisions, 12 action items.
