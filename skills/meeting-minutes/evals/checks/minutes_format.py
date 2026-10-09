"""Deterministic check: minutes.md sections, action-item table with owner and ISO due date on every row."""
import re, sys
from pathlib import Path

f = Path(sys.argv[1]) / "minutes.md"
if not f.exists():
    print("minutes.md not found in outputs"); sys.exit(1)
t = f.read_text()
problems = [h for h in ("## Decisions", "## Action items", "## Open questions") if h not in t]
rows = [l for l in t.split("## Action items")[-1].split("## Open questions")[0].splitlines()
        if l.strip().startswith("|") and not re.match(r"^\|\s*(#|-)", l.strip())]
if not rows:
    problems.append("no action-item rows")
for r in rows:
    cells = [c.strip() for c in r.strip().strip("|").split("|")]
    if len(cells) < 4 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", cells[3]) or not cells[2] or cells[2].lower() in ("team", "all", "tbd"):
        problems.append(f"bad row: {r.strip()[:80]}")
print("format ok" if not problems else "problems: " + "; ".join(problems))
sys.exit(1 if problems else 0)
