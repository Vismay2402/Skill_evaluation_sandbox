"""Deterministic check: profile.md exists with the required sections and exact counts."""
import re, sys
from pathlib import Path

out = Path(sys.argv[1])
f = out / "profile.md"
if not f.exists():
    print("profile.md not found in outputs"); sys.exit(1)
text = f.read_text()
missing = [h for h in ("## Overview", "## Columns", "## Issues") if h not in text]
if missing:
    print(f"missing sections: {missing}"); sys.exit(1)
overview = text.split("## Columns")[0]
ok_rows = re.search(r"\b8\b", overview) is not None
ok_dupes = re.search(r"duplicat[^\n]*\b1\b", overview, re.I) is not None
print(f"sections ok; rows=8 reported: {ok_rows}; 1 duplicate row reported: {ok_dupes}")
sys.exit(0 if ok_rows and ok_dupes else 1)
