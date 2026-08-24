"""Alerts, notifications, audit, secrets and long-running operations."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean, DateTime, Enum as SAEnum, ForeignKey, Index, Integer, String, Text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin
from app.models.enums import (
    ActorType, AlertChannel, AlertEventType, AuditResult, NotificationStatus, OperationKind,
    OperationStatus, ProductResourceType, Severity,
)


class SecretRecord(Base, TimestampMixin):
    """Envelope-encrypted credential payload. Never joined into API responses."""

    __tablename__ = "secrets"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ref: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    wrapped_data_key: Mapped[str] = mapped_column(Text, nullable=False)
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    field_names: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AlertRule(Base, TimestampMixin):
    __tablename__ = "alert_rules"
    __table_args__ = (Index("ix_alert_rules_ws_event", "workspace_id", "event_type"),)

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    event_type: Mapped[AlertEventType] = mapped_column(
        SAEnum(AlertEventType, name="alert_event_type"), nullable=False
    )
    # NULL => every pipeline in the workspace.
    resource_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    threshold: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    channel: Mapped[AlertChannel] = mapped_column(
        SAEnum(AlertChannel, name="alert_channel"), default=AlertChannel.IN_APP, nullable=False
    )
    channel_config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    cooldown_seconds: Mapped[int] = mapped_column(Integer, default=900, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)


class Notification(Base, TimestampMixin):
    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_ws_status_created", "workspace_id", "status", "created_at"),
        Index("ix_notifications_dedup", "dedup_key", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    rule_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("alert_rules.id", ondelete="SET NULL"), nullable=True
    )
    event_type: Mapped[AlertEventType] = mapped_column(
        SAEnum(AlertEventType, name="alert_event_type"), nullable=False
    )
    severity: Mapped[Severity] = mapped_column(
        SAEnum(Severity, name="severity"), default=Severity.ERROR, nullable=False
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    resource_type: Mapped[ProductResourceType | None] = mapped_column(
        SAEnum(ProductResourceType, name="product_resource_type"), nullable=True
    )
    resource_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    remediation_action: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # workspace + resource + event + error fingerprint, inside the cooldown window.
    dedup_key: Mapped[str] = mapped_column(String(200), nullable=False)
    occurrence_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[NotificationStatus] = mapped_column(
        SAEnum(NotificationStatus, name="notification_status"),
        default=NotificationStatus.NEW, nullable=False,
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_by: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class AuditEvent(Base):
    """Append-only. Payload summaries are sanitized before they get here (section 20)."""

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_ws_created", "workspace_id", "created_at"),
        Index("ix_audit_resource", "resource_type", "resource_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True, index=True)
    actor_type: Mapped[ActorType] = mapped_column(
        SAEnum(ActorType, name="actor_type"), default=ActorType.USER, nullable=False
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    actor_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    action: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    resource_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    resource_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    result: Mapped[AuditResult] = mapped_column(
        SAEnum(AuditResult, name="audit_result"), default=AuditResult.SUCCESS, nullable=False
    )
    before_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    after_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(INET, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(400), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Operation(Base, TimestampMixin):
    """Generic async operation resource for test/discover/pull (section 80)."""

    __tablename__ = "operations"
    __table_args__ = (Index("ix_operations_ws_created", "workspace_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    kind: Mapped[OperationKind] = mapped_column(SAEnum(OperationKind, name="operation_kind"), nullable=False)
    status: Mapped[OperationStatus] = mapped_column(
        SAEnum(OperationStatus, name="operation_status"), default=OperationStatus.PENDING, nullable=False
    )
    resource_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    progress_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
