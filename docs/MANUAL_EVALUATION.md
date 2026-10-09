# Evaluating a skill manually

Three ways, from fewest clicks to no automation at all. All three use the same Skills 2.0 ideas: realistic
test prompts, objective checks, and a comparison **with vs without** the skill.

---

## A. From GitHub, on demand (recommended)

1. Open the repo → **Actions** → **Skill evaluation** → **Run workflow** (top right).
2. Fill in the form:

   | Field | What to choose |
   |---|---|
   | **skill** | pick one skill from the dropdown, `all`, or `several (type below)` and list them in **skills_other** (`incident-postmortem,snowflake-sql-review`) |
   | **model** | default **claude-sonnet-5-5**. Pick a combination (e.g. `claude-haiku-5-5,claude-sonnet-5-5`) to see if the skill matters less on stronger models, or `other` + **model_other** for a model not in the list (e.g. one released yesterday) |
   | **grader_model** | default Sonnet 5.5; choose Opus 5.5 for high-stakes approvals |
   | **runs_per_config** | `1` while trying things, `3` for an approval you want to rely on |
   | **baseline** | `without_skill` = "is the skill still useful?"; `old_skill` = "is my edit an improvement?"; `auto` picks for you |
   | **trigger_evals** | on = also test whether the description makes Claude load the skill |
   | **ab_comparison** | on = blind A/B judge and win rate; off = cheaper trial run |
   | **test_cases** | optional - your own prompts: a repo path (`test-cases/my-cases.csv`) **or paste CSV/JSON** (see [test-cases/README.md](../test-cases/README.md)) |
   | **test_cases_mode** | `replace` = only your cases; `append` = the skill's cases + yours |
   | **allow_failed_gate** | tick to send results to review even if the automatic gate fails |
   | **executor** | `mock` = free dry run of the whole flow with fake results |

3. Wait for the **evaluate** job (about 5-15 minutes per skill). The run page shows the summary: verdict,
   Skills 2.0 steps, A/B win rate, token usage, per-case results, failures, trigger results, test cases.
4. Download the **skill-eval-…** artifact for `<skill>/report.pdf` (the full report) and `review.html`
   (every output and grade).
5. **Human gate:** on the run page, **Review deployments → skill-eval-approval → Approve** (add a comment -
   it is stored with the results) or **Reject**.
6. On approval a pull request with the results opens (`eval-results/<skill>/…`, registry, history). Merge it.

## B. From your laptop (same pipeline, no GitHub)

```bash
git clone <repo> && cd <repo>
pip install -r requirements.txt -r requirements-skills.txt
npm install -g @anthropic-ai/claude-code          # the `claude` CLI
export ANTHROPIC_API_KEY=sk-ant-...               # or use Bedrock (CLAUDE_CODE_USE_BEDROCK=1)

python -m skilleval validate --skill incident-postmortem
python -m skilleval run --skill incident-postmortem \
    --models claude-sonnet-5-5 --grader-model claude-sonnet-5-5 \
    --baseline without_skill --runs 1 \
    [--test-cases test-cases/my-cases.csv --test-cases-mode append] [--no-compare] [--no-trigger]

open skilleval-workspace/incident-postmortem/report.pdf
open skilleval-workspace/incident-postmortem/review.html

# local equivalent of the human gate: store the approved results and commit them
python -m skilleval publish --approver "your-github-id" --note "reviewed outputs" --commit
git push   # open a PR so the approval check and reviewers see it
```

Useful extras: `python -m skilleval due` (what needs evaluating), `python -m skilleval models-check`
(newly released models), `python -m skilleval import-cases --skill X --file my-cases.csv` (keep your cases).

## C. By hand, without this tooling (spot check)

When you only want a quick judgement - or cannot send data to the API - you can run the same method in
Claude Code or Claude.ai yourself:

1. **Write 5-10 test prompts** a real user would send, incl. edge cases and 2-3 near-misses that should *not*
   use the skill. For each, write 3-5 checks that are objectively true/false ("names the SEV level and the
   rule", not "is good").
2. **Run each prompt twice** in fresh conversations: once with the skill enabled, once with it disabled
   (Claude.ai: switch the skill off in your skill settings; Claude Code: move the folder out of `.claude/skills/`).
   Same model both times.
3. **Score blind:** paste both answers to a colleague (or a new Claude chat) as "A" and "B" without saying which
   used the skill; ask which is better and why. Also tick each check for each answer.
4. **Record** per prompt: checks passed with / without, A/B winner, and whether the skill loaded when it
   should (and stayed out when it should not).
5. **Decide** with the same rules the pipeline uses:
   - uplift = (checks passed with − without) / total checks
   - **KEEP** ≥ 15 pts, or the skill alone gets a safety check right · **KEEP, SLIM DOWN** 5-15 pts ·
     **RETIRE CANDIDATE** < 5 pts · **HARMFUL** < 0
   - A/B win rate ≥ 70% keep · 50-70% refine · < 50% remove or rewrite
6. Commit your notes and prompts under `test-cases/` so the automated runs can reuse them
   (`python -m skilleval import-cases`).

Skill-creator itself can drive this interactively: in Claude Code with the skill-creator skill, ask
*"run evals for the incident-postmortem skill and benchmark it against no skill"*.
