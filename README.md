# Skill evaluation automation

Automated evaluation and benchmarking for Claude skills, built on the eval tooling that ships with
Anthropic's **skill-creator** (Skills 2.0): the same `evals.json` format, grader agent, benchmark
aggregation (`benchmark.json`, mean ± stddev, with/without-skill delta), trigger evals and review viewer -
run headless in CI, with a human approval gate before anything is stored in git.

Every run produces a **PDF evaluation report** and a **usefulness verdict** - is the skill still worth
having, or are newer models making it obsolete? - by running each test case with and without the skill
on several models and tracking that uplift across model releases.

```mermaid
flowchart LR
  A[PR touching skills/**<br/>Run workflow button<br/>scheduler: due / new model] --> B{evals.json<br/>present?}
  B -- no --> C[Claude drafts evals<br/>pushes to PR for review] --> A
  B -- yes --> D[Validate]
  D --> E[Run evals headless<br/>with_skill vs baseline × N runs]
  E --> F[Grade<br/>scripts + grader agent]
  F --> G[Benchmark + trigger evals<br/>+ analyst narrative]
  G --> V[Usefulness verdict<br/>+ report.pdf]
  V --> H{Automated gate}
  H -- fail --> X[Stop, report on PR]
  H -- pass --> I[[Human review<br/>Environment approval]]
  I -- reject --> Y[Nothing stored]
  I -- approve --> J[Commit results + registry<br/>to PR / results PR]
  J --> K[Skill approval check<br/>required to merge]
```

## What a run does

| Stage | What happens | Built on |
|---|---|---|
| Validate | Frontmatter, single SKILL.md, name/description limits, eval-set schema, input files exist | `scripts/quick_validate.py` |
| Execute | Each eval prompt runs in an isolated folder via `claude -p` on **every configured model**, once **with** the skill installed in `.claude/skills/` and once against a **baseline** (no skill for new skills, the previous version from the base branch for changed skills), N times each, in parallel | skill-creator run layout |
| Grade | Script expectations run deterministically; text expectations go to the skill-creator grader agent, which also critiques weak assertions | `agents/grader.md` |
| Benchmark | Pass rate, time and tokens per configuration with mean ± stddev and delta; whether the skill was actually loaded in each run | `scripts/aggregate_benchmark.py` |
| Trigger evals | Should-trigger / near-miss queries check that the description makes Claude load the skill | `scripts/run_eval.py` |
| Analyst | Claude reads the results and transcripts and writes the narrative: headline, where the skill helped, where it did not (with fixes), what differed per case, and a value category for each check. It never supplies numbers - those come from code | `agents/analyzer.md` |
| A/B benchmark | Skills 2.0 blind comparison: the comparator agent sees the with-skill and baseline outputs as A/B in random order and picks the better one -> win rate per model (>= 70% keep, 50-70% refine, < 50% remove or rewrite) | `agents/comparator.md` |
| Description review | Trigger optimization: is the description too broad, too narrow or overlapping another skill in the repo? Suggests a rewrite that says when to use the skill and when not to | `run_eval.py` results + analyzer |
| Token usage | Input / output / cache tokens and cost per model and configuration, and per stage (skill runs, grading, comparison, analysis) | `usage.json` |
| Verdict | Per model: uplift, checks only the skill gets right, checks the model now passes unaided, token overhead, trend vs past approved evaluations -> KEEP / KEEP, SLIM DOWN / RETIRE CANDIDATE / HARMFUL | see below |
| Gate | Thresholds from `skilleval.config.yaml` (pass rate, delta vs baseline, regression vs last approved, trigger accuracy, harness errors) | |
| Report | `report.pdf` (the evaluation report), `SUMMARY.md` (PR comment + job summary) and `review.html` - the skill-creator viewer with every prompt, output file and grade | `eval-viewer/generate_review.py` |
| Human gate | The `human-review` job waits on the `skill-eval-approval` environment; a named reviewer approves or rejects | GitHub Environments |
| Store | Approved results and the report go to `eval-results/<skill>/<timestamp>-<version>/`; `latest.json`, `history.json` (per-model uplift over time) and `registry/skills-registry.json` are updated, committed with the approver's name | |

## The report

`report.pdf` follows the structure of a hand-written skill eval report, generated for every run. The same
content (minus the long tables) is the GitHub job summary and PR comment.

0. **Skill under test** - name, the full **description** (what Claude reads to decide whether to load it),
   length vs the 1,024-character limit, whether it says when to use / not use the skill, SKILL.md size and
   bundled files; and a **"Evaluated with Skills 2.0"** banner
1. **Verdict** - KEEP / KEEP, SLIM DOWN / RETIRE CANDIDATE / HARMFUL, the reason, and a per-model table
2. **Outlook as models evolve** - is uplift shrinking on more capable models, or versus past evaluations?
3. **Headline** - checks passed, pass rate, cases fully passed, tokens and time per model and configuration, with uplift rows
4. **Where the skill made the difference / where it does not help** (with concrete fixes)
5. **What still needs the skill, and what models now do unaided** - every check classified per model
6. **Trend across approved evaluations** (once there is history)
7. **A/B benchmark (blind comparison)** - win rate per model with the Skills 2.0 reading
8. **Skills 2.0 evaluation steps, as run** - every step from the Skills 2.0 guide (validate, define test
   cases, run in parallel with clean contexts, grade, metrics, fix failures, re-run to the threshold, A/B
   benchmark, interpret, trigger tests, description revision, review, version in git, re-benchmark) with the
   skill-creator component behind it and its result
9. **Side by side per case**, **tokens and time per case**, **token usage** (input/output/cache/cost by
   model, configuration and stage), **what differed in each case**
10. **Check-by-check results** on every model and **specific failures** with the grader's evidence
11. **Trigger optimization** - every trigger query with its result, the description review and a suggested
    rewrite
12. **Test cases used** - every prompt in full, expected output, checks, input files and where the case
    came from (the skill's evals.json or your own file), then **when to re-benchmark** and **limits**

Every number is computed from the graded runs; Claude writes only the prose, so the tables can be trusted
even when the narrative is wrong.

## Is the skill still useful? (the verdict)

For each model the pipeline compares with-skill and without-skill runs check by check:

- **Only with skill** - the model passes the check only when the skill is loaded: the value the skill adds.
- **Unaided** - the model passes it without the skill: guidance that can be trimmed.
- **Worse with skill** - the skill gets in the way.

| Verdict | Rule (defaults in `skilleval.config.yaml` -> `verdict`) | What to do |
|---|---|---|
| KEEP | uplift >= 15 pts, or the skill alone gets a `safety` check right | Keep |
| KEEP, SLIM DOWN | uplift 5-15 pts, or several unique checks | Remove guidance for the unaided checks to cut tokens |
| RETIRE CANDIDATE | uplift < 5 pts and at most 1 unique check | Retire, or keep only bundled assets / org-specific facts |
| HARMFUL | uplift < 0 | Fix the cases under "where it does not help" or retire |

The overall verdict is the best one across `production_models` (a skill stays while any model your users
run still needs it). The outlook flags when uplift falls on more capable models in the same run, and when
it drops by 10+ pts for a model compared with the last approved evaluation.

**Why categories matter:** value from `asset` (bundled logos, templates) and `org_knowledge` (rules only
your organisation knows) will not be absorbed by model upgrades; value from `quality` and `behavior` usually
is. Tag checks with a `category` in `evals.json`; the analyst categorises any you leave out.

**Scheduled re-evaluation and new-model alerts:** see [Scheduling and notifications](#scheduling-and-notifications).

### evals.json fields used by the report

```json
{
  "id": 5, "name": "prohibited-letterhead", "type": "edge",
  "description": "Prohibited letterhead requests",
  "prompt": "...", "files": ["evals/files/logo-request.docx"],
  "expectations": [
    {"text": "Declines recolouring the logo", "category": "safety"},
    {"text": "Uses the approved logo asset", "category": "asset", "script": "evals/checks/logo.py"},
    "Points the user to Corporate Communications"
  ]
}
```

`type` is one of standard, edge, ambiguous, review, complex, should_not_trigger. A `should_not_trigger`
case automatically gets a "Skill not invoked" check read from the run's tool calls.

## Scheduling and notifications

`.github/workflows/skill-eval-scheduler.yml` runs **daily** (a few seconds, no model calls unless something
runs). The **frequency is set in `skilleval.config.yaml` → `schedule`**, not in the cron:

```yaml
schedule:
  every_days: 30          # re-evaluate each skill at least this often (override per skill in evals/eval-config.yaml)
  auto_run: false         # false = notify only; true = also start the evaluation (results still need approval)
  max_skills_per_run: 10  # cost cap for automatic runs
  watch_new_models: true  # alert when Anthropic releases a model
  notify: [Vismay2402]    # assigned to the issues -> GitHub emails them
```

Each day it:

1. Lists skills that are **due**: never approved, changed since approval, older than `every_days`, or never
   evaluated on a model in `execution.models` (so adding a model there flags every skill).
2. Asks the Models API (or Bedrock) for **newly released Claude models** and opens one issue per model -
   *"New Claude model available: …"* - listing the skills to re-evaluate, with the exact Run workflow settings.
3. Keeps one **"Skills due for evaluation"** issue up to date and closes it when nothing is due.
4. Posts to Slack/Teams if the secret `SKILL_EVAL_WEBHOOK_URL` is set (Slack incoming webhook, or a Teams
   / Power Automate flow that accepts `{"text": ...}`).
5. If `auto_run: true` (or you run the scheduler with *run_due_now*), starts the evaluations itself -
   `without_skill` baseline, so each gets a usefulness verdict - and they wait for human approval as usual.

GitHub emails issue assignees and repo watchers, so the issues are the notification.

## Where should skills live?

- **Recommended: one central skills repo** (this one) for every skill you distribute. It is the single place
  for review, evaluation history, the approval gate and the registry. Give each team its own folders and a
  `CODEOWNERS` entry (e.g. `skills/finance-* @org/finance-ai`) so the right people approve their skills.
- **Skills that must live elsewhere** (inside a product repo or a plugin): keep them there for development,
  but publish released versions into the central repo (a PR that copies the folder) - that is what gets
  evaluated, approved and distributed. Alternatively copy `skilleval/`, the config and `.github/` into that
  repo; the pipeline only needs a `skills/` folder.
- **Third-party skills you are considering** (Anthropic's, community ones): add them to the central repo
  under `skills/` with an eval set and run them through the same gate before anyone in the org uses them.
- One folder per skill, `SKILL.md` at its root, evals in `evals/`. Client-confidential skills or test data
  belong in a private repo.

## Your own test cases

Evaluate any skill with **your** prompts - instead of, or on top of, its `evals/evals.json`:

- **GitHub:** Actions → *Skill evaluation* → *Run workflow* → `test_cases`: a repo path such as
  `test-cases/my-cases.csv`, **or paste JSON / CSV into the box**; `test_cases_mode`: `replace` (only yours)
  or `append` (the skill's cases + yours).
- **Locally:** `python -m skilleval run --skill NAME --test-cases my-cases.csv [--test-cases-mode append]`
- **Keep them:** `python -m skilleval import-cases --skill NAME --file my-cases.csv` writes them into the
  skill's `evals/evals.json`.

CSV columns: `prompt`, `expectations` (separated by ` | `, optional `[safety]`/`[org_knowledge]`/... prefix),
and optionally `skill`, `id`, `name`, `type`, `description`, `expected_output`, `files`. JSON uses the
skill-creator schema. Templates and details: [test-cases/](test-cases/README.md).

## Skills in this repo

| Skill | What it does | Why it is here |
|---|---|---|
| `csv-data-profiler` | Data-quality report for a CSV/TSV | Mostly generic guidance - shows how much newer models absorb |
| `incident-postmortem` | Blameless postmortem in a house format with an org SEV scale | Bundled template + org rules: value models cannot learn |
| `snowflake-sql-review` | Reviews SQL against org rules (critical skill, 90% target) | Safety checks (blocks destructive prod SQL) |
| `conventional-commits` | Conventional Commits messages | Almost entirely generic: a likely retire candidate on strong models |
| `meeting-minutes` | Minutes with decisions, owned and dated action items | Team format rules (absolute dates, UNASSIGNED owners, confidentiality line) |
| `pii-redactor` | Redacts personal data (incl. Aadhaar / PAN) before sharing, logs counts only | Safety: leaks are checked by a script |

**Adding a skill:** create `skills/<name>/SKILL.md` (+ `evals/`), then run `python -m skilleval sync-workflow`
so it appears in the Run workflow dropdown, and commit both. The *Skill approval check* fails on pull requests
whose dropdown is out of date, so it cannot be forgotten. (GitHub's dropdowns are static, and the default
`GITHUB_TOKEN` is not allowed to edit workflow files, which is why this is a command rather than automatic.)

## Repo layout

```
skills/<skill-name>/SKILL.md            the skill (one folder per skill)
skills/<skill-name>/evals/evals.json    functional evals (skill-creator schema)
skills/<skill-name>/evals/trigger_evals.json   optional trigger evals
skills/<skill-name>/evals/files/        input files for evals
skills/<skill-name>/evals/checks/*.py   optional deterministic checks
skills/<skill-name>/evals/eval-config.yaml     optional per-skill config overrides
eval-results/<skill>/...                approved results (written only after human approval)
eval-results/<skill>/history.json       per-model uplift and verdict for every approved evaluation
registry/skills-registry.json           current approved version, pass rate, verdict, approver, history
skilleval/                              the pipeline (CLI: python -m skilleval)
skilleval/vendor/skill_creator/         vendored skill-creator tooling (Apache 2.0)
test-cases/                             your own test-case files + templates (CSV / JSON)
docs/MANUAL_EVALUATION.md               running an evaluation by hand (GitHub form, laptop, or no tooling)
.github/workflows/skill-eval.yml        the evaluation + human gate
.github/workflows/skill-eval-scheduler.yml  daily due-check, new-model alerts, optional auto-run
skilleval.config.yaml                   models, runs, schedule, gate thresholds, A/B comparison, targets
```

## One-time setup (GitHub)

1. Push this folder to a repo (or copy `skilleval/`, the config and `.github/` into an existing skills repo).
2. **Secrets** (Settings → Secrets and variables → Actions): `ANTHROPIC_API_KEY`; optionally
   `SKILL_EVAL_WEBHOOK_URL` for Slack/Teams notifications.
   For Amazon Bedrock instead: variable `CLAUDE_CODE_USE_BEDROCK=1`, variable `AWS_REGION`, secrets
   `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`, and set Bedrock model IDs in `skilleval.config.yaml`.
3. **Human gate** (without this the approval step does **not** wait - GitHub creates the environment
   unprotected and the run stores results as "unreviewed"): Settings → Environments → `skill-eval-approval`
   → tick **Required reviewers** → add the people (up to 6) or a team → **Save protection rules**. Leave
   *Prevent self-review* off while you are the only reviewer. Then every run pauses at **human-review** with a
   yellow **Review deployments** button; clicking it opens the Approve / Reject dialog with a comment box.
   GitHub notifies the reviewers (web + email, per their notification settings). Required reviewers work on
   public repos on any plan; private repos need GitHub Enterprise.
4. **Reviewer emails (optional)** - to email people (any address, with each `report.pdf` attached) when
   results wait for approval and again when they are approved: add secrets `SMTP_HOST`, `SMTP_PORT`
   (587 or 465), `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM` (e.g. Office 365 `smtp.office365.com`,
   Gmail `smtp.gmail.com` with an app password, SendGrid, Amazon SES), and list the addresses in
   `skilleval.config.yaml` → `review.notify_emails` or the Run workflow form's **reviewer_email**.
   Approving still happens in GitHub by a required reviewer - the email links straight to the run.
5. **Merge protection**: in a branch ruleset for the default branch, require the status check
   `Skill approval check / verify`. It fails unless every changed skill has an approved result for its
   exact current content, so editing a skill after approval re-blocks the merge.
6. **Bot token (recommended)**: add a fine-grained PAT or GitHub App token with `contents: write` and
   `pull-requests: write` as `SKILL_EVAL_BOT_TOKEN`. Commits pushed with the default `GITHUB_TOKEN` don't
   trigger other workflows, so without it the approval check won't re-run on the results commit
   (re-run it manually in that case).
7. Settings → Actions → General → allow GitHub Actions to create pull requests (needed for manual runs).

## Where results are stored, and in what format

Nothing is stored until a reviewer approves. On approval the results are committed (to the skill's PR, or
to a new `skill-eval/results-<run id>` PR for manual runs) - so git is the database, every change is
reviewed, and history is never lost.

```
EVALUATIONS.md                              all skills: number of evaluations, first/latest, verdict, approver
registry/skills-registry.json               current approved version, pass rate, verdict per skill (machine-readable)
eval-results/<skill>/
  EVALUATIONS.md                            one row per evaluation: timestamp, version, models, pass rate,
                                            A/B win rate, verdict, tokens, cost, approver, links
  latest.json                               the currently approved evaluation (used by the merge check)
  history.json                              per-model uplift over time (the "still useful?" trend)
  <UTC timestamp>-<version>/                one folder per approved evaluation, e.g. 20261009T085039Z-5c0d71c1/
    report.pdf                              the evaluation report
    summary.md                              the job summary (renders on GitHub)
    review.html                             skill-creator viewer: every prompt, output and grade
    approval.json                           who approved, when, their comment, the run link
    run.json                                models, configs, test-case source, pass rates, verdict, tokens, cost
    verdict.json · gate.json · matrix.json · benchmark*.json · comparison.json · usage.json · steps.json
    test_cases.json · trigger_results.json · analyst.json
    runs/<model>/eval-<id>-<name>/<with_skill|without_skill>/run-<n>/
      outputs/ (files the run produced + final_response.md) · grading.json · timing.json · transcript.md
```

All files are JSON, Markdown, HTML or PDF, so they are readable on GitHub, diffable, and easy to load into a
dashboard or Snowflake later. `python -m skilleval history` prints the count and timestamps per skill.
Every run - approved or not - is also listed under **Actions → Skill evaluation**, titled with the skill and
model (e.g. *Skill eval: meeting-minutes on claude-sonnet-5-5*), with its artifact kept 30 days.

## Day-to-day use

**New or changed skill** - open a PR that adds/edits `skills/<name>/`.
- No evals yet? Claude drafts `evals.json` and `trigger_evals.json`, pushes them to the PR and stops. Edit them
  (they define what "good" means), push, and the evaluation starts.
- The PR gets a results comment. Download the run artifact, open `review.html`, then go to the run and
  **Review deployments → Approve** (add a comment - it's stored as the approval note) or **Reject**.
- On approval the results commit lands in the same PR, so the skill and its evidence merge together.

**Re-evaluate any skill (a few clicks)** - Actions → *Skill evaluation* → *Run workflow*: pick the **skill**
from the dropdown (or `all`, or `several` + names), pick the **model** from the dropdown (default **Sonnet 5.5**; combinations or `other` for any model
id), the **grader model**, runs, baseline (`without_skill` for a usefulness verdict), and optionally paste
your own **test cases**. Approved results arrive as a PR. Step-by-step (and how to evaluate without any
automation): [docs/MANUAL_EVALUATION.md](docs/MANUAL_EVALUATION.md).

**Dry run of the whole flow at zero cost** - choose executor `mock`.

## Local use

```bash
pip install -r requirements.txt -r requirements-skills.txt   # needs the `claude` CLI, authenticated
python -m skilleval list
python -m skilleval validate --all
python -m skilleval draft-evals --skill my-skill
python -m skilleval run --skill my-skill --runs 1 --models claude-haiku-5-5,claude-sonnet-5-5 --no-trigger
open skilleval-workspace/my-skill/report.pdf         # the evaluation report + verdict
open skilleval-workspace/my-skill/review.html        # every output and grade
python -m skilleval publish --approver "$USER" --commit      # local equivalent of the human gate
python -m skilleval verify --changed origin/main
```

## Writing evals that mean something

- Assertions should be things a run **without** the skill would plausibly fail. The analyst notes call
  out assertions that pass in both configurations - rewrite those, they inflate scores without proving value.
- Prefer a check script for anything exact (counts, file present, schema valid) and keep the grader for
  judgement calls.
- Add near-miss queries to `trigger_evals.json`; a skill that never loads scores like the baseline.
- `runs_per_config: 3` is the minimum for a meaningful ± stddev; use 1 only while iterating.

## Cost control

Every eval × configuration × model × run is one `claude -p` session plus one grading call; each
(eval × model × run) pair adds one blind comparison call, and each skill gets one analyst call. The
report's **Token usage** section shows the exact split. Turn the comparison off with `comparison.enabled:
false` or `--no-compare`. Ten evals, two configurations, two models and three runs is 120 sessions +
120 gradings; use `--runs 1` while iterating. Test run: 3 evals × 2 configs × 2 models × 1 run on Haiku and
Sonnet cost about $0.48 in skill runs; grading and the analyst pass (Opus) come on top. `max_budget_usd_per_run` caps each session;
`run.json` records actual cost; `executor: mock` costs nothing.
