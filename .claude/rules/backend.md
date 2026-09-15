---
paths:
  - "backend/app/**"
---

# Backend rules

FastAPI + SQLAlchemy, Python 3.11 in CI. Async throughout.

## Layering, as it actually is

`api/v1/<area>.py` → `services/<area>.py` → `models/*.py`.

- **Routers** do routing, auth dependency wiring, request/response schema
  binding and nothing else. Business rules, tenancy decisions and multi-step
  orchestration live in `services/`. A router that grows an `if` about domain
  state is in the wrong layer.
- **Schemas** (`app/schemas/`) are the wire contract; **models** (`app/models/`)
  are the database. Never return a model from a router.
- There is **no repository layer**. `app/repositories/` and `app/workers/` are
  empty packages. Services use the SQLAlchemy session directly — follow that,
  do not introduce a repository abstraction.
- `app/transforms/` is a vertical slice with its own router (`transforms/api.py`,
  mounted from `main.py`), services and dbt runtime. Transform code stays there.
- `app/core/` holds cross-cutting machinery: config, db, errors, logging,
  permissions, secrets, security, context. Import from it; do not duplicate it.

## Errors and auth

- Raise `AppError` with an `ErrorCategory` from `app/core/errors.py`. The
  normalized envelope and its remediation action are what the frontend renders.
  Do not return a bare 500 or a hand-built dict.
- Authorization goes through `app/core/permissions.py` and the dependencies in
  `app/api/deps.py`. Never re-derive a permission check inline.
- Every tenant-scoped query filters by workspace. A missing filter is a
  cross-tenant read, not a bug of convenience.

## Async and transactions

- Handlers and services are `async def`. Do not call blocking I/O inside them;
  the worker processes (`worker.py`, `transform_worker.py`) exist for long work.
- One request, one transaction boundary. Do not commit mid-service and leave a
  caller unable to roll back.

## Engine access

Only `app/adapters/` imports engine specifics. A service depends on the
`IntegrationEngineAdapter` Protocol in `app/adapters/base.py` and obtains an
instance from `app/adapters/registry.py`. See `.claude/rules/engine-boundary.md`.

## Verification

- `python -m pyflakes app` must be clean. This exists because a name that
  stopped existing after a refactor shipped twice and was only found at runtime.
- Any behaviour change needs a test in `backend/tests/`. Run the targeted file
  during work; run the suite before done.
