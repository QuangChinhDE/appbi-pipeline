"""Sources, destinations, pipelines, streams, schema snapshots (section 22)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger, Boolean, DateTime, Enum as SAEnum, ForeignKey, Index, Integer, String, Text,
    UniqueConstraint, text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TimestampMixin
from app.models.enums import (
    DestinationSyncMode, HealthLevel, OverlapPolicy, PipelineStatus, ResourceStatus, ScheduleType,
    SyncMode, TestResult,
)


class _ActorMixin:
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )


class Source(Base, TimestampMixin, _ActorMixin):
    __tablename__ = "sources"
    __table_args__ = (
        # Only live rows compete for a name: a soft-deleted source must not
        # reserve its name forever (and must not turn a re-create into a 500).
        Index("uq_source_ws_name_live", "workspace_id", "name",
              unique=True, postgresql_where=text("deleted_at IS NULL")),
        Index("ix_sources_ws_status", "workspace_id", "status"),
        Index("ix_sources_ws_connector", "workspace_id", "connector_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    connector_key: Mapped[str] = mapped_column(String(120), nullable=False)
    connector_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Non-secret configuration only. Credentials live behind `secret_ref`.
    configuration_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    secret_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)

    status: Mapped[ResourceStatus] = mapped_column(
        SAEnum(ResourceStatus, name="resource_status"), default=ResourceStatus.ACTIVE, nullable=False
    )
    health_status: Mapped[HealthLevel] = mapped_column(
        SAEnum(HealthLevel, name="health_level"), default=HealthLevel.UNKNOWN, nullable=False
    )
    health_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    health_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_test_result: Mapped[TestResult] = mapped_column(
        SAEnum(TestResult, name="test_result"), default=TestResult.NOT_TESTED, nullable=False
    )
    active_schema_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    discover_locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Destination(Base, TimestampMixin, _ActorMixin):
    __tablename__ = "destinations"
    __table_args__ = (
        Index("uq_destination_ws_name_live", "workspace_id", "name",
              unique=True, postgresql_where=text("deleted_at IS NULL")),
        Index("ix_destinations_ws_status", "workspace_id", "status"),
        Index("ix_destinations_ws_connector", "workspace_id", "connector_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    connector_key: Mapped[str] = mapped_column(String(120), nullable=False)
    connector_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    configuration_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    secret_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)

    status: Mapped[ResourceStatus] = mapped_column(
        SAEnum(ResourceStatus, name="resource_status"), default=ResourceStatus.ACTIVE, nullable=False
    )
    health_status: Mapped[HealthLevel] = mapped_column(
        SAEnum(HealthLevel, name="health_level"), default=HealthLevel.UNKNOWN, nullable=False
    )
    health_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    health_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_test_result: Mapped[TestResult] = mapped_column(
        SAEnum(TestResult, name="test_result"), default=TestResult.NOT_TESTED, nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SchemaSnapshot(Base):
    """Immutable record of one successful discover (section 15.1)."""

    __tablename__ = "schema_snapshots"
    __table_args__ = (Index("ix_schema_snapshots_source_time", "source_id", "discovered_at"),)

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    source_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("sources.id", ondelete="CASCADE"), nullable=False
    )
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    catalog_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    normalized_catalog: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    stream_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    connector_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    discovered_by: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)


class Pipeline(Base, TimestampMixin, _ActorMixin):
    __tablename__ = "pipelines"
    __table_args__ = (
        Index("uq_pipeline_ws_name_live", "workspace_id", "name",
              unique=True, postgresql_where=text("deleted_at IS NULL")),
        Index("ix_pipelines_ws_status", "workspace_id", "status"),
        Index("ix_pipelines_ws_source", "workspace_id", "source_id"),
        Index("ix_pipelines_ws_destination", "workspace_id", "destination_id"),
        Index("ix_pipelines_next_run", "next_run_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("sources.id"), nullable=False
    )
    destination_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("destinations.id"), nullable=False
    )

    status: Mapped[PipelineStatus] = mapped_column(
        SAEnum(PipelineStatus, name="pipeline_status"), default=PipelineStatus.ACTIVE, nullable=False
    )
    needs_review_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    schedule_type: Mapped[ScheduleType] = mapped_column(
        SAEnum(ScheduleType, name="schedule_type"), default=ScheduleType.MANUAL, nullable=False
    )
    schedule_config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Bangkok", nullable=False)
    overlap_policy: Mapped[OverlapPolicy] = mapped_column(
        SAEnum(OverlapPolicy, name="overlap_policy"), default=OverlapPolicy.SKIP_IF_RUNNING, nullable=False
    )
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    active_schema_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("schema_snapshots.id"), nullable=True
    )
    namespace_format: Mapped[str | None] = mapped_column(String(120), nullable=True)
    stream_prefix: Mapped[str | None] = mapped_column(String(64), nullable=True)

    last_run_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Airbyte Protocol state, as committed by the destination. This is what
    # makes incremental syncs actually incremental across runs.
    sync_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    # Airbyte refresh bookkeeping. Modern destinations require a generation id
    # per stream and refuse to start without one: `generation_id` marks the
    # current data generation, `sync_counter` gives each sync a unique id.
    generation_id: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    sync_counter: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    streams: Mapped[list["PipelineStream"]] = relationship(
        back_populates="pipeline", cascade="all, delete-orphan", lazy="selectin",
    )


class PipelineStream(Base, TimestampMixin):
    __tablename__ = "pipeline_streams"
    __table_args__ = (
        UniqueConstraint("pipeline_id", "namespace", "stream_name", name="uq_pipeline_stream"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pipeline_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("pipelines.id", ondelete="CASCADE"), nullable=False, index=True
    )
    namespace: Mapped[str | None] = mapped_column(String(200), nullable=True)
    stream_name: Mapped[str] = mapped_column(String(300), nullable=False)
    selected: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sync_mode: Mapped[SyncMode] = mapped_column(
        SAEnum(SyncMode, name="sync_mode"), default=SyncMode.FULL_REFRESH, nullable=False
    )
    destination_sync_mode: Mapped[DestinationSyncMode] = mapped_column(
        SAEnum(DestinationSyncMode, name="destination_sync_mode"),
        default=DestinationSyncMode.OVERWRITE, nullable=False,
    )
    cursor_fields: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    primary_key_fields: Mapped[list[list[str]]] = mapped_column(JSONB, default=list, nullable=False)
    selected_fields: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    json_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    schema_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    pipeline: Mapped[Pipeline] = relationship(back_populates="streams")


class PipelineStreamStat(Base):
    """Per-stream outcome of a run, shown on the run detail page (section 16.3)."""

    __tablename__ = "pipeline_stream_stats"
    __table_args__ = (Index("ix_stream_stats_run", "run_id"),)

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("pipeline_runs.id", ondelete="CASCADE"), nullable=False
    )
    namespace: Mapped[str | None] = mapped_column(String(200), nullable=True)
    stream_name: Mapped[str] = mapped_column(String(300), nullable=False)
    records_emitted: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    bytes_emitted: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    sync_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="COMPLETED", nullable=False)
