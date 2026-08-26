"""Run endpoints (section 23.6)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.deps import CtxDep, SessionDep
from app.api.v1.presenters import run_detail, run_view
from app.core.permissions import Action, Module
from app.models.engine import ConnectorDefinition
from app.models.identity import User
from app.models.integration import Destination, Pipeline, Source
from app.models.run import PipelineRun
from app.schemas.common import ActorRef, PageInfo, Paginated
from app.schemas.domain import RunDetail, RunLogPage, RunView
from app.services import runs as run_service

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


async def _actor_ref(session, actor) -> ActorRef | None:
    if actor is None:
        return None
    connector = await _connector(session, actor.connector_key)
    return ActorRef(
        id=actor.id, name=actor.name, connector_key=actor.connector_key,
        connector_display_name=connector.display_name if connector else None,
        icon=connector.icon if connector else None,
    )


@router.get("", response_model=Paginated[RunView])
async def list_runs(
    session: SessionDep,
    ctx: CtxDep,
    pipeline_id: Annotated[uuid.UUID | None, Query()] = None,
    status: Annotated[str | None, Query()] = None,
    trigger_type: Annotated[str | None, Query()] = None,
    error_category: Annotated[str | None, Query()] = None,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Paginated[RunView]:
    ctx.require(Module.MONITORING, Action.VIEW)
    rows, total, summary = await run_service.list_runs(
        session, ctx, pipeline_id=pipeline_id, status=status, trigger_type=trigger_type,
        error_category=error_category, since=since, until=until, limit=limit, offset=offset,
    )
    # The pipeline was already cached per page; the user was not, so a page of
    # runs triggered by different people issued one query each.
    triggered_ids = {run.triggered_by for run in rows if run.triggered_by}
    users = {
        user.id: user
        for user in (await session.scalars(
            select(User).where(User.id.in_(triggered_ids)))).all()
    } if triggered_ids else {}

    pipeline_cache: dict[uuid.UUID, ActorRef | None] = {}
    items: list[RunView] = []
    for run in rows:
        if run.pipeline_id not in pipeline_cache:
            pipeline_cache[run.pipeline_id] = await _pipeline_ref(
                session, await session.get(Pipeline, run.pipeline_id)
            )
        items.append(run_view(ctx, run, pipeline_ref=pipeline_cache[run.pipeline_id],
                              triggered_by=users.get(run.triggered_by),
                              is_stale=run_service.is_stale(run)))
    return Paginated[RunView](
        items=items,
        page=PageInfo(has_more=offset + len(items) < total, total=total,
                      limit=limit, offset=offset),
        summary=summary,
    )


@router.get("/{run_id}", response_model=RunDetail)
async def detail(run_id: uuid.UUID, session: SessionDep, ctx: CtxDep) -> RunDetail:
    ctx.require(Module.MONITORING, Action.VIEW)
    run = await run_service.get(session, ctx, run_id)
    return await _detail(session, ctx, run)


@router.post("/{run_id}/cancel", response_model=RunDetail)
async def cancel(run_id: uuid.UUID, session: SessionDep, ctx: CtxDep) -> RunDetail:
    run = await run_service.cancel(session, ctx, run_id)
    await session.commit()
    return await _detail(session, ctx, run)


@router.post("/{run_id}/retry", response_model=RunDetail, status_code=202)
async def retry(run_id: uuid.UUID, session: SessionDep, ctx: CtxDep) -> RunDetail:
    run = await run_service.retry(session, ctx, run_id)
    await session.commit()
    return await _detail(session, ctx, run)


@router.get("/{run_id}/logs", response_model=RunLogPage)
async def logs(
    run_id: uuid.UUID,
    session: SessionDep,
    ctx: CtxDep,
    cursor: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
) -> RunLogPage:
    """Chunked: a multi-hundred-MB connector log must never land in the browser
    in one response (section 33.6)."""
    lines, next_cursor, has_more, total = await run_service.fetch_logs(
        session, ctx, run_id, cursor=cursor, limit=limit
    )
    return RunLogPage(
        run_id=run_id, lines=lines, next_cursor=next_cursor,
        has_more=has_more, total_lines=total,
    )


async def _detail(session, ctx, run: PipelineRun) -> RunDetail:
    pipeline = await session.get(Pipeline, run.pipeline_id)
    user = await session.get(User, run.triggered_by) if run.triggered_by else None
    base = run_view(ctx, run, pipeline_ref=await _pipeline_ref(session, pipeline),
                    triggered_by=user, is_stale=run_service.is_stale(run))
    source = await session.get(Source, pipeline.source_id) if pipeline else None
    destination = await session.get(Destination, pipeline.destination_id) if pipeline else None
    return run_detail(
        base, run,
        stream_stats=await run_service.stream_stats(session, run.id),
        source_ref=await _actor_ref(session, source),
        destination_ref=await _actor_ref(session, destination),
    )
