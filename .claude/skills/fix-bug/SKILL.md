---
name: fix-bug
description: Diagnose and fix a defect in AppBI Pipeline. Use for bug reports, regressions, incorrect behaviour or a failing check. Enforces reproduce, find the owning layer, write a failing regression test, then make the minimal fix.
---

# Fix a bug

Order matters: **reproduce → locate the owning layer → failing regression test
→ minimal fix → verify**.

The layer that owns the defect sets its risk level, not the size of the fix: a
one-line change in a permission check or a migration is High and takes the High
workflow in `.claude/CLAUDE.md`, including an independent review.

## 1. Reproduce

Get the defect to happen on demand before changing anything. A fix for a bug
you never reproduced is a guess.

If it truly cannot be reproduced locally (needs a live engine, a warehouse, a
specific installation), say so explicitly in the report — do not quietly skip
this step and present the fix as confirmed.

## 2. Ask the four questions

- **What layer actually owns this failure?** UI, router, service, model,
  migration, adapter, engine, deployment. The layer where you *see* it is
  usually not the layer that *caused* it.
- **What invariant was violated?** A workspace filter missing, a secret not
  split out of config, a schema and model disagreeing, an adapter contract only
  one implementation satisfies.
- **Can it be reproduced automatically?** Almost always yes, at the layer that
  owns it.
- **What test would have caught this?** Write that one.

## 3. Regression test first

Add a test to `backend/tests/` that fails for the stated reason. Watch it fail.
Then fix. Then watch it pass. A test written after the fix tends to assert what
the code now does rather than what it should do.

## 4. Forbidden fixes

- Fixing the UI symptom when the backend or the stored data is wrong.
- Catching and swallowing an exception instead of fixing its cause.
- Returning a default, an empty list or a fake success to hide a failure.
- Loosening a validation, a permission check or a type to make an error stop.
- Deleting or `skip`-ing a failing test. The only exception is that the
  behaviour it asserts is being deliberately removed — and then say so out loud.
- Adding a retry around a deterministic bug.

## 5. Minimal fix

Change the owning layer only. Resist fixing the three nearby things you noticed;
note them in the report instead.

## 6. Verify

    python scripts/verify.py task

Confirm the new test passes, the previously-passing tests still do, and the
original reproduction no longer reproduces.

## 7. Report

**Changed**, **Verified**, **Not verified**, **Risks** — plus one line on the
root cause and the invariant that was broken.
