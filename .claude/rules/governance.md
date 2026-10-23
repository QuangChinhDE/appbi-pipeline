---
paths:
  - "AGENTS.md"
  - ".claude/**"
  - "scripts/verify.py"
  - "scripts/guardrail.py"
  - "scripts/claude_guard.py"
  - "scripts/check-agent-config.py"
  - ".github/**"
  - "docs/features/**"
  - "backend/tests/test_guardrail.py"
  - "backend/tests/test_layering_debt.py"
---

# Governance rules

These files are the protection system. A change to them is **High** risk, and
`scripts/guardrail.py` classifies it that way.

## What enforces what

| Control | Kind | Where |
|---|---|---|
| Rules, skills, hooks and tracked files are consistent | executable | `scripts/check-agent-config.py`, CI job *Agent config*, `backend/tests/test_agent_config.py` |
| A diff may not weaken a gate or touch a protected contract | executable, base-defined | `scripts/guardrail.py`, run by `.github/workflows/guardrail.yml` (`pull_request_target`: the workflow and the script both come from the base branch), `backend/tests/test_guardrail.py` |
| `task` never reports OK with nothing run, a skipped step, or an uncovered path | executable | `scripts/verify.py`, `backend/tests/test_verify.py` |
| CI and `verify.py full` run the same checks | executable | `backend/tests/test_verify.py::test_every_ci_check_has_a_verify_step` |
| Layering debt can shrink but not grow | executable ratchet | `backend/tests/test_layering_debt.py`, and the guardrail refuses a raised allowance |
| Destructive commands and hand-edits of locks/secrets are stopped at the moment of the attempt | local hook, defence-in-depth | `scripts/claude_guard.py`, `backend/tests/test_claude_guard.py` |
| Only the owner can update `master`, only through a PR with green checks | server-side | GitHub repository rulesets (owner-managed) |
| A deliberate protection change | human approval | the owner sets `APPBI_PROTECTION_APPROVAL_SHA` (below) |

The hook matches command text and can be bypassed by an indirect command. It
is not the boundary; the rulesets and the CI guardrail are.

Any change under `.github/workflows/`, to `pytest.ini`, a `conftest.py`, or
the Playwright config is a BLOCK: those files decide which gates run, in ways
no line-level diff check reliably reads. Residual limit: GitHub matches a
required check by name, so a PR-defined job could also report a run named
*Change guardrail*. Such a PR edits a workflow, which the trusted run blocks,
and only the owner can merge -- review the checks list before merging.

## Owner approval for a protection change

When a change genuinely has to touch a lock file, an applied migration, a CI
job, a verify step, a hook, a test or a debt allowance, the guardrail blocks it
until the repository owner sets the repository variable
`APPBI_PROTECTION_APPROVAL_SHA` to the full SHA of the pull request's head
commit (Settings → Secrets and variables → Actions → Variables). The approval:

- names one commit; pushing anything else voids it;
- cannot be set from inside a pull request -- only a repository admin can
  write repository variables;
- is never honoured locally, where there is no commit to bind it to;
- is blanket for that commit: it lifts every BLOCK the guardrail prints for
  it, so read the whole list in the *Change guardrail* log before setting it.

An agent never sets this variable. It reports the BLOCK and the SHA, and the
owner decides.

## Baseline debt

`backend/tests/test_layering_debt.py::DEBT` records the layering violations
that existed when the rule was made enforceable. Lower an allowance when debt
is paid down; never raise one. Fixing debt is its own change, never a side
effect of unrelated work.

## Editing the governance files

- `AGENTS.md` holds the provider-neutral contract. `.claude/CLAUDE.md` imports
  it with `@../AGENTS.md` and adds only Claude-specific material. Never copy a
  rule into both.
- A rule file needs `paths:` frontmatter whose globs match tracked files. A
  rule without one loads on every session and stops being scoped.
- Every path a rule, skill or `AGENTS.md` names must be tracked.
- `.claude/` is tracked through an allow-list in `.gitignore`. Personal
  settings go in `.claude/settings.local.json`, which stays ignored. Never
  negate the ignore rules for `.env*`, `secrets/`, `evidence*` or memory.
- Do not loosen, skip or delete a check to make a task pass. If a check is
  wrong, fix the check in its own change, with a test showing why.
