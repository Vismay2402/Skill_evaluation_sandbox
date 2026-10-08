"""Deterministic check: postmortem.md follows the Acme template (sections in order, timeline and action tables)."""
import re, sys
from pathlib import Path

f = Path(sys.argv[1]) / "postmortem.md"
if not f.exists():
    print("postmortem.md not found in outputs"); sys.exit(1)
t = f.read_text()
order = ["## Summary", "## Impact", "## Timeline", "## Root cause", "## Contributing factors",
         "## What went well", "## Action items"]
pos = [t.find(h) for h in order]
if -1 in pos:
    print("missing sections: " + ", ".join(h for h, p in zip(order, pos) if p == -1)); sys.exit(1)
if pos != sorted(pos):
    print("sections out of order"); sys.exit(1)
problems = []
if not re.search(r"\|\s*Time \(UTC\)\s*\|\s*Event\s*\|\s*Who\s*\|", t):
    problems.append("timeline table header")
if not re.search(r"\|\s*Action\s*\|\s*Owner \(role\)\s*\|\s*Due\s*\|\s*Ticket\s*\|", t):
    problems.append("action items table header")
if "OPS-" not in t:
    problems.append("no OPS ticket placeholder")
if not re.search(r"Status:\s*Draft", t):
    problems.append("no draft status line")
print("format ok" if not problems else "problems: " + ", ".join(problems))
sys.exit(1 if problems else 0)
