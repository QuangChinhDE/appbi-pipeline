"""Run lifecycle: trigger, claim, execute, reconcile, cancel, retry (section 16).

Triggering never blocks on the sync. The API writes a QUEUED row and returns
202; the worker claims it, drives the engine and reconciles the terminal state
back. That is why a restart of either process cannot lose a run.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import func, or_, select, update as sa_update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.dto import EngineJobStatus, EngineSyncRequest
from app.adapters.registry import get_adapter
from app.core.config import settings
from app.core.context import RequestContext
from app.core.db import utcnow
from app.core.errors import (
    AppError, EngineResourceGoneError, EngineUnavailableError, ErrorCategory,
    NotFoundError, QuotaExceededError,
    ValidationError, error_from_matrix,
)
from app.core.logging import log_event, new_trace_id
from app.core.params import as_enum
from app.core.permissions import Action, Module
from app.models.enums import (
    ACTIVE_RUN_STATUSES, HealthLevel, PipelineStatus, RunStatus, TriggerType,
)
from app.models.integration import Destination, Pipeline, PipelineStreamStat, Source
from app.models.run import PipelineRun, RunAttempt
from app.services import actors, audit, catalog, pipelines as pipeline_service, scheduling

logger = logging.getLogger(__name__)

RETRYABLE = {RunStatus.FAILED, RunStatus.FAILED_TO_START, RunStatus.CANCELLED, RunStatus.TIMED_OUT}


# ── reads ──────────────────────────────────────────────────────────────────

async def get(session: AsyncSession, ctx: RequestContext, run_id: uuid.UUID) -> PipelineRun:
    run = await session.scalar(
        select(PipelineRun).where(
            PipelineRun.id == run_id, PipelineRun.workspace_id == ctx.workspace_id
        )
    )
    if run is None:
        raise NotFoundError("Không tìm thấy lần chạy này trong workspace.")
    return run


async def list_runs(
    session: AsyncSession,
    ctx: RequestContext,
    *,
    pipeline_id: uuid.UUID | None = None,
    status: str | None = None,
    trigger_type: str | None = None,
    error_category: str | None = None,
    since: Any | None = None,
    until: Any | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[PipelineRun], int, dict[str, int]]:
    stmt = select(PipelineRun).where(PipelineRun.workspace_id == ctx.workspace_id)
    if pipeline_id:
        stmt = stmt.where(PipelineRun.pipeline_id == pipeline_id)
    if status:
        # "ACTIVE" is a product-level shorthand for the non-terminal states.
        if status.strip().upper() == "ACTIVE":
            stmt = stmt.where(PipelineRun.status.in_(list(ACTIVE_RUN_STATUSES)))
        else:
            stmt = stmt.where(PipelineRun.status == as_enum(status, RunStatus, field="status"))
    if trigger_type:
        stmt = stmt.where(
            PipelineRun.trigger_type == as_enum(trigger_type, TriggerType, field="trigger_type")
        )
    if error_category:
        stmt = stmt.where(
            PipelineRun.error_category
            == as_enum(error_category, ErrorCategory, field="error_category")
        )
    if since:
        stmt = stmt.where(PipelineRun.created_at >= since)
    if until:
        stmt = stmt.where(PipelineRun.created_at <= until)

    total = await session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list((await session.scalars(
        stmt.order_by(PipelineRun.created_at.desc()).limit(limit).offset(offset)
    )).all())

    counts_stmt = (
        select(PipelineRun.status, func.count())
        .where(PipelineRun.workspace_id == ctx.workspace_id)
        .group_by(PipelineRun.status)
    )
    if pipeline_id:
        counts_stmt = counts_stmt.where(PipelineRun.pipeline_id == pipeline_id)
    summary = {
        status_value.value.lower(): count
        for status_value, count in (await session.execute(counts_stmt)).all()
    }
    summary["total"] = total
    return rows, total, summary


async def stream_stats(session: AsyncSession, run_id: uuid.UUID) -> list[PipelineStreamStat]:
    return list((await session.scalars(
        select(PipelineStreamStat).where(PipelineStreamStat.run_id == run_id)
        .order_by(PipelineStreamStat.stream_name)
    )).all())


def is_stale(run: PipelineRun) -> bool:
    """Possible-stuck detection (section 9.4). We flag, we never fake a status."""
    if not run.status.is_active:
        return False
    reference = run.heartbeat_at or run.started_at or run.created_at
    return (utcnow() - reference).total_seconds() > settings.stale_run_seconds


async def fetch_logs(
    session: AsyncSession, ctx: RequestContext, run_id: uuid.UUID, *, cursor: int, limit: int
) -> tuple[list[str], int | None, bool, int | None]:
    """Read a window of the run's technical log.

    The engine job handle stays inside the service layer: the route asks for
    "logs for this run", never for "logs for this engine job".
    """
    ctx.require(Module.MONITORING, Action.VIEW_DATA)
    run = await get(session, ctx, run_id)
    if not run.engine_job_ref:
        return [], None, False, 0
    try:
        result = await get_adapter().get_job_logs(run.engine_job_ref, cursor=cursor, limit=limit)
    except AppError as exc:
        log_event(logger, logging.WARNING, "run.logs_unavailable",
                  run_id=str(run.id), error=str(exc))
        return ["[engine] không đọc được log của lần chạy này."], None, False, 1
    return result.lines, result.next_cursor, result.has_more, result.total_lines


def actions_for(ctx: RequestContext, run: PipelineRun) -> dict[str, bool]:
    can_operate = ctx.can(Module.PIPELINES, Action.OPERATE)
    return {
        "can_cancel": can_operate and run.status.is_active
                      and run.status is not RunStatus.CANCEL_REQUESTED,
        "can_retry": can_operate and run.status in RETRYABLE,
        "can_view_logs": True,
    }


# ── quota ──────────────────────────────────────────────────────────────────

async def _quota_reason(session: AsyncSession, workspace_id: uuid.UUID) -> str | None:
    """Over quota means QUEUED with a reason, never a failed run (section 28.3)."""
    global_active = await session.scalar(
        select(func.count()).select_from(PipelineRun).where(
            PipelineRun.status.in_([RunStatus.STARTING, RunStatus.RUNNING])
        )
    ) or 0
    if global_active >= settings.max_concurrent_runs_global:
        return "WAITING_GLOBAL_CAPACITY"
    workspace_active = await session.scalar(
        select(func.count()).select_from(PipelineRun).where(
            PipelineRun.workspace_id == workspace_id,
            PipelineRun.status.in_([RunStatus.STARTING, RunStatus.RUNNING]),
        )
    ) or 0
    if workspace_active >= settings.max_concurrent_runs_per_workspace:
        return "WAITING_WORKSPACE_CAPACITY"
    return None


# ── trigger ────────────────────────────────────────────────────────────────

def _violated_constraint(error: IntegrityError) -> str:
    """Which unique index rejected this insert.

    asyncpg surfaces the constraint name on the wrapped error; psycopg puts it
    on `diag`. Falling back to the message text is ugly but the alternative is
    treating "someone else won the race" as an unhandled 500.
    """
    original = getattr(error, "orig", None)
    name = getattr(original, "constraint_name", None)
    if not name:
        diagnostic = getattr(original, "diag", None)
        name = getattr(diagnostic, "constraint_name", None)
    if name:
        return str(name)
    text = str(error)
    for candidate in ("uq_run_idempotency_key", "uq_pipeline_active_run"):
        if candidate in text:
            return candidate
    return ""


async def trigger(
    session: AsyncSession,
    ctx: RequestContext,
    pipeline: Pipeline,
    *,
    trigger_type: TriggerType = TriggerType.MANUAL,
    retry_of: uuid.UUID | None = None,
    idempotency_key: str | None = None,
    enforce_permission: bool = True,
) -> PipelineRun:
    if enforce_permission:
        ctx.require(Module.PIPELINES, Action.OPERATE)

    if idempotency_key:
        existing = await session.scalar(
            select(PipelineRun).where(
                PipelineRun.workspace_id == ctx.workspace_id,
                PipelineRun.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            return existing

    if pipeline.status is PipelineStatus.PAUSED:
        raise error_from_matrix("PIPELINE_PAUSED", resource_id=pipeline.id)
    if pipeline.status is PipelineStatus.NEEDS_REVIEW:
        raise error_from_matrix("PIPELINE_NEEDS_REVIEW", resource_id=pipeline.id)
    if pipeline.status in (PipelineStatus.DELETE_PENDING, PipelineStatus.DELETED):
        raise ValidationError("Pipeline đang được xóa.")

    if not [s for s in pipeline.streams if s.selected]:
        raise error_from_matrix("PIPELINE_NO_STREAM_SELECTED", resource_id=pipeline.id)

    running = await pipeline_service.active_run(session, pipeline.id)
    if running is not None:
        raise error_from_matrix(
            "PIPELINE_ALREADY_RUNNING", resource_id=running.id,
            remediation={"action": "VIEW_ACTIVE_RUN", "resource_id": str(running.id)},
        )

    run = PipelineRun(
        workspace_id=ctx.workspace_id,
        pipeline_id=pipeline.id,
        trigger_type=trigger_type,
        triggered_by=ctx.user_id,
        retry_of_run_id=retry_of,
        idempotency_key=idempotency_key,
        status=RunStatus.QUEUED,
        queue_reason=await _quota_reason(session, ctx.workspace_id),
        technical_metadata={"trace_id": ctx.trace_id},
    )
    session.add(run)
    try:
        # The checks above are the fast path and the good error messages. This
        # is the one that actually holds: production runs two API replicas, and
        # two concurrent triggers both passed those checks before either wrote.
        # The database decides who wins.
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        constraint = _violated_constraint(exc)
        if constraint == "uq_run_idempotency_key":
            # Someone else got there with the same key. Returning their run is
            # the whole contract of an idempotency key.
            duplicate = await session.scalar(
                select(PipelineRun).where(
                    PipelineRun.workspace_id == ctx.workspace_id,
                    PipelineRun.idempotency_key == idempotency_key,
                )
            )
            if duplicate is not None:
                log_event(logger, logging.INFO, "run.idempotent_replay",
                          run_id=str(duplicate.id), key=idempotency_key)
                return duplicate
        if constraint == "uq_pipeline_active_run":
            running = await pipeline_service.active_run(session, pipeline.id)
            raise error_from_matrix(
                "PIPELINE_ALREADY_RUNNING",
                resource_id=running.id if running else pipeline.id,
                remediation={"action": "VIEW_ACTIVE_RUN",
                             "resource_id": str(running.id) if running else None},
            ) from exc
        raise

    pipeline.last_run_id = run.id
    await session.flush()

    await audit.record(
        session, ctx, "pipeline.run.triggered",
        resource_type="RUN", resource_id=run.id, resource_name=pipeline.name,
        after={"pipeline_id": str(pipeline.id), "trigger": trigger_type.value,
               "retry_of": str(retry_of) if retry_of else None},
    )
    log_event(logger, logging.INFO, "run.queued", run_id=str(run.id),
              pipeline_id=str(pipeline.id), trigger=trigger_type.value)
    return run


async def cancel(session: AsyncSession, ctx: RequestContext, run_id: uuid.UUID) -> PipelineRun:
    """Idempotent: cancelling a terminal run returns its state, not an error."""
    ctx.require(Module.PIPELINES, Action.OPERATE)
    run = await get(session, ctx, run_id)
    if run.status.is_terminal:
        return run
    if run.status is RunStatus.QUEUED:
        run.status = RunStatus.CANCELLED
        run.ended_at = utcnow()
        run.error_category = ErrorCategory.CANCELLED
        run.error_code = "RUN_CANCELLED"
        run.error_summary = "Lần chạy bị hủy trước khi bắt đầu."
    else:
        run.status = RunStatus.CANCEL_REQUESTED
        if run.engine_job_ref:
            try:
                await get_adapter().cancel_job(run.engine_job_ref)
            except AppError as exc:
                log_event(logger, logging.WARNING, "run.cancel_engine_failed",
                          run_id=str(run.id), error=str(exc))
    await session.flush()
    await audit.record(
        session, ctx, "pipeline.run.cancel_requested",
        resource_type="RUN", resource_id=run.id,
    )
    return run


async def retry(session: AsyncSession, ctx: RequestContext, run_id: uuid.UUID) -> PipelineRun:
    """A retry is always a NEW run; history is never rewritten (section 16.6)."""
    ctx.require(Module.PIPELINES, Action.OPERATE)
    original = await get(session, ctx, run_id)
    if original.status not in RETRYABLE:
        raise ValidationError(
            "Chỉ có thể chạy lại những lần chạy đã kết thúc ở trạng thái thất bại hoặc bị hủy.",
            code="RUN_NOT_RETRYABLE",
        )
    pipeline = await pipeline_service.get(session, ctx, original.pipeline_id)
    return await trigger(session, ctx, pipeline, trigger_type=TriggerType.RETRY, retry_of=original.id)


# ── worker side: claim + execute ───────────────────────────────────────────

async def claim_next(session: AsyncSession, worker_id: str) -> PipelineRun | None:
    """Atomically take one QUEUED run. SKIP LOCKED means N workers can race."""
    candidate = await session.scalar(
        select(PipelineRun)
        .where(PipelineRun.status == RunStatus.QUEUED)
        .order_by(PipelineRun.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if candidate is None:
        return None

    reason = await _quota_reason(session, candidate.workspace_id)
    if reason is not None:
        candidate.queue_reason = reason
        await session.commit()
        return None

    candidate.status = RunStatus.STARTING
    candidate.queue_reason = None
    candidate.claimed_by = worker_id
    candidate.started_at = utcnow()
    candidate.heartbeat_at = utcnow()
    candidate.attempt_count += 1
    session.add(RunAttempt(
        run_id=candidate.id, attempt_number=candidate.attempt_count,
        status=RunStatus.STARTING, started_at=utcnow(),
    ))
    await session.commit()
    return candidate


async def build_sync_request(session: AsyncSession, run: PipelineRun) -> EngineSyncRequest:
    """Resolve everything the engine needs, including decrypted credentials."""
    pipeline = await session.get(Pipeline, run.pipeline_id)
    if pipeline is None:
        raise NotFoundError("Pipeline đã bị xóa.")
    source = await session.get(Source, pipeline.source_id)
    destination = await session.get(Destination, pipeline.destination_id)
    if source is None or destination is None:
        raise NotFoundError("Source hoặc destination của pipeline không còn tồn tại.")

    source_connector = await catalog.get_connector(session, source.connector_key)
    destination_connector = await catalog.get_connector(session, destination.connector_key)

    source_config = catalog.apply_spec_defaults(
        source_connector.spec_schema, await actors.resolve_configuration(session, source)
    )
    destination_config = catalog.apply_spec_defaults(
        destination_connector.spec_schema,
        await actors.resolve_configuration(session, destination),
    )
    # Every sync opens a new generation so an overwriting destination knows it
    # may drop the previous one; appending destinations keep them all.
    pipeline.generation_id += 1
    pipeline.sync_counter += 1
    await session.flush()

    return EngineSyncRequest(
        workspace_id=run.workspace_id,
        pipeline_id=pipeline.id,
        run_id=run.id,
        connection_ref=await pipeline_service.connection_ref(session, pipeline.id) or "",
        source=catalog.descriptor(source_connector),
        destination=catalog.descriptor(destination_connector),
        source_config=source_config,
        destination_config=destination_config,
        streams=pipeline_service.configured_streams(pipeline),
        state=pipeline.sync_state,
        generation_id=pipeline.generation_id,
        sync_id=pipeline.sync_counter,
        timeout_seconds=settings.run_timeout_seconds,
        log_path=f"{settings.engine_log_dir}/run-{run.id}.log",
    )


async def mark_started(session: AsyncSession, run: PipelineRun, engine_ref: str) -> None:
    run.engine_job_ref = engine_ref
    run.status = RunStatus.RUNNING
    run.heartbeat_at = utcnow()
    attempt = await _current_attempt(session, run)
    if attempt is not None:
        attempt.status = RunStatus.RUNNING
        attempt.engine_attempt_ref = engine_ref
        attempt.log_path = f"{settings.engine_log_dir}/run-{run.id}.log"
    await session.commit()


async def mark_failed_to_start(session: AsyncSession, run: PipelineRun, error: Exception) -> None:
    from app.adapters.error_mapper import classify

    failure = classify(str(error), default_category=ErrorCategory.ENGINE)
    run.status = RunStatus.FAILED_TO_START
    run.ended_at = utcnow()
    run.error_code = failure.code
    run.error_category = failure.category
    run.error_summary = failure.summary
    run.error_fingerprint = failure.fingerprint
    run.remediation_action = failure.remediation_action
    attempt = await _current_attempt(session, run)
    if attempt is not None:
        attempt.status = RunStatus.FAILED_TO_START
        attempt.ended_at = utcnow()
        attempt.failure_summary = failure.summary
    await _apply_pipeline_outcome(session, run)
    await session.commit()


async def apply_engine_status(
    session: AsyncSession, run: PipelineRun, status: EngineJobStatus
) -> bool:
    """Fold an engine snapshot into the product run. Returns True when terminal."""
    run.heartbeat_at = utcnow()
    if status.records_synced is not None:
        run.records_synced = status.records_synced
    if status.bytes_synced is not None:
        run.bytes_synced = status.bytes_synced
    if status.raw_status:
        run.technical_metadata = {**(run.technical_metadata or {}),
                                  "engine_status": status.raw_status}

    # A cancel request stays visible until the engine actually confirms.
    if run.status is RunStatus.CANCEL_REQUESTED and not status.status.is_terminal:
        await session.commit()
        return False

    if not status.status.is_terminal:
        if run.status is not RunStatus.RUNNING:
            run.status = status.status
        await session.commit()
        return False

    run.status = status.status
    run.ended_at = status.ended_at or utcnow()
    if status.failure is not None:
        run.error_code = status.failure.code
        run.error_category = status.failure.category
        run.error_summary = status.failure.summary
        run.error_fingerprint = status.failure.fingerprint
        run.remediation_action = status.failure.remediation_action
        run.technical_metadata = {
            **(run.technical_metadata or {}),
            "technical_message": (status.failure.technical_message or "")[:4000],
        }

    attempt = await _current_attempt(session, run)
    if attempt is not None:
        attempt.status = status.status
        attempt.ended_at = run.ended_at
        attempt.records_synced = run.records_synced
        attempt.bytes_synced = run.bytes_synced
        attempt.failure_summary = status.failure.summary if status.failure else None

    for stat in status.stream_stats:
        session.add(PipelineStreamStat(
            run_id=run.id, namespace=stat.namespace, stream_name=stat.stream_name,
            records_emitted=stat.records_emitted, bytes_emitted=stat.bytes_emitted,
            status=stat.status,
        ))

    await _apply_pipeline_outcome(session, run, state=status.state)
    await session.commit()
    log_event(logger, logging.INFO, "run.terminal", run_id=str(run.id),
              status=run.status.value, records=run.records_synced)
    return True


async def _apply_pipeline_outcome(
    session: AsyncSession, run: PipelineRun, *, state: Any | None = None
) -> None:
    pipeline = await session.get(Pipeline, run.pipeline_id)
    if pipeline is None:
        return
    pipeline.last_run_id = run.id
    if run.status is RunStatus.SUCCEEDED:
        pipeline.last_success_at = run.ended_at or utcnow()
        pipeline.consecutive_failures = 0
    elif run.status in (RunStatus.FAILED, RunStatus.FAILED_TO_START, RunStatus.TIMED_OUT):
        pipeline.consecutive_failures += 1

    # Persist destination-committed state so the next incremental run resumes.
    if state:
        pipeline.sync_state = state

    if pipeline.status is PipelineStatus.ACTIVE:
        pipeline.next_run_at = scheduling.next_run_at(
            pipeline.schedule_type, pipeline.schedule_config, pipeline.timezone
        )

    # Push an auth failure onto the source's health so the Sources page shows it.
    if run.error_category is ErrorCategory.AUTHENTICATION:
        source = await session.get(Source, pipeline.source_id)
        if source is not None:
            source.health_status = HealthLevel.ERROR
            source.health_code = run.error_code
            source.health_message = run.error_summary
    elif run.error_category is ErrorCategory.DESTINATION_WRITE:
        destination = await session.get(Destination, pipeline.destination_id)
        if destination is not None:
            destination.health_status = HealthLevel.ERROR
            destination.health_code = run.error_code
            destination.health_message = run.error_summary


async def _current_attempt(session: AsyncSession, run: PipelineRun) -> RunAttempt | None:
    return await session.scalar(
        select(RunAttempt).where(RunAttempt.run_id == run.id)
        .order_by(RunAttempt.attempt_number.desc()).limit(1)
    )


async def enforce_timeouts(session: AsyncSession) -> dict[str, int]:
    """Stop runs that have outrun `RUN_TIMEOUT_SECONDS`, on the engine as well.

    Timeout ownership was undefined. `EngineSyncRequest.timeout_seconds` is set
    on every request and only the embedded runner honours it; the Airbyte API
    adapter passes it nowhere, because Airbyte has no per-job deadline to pass
    it to. So a sync that hangs -- a source that stops producing, a network
    path that half-closes -- stayed RUNNING forever, held the pipeline's one
    active-run slot, and nothing ever told anyone.

    The product owns the deadline, so the product enforces it. That means two
    things in order, and the order is the point:

    1. Ask the engine to cancel. Marking the run terminal without doing this
       leaves an Airbyte job running against the destination while the product
       believes nothing is in flight -- the same duplicate-write shape that
       worker recovery had.
    2. Only then move the run to TIMED_OUT.

    If the engine cannot be reached, the run is left alone and retried on the
    next pass. A timeout is not urgent enough to justify lying about state.
    """
    from app.adapters.registry import get_adapter

    counts = {"timed_out": 0, "deferred": 0}
    deadline = utcnow() - timedelta(seconds=settings.run_timeout_seconds)

    overdue = list((await session.scalars(
        select(PipelineRun).where(
            PipelineRun.status.in_([RunStatus.STARTING, RunStatus.RUNNING,
                                    RunStatus.CANCEL_REQUESTED]),
            # `started_at` rather than `created_at`: time spent queued behind a
            # concurrency limit is not the sync running long.
            PipelineRun.started_at.is_not(None),
            PipelineRun.started_at < deadline,
        )
    )).all())

    adapter = get_adapter()
    for run in overdue:
        if run.engine_job_ref:
            try:
                await adapter.cancel_job(run.engine_job_ref)
            except EngineResourceGoneError:
                # Already gone from the engine's side. Nothing to cancel, and
                # the run is safe to close.
                pass
            except AppError as exc:
                counts["deferred"] += 1
                log_event(logger, logging.WARNING, "run.timeout_deferred",
                          run_id=str(run.id), error=str(exc)[:200],
                          detail="could not cancel the engine job, so the run "
                                 "is left active rather than marked terminal "
                                 "while the engine may still be writing")
                continue

        run.status = RunStatus.TIMED_OUT
        run.ended_at = utcnow()
        run.error_category = ErrorCategory.ENGINE
        run.error_code = "RUN_TIMEOUT"
        run.error_summary = (
            f"Lần chạy vượt quá giới hạn {settings.run_timeout_seconds} giây "
            "và đã được hủy trên engine.")
        run.remediation_action = "RETRY_RUN"
        counts["timed_out"] += 1
        log_event(logger, logging.WARNING, "run.timed_out", run_id=str(run.id),
                  limit_seconds=settings.run_timeout_seconds)

    if counts["timed_out"] or counts["deferred"]:
        await session.commit()
    return counts


async def recover_orphans(session: AsyncSession, worker_id: str) -> dict[str, int]:
    """Decide what a worker restart actually means for the runs it owned.

    The previous version failed every active run whose `claimed_by` matched
    this worker. In a container restart the hostname is unchanged and the
    process is PID 1 again, so `WORKER_ID` is identical -- which meant a
    restart marked a perfectly healthy Airbyte job FAILED. Airbyte kept
    running and kept writing; the user saw a failure and retried, and the
    retry started a second job against the same destination.

    Ownership is the wrong question. The right one is: does the engine still
    have this job? So that is what this asks.

    Three outcomes, and the distinction between the last two is the point:

    - **adopted**  -- the engine still has the job. Ownership is released and
      the reconciler picks it up on its next pass. Nothing is failed.
    - **lost**     -- the run never reached the engine (no `engine_job_ref`)
      and its lease has expired. There is nothing running, so failing it is
      honest and there is nothing to duplicate.
    - **deferred** -- the engine could not be reached. Nothing is decided.
      Reporting these as lost is how one engine restart turns into a wave of
      spurious failures and user-initiated duplicate syncs.
    """
    from app.adapters.registry import get_adapter

    stale_before = utcnow() - timedelta(seconds=settings.stale_run_seconds)
    counts = {"adopted": 0, "lost": 0, "deferred": 0}

    candidates = list((await session.scalars(
        select(PipelineRun).where(
            PipelineRun.status.in_([RunStatus.STARTING, RunStatus.RUNNING,
                                    RunStatus.CANCEL_REQUESTED]),
            or_(PipelineRun.claimed_by == worker_id,
                PipelineRun.heartbeat_at < stale_before,
                PipelineRun.heartbeat_at.is_(None)),
        )
    )).all())

    adapter = get_adapter()
    for run in candidates:
        if run.engine_job_ref:
            # Ask rather than assume. An embedded run really is gone after a
            # restart and the engine says so; an Airbyte job is not, and the
            # engine says that too. One question, correct for both.
            try:
                await adapter.get_job(run.engine_job_ref)
            except EngineResourceGoneError:
                # The only answer that means absence. Everything else below is
                # the engine failing to answer, which is a different thing.
                _mark_lost(run)
                counts["lost"] += 1
                continue
            except AppError as exc:
                # 401/403 (rotated credential), 429 (rate limited), 5xx, a
                # transport error, a malformed request -- none of these say the
                # job is gone. Treating them as absence marked live Airbyte
                # jobs FAILED while Airbyte kept writing, and users retried
                # into a second job against the same destination.
                counts["deferred"] += 1
                log_event(logger, logging.WARNING, "worker.recovery_deferred",
                          run_id=str(run.id), reason=getattr(exc, "code", ""),
                          detail="the engine did not confirm this job is gone; "
                                 "the run stays active for the reconciler "
                                 "rather than being failed")
                continue

            # Release ownership so this worker's claim does not block the
            # reconciler, and leave the status alone.
            run.claimed_by = None
            counts["adopted"] += 1
            log_event(logger, logging.INFO, "worker.run_adopted",
                      run_id=str(run.id),
                      detail="the engine still has this job; reconciling rather "
                             "than failing")
            continue

        # No engine reference. The run never got as far as starting anything,
        # so there is nothing to duplicate -- but only once its lease is up,
        # because a run being claimed right now also has no ref yet.
        if run.heartbeat_at is None or run.heartbeat_at < stale_before:
            _mark_lost(run)
            counts["lost"] += 1
        else:
            counts["deferred"] += 1

    await session.commit()
    return counts


def _mark_lost(run: PipelineRun) -> None:
    run.status = RunStatus.FAILED
    run.ended_at = utcnow()
    run.error_code = "ENGINE_JOB_LOST"
    run.error_category = ErrorCategory.ENGINE
    run.error_summary = "Tiến trình đồng bộ bị gián đoạn (engine hoặc worker khởi động lại)."
    run.remediation_action = "RETRY_RUN"


async def fail_orphans(session: AsyncSession, worker_id: str) -> int:
    """Kept for callers that only want a count. Delegates; does not duplicate."""
    counts = await recover_orphans(session, worker_id)
    return counts["lost"]
