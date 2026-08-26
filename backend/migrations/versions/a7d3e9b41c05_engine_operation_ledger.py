"""engine operation ledger

The durable record of every intended engine mutation, written and committed
before the engine is called. See `app/models/outbox.py` for why: without it, a
Product DB rollback after a successful engine call leaves a resource holding
customer credentials that nothing in the product knows about.

Revision ID: a7d3e9b41c05
Revises: f2c0a15b8e37
Create Date: 2026-08-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a7d3e9b41c05"
down_revision = "f2c0a15b8e37"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "engine_operations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("resource_type", sa.String(length=20), nullable=False),
        sa.Column("operation", sa.String(length=20), nullable=False),
        sa.Column("product_resource_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False,
                  server_default="PENDING"),
        sa.Column("engine_ref", sa.String(length=255), nullable=True),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_engine_operations_workspace_id", "engine_operations",
                    ["workspace_id"])
    op.create_index("ix_engine_operations_product_resource_id", "engine_operations",
                    ["product_resource_id"])
    op.create_index("ix_engine_operations_state", "engine_operations", ["state"])
    # The sweeper's query: everything unfinished, oldest first.
    op.create_index("ix_engine_operations_open", "engine_operations",
                    ["state", "updated_at"])
    # One row per (resource, verb), so a retry re-enters the same saga instead
    # of starting a second one against the same engine resource.
    op.create_unique_constraint("uq_engine_operation_resource_operation",
                                "engine_operations",
                                ["product_resource_id", "operation"])


def downgrade() -> None:
    op.drop_constraint("uq_engine_operation_resource_operation", "engine_operations",
                       type_="unique")
    op.drop_index("ix_engine_operations_open", table_name="engine_operations")
    op.drop_index("ix_engine_operations_state", table_name="engine_operations")
    op.drop_index("ix_engine_operations_product_resource_id",
                  table_name="engine_operations")
    op.drop_index("ix_engine_operations_workspace_id", table_name="engine_operations")
    op.drop_table("engine_operations")
