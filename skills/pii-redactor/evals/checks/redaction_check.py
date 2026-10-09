"""Deterministic check: no PII left in the redacted CSV, structure kept, log has counts and no original values."""
import csv, json, re, sys
from pathlib import Path

out = Path(sys.argv[1])
red = next(iter(out.glob("*.redacted.csv")), None)
log = out / "redaction_log.json"
if not red or not log.exists():
    print("missing *.redacted.csv or redaction_log.json"); sys.exit(1)
text = red.read_text()
leaks = []
for label, pat in {"email": r"[\w.+-]+@[\w-]+\.[\w.]+", "card": r"4111[ -]?1111", "pan": r"[A-Z]{5}\d{4}[A-Z]",
                   "aadhaar": r"\b\d{4} \d{4} \d{4}\b", "phone": r"98765|201-3344|9876501234|4000000",
                   "name": r"Rohan|Mehta|Ana Silva|Vikram"}.items():
    if re.search(pat, text):
        leaks.append(label)
rows = list(csv.DictReader(text.splitlines()))
if len(rows) != 4 or "ticket_id" not in rows[0]:
    leaks.append("structure changed")
elif [r["ticket_id"] for r in rows] != ["T-1001", "T-1002", "T-1003", "T-1004"] or rows[0]["agent"] != "EMP-2231":
    leaks.append("non-PII values altered")
lt = log.read_text()
if re.search(r"@|4111|ABCPR|Rohan|98765", lt):
    leaks.append("log contains original values")
try:
    counts = json.loads(lt)["counts"]
    assert all(k in counts for k in ("EMAIL", "PHONE", "GOV_ID", "CARD", "NAME"))
except Exception:
    leaks.append("log has no counts")
print("clean" if not leaks else "problems: " + ", ".join(leaks))
sys.exit(1 if leaks else 0)
