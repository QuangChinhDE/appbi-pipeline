# Plan: <feature>

## Files and layers

Expected changes, grouped by layer. Being wrong here is fine; being silent is not
— an unexpected file in the diff is a signal worth noticing.

## Sequence

1. …

Order the steps so each one is verifiable on its own. Migrations before the code
that needs them; adapter contract before both implementations.

## Risks

What could go wrong, and how likely.

## Regression surface

What already works that this could break. Existing installations, other
deployment modes, the other adapter implementation, cached frontend queries.

## Tests required

Named tests, written before or alongside the code:

- `backend/tests/test_<x>.py::test_<y>` — …

## Rejected alternatives

Only when the choice was close or will be questioned later.

## Rollback

For engine, migration, security or deployment changes: how this is undone after
it has shipped and data has been written.
