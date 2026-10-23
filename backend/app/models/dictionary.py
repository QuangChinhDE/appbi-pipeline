"""What each field of a stream means -- the data dictionary.

Kept in its own table, read by nothing on the sync path: no run, schedule,
discover or engine call touches it, so a fault here costs the Dictionary tab
and nothing else.

Two kinds of row, told apart by `origin`:

* ``AI`` -- written by the worker from a field's name and type alone (no record
  values ever leave the product). Shared: one row per connector, stream and
  field path, used by every workspace whose pipelines carry that field.
  `source_id` and `workspace_id` are NULL.
* ``USER`` -- written by a person in the Dictionary tab. Scoped to one source,
  because a workspace's wording of its own business data is not another
  workspace's to read. Never overwritten by the worker.

The meaning a connector ships in its own schema (`description` on the JSON
schema property) is not stored: it is read from the schema each time, so a
connector update corrects it everywhere at once.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

ORIGIN_AI = "AI"
ORIGIN_USER = "USER"


class DataDictionaryEntry(Base):
    __tablename__ = "data_dictionary_entries"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    connector_key: Mapped[str] = mapped_column(String(120), nullable=False)
    #: NULL on a shared (AI) row.
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    #: NULL on a shared (AI) row; the source a USER row belongs to otherwise.
    source_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    stream_name: Mapped[str] = mapped_column(String(255), nullable=False)
    #: Dotted route, `[]` marking array items: `form[].code`.
    field_path: Mapped[str] = mapped_column(String(500), nullable=False)
    #: The type the definition was written for. An AI row whose field later
    #: changes type is stale and is rewritten; a USER row is kept regardless.
    field_type: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    description_vi: Mapped[str] = mapped_column(Text, nullable=False, default="")
    origin: Mapped[str] = mapped_column(String(16), nullable=False)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        # One shared definition per connector field.
        Index("uq_dictionary_shared", "connector_key", "stream_name", "field_path",
              unique=True, postgresql_where=text("source_id IS NULL")),
        # One edit per source field.
        Index("uq_dictionary_source", "source_id", "stream_name", "field_path",
              unique=True, postgresql_where=text("source_id IS NOT NULL")),
    )
