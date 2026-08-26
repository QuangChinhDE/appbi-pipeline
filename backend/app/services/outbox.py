"""Driving the engine-operation ledger.

Every function here uses its **own** session and commits immediately. That is
the entire point and it is worth being explicit about, because sharing the
request's session would defeat it: the ledger has to be durable at moments when
the request transaction is precisely what is about to be rolled back.

So the sequence around an engine call is:

    begin()            own transaction, committed   -- intent is now durable
    <engine call>
    engine_created()   own transaction, committed   -- outcome is now durable
    <product DB work, then the request commits>
    committed()        own transaction, committed   -- saga closed

If the process dies anywhere in the middle, the ledger says where it was. If
the request transaction rolls back after the engine call, the row sits in
`ENGINE_CREATED` and `sweep()` compensates it. Nothing is left to a comment
about what should happen.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select

from app.core.db import SessionLocal, utcnow
from app.core.logging import log_event
from app.models.outbox import EngineOperation, EngineOperationState

logger = logging.getLogger(__name__)

#: How long an operation may sit unfinished before it is treated as abandoned.
#: Long enough that a slow engine call is not swept out from under itself,
#: short enough that an orphaned credential does not sit for an hour.
STALE_SECONDS = 120
#: Give up compensating after this many tries and ask for a human. Retrying
#: forever turns one stuck resource into an unbounded log of the same error.
MAX_COMPENSATION_ATTEMPTS = 8


async def begin(workspace_id: uuid.UUID, resource_type: str, operation: str,
                product_resource_id: uuid.UUID, detail: dict[str, Any] | None = None
                ) -> tuple[uuid.UUID, bool]:
    """Record the intent to change engine state, and commit it.

    Returns `(operation_id, is_retry)`.

    The second value matters, and used to be missing. Reusing the ledger row on
    a retry makes the *ledger* idempotent; it does nothing about the engine.
    Airbyte's `sources/create` has no idempotency key, so calling it twice
    creates two sources -- and the caller needs to know to look before it
    leaps. A test double that minted refs from the product id hid this
    completely.
    """
    now = utcnow()
    async with SessionLocal() as session:
        existing = await session.scalar(select(EngineOperation).where(
            EngineOperation.product_resource_id == product_resource_id,
            EngineOperation.operation == operation))
        if existing is not None:
            existing.state = EngineOperationState.PENDING
            existing.attempts += 1
            existing.updated_at = now
            if detail:
                existing.detail = {**(existing.detail or {}), **detail}
            await session.commit()
            return existing.id, True

        row = EngineOperation(
            workspace_id=workspace_id, resource_type=resource_type,
            operation=operation, product_resource_id=product_resource_id,
            state=EngineOperationState.PENDING, detail=detail or {},
            created_at=now, updated_at=now)
        session.add(row)
        await session.commit()
        return row.id, False


def _as_ref(engine_ref: Any) -> str | None:
    """Adapters return an `EngineResourceRef`; this column stores the string.

    Coerced here rather than at each call site, because getting it wrong makes
    the ledger write fail *after* the engine has already created the resource
    -- turning the mechanism that prevents orphans into one that causes them.
    """
    if engine_ref is None:
        return None
    inner = getattr(engine_ref, "ref", engine_ref)
    return str(inner)[:255]


async def engine_created(operation_id: uuid.UUID, engine_ref: Any) -> None:
    """The engine did the work. Record what it returned, before anything else.

    Between this line and the request's commit is the window the whole ledger
    exists for.
    """
    async with SessionLocal() as session:
        row = await session.get(EngineOperation, operation_id)
        if row is None:
            return
        row.state = EngineOperationState.ENGINE_CREATED
        row.engine_ref = _as_ref(engine_ref)
        row.updated_at = utcnow()
        await session.commit()


async def committed(operation_id: uuid.UUID) -> None:
    """Both sides agree. Close the saga."""
    async with SessionLocal() as session:
        row = await session.get(EngineOperation, operation_id)
        if row is None:
            return
        row.state = EngineOperationState.COMMITTED
        row.updated_at = utcnow()
        await session.commit()


async def failed(operation_id: uuid.UUID, error: str) -> None:
    """The engine call itself failed, so there is nothing on the engine.

    Distinct from compensation: no engine resource was created, so the row is
    closed rather than swept.
    """
    async with SessionLocal() as session:
        row = await session.get(EngineOperation, operation_id)
        if row is None:
            return
        row.state = EngineOperationState.COMPENSATED
        row.last_error = error[:2000]
        row.updated_at = utcnow()
        await session.commit()


def close_on_commit(session, operation_id: uuid.UUID) -> None:
    """Close the saga only once the Product DB transaction has actually landed.

    Closing it any earlier would be a lie: the row would claim both systems
    agree while the product half was still uncommitted and could yet roll back,
    which is precisely the failure this ledger exists to catch. So the write
    hangs off SQLAlchemy's `after_commit`.

    Best effort by design. If this update is lost the row simply stays open,
    the sweeper finds it, sees the product row present, and closes it then --
    the recovery path and the happy path converge on the same answer.
    """
    import asyncio

    from sqlalchemy import event

    sync_session = getattr(session, "sync_session", session)
    loop = asyncio.get_running_loop()

    def _after_commit(_sync_session) -> None:
        # Deferred with `call_soon` rather than run inline. This handler fires
        # inside SQLAlchemy's commit dispatch, and doing anything there that
        # re-enters the session leaves its state machine mid-transition -- the
        # visible symptom was the request's own cleanup raising "this session
        # is in 'committed' state" and turning a successful create into a 500.
        loop.call_soon(lambda: loop.create_task(committed(operation_id)))

    # `once=True` so SQLAlchemy removes the listener itself. Removing it from
    # inside the handler mutates the listener collection mid-dispatch, which is
    # what caused that same 500.
    event.listen(sync_session, "after_commit", _after_commit, once=True)


async def open_operations() -> list[EngineOperation]:
    async with SessionLocal() as session:
        return list((await session.scalars(
            select(EngineOperation)
            .where(EngineOperation.state.in_(EngineOperationState.OPEN))
            .order_by(EngineOperation.updated_at)
        )).all())


async def sweep(adapter, *, stale_seconds: int = STALE_SECONDS) -> dict[str, int]:
    """Find abandoned operations and put the two systems back in agreement.

    An operation still open past the SLO means the request that started it is
    gone -- the process died, the transaction rolled back, the connection
    dropped. Two cases:

    * `ENGINE_CREATED`: the engine holds a resource the Product DB never
      recorded. If the product row genuinely does not exist, delete the engine
      resource. If it does exist, the request actually did commit and the
      ledger simply never got its final update, so close it.
    * `PENDING`: we may or may not have reached the engine. The delete is
      addressed by the product resource id, so issuing it is safe either way --
      it removes the resource if it exists and is a no-op if it does not.

    Returns counts, so the caller can log and alert on them.
    """
    from datetime import timedelta

    from app.models.integration import Destination, Pipeline, Source

    cutoff = utcnow() - timedelta(seconds=stale_seconds)
    counts = {"checked": 0, "compensated": 0, "closed": 0, "failed": 0}

    models = {"SOURCE": Source, "DESTINATION": Destination, "PIPELINE": Pipeline}
    # The adapter deletes by kind, not through one generic entry point.
    deleters = {
        "SOURCE": adapter.delete_source,
        "DESTINATION": adapter.delete_destination,
        "PIPELINE": adapter.delete_connection,
    }

    async with SessionLocal() as session:
        # `FOR UPDATE SKIP LOCKED`, because this loop runs in every worker.
        #
        # Without it two workers read the same stale row and both issue the
        # delete. One succeeds; the other gets a 404 it records as a
        # compensation failure and retries until it gives up -- an operation
        # escalated to FAILED when nothing was ever wrong, and a page for a
        # human at the end of it. Skipping locked rows means the second worker
        # simply moves on to different work, which is what you want from a
        # queue anyway.
        stale = list((await session.scalars(
            select(EngineOperation).where(
                EngineOperation.state.in_(EngineOperationState.OPEN),
                EngineOperation.updated_at < cutoff,
            ).order_by(EngineOperation.updated_at).limit(100)
            .with_for_update(skip_locked=True)
        )).all())

        for row in stale:
            counts["checked"] += 1
            model = models.get(row.resource_type)
            if model is None:
                continue

            # Did the product row make it after all? If so the request
            # committed and only the final ledger update was lost.
            product_row = await session.get(model, row.product_resource_id)
            if product_row is not None and product_row.deleted_at is None:
                row.state = EngineOperationState.COMMITTED
                row.updated_at = utcnow()
                counts["closed"] += 1
                continue

            # No product row. Anything on the engine is an orphan holding
            # customer credentials.
            row.state = EngineOperationState.COMPENSATION_REQUIRED
            row.attempts += 1
            row.updated_at = utcnow()

            if row.attempts > MAX_COMPENSATION_ATTEMPTS:
                row.state = EngineOperationState.FAILED
                counts["failed"] += 1
                log_event(logger, logging.ERROR, "outbox.compensation_exhausted",
                          operation_id=str(row.id),
                          resource_type=row.resource_type,
                          product_resource_id=str(row.product_resource_id),
                          attempts=row.attempts, last_error=row.last_error)
                continue

            try:
                ref = row.engine_ref
                if not ref:
                    # The crash window: the engine answered and the process
                    # died before the reference was recorded.
                    #
                    # There is no id to delete by. Airbyte generates its own
                    # `sourceId` and the Config API has no external-id field,
                    # so deleting by the product's UUID would address nothing
                    # and silently leave the orphan in place -- which is what
                    # this code used to do, and what the fake engine in the
                    # tests obligingly made work.
                    #
                    # The adapter embeds the product id in the resource name on
                    # create, so it can be found again by listing. A `None`
                    # here is a real answer: the create never landed, and there
                    # is nothing to compensate.
                    finder = getattr(adapter, "find_by_product_id", None)
                    if finder is None:
                        # An adapter that cannot answer this cannot be
                        # compensated safely. Escalate rather than issue a
                        # delete against an id that means nothing to it.
                        row.state = EngineOperationState.FAILED
                        row.last_error = (
                            "adapter cannot look up a resource by product id, so "
                            "an orphan here needs manual cleanup")
                        row.updated_at = utcnow()
                        counts["failed"] += 1
                        log_event(logger, logging.ERROR, "outbox.no_lookup",
                                  operation_id=str(row.id),
                                  product_resource_id=str(row.product_resource_id))
                        continue
                    ref = await finder(
                        row.resource_type, str(row.product_resource_id))
                    if ref:
                        row.engine_ref = ref
                    else:
                        row.state = EngineOperationState.COMPENSATED
                        row.last_error = None
                        row.updated_at = utcnow()
                        counts["compensated"] += 1
                        log_event(logger, logging.INFO, "outbox.nothing_to_compensate",
                                  operation_id=str(row.id),
                                  product_resource_id=str(row.product_resource_id))
                        continue
                await deleters[row.resource_type](ref)
                row.state = EngineOperationState.COMPENSATED
                row.last_error = None
                counts["compensated"] += 1
                log_event(logger, logging.WARNING, "outbox.compensated",
                          operation_id=str(row.id),
                          resource_type=row.resource_type,
                          product_resource_id=str(row.product_resource_id),
                          engine_ref=row.engine_ref)
            except Exception as exc:                       # noqa: BLE001
                # Left in COMPENSATION_REQUIRED so the next sweep tries again.
                row.last_error = str(exc)[:2000]
                log_event(logger, logging.ERROR, "outbox.compensation_failed",
                          operation_id=str(row.id), attempts=row.attempts,
                          error=str(exc)[:200])
            row.updated_at = utcnow()

        await session.commit()
    return counts


async def overdue(stale_seconds: int = STALE_SECONDS) -> list[EngineOperation]:
    """Operations that are past the SLO and still not resolved.

    This is what an alert should fire on: not that compensation happened --
    that is the system working -- but that something has been stuck through
    several sweeps and is not getting better.
    """
    from datetime import timedelta

    cutoff = utcnow() - timedelta(seconds=stale_seconds * 3)
    async with SessionLocal() as session:
        return list((await session.scalars(
            select(EngineOperation).where(
                EngineOperation.state.in_(
                    (EngineOperationState.COMPENSATION_REQUIRED,
                     EngineOperationState.FAILED)),
                EngineOperation.updated_at < cutoff,
            ).order_by(EngineOperation.updated_at)
        )).all())
