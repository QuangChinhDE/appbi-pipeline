---
paths:
  - "AGENTS.md"
  - ".claude/**"
  - "scripts/verify.py"
  - "scripts/claude_guard.py"
  - "scripts/check-agent-config.py"
  - ".github/**"
  - "docs/features/**"
---

# Governance rules

These files are the protection system. Treat a change to them as **High** risk.

- `AGENTS.md` holds the provider-neutral contract. `.claude/CLAUDE.md` imports
  it with `@../AGENTS.md` and adds only Claude-specific material. Never copy a
  rule into both.
- A rule file needs `paths:` frontmatter whose globs match real files. A rule
  without one loads on every session and stops being scoped.
- Every path a rule, skill or `AGENTS.md` names must exist.
  `python scripts/check-agent-config.py` checks this; CI runs it.
- `.claude/` is tracked through an allow-list in `.gitignore`. Personal
  settings go in `.claude/settings.local.json`, which stays ignored. Never
  negate the ignore rules for `.env*`, `secrets/`, `evidence*` or memory.
- Do not loosen, skip or delete a check to make a task pass. If a check is
  wrong, fix the check in its own change, with a test showing why.
