---
paths:
  - "backend/app/**"
---

# Backend rules

FastAPI + SQLAlchemy async, Python 3.11 in CI. Every statement below was checked
against the code; counts are from 2026-09-15.

## Layering, as it actually is

`api/v1/<area>.py` → `services/<area>.py` → `models/*.py`. Eleven of twelve
routers import from `app.services`.

- There is **no repository layer**. `app/repositories/` and `app/workers/` are
  empty packages. Services use the `AsyncSession` directly. Do not introduce a
  repository abstraction.
- **Schemas** (`app/schemas/`) are the wire contract; **models**
  (`app/models/`) are the database. Routers declare a schema return type.
- `app/transforms/` is a vertical slice with its own router
  (`transforms/api.py`, mounted from `main.py`), services and dbt runtime.
  Transform code stays there.
- `app/core/` holds cross-cutting machinery: config, context, db, errors,
  logging, params, permissions, readiness, secrets, security. Import it; never
  reimplement it.

## The request shape

This is the idiom, and it is consistent across routers:

```python
@router.post("/{pipeline_id}/pause")
async def pause(pipeline_id: uuid.UUID, session: SessionDep, ctx: CtxDep) -> PipelineDetail:
    ctx.require(Module.PIPELINES, Action.OPERATE)      # authorize here
    pipeline = await pipeline_service.set_paused(...)  # mutate in the service
    await session.commit()                             # commit here
    await session.refresh(pipeline)
    return presenters.pipeline_detail(pipeline)
```

**The router owns the transaction boundary.** 74 `session.commit()` calls live
in `api/v1/`, 17 in `services/` — and those exceptions are deliberate
(`runs.py`, `outbox.py`, `catalog.py` are driven by the worker and the outbox
pattern, not by a request). A service mutates the session and returns; it does
not commit on a request path. Do not move a commit into a service to make a
call site shorter.

## Authorization and tenancy

- `CtxDep` yields a `RequestContext` (`app/core/context.py`) carrying
  `workspace_id`, `role`, `organization_id`, `org_role`, `trace_id`.
- Authorize in the **router**, with `ctx.require(Module.X, Action.Y)` (58 uses)
  or `ctx.require_org(Action.Y)` (12). `ctx.can(...)` / `ctx.can_org(...)` are
  for branching, not for gating. Never compare a role inline, and never invent
  a new check when `Module`/`Action` in `app/core/permissions.py` already has one.
- Services filter by `ctx.workspace_id`. A query without it is a cross-tenant
  read, not a convenience.
- `AdminDep` is the platform-admin dependency; use it rather than testing
  `ctx.is_platform_admin` by hand.

## Errors

`HTTPException` appears **zero** times in `api/v1/`, and that is the rule.

- Raise a **specific subclass** from `app/core/errors.py`: `ValidationError`
  (41 uses), `ForbiddenError` (16), `UnauthorizedError` (6), `NotFoundError`,
  `ConflictError`, `ResourceInUseError`, `QuotaExceededError`,
  `EngineUnavailableError`, `EngineOperationError`. Raising bare `AppError` is
  the exception, not the default.
- For a failure the UI must react to, prefer
  `error_from_matrix("PIPELINE_ALREADY_RUNNING")`. `ERROR_UX_MATRIX` holds the
  status, `ErrorCategory`, wording and the `remediation.action` string the
  frontend turns into its primary CTA. Adding a new product error means adding
  a matrix entry, not inventing wording at the call site.
- `technical_message` is for operators; `message` is user-facing. Never put an
  upstream driver string, a connection string or a credential in `message`.

## Async

Handlers and request-path service functions are `async def`. Services also hold
plenty of ordinary `def` helpers (pure mapping, validation, SQL building) — that
is fine and normal. What matters: no blocking I/O on the request path. Long work
belongs to `worker.py` / `transform_worker.py`.

## Engine access

Only `app/adapters/` imports engine specifics. Services depend on the
`IntegrationEngineAdapter` Protocol in `app/adapters/base.py` and obtain an
instance from `app/adapters/registry.py`. See `.claude/rules/engine-boundary.md`.

## Verification

- `python -m pyflakes app` must be clean, and is: the backlog of 76 findings was
  cleared on 2026-09-15. It exists because a name that stopped existing after a
  refactor shipped twice and was only found at runtime. If it goes red, you did
  that — do not add `# noqa`, which pyflakes does not read anyway. For an import
  that is load-bearing but unused locally, say so in a form the checker
  understands: `__all__` for a re-export, `importlib.import_module()` for a pure
  side effect.
- Any behaviour change needs a test in `backend/tests/`.
