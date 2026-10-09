# claude-sonnet-5-5

# Skill Benchmark: meeting-minutes

**Model**: claude-sonnet-5-5
**Date**: 2026-10-09T08:48:47Z
**Evals**: 1, 2, 3 (1 runs each per configuration)

## Summary

| Metric | With Skill | Without Skill | Delta |
|--------|------------|---------------|-------|
| Pass Rate | 94% ± 10% | 72% ± 25% | +0.22 |
| Time | 9.7s ± 2.6s | 9.8s ± 4.2s | -0.1s |
| Tokens | 48143 ± 10168 | 46805 ± 10909 | +1338 |

## Notes

- Non-discriminating assertion (passes with and without the skill): 'Rahul's hourly-refresh action is due 2026-10-09 and Meera's Power BI estimate is due 2026-'
- Non-discriminating assertion (passes with and without the skill): 'Retiring the old S3 bucket on 2026-10-31 is recorded as a decision'
- Non-discriminating assertion (passes with and without the skill): 'No small talk (coffee joke, audio check) is included'
- Non-discriminating assertion (passes with and without the skill): 'The calendar-invite action has owner UNASSIGNED and is also listed under Open questions'
- Non-discriminating assertion (passes with and without the skill): 'Priya's Q3 numbers action is due 2026-10-31'
- Non-discriminating assertion (passes with and without the skill): 'Moving standup to 10am is recorded as a decision'
- Non-discriminating assertion (passes with and without the skill): 'The reply is an agenda with the three topics and time allocations adding up to about 45 mi'
- Assertion never passes with the skill: 'minutes.md has Decisions, Action items and Open questions, and every action item has one o'