"""Custom connectors built in the product (Connector Builder).

A builder project is a *declarative* connector definition: a document that
describes how to talk to an HTTP API, rather than code that has to be compiled
and shipped as an image. The engine runs it with a generic runner image, so a
connector a user builds here is executed exactly like any certified one.

The project is the editable draft; publishing snapshots it into the connector
catalogue so sources can be created from it (section 53).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean, DateTime, Enum as SAEnum, ForeignKey, Index, Integer, LargeBinary,
    String, Text, text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin
from app.models.enums import BuilderStatus


class BuilderProject(Base, TimestampMixin):
    __tablename__ = "builder_projects"
    __table_args__ = (
        # Same rule as every other named resource: only live rows compete.
        Index("uq_builder_ws_name_live", "workspace_id", "name",
              unique=True, postgresql_where=text("deleted_at IS NULL")),
        Index("ix_builder_ws_status", "workspace_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # The connector key this project publishes under. Fixed at creation so a
    # rename cannot orphan the sources already built on it.
    connector_key: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    # Curated product glyph. Keeping this as a key rather than uploaded SVG
    # avoids executable image content and lets the same icon render everywhere.
    icon: Mapped[str] = mapped_column(String(40), default="api", nullable=False)

    # The structured editor state. The engine never sees this; it sees the
    # manifest compiled from it, so the editor can evolve without a migration.
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    status: Mapped[BuilderStatus] = mapped_column(
        SAEnum(BuilderStatus, name="builder_status"),
        default=BuilderStatus.DRAFT, nullable=False,
    )
    # Bumped on every publish; the catalogue entry carries the same number so an
    # operator can tell which revision a source is actually running.
    published_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_test_ok: Mapped[bool | None] = mapped_column(nullable=True)

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BuilderAISource(Base, TimestampMixin):
    """A bounded, workspace-scoped document used to understand an API."""

    __tablename__ = "builder_ai_sources"
    __table_args__ = (Index("ix_builder_ai_source_ws_created", "workspace_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False,
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("builder_projects.id", ondelete="CASCADE"), nullable=True,
    )
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(160), nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    content: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="UPLOADED", nullable=False)
    knowledge: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)


class BuilderAIPlan(Base, TimestampMixin):
    __tablename__ = "builder_ai_plans"
    __table_args__ = (Index("ix_builder_ai_plan_ws_created", "workspace_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False,
    )
    source_ids: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    intent: Mapped[str | None] = mapped_column(Text, nullable=True)
    plan: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="READY", nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)


class BuilderAISession(Base, TimestampMixin):
    __tablename__ = "builder_ai_sessions"
    __table_args__ = (
        Index("ix_builder_ai_session_project_created", "project_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("builder_projects.id", ondelete="CASCADE"), nullable=False,
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="ACTIVE", nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)


class BuilderAIMessage(Base):
    __tablename__ = "builder_ai_messages"
    __table_args__ = (Index("ix_builder_ai_message_session_created", "session_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("builder_ai_sessions.id", ondelete="CASCADE"), nullable=False,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BuilderAIChangeSet(Base, TimestampMixin):
    __tablename__ = "builder_ai_change_sets"
    __table_args__ = (Index("ix_builder_ai_changes_project_created", "project_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("builder_projects.id", ondelete="CASCADE"), nullable=False,
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("builder_ai_sessions.id", ondelete="SET NULL"), nullable=True,
    )
    base_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    proposed_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    previous_definition: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    proposed_definition: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    operations: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="PROPOSED", nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BuilderAIToolEvent(Base):
    __tablename__ = "builder_ai_tool_events"
    __table_args__ = (Index("ix_builder_ai_tool_session_created", "session_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("builder_ai_sessions.id", ondelete="CASCADE"), nullable=False,
    )
    tool_name: Mapped[str] = mapped_column(String(80), nullable=False)
    phase: Mapped[str] = mapped_column(String(32), nullable=False)
    result_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BuilderTestRun(Base):
    """Sanitized evidence only; sampled records and credentials are not retained."""

    __tablename__ = "builder_test_runs"
    __table_args__ = (Index("ix_builder_test_run_project_created", "project_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("builder_projects.id", ondelete="CASCADE"), nullable=False,
    )
    test_session_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("builder_test_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    stream_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    definition_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False)
    record_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BuilderTestSession(Base):
    __tablename__ = "builder_test_sessions"
    __table_args__ = (Index("ix_builder_test_session_project_expiry", "project_id", "expires_at"),)

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("builder_projects.id", ondelete="CASCADE"), nullable=False,
    )
    secret_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    field_names: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
