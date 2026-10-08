---
name: csv-data-profiler
description: Profiles a CSV or TSV file and writes a data-quality report (profile.md) with row and column counts, inferred column types, null and duplicate counts, and notable issues. Use this whenever someone asks to profile, sanity-check, audit or "take a first look" at a tabular data file, even if they don't say "profile".
---

# CSV data profiler

Produce a short, factual data-quality report someone can skim before using a dataset.

## Steps

1. Load the file with pandas (`pd.read_csv`, `sep=None, engine="python"` handles TSV and odd delimiters).
2. Compute, per column: inferred type (integer, float, date, boolean, categorical, text), null count and %,
   distinct count, and min/max for numeric and date columns.
3. Count fully duplicated rows.
4. Flag issues worth a human's attention: columns over 20% null, numeric-looking columns stored as text,
   constant columns, and likely ID columns with duplicates.

## Output

Write `profile.md` to the output folder, using exactly these sections, so reports are comparable:

```
# Data profile: <file name>
## Overview        (rows, columns, duplicate rows)
## Columns         (one markdown table: column | type | nulls | null % | distinct | min | max)
## Issues          (bullets; write "None found" if empty)
```

Report numbers exactly as computed; never estimate.
