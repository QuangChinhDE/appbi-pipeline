"""Transform releases and schedule.

A release freezes the generated dbt project so an unattended run executes the
code that was published, not whatever the editor happens to hold when the
worker picks the job up.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a4c71d29f8b6"
down_revision: tuple[str, ...] = ("f2c0a15b8e37", "e8b2d4f19a37")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "transform_releases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "transform_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("transforms.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("release_number", sa.Integer(), nullable=False),
        sa.Column(
            "project_files", postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}", nullable=False,
        ),
        sa.Column(
            "model_snapshot", postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]", nullable=False,
        ),
        sa.Column("source_version", sa.Integer(), nullable=False),
        sa.Column("default_schema", sa.String(length=200), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_by", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"), nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.UniqueConstraint(
            "transform_id", "release_number", name="uq_transform_release_number",
        ),
    )
    op.create_index(
        "ix_transform_releases_transform", "transform_releases",
        ["transform_id", "created_at"],
    )

    op.add_column(
        "transforms",
        sa.Column("active_release_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_transform_active_release", "transforms", "transform_releases",
        ["active_release_id"], ["id"],
    )

    # Same shape as pipelines, so one scheduler pattern covers both.
    op.add_column(
        "transforms",
        sa.Column(
            "schedule_type",
            sa.Enum("MANUAL", "INTERVAL", "DAILY", "CRON", name="schedule_type",
                    create_type=False),
            server_default="MANUAL", nullable=False,
        ),
    )
    op.add_column(
        "transforms",
        sa.Column(
            "schedule_config", postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}", nullable=False,
        ),
    )
    op.add_column(
        "transforms",
        sa.Column(
            "timezone", sa.String(length=64),
            server_default="Asia/Bangkok", nullable=False,
        ),
    )
    op.add_column(
        "transforms",
        sa.Column(
            "overlap_policy",
            sa.Enum("SKIP_IF_RUNNING", "QUEUE", name="overlap_policy", create_type=False),
            server_default="SKIP_IF_RUNNING", nullable=False,
        ),
    )
    op.add_column(
        "transforms",
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_transforms_next_run_at", "transforms", ["next_run_at"],
        postgresql_where=sa.text("next_run_at IS NOT NULL"),
    )

    op.add_column(
        "transform_runs",
        sa.Column("release_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_transform_run_release", "transform_runs", "transform_releases",
        ["release_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_transform_run_release", "transform_runs", type_="foreignkey")
    op.drop_column("transform_runs", "release_id")
    op.drop_index("ix_transforms_next_run_at", table_name="transforms")
    op.drop_column("transforms", "next_run_at")
    op.drop_column("transforms", "overlap_policy")
    op.drop_column("transforms", "timezone")
    op.drop_column("transforms", "schedule_config")
    op.drop_column("transforms", "schedule_type")
    op.drop_constraint("fk_transform_active_release", "transforms", type_="foreignkey")
    op.drop_column("transforms", "active_release_id")
    op.drop_index("ix_transform_releases_transform", table_name="transform_releases")
    op.drop_table("transform_releases")
