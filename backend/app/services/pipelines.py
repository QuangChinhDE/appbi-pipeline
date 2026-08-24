"""Pipeline lifecycle (section 14)."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.dto import ConfiguredStream, EngineConnectionRequest
from app.adapters.registry import get_adapter
from app.core.context import RequestContext
from app.core.db import utcnow
from app.core.errors import (
    AppError, NotFoundError, ResourceInUseError, ResourceModifiedError, ValidationError,
    error_from_matrix,
)
from app.core.logging import log_event
from app.core.params import as_enum
from app.core.permissions import Action, Module
from app.models.engine import EngineMapping
from app.models.enums import (
    ACTIVE_RUN_STATUSES, DestinationSyncMode, EngineResourceType, HealthLevel, OverlapPolicy,
    PipelineHealth, PipelineStatus, ProductResourceType, ResourceStatus, RunStatus, ScheduleType,
    SyncMode,
)
from app.models.integration import Destination, Pipeline, PipelineStream, SchemaSnapshot, Source
from app.models.run import PipelineRun
from app.services import actors, audit, catalog, scheduling, schema_service

logger = logging.getLogger(__name__)


# ── reads ──────────────────────────────────────────────────────────────────

async def get(session: AsyncSession, ctx: RequestContext, pipeline_id: uuid.UUID) -> Pipeline:
    pipeline = await session.scalar(
        select(Pipeline).where(
            Pipeline.id == pipeline_id,
            Pipeline.workspace_id == ctx.workspace_id,
            Pipeline.deleted_at.is_(None),
        )
    )
    if pipeline is None:
        raise NotFoundError("Không tìm thấy pipeline này trong workspace.")
    return pipeline


async def list_pipelines(
    session: AsyncSession,
    ctx: RequestContext,
    *,
    query: str | None = None,
    status: str | None = None,
    health: str | None = None,
    source_id: uuid.UUID | None = None,
    destination_id: uuid.UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Pipeline], int, dict[str, int]]:
    stmt = select(Pipeline).where(
        Pipeline.workspace_id == ctx.workspace_id, Pipeline.deleted_at.is_(None)
    )
    if query:
        stmt = stmt.where(func.lower(Pipeline.name).like(f"%{query.lower()}%"))
    if status:
        stmt = stmt.where(Pipeline.status == as_enum(status, PipelineStatus, field="status"))
    if source_id:
        stmt = stmt.where(Pipeline.source_id == source_id)
    if destination_id:
        stmt = stmt.where(Pipeline.destination_id == destination_id)

    rows = list((await session.scalars(stmt.order_by(Pipeline.updated_at.desc()))).all())
    health_map = await health_for(session, ctx.workspace_id, rows)
    if health:
        wanted = as_enum(health, PipelineHealth, field="health")
        rows = [row for row in rows if health_map.get(row.id) is wanted]

    summary: dict[str, int] = {"total": len(rows)}
    for value in PipelineHealth:
        summary[value.value.lower()] = sum(1 for r in rows if health_map.get(r.id) is value)
    return rows[offset: offset + limit], len(rows), summary


async def health_for(
    session: AsyncSession, workspace_id: uuid.UUID, pipelines: list[Pipeline]
) -> dict[uuid.UUID, PipelineHealth]:
    """Derived, not stored (section 18.2) -- one query, not one per row."""
    if not pipelines:
        return {}
    ids = [p.id for p in pipelines]
    active = set((await session.scalars(
        select(PipelineRun.pipeline_id).where(
            PipelineRun.workspace_id == workspace_id,
            PipelineRun.pipeline_id.in_(ids),
            PipelineRun.status.in_(list(ACTIVE_RUN_STATUSES)),
        ).distinct()
    )).all())
    last_runs = await last_run_map(session, workspace_id, ids)

    out: dict[uuid.UUID, PipelineHealth] = {}
    for pipeline in pipelines:
        if pipeline.id in active:
            out[pipeline.id] = PipelineHealth.RUNNING
        elif pipeline.status is PipelineStatus.NEEDS_REVIEW:
            out[pipeline.id] = PipelineHealth.ACTION_REQUIRED
        elif pipeline.status is PipelineStatus.PAUSED:
            out[pipeline.id] = PipelineHealth.PAUSED
        else:
            last = last_runs.get(pipeline.id)
            if last is None:
                out[pipeline.id] = PipelineHealth.NEVER_RUN
            elif last.status is RunStatus.SUCCEEDED:
                out[pipeline.id] = PipelineHealth.HEALTHY
            elif last.status in (RunStatus.FAILED, RunStatus.FAILED_TO_START, RunStatus.TIMED_OUT):
                out[pipeline.id] = (
                    PipelineHealth.ACTION_REQUIRED
                    if (last.remediation_action in ("UPDATE_CREDENTIALS", "REDISCOVER_SCHEMA",
                                                    "OPEN_CONFIGURATION", "GRANT_PERMISSION"))
                    else PipelineHealth.FAILED
                )
            elif last.status is RunStatus.CANCELLED:
                out[pipeline.id] = PipelineHealth.WARNING
            else:
                out[pipeline.id] = PipelineHealth.RUNNING
    return out


async def last_run_map(
    session: AsyncSession, workspace_id: uuid.UUID, pipeline_ids: list[uuid.UUID]
) -> dict[uuid.UUID, PipelineRun]:
    if not pipeline_ids:
        return {}
    newest = (
        select(PipelineRun.pipeline_id, func.max(PipelineRun.created_at).label("created_at"))
        .where(PipelineRun.workspace_id == workspace_id, PipelineRun.pipeline_id.in_(pipeline_ids))
        .group_by(PipelineRun.pipeline_id)
        .subquery()
    )
    rows = await session.scalars(
        select(PipelineRun).join(
            newest,
            (PipelineRun.pipeline_id == newest.c.pipeline_id)
            & (PipelineRun.created_at == newest.c.created_at),
        )
    )
    return {run.pipeline_id: run for run in rows.all()}


async def stream_counts(
    session: AsyncSession, pipeline_ids: list[uuid.UUID]
) -> dict[uuid.UUID, int]:
    if not pipeline_ids:
        return {}
    rows = await session.execute(
        select(PipelineStream.pipeline_id, func.count(PipelineStream.id))
        .where(PipelineStream.pipeline_id.in_(pipeline_ids), PipelineStream.selected.is_(True))
        .group_by(PipelineStream.pipeline_id)
    )
    return dict(rows.all())


async def active_run(session: AsyncSession, pipeline_id: uuid.UUID) -> PipelineRun | None:
    return await session.scalar(
        select(PipelineRun)
        .where(PipelineRun.pipeline_id == pipeline_id,
               PipelineRun.status.in_(list(ACTIVE_RUN_STATUSES)))
        .order_by(PipelineRun.created_at.desc())
        .limit(1)
    )


# ── stream validation ──────────────────────────────────────────────────────

def _validate_streams(
    selections: list[Any], snapshot: SchemaSnapshot, connector_dest_modes: list[str]
) -> list[dict[str, Any]]:
    """Reject anything the connector or catalog does not actually support
    (section 14.2 step 3: never offer an option the engine cannot honour)."""
    catalog_streams = schema_service.streams_of(snapshot)
    chosen = [s for s in selections if s.selected]
    if not chosen:
        raise error_from_matrix("PIPELINE_NO_STREAM_SELECTED")

    resolved: list[dict[str, Any]] = []
    for selection in chosen:
        key = (selection.namespace, selection.name)
        entry = catalog_streams.get(key)
        if entry is None:
            raise ValidationError(
                f"Stream '{selection.name}' không có trong snapshot cấu trúc đang dùng.",
                details={"stream": selection.name},
            )
        supported = entry.get("supported_sync_modes") or ["full_refresh"]
        if selection.sync_mode not in supported:
            raise ValidationError(
                f"Stream '{selection.name}' không hỗ trợ chế độ {selection.sync_mode}.",
                details={"stream": selection.name, "supported_sync_modes": supported},
            )
        if selection.destination_sync_mode not in connector_dest_modes:
            raise ValidationError(
                f"Destination không hỗ trợ chế độ ghi '{selection.destination_sync_mode}'.",
                details={"supported": connector_dest_modes},
            )

        available = set(schema_service.field_types(entry.get("json_schema") or {}))
        cursor = list(selection.cursor_fields or [])
        if selection.sync_mode == SyncMode.INCREMENTAL.value:
            if not cursor and not entry.get("source_defined_cursor"):
                cursor = list(entry.get("default_cursor_field") or [])
            if not cursor and not entry.get("source_defined_cursor"):
                raise error_from_matrix(
                    "PIPELINE_CURSOR_INVALID",
                    message=f"Stream '{selection.name}' cần chọn cursor cho chế độ incremental.",
                )
            if cursor and not set(cursor) <= available:
                raise error_from_matrix(
                    "PIPELINE_CURSOR_INVALID",
                    message=f"Cursor '{', '.join(cursor)}' không tồn tại trong stream "
                            f"'{selection.name}'.",
                )
        else:
            cursor = []

        primary_key = [list(pk) for pk in (selection.primary_key_fields or [])]
        if selection.destination_sync_mode == DestinationSyncMode.APPEND_DEDUP.value:
            if not primary_key:
                primary_key = [list(pk) for pk in (entry.get("source_defined_primary_key") or [])]
            if not primary_key:
                raise error_from_matrix(
                    "PIPELINE_PRIMARY_KEY_REQUIRED",
                    message=f"Stream '{selection.name}' cần primary key cho chế độ dedupe.",
                )
            flat = {field for pk in primary_key for field in pk}
            if not flat <= available:
                raise error_from_matrix(
                    "PIPELINE_PRIMARY_KEY_REQUIRED",
                    message=f"Primary key của '{selection.name}' không tồn tại trong dữ liệu nguồn.",
                )
        else:
            primary_key = primary_key or []

        resolved.append({
            "name": selection.name,
            "namespace": selection.namespace,
            "sync_mode": selection.sync_mode,
            "destination_sync_mode": selection.destination_sync_mode,
            "cursor_fields": cursor,
            "primary_key_fields": primary_key,
            "selected_fields": selection.selected_fields,
            "json_schema": entry.get("json_schema") or {},
            "schema_hash": entry.get("schema_hash"),
        })
    return resolved


def configured_streams(pipeline: Pipeline) -> list[ConfiguredStream]:
    return [
        ConfiguredStream(
            name=stream.stream_name,
            namespace=stream.namespace,
            json_schema=stream.json_schema,
            sync_mode=stream.sync_mode.value,
            destination_sync_mode=stream.destination_sync_mode.value,
            cursor_field=list(stream.cursor_fields or []),
            primary_key=[list(pk) for pk in (stream.primary_key_fields or [])],
        )
        for stream in pipeline.streams if stream.selected
    ]


# ── mutations ──────────────────────────────────────────────────────────────

async def create(session: AsyncSession, ctx: RequestContext, payload) -> Pipeline:
    ctx.require(Module.PIPELINES, Action.CREATE)

    source = await actors.get(session, ctx, actors.SOURCE, payload.source_id)
    destination = await actors.get(session, ctx, actors.DESTINATION, payload.destination_id)
    for actor, label in ((source, "Source"), (destination, "Destination")):
        if actor.status is not ResourceStatus.ACTIVE:
            raise ValidationError(f"{label} '{actor.name}' đang không hoạt động.")

    clash = await session.scalar(
        select(Pipeline).where(
            Pipeline.workspace_id == ctx.workspace_id,
            func.lower(Pipeline.name) == payload.name.strip().lower(),
            Pipeline.deleted_at.is_(None),
        )
    )
    if clash is not None:
        raise ValidationError(f"Đã có pipeline tên '{payload.name}'.")

    snapshot = (
        await schema_service.get_snapshot(session, ctx, payload.schema_snapshot_id)
        if payload.schema_snapshot_id
        else await schema_service.latest_snapshot(session, source.id)
    )
    if snapshot is None:
        raise ValidationError(
            "Chưa có cấu trúc dữ liệu cho nguồn này. Hãy chạy Discover trước.",
            code="SCHEMA_SNAPSHOT_MISSING",
        )
    if snapshot.source_id != source.id:
        raise ValidationError("Snapshot cấu trúc không thuộc về source đã chọn.")

    destination_connector = await catalog.get_connector(session, destination.connector_key)
    resolved = _validate_streams(
        payload.streams, snapshot,
        destination_connector.supported_destination_sync_modes or ["overwrite", "append"],
    )
    schedule = scheduling.validate(payload.schedule.model_dump())
    schedule_type = ScheduleType(schedule["type"])

    pipeline = Pipeline(
        workspace_id=ctx.workspace_id,
        name=payload.name.strip(),
        description=payload.description,
        source_id=source.id,
        destination_id=destination.id,
        status=PipelineStatus.ACTIVE,
        schedule_type=schedule_type,
        schedule_config=schedule,
        timezone=schedule.get("timezone", ctx.timezone),
        overlap_policy=OverlapPolicy(payload.overlap_policy),
        active_schema_snapshot_id=snapshot.id,
        namespace_format=payload.namespace_format,
        stream_prefix=payload.stream_prefix,
        created_by=ctx.user_id,
        updated_by=ctx.user_id,
    )
    pipeline.next_run_at = scheduling.next_run_at(
        schedule_type, schedule, pipeline.timezone
    )
    session.add(pipeline)
    await session.flush()

    for entry in resolved:
        session.add(PipelineStream(
            pipeline_id=pipeline.id,
            namespace=entry["namespace"],
            stream_name=entry["name"],
            selected=True,
            sync_mode=SyncMode(entry["sync_mode"]),
            destination_sync_mode=DestinationSyncMode(entry["destination_sync_mode"]),
            cursor_fields=entry["cursor_fields"],
            primary_key_fields=entry["primary_key_fields"],
            selected_fields=entry["selected_fields"],
            json_schema=entry["json_schema"],
            schema_hash=entry["schema_hash"],
        ))
    await session.flush()
    await session.refresh(pipeline, ["streams"])

    try:
        ref = await get_adapter().create_connection(
            await _connection_request(session, ctx, pipeline, source, destination)
        )
    except AppError:
        # Compensation: nothing engine-side survived, so drop the product row.
        await session.delete(pipeline)
        await session.flush()
        raise
    session.add(EngineMapping(
        workspace_id=ctx.workspace_id,
        product_resource_type=ProductResourceType.PIPELINE,
        product_resource_id=pipeline.id,
        engine_type=ref.engine_type,
        engine_resource_type=EngineResourceType.CONNECTION,
        engine_resource_ref=ref.ref,
    ))
    await session.flush()

    await audit.record(
        session, ctx, "pipeline.created",
        resource_type="PIPELINE", resource_id=pipeline.id, resource_name=pipeline.name,
        after={"source": source.name, "destination": destination.name,
               "streams": len(resolved), "schedule": schedule},
    )
    log_event(logger, logging.INFO, "pipeline.created", pipeline_id=str(pipeline.id),
              streams=len(resolved))
    return pipeline


async def _connection_request(
    session: AsyncSession, ctx: RequestContext, pipeline: Pipeline,
    source: Source, destination: Destination
) -> EngineConnectionRequest:
    return EngineConnectionRequest(
        workspace_id=ctx.workspace_id,
        product_resource_id=pipeline.id,
        name=pipeline.name,
        source_ref=await actors.engine_ref(session, actors.SOURCE, source.id) or "",
        destination_ref=await actors.engine_ref(session, actors.DESTINATION, destination.id) or "",
        streams=configured_streams(pipeline),
        namespace_format=pipeline.namespace_format,
        stream_prefix=pipeline.stream_prefix,
    )


async def update(session: AsyncSession, ctx: RequestContext, pipeline_id: uuid.UUID, payload
                 ) -> Pipeline:
    ctx.require(Module.PIPELINES, Action.EDIT)
    pipeline = await get(session, ctx, pipeline_id)
    if payload.version is not None and payload.version != pipeline.version:
        raise ResourceModifiedError()

    before = {"name": pipeline.name, "schedule": pipeline.schedule_config,
              "streams": len(pipeline.streams)}

    if payload.name and payload.name.strip() != pipeline.name:
        clash = await session.scalar(
            select(Pipeline).where(
                Pipeline.workspace_id == ctx.workspace_id,
                func.lower(Pipeline.name) == payload.name.strip().lower(),
                Pipeline.id != pipeline.id,
                Pipeline.deleted_at.is_(None),
            )
        )
        if clash is not None:
            raise ValidationError(f"Đã có pipeline tên '{payload.name}'.")
        pipeline.name = payload.name.strip()
    if payload.description is not None:
        pipeline.description = payload.description
    if payload.namespace_format is not None:
        pipeline.namespace_format = payload.namespace_format or None
    if payload.stream_prefix is not None:
        pipeline.stream_prefix = payload.stream_prefix or None
    if payload.overlap_policy is not None:
        pipeline.overlap_policy = OverlapPolicy(payload.overlap_policy)

    if payload.schedule is not None:
        schedule = scheduling.validate(payload.schedule.model_dump())
        pipeline.schedule_type = ScheduleType(schedule["type"])
        pipeline.schedule_config = schedule
        pipeline.timezone = schedule.get("timezone", pipeline.timezone)
        pipeline.next_run_at = (
            scheduling.next_run_at(pipeline.schedule_type, schedule, pipeline.timezone)
            if pipeline.status is PipelineStatus.ACTIVE else None
        )

    if payload.streams is not None:
        snapshot = await session.get(SchemaSnapshot, pipeline.active_schema_snapshot_id)
        if snapshot is None:
            raise ValidationError("Pipeline chưa có snapshot cấu trúc dữ liệu hợp lệ.")
        destination = await actors.get(session, ctx, actors.DESTINATION, pipeline.destination_id)
        destination_connector = await catalog.get_connector(session, destination.connector_key)
        resolved = _validate_streams(
            payload.streams, snapshot,
            destination_connector.supported_destination_sync_modes or ["overwrite", "append"],
        )
        existing = {(s.namespace, s.stream_name): s for s in pipeline.streams}
        keep: set[tuple[str | None, str]] = set()
        for entry in resolved:
            key = (entry["namespace"], entry["name"])
            keep.add(key)
            stream = existing.get(key)
            if stream is None:
                stream = PipelineStream(pipeline_id=pipeline.id, namespace=entry["namespace"],
                                        stream_name=entry["name"])
                session.add(stream)
            stream.selected = True
            stream.sync_mode = SyncMode(entry["sync_mode"])
            stream.destination_sync_mode = DestinationSyncMode(entry["destination_sync_mode"])
            stream.cursor_fields = entry["cursor_fields"]
            stream.primary_key_fields = entry["primary_key_fields"]
            stream.selected_fields = entry["selected_fields"]
            stream.json_schema = entry["json_schema"]
            stream.schema_hash = entry["schema_hash"]
        for key, stream in existing.items():
            if key not in keep:
                await session.delete(stream)

    pipeline.updated_by = ctx.user_id
    pipeline.version += 1
    await session.flush()
    await session.refresh(pipeline, ["streams"])

    ref = await connection_ref(session, pipeline.id)
    if ref:
        source = await actors.get(session, ctx, actors.SOURCE, pipeline.source_id)
        destination = await actors.get(session, ctx, actors.DESTINATION, pipeline.destination_id)
        await get_adapter().update_connection(
            ref, await _connection_request(session, ctx, pipeline, source, destination)
        )

    await audit.record(
        session, ctx, "pipeline.updated",
        resource_type="PIPELINE", resource_id=pipeline.id, resource_name=pipeline.name,
        before=before,
        after={"name": pipeline.name, "schedule": pipeline.schedule_config,
               "streams": len(pipeline.streams)},
    )
    return pipeline


async def connection_ref(session: AsyncSession, pipeline_id: uuid.UUID) -> str | None:
    mapping = await session.scalar(
        select(EngineMapping).where(
            EngineMapping.product_resource_type == ProductResourceType.PIPELINE,
            EngineMapping.product_resource_id == pipeline_id,
            EngineMapping.engine_resource_type == EngineResourceType.CONNECTION,
        )
    )
    return mapping.engine_resource_ref if mapping else None


async def set_paused(session: AsyncSession, ctx: RequestContext, pipeline_id: uuid.UUID,
                     paused: bool) -> Pipeline:
    ctx.require(Module.PIPELINES, Action.OPERATE)
    pipeline = await get(session, ctx, pipeline_id)
    if paused:
        pipeline.status = PipelineStatus.PAUSED
        pipeline.next_run_at = None
    else:
        if pipeline.status is PipelineStatus.NEEDS_REVIEW:
            raise error_from_matrix("PIPELINE_NEEDS_REVIEW", resource_id=pipeline.id)
        pipeline.status = PipelineStatus.ACTIVE
        pipeline.next_run_at = scheduling.next_run_at(
            pipeline.schedule_type, pipeline.schedule_config, pipeline.timezone
        )
    pipeline.updated_by = ctx.user_id
    await session.flush()
    await audit.record(
        session, ctx, "pipeline.paused" if paused else "pipeline.enabled",
        resource_type="PIPELINE", resource_id=pipeline.id, resource_name=pipeline.name,
    )
    return pipeline


async def delete(session: AsyncSession, ctx: RequestContext, pipeline_id: uuid.UUID) -> None:
    ctx.require(Module.PIPELINES, Action.DELETE)
    pipeline = await get(session, ctx, pipeline_id)
    running = await active_run(session, pipeline.id)
    if running is not None:
        raise ResourceInUseError(
            "Pipeline đang có lần chạy chưa kết thúc. Hãy hủy hoặc đợi hoàn tất trước khi xóa.",
            constraints=[{"type": "RUN", "id": str(running.id), "name": str(running.id)[:8]}],
        )

    pipeline.status = PipelineStatus.DELETE_PENDING
    pipeline.next_run_at = None
    await session.flush()

    ref = await connection_ref(session, pipeline.id)
    try:
        if ref:
            await get_adapter().delete_connection(ref)
    except AppError as exc:
        log_event(logger, logging.WARNING, "pipeline.engine_delete_failed",
                  pipeline_id=str(pipeline.id), error=str(exc))
        await audit.record(
            session, ctx, "pipeline.delete.pending",
            resource_type="PIPELINE", resource_id=pipeline.id, resource_name=pipeline.name,
        )
        return

    pipeline.status = PipelineStatus.DELETED
    pipeline.deleted_at = utcnow()
    await session.flush()
    await audit.record(
        session, ctx, "pipeline.deleted",
        resource_type="PIPELINE", resource_id=pipeline.id, resource_name=pipeline.name,
        before={"name": pipeline.name},
    )


async def metrics(session: AsyncSession, workspace_id: uuid.UUID, pipeline: Pipeline
                  ) -> dict[str, Any]:
    from datetime import timedelta

    now = utcnow()
    window_30 = now - timedelta(days=30)
    window_7 = now - timedelta(days=7)

    runs = list((await session.scalars(
        select(PipelineRun).where(
            PipelineRun.workspace_id == workspace_id,
            PipelineRun.pipeline_id == pipeline.id,
            PipelineRun.created_at >= window_30,
        )
    )).all())
    terminal = [r for r in runs if r.status.is_terminal]

    def rate(subset: list[PipelineRun]) -> float | None:
        if not subset:
            return None
        succeeded = sum(1 for r in subset if r.status is RunStatus.SUCCEEDED)
        return round(succeeded / len(subset) * 100, 1)

    durations = [
        (r.ended_at - r.started_at).total_seconds()
        for r in terminal if r.started_at and r.ended_at
    ]
    return {
        "success_rate_7d": rate([r for r in terminal if r.created_at >= window_7]),
        "success_rate_30d": rate(terminal),
        "average_duration_seconds": round(sum(durations) / len(durations), 1) if durations else None,
        "total_runs_30d": len(runs),
        "records_synced_30d": sum(r.records_synced or 0 for r in runs),
        "last_success_at": pipeline.last_success_at,
        "consecutive_failures": pipeline.consecutive_failures,
    }


def health_block(pipeline: Pipeline, health: PipelineHealth) -> dict[str, Any]:
    labels = {
        PipelineHealth.HEALTHY: "Hoạt động tốt",
        PipelineHealth.RUNNING: "Đang chạy",
        PipelineHealth.WARNING: "Cần theo dõi",
        PipelineHealth.ACTION_REQUIRED: "Cần xử lý",
        PipelineHealth.FAILED: "Thất bại",
        PipelineHealth.PAUSED: "Tạm dừng",
        PipelineHealth.NEVER_RUN: "Chưa chạy lần nào",
    }
    level_map = {
        PipelineHealth.HEALTHY: "HEALTHY",
        PipelineHealth.RUNNING: "RUNNING",
        PipelineHealth.WARNING: "WARNING",
        PipelineHealth.ACTION_REQUIRED: "ERROR",
        PipelineHealth.FAILED: "ERROR",
        PipelineHealth.PAUSED: "UNKNOWN",
        PipelineHealth.NEVER_RUN: "UNKNOWN",
    }
    return {
        "level": level_map[health],
        "code": health.value,
        "label": labels[health],
        "last_checked_at": pipeline.updated_at,
        "message": pipeline.needs_review_reason,
    }


def available_actions(ctx: RequestContext, pipeline: Pipeline, health: PipelineHealth) -> list[str]:
    actions: list[str] = []
    if ctx.can(Module.PIPELINES, Action.OPERATE):
        if pipeline.status is PipelineStatus.ACTIVE and health is not PipelineHealth.RUNNING:
            actions.append("RUN_NOW")
        if health is PipelineHealth.RUNNING:
            actions.append("CANCEL_RUN")
        actions.append("RESUME" if pipeline.status is PipelineStatus.PAUSED else "PAUSE")
    if ctx.can(Module.PIPELINES, Action.EDIT):
        actions += ["EDIT", "REDISCOVER_SCHEMA"]
        if pipeline.status is PipelineStatus.NEEDS_REVIEW:
            actions.append("REVIEW_SCHEMA")
    if ctx.can(Module.PIPELINES, Action.DELETE):
        actions.append("DELETE")
    return actions
