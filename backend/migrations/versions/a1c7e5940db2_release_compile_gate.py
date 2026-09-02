"""A release is not live until the exact snapshot has compiled.

Publishing froze the code; it never proved the code runs. `generate_project`
renders files -- it does not invoke dbt -- so a Transform whose SQL was broken
could be published, made live, and discovered at 03:00 by a scheduler. With Git
auto-pull plus auto-publish, nobody had to press anything for that to happen.

A release now carries the verdict of a compile against its own frozen files:
VERIFYING while that compile is queued or running, READY when it passed, FAILED
when it did not. Only READY can be activated. Rows that already exist were
published under the old rule and are left READY -- retroactively marking live
releases unverified would stop schedules that are working today.

Revision ID: a1c7e5940db2
Revises: f7d31a8e6c02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a1c7e5940db2"
down_revision: str | None = "f7d31a8e6c02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "transform_releases",
        sa.Column("status", sa.String(length=20), nullable=False,
                  server_default="READY"),
    )
    op.add_column(
        "transform_releases",
        sa.Column("verify_run_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("transform_releases", sa.Column("verify_error", sa.Text(), nullable=True))
    op.add_column(
        "transform_releases",
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "transform_releases",
        sa.Column("activate_on_success", sa.Boolean(), nullable=False,
                  server_default=sa.text("true")),
    )
    # Everything published before this migration ran under the old contract and
    # is running now. Marking it verified records that honestly: it was accepted,
    # not proven.
    op.execute("UPDATE transform_releases SET status = 'READY', verified_at = created_at")
    op.create_index(
        "ix_transform_releases_status", "transform_releases", ["transform_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_transform_releases_status", table_name="transform_releases")
    op.drop_column("transform_releases", "activate_on_success")
    op.drop_column("transform_releases", "verified_at")
    op.drop_column("transform_releases", "verify_error")
    op.drop_column("transform_releases", "verify_run_id")
    op.drop_column("transform_releases", "status")
