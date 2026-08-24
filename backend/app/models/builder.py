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
    DateTime, Enum as SAEnum, ForeignKey, Index, Integer, String, Text, text,
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
