# Feature documents

How much is written down before code follows the risk level in `AGENTS.md`,
not the size of the diff.

| Risk | What to write |
|---|---|
| Low | Nothing. The commit message is enough. |
| Medium | A short plan in the task or PR description: files, tests, regression surface. |
| High | `docs/features/<slug>/intent.md`, `spec.md` and `plan.md`, copied from `_TEMPLATE/`, before code. |

Feature folders other than `_TEMPLATE/` are local working notes and are not
tracked. Anything that must outlive the task belongs in a test, a rule, or the
PR description.

## Acceptance criteria are tests

Each acceptance criterion in `intent.md` names the check that proves it: a
pytest node, a Playwright spec, a `verify.py` step, or `manual:` with the
reason it cannot be automated. A criterion with no check is not accepted.
`plan.md` lists those checks under **Tests required**, and the task is not done
until `python scripts/verify.py task` has run them.
