"""The data dictionary: what each field of a stream means.

Additive only -- one new table, nothing existing is altered, and nothing on
the sync path reads it. See `app/models/dictionary.py`.

Revision ID: d9e4a6b13f70
Revises: c3a58f1d97e4
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d9e4a6b13f70"
down_revision: str | None = "c3a58f1d97e4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "data_dictionary_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("connector_key", sa.String(length=120), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("stream_name", sa.String(length=255), nullable=False),
        sa.Column("field_path", sa.String(length=500), nullable=False),
        sa.Column("field_type", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("description_vi", sa.Text(), nullable=False),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "uq_dictionary_shared", "data_dictionary_entries",
        ["connector_key", "stream_name", "field_path"],
        unique=True, postgresql_where=sa.text("source_id IS NULL"),
    )
    op.create_index(
        "uq_dictionary_source", "data_dictionary_entries",
        ["source_id", "stream_name", "field_path"],
        unique=True, postgresql_where=sa.text("source_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_dictionary_source", table_name="data_dictionary_entries")
    op.drop_index("uq_dictionary_shared", table_name="data_dictionary_entries")
    op.drop_table("data_dictionary_entries")
