"""Source and destination endpoints (sections 23.3, 23.4).

One router factory builds both surfaces: the domain difference lives in
`services.actors`, so /sources and /destinations cannot drift apart.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response

from app.api.deps import CtxDep, SessionDep
from app.api.v1.presenters import actor_detail, actor_view
from app.core.db import utcnow
from app.core.errors import ValidationError
from app.core.permissions import Action
from app.core.secrets import secret_store
from app.schemas.common import Paginated, PageInfo
from app.schemas.domain import (
    ActorCreate, ActorDetail, ActorTestRequest, ActorTestResult, ActorUpdate, ActorView,
)
from app.services import actors as actor_service, catalog


def build_router(kind, *, prefix: str, tag: str) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=[tag])

    @router.get("", response_model=Paginated[ActorView])
    async def list_all(
        session: SessionDep,
        ctx: CtxDep,
        q: Annotated[str | None, Query()] = None,
        connector_key: Annotated[str | None, Query()] = None,
        health: Annotated[str | None, Query()] = None,
        status: Annotated[str | None, Query()] = None,
        usage: Annotated[str | None, Query(pattern="^(used|unused)$")] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> Paginated[ActorView]:
        ctx.require(kind.module, Action.VIEW)
        rows, total, summary = await actor_service.list_actors(
            session, ctx, kind, query=q, connector_key=connector_key, health=health,
            status=status, usage=usage, limit=limit, offset=offset,
        )
        usage_map = await actor_service.pipeline_usage(session, ctx.workspace_id, kind)
        items = []
        for row in rows:
            connector = await _connector(session, row.connector_key)
            owner = await actor_service.owner_of(session, row.created_by)
            items.append(actor_view(ctx, kind, row, connector=connector,
                                    pipeline_count=usage_map.get(row.id, 0), owner=owner))
        return Paginated[ActorView](
            items=items,
            page=PageInfo(has_more=offset + len(items) < total, total=total,
                          limit=limit, offset=offset),
            summary=summary,
        )

    @router.post("", response_model=ActorDetail, status_code=201)
    async def create(payload: ActorCreate, session: SessionDep, ctx: CtxDep) -> ActorDetail:
        actor = await actor_service.create(session, ctx, kind, payload)
        await session.commit()
        await session.refresh(actor)
        return await _detail(session, ctx, actor)

    @router.post("/test", response_model=ActorTestResult)
    async def test_unsaved(
        payload: ActorTestRequest, session: SessionDep, ctx: CtxDep
    ) -> ActorTestResult:
        if not payload.connector_key:
            raise ValidationError("Thiếu connector_key.")
        result, check_token = await actor_service.test_payload(
            session, ctx, kind, payload.connector_key,
            payload.configuration or {}, payload.credentials or {},
        )
        return ActorTestResult(
            succeeded=result.succeeded, check_token=check_token,
            message=result.message, error_code=result.error_code,
            category=actor_service.category_of(result),
            technical_message=result.technical_message,
            duration_ms=result.duration_ms, tested_at=utcnow(),
        )

    @router.get("/{actor_id}", response_model=ActorDetail)
    async def detail(actor_id: uuid.UUID, session: SessionDep, ctx: CtxDep) -> ActorDetail:
        ctx.require(kind.module, Action.VIEW)
        actor = await actor_service.get(session, ctx, kind, actor_id)
        return await _detail(session, ctx, actor)

    @router.patch("/{actor_id}", response_model=ActorDetail)
    async def update(
        actor_id: uuid.UUID, payload: ActorUpdate, session: SessionDep, ctx: CtxDep
    ) -> ActorDetail:
        actor = await actor_service.update(session, ctx, kind, actor_id, payload)
        await session.commit()
        await session.refresh(actor)
        return await _detail(session, ctx, actor)

    @router.post("/{actor_id}/test", response_model=ActorTestResult)
    async def test_existing(
        actor_id: uuid.UUID, session: SessionDep, ctx: CtxDep
    ) -> ActorTestResult:
        _, result = await actor_service.test_existing(session, ctx, kind, actor_id)
        await session.commit()
        return ActorTestResult(
            succeeded=result.succeeded, message=result.message, error_code=result.error_code,
            category=actor_service.category_of(result),
            technical_message=result.technical_message,
            duration_ms=result.duration_ms, tested_at=utcnow(),
        )

    @router.post("/{actor_id}/enable", response_model=ActorDetail)
    async def enable(actor_id: uuid.UUID, session: SessionDep, ctx: CtxDep) -> ActorDetail:
        actor = await actor_service.set_enabled(session, ctx, kind, actor_id, True)
        await session.commit()
        await session.refresh(actor)
        return await _detail(session, ctx, actor)

    @router.post("/{actor_id}/disable", response_model=ActorDetail)
    async def disable(actor_id: uuid.UUID, session: SessionDep, ctx: CtxDep) -> ActorDetail:
        actor = await actor_service.set_enabled(session, ctx, kind, actor_id, False)
        await session.commit()
        await session.refresh(actor)
        return await _detail(session, ctx, actor)

    @router.delete("/{actor_id}", status_code=204)
    async def delete(
        actor_id: uuid.UUID,
        session: SessionDep,
        ctx: CtxDep,
        force: Annotated[bool, Query()] = False,
    ) -> Response:
        await actor_service.delete(session, ctx, kind, actor_id, force=force)
        await session.commit()
        return Response(status_code=204)

    @router.get("/{actor_id}/pipelines")
    async def dependents(actor_id: uuid.UUID, session: SessionDep, ctx: CtxDep) -> list[dict]:
        ctx.require(kind.module, Action.VIEW)
        await actor_service.get(session, ctx, kind, actor_id)
        rows = await actor_service.dependent_pipelines(session, ctx.workspace_id, kind, actor_id)
        return [
            {"id": str(p.id), "name": p.name, "status": p.status.value,
             "next_run_at": p.next_run_at.isoformat() if p.next_run_at else None}
            for p in rows
        ]

    async def _connector(session, connector_key: str):
        from sqlalchemy import select

        from app.models.engine import ConnectorDefinition

        return await session.scalar(
            select(ConnectorDefinition).where(ConnectorDefinition.connector_key == connector_key)
        )

    async def _detail(session, ctx, actor) -> ActorDetail:
        connector = await _connector(session, actor.connector_key)
        usage_map = await actor_service.pipeline_usage(session, ctx.workspace_id, kind)
        owner = await actor_service.owner_of(session, actor.created_by)
        credentials = await secret_store.describe(session, actor.secret_ref)
        last_discovered = (
            await actor_service.last_discovery(session, actor.id)
            if kind.side == "SOURCE" else None
        )
        return actor_detail(
            ctx, kind, actor, connector=connector,
            pipeline_count=usage_map.get(actor.id, 0), owner=owner,
            credentials=credentials, last_discovered_at=last_discovered,
        )

    return router


sources_router = build_router(actor_service.SOURCE, prefix="/sources", tag="sources")
destinations_router = build_router(
    actor_service.DESTINATION, prefix="/destinations", tag="destinations"
)
