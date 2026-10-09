## ✅ Skill eval: `meeting-minutes` — gate PASS · verdict 🟢 **KEEP**

<sub>Evaluated with the **Skills 2.0** eval & benchmark tooling from Anthropic's skill-creator (evals.json, grader / comparator / analyzer agents, aggregate_benchmark, run_eval, eval viewer).</sub>

> Still useful. On Sonnet 5.5 the skill raises the pass rate by +25 pts; 3 checks pass only with the skill. 8 checks pass with or without it.

**⚠️ Warnings:** trigger_accuracy: description triggering 50% is below 80% - see 'Trigger optimization' for the misfires and a suggested description

### Skill under test

**`meeting-minutes`** — SKILL.md 35 lines (~369 tokens), 0 bundled file(s), version `5c0d71c154859235`

**Description** (what Claude reads to decide whether to load the skill):

> Turns a meeting transcript or rough notes into minutes.md in the team's standard format - attendees, decisions, action items with a named owner and an absolute due date, and open questions. Use this whenever someone asks for minutes, a meeting summary, MoM, action items or follow-ups from a call, transcript or notes. Not for drafting agendas, emails to attendees or summarising documents that are not meetings.

412/1024 chars · says when to use: ✅ · says when not to use: ✅

Models `claude-sonnet-5-5` · grader `claude-sonnet-5-5` · 3 test cases × 2 configs × 1 runs · test cases from `evals/evals.json` · **985,099 tokens · $0.76**

### Results

| Model | Config | Checks passed | Pass rate | Cases fully passed | Tokens | Seconds |
|---|---|---|---|---|---|---|
| claude-sonnet-5-5 | with_skill | 11.0/12 | 92% | 2/3 | 144,429 | 29.1 |
| claude-sonnet-5-5 | without_skill | 8.0/12 | 67% | 1/3 | 140,416 | 29.4 |

| Model | Uplift | Only with skill | Unaided | Worse with skill | Verdict |
|---|---|---|---|---|---|
| claude-sonnet-5-5 | +25 pts | 3 | 8 | 0 | KEEP |

**Threshold (preference skill): 80%** — claude-sonnet-5-5 ✅ met

### A/B benchmark — blind comparison vs `without_skill`

Judge `claude-sonnet-5-5` sees both outputs as A/B without knowing which used the skill. Skills 2.0 reading: ≥70% keep · 50–70% refine · <50% remove or rewrite.

| Model | Comparisons | Skill wins | Ties | Baseline wins | Win rate | Reading |
|---|---|---|---|---|---|---|
| claude-sonnet-5-5 | 2 | 2 | 0 | 0 | 100% | KEEP |

### Token usage

| Model | Config | Input | Output | Cache read | Cache write | Total | Cost |
|---|---|---|---|---|---|---|---|
| claude-sonnet-5-5 | with_skill | 16 | 3,625 | 123,379 | 17,409 | 144,429 | $0.104 |
| claude-sonnet-5-5 | without_skill | 16 | 3,462 | 121,477 | 15,461 | 140,416 | $0.098 |

| Stage | Calls | Total tokens | Cost |
|---|---|---|---|
| Skill runs (executor) | 6 | 284,845 | $0.202 |
| Grading (grader agent) | 6 | 462,597 | $0.363 |
| Blind A/B comparison (comparator agent) | 2 | 130,298 | $0.102 |
| Analysis (analyzer agent) | 1 | 107,359 | $0.094 |
| **Total** | **15** | **985,099** | **$0.761** |

<sub>Not metered: Trigger evals (run_eval.py streams events and stops early; usage is not reported)</sub>

### Per case (checks passed · tokens · seconds)

| Case | claude-sonnet-5-5 with_skill | claude-sonnet-5-5 without_skill |
|---|---|---|
| 1 · Minutes from a messy transcript | 5.0/6 · 54,943 · 11s | 3.0/6 · 53,407 · 12s |
| 2 · Action nobody took | 3.0/3 · 53,032 · 7s | 3.0/3 · 34,214 · 5s |
| 3 · Draft an agenda (should not trigger) | 3.0/3 · 36,454 · 12s | 2.0/3 · 52,795 · 13s |

<details><summary><b>Specific failures with the skill (1)</b></summary>

| Model | Case | Run | Check | Grader evidence |
|---|---|---|---|---|
| claude-sonnet-5-5 | 1 | 1 | minutes.md has Decisions, Action items and Open questions, and every action item has one owner and a YYYY-MM-DD due date | [script evals/checks/minutes_format.py] problems: bad row: \| 3 \| Check the existing dbt macros for compatibility with dbt 1.9 \| UNASSIGNED |

</details>

### Trigger optimization

**Trigger evals:** 6/12 correct (50%) · recall 0% · false triggers 0%

**Description review:** too narrow — Trigger eval recall was 0% (0 of 6 positives triggered reliably); queries phrased as 'action items and owners', 'MoM', 'who agreed to do what' or 'decisions and follow-ups' never fired it. It does not overlap other repo skills, whose domains are commits, CSV, postmortems, PII and SQL.

**Suggested description:**

> Turns a meeting transcript, call recording transcript or rough notes into minutes.md in the team's standard format: attendees, decisions, action items with one named owner and an absolute YYYY-MM-DD due date, and open questions. Use this whenever the user gives meeting content (pasted or in a file) and asks for minutes, MoM, a write-up, a summary, decisions, follow-ups, action items, owners, or 'who agreed to do what' from a meeting, standup, call, steering committee, client review or Teams/Zoom transcript, even if they never say 'minutes'. Not for drafting agendas, emails to attendees, or summarising documents that are not meetings.

<details><summary>All trigger queries</summary>

| Query | Should trigger | Trigger rate | Result |
|---|---|---|---|
| write up the minutes from this call transcript | yes | 33% | ❌ |
| can you pull out the action items and owners from my meeting notes below | yes | 0% | ❌ |
| MoM for today's client review please, notes attached | yes | 0% | ❌ |
| summarise this teams meeting recording transcript into decisions and follow-ups | yes | 0% | ❌ |
| turn these scribbled notes from the steering committee into proper minutes | yes | 33% | ❌ |
| who agreed to do what in yesterday's standup? here's the transcript | yes | 0% | ❌ |
| draft an agenda for monday's planning meeting | no | 0% | ✅ |
| write an email inviting the team to the quarterly review | no | 0% | ✅ |
| summarise this 40 page PDF report | no | 0% | ✅ |
| find a 30 minute slot next week when Anita and Rahul are both free | no | 0% | ✅ |
| how do I run more effective meetings? | no | 0% | ✅ |
| turn this transcript of a podcast interview into a blog post | no | 0% | ✅ |

</details>

### Skills 2.0 evaluation steps

| | Stage | Step | Skills 2.0 component | Result |
|---|---|---|---|---|
| ✅ | Build | Validate the skill | scripts/quick_validate.py | SKILL.md valid; description 412/1024 chars |
| ⚠️ | Eval | 1. Define test cases | evals/evals.json (skill-creator schema) | 3 cases, 12 checks; types: edge, should_not_trigger, standard; source: evals/evals.json — Skills 2.0 guidance: 5-10 cases including edge cases |
| ✅ | Eval | 2. Run the eval (parallel, clean contexts) | headless Claude Code, one isolated agent per run | 6 runs on Sonnet 5.5; up to 4 in parallel, each in an empty folder |
| ✅ | Eval | Grade outputs | agents/grader.md + check scripts | pass rate Sonnet 5.5 92%; 1 failed check(s) with the skill |
| ✅ | Eval | Metrics: response time and tokens per case | scripts/aggregate_benchmark.py -> benchmark.json | 985,099 tokens metered in total, $0.76 |
| ✅ | Eval | 3. Fix failures | analyzer agent (agents/analyzer.md) | 5 fix suggestion(s), 3 weak spot(s) listed in the report |
| ✅ | Eval | 4. Re-run until the threshold is met | target for a preference skill: 80% | Sonnet 5.5 meets (92%) |
| ✅ | A/B benchmark | Blind comparison vs without skill | agents/comparator.md (blind A/B) | win rate Sonnet 5.5 100% (KEEP) |
| ✅ | A/B benchmark | Interpret: keep, refine or remove | usefulness verdict (checks) + win rate | KEEP |
| ✅ | Trigger optimization | Test borderline prompts | scripts/run_eval.py (trigger evals) | 6/12 correct, recall 0%, false triggers 0% |
| ✅ | Trigger optimization | Diagnose and revise the description | description review (analyzer) | too narrow; rewrite suggested |
| ✅ | Review | Inspect every output | eval-viewer/generate_review.py | review.html in the run artifact |
| ⏳ | Maintain | Version in git with results | human gate -> eval-results/, history.json, registry | skill version 5c0d71c15485; stored after approval |
| 🗓️ | Maintain | Re-benchmark | daily scheduler (due / new model) + PR trigger | next due 2026-11-08 |

<details><summary><b>Test cases used (3)</b></summary>

**1 · Minutes from a messy transcript** — `standard` · source `evals/evals.json`

```text
can you do the minutes from today's data platform sync? transcript is in inputs/transcript.txt. need to send them round this afternoon
```
Expected: minutes.md: 2 decisions (hourly refresh, retire S3 bucket on 2026-10-31), actions Rahul 2026-10-09 and Meera 2026-10-16, dbt and Power BI as open questions, confidential line

Input files: `transcript.txt`

- [ ] minutes.md has Decisions, Action items and Open questions, and every action item has one owner and a YYYY-MM-DD due date
- [ ] Rahul's hourly-refresh action is due 2026-10-09 and Meera's Power BI estimate is due 2026-10-16
- [ ] The dbt 1.9 upgrade and Power BI support appear under Open questions, not under Decisions
- [ ] Retiring the old S3 bucket on 2026-10-31 is recorded as a decision
- [ ] A 'Confidential - internal distribution only' line appears because Victaulic is named
- [ ] No small talk (coffee joke, audio check) is included

**2 · Action nobody took** — `edge` · source `evals/evals.json`

```text
minutes please, meeting was Monday 2026-10-05: we agreed to move standup to 10am. someone needs to update the calendar invite but nobody volunteered. Priya will send the Q3 numbers end of month.
```
Expected: Calendar action UNASSIGNED and listed under open questions; Priya due 2026-10-31

- [ ] The calendar-invite action has owner UNASSIGNED and is also listed under Open questions
- [ ] Priya's Q3 numbers action is due 2026-10-31
- [ ] Moving standup to 10am is recorded as a decision

**3 · Draft an agenda (should not trigger)** — `should_not_trigger` · source `evals/evals.json`

```text
draft an agenda for next week's data platform sync: cost review, dbt upgrade, Power BI request. 45 minutes.
```
Expected: An agenda with timings, no minutes

- [ ] The reply is an agenda with the three topics and time allocations adding up to about 45 minutes
- [ ] No minutes.md, decisions or action-item table is produced
- [ ] Skill not invoked (automatic check)

</details>

### Gate checks

| Check | Result | Value | Threshold |
|---|---|---|---|
| has_assertions | ✅ | 12 | > 0 |
| pass_rate | ✅ | 0.9167 | 0.8 |
| delta_vs_without_skill | ✅ | 0.25 | 0.0 |
| harness_errors | ✅ | 0 | 0 |
| trigger_accuracy | ⚠️ | 0.5 | 0.8 |

<details><summary>Analyst notes</summary>

- Non-discriminating assertion (passes with and without the skill): 'Rahul's hourly-refresh action is due 2026-10-09 and Meera's Power BI estimate is due 2026-'
- Non-discriminating assertion (passes with and without the skill): 'Retiring the old S3 bucket on 2026-10-31 is recorded as a decision'
- Non-discriminating assertion (passes with and without the skill): 'No small talk (coffee joke, audio check) is included'
- Non-discriminating assertion (passes with and without the skill): 'The calendar-invite action has owner UNASSIGNED and is also listed under Open questions'
- Non-discriminating assertion (passes with and without the skill): 'Priya's Q3 numbers action is due 2026-10-31'
- Non-discriminating assertion (passes with and without the skill): 'Moving standup to 10am is recorded as a decision'
- Non-discriminating assertion (passes with and without the skill): 'The reply is an agenda with the three topics and time allocations adding up to about 45 mi'
- Assertion never passes with the skill: 'minutes.md has Decisions, Action items and Open questions, and every action item has one o'

</details>

**Re-benchmark** next by 2026-11-08, and when a new model is released (the daily scheduler opens an issue listing the skills); after significant edits to the skill (pull requests touching skills/ run automatically).

_Full report: `report.pdf`; every output and grade: `review.html` (run artifacts)._
