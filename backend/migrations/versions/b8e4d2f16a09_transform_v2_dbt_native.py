"""Transform V2: dbt project files become the source of truth.

The V1 domain modelled a dbt project as product rows -- `transform_models` held
SQL, `transform_tests` held four test kinds, `transform_dependencies` held a
graph AppBI computed itself -- and generated a dbt project from them on every
run. That design could only ever express the subset of dbt somebody had already
added a column for, and every dbt capability cost a migration, an API change, a
form and a generator change before it could be used at all.

V2 stores the project files. dbt parses them, and everything the product shows
about resources, dependencies, status and columns is read out of dbt's own
artifacts. The tables below are what AppBI genuinely owns: which projects exist,
which credentials they run with, what was executed, and which exact code
production is allowed to execute.

This migration is destructive by decision. Transform V1 had no production data
to preserve, and maintaining a compatibility layer for a domain the rework
exists to remove would have produced a hybrid worse than either design. Export
any V1 Transform to a dbt project ZIP before running it if there is anything to
keep.

Nothing outside Transform is touched. Pipelines, Sources, Destinations, runs and
the Airbyte integration keep their tables, columns and constraints exactly as
they are.

Revision ID: b8e4d2f16a09
Revises: a1c7e5940db2
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b8e4d2f16a09"
down_revision: str | None = "a1c7e5940db2"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB()
HEALTH = postgresql.ENUM(name="health_level", create_type=False)
RUN_STATUS = postgresql.ENUM(name="run_status", create_type=False)
TRIGGER_TYPE = postgresql.ENUM(name="trigger_type", create_type=False)
ERROR_CATEGORY = postgresql.ENUM(name="error_category", create_type=False)
SCHEDULE_TYPE = postgresql.ENUM(name="schedule_type", create_type=False)
OVERLAP_POLICY = postgresql.ENUM(name="overlap_policy", create_type=False)


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )


def upgrade() -> None:
    # ── take the V1 domain down ───────────────────────────────────────────
    #
    # Order matters: `data_assets` and `transform_connections` are kept and
    # rebuilt, so their foreign keys into the departing tables have to go
    # first. Everything else is dropped outright.
    op.execute("ALTER TABLE data_assets DROP CONSTRAINT IF EXISTS "
               "fk_data_assets_transform_id_transforms")
    op.execute("ALTER TABLE data_assets DROP CONSTRAINT IF EXISTS "
               "fk_data_assets_transform_model_id_transform_models")
    op.execute("ALTER TABLE transform_connections DROP CONSTRAINT IF EXISTS "
               "fk_transform_connections_destination_id_destinations")

    for table in (
        "transform_artifacts",
        "transform_run_nodes",
        "transform_run_attempts",
        "transform_runs",
        "transform_releases",
        "transform_dependencies",
        "transform_tests",
        "transform_models",
        "transform_inputs",
        "transforms",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")

    # `data_assets` survives, because the Pipeline link it carries is the one
    # thing dbt cannot know. Its Transform-shaped columns are replaced with
    # Transform-V2-shaped ones.
    with op.batch_alter_table("data_assets") as batch:
        batch.drop_column("transform_id")
        batch.drop_column("transform_model_id")
    op.add_column("data_assets", sa.Column("project_id", UUID, nullable=True))
    op.add_column("data_assets", sa.Column("dbt_unique_id", sa.String(500), nullable=True))

    # `transform_connections` survives too -- a warehouse key somebody entered
    # is worth keeping across a rework -- and gains its lifecycle columns.
    op.add_column(
        "transform_connections",
        sa.Column("verification_status", sa.String(24), nullable=False,
                  server_default="UNVERIFIED"),
    )
    op.add_column(
        "transform_connections",
        sa.Column("verification_message", sa.Text(), nullable=True),
    )
    op.execute(
        "UPDATE transform_connections SET verification_status = 'OK' "
        "WHERE last_verified_at IS NOT NULL"
    )
    op.create_foreign_key(
        "fk_transform_connections_destination_id_destinations",
        "transform_connections", "destinations", ["destination_id"], ["id"],
        ondelete="CASCADE",
    )

    # ── the V2 domain ─────────────────────────────────────────────────────

    op.create_table(
        "transform_projects",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("workspace_id", UUID, nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("mode", sa.String(16), nullable=False, server_default="MANAGED"),
        sa.Column("status", sa.String(24), nullable=False, server_default="ACTIVE"),
        sa.Column("dbt_project_name", sa.String(200), nullable=True),
        sa.Column("working_revision_id", UUID, nullable=True),
        sa.Column("active_release_id", UUID, nullable=True),
        sa.Column("default_environment_id", UUID, nullable=True),
        sa.Column("production_environment_id", UUID, nullable=True),
        sa.Column("health_status", HEALTH, nullable=False, server_default="UNKNOWN"),
        sa.Column("health_message", sa.Text(), nullable=True),
        sa.Column("parse_status", sa.String(24), nullable=False, server_default="UNKNOWN"),
        sa.Column("parse_error", sa.Text(), nullable=True),
        sa.Column("parsed_revision_id", UUID, nullable=True),
        sa.Column("last_parsed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_invocation_id", UUID, nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dbt_core_version", sa.String(32), nullable=False),
        sa.Column("dbt_adapter_name", sa.String(80), nullable=False),
        sa.Column("dbt_adapter_version", sa.String(32), nullable=False),
        sa.Column("schedule_type", SCHEDULE_TYPE, nullable=False, server_default="MANUAL"),
        sa.Column("schedule_config", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("schedule_command", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="Asia/Bangkok"),
        sa.Column("overlap_policy", OVERLAP_POLICY, nullable=False,
                  server_default="SKIP_IF_RUNNING"),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", UUID, nullable=True),
        sa.Column("updated_by", UUID, nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"]),
    )
    op.create_index(
        "uq_transform_project_ws_name_live", "transform_projects",
        ["workspace_id", "name"], unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_transform_projects_ws_health", "transform_projects",
        ["workspace_id", "health_status"],
    )

    op.create_table(
        "transform_project_revisions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("project_id", UUID, nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        # sha256 over the whole canonical file set. Two revisions with the same
        # hash contain the same project, which is what "Draft matches Live"
        # compares -- not SQL text.
        sa.Column("content_hash", sa.String(64), nullable=False),
        # {path: {key, size, sha256, is_text}}. Paths point at content-addressed
        # blobs in object storage; no file bytes live in Postgres.
        sa.Column("manifest_index", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("file_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("git_commit_sha", sa.String(64), nullable=True),
        sa.Column("git_branch", sa.String(255), nullable=True),
        sa.Column("parent_revision_id", UUID, nullable=True),
        sa.Column("frozen", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_by", UUID, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["project_id"], ["transform_projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_revision_id"], ["transform_project_revisions.id"],
                                ondelete="SET NULL"),
        sa.UniqueConstraint("project_id", "revision_number",
                            name="uq_transform_revision_number"),
    )
    op.create_index("ix_transform_revisions_project", "transform_project_revisions",
                    ["project_id", "created_at"])
    op.create_index("ix_transform_revisions_hash", "transform_project_revisions",
                    ["project_id", "content_hash"])

    op.create_table(
        "transform_environments",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("project_id", UUID, nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("type", sa.String(24), nullable=False),
        sa.Column("connection_id", UUID, nullable=True),
        sa.Column("target_name", sa.String(64), nullable=False, server_default="dev"),
        sa.Column("schema_strategy", sa.String(24), nullable=False, server_default="STATIC"),
        sa.Column("schema_name", sa.String(200), nullable=False),
        sa.Column("schema_prefix", sa.String(100), nullable=True),
        sa.Column("schema_suffix", sa.String(100), nullable=True),
        sa.Column("threads", sa.Integer(), nullable=False, server_default="4"),
        sa.Column("vars_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("env_metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("protected", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["project_id"], ["transform_projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["connection_id"], ["transform_connections.id"],
                                ondelete="SET NULL"),
        sa.UniqueConstraint("project_id", "name", name="uq_transform_environment_name"),
    )
    op.create_index("ix_transform_environments_project", "transform_environments",
                    ["project_id"])

    op.create_table(
        "transform_git_bindings",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("project_id", UUID, nullable=False),
        sa.Column("provider", sa.String(24), nullable=False, server_default="github"),
        sa.Column("repo_url", sa.String(500), nullable=False),
        sa.Column("branch", sa.String(255), nullable=False, server_default="main"),
        sa.Column("subdirectory", sa.String(500), nullable=False, server_default=""),
        sa.Column("secret_ref", sa.String(255), nullable=True),
        sa.Column("head_commit_sha", sa.String(64), nullable=True),
        sa.Column("remote_commit_sha", sa.String(64), nullable=True),
        sa.Column("auto_pull", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("interval_minutes", sa.Integer(), nullable=False, server_default="15"),
        sa.Column("next_pull_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_pulled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status", sa.String(24), nullable=True),
        sa.Column("last_message", sa.Text(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["project_id"], ["transform_projects.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("project_id", name="uq_transform_git_binding_project"),
    )

    op.create_table(
        "transform_releases",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("project_id", UUID, nullable=False),
        sa.Column("release_number", sa.Integer(), nullable=False),
        sa.Column("revision_id", UUID, nullable=False),
        sa.Column("project_hash", sa.String(64), nullable=False),
        sa.Column("git_commit_sha", sa.String(64), nullable=True),
        sa.Column("environment_id", UUID, nullable=True),
        sa.Column("dbt_version", sa.String(32), nullable=True),
        sa.Column("adapter_version", sa.String(32), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="VERIFYING"),
        sa.Column("verification_invocation_id", UUID, nullable=True),
        sa.Column("verification_error", sa.Text(), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activate_on_success", sa.Boolean(), nullable=False,
                  server_default=sa.true()),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        # Monotonic per project. A verification that finishes late must not
        # activate over a newer release that already went live; this value
        # decides, rather than the order two async jobs happen to complete in.
        sa.Column("activation_sequence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by", UUID, nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["project_id"], ["transform_projects.id"], ondelete="CASCADE"),
        # RESTRICT, not CASCADE: the revision a release points at is what
        # production runs, and retention must never be able to delete it.
        sa.ForeignKeyConstraint(["revision_id"], ["transform_project_revisions.id"],
                                ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["environment_id"], ["transform_environments.id"],
                                ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.UniqueConstraint("project_id", "release_number",
                            name="uq_transform_release_number"),
    )
    op.create_index("ix_transform_releases_project", "transform_releases",
                    ["project_id", "created_at"])

    op.create_table(
        "transform_invocations",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("workspace_id", UUID, nullable=False),
        sa.Column("project_id", UUID, nullable=False),
        sa.Column("environment_id", UUID, nullable=False),
        # Not nullable. "The run of the current code" is exactly the ambiguity
        # that made a production failure impossible to reproduce.
        sa.Column("revision_id", UUID, nullable=False),
        sa.Column("release_id", UUID, nullable=True),
        sa.Column("command", sa.String(32), nullable=False),
        sa.Column("selector", sa.String(1000), nullable=True),
        sa.Column("exclude", sa.String(1000), nullable=True),
        sa.Column("args_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("trigger_type", TRIGGER_TYPE, nullable=False),
        sa.Column("triggered_by", UUID, nullable=True),
        sa.Column("retry_of_invocation_id", UUID, nullable=True),
        sa.Column("idempotency_key", sa.String(120), nullable=True),
        sa.Column("status", RUN_STATUS, nullable=False, server_default="QUEUED"),
        sa.Column("queue_reason", sa.String(120), nullable=True),
        sa.Column("error_category", ERROR_CATEGORY, nullable=True),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("remediation_action", sa.String(64), nullable=True),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("dbt_invocation_id", sa.String(64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("nodes_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("nodes_succeeded", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("nodes_failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("nodes_skipped", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tests_passed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tests_failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tests_warned", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rows_affected", sa.BigInteger(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("technical_metadata", JSONB, nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("claimed_by", sa.String(100), nullable=True),
        sa.Column("artifact_bundle_id", UUID, nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["project_id"], ["transform_projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["environment_id"], ["transform_environments.id"],
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["revision_id"], ["transform_project_revisions.id"],
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["release_id"], ["transform_releases.id"],
                                ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["triggered_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["retry_of_invocation_id"], ["transform_invocations.id"]),
    )
    op.create_index("ix_transform_invocations_ws_created", "transform_invocations",
                    ["workspace_id", "created_at"])
    op.create_index("ix_transform_invocations_project_created", "transform_invocations",
                    ["project_id", "created_at"])
    op.create_index("ix_transform_invocations_status_started", "transform_invocations",
                    ["status", "started_at"])
    # One writing command per project+environment at a time. Read commands
    # (parse, compile, show, ls) are excluded: they touch no relation, and
    # serialising them would make the editor feel broken.
    op.create_index(
        "uq_transform_active_write", "transform_invocations",
        ["project_id", "environment_id"], unique=True,
        postgresql_where=sa.text(
            "status IN ('QUEUED', 'STARTING', 'RUNNING', 'CANCEL_REQUESTED') "
            "AND command IN ('build', 'run', 'seed', 'snapshot', 'run-operation', "
            "'clone', 'test')"
        ),
    )
    op.create_index(
        "uq_transform_invocation_idempotency", "transform_invocations",
        ["workspace_id", "idempotency_key"], unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )

    op.create_table(
        "transform_invocation_nodes",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("invocation_id", UUID, nullable=False),
        sa.Column("unique_id", sa.String(500), nullable=False),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("resource_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("execution_time", sa.Float(), nullable=True),
        sa.Column("relation_name", sa.String(500), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("rows_affected", sa.BigInteger(), nullable=True),
        sa.Column("bytes_processed", sa.BigInteger(), nullable=True),
        sa.Column("failures", sa.Integer(), nullable=True),
        sa.Column("adapter_response", JSONB, nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("error_location", JSONB, nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.ForeignKeyConstraint(["invocation_id"], ["transform_invocations.id"],
                                ondelete="CASCADE"),
        sa.UniqueConstraint("invocation_id", "unique_id",
                            name="uq_transform_invocation_node"),
    )
    op.create_index("ix_transform_invocation_nodes_invocation",
                    "transform_invocation_nodes", ["invocation_id"])

    op.create_table(
        "transform_artifact_bundles",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("project_id", UUID, nullable=False),
        sa.Column("invocation_id", UUID, nullable=True),
        sa.Column("revision_id", UUID, nullable=True),
        # DRAFT for a working-revision parse, RELEASE for a release's own
        # artifacts. Keeping them apart is what stops a draft parse overwriting
        # the graph production is running.
        sa.Column("scope", sa.String(16), nullable=False, server_default="DRAFT"),
        sa.Column("manifest_storage_key", sa.String(500), nullable=True),
        sa.Column("run_results_storage_key", sa.String(500), nullable=True),
        sa.Column("catalog_storage_key", sa.String(500), nullable=True),
        sa.Column("sources_storage_key", sa.String(500), nullable=True),
        sa.Column("semantic_manifest_storage_key", sa.String(500), nullable=True),
        sa.Column("log_storage_key", sa.String(500), nullable=True),
        sa.Column("dbt_version", sa.String(32), nullable=True),
        sa.Column("dbt_schema_version", sa.String(200), nullable=True),
        sa.Column("adapter_type", sa.String(64), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["project_id"], ["transform_projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["invocation_id"], ["transform_invocations.id"],
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["revision_id"], ["transform_project_revisions.id"],
                                ondelete="SET NULL"),
    )
    op.create_index("ix_transform_artifact_bundles_project", "transform_artifact_bundles",
                    ["project_id", "created_at"])
    op.create_index("ix_transform_artifact_bundles_invocation",
                    "transform_artifact_bundles", ["invocation_id"])

    # Index and edges: caches built from manifest.json, never truth. Dropping
    # them loses nothing -- rebuilding is one read of a stored artifact.
    op.create_table(
        "transform_resource_index",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("project_id", UUID, nullable=False),
        sa.Column("bundle_id", UUID, nullable=False),
        sa.Column("revision_id", UUID, nullable=True),
        sa.Column("scope", sa.String(16), nullable=False, server_default="DRAFT"),
        sa.Column("unique_id", sa.String(500), nullable=False),
        # A string, deliberately not an enum: dbt adds resource types, and an
        # enum here would mean a migration every time it does.
        sa.Column("resource_type", sa.String(32), nullable=False),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("package_name", sa.String(200), nullable=True),
        sa.Column("original_file_path", sa.String(700), nullable=True),
        sa.Column("patch_path", sa.String(700), nullable=True),
        sa.Column("database_name", sa.String(300), nullable=True),
        sa.Column("schema_name", sa.String(300), nullable=True),
        sa.Column("alias", sa.String(300), nullable=True),
        sa.Column("relation_name", sa.String(700), nullable=True),
        sa.Column("materialized", sa.String(64), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("group_name", sa.String(200), nullable=True),
        sa.Column("tags_json", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("config_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("columns_json", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("checksum", sa.String(100), nullable=True),
        sa.Column("produced_by_pipeline_id", UUID, nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["transform_projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["bundle_id"], ["transform_artifact_bundles.id"],
                                ondelete="CASCADE"),
        sa.UniqueConstraint("bundle_id", "unique_id", name="uq_transform_resource_unique"),
    )
    op.create_index("ix_transform_resource_project_scope", "transform_resource_index",
                    ["project_id", "scope"])
    op.create_index("ix_transform_resource_type", "transform_resource_index",
                    ["bundle_id", "resource_type"])
    op.create_index("ix_transform_resource_name", "transform_resource_index",
                    ["project_id", "name"])

    op.create_table(
        "transform_resource_edges",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("project_id", UUID, nullable=False),
        sa.Column("bundle_id", UUID, nullable=False),
        sa.Column("scope", sa.String(16), nullable=False, server_default="DRAFT"),
        sa.Column("parent_unique_id", sa.String(500), nullable=False),
        sa.Column("child_unique_id", sa.String(500), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["transform_projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["bundle_id"], ["transform_artifact_bundles.id"],
                                ondelete="CASCADE"),
        sa.UniqueConstraint("bundle_id", "parent_unique_id", "child_unique_id",
                            name="uq_transform_resource_edge"),
    )
    op.create_index("ix_transform_resource_edges_child", "transform_resource_edges",
                    ["bundle_id", "child_unique_id"])
    op.create_index("ix_transform_resource_edges_parent", "transform_resource_edges",
                    ["bundle_id", "parent_unique_id"])

    # Circular references, added after both tables exist.
    op.create_foreign_key(
        "fk_transform_project_working_revision", "transform_projects",
        "transform_project_revisions", ["working_revision_id"], ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_transform_project_active_release", "transform_projects",
        "transform_releases", ["active_release_id"], ["id"], ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_transform_project_default_environment", "transform_projects",
        "transform_environments", ["default_environment_id"], ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_transform_project_production_environment", "transform_projects",
        "transform_environments", ["production_environment_id"], ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_data_assets_project_id_transform_projects", "data_assets",
        "transform_projects", ["project_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    """Remove V2.

    Deliberately does not resurrect V1. Recreating those tables would produce
    empty ones -- the data they held is gone -- and a downgrade that leaves an
    empty `transform_models` behind is more misleading than one that leaves the
    module absent. Restore from a database backup if V1 is genuinely wanted.
    """
    op.drop_constraint("fk_data_assets_project_id_transform_projects", "data_assets",
                       type_="foreignkey")
    for name in (
        "fk_transform_project_production_environment",
        "fk_transform_project_default_environment",
        "fk_transform_project_active_release",
        "fk_transform_project_working_revision",
    ):
        op.drop_constraint(name, "transform_projects", type_="foreignkey")

    for table in (
        "transform_resource_edges",
        "transform_resource_index",
        "transform_artifact_bundles",
        "transform_invocation_nodes",
        "transform_invocations",
        "transform_releases",
        "transform_git_bindings",
        "transform_environments",
        "transform_project_revisions",
        "transform_projects",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")

    op.drop_column("data_assets", "dbt_unique_id")
    op.drop_column("data_assets", "project_id")
    op.add_column("data_assets", sa.Column("transform_id", UUID, nullable=True))
    op.add_column("data_assets", sa.Column("transform_model_id", UUID, nullable=True))
    op.drop_column("transform_connections", "verification_message")
    op.drop_column("transform_connections", "verification_status")
