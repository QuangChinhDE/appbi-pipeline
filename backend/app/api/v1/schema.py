"""Schema discovery endpoints, hung off /sources (sections 15, 23.3)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CtxDep, SessionDep
from app.core.permissions import Action, Module
from app.schemas.domain import SchemaSnapshotView, StreamCapability
from app.services import actors as actor_service, schema_service

router = APIRouter(prefix="/sources", tags=["schema"])


def _snapshot_view(snapshot) -> SchemaSnapshotView:
    streams = (snapshot.normalized_catalog or {}).get("streams") or []
    return SchemaSnapshotView(
        id=snapshot.id,
        source_id=snapshot.source_id,
        discovered_at=snapshot.discovered_at,
        catalog_hash=snapshot.catalog_hash,
        stream_count=snapshot.stream_count,
        connector_version=snapshot.connector_version,
        streams=[StreamCapability(**schema_service.capability_view(entry)) for entry in streams],
    )


@router.post("/{source_id}/discover", response_model=SchemaSnapshotView)
async def discover(
    source_id: uuid.UUID,
    session: SessionDep,
    ctx: CtxDep,
    force: Annotated[bool, Query()] = False,
) -> SchemaSnapshotView:
    snapshot = await schema_service.discover(session, ctx, source_id, force=force)
    await session.commit()
    return _snapshot_view(snapshot)


@router.get("/{source_id}/schema", response_model=SchemaSnapshotView | None)
async def latest_schema(
    source_id: uuid.UUID, session: SessionDep, ctx: CtxDep
) -> SchemaSnapshotView | None:
    ctx.require(Module.SOURCES, Action.VIEW)
    await actor_service.get(session, ctx, actor_service.SOURCE, source_id)
    snapshot = await schema_service.latest_snapshot(session, source_id)
    return _snapshot_view(snapshot) if snapshot else None
