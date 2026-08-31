"""Unified Pipeline and Transform run endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.deps import CtxDep, SessionDep
from app.api.v1.presenters import run_detail, run_view, user_ref
from app.core.config import settings
from app.core.db import utcnow
from app.core.errors import NotFoundError, ValidationError
from app.core.permissions import Action, Module
from app.models.engine import ConnectorDefinition
from app.models.identity import User
from app.models.integration import Destination, Pipeline, Source
from app.models.run import PipelineRun
from app.models.transform import Transform, TransformRun
from app.schemas.common import ActorRef, PageInfo, Paginated
from app.schemas.domain import (
    RunAttemptView, RunDetail, RunError, RunLogPage, RunView, TransformRunNodeView,
)
from app.services import runs as run_service
from app.services import transforms as transform_service

router = APIRouter(prefix="/runs", tags=["runs"])


async def _connector(session, connector_key: str) -> ConnectorDefinition | None:
    return await session.scalar(
        select(ConnectorDefinition).where(ConnectorDefinition.connector_key == connector_key)
    )


async def _pipeline_ref(session, pipeline: Pipeline | None) -> ActorRef | None:
    if pipeline is None:
        return None
    source = await session.get(Source, pipeline.source_id)
    connector = await _connector(session, source.connector_key) if source else None
    return ActorRef(
        id=pipeline.id, name=pipeline.name,
        connector_key=source.connector_key if source else "",
        connector_display_name=connector.display_name if connector else None,
        icon=connector.icon if connector else None,
    )


def _transform_ref(transform: Transform | None) -> ActorRef | None:
    if transform is None:
        return None
    return ActorRef(
        id=transform.id, name=transform.name, connector_key="transform",
        connector_display_name="dbt Core", icon="workflow",
    )


async def _actor_ref(session, actor) -> ActorRef | None:
    if actor is None:
        return None
    connector = await _connector(session, actor.connector_key)
    return ActorRef(
        id=actor.id, name=actor.name, connector_key=actor.connector_key,
        connector_display_name=connector.display_name if connector else None,
        icon=connector.icon if connector else None,
    )


def _transform_stale(run: TransformRun) -> bool:
    if not run.status.is_active:
        return False
    reference = run.heartbeat_at or run.started_at or run.created_at
    return (utcnow() - reference).total_seconds() > settings.stale_run_seconds


def _transform_run_view(
    ctx, run: TransformRun, transform: Transform | None, user: User | None,
) -> RunView:
    duration = None
    if run.started_at:
        duration = ((run.ended_at or utcnow()) - run.started_at).total_seconds()
    error = None
    if run.error_code or run.error_summary or run.error_category:
        error = RunError(
            code=run.error_code,
            category=run.error_category.value if run.error_category else None,
            summary=run.error_summary,
            remediation_action=run.remediation_action,
            technical_message=(run.technical_metadata or {}).get("technical_message"),
        )
    can_operate = ctx.can(Module.TRANSFORMS, Action.OPERATE)
    retryable = run.status.value in {"FAILED", "FAILED_TO_START", "CANCELLED", "TIMED_OUT"}
    return RunView(
        id=run.id, short_id=str(run.id)[:8], run_type="TRANSFORM",
        transform=_transform_ref(transform), operation=run.operation,
        status=run.status.value, trigger_type=run.trigger_type.value,
        triggered_by=user_ref(user), retry_of_run_id=run.retry_of_run_id,
        queue_reason=run.queue_reason, started_at=run.started_at, ended_at=run.ended_at,
        created_at=run.created_at, duration_seconds=duration,
        models_built=run.models_built, tests_passed=run.tests_passed,
        tests_failed=run.tests_failed, tests_warned=run.tests_warned,
        rows_affected=run.rows_affected, error=error, is_stale=_transform_stale(run),
        actions={
            "can_cancel": can_operate and run.status.is_active
                          and run.status.value != "CANCEL_REQUESTED",
            "can_retry": can_operate and retryable,
            "can_view_logs": True,
        },
    )


@router.get("", response_model=Paginated[RunView])
async def list_runs(
    session: SessionDep,
    ctx: CtxDep,
    run_type: Annotated[str | None, Query(alias="type")] = None,
    pipeline_id: Annotated[uuid.UUID | None, Query()] = None,
    transform_id: Annotated[uuid.UUID | None, Query()] = None,
    status: Annotated[str | None, Query()] = None,
    trigger_type: Annotated[str | None, Query()] = None,
    error_category: Annotated[str | None, Query()] = None,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Paginated[RunView]:
    ctx.require(Module.MONITORING, Action.VIEW)
    normalized = (run_type or "ALL").upper()
    if normalized not in {"ALL", "PIPELINE", "TRANSFORM"}:
        raise ValidationError("Run type must be ALL, PIPELINE, or TRANSFORM.")
    if pipeline_id:
        normalized = "PIPELINE"
    if transform_id:
        normalized = "TRANSFORM"

    fetch_limit = limit if normalized != "ALL" else offset + limit
    fetch_offset = offset if normalized != "ALL" else 0
    pipeline_rows: list[PipelineRun] = []
    transform_rows: list[TransformRun] = []
    pipeline_total = transform_total = 0
    pipeline_summary: dict[str, int] = {}
    transform_summary: dict[str, int] = {}
    if normalized in {"ALL", "PIPELINE"}:
        pipeline_rows, pipeline_total, pipeline_summary = await run_service.list_runs(
            session, ctx, pipeline_id=pipeline_id, status=status, trigger_type=trigger_type,
            error_category=error_category, since=since, until=until,
            limit=fetch_limit, offset=fetch_offset,
        )
    if normalized in {"ALL", "TRANSFORM"}:
        transform_rows, transform_total, transform_summary = (
            await transform_service.list_runs_for_global(
                session, ctx, transform_id=transform_id, status=status,
                trigger_type=trigger_type, error_category=error_category,
                since=since, until=until, limit=fetch_limit, offset=fetch_offset,
            )
        )

    triggered_ids = {
        run.triggered_by for run in [*pipeline_rows, *transform_rows] if run.triggered_by
    }
    users = {
        user.id: user for user in (await session.scalars(
            select(User).where(User.id.in_(triggered_ids))
        )).all()
    } if triggered_ids else {}
    pipeline_cache: dict[uuid.UUID, ActorRef | None] = {}
    transform_cache: dict[uuid.UUID, Transform | None] = {}
    dated_items: list[tuple[datetime, RunView]] = []
    for run in pipeline_rows:
        if run.pipeline_id not in pipeline_cache:
            pipeline_cache[run.pipeline_id] = await _pipeline_ref(
                session, await session.get(Pipeline, run.pipeline_id),
            )
        dated_items.append((run.created_at, run_view(
            ctx, run, pipeline_ref=pipeline_cache[run.pipeline_id],
            triggered_by=users.get(run.triggered_by), is_stale=run_service.is_stale(run),
        )))
    for run in transform_rows:
        if run.transform_id not in transform_cache:
            transform_cache[run.transform_id] = await session.get(Transform, run.transform_id)
        dated_items.append((run.created_at, _transform_run_view(
            ctx, run, transform_cache[run.transform_id], users.get(run.triggered_by),
        )))
    dated_items.sort(key=lambda item: item[0], reverse=True)
    items = [item for _, item in dated_items]
    if normalized == "ALL":
        items = items[offset:offset + limit]
    total = pipeline_total + transform_total
    summary = {
        key: pipeline_summary.get(key, 0) + transform_summary.get(key, 0)
        for key in set(pipeline_summary) | set(transform_summary)
        if key != "total"
    }
    summary["total"] = total
    return Paginated[RunView](
        items=items,
        page=PageInfo(
            has_more=offset + len(items) < total, total=total, limit=limit, offset=offset,
        ),
        summary=summary,
    )


async def _find(
    session, ctx, run_id: uuid.UUID,
) -> tuple[Literal["PIPELINE", "TRANSFORM"], Any]:
    pipeline_run = await session.scalar(select(PipelineRun).where(
        PipelineRun.id == run_id, PipelineRun.workspace_id == ctx.workspace_id,
    ))
    if pipeline_run is not None:
        return "PIPELINE", pipeline_run
    transform_run = await session.scalar(select(TransformRun).where(
        TransformRun.id == run_id, TransformRun.workspace_id == ctx.workspace_id,
    ))
    if transform_run is not None:
        return "TRANSFORM", transform_run
    raise NotFoundError("Run was not found in this workspace.")


@router.get("/{run_id}", response_model=RunDetail)
async def detail(run_id: uuid.UUID, session: SessionDep, ctx: CtxDep) -> RunDetail:
    ctx.require(Module.MONITORING, Action.VIEW)
    kind, run = await _find(session, ctx, run_id)
    return await (_pipeline_detail(session, ctx, run) if kind == "PIPELINE"
                  else _transform_detail(session, ctx, run))


@router.post("/{run_id}/cancel", response_model=RunDetail)
async def cancel(run_id: uuid.UUID, session: SessionDep, ctx: CtxDep) -> RunDetail:
    kind, found = await _find(session, ctx, run_id)
    if kind == "PIPELINE":
        run = await run_service.cancel(session, ctx, run_id)
        await session.commit()
        return await _pipeline_detail(session, ctx, run)
    await transform_service.request_cancel(session, ctx, found)
    await session.commit()
    return await _transform_detail(session, ctx, found)


@router.post("/{run_id}/retry", response_model=RunDetail, status_code=202)
async def retry(run_id: uuid.UUID, session: SessionDep, ctx: CtxDep) -> RunDetail:
    kind, found = await _find(session, ctx, run_id)
    if kind == "PIPELINE":
        run = await run_service.retry(session, ctx, run_id)
        await session.commit()
        return await _pipeline_detail(session, ctx, run)
    run = await transform_service.retry_run(session, ctx, found)
    await session.commit()
    return await _transform_detail(session, ctx, run)


@router.get("/{run_id}/logs", response_model=RunLogPage)
async def logs(
    run_id: uuid.UUID,
    session: SessionDep,
    ctx: CtxDep,
    cursor: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
) -> RunLogPage:
    kind, _ = await _find(session, ctx, run_id)
    if kind == "PIPELINE":
        lines, next_cursor, has_more, total = await run_service.fetch_logs(
            session, ctx, run_id, cursor=cursor, limit=limit,
        )
    else:
        lines, next_cursor, has_more, total = await transform_service.fetch_logs(
            session, ctx, run_id, cursor=cursor, limit=limit,
        )
    return RunLogPage(
        run_id=run_id, lines=lines, next_cursor=next_cursor,
        has_more=has_more, total_lines=total,
    )


async def _pipeline_detail(session, ctx, run: PipelineRun) -> RunDetail:
    pipeline = await session.get(Pipeline, run.pipeline_id)
    user = await session.get(User, run.triggered_by) if run.triggered_by else None
    base = run_view(
        ctx, run, pipeline_ref=await _pipeline_ref(session, pipeline),
        triggered_by=user, is_stale=run_service.is_stale(run),
    )
    source = await session.get(Source, pipeline.source_id) if pipeline else None
    destination = await session.get(Destination, pipeline.destination_id) if pipeline else None
    return run_detail(
        base, run, stream_stats=await run_service.stream_stats(session, run.id),
        source_ref=await _actor_ref(session, source),
        destination_ref=await _actor_ref(session, destination),
    )


async def _transform_detail(session, ctx, run: TransformRun) -> RunDetail:
    transform = await session.get(Transform, run.transform_id)
    user = await session.get(User, run.triggered_by) if run.triggered_by else None
    destination = await session.get(Destination, transform.destination_id) if transform else None
    base = _transform_run_view(ctx, run, transform, user)
    return RunDetail(
        **base.model_dump(),
        attempts=[RunAttemptView(
            attempt_number=item.attempt_number, status=item.status.value,
            started_at=item.started_at, ended_at=item.ended_at,
            duration_seconds=(
                ((item.ended_at or utcnow()) - item.started_at).total_seconds()
                if item.started_at else None
            ),
            failure_summary=item.failure_summary,
        ) for item in run.attempts],
        destination=await _actor_ref(session, destination),
        trace_id=(run.technical_metadata or {}).get("trace_id"),
        technical_metadata=run.technical_metadata or {},
        transform_nodes=[TransformRunNodeView(
            name=node.name, resource_type=node.resource_type, status=node.status,
            execution_time=node.execution_time, relation_name=node.relation_name,
            message=node.message,
        ) for node in run.nodes],
    )
