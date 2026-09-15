---
paths:
  - "backend/migrations/**"
  - "backend/app/models/**"
  - "backend/alembic.ini"
---

# Database and migration rules

Alembic, with 25 revisions under `backend/migrations/versions/`.

## Before writing a migration

1. Read the current chain. Find the head:
   `python scripts/check-migration-heads.py`
2. Read the two or three most recent revisions to match the house style
   (naming, docstring explaining *why*, reversible `downgrade`).
3. Check whether an existing revision already does part of it.

## Rules

- **Exactly one head.** CI fails otherwise, and a second head means an
  installed database cannot upgrade. A new revision sets `down_revision` to the
  current head.
- Model and migration must agree. A column added to `app/models/` with no
  migration is a production `UndefinedColumn` at first query.
- Assume real installations with real rows. A new non-nullable column needs a
  default or a backfill; a rename is add + backfill + drop across releases, not
  an in-place rename.
- **Destructive changes require explicit approval.** Dropping a column or table,
  narrowing a type, or deleting rows — ask first, with the data loss stated.
- `downgrade()` must actually work, or say plainly why it cannot.
- Never edit a revision that has already shipped. Add a new one.

## Verification

- `python scripts/check-migration-heads.py` — single head (also a CI step)
- `python -m pytest backend/tests -q` — the suite includes persistence tests
- The Transform integration suite needs a migrated disposable database and opts
  in with `RUN_TRANSFORM_INTEGRATION_TESTS=1`. Everything else runs offline.
