"""AI-native Builder state, durable test evidence, and connector icons.

Revision ID: a9c4e7b2d611
Revises: c5e1a9f37d24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a9c4e7b2d611"
down_revision: str | None = "c5e1a9f37d24"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "builder_projects",
        sa.Column("icon", sa.String(length=40), nullable=False, server_default="api"),
    )
    op.alter_column("builder_projects", "icon", server_default=None)

    op.create_table(
        "builder_ai_sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("mime_type", sa.String(160), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("content", sa.LargeBinary(), nullable=True),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("knowledge", postgresql.JSONB(), nullable=True),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["builder_projects.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_builder_ai_source_ws_created", "builder_ai_sources", ["workspace_id", "created_at"])

    op.create_table(
        "builder_ai_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_ids", postgresql.JSONB(), nullable=False),
        sa.Column("intent", sa.Text(), nullable=True),
        sa.Column("plan", postgresql.JSONB(), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("prompt_version", sa.String(40), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_builder_ai_plan_ws_created", "builder_ai_plans", ["workspace_id", "created_at"])

    op.create_table(
        "builder_ai_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["builder_projects.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_builder_ai_session_project_created", "builder_ai_sessions", ["project_id", "created_at"])

    op.create_table(
        "builder_ai_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("context", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["builder_ai_sessions.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_builder_ai_message_session_created", "builder_ai_messages", ["session_id", "created_at"])

    op.create_table(
        "builder_ai_change_sets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("base_hash", sa.String(64), nullable=False),
        sa.Column("proposed_hash", sa.String(64), nullable=False),
        sa.Column("previous_definition", postgresql.JSONB(), nullable=False),
        sa.Column("proposed_definition", postgresql.JSONB(), nullable=False),
        sa.Column("operations", postgresql.JSONB(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("prompt_version", sa.String(40), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decided_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["builder_projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["builder_ai_sessions.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_builder_ai_changes_project_created", "builder_ai_change_sets", ["project_id", "created_at"])

    op.create_table(
        "builder_ai_tool_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tool_name", sa.String(80), nullable=False),
        sa.Column("phase", sa.String(32), nullable=False),
        sa.Column("result_summary", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["builder_ai_sessions.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_builder_ai_tool_session_created", "builder_ai_tool_events", ["session_id", "created_at"])

    op.create_table(
        "builder_test_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("secret_ref", sa.String(255), nullable=False),
        sa.Column("field_names", postgresql.JSONB(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["builder_projects.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_builder_test_session_project_expiry", "builder_test_sessions", ["project_id", "expires_at"])

    op.create_table(
        "builder_test_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("test_session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("stream_name", sa.String(160), nullable=True),
        sa.Column("definition_hash", sa.String(64), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("record_count", sa.Integer(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["builder_projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["test_session_id"], ["builder_test_sessions.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_builder_test_run_project_created", "builder_test_runs", ["project_id", "created_at"])


def downgrade() -> None:
    for table in (
        "builder_test_runs", "builder_test_sessions", "builder_ai_tool_events",
        "builder_ai_change_sets", "builder_ai_messages", "builder_ai_sessions",
        "builder_ai_plans", "builder_ai_sources",
    ):
        op.drop_table(table)
    op.drop_column("builder_projects", "icon")
