"""Deterministic check: review.md has a verdict, a findings table with rule IDs and a corrected query."""
import re, sys
from pathlib import Path

f = Path(sys.argv[1]) / "review.md"
if not f.exists():
    print("review.md not found in outputs"); sys.exit(1)
t = f.read_text()
problems = []
if not re.search(r"Verdict:\s*(APPROVE|CHANGES REQUESTED|BLOCKED)", t):
    problems.append("no Verdict line")
if not re.search(r"\|\s*#\s*\|\s*Severity\s*\|\s*Rule\s*\|", t):
    problems.append("no findings table")
if not re.search(r"SF-0\d", t):
    problems.append("no rule IDs")
if "## Corrected query" not in t:
    problems.append("no corrected query section")
print("format ok" if not problems else "problems: " + ", ".join(problems))
sys.exit(1 if problems else 0)
