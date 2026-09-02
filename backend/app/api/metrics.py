"""Prometheus exposition for the control plane.

An on-call rotation needs something to alert on, and "poll this JSON endpoint
and parse it" is not that. This renders the numbers the product already
computes in the text format every scrape target speaks, so alerting is a
Prometheus rule rather than a bespoke script.

Hand-rendered rather than pulled in through a client library. The numbers here
are all derived from a database query at scrape time — there is no in-process
counter to register, no multiprocess collector to configure, and no metric that
survives a restart. A dependency would add machinery for state this endpoint
does not have.

Deliberately outside the workspace-scoped API: metrics describe the deployment,
not a tenant. It carries no per-tenant data, and it is not part of the product
contract — the shape here may change with the deployment's needs.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from fastapi import APIRouter, Response
from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.core.logging import log_event
from app.models.enums import RunStatus
from app.models.run import PipelineRun

logger = logging.getLogger(__name__)

router = APIRouter()

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


def _render(name: str, help_text: str, kind: str, samples: list[tuple[str, float]]) -> str:
    lines = [f"# HELP {name} {help_text}", f"# TYPE {name} {kind}"]
    lines += [f"{name}{labels} {value}" for labels, value in samples]
    return "\n".join(lines)


async def _collect() -> list[str]:
    from app.adapters.registry import get_adapter
    from app.core.config import settings
    from app.models.integration import Pipeline

    blocks: list[str] = []

    # Engine reachability. The single most useful thing to page on: the control
    # plane can be perfectly healthy while nothing can actually sync.
    reachable = 0.0
    version = "unknown"
    try:
        health = await get_adapter().health()
        reachable = 1.0 if health.reachable else 0.0
        version = health.version or "unknown"
    except Exception as exc:  # noqa: BLE001 - unreachable is the datapoint
        log_event(logger, logging.WARNING, "metrics.engine_probe_failed",
                  error=str(exc)[:200])

    engine_type = (settings.engine_type or "unknown").upper()
    blocks.append(_render(
        "appbi_engine_reachable",
        "1 if the integration engine answered a health check, 0 otherwise",
        "gauge",
        [(f'{{engine_type="{engine_type}",engine_version="{version}"}}', reachable)],
    ))

    async with SessionLocal() as session:
        # Runs by status. Alert on FAILED rising, and on RUNNING stuck high.
        rows = (await session.execute(
            select(PipelineRun.status, func.count()).group_by(PipelineRun.status)
        )).all()
        by_status = {status.value: float(count) for status, count in rows}
        blocks.append(_render(
            "appbi_runs_total",
            "Pipeline runs recorded, by terminal or current status",
            "gauge",
            [(f'{{status="{name}"}}', by_status.get(name, 0.0))
             for name in sorted(s.value for s in RunStatus)],
        ))

        # Runs in flight right now, and the oldest one's age. A run that has
        # been RUNNING for hours is the shape of a stuck job, and a count alone
        # cannot show it.
        active_statuses = [s for s in RunStatus if s.is_active]
        active = (await session.execute(
            select(func.count(), func.min(PipelineRun.started_at))
            .where(PipelineRun.status.in_(active_statuses))
        )).one()
        blocks.append(_render(
            "appbi_runs_active", "Runs currently queued or executing", "gauge",
            [("", float(active[0] or 0))],
        ))

        oldest_seconds = 0.0
        if active[1] is not None:
            from app.core.db import utcnow
            oldest_seconds = max(0.0, (utcnow() - active[1]).total_seconds())
        blocks.append(_render(
            "appbi_oldest_active_run_seconds",
            "Age of the longest-running active run; 0 when nothing is running",
            "gauge", [("", oldest_seconds)],
        ))

        # Pipelines by status, so a paused-everything incident is visible.
        pipeline_rows = (await session.execute(
            select(Pipeline.status, func.count()).group_by(Pipeline.status)
        )).all()
        blocks.append(_render(
            "appbi_pipelines_total", "Pipelines by status", "gauge",
            [(f'{{status="{status.value}"}}', float(count))
             for status, count in sorted(pipeline_rows, key=lambda r: r[0].value)],
        ))

        # Engine-operation ledger. An open operation means the product and the
        # engine may disagree, and the engine side of that disagreement is a
        # resource holding customer credentials. Logging it was not enough:
        # nothing paged, and nothing showed how long it had been true.
        from app.models.outbox import EngineOperation, EngineOperationState

        ledger_rows = (await session.execute(
            select(EngineOperation.state, func.count())
            .group_by(EngineOperation.state)
        )).all()
        by_state = {state: float(count) for state, count in ledger_rows}
        blocks.append(_render(
            "appbi_engine_operations_total",
            "Engine operations by ledger state", "gauge",
            [(f'{{state="{state}"}}', by_state.get(state, 0.0))
             for state in (EngineOperationState.PENDING,
                           EngineOperationState.ENGINE_CREATED,
                           EngineOperationState.COMMITTED,
                           EngineOperationState.COMPENSATION_REQUIRED,
                           EngineOperationState.COMPENSATED,
                           EngineOperationState.FAILED)],
        ))
        blocks.append(_render(
            "appbi_engine_operations_open",
            "Engine operations not yet resolved either way", "gauge",
            [("", sum(by_state.get(state, 0.0)
                      for state in EngineOperationState.OPEN))],
        ))

        # Age of the oldest unresolved one. The count alone cannot separate
        # "three sagas in flight right now", which is healthy, from "three
        # stuck since Tuesday", which is an orphaned credential.
        oldest = (await session.execute(
            select(func.min(EngineOperation.updated_at))
            .where(EngineOperation.state.in_(EngineOperationState.OPEN))
        )).scalar()
        from app.core.db import utcnow as _utcnow

        blocks.append(_render(
            "appbi_engine_operation_oldest_open_seconds",
            "Age of the oldest unresolved engine operation; 0 when none",
            "gauge",
            [("", 0.0 if oldest is None
              else max(0.0, (_utcnow() - oldest).total_seconds()))],
        ))

        blocks.extend(await _transform_metrics(session))

    return blocks


async def _transform_metrics(session) -> list[str]:
    """The Transform queue, separately from the Pipeline one.

    Every metric above counts `PipelineRun`. A Transform worker that is down
    moves none of them, so the whole dashboard stays green while no Transform
    executes -- and the incident arrives as a customer saying their tables did
    not refresh. These are the numbers that would have paged instead.
    """
    from app.core.config import settings
    from app.core.db import utcnow
    from app.models.transform import Transform, TransformRun

    blocks: list[str] = []
    now = utcnow()

    rows = (await session.execute(
        select(TransformRun.status, func.count()).group_by(TransformRun.status)
    )).all()
    by_status = {status.value: float(count) for status, count in rows}
    blocks.append(_render(
        "appbi_transform_runs_total",
        "Transform runs recorded, by terminal or current status", "gauge",
        [(f'{{status="{name}"}}', by_status.get(name, 0.0))
         for name in sorted(s.value for s in RunStatus)],
    ))

    active_statuses = [s for s in RunStatus if s.is_active]
    active = (await session.execute(
        select(func.count(), func.min(TransformRun.started_at))
        .where(TransformRun.status.in_(active_statuses))
    )).one()
    blocks.append(_render(
        "appbi_transform_runs_active",
        "Transform runs currently starting or executing", "gauge",
        [("", float(active[0] or 0))],
    ))
    blocks.append(_render(
        "appbi_transform_oldest_active_run_seconds",
        "Age of the longest-running active Transform run; 0 when none",
        "gauge",
        [("", 0.0 if active[1] is None
          else max(0.0, (now - active[1]).total_seconds()))],
    ))

    # Queued is the one that separates "busy" from "nothing is picking work
    # up". A backlog that never drains is a worker that is gone.
    queued = (await session.execute(
        select(func.count(), func.min(TransformRun.created_at))
        .where(TransformRun.status == RunStatus.QUEUED)
    )).one()
    blocks.append(_render(
        "appbi_transform_runs_queued", "Transform runs waiting for a worker",
        "gauge", [("", float(queued[0] or 0))],
    ))
    blocks.append(_render(
        "appbi_transform_oldest_queued_seconds",
        "How long the oldest queued Transform run has been waiting; 0 when none",
        "gauge",
        [("", 0.0 if queued[1] is None
          else max(0.0, (now - queued[1]).total_seconds()))],
    ))

    # Liveness without a separate registration table: a worker that is running
    # heartbeats the run it holds, and one that has claimed nothing recently
    # cannot be distinguished from one that is down -- so an idle deployment
    # reports alive, and a deployment with work that nobody touches does not.
    recent_heartbeat = (await session.execute(
        select(func.count()).where(
            TransformRun.status.in_(active_statuses),
            TransformRun.heartbeat_at.is_not(None),
            TransformRun.heartbeat_at
            >= now - timedelta(seconds=settings.transform_stale_queue_seconds),
        )
    )).scalar() or 0
    stuck_queue = bool(queued[0]) and queued[1] is not None and (
        (now - queued[1]).total_seconds() > settings.transform_stale_queue_seconds
    )
    alive = 0.0 if stuck_queue else 1.0 if (recent_heartbeat or not active[0]) else 0.0
    blocks.append(_render(
        "appbi_transform_worker_alive",
        "1 while Transform work is moving, 0 when the queue has stalled",
        "gauge", [("", alive)],
    ))

    # Durations as a summary rather than a histogram: these are computed from a
    # query at scrape time, and there is no in-process observation to bucket.
    finished = (await session.execute(
        select(
            func.count(),
            func.avg(func.extract("epoch", TransformRun.ended_at - TransformRun.started_at)),
            func.max(func.extract("epoch", TransformRun.ended_at - TransformRun.started_at)),
        ).where(
            TransformRun.ended_at.is_not(None),
            TransformRun.started_at.is_not(None),
            TransformRun.ended_at >= now - timedelta(hours=24),
        )
    )).one()
    blocks.append(_render(
        "appbi_transform_run_duration_seconds",
        "Transform run duration over the last 24 hours", "gauge",
        [('{stat="count"}', float(finished[0] or 0)),
         ('{stat="avg"}', float(finished[1] or 0.0)),
         ('{stat="max"}', float(finished[2] or 0.0))],
    ))

    # A Transform whose health is ERROR is failing its own tests or its build.
    # A wave of them at once is an upstream change, not five unlucky models.
    health_rows = (await session.execute(
        select(Transform.health_status, func.count())
        .where(Transform.deleted_at.is_(None))
        .group_by(Transform.health_status)
    )).all()
    blocks.append(_render(
        "appbi_transforms_total", "Transforms by health", "gauge",
        [(f'{{health="{status.value}"}}', float(count))
         for status, count in sorted(health_rows, key=lambda r: r[0].value)],
    ))

    return blocks


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    try:
        body = "\n\n".join(await _collect()) + "\n"
    except Exception as exc:  # noqa: BLE001
        # A scrape endpoint that 500s takes the monitoring down with the thing
        # it monitors. Report the failure as a metric instead.
        log_event(logger, logging.ERROR, "metrics.collection_failed",
                  error=str(exc)[:300])
        body = _render("appbi_metrics_up",
                       "1 if this scrape collected cleanly, 0 if it failed",
                       "gauge", [("", 0.0)]) + "\n"
        return Response(content=body, media_type=CONTENT_TYPE)

    body += "\n" + _render("appbi_metrics_up",
                           "1 if this scrape collected cleanly, 0 if it failed",
                           "gauge", [("", 1.0)]) + "\n"
    return Response(content=body, media_type=CONTENT_TYPE)
