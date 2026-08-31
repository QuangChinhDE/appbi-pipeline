"""Add the AppBI Transform domain without changing ingestion run tables.

Revision ID: d7f3a81c9e20
Revises: a9c4e7b2d611
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d7f3a81c9e20"
down_revision: str | None = "a9c4e7b2d611"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB()
HEALTH = postgresql.ENUM(name="health_level", create_type=False)
RUN_STATUS = postgresql.ENUM(name="run_status", create_type=False)
TRIGGER_TYPE = postgresql.ENUM(name="trigger_type", create_type=False)
ERROR_CATEGORY = postgresql.ENUM(name="error_category", create_type=False)


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )


def upgrade() -> None:
    op.execute("ALTER TYPE trigger_type ADD VALUE IF NOT EXISTS 'AFTER_UPSTREAM'")
    op.create_table(
        "transforms",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("workspace_id", UUID, nullable=False),
        sa.Column("destination_id", UUID, nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("default_schema", sa.String(200), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="ACTIVE"),
        sa.Column("health_status", HEALTH, nullable=False, server_default="UNKNOWN"),
        sa.Column("health_message", sa.Text(), nullable=True),
        sa.Column("execution_trigger", sa.String(40), nullable=False, server_default="MANUAL"),
        sa.Column("trigger_config", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("dbt_core_version", sa.String(32), nullable=False),
        sa.Column("dbt_adapter_name", sa.String(80), nullable=False),
        sa.Column("dbt_adapter_version", sa.String(32), nullable=False),
        sa.Column("last_run_id", UUID, nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", UUID, nullable=True),
        sa.Column("updated_by", UUID, nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["destination_id"], ["destinations.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"]),
    )
    op.create_index(
        "uq_transform_ws_name_live", "transforms", ["workspace_id", "name"], unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index("ix_transforms_ws_health", "transforms", ["workspace_id", "health_status"])
    op.create_index("ix_transforms_destination", "transforms", ["destination_id"])

    op.create_table(
        "transform_models",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("transform_id", UUID, nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("layer", sa.String(24), nullable=False, server_default="STAGING"),
        sa.Column("materialization", sa.String(24), nullable=False, server_default="VIEW"),
        sa.Column("sql", sa.Text(), nullable=False),
        sa.Column("output_schema", sa.String(200), nullable=True),
        sa.Column("relation_name", sa.String(200), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("tags", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("config_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_by", UUID, nullable=True),
        sa.Column("updated_by", UUID, nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["transform_id"], ["transforms.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "uq_transform_model_name_live", "transform_models", ["transform_id", "name"],
        unique=True, postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_transform_models_transform_layer", "transform_models", ["transform_id", "layer"])

    op.create_table(
        "transform_tests",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("model_id", UUID, nullable=False),
        sa.Column("column_name", sa.String(200), nullable=True),
        sa.Column("rule", sa.String(40), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False, server_default="ERROR"),
        sa.Column("config_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("last_status", sa.String(24), nullable=False, server_default="NOT_RUN"),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", UUID, nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["model_id"], ["transform_models.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_transform_tests_model", "transform_tests", ["model_id"])

    op.create_table(
        "data_assets",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("workspace_id", UUID, nullable=False),
        sa.Column("destination_id", UUID, nullable=False),
        sa.Column("catalog_name", sa.String(200), nullable=True),
        sa.Column("schema_name", sa.String(200), nullable=False),
        sa.Column("relation_name", sa.String(300), nullable=False),
        sa.Column("relation_type", sa.String(24), nullable=False, server_default="TABLE"),
        sa.Column("asset_type", sa.String(24), nullable=False, server_default="RAW"),
        sa.Column("owner_type", sa.String(24), nullable=False),
        sa.Column("owner_resource_id", UUID, nullable=False),
        sa.Column("pipeline_id", UUID, nullable=True),
        sa.Column("pipeline_stream_id", UUID, nullable=True),
        sa.Column("transform_id", UUID, nullable=True),
        sa.Column("transform_model_id", UUID, nullable=True),
        sa.Column("physical_identity", sa.String(64), nullable=False),
        sa.Column("resolution_status", sa.String(24), nullable=False, server_default="UNRESOLVED"),
        sa.Column("schema_metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("last_ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fresh_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["destination_id"], ["destinations.id"]),
        sa.ForeignKeyConstraint(["pipeline_id"], ["pipelines.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["pipeline_stream_id"], ["pipeline_streams.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["transform_id"], ["transforms.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["transform_model_id"], ["transform_models.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "uq_data_asset_physical_live", "data_assets", ["destination_id", "physical_identity"],
        unique=True, postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_data_assets_ws_destination", "data_assets", ["workspace_id", "destination_id"])
    op.create_index("ix_data_assets_owner", "data_assets", ["owner_type", "owner_resource_id"])

    op.create_table(
        "transform_inputs",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("transform_id", UUID, nullable=False),
        sa.Column("data_asset_id", UUID, nullable=False),
        sa.Column("source_name", sa.String(200), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_timestamps(),
        sa.ForeignKeyConstraint(["transform_id"], ["transforms.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["data_asset_id"], ["data_assets.id"]),
        sa.UniqueConstraint("transform_id", "data_asset_id", name="uq_transform_input_asset"),
        sa.UniqueConstraint("transform_id", "source_name", name="uq_transform_input_source_name"),
    )

    op.create_table(
        "transform_dependencies",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("transform_id", UUID, nullable=False),
        sa.Column("upstream_asset_id", UUID, nullable=True),
        sa.Column("upstream_model_id", UUID, nullable=True),
        sa.Column("downstream_model_id", UUID, nullable=False),
        sa.Column("dbt_unique_id", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["transform_id"], ["transforms.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["upstream_asset_id"], ["data_assets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["upstream_model_id"], ["transform_models.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["downstream_model_id"], ["transform_models.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_transform_dependencies_transform", "transform_dependencies", ["transform_id"])
    op.create_index(
        "ix_transform_dependencies_downstream", "transform_dependencies", ["downstream_model_id"])

    op.create_table(
        "transform_runs",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("workspace_id", UUID, nullable=False),
        sa.Column("transform_id", UUID, nullable=False),
        sa.Column("operation", sa.String(24), nullable=False),
        sa.Column("selected_model_id", UUID, nullable=True),
        sa.Column("trigger_type", TRIGGER_TYPE, nullable=False),
        sa.Column("triggered_by", UUID, nullable=True),
        sa.Column("retry_of_run_id", UUID, nullable=True),
        sa.Column("idempotency_key", sa.String(120), nullable=True),
        sa.Column("status", RUN_STATUS, nullable=False, server_default="QUEUED"),
        sa.Column("queue_reason", sa.String(120), nullable=True),
        sa.Column("error_category", ERROR_CATEGORY, nullable=True),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("error_fingerprint", sa.String(64), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("remediation_action", sa.String(64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("models_built", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tests_passed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tests_failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tests_warned", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rows_affected", sa.BigInteger(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("technical_metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("claimed_by", sa.String(100), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["transform_id"], ["transforms.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["selected_model_id"], ["transform_models.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["triggered_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["retry_of_run_id"], ["transform_runs.id"]),
    )
    op.create_index("ix_transform_runs_ws_created", "transform_runs", ["workspace_id", "created_at"])
    op.create_index(
        "ix_transform_runs_transform_created", "transform_runs", ["transform_id", "created_at"])
    op.create_index("ix_transform_runs_status_started", "transform_runs", ["status", "started_at"])
    op.create_index(
        "uq_transform_active_build", "transform_runs", ["transform_id"], unique=True,
        postgresql_where=sa.text(
            "status IN ('QUEUED', 'STARTING', 'RUNNING', 'CANCEL_REQUESTED') "
            "AND operation IN ('RUN_MODEL', 'BUILD')"
        ),
    )
    op.create_index(
        "uq_transform_run_idempotency", "transform_runs", ["workspace_id", "idempotency_key"],
        unique=True, postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )

    op.create_table(
        "transform_run_attempts",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("run_id", UUID, nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", RUN_STATUS, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_summary", sa.Text(), nullable=True),
        sa.Column("log_path", sa.String(500), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["transform_runs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("run_id", "attempt_number", name="uq_transform_run_attempt"),
    )

    op.create_table(
        "transform_run_nodes",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("run_id", UUID, nullable=False),
        sa.Column("model_id", UUID, nullable=True),
        sa.Column("dbt_unique_id", sa.String(500), nullable=False),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("resource_type", sa.String(24), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("execution_time", sa.Float(), nullable=True),
        sa.Column("relation_name", sa.String(500), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("adapter_response", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.ForeignKeyConstraint(["run_id"], ["transform_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["model_id"], ["transform_models.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("run_id", "dbt_unique_id", name="uq_transform_run_node"),
    )
    op.create_index("ix_transform_run_nodes_run", "transform_run_nodes", ["run_id"])

    op.create_table(
        "transform_artifacts",
        sa.Column("run_id", UUID, primary_key=True),
        sa.Column("manifest", JSONB, nullable=True),
        sa.Column("run_results", JSONB, nullable=True),
        sa.Column("compiled_sql", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("preview", JSONB, nullable=True),
        sa.Column("log_text", sa.Text(), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["transform_runs.id"], ondelete="CASCADE"),
    )


def downgrade() -> None:
    for table in (
        "transform_artifacts", "transform_run_nodes", "transform_run_attempts", "transform_runs",
        "transform_dependencies", "transform_inputs", "data_assets", "transform_tests",
        "transform_models", "transforms",
    ):
        op.drop_table(table)
