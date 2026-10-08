"""Loading and validating a skill's eval definitions.

Eval files follow the skill-creator (Skills 2.0) schema:

  skills/<name>/evals/evals.json          functional evals (prompt + expectations)
  skills/<name>/evals/trigger_evals.json  optional description-trigger evals
  skills/<name>/evals/files/...           optional input files referenced by evals
  skills/<name>/evals/checks/*.py         optional deterministic assertion scripts

Expectations may be plain strings (graded by the LLM grader, agents/grader.md) or
objects {"text": "...", "script": "evals/checks/x.py"} graded by running the script
with the run's outputs directory as argv[1] (exit code 0 = pass, stdout = evidence).
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .config import VENDOR_DIR

if str(VENDOR_DIR) not in sys.path:
    sys.path.insert(0, str(VENDOR_DIR))
from scripts.quick_validate import validate_skill  # noqa: E402  (vendored skill-creator)
from scripts.utils import parse_skill_md  # noqa: E402


AUTO_NOT_TRIGGERED = "__auto__:skill_not_invoked"
CASE_TYPES = {"standard", "edge", "ambiguous", "review", "complex", "should_not_trigger"}
CATEGORIES = {"asset", "org_knowledge", "safety", "behavior", "quality"}
# asset          uses files bundled with the skill (logos, templates)  - models can't learn these
# org_knowledge  rules/facts only your organisation knows               - models can't learn these
# safety         refuses / prevents something prohibited or risky
# behavior       workflow behaviour (asks before acting, stays out of the way)
# quality        general output quality any good model should reach    - most likely to be absorbed


@dataclass
class Expectation:
    text: str
    script: str | None = None
    category: str | None = None


@dataclass
class EvalCase:
    id: int
    name: str
    prompt: str
    expected_output: str = ""
    files: list[str] = field(default_factory=list)
    expectations: list[Expectation] = field(default_factory=list)
    type: str = "standard"
    description: str = ""

    @property
    def dirname(self) -> str:
        return f"eval-{self.id}-{self.name}"

    def metadata(self) -> dict:
        return {
            "eval_id": self.id,
            "eval_name": self.name,
            "prompt": self.prompt,
            "expected_output": self.expected_output,
            "assertions": [e.text for e in self.expectations],
            "type": self.type,
            "description": self.description,
        }


def slugify(text: str, limit: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s[:limit].rstrip("-")) or "eval"


def skill_identity(skill_path: Path) -> tuple[str, str]:
    name, description, _ = parse_skill_md(Path(skill_path))
    return name, description


def validate(skill_path: Path) -> list[str]:
    """Return a list of problems; empty means the skill and its evals are well formed."""
    skill_path = Path(skill_path)
    problems: list[str] = []
    ok, msg = validate_skill(skill_path)
    if not ok:
        problems.append(f"SKILL.md: {msg}")
        return problems

    name, _ = skill_identity(skill_path)
    if name != skill_path.name:
        problems.append(f"Frontmatter name '{name}' does not match folder name '{skill_path.name}'")

    evals_file = skill_path / "evals" / "evals.json"
    if not evals_file.exists():
        problems.append("evals/evals.json is missing (run: python -m skilleval draft-evals --skill "
                        f"{skill_path.name})")
        return problems
    try:
        cases = load_evals(skill_path)
    except (ValueError, json.JSONDecodeError) as e:
        problems.append(f"evals/evals.json: {e}")
        return problems
    if not cases:
        problems.append("evals/evals.json has no evals")
    for c in cases:
        if not c.expectations:
            problems.append(f"eval {c.id} ({c.name}) has no expectations - nothing to grade")
        for f in c.files:
            if not (skill_path / f).exists():
                problems.append(f"eval {c.id}: input file not found: {f}")
        if c.type not in CASE_TYPES:
            problems.append(f"eval {c.id}: unknown type '{c.type}' (one of {sorted(CASE_TYPES)})")
        for e in c.expectations:
            if e.category and e.category not in CATEGORIES:
                problems.append(f"eval {c.id}: unknown category '{e.category}' (one of {sorted(CATEGORIES)})")
            if e.script and e.script != AUTO_NOT_TRIGGERED and not (skill_path / e.script).exists():
                problems.append(f"eval {c.id}: check script not found: {e.script}")

    trig = skill_path / "evals" / "trigger_evals.json"
    if trig.exists():
        try:
            items = json.loads(trig.read_text())
            assert isinstance(items, list) and all("query" in i and "should_trigger" in i for i in items)
        except Exception:
            problems.append("evals/trigger_evals.json must be a list of {query, should_trigger}")
    return problems


def load_evals(skill_path: Path) -> list[EvalCase]:
    data = json.loads((Path(skill_path) / "evals" / "evals.json").read_text())
    raw = data.get("evals") if isinstance(data, dict) else data
    if not isinstance(raw, list):
        raise ValueError("expected {'evals': [...]}")
    cases, seen = [], set()
    for i, e in enumerate(raw):
        if "prompt" not in e:
            raise ValueError(f"eval #{i} has no prompt")
        eid = int(e.get("id", i))
        if eid in seen:
            raise ValueError(f"duplicate eval id {eid}")
        seen.add(eid)
        exps = []
        for x in e.get("expectations", e.get("assertions", [])) or []:
            if isinstance(x, str):
                exps.append(Expectation(text=x))
            elif isinstance(x, dict) and "text" in x:
                exps.append(Expectation(text=x["text"], script=x.get("script"), category=x.get("category")))
            else:
                raise ValueError(f"eval {eid}: bad expectation {x!r}")
        ctype = e.get("type", "standard")
        if ctype == "should_not_trigger":
            # Checked automatically from the run's tool calls: the skill must not be loaded.
            exps.append(Expectation(text="Skill not invoked (automatic check)", script=AUTO_NOT_TRIGGERED,
                                    category="behavior"))
        cases.append(EvalCase(
            id=eid,
            name=slugify(e.get("name") or e["prompt"]),
            prompt=e["prompt"],
            expected_output=e.get("expected_output", ""),
            files=list(e.get("files", [])),
            expectations=exps,
            type=ctype,
            description=e.get("description") or e.get("name") or e["prompt"][:60],
        ))
    return cases


def load_trigger_evals(skill_path: Path) -> list[dict] | None:
    f = Path(skill_path) / "evals" / "trigger_evals.json"
    return json.loads(f.read_text()) if f.exists() else None
