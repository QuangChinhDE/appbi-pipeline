"""Alert rules, dedup and the in-app notification centre (section 19)."""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import RequestContext
from app.core.db import utcnow
from app.core.errors import ErrorCategory, NotFoundError
from app.core.params import as_enum
from app.core.permissions import Action, Module
from app.models.enums import (
    AlertChannel, AlertEventType, NotificationStatus, ProductResourceType, RunStatus, Severity,
)
from app.models.integration import Pipeline
from app.models.ops import AlertRule, Notification
from app.models.run import PipelineRun
from app.services import audit

logger = logging.getLogger(__name__)

DEFAULT_RULES = [
    ("Đồng bộ thất bại", AlertEventType.RUN_FAILED, 1, 900),
    ("Thất bại liên tiếp", AlertEventType.CONSECUTIVE_FAILURES, 3, 3600),
    ("Lỗi xác thực nguồn", AlertEventType.SOURCE_AUTH_ERROR, 1, 3600),
    ("Cấu trúc dữ liệu thay đổi", AlertEventType.SCHEMA_BREAKING_CHANGE, 1, 3600),
    ("Dữ liệu quá hạn làm mới", AlertEventType.FRESHNESS_BREACH, 1, 7200),
]


async def ensure_default_rules(session: AsyncSession, workspace_id: uuid.UUID) -> None:
    existing = set((await session.scalars(
        select(AlertRule.event_type).where(AlertRule.workspace_id == workspace_id)
    )).all())
    for name, event_type, threshold, cooldown in DEFAULT_RULES:
        if event_type in existing:
            continue
        session.add(AlertRule(
            workspace_id=workspace_id, name=name, event_type=event_type,
            threshold=threshold, channel=AlertChannel.IN_APP, cooldown_seconds=cooldown,
        ))
    await session.flush()


async def list_rules(session: AsyncSession, ctx: RequestContext) -> list[AlertRule]:
    ctx.require(Module.ALERTS, Action.VIEW)
    return list((await session.scalars(
        select(AlertRule).where(AlertRule.workspace_id == ctx.workspace_id)
        .order_by(AlertRule.created_at)
    )).all())


async def upsert_rule(
    session: AsyncSession, ctx: RequestContext, payload, rule_id: uuid.UUID | None = None
) -> AlertRule:
    ctx.require(Module.ALERTS, Action.EDIT if rule_id else Action.CREATE)
    if rule_id:
        rule = await session.scalar(
            select(AlertRule).where(
                AlertRule.id == rule_id, AlertRule.workspace_id == ctx.workspace_id
            )
        )
        if rule is None:
            raise NotFoundError("Không tìm thấy quy tắc cảnh báo.")
    else:
        rule = AlertRule(workspace_id=ctx.workspace_id, created_by=ctx.user_id,
                         event_type=AlertEventType(payload.event_type))
        session.add(rule)

    rule.name = payload.name
    rule.event_type = AlertEventType(payload.event_type)
    rule.resource_id = payload.resource_id
    rule.threshold = payload.threshold
    rule.channel = AlertChannel(payload.channel)
    rule.channel_config = payload.channel_config
    rule.cooldown_seconds = payload.cooldown_seconds
    rule.enabled = payload.enabled
    await session.flush()
    await audit.record(
        session, ctx, "alert.rule.updated" if rule_id else "alert.rule.created",
        resource_type="ALERT_RULE", resource_id=rule.id, resource_name=rule.name,
        after={"event_type": rule.event_type.value, "enabled": rule.enabled},
    )
    return rule


async def delete_rule(session: AsyncSession, ctx: RequestContext, rule_id: uuid.UUID) -> None:
    ctx.require(Module.ALERTS, Action.DELETE)
    rule = await session.scalar(
        select(AlertRule).where(AlertRule.id == rule_id, AlertRule.workspace_id == ctx.workspace_id)
    )
    if rule is None:
        raise NotFoundError("Không tìm thấy quy tắc cảnh báo.")
    await session.delete(rule)
    await audit.record(session, ctx, "alert.rule.deleted",
                       resource_type="ALERT_RULE", resource_id=rule_id, resource_name=rule.name)


async def list_notifications(
    session: AsyncSession, ctx: RequestContext, *, status: str | None = None, limit: int = 50
) -> list[Notification]:
    ctx.require(Module.ALERTS, Action.VIEW)
    stmt = select(Notification).where(Notification.workspace_id == ctx.workspace_id)
    if status:
        stmt = stmt.where(
            Notification.status == as_enum(status, NotificationStatus, field="status")
        )
    return list((await session.scalars(
        stmt.order_by(Notification.created_at.desc()).limit(limit)
    )).all())


async def unread_count(session: AsyncSession, workspace_id: uuid.UUID) -> int:
    return await session.scalar(
        select(func.count()).select_from(Notification).where(
            Notification.workspace_id == workspace_id,
            Notification.status == NotificationStatus.NEW,
        )
    ) or 0


async def acknowledge(
    session: AsyncSession, ctx: RequestContext, notification_id: uuid.UUID | None = None
) -> int:
    ctx.require(Module.ALERTS, Action.OPERATE)
    stmt = select(Notification).where(
        Notification.workspace_id == ctx.workspace_id,
        Notification.status == NotificationStatus.NEW,
    )
    if notification_id:
        stmt = stmt.where(Notification.id == notification_id)
    rows = list((await session.scalars(stmt)).all())
    for row in rows:
        row.status = NotificationStatus.ACKNOWLEDGED
        row.acknowledged_at = utcnow()
        row.acknowledged_by = ctx.user_id
    await session.flush()
    return len(rows)


# ── evaluation (worker) ────────────────────────────────────────────────────

async def _emit(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    rule: AlertRule | None,
    *,
    event_type: AlertEventType,
    severity: Severity,
    title: str,
    body: str | None,
    dedup_key: str,
    cooldown_seconds: int,
    resource_type: ProductResourceType | None = None,
    resource_id: uuid.UUID | None = None,
    run_id: uuid.UUID | None = None,
    remediation_action: str | None = None,
) -> Notification | None:
    """Dedup inside the cooldown window: one alert, a bumped counter, not 50."""
    window_start = utcnow() - timedelta(seconds=cooldown_seconds)
    existing = await session.scalar(
        select(Notification).where(
            Notification.workspace_id == workspace_id,
            Notification.dedup_key == dedup_key,
            Notification.created_at >= window_start,
        ).order_by(Notification.created_at.desc()).limit(1)
    )
    if existing is not None:
        existing.occurrence_count += 1
        existing.last_seen_at = utcnow()
        await session.flush()
        return None

    notification = Notification(
        workspace_id=workspace_id,
        rule_id=rule.id if rule else None,
        event_type=event_type,
        severity=severity,
        title=title,
        body=body,
        resource_type=resource_type,
        resource_id=resource_id,
        run_id=run_id,
        remediation_action=remediation_action,
        dedup_key=dedup_key,
        last_seen_at=utcnow(),
    )
    session.add(notification)
    await session.flush()
    return notification


async def _rules_for(
    session: AsyncSession, workspace_id: uuid.UUID, event_type: AlertEventType,
    resource_id: uuid.UUID | None,
) -> list[AlertRule]:
    rows = list((await session.scalars(
        select(AlertRule).where(
            AlertRule.workspace_id == workspace_id,
            AlertRule.event_type == event_type,
            AlertRule.enabled.is_(True),
        )
    )).all())
    return [r for r in rows if r.resource_id is None or r.resource_id == resource_id]


async def evaluate_run(session: AsyncSession, run: PipelineRun) -> list[Notification]:
    """Called by the worker once a run reaches a terminal state."""
    if run.status not in (RunStatus.FAILED, RunStatus.FAILED_TO_START, RunStatus.TIMED_OUT):
        return []
    pipeline = await session.get(Pipeline, run.pipeline_id)
    if pipeline is None:
        return []

    created: list[Notification] = []
    fingerprint = run.error_fingerprint or run.error_code or "unknown"

    for rule in await _rules_for(session, run.workspace_id, AlertEventType.RUN_FAILED, pipeline.id):
        note = await _emit(
            session, run.workspace_id, rule,
            event_type=AlertEventType.RUN_FAILED, severity=Severity.ERROR,
            title=f"Pipeline '{pipeline.name}' đồng bộ thất bại",
            body=run.error_summary,
            dedup_key=f"{run.workspace_id}:{pipeline.id}:RUN_FAILED:{fingerprint}",
            cooldown_seconds=rule.cooldown_seconds,
            resource_type=ProductResourceType.PIPELINE, resource_id=pipeline.id, run_id=run.id,
            remediation_action=run.remediation_action,
        )
        if note:
            created.append(note)

    if pipeline.consecutive_failures >= 2:
        for rule in await _rules_for(
            session, run.workspace_id, AlertEventType.CONSECUTIVE_FAILURES, pipeline.id
        ):
            if pipeline.consecutive_failures < rule.threshold:
                continue
            note = await _emit(
                session, run.workspace_id, rule,
                event_type=AlertEventType.CONSECUTIVE_FAILURES, severity=Severity.CRITICAL,
                title=f"Pipeline '{pipeline.name}' thất bại {pipeline.consecutive_failures} lần liên tiếp",
                body=run.error_summary,
                dedup_key=f"{run.workspace_id}:{pipeline.id}:CONSECUTIVE:{pipeline.consecutive_failures // rule.threshold}",
                cooldown_seconds=rule.cooldown_seconds,
                resource_type=ProductResourceType.PIPELINE, resource_id=pipeline.id, run_id=run.id,
                remediation_action=run.remediation_action,
            )
            if note:
                created.append(note)

    if run.error_category is ErrorCategory.AUTHENTICATION:
        for rule in await _rules_for(
            session, run.workspace_id, AlertEventType.SOURCE_AUTH_ERROR, pipeline.source_id
        ):
            note = await _emit(
                session, run.workspace_id, rule,
                event_type=AlertEventType.SOURCE_AUTH_ERROR, severity=Severity.CRITICAL,
                title="Thông tin đăng nhập nguồn không còn hợp lệ",
                body=f"Pipeline '{pipeline.name}' không xác thực được với nguồn dữ liệu.",
                dedup_key=f"{run.workspace_id}:{pipeline.source_id}:AUTH",
                cooldown_seconds=rule.cooldown_seconds,
                resource_type=ProductResourceType.SOURCE, resource_id=pipeline.source_id,
                run_id=run.id, remediation_action="UPDATE_CREDENTIALS",
            )
            if note:
                created.append(note)

    if run.error_category is ErrorCategory.DESTINATION_WRITE:
        for rule in await _rules_for(
            session, run.workspace_id, AlertEventType.DESTINATION_ERROR, pipeline.destination_id
        ):
            note = await _emit(
                session, run.workspace_id, rule,
                event_type=AlertEventType.DESTINATION_ERROR, severity=Severity.ERROR,
                title="Không ghi được dữ liệu vào đích",
                body=run.error_summary,
                dedup_key=f"{run.workspace_id}:{pipeline.destination_id}:DEST_WRITE",
                cooldown_seconds=rule.cooldown_seconds,
                resource_type=ProductResourceType.DESTINATION, resource_id=pipeline.destination_id,
                run_id=run.id, remediation_action="UPDATE_DESTINATION",
            )
            if note:
                created.append(note)

    if run.error_category is ErrorCategory.SCHEMA:
        for rule in await _rules_for(
            session, run.workspace_id, AlertEventType.SCHEMA_BREAKING_CHANGE, pipeline.id
        ):
            note = await _emit(
                session, run.workspace_id, rule,
                event_type=AlertEventType.SCHEMA_BREAKING_CHANGE, severity=Severity.WARNING,
                title=f"Cấu trúc nguồn của '{pipeline.name}' đã thay đổi",
                body=run.error_summary,
                dedup_key=f"{run.workspace_id}:{pipeline.id}:SCHEMA:{fingerprint}",
                cooldown_seconds=rule.cooldown_seconds,
                resource_type=ProductResourceType.PIPELINE, resource_id=pipeline.id, run_id=run.id,
                remediation_action="REDISCOVER_SCHEMA",
            )
            if note:
                created.append(note)

    await session.flush()
    return created


async def evaluate_freshness(session: AsyncSession, pipeline: Pipeline) -> Notification | None:
    from app.services import scheduling

    deadline = scheduling.freshness_deadline(
        pipeline.schedule_type, pipeline.schedule_config, pipeline.last_success_at
    )
    if deadline is None or utcnow() <= deadline:
        return None
    rules = await _rules_for(
        session, pipeline.workspace_id, AlertEventType.FRESHNESS_BREACH, pipeline.id
    )
    for rule in rules:
        note = await _emit(
            session, pipeline.workspace_id, rule,
            event_type=AlertEventType.FRESHNESS_BREACH, severity=Severity.WARNING,
            title=f"Pipeline '{pipeline.name}' chưa đồng bộ thành công đúng hạn",
            body=f"Hạn làm mới dữ liệu đã qua lúc {deadline.isoformat()}.",
            dedup_key=f"{pipeline.workspace_id}:{pipeline.id}:FRESHNESS:{deadline.date()}",
            cooldown_seconds=rule.cooldown_seconds,
            resource_type=ProductResourceType.PIPELINE, resource_id=pipeline.id,
            remediation_action="VIEW_PIPELINE",
        )
        if note:
            return note
    return None
