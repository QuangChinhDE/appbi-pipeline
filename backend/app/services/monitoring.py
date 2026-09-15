"""Overview, monitoring and engine status (section 18)."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.registry import get_adapter
from app.core.config import settings
from app.core.context import RequestContext
from app.core.db import utcnow
from app.models.engine import ConnectorDefinition
from app.models.enums import (
    ACTIVE_RUN_STATUSES, HealthLevel, PipelineHealth, PipelineStatus, RunStatus,
)
from app.models.integration import Destination, Pipeline, Source
from app.models.run import PipelineRun
from app.services import pipelines as pipeline_service, scheduling


async def kpis(session: AsyncSession, workspace_id: uuid.UUID) -> dict[str, Any]:
    now = utcnow()
    day_ago = now - timedelta(hours=24)
    week_ago = now - timedelta(days=7)

    async def count(stmt) -> int:
        return await session.scalar(select(func.count()).select_from(stmt.subquery())) or 0

    active_pipelines = await count(
        select(Pipeline.id).where(
            Pipeline.workspace_id == workspace_id,
            Pipeline.status == PipelineStatus.ACTIVE,
            Pipeline.deleted_at.is_(None),
        )
    )
    running_now = await count(
        select(PipelineRun.id).where(
            PipelineRun.workspace_id == workspace_id,
            PipelineRun.status.in_(list(ACTIVE_RUN_STATUSES)),
        )
    )
    failed_24h = await count(
        select(PipelineRun.id).where(
            PipelineRun.workspace_id == workspace_id,
            PipelineRun.created_at >= day_ago,
            PipelineRun.status.in_([RunStatus.FAILED, RunStatus.FAILED_TO_START,
                                    RunStatus.TIMED_OUT]),
        )
    )
    week_runs = list((await session.scalars(
        select(PipelineRun).where(
            PipelineRun.workspace_id == workspace_id,
            PipelineRun.created_at >= week_ago,
        )
    )).all())
    terminal = [r for r in week_runs if r.status.is_terminal]
    success_rate = (
        round(sum(1 for r in terminal if r.status is RunStatus.SUCCEEDED) / len(terminal) * 100, 1)
        if terminal else None
    )
    records_24h = sum(
        r.records_synced or 0 for r in week_runs if r.created_at >= day_ago
    )

    sources_attention = await count(
        select(Source.id).where(
            Source.workspace_id == workspace_id, Source.deleted_at.is_(None),
            Source.health_status == HealthLevel.ERROR,
        )
    )
    destinations_attention = await count(
        select(Destination.id).where(
            Destination.workspace_id == workspace_id, Destination.deleted_at.is_(None),
            Destination.health_status == HealthLevel.ERROR,
        )
    )
    total_sources = await count(
        select(Source.id).where(Source.workspace_id == workspace_id, Source.deleted_at.is_(None))
    )
    total_destinations = await count(
        select(Destination.id).where(
            Destination.workspace_id == workspace_id, Destination.deleted_at.is_(None)
        )
    )
    return {
        "active_pipelines": active_pipelines,
        "running_now": running_now,
        "failed_last_24h": failed_24h,
        "success_rate_7d": success_rate,
        "sources_needing_attention": sources_attention,
        "destinations_needing_attention": destinations_attention,
        "total_sources": total_sources,
        "total_destinations": total_destinations,
        "records_synced_24h": records_24h,
    }


async def recent_runs(
    session: AsyncSession, workspace_id: uuid.UUID, *, statuses: list[RunStatus], limit: int = 5
) -> list[PipelineRun]:
    return list((await session.scalars(
        select(PipelineRun)
        .where(PipelineRun.workspace_id == workspace_id, PipelineRun.status.in_(statuses))
        .order_by(PipelineRun.created_at.desc())
        .limit(limit)
    )).all())


async def connectors_with_updates(session: AsyncSession) -> list[ConnectorDefinition]:
    rows = list((await session.scalars(select(ConnectorDefinition))).all())
    return [
        row for row in rows
        if row.latest_version and row.latest_version != row.version
    ]


async def onboarding_state(session: AsyncSession, workspace_id: uuid.UUID) -> dict[str, bool]:
    async def any_row(model) -> bool:
        return bool(await session.scalar(
            select(model.id).where(model.workspace_id == workspace_id,
                                   model.deleted_at.is_(None)).limit(1)
        ))

    has_run = bool(await session.scalar(
        select(PipelineRun.id).where(
            PipelineRun.workspace_id == workspace_id,
            PipelineRun.status == RunStatus.SUCCEEDED,
        ).limit(1)
    ))
    return {
        "has_source": await any_row(Source),
        "has_destination": await any_row(Destination),
        "has_pipeline": await any_row(Pipeline),
        "has_successful_run": has_run,
    }


async def monitoring_rows(
    session: AsyncSession, ctx: RequestContext
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    rows = list((await session.scalars(
        select(Pipeline).where(
            Pipeline.workspace_id == ctx.workspace_id, Pipeline.deleted_at.is_(None)
        ).order_by(Pipeline.name)
    )).all())
    health_map = await pipeline_service.health_for(session, ctx.workspace_id, rows)

    now = utcnow()
    out: list[dict[str, Any]] = []
    counts: dict[str, int] = {value.value.lower(): 0 for value in PipelineHealth}
    for pipeline in rows:
        health = health_map.get(pipeline.id, PipelineHealth.NEVER_RUN)
        counts[health.value.lower()] += 1
        deadline = scheduling.freshness_deadline(
            pipeline.schedule_type, pipeline.schedule_config, pipeline.last_success_at
        )
        out.append({
            "pipeline": pipeline,
            "health": health,
            "freshness_deadline": deadline,
            "freshness_breached": bool(deadline and now > deadline),
            "failure_streak": pipeline.consecutive_failures,
            "last_success_age_seconds": (
                (now - pipeline.last_success_at).total_seconds()
                if pipeline.last_success_at else None
            ),
        })
    counts["total"] = len(rows)
    return out, counts


async def engine_status(
    session: AsyncSession, ctx: RequestContext, *, detailed: bool
) -> dict[str, Any]:
    """Tenant users see a generic label; platform admins get the internals."""
    health = await get_adapter().health()
    active = await session.scalar(
        select(func.count()).select_from(PipelineRun).where(
            PipelineRun.status.in_([RunStatus.STARTING, RunStatus.RUNNING])
        )
    ) or 0
    queued = await session.scalar(
        select(func.count()).select_from(PipelineRun).where(PipelineRun.status == RunStatus.QUEUED)
    ) or 0
    oldest_heartbeat = await session.scalar(
        select(func.min(PipelineRun.heartbeat_at)).where(
            PipelineRun.status.in_([RunStatus.STARTING, RunStatus.RUNNING])
        )
    )
    lag = (utcnow() - oldest_heartbeat).total_seconds() if oldest_heartbeat else 0.0

    payload: dict[str, Any] = {
        "label": "The sync service is working normally" if health.reachable
                 else "The sync service is unavailable at the moment",
        "operational": health.reachable,
        "checked_at": health.checked_at,
        "active_runs": active,
        "queued_runs": queued,
    }
    if detailed:
        payload.update({
            "engine_type": health.engine_type.value,
            "version": health.version,
            "detail": health.detail,
            "metrics": health.metrics,
            "adapter_contract_version": settings.adapter_contract_version,
            "product_version": settings.product_version,
            "reconciliation_lag_seconds": round(lag, 1),
        })
    return payload


async def platform_facts(session: AsyncSession) -> dict[str, Any]:
    """The operational numbers the product judges the platform by.

    Deliberately engine-wide rather than per-workspace: a stalled Transform
    worker or an unresolved engine operation is not one tenant's problem, and
    `engine_status` above is already whole-installation for the same reason.
    Callers turn these into states; nothing here decides what is bad.

    `api/metrics.py` renders the same numbers as gauges. It reads them from
    here so the two cannot disagree about what "queued" or "alive" means.
    """
    # Transform lives in its own vertical slice, so the import is local -- the
    # same shape `api/metrics.py` uses, and for the same reason.
    from app.models.outbox import EngineOperation, EngineOperationState
    from app.transforms.models import TransformInvocation

    now = utcnow()
    active_statuses = [s for s in RunStatus if s.is_active]

    active_count, oldest_active = (await session.execute(
        select(func.count(), func.min(TransformInvocation.started_at))
        .where(TransformInvocation.status.in_(active_statuses))
    )).one()
    queued_count, oldest_queued = (await session.execute(
        select(func.count(), func.min(TransformInvocation.created_at))
        .where(TransformInvocation.status == RunStatus.QUEUED)
    )).one()

    # Liveness without a registration table: a worker that is running
    # heartbeats the run it holds. One that has claimed nothing recently cannot
    # be told apart from one that is down, so an idle deployment reports alive
    # and a deployment with work nobody touches does not.
    recent_heartbeat = (await session.execute(
        select(func.count()).where(
            TransformInvocation.status.in_(active_statuses),
            TransformInvocation.heartbeat_at.is_not(None),
            TransformInvocation.heartbeat_at
            >= now - timedelta(seconds=settings.transform_stale_queue_seconds),
        )
    )).scalar() or 0
    queued_age = 0.0 if oldest_queued is None else max(0.0, (now - oldest_queued).total_seconds())
    stuck_queue = bool(queued_count) and queued_age > settings.transform_stale_queue_seconds
    worker_alive = not stuck_queue and bool(recent_heartbeat or not active_count)

    open_ops, oldest_open = (await session.execute(
        select(func.count(), func.min(EngineOperation.updated_at))
        .where(EngineOperation.state.in_(EngineOperationState.OPEN))
    )).one()

    # Scheduler backlog, which is the only honest liveness signal available:
    # there is no scheduler heartbeat, but both schedulers advance
    # `next_run_at` *before* deciding whether to run, so an ACTIVE pipeline
    # still sitting in the past was never claimed. Being a little overdue is
    # normal -- the loop polls every ten seconds -- so the threshold is the
    # shortest schedule the product will accept. Past that, the scheduler has
    # missed an entire minimum interval and something is wrong with it.
    overdue_cutoff = now - timedelta(seconds=settings.min_schedule_interval_seconds)
    overdue_count, oldest_due = (await session.execute(
        select(func.count(), func.min(Pipeline.next_run_at)).where(
            Pipeline.status == PipelineStatus.ACTIVE,
            Pipeline.deleted_at.is_(None),
            Pipeline.next_run_at.is_not(None),
            Pipeline.next_run_at <= overdue_cutoff,
        )
    )).one()

    return {
        "transform_active": int(active_count or 0),
        "transform_oldest_active_seconds":
            0.0 if oldest_active is None else max(0.0, (now - oldest_active).total_seconds()),
        "transform_queued": int(queued_count or 0),
        "transform_oldest_queued_seconds": queued_age,
        "transform_worker_alive": worker_alive,
        "engine_operations_open": int(open_ops or 0),
        "engine_operation_oldest_open_seconds":
            0.0 if oldest_open is None else max(0.0, (now - oldest_open).total_seconds()),
        "scheduler_overdue": int(overdue_count or 0),
        "scheduler_oldest_overdue_seconds":
            0.0 if oldest_due is None else max(0.0, (now - oldest_due).total_seconds()),
    }
