---
name: conventional-commits
description: Writes git commit messages in the Conventional Commits format from a diff or a description of a change - type, optional scope, imperative subject under 72 characters, body and footers such as BREAKING CHANGE and Refs. Use this whenever someone asks for a commit message, or to reword or fix one. Not for PR descriptions, changelogs or release notes.
---

# Conventional commit messages

Write the message to `commit.txt` in the outputs folder and show it in the reply.

## Format

```
<type>(<scope>)<!>: <subject>

<body: what changed and why, wrapped at 72 columns>

<footers>
```

- **type**: feat, fix, docs, style, refactor, perf, test, build, ci, chore, revert.
- **scope**: the top-level folder or package the change touches (e.g. `api`, `web`), lower case.
  Leave it out if the change spans several.
- **subject**: imperative mood ("add", not "added"), no trailing period, 72 characters or fewer
  for the whole first line.
- **breaking change**: add `!` after the type/scope AND a `BREAKING CHANGE: <what breaks and how to
  migrate>` footer.
- **Refs footer**: if the branch name or request contains a ticket key like `PAY-123`, add
  `Refs: PAY-123`.
- One logical change per commit. If the diff mixes unrelated changes, say so and propose a split.
