# Your own test cases

Put test-case files here (or anywhere in the repo) to evaluate a skill with **your** prompts instead of,
or in addition to, the skill's `evals/evals.json`.

**Run with them**
- GitHub: Actions → *Skill evaluation* → *Run workflow* → `test_cases` = `test-cases/my-cases.csv`
  (a repo path) **or paste JSON/CSV straight into the box**; `test_cases_mode` = `replace` (only your cases)
  or `append` (the skill's cases + yours).
- Locally: `python -m skilleval run --skill csv-data-profiler --test-cases test-cases/my-cases.csv`
- Keep them for good: `python -m skilleval import-cases --skill csv-data-profiler --file test-cases/my-cases.csv`
  writes them into the skill's `evals/evals.json` (input files are copied into `evals/files/`).

The report and the job summary list every test case used and where it came from (`custom: test-cases/...`).

## CSV (easiest - edit in Excel or Google Sheets)

| column | required | meaning |
|---|---|---|
| `prompt` | yes | what the user asks Claude, exactly as they would type it |
| `expectations` | yes | checks the answer must pass, separated by ` \| ` (or new lines in the cell). Prefix with `[safety]`, `[asset]`, `[org_knowledge]`, `[behavior]` or `[quality]` to set its category |
| `skill` | no | which skill the row is for (rows for other skills are ignored); blank = every selected skill |
| `id`, `name`, `description` | no | labels in the report |
| `type` | no | `standard`, `edge`, `ambiguous`, `review`, `complex` or `should_not_trigger` (adds an automatic "skill not loaded" check) |
| `expected_output` | no | short description of a good answer (shown to the grader and in the report) |
| `files` | no | input files separated by `;` - paths relative to this file, the skill folder or the repo root. Claude finds them in `inputs/` |

See [template.csv](template.csv).

## JSON (skill-creator / Skills 2.0 schema)

Same format as `skills/<name>/evals/evals.json` - see [template.json](template.json). A file can also hold
cases for several skills: `{"skills": {"csv-data-profiler": [...], "incident-postmortem": [...]}}`.

## Writing good checks
- Each check should be something a run **without** the skill would plausibly fail - otherwise it cannot show
  the skill's value.
- Make checks objectively verifiable from the reply or the files ("reports 8 rows", not "is helpful").
- Skills 2.0 guidance: 5-10 cases per skill, including edge cases.
