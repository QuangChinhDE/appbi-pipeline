"""Pipeline endpoints (section 23.5)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Header, Query, Response
from sqlalchemy import select

from app.api.deps import CtxDep, SessionDep
from app.api.v1.presenters import pipeline_detail, pipeline_view
from app.core.db import utcnow
from app.core.errors import ValidationError
from app.core.permissions import Action, Module
from app.models.engine import ConnectorDefinition
from app.models.identity import User
from app.models.enums import PipelineHealth, TriggerType
from app.models.integration import Destination, SchemaSnapshot, Source
from app.models.run import PipelineRun
from app.schemas.common import PageInfo, Paginated
from app.schemas.domain import (
    PipelineCreate, PipelineDetail, PipelineUpdate, PipelineView, RunView, SchemaApproveRequest,
    ConnectionStateUpdate, ConnectionStateView, SchemaDiffView, SchemaSnapshotView, ScheduleConfig,
    StreamSyncState,
)
from app.services import (
    actors as actor_service, pipelines as pipeline_service, runs as run_service, scheduling,
    schema_service,
)

router = APIRouter(prefix="/pipelines", tags=["pipelines"])


async def _connector(session, connector_key: str) -> ConnectorDefinition | None:
    return await session.scalar(
        select(ConnectorDefinition).where(ConnectorDefinition.connector_key == connector_key)
    )


class _Prefetched:
    """Everything a page of pipeline rows needs, loaded in four queries.

    Rendering a row needs its source, destination, both connectors and the
    owner. Fetching those per row is four to six round trips each -- on a
    200-row page, around a thousand queries to render one screen, which is
    what makes a list look fine on a laptop and fall over under load.

    Optional on purpose: `_view` still works without it, so the single-row
    detail route is unchanged and there is no second rendering path to keep
    in step.
    """

    __slots__ = ("sources", "destinations", "connectors", "owners")

    def __init__(self) -> None:
        self.sources: dict[uuid.UUID, Source] = {}
        self.destinations: dict[uuid.UUID, Destination] = {}
        self.connectors: dict[str, ConnectorDefinition] = {}
        self.owners: dict[uuid.UUID, User] = {}


async def _prefetch(session, pipelines) -> _Prefetched:
    loaded = _Prefetched()
    if not pipelines:
        return loaded

    source_ids = {p.source_id for p in pipelines if p.source_id}
    destination_ids = {p.destination_id for p in pipelines if p.destination_id}
    owner_ids = {p.created_by for p in pipelines if p.created_by}

    if source_ids:
        loaded.sources = {row.id: row for row in (await session.scalars(
            select(Source).where(Source.id.in_(source_ids)))).all()}
    if destination_ids:
        loaded.destinations = {row.id: row for row in (await session.scalars(
            select(Destination).where(Destination.id.in_(destination_ids)))).all()}
    if owner_ids:
        loaded.owners = {row.id: row for row in (await session.scalars(
            select(User).where(User.id.in_(owner_ids)))).all()}

    keys = ({row.connector_key for row in loaded.sources.values()}
            | {row.connector_key for row in loaded.destinations.values()})
    if keys:
        loaded.connectors = {row.connector_key: row for row in (await session.scalars(
            select(ConnectorDefinition).where(
                ConnectorDefinition.connector_key.in_(keys)))).all()}
    return loaded


async def _view(session, ctx, pipeline, *, health=None, last_run=None,
                stream_count: int | None = None,
                loaded: _Prefetched | None = None) -> PipelineView:
    source = (loaded.sources.get(pipeline.source_id) if loaded
              else None) or await session.get(Source, pipeline.source_id)
    destination = (loaded.destinations.get(pipeline.destination_id) if loaded
                   else None) or await session.get(Destination, pipeline.destination_id)
    if health is None:
        health = (await pipeline_service.health_for(session, ctx.workspace_id, [pipeline])).get(
            pipeline.id, PipelineHealth.NEVER_RUN
        )
    if last_run is None and pipeline.last_run_id:
        last_run = await session.get(PipelineRun, pipeline.last_run_id)
    if stream_count is None:
        stream_count = sum(1 for s in pipeline.streams if s.selected)
    owner = ((loaded.owners.get(pipeline.created_by) if loaded and pipeline.created_by
              else None)
             or await actor_service.owner_of(session, pipeline.created_by))

    async def connector_for(key: str):
        if loaded and key in loaded.connectors:
            return loaded.connectors[key]
        return await _connector(session, key)

    return pipeline_view(
        ctx, pipeline, health=health, source=source, destination=destination,
        source_connector=await connector_for(source.connector_key),
        destination_connector=await connector_for(destination.connector_key),
        last_run=last_run, stream_count=stream_count, owner=owner,
    )


@router.get("", response_model=Paginated[PipelineView])
async def list_pipelines(
    session: SessionDep,
    ctx: CtxDep,
    q: Annotated[str | None, Query()] = None,
    status: Annotated[str | None, Query()] = None,
    health: Annotated[str | None, Query()] = None,
    source_id: Annotated[uuid.UUID | None, Query()] = None,
    destination_id: Annotated[uuid.UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Paginated[PipelineView]:
    ctx.require(Module.PIPELINES, Action.VIEW)
    rows, total, summary = await pipeline_service.list_pipelines(
        session, ctx, query=q, status=status, health=health, source_id=source_id,
        destination_id=destination_id, limit=limit, offset=offset,
    )
    health_map = await pipeline_service.health_for(session, ctx.workspace_id, rows)
    last_runs = await pipeline_service.last_run_map(
        session, ctx.workspace_id, [p.id for p in rows]
    )
    counts = await pipeline_service.stream_counts(session, [p.id for p in rows])
    loaded = await _prefetch(session, rows)
    items = [
        await _view(session, ctx, pipeline,
                    health=health_map.get(pipeline.id, PipelineHealth.NEVER_RUN),
                    last_run=last_runs.get(pipeline.id),
                    stream_count=counts.get(pipeline.id, 0),
                    loaded=loaded)
        for pipeline in rows
    ]
    return Paginated[PipelineView](
        items=items,
        page=PageInfo(has_more=offset + len(items) < total, total=total,
                      limit=limit, offset=offset),
        summary=summary,
    )


@router.post("", response_model=PipelineDetail, status_code=201)
async def create(payload: PipelineCreate, session: SessionDep, ctx: CtxDep) -> PipelineDetail:
    pipeline = await pipeline_service.create(session, ctx, payload)
    if payload.run_first_sync and ctx.can(Module.PIPELINES, Action.OPERATE):
        await run_service.trigger(session, ctx, pipeline, trigger_type=TriggerType.MANUAL)
    await session.commit()
    await session.refresh(pipeline)
    return await _detail(session, ctx, pipeline)


@router.post("/schedule/preview")
async def preview_schedule(payload: ScheduleConfig, ctx: CtxDep) -> dict:
    """Wizard step 4 shows the next three fire times before the user commits."""
    ctx.require(Module.PIPELINES, Action.VIEW)
    from app.models.enums import ScheduleType

    normalized = scheduling.validate(payload.model_dump())
    schedule_type = ScheduleType(normalized["type"])
    upcoming = scheduling.preview(schedule_type, normalized, normalized.get("timezone", ctx.timezone))
    return {
        "description": scheduling.describe(schedule_type, normalized),
        "timezone": normalized.get("timezone"),
        "next_runs": [dt.isoformat() for dt in upcoming],
    }


@router.get("/{pipeline_id}", response_model=PipelineDetail)
async def detail(pipeline_id: uuid.UUID, session: SessionDep, ctx: CtxDep) -> PipelineDetail:
    ctx.require(Module.PIPELINES, Action.VIEW)
    pipeline = await pipeline_service.get(session, ctx, pipeline_id)
    return await _detail(session, ctx, pipeline)


@router.patch("/{pipeline_id}", response_model=PipelineDetail)
async def update(
    pipeline_id: uuid.UUID, payload: PipelineUpdate, session: SessionDep, ctx: CtxDep
) -> PipelineDetail:
    pipeline = await pipeline_service.update(session, ctx, pipeline_id, payload)
    await session.commit()
    await session.refresh(pipeline)
    return await _detail(session, ctx, pipeline)


@router.post("/{pipeline_id}/enable", response_model=PipelineDetail)
async def enable(pipeline_id: uuid.UUID, session: SessionDep, ctx: CtxDep) -> PipelineDetail:
    pipeline = await pipeline_service.set_paused(session, ctx, pipeline_id, False)
    await session.commit()
    await session.refresh(pipeline)
    return await _detail(session, ctx, pipeline)


@router.post("/{pipeline_id}/pause", response_model=PipelineDetail)
async def pause(pipeline_id: uuid.UUID, session: SessionDep, ctx: CtxDep) -> PipelineDetail:
    pipeline = await pipeline_service.set_paused(session, ctx, pipeline_id, True)
    await session.commit()
    await session.refresh(pipeline)
    return await _detail(session, ctx, pipeline)


@router.delete("/{pipeline_id}", status_code=204)
async def delete(pipeline_id: uuid.UUID, session: SessionDep, ctx: CtxDep) -> Response:
    await pipeline_service.delete(session, ctx, pipeline_id)
    await session.commit()
    return Response(status_code=204)


@router.post("/{pipeline_id}/rediscover", response_model=SchemaSnapshotView)
async def rediscover(
    pipeline_id: uuid.UUID, session: SessionDep, ctx: CtxDep
) -> SchemaSnapshotView:
    from app.api.v1.schema import _snapshot_view

    ctx.require(Module.PIPELINES, Action.EDIT)
    pipeline = await pipeline_service.get(session, ctx, pipeline_id)
    snapshot = await schema_service.discover(session, ctx, pipeline.source_id, force=True)
    await session.commit()
    return _snapshot_view(snapshot)


@router.get("/{pipeline_id}/schema-diff", response_model=SchemaDiffView)
async def schema_diff(pipeline_id: uuid.UUID, session: SessionDep, ctx: CtxDep) -> SchemaDiffView:
    ctx.require(Module.PIPELINES, Action.VIEW)
    pipeline = await pipeline_service.get(session, ctx, pipeline_id)
    current = (await session.get(SchemaSnapshot, pipeline.active_schema_snapshot_id)
               if pipeline.active_schema_snapshot_id else None)
    latest = await schema_service.latest_snapshot(session, pipeline.source_id)
    selected = {(s.namespace, s.stream_name) for s in pipeline.streams if s.selected}
    cursors = {(s.namespace, s.stream_name): s.cursor_fields
               for s in pipeline.streams if s.selected}
    result = schema_service.diff(current, latest, selected=selected, selected_cursors=cursors)
    return SchemaDiffView(
        pipeline_id=pipeline.id,
        from_snapshot_id=current.id if current else None,
        to_snapshot_id=latest.id if latest else None,
        generated_at=utcnow(),
        has_breaking=schema_service.has_breaking(result),
        added=result["added"], removed=result["removed"], changed=result["changed"],
    )


@router.post("/{pipeline_id}/schema-approve", response_model=PipelineDetail)
async def schema_approve(
    pipeline_id: uuid.UUID, payload: SchemaApproveRequest, session: SessionDep, ctx: CtxDep
) -> PipelineDetail:
    pipeline = await pipeline_service.get(session, ctx, pipeline_id)
    snapshot = await schema_service.get_snapshot(session, ctx, payload.snapshot_id)
    if snapshot.source_id != pipeline.source_id:
        raise ValidationError("Snapshot không thuộc về source của pipeline này.")
    await schema_service.approve(session, ctx, pipeline, snapshot,
                                 drop_removed=payload.drop_removed_streams)
    await session.commit()
    await session.refresh(pipeline)
    return await _detail(session, ctx, pipeline)


@router.post("/{pipeline_id}/runs", response_model=RunView, status_code=202)
async def trigger_run(
    pipeline_id: uuid.UUID,
    session: SessionDep,
    ctx: CtxDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> RunView:
    """202: the trigger is accepted; the sync itself runs in the worker."""
    from app.api.v1.presenters import run_view
    from app.schemas.common import ActorRef

    pipeline = await pipeline_service.get(session, ctx, pipeline_id)
    run = await run_service.trigger(
        session, ctx, pipeline, trigger_type=TriggerType.MANUAL, idempotency_key=idempotency_key
    )
    await session.commit()
    source = await session.get(Source, pipeline.source_id)
    connector = await _connector(session, source.connector_key)
    return run_view(
        ctx, run,
        pipeline_ref=ActorRef(
            id=pipeline.id, name=pipeline.name, connector_key=source.connector_key,
            connector_display_name=connector.display_name if connector else None,
            icon=connector.icon if connector else None,
        ),
        triggered_by=None, is_stale=False,
    )


@router.get("/{pipeline_id}/state", response_model=ConnectionStateView)
async def replication_state(
    pipeline_id: uuid.UUID, session: SessionDep, ctx: CtxDep,
) -> ConnectionStateView:
    """The cursor the next incremental sync resumes from.

    Its own endpoint rather than a field on the detail payload: the answer
    comes from the engine, and the settings page must render whether or not the
    engine is reachable.
    """
    supported, state, reason = await pipeline_service.replication_state(
        session, ctx, pipeline_id
    )
    return ConnectionStateView(
        supported=supported, state=state,
        fetched_at=utcnow() if supported and reason is None else None,
        unavailable_reason=reason,
    )


@router.put("/{pipeline_id}/state", response_model=ConnectionStateView)
async def replace_replication_state(
    pipeline_id: uuid.UUID, payload: ConnectionStateUpdate,
    session: SessionDep, ctx: CtxDep,
) -> ConnectionStateView:
    """Replace the cursor the next incremental run resumes from.

    PUT, not PATCH: the body is the whole cursor. The service refuses while a
    run is active, because that run commits its own copy at the end and would
    overwrite the edit with no error anywhere.
    """
    accepted, _sent = await pipeline_service.set_replication_state(
        session, ctx, pipeline_id, payload.state
    )
    await session.commit()

    # Read back rather than echo. Airbyte normalises what it is given -- an
    # unrecognised key inside a stream entry is dropped without comment -- so
    # echoing the request would tell the editor its edit landed intact when
    # part of it did not. The panel diffs the two and says so.
    del accepted
    supported, state, reason = await pipeline_service.replication_state(
        session, ctx, pipeline_id
    )
    return ConnectionStateView(
        supported=supported, state=state,
        fetched_at=utcnow() if reason is None else None,
        unavailable_reason=reason,
    )


async def _detail(session, ctx, pipeline) -> PipelineDetail:
    base = await _view(session, ctx, pipeline)
    metrics = await pipeline_service.metrics(session, ctx.workspace_id, pipeline)
    recent = list((await session.scalars(
        select(PipelineRun).where(PipelineRun.pipeline_id == pipeline.id)
        .order_by(PipelineRun.created_at.desc()).limit(10)
    )).all())
    snapshot = (await session.get(SchemaSnapshot, pipeline.active_schema_snapshot_id)
                if pipeline.active_schema_snapshot_id else None)
    latest = await schema_service.latest_snapshot(session, pipeline.source_id)
    pending = bool(latest and snapshot and latest.id != snapshot.id)
    return pipeline_detail(
        base, pipeline, metrics=metrics, recent=recent,
        snapshot_at=snapshot.discovered_at if snapshot else None,
        schema_change_pending=pending,
        stream_sync=await _stream_sync(session, pipeline.id),
    )


async def _stream_sync(session, pipeline_id) -> dict:
    """Per-stream outcome of the most recent run that reported any.

    Deliberately the latest run that *produced stats*, not the latest run: a
    sync that failed before replication reports none, and taking that as the
    answer would blank the whole Status view and read as "no data has ever
    landed" rather than "the last attempt did not get that far".
    """
    from app.models.integration import PipelineStreamStat

    newest = (await session.scalars(
        select(PipelineRun.id)
        .join(PipelineStreamStat, PipelineStreamStat.run_id == PipelineRun.id)
        .where(PipelineRun.pipeline_id == pipeline_id)
        .order_by(PipelineRun.created_at.desc()).limit(1)
    )).first()
    if newest is None:
        return {}

    run = await session.get(PipelineRun, newest)
    when = run.ended_at or run.started_at if run else None
    stats = (await session.scalars(
        select(PipelineStreamStat).where(PipelineStreamStat.run_id == newest)
    )).all()
    return {
        (stat.namespace, stat.stream_name): StreamSyncState(
            status=stat.status,
            records_loaded=stat.records_emitted,
            bytes_loaded=stat.bytes_emitted,
            synced_at=when,
        )
        for stat in stats
    }
