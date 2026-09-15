---
paths:
  - "backend/tests/**"
  - "pytest.ini"
  - ".github/workflows/**"
---

# Testing rules

`pytest.ini` sets `testpaths = backend/tests`. Everything runs offline; the
Transform integration suite opts in with `RUN_TRANSFORM_INTEGRATION_TESTS=1`
and needs a migrated disposable database.

## Choosing tests by area

| Changed | Run first |
|---|---|
| a service or router | the matching `backend/tests/test_<area>.py` |
| `app/transforms/**` | `test_transform_*.py` |
| `app/adapters/**` | `test_*connector*.py`, plus the whole suite before done |
| `app/models/**` or a migration | `python scripts/check-migration-heads.py` then the suite |
| frontend strings or components | `test_i18n_coverage.py`, then `tsc --noEmit` |
| permissions or auth | `test_permissions.py`, `test_admin_credentials.py` |

`backend/tests/` also holds structural tests that read *frontend* source
(`test_i18n_coverage.py`) and packaging facts. A Python test failing after a
`.tsx` edit is expected, not a mistake.

## Bug fixes

Reproduce first. Write a test that fails for the stated reason, then fix it.
If the defect genuinely cannot be reproduced automatically, say so explicitly
in the report rather than skipping the step silently.

Never delete or weaken a failing test to go green. The only case for removing a
test is that the behaviour it asserts is being deliberately removed — and then
say so.

## Layering the cost

- During implementation: `python scripts/verify.py fast` — targeted, seconds.
- Before claiming done: `python scripts/verify.py task`.
- Before push / in CI: `python scripts/verify.py full`.

Do not run the full suite after every edit. Do not use that as a reason to skip
`task` before reporting done.
