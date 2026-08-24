"""Schema discovery, snapshots and diff classification (section 15)."""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.dto import DiscoveredCatalog, DiscoveredStream
from app.adapters.registry import get_adapter
from app.core.context import RequestContext
from app.core.db import utcnow
from app.core.errors import ConflictError, NotFoundError
from app.core.permissions import Action, Module
from app.models.enums import SchemaChangeSeverity
from app.models.integration import Pipeline, PipelineStream, SchemaSnapshot, Source
from app.services import actors, audit, catalog

logger = logging.getLogger(__name__)

DISCOVER_LOCK_SECONDS = 60


def serialize_stream(stream: DiscoveredStream) -> dict[str, Any]:
    return {
        "name": stream.name,
        "namespace": stream.namespace,
        "json_schema": stream.json_schema,
        "supported_sync_modes": stream.supported_sync_modes,
        "source_defined_cursor": stream.source_defined_cursor,
        "default_cursor_field": stream.default_cursor_field,
        "source_defined_primary_key": stream.source_defined_primary_key,
        "schema_hash": stream.schema_hash,
    }


def field_list(json_schema: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten a stream's JSON schema into the field rows the UI shows."""
    properties = (json_schema or {}).get("properties") or {}
    out: list[dict[str, Any]] = []
    for name, prop in properties.items():
        raw_type = prop.get("type")
        if isinstance(raw_type, list):
            type_name = next((t for t in raw_type if t != "null"), "unknown")
            nullable = "null" in raw_type
        else:
            type_name = raw_type or "unknown"
            nullable = False
        if prop.get("format"):
            type_name = f"{type_name} ({prop['format']})"
        out.append({"name": name, "type": type_name, "nullable": nullable})
    out.sort(key=lambda f: f["name"])
    return out


def field_types(json_schema: dict[str, Any]) -> dict[str, str]:
    return {f["name"]: f["type"] for f in field_list(json_schema)}


async def discover(
    session: AsyncSession, ctx: RequestContext, source_id: uuid.UUID, *, force: bool = False
) -> SchemaSnapshot:
    """Run discover against the source and persist an immutable snapshot."""
    ctx.require(Module.SOURCES, Action.OPERATE)
    source = await actors.get(session, ctx, actors.SOURCE, source_id)

    now = utcnow()
    if not force and source.discover_locked_until and source.discover_locked_until > now:
        raise ConflictError(
            "Đang đọc cấu trúc dữ liệu của nguồn này. Vui lòng đợi vài giây.",
            code="DISCOVER_IN_PROGRESS",
        )
    source.discover_locked_until = now + timedelta(seconds=DISCOVER_LOCK_SECONDS)
    await session.flush()

    connector = await catalog.get_connector(session, source.connector_key)
    configuration = catalog.apply_spec_defaults(
        connector.spec_schema, await actors.resolve_configuration(session, source)
    )
    ref = await actors.engine_ref(session, actors.SOURCE, source.id)
    try:
        result: DiscoveredCatalog = await get_adapter().discover_source(
            catalog.descriptor(connector), configuration, source_ref=ref
        )
    finally:
        source.discover_locked_until = None
        await session.flush()

    latest = await latest_snapshot(session, source.id)
    if latest is not None and latest.catalog_hash == result.catalog_hash:
        latest.discovered_at = result.discovered_at
        await session.flush()
        return latest

    snapshot = SchemaSnapshot(
        workspace_id=ctx.workspace_id,
        source_id=source.id,
        discovered_at=result.discovered_at,
        catalog_hash=result.catalog_hash,
        normalized_catalog={"streams": [serialize_stream(s) for s in result.streams]},
        stream_count=len(result.streams),
        connector_version=result.connector_version,
        discovered_by=ctx.user_id,
    )
    session.add(snapshot)
    await session.flush()
    source.active_schema_snapshot_id = snapshot.id
    await session.flush()

    await audit.record(
        session, ctx, "source.schema.discovered",
        resource_type="SOURCE", resource_id=source.id, resource_name=source.name,
        after={"snapshot_id": str(snapshot.id), "streams": snapshot.stream_count,
               "catalog_hash": snapshot.catalog_hash[:16]},
    )
    await flag_affected_pipelines(session, ctx, source, snapshot)
    return snapshot


async def latest_snapshot(session: AsyncSession, source_id: uuid.UUID) -> SchemaSnapshot | None:
    return await session.scalar(
        select(SchemaSnapshot)
        .where(SchemaSnapshot.source_id == source_id)
        .order_by(SchemaSnapshot.discovered_at.desc())
        .limit(1)
    )


async def get_snapshot(session: AsyncSession, ctx: RequestContext, snapshot_id: uuid.UUID
                       ) -> SchemaSnapshot:
    snapshot = await session.scalar(
        select(SchemaSnapshot).where(
            SchemaSnapshot.id == snapshot_id,
            SchemaSnapshot.workspace_id == ctx.workspace_id,
        )
    )
    if snapshot is None:
        raise NotFoundError("Không tìm thấy snapshot cấu trúc dữ liệu.")
    return snapshot


def streams_of(snapshot: SchemaSnapshot | None) -> dict[tuple[str | None, str], dict[str, Any]]:
    if snapshot is None:
        return {}
    return {
        (s.get("namespace"), s.get("name")): s
        for s in (snapshot.normalized_catalog or {}).get("streams") or []
    }


def diff(
    old: SchemaSnapshot | None,
    new: SchemaSnapshot | None,
    *,
    selected: set[tuple[str | None, str]] | None = None,
    selected_cursors: dict[tuple[str | None, str], list[str]] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Classify changes per section 15.2. Severity depends on whether the stream
    is actually selected: a removed field nobody syncs is informational."""
    before = streams_of(old)
    after = streams_of(new)
    selected = selected if selected is not None else set(before)
    selected_cursors = selected_cursors or {}

    added: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    changed: list[dict[str, Any]] = []

    for key in after.keys() - before.keys():
        namespace, name = key
        added.append({
            "kind": "STREAM_ADDED", "severity": SchemaChangeSeverity.INFO.value,
            "namespace": namespace, "stream_name": name,
            "message": f"Bảng/stream mới: {name}. Chưa được chọn tự động.",
        })

    for key in before.keys() - after.keys():
        namespace, name = key
        is_selected = key in selected
        removed.append({
            "kind": "STREAM_REMOVED",
            "severity": (SchemaChangeSeverity.BREAKING if is_selected
                         else SchemaChangeSeverity.INFO).value,
            "namespace": namespace, "stream_name": name,
            "message": (f"Stream '{name}' đang được đồng bộ nhưng không còn ở nguồn."
                        if is_selected else f"Stream '{name}' đã bị xóa khỏi nguồn."),
        })

    for key in before.keys() & after.keys():
        namespace, name = key
        is_selected = key in selected
        old_fields = field_types(before[key].get("json_schema") or {})
        new_fields = field_types(after[key].get("json_schema") or {})

        for field_name in new_fields.keys() - old_fields.keys():
            added.append({
                "kind": "FIELD_ADDED", "severity": SchemaChangeSeverity.INFO.value,
                "namespace": namespace, "stream_name": name, "field_name": field_name,
                "after": new_fields[field_name],
                "message": f"Trường mới '{field_name}' ({new_fields[field_name]}).",
            })

        for field_name in old_fields.keys() - new_fields.keys():
            removed.append({
                "kind": "FIELD_REMOVED",
                "severity": (SchemaChangeSeverity.BREAKING if is_selected
                             else SchemaChangeSeverity.INFO).value,
                "namespace": namespace, "stream_name": name, "field_name": field_name,
                "before": old_fields[field_name],
                "message": f"Trường '{field_name}' không còn tồn tại ở nguồn.",
            })

        for field_name in old_fields.keys() & new_fields.keys():
            if old_fields[field_name] != new_fields[field_name]:
                changed.append({
                    "kind": "FIELD_TYPE_CHANGED",
                    "severity": (SchemaChangeSeverity.BREAKING if is_selected
                                 else SchemaChangeSeverity.WARNING).value,
                    "namespace": namespace, "stream_name": name, "field_name": field_name,
                    "before": old_fields[field_name], "after": new_fields[field_name],
                    "message": (f"Kiểu dữ liệu của '{field_name}' đổi từ "
                                f"{old_fields[field_name]} sang {new_fields[field_name]}."),
                })

        cursors = selected_cursors.get(key) or []
        for cursor in cursors:
            if cursor not in new_fields:
                changed.append({
                    "kind": "CURSOR_REMOVED", "severity": SchemaChangeSeverity.BREAKING.value,
                    "namespace": namespace, "stream_name": name, "field_name": cursor,
                    "message": (f"Cursor '{cursor}' không còn ở nguồn. Pipeline không thể "
                                f"chạy incremental cho stream này."),
                })

        old_pk = before[key].get("source_defined_primary_key") or []
        new_pk = after[key].get("source_defined_primary_key") or []
        if is_selected and old_pk and old_pk != new_pk:
            changed.append({
                "kind": "PRIMARY_KEY_CHANGED", "severity": SchemaChangeSeverity.BREAKING.value,
                "namespace": namespace, "stream_name": name,
                "before": str(old_pk), "after": str(new_pk),
                "message": "Primary key của stream đã thay đổi; chế độ dedupe cần được xác nhận lại.",
            })

    return {"added": added, "removed": removed, "changed": changed}


def has_breaking(result: dict[str, list[dict[str, Any]]]) -> bool:
    return any(
        change["severity"] == SchemaChangeSeverity.BREAKING.value
        for bucket in result.values() for change in bucket
    )


async def flag_affected_pipelines(
    session: AsyncSession, ctx: RequestContext, source: Source, snapshot: SchemaSnapshot
) -> None:
    """A new snapshot never silently rewrites a pipeline. Breaking changes move
    the pipeline to NEEDS_REVIEW; additive ones are accepted only if policy says so."""
    from app.models.enums import PipelineStatus

    pipelines = list((await session.scalars(
        select(Pipeline).where(
            Pipeline.workspace_id == source.workspace_id,
            Pipeline.source_id == source.id,
            Pipeline.deleted_at.is_(None),
        )
    )).all())
    auto_accept = bool(ctx.workspace_settings.get("auto_accept_additive_schema", True))

    for pipeline in pipelines:
        if pipeline.active_schema_snapshot_id == snapshot.id:
            continue
        previous = (await session.get(SchemaSnapshot, pipeline.active_schema_snapshot_id)
                    if pipeline.active_schema_snapshot_id else None)
        selected = {(s.namespace, s.stream_name) for s in pipeline.streams if s.selected}
        cursors = {(s.namespace, s.stream_name): s.cursor_fields
                   for s in pipeline.streams if s.selected}
        result = diff(previous, snapshot, selected=selected, selected_cursors=cursors)
        if has_breaking(result):
            pipeline.status = PipelineStatus.NEEDS_REVIEW
            pipeline.needs_review_reason = _summarize(result)
            await audit.record(
                session, ctx, "pipeline.schema.review_required",
                resource_type="PIPELINE", resource_id=pipeline.id, resource_name=pipeline.name,
                after={"snapshot_id": str(snapshot.id), "reason": pipeline.needs_review_reason},
            )
        elif auto_accept:
            pipeline.active_schema_snapshot_id = snapshot.id
            await _sync_stream_schemas(session, pipeline, snapshot)
    await session.flush()


def _summarize(result: dict[str, list[dict[str, Any]]]) -> str:
    breaking = [
        change for bucket in result.values() for change in bucket
        if change["severity"] == SchemaChangeSeverity.BREAKING.value
    ]
    if not breaking:
        return "Cấu trúc nguồn đã thay đổi."
    head = "; ".join(change["message"] for change in breaking[:3])
    if len(breaking) > 3:
        head += f" (+{len(breaking) - 3} thay đổi khác)"
    return head


async def _sync_stream_schemas(
    session: AsyncSession, pipeline: Pipeline, snapshot: SchemaSnapshot
) -> None:
    """Carry the new JSON schema onto still-existing selected streams."""
    catalog_streams = streams_of(snapshot)
    for stream in pipeline.streams:
        entry = catalog_streams.get((stream.namespace, stream.stream_name))
        if entry is None:
            continue
        stream.json_schema = entry.get("json_schema") or {}
        stream.schema_hash = entry.get("schema_hash")
    await session.flush()


async def approve(
    session: AsyncSession, ctx: RequestContext, pipeline: Pipeline,
    snapshot: SchemaSnapshot, *, drop_removed: bool = True
) -> Pipeline:
    """Accept a snapshot as the pipeline's active schema (section 9.3 step 7-8)."""
    from app.models.enums import PipelineStatus

    ctx.require(Module.PIPELINES, Action.EDIT)
    before_hash = None
    if pipeline.active_schema_snapshot_id:
        previous = await session.get(SchemaSnapshot, pipeline.active_schema_snapshot_id)
        before_hash = previous.catalog_hash[:16] if previous else None

    catalog_streams = streams_of(snapshot)
    for stream in list(pipeline.streams):
        key = (stream.namespace, stream.stream_name)
        entry = catalog_streams.get(key)
        if entry is None:
            if drop_removed:
                await session.delete(stream)
            else:
                stream.selected = False
            continue
        stream.json_schema = entry.get("json_schema") or {}
        stream.schema_hash = entry.get("schema_hash")
        available = set(field_types(stream.json_schema))
        if stream.cursor_fields and not set(stream.cursor_fields) <= available:
            stream.cursor_fields = list(entry.get("default_cursor_field") or [])
            if not stream.cursor_fields:
                from app.models.enums import SyncMode

                stream.sync_mode = SyncMode.FULL_REFRESH
        if stream.primary_key_fields:
            flat = {f for pk in stream.primary_key_fields for f in pk}
            if not flat <= available:
                stream.primary_key_fields = [
                    list(pk) for pk in (entry.get("source_defined_primary_key") or [])
                ]

    pipeline.active_schema_snapshot_id = snapshot.id
    pipeline.status = PipelineStatus.ACTIVE
    pipeline.needs_review_reason = None
    pipeline.version += 1
    await session.flush()

    await audit.record(
        session, ctx, "pipeline.schema.approved",
        resource_type="PIPELINE", resource_id=pipeline.id, resource_name=pipeline.name,
        before={"catalog_hash": before_hash},
        after={"catalog_hash": snapshot.catalog_hash[:16], "snapshot_id": str(snapshot.id)},
    )
    return pipeline


def capability_view(entry: dict[str, Any]) -> dict[str, Any]:
    """Snapshot stream -> the capability shape the wizard renders."""
    modes = entry.get("supported_sync_modes") or ["full_refresh"]
    reason = None
    if "incremental" not in modes:
        reason = "Connector không hỗ trợ incremental cho stream này."
    return {
        "name": entry.get("name"),
        "namespace": entry.get("namespace"),
        "supported_sync_modes": modes,
        "source_defined_cursor": entry.get("source_defined_cursor", False),
        "default_cursor_field": entry.get("default_cursor_field") or [],
        "source_defined_primary_key": entry.get("source_defined_primary_key") or [],
        "fields": field_list(entry.get("json_schema") or {}),
        "unsupported_reason": reason,
    }
