"""Deterministic check: commit.txt has a valid Conventional Commits header (<= 72 chars)."""
import re, sys
from pathlib import Path

f = Path(sys.argv[1]) / "commit.txt"
if not f.exists():
    print("commit.txt not found in outputs"); sys.exit(1)
lines = f.read_text().strip().splitlines()
head = lines[0] if lines else ""
ok = re.match(r"^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)(\([a-z0-9_-]+\))?!?: \S.*[^.]$", head)
if not ok:
    print(f"invalid header: {head!r}"); sys.exit(1)
if len(head) > 72:
    print(f"header is {len(head)} chars"); sys.exit(1)
if len(lines) > 1 and lines[1].strip():
    print("no blank line after header"); sys.exit(1)
print(f"header ok: {head}")
