"""Overview, monitoring, connectors, alerts, audit and admin endpoints."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Query, Response
from sqlalchemy import select

from app.api.deps import AdminDep, CtxDep, SessionDep
from app.api.v1.presenters import connector_detail, connector_view, run_view
from app.api.v1.pipelines import _view as pipeline_view_of
from app.api.v1.runs import _pipeline_ref
from app.core.config import settings
from app.core.params import as_enum
from app.core.permissions import Action, Module
from app.models.engine import ConnectorDefinition, EngineMapping
from app.models.enums import (
    Certification, ConnectorStatus, PipelineHealth, ProductResourceType, RunStatus,
)
from app.models.identity import User
from app.models.integration import Pipeline
from app.models.ops import AuditEvent
from app.schemas.common import Acknowledged, PageInfo, Paginated
from app.schemas.domain import (
    AlertRuleView, AlertRuleWrite, AuditEventView, ConnectorDetail, ConnectorView,
    EngineReconcileItem, EngineReconcileView, EngineStatusView, MonitoringPipelineRow,
    MonitoringResponse, NotificationView, OverviewKpis,
    OverviewResponse, PipelineView, RunView,
)
from app.services import alerts as alert_service, audit, catalog, monitoring, reconcile

_ICON_DIR = Path(__file__).resolve().parents[2] / "resources" / "connector_icons"
_SAFE_CONNECTOR_KEY = re.compile(r"[a-z0-9][a-z0-9._-]{0,119}")

router = APIRouter(tags=["operations"])


# ── overview ───────────────────────────────────────────────────────────────

@router.get("/overview", response_model=OverviewResponse)
async def overview(session: SessionDep, ctx: CtxDep) -> OverviewResponse:
    ctx.require(Module.MONITORING, Action.VIEW)

    async def to_views(runs) -> list[RunView]:
        from app.services import runs as run_service

        out: list[RunView] = []
        for run in runs:
            pipeline = await session.get(Pipeline, run.pipeline_id)
            user = await session.get(User, run.triggered_by) if run.triggered_by else None
            out.append(run_view(ctx, run, pipeline_ref=await _pipeline_ref(session, pipeline),
                                triggered_by=user, is_stale=run_service.is_stale(run)))
        return out

    failures = await monitoring.recent_runs(
        session, ctx.workspace_id,
        statuses=[RunStatus.FAILED, RunStatus.FAILED_TO_START, RunStatus.TIMED_OUT], limit=5,
    )
    running = await monitoring.recent_runs(
        session, ctx.workspace_id,
        statuses=[RunStatus.QUEUED, RunStatus.STARTING, RunStatus.RUNNING,
                  RunStatus.CANCEL_REQUESTED], limit=5,
    )
    successes = await monitoring.recent_runs(
        session, ctx.workspace_id, statuses=[RunStatus.SUCCEEDED], limit=5,
    )

    rows, _ = await monitoring.monitoring_rows(session, ctx)
    attention = [
        await pipeline_view_of(session, ctx, row["pipeline"], health=row["health"])
        for row in rows
        if row["health"] in (PipelineHealth.ACTION_REQUIRED, PipelineHealth.FAILED)
           or row["freshness_breached"]
    ][:5]

    return OverviewResponse(
        kpis=OverviewKpis(**await monitoring.kpis(session, ctx.workspace_id)),
        recent_failures=await to_views(failures),
        running=await to_views(running),
        recent_successes=await to_views(successes),
        attention_pipelines=attention,
        connector_updates=[
            connector_view(c) for c in await monitoring.connectors_with_updates(session)
        ],
        onboarding=await monitoring.onboarding_state(session, ctx.workspace_id),
    )


@router.get("/monitoring", response_model=MonitoringResponse)
async def monitoring_view(session: SessionDep, ctx: CtxDep) -> MonitoringResponse:
    ctx.require(Module.MONITORING, Action.VIEW)
    rows, counts = await monitoring.monitoring_rows(session, ctx)
    return MonitoringResponse(
        engine=await monitoring.engine_status(session, ctx, detailed=ctx.is_platform_admin),
        pipelines=[
            MonitoringPipelineRow(
                pipeline=await pipeline_view_of(session, ctx, row["pipeline"],
                                                health=row["health"]),
                freshness_deadline=row["freshness_deadline"],
                freshness_breached=row["freshness_breached"],
                failure_streak=row["failure_streak"],
                last_success_age_seconds=row["last_success_age_seconds"],
            )
            for row in rows
        ],
        counts=counts,
    )


@router.get("/engine/status", response_model=EngineStatusView)
async def engine_status(session: SessionDep, ctx: CtxDep) -> EngineStatusView:
    ctx.require(Module.SETTINGS, Action.VIEW)
    return EngineStatusView(
        **await monitoring.engine_status(session, ctx, detailed=ctx.is_platform_admin)
    )


@router.get("/engine/reconcile", response_model=EngineReconcileView)
async def engine_reconcile(session: SessionDep, ctx: CtxDep, _: AdminDep) -> EngineReconcileView:
    """Ask the engine whether it still has what this database says it has.

    Admin-only and deliberately not on any page's load path: it is one engine
    round trip per mapped resource, and the question is only interesting after
    a restore or a migration.
    """
    report = await reconcile.reconcile(session, workspace_id=ctx.workspace_id)
    return EngineReconcileView(
        consistent=report.consistent,
        engine_reachable=report.engine_reachable,
        checked=report.checked,
        present=report.present,
        foreign=report.foreign,
        missing=[EngineReconcileItem(resource_type=item.resource_type,
                                     resource_id=item.resource_id,
                                     name=item.name)
                 for item in report.missing],
        detail=report.detail,
    )


# ── connectors ─────────────────────────────────────────────────────────────

@router.get("/connectors", response_model=list[ConnectorView])
async def list_connectors(
    session: SessionDep,
    ctx: CtxDep,
    type: Annotated[str | None, Query()] = None,
    q: Annotated[str | None, Query()] = None,
    category: Annotated[str | None, Query()] = None,
    limit: Annotated[int | None, Query(ge=1, le=1000)] = None,
) -> list[ConnectorView]:
    ctx.require(Module.CONNECTORS, Action.VIEW)
    rows = await catalog.list_connectors(
        session, connector_type=type, query=q, category=category,
        include_hidden=ctx.is_platform_admin, limit=limit,
    )
    return [connector_view(row) for row in rows]


@router.get("/connectors/{connector_key}", response_model=ConnectorDetail)
async def connector(connector_key: str, session: SessionDep, ctx: CtxDep) -> ConnectorDetail:
    ctx.require(Module.CONNECTORS, Action.VIEW)
    return connector_detail(await catalog.get_connector(session, connector_key))


@router.get("/connectors/{connector_key}/icon.svg", include_in_schema=False)
async def connector_icon(connector_key: str) -> Response:
    """Serve the vendored connector logo.

    The logo is served from here rather than linked to the upstream registry so
    that no browser request leaves for it and the catalogue still renders offline
    (section 11.4). An unknown or missing logo is a 404, not an error the page
    has to handle: the UI falls back to its built-in glyph.
    """
    # The key comes from the URL, so it must never be able to escape the folder.
    if not _SAFE_CONNECTOR_KEY.fullmatch(connector_key):
        return Response(status_code=404)
    path = _ICON_DIR / f"{connector_key}.svg"
    if not path.is_file():
        return Response(status_code=404)
    return Response(
        content=path.read_bytes(),
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400, immutable"},
    )


# ── alerts ─────────────────────────────────────────────────────────────────

@router.get("/alerts/rules", response_model=list[AlertRuleView])
async def list_rules(session: SessionDep, ctx: CtxDep) -> list[AlertRuleView]:
    return [AlertRuleView.model_validate(r) for r in await alert_service.list_rules(session, ctx)]


@router.post("/alerts/rules", response_model=AlertRuleView, status_code=201)
async def create_rule(payload: AlertRuleWrite, session: SessionDep, ctx: CtxDep) -> AlertRuleView:
    rule = await alert_service.upsert_rule(session, ctx, payload)
    await session.commit()
    return AlertRuleView.model_validate(rule)


@router.patch("/alerts/rules/{rule_id}", response_model=AlertRuleView)
async def update_rule(
    rule_id: uuid.UUID, payload: AlertRuleWrite, session: SessionDep, ctx: CtxDep
) -> AlertRuleView:
    rule = await alert_service.upsert_rule(session, ctx, payload, rule_id)
    await session.commit()
    return AlertRuleView.model_validate(rule)


@router.delete("/alerts/rules/{rule_id}", status_code=204)
async def delete_rule(rule_id: uuid.UUID, session: SessionDep, ctx: CtxDep) -> Response:
    await alert_service.delete_rule(session, ctx, rule_id)
    await session.commit()
    return Response(status_code=204)


@router.get("/alerts/notifications", response_model=list[NotificationView])
async def notifications(
    session: SessionDep,
    ctx: CtxDep,
    status: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[NotificationView]:
    rows = await alert_service.list_notifications(session, ctx, status=status, limit=limit)
    return [NotificationView.model_validate(r) for r in rows]


@router.get("/alerts/unread-count")
async def unread(session: SessionDep, ctx: CtxDep) -> dict[str, int]:
    ctx.require(Module.ALERTS, Action.VIEW)
    return {"count": await alert_service.unread_count(session, ctx.workspace_id)}


@router.post("/alerts/notifications/acknowledge", response_model=Acknowledged)
async def acknowledge(
    session: SessionDep,
    ctx: CtxDep,
    notification_id: Annotated[uuid.UUID | None, Query()] = None,
) -> Acknowledged:
    count = await alert_service.acknowledge(session, ctx, notification_id)
    await session.commit()
    return Acknowledged(message=f"Đã đánh dấu {count} thông báo.")


# ── audit ──────────────────────────────────────────────────────────────────

@router.get("/audit", response_model=Paginated[AuditEventView])
async def audit_log(
    session: SessionDep,
    ctx: CtxDep,
    action: Annotated[str | None, Query()] = None,
    resource_type: Annotated[str | None, Query()] = None,
    resource_id: Annotated[uuid.UUID | None, Query()] = None,
    actor_id: Annotated[uuid.UUID | None, Query()] = None,
    since: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Paginated[AuditEventView]:
    ctx.require(Module.AUDIT, Action.VIEW)
    stmt = select(AuditEvent).where(AuditEvent.workspace_id == ctx.workspace_id)
    if action:
        stmt = stmt.where(AuditEvent.action.like(f"{action}%"))
    if resource_type:
        stmt = stmt.where(AuditEvent.resource_type == resource_type.upper())
    if resource_id:
        stmt = stmt.where(AuditEvent.resource_id == resource_id)
    if actor_id:
        stmt = stmt.where(AuditEvent.actor_id == actor_id)
    if since:
        stmt = stmt.where(AuditEvent.created_at >= since)

    from sqlalchemy import func

    total = await session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list((await session.scalars(
        stmt.order_by(AuditEvent.created_at.desc()).limit(limit).offset(offset)
    )).all())
    return Paginated[AuditEventView](
        items=[AuditEventView.model_validate(r) for r in rows],
        page=PageInfo(has_more=offset + len(rows) < total, total=total,
                      limit=limit, offset=offset),
    )


# ── admin ──────────────────────────────────────────────────────────────────

admin_router = APIRouter(prefix="/admin", tags=["admin"])


@admin_router.post("/connectors/refresh")
async def refresh_catalog(
    session: SessionDep,
    ctx: AdminDep,
    connector_key: Annotated[str | None, Query()] = None,
) -> dict:
    """Pulls each connector image and reads its real SPEC. Slow on first run."""
    outcome = await catalog.refresh_specs(session, only_key=connector_key)
    await audit.record(session, ctx, "connector.catalog.refreshed",
                       resource_type="CONNECTOR", after={"result": outcome})
    await session.commit()
    return {"result": outcome}


@admin_router.post("/connectors/{connector_key}/certification")
async def set_certification(
    connector_key: str,
    session: SessionDep,
    ctx: AdminDep,
    certification: Annotated[str, Query()],
    reason: Annotated[str | None, Query()] = None,
) -> ConnectorDetail:
    connector = await catalog.get_connector(session, connector_key)
    before = connector.certification.value
    connector.certification = as_enum(certification, Certification, field="certification")
    connector.disabled_reason = reason
    connector.status = (
        ConnectorStatus.DISABLED if connector.certification is Certification.BLOCKED
        else ConnectorStatus.ACTIVE
    )
    await audit.record(session, ctx, "connector.certification.changed",
                       resource_type="CONNECTOR", resource_name=connector_key,
                       before={"certification": before},
                       after={"certification": connector.certification.value})
    await session.commit()
    return connector_detail(connector)


@admin_router.post("/connectors/{connector_key}/pin")
async def pin_version(
    connector_key: str, session: SessionDep, ctx: AdminDep, version: Annotated[str, Query()]
) -> ConnectorDetail:
    connector = await catalog.get_connector(session, connector_key)
    before = connector.version
    connector.version = version
    connector.image_pulled = False
    connector.spec_source = "PINNED"
    await audit.record(session, ctx, "connector.version.changed",
                       resource_type="CONNECTOR", resource_name=connector_key,
                       before={"version": before}, after={"version": version})
    await session.commit()
    return connector_detail(connector)


@admin_router.get("/resources/{product_id}/engine-debug")
async def engine_debug(product_id: uuid.UUID, session: SessionDep, ctx: AdminDep) -> dict:
    """Admin-only, audited window onto the mapping layer (section 59).
    The engine ref is partially masked even here."""
    mappings = list((await session.scalars(
        select(EngineMapping).where(EngineMapping.product_resource_id == product_id)
    )).all())
    await audit.record(session, ctx, "admin.engine_debug.viewed",
                       resource_type="ENGINE_MAPPING", resource_id=product_id)
    await session.commit()

    def mask(ref: str) -> str:
        return ref if len(ref) <= 12 else f"{ref[:8]}...{ref[-4:]}"

    return {
        "product_resource_id": str(product_id),
        "engine_type": settings.engine_type,
        "adapter_contract_version": settings.adapter_contract_version,
        "mappings": [
            {
                "product_resource_type": m.product_resource_type.value,
                "engine_resource_type": m.engine_resource_type.value,
                "engine_resource_ref_masked": mask(m.engine_resource_ref),
                "engine_version": m.engine_version,
                "created_at": m.created_at.isoformat(),
            }
            for m in mappings
        ],
    }


@admin_router.get("/compatibility")
async def compatibility(session: SessionDep, ctx: AdminDep) -> dict:
    """Section 60 compatibility matrix, generated from what is actually pinned."""
    rows = list((await session.scalars(select(ConnectorDefinition))).all())
    health = await monitoring.engine_status(session, ctx, detailed=True)
    return {
        "product_version": settings.product_version,
        # What this running process actually is. Reported by the deployment
        # rather than read from a checkout, because the release gate has to
        # bind evidence to the build that produced it.
        "build": {
            "sha": settings.build_sha,
            "digest": settings.build_digest,
            "built_at": settings.build_time,
        },
        "workspace_fingerprint": (settings.airbyte_workspace_id or "")[:8] or None,
        "engine": {
            "type": settings.engine_type,
            "version": health.get("version"),
            "adapter_contract_version": settings.adapter_contract_version,
            "reachable": health.get("operational"),
        },
        # Two versions, never merged into one field. `bundled` is what this
        # product locked and ships; `engine` is the tag the deployment will
        # actually run. They are the same in embedded mode and routinely differ
        # in API mode, where Airbyte pins its own — and a single "image" line
        # would then be a confident statement of the wrong thing.
        "connectors": {
            row.connector_key: {
                "bundled_image": f"{row.docker_repository}:{row.version}",
                "engine_image": (f"{row.docker_repository}:{row.engine_version}"
                                 if row.engine_version else None),
                "version_matches_engine": (row.engine_version is None
                                           or row.engine_version == row.version),
                "certification": row.certification.value,
                "spec_source": row.spec_source,
                "last_refreshed_at": row.last_refreshed_at.isoformat()
                if row.last_refreshed_at else None,
            }
            for row in rows
        },
    }
