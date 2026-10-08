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

import csv
import io
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
    source: str = "evals/evals.json"

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
            "source": self.source,
        }


def slugify(text: str, limit: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s[:limit].rstrip("-")) or "eval"


def skill_identity(skill_path: Path) -> tuple[str, str]:
    name, description, _ = parse_skill_md(Path(skill_path))
    return name, description


def validate(skill_path: Path, custom: Path | None = None, mode: str = "replace") -> list[str]:
    """Return a list of problems; empty means the skill and its evals are well formed.
    custom: optional user-supplied test-case file (JSON or CSV) used instead of / in addition to evals.json."""
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
    if not evals_file.exists() and not (custom and mode == "replace"):
        problems.append("evals/evals.json is missing (run: python -m skilleval draft-evals --skill "
                        f"{skill_path.name})")
        return problems
    try:
        cases = load_evals(skill_path, custom, mode)
    except (ValueError, json.JSONDecodeError, OSError, csv.Error) as e:
        problems.append(f"{'test cases ' + str(custom) if custom else 'evals/evals.json'}: {e}")
        return problems
    if not cases:
        problems.append("evals/evals.json has no evals")
    for c in cases:
        if not c.expectations:
            problems.append(f"eval {c.id} ({c.name}) has no expectations - nothing to grade")
        for f in c.files:
            if not (skill_path / f).exists():  # absolute paths (custom cases) pass through unchanged
                problems.append(f"eval {c.id}: input file not found: {f}")
        if c.type not in CASE_TYPES:
            problems.append(f"eval {c.id}: unknown type '{c.type}' (one of {sorted(CASE_TYPES)})")
        for e in c.expectations:
            if e.category and e.category not in CATEGORIES:
                problems.append(f"eval {c.id}: unknown category '{e.category}' (one of {sorted(CATEGORIES)})")
            if e.script and e.script != AUTO_NOT_TRIGGERED and not (skill_path / e.script).exists():  # noqa
                problems.append(f"eval {c.id}: check script not found: {e.script}")

    trig = skill_path / "evals" / "trigger_evals.json"
    if trig.exists():
        try:
            items = json.loads(trig.read_text())
            assert isinstance(items, list) and all("query" in i and "should_trigger" in i for i in items)
        except Exception:
            problems.append("evals/trigger_evals.json must be a list of {query, should_trigger}")
    return problems


def _parse(raw: list, source: str, resolve_file=None) -> list[EvalCase]:
    cases, seen = [], set()
    for i, e in enumerate(raw):
        if "prompt" not in e or not str(e["prompt"]).strip():
            raise ValueError(f"test case #{i + 1} has no prompt")
        eid = int(e.get("id") or i + 1)
        if eid in seen:
            raise ValueError(f"duplicate eval id {eid}")
        seen.add(eid)
        exps = []
        for x in e.get("expectations", e.get("assertions", [])) or []:
            if isinstance(x, str):
                m = re.match(r"^\s*\[(\w+)\]\s*(.+)$", x)   # "[safety] Declines ..." sets the category
                if m and m.group(1).lower() in CATEGORIES:
                    exps.append(Expectation(text=m.group(2).strip(), category=m.group(1).lower()))
                else:
                    exps.append(Expectation(text=x))
            elif isinstance(x, dict) and "text" in x:
                exps.append(Expectation(text=x["text"], script=x.get("script"), category=x.get("category")))
            else:
                raise ValueError(f"eval {eid}: bad expectation {x!r}")
        ctype = (e.get("type") or "standard").strip()
        if ctype == "should_not_trigger":
            # Checked automatically from the run's tool calls: the skill must not be loaded.
            exps.append(Expectation(text="Skill not invoked (automatic check)", script=AUTO_NOT_TRIGGERED,
                                    category="behavior"))
        files = list(e.get("files", []) or [])
        if resolve_file:
            files = [resolve_file(f) for f in files]
        cases.append(EvalCase(
            id=eid,
            name=slugify(e.get("name") or e["prompt"]),
            prompt=e["prompt"],
            expected_output=e.get("expected_output", "") or "",
            files=files,
            expectations=exps,
            type=ctype,
            description=e.get("description") or e.get("name") or e["prompt"][:60],
            source=source,
        ))
    return cases


def _split(cell: str, seps: str) -> list[str]:
    return [x.strip() for x in re.split(seps, cell or "") if x.strip()]


def read_test_case_file(path: Path, skill_name: str) -> list[dict] | None:
    """User-supplied test cases. Returns raw case dicts for this skill, or None if the file has none for it.

    JSON: {"skill_name": "...", "evals": [...]} (skill-creator schema), a bare list of cases, or
          {"skills": {"<skill>": [...]}} for several skills in one file.
    CSV:  columns prompt (required), id, name, type, description, expected_output, files, skill,
          expectations - several expectations separated by " | " or new lines; prefix one with
          "[safety]", "[asset]", ... to set its category; several files separated by ";".
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".csv" or (not text.lstrip().startswith(("{", "["))):
        rows = list(csv.DictReader(io.StringIO(text)))
        if rows and "prompt" not in rows[0]:
            raise ValueError("CSV needs a 'prompt' column (see templates/test-cases-template.csv)")
        out = []
        for r in rows:
            r = {k.strip().lower(): (v or "").strip() for k, v in r.items() if k}
            if r.get("skill") and r["skill"] != skill_name:
                continue
            if not r.get("prompt"):
                continue
            out.append({"id": r.get("id") or None, "name": r.get("name"), "type": r.get("type") or "standard",
                        "description": r.get("description"), "prompt": r["prompt"],
                        "expected_output": r.get("expected_output", ""),
                        "expectations": _split(r.get("expectations", ""), r"\s\|\s|\n"),
                        "files": _split(r.get("files", ""), r";")})
        return out or None
    data = json.loads(text)
    if isinstance(data, list):
        return data
    if "skills" in data:
        found = data["skills"].get(skill_name)
        return (found.get("evals") if isinstance(found, dict) else found) or None
    if data.get("skill_name") and data["skill_name"] != skill_name:
        return None
    return data.get("evals")


def load_evals(skill_path: Path, custom: Path | None = None, mode: str = "replace") -> list[EvalCase]:
    """The skill's evals/evals.json, optionally replaced or extended by a user-supplied test-case file."""
    skill_path = Path(skill_path)
    own: list[EvalCase] = []
    f = skill_path / "evals" / "evals.json"
    if f.exists() and not (custom and mode == "replace"):
        data = json.loads(f.read_text())
        raw = data.get("evals") if isinstance(data, dict) else data
        if not isinstance(raw, list):
            raise ValueError("expected {'evals': [...]}")
        own = _parse(raw, "evals/evals.json")
    if not custom:
        return own
    custom = Path(custom).resolve()
    raw = read_test_case_file(custom, skill_path.name)
    if raw is None:
        if mode == "replace" and not f.exists():
            raise ValueError(f"{custom.name} has no test cases for {skill_path.name}")
        return own if mode == "append" else _parse(json.loads(f.read_text()).get("evals", []), "evals/evals.json")

    from .config import REPO_ROOT

    def resolve(p: str) -> str:
        for base in (custom.parent, skill_path, REPO_ROOT):
            if (base / p).exists():
                return str((base / p).resolve())
        raise ValueError(f"input file not found: {p} (looked next to {custom.name}, in the skill folder and the repo root)")

    try:
        rel = custom.relative_to(REPO_ROOT)
    except ValueError:
        rel = Path(custom.name)
    extra = _parse(raw, f"custom: {rel}", resolve)
    if mode == "append":
        next_id = max([c.id for c in own], default=0) + 1
        taken = {c.id for c in own}
        for c in extra:
            if c.id in taken:
                c.id, next_id = next_id, next_id + 1
            taken.add(c.id)
            next_id = max(next_id, c.id + 1)
        return own + extra
    return extra


def load_trigger_evals(skill_path: Path) -> list[dict] | None:
    f = Path(skill_path) / "evals" / "trigger_evals.json"
    return json.loads(f.read_text()) if f.exists() else None
