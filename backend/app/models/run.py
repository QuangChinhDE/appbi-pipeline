"""Runs and attempts (section 16)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger, DateTime, Enum as SAEnum, ForeignKey, Index, Integer, String, Text, text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TimestampMixin
from app.core.errors import ErrorCategory
from app.models.enums import RunStatus, TriggerType


class PipelineRun(Base, TimestampMixin):
    __tablename__ = "pipeline_runs"
    __table_args__ = (
        Index("ix_runs_ws_pipeline_started", "workspace_id", "pipeline_id", "started_at"),
        Index("ix_runs_status_started", "status", "started_at"),
        Index("ix_runs_ws_created", "workspace_id", "created_at"),
        # Two invariants the application used to assert with a SELECT before an
        # INSERT. That is correct with one API replica and wrong with the two
        # production runs: both requests read "nothing active" and both wrote.
        #
        # Partial, because the uniqueness only applies to a subset. A plain
        # unique index on pipeline_id would allow one run per pipeline ever.
        Index("uq_run_idempotency_key", "workspace_id", "idempotency_key",
              unique=True, postgresql_where=text("idempotency_key IS NOT NULL")),
        Index("uq_pipeline_active_run", "pipeline_id", unique=True,
              postgresql_where=text(
                  "status IN ('QUEUED', 'STARTING', 'RUNNING', 'CANCEL_REQUESTED')")),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    pipeline_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("pipelines.id", ondelete="CASCADE"), nullable=False
    )
    # Opaque engine handle -- never leaves the adapter/admin boundary.
    engine_job_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)

    trigger_type: Mapped[TriggerType] = mapped_column(
        SAEnum(TriggerType, name="trigger_type"), nullable=False
    )
    triggered_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    retry_of_run_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("pipeline_runs.id"), nullable=True
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)

    status: Mapped[RunStatus] = mapped_column(
        SAEnum(RunStatus, name="run_status"), default=RunStatus.QUEUED, nullable=False
    )
    queue_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_category: Mapped[ErrorCategory | None] = mapped_column(
        SAEnum(ErrorCategory, name="error_category"), nullable=True
    )
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    remediation_action: Mapped[str | None] = mapped_column(String(64), nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    records_synced: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    bytes_synced: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Sanitized only. Nothing here may contain credentials.
    technical_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    claimed_by: Mapped[str | None] = mapped_column(String(80), nullable=True)

    attempts: Mapped[list["RunAttempt"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", lazy="selectin",
        order_by="RunAttempt.attempt_number",
    )


class RunAttempt(Base):
    __tablename__ = "run_attempts"
    __table_args__ = (Index("ix_attempts_run", "run_id", "attempt_number"),)

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("pipeline_runs.id", ondelete="CASCADE"), nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[RunStatus] = mapped_column(SAEnum(RunStatus, name="run_status"), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    records_synced: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    bytes_synced: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    failure_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    log_path: Mapped[str | None] = mapped_column(String(400), nullable=True)
    engine_attempt_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)

    run: Mapped[PipelineRun] = relationship(back_populates="attempts")
