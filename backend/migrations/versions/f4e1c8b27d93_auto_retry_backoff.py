"""Automatic retry with backoff for transient source failures.

From a customer deployment. One pipeline failed three times in a row, always
after exactly 1,319 records, and succeeded on the fourth attempt thirteen
minutes later. Nothing was wrong with the configuration -- the source was
refusing requests for a few minutes. Every one of those four attempts was a
person noticing the failure and pressing a button.

`run_after` is what lets a queued run wait: the worker claims the oldest
QUEUED run, and now only once that moment has passed. `AUTO_RETRY` keeps the
distinction visible, because "this failed four times" and "this failed once
and recovered by itself" are different things to be told.

Revision ID: f4e1c8b27d93
Revises: d5b81f7c0a24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "f4e1c8b27d93"
down_revision: str | None = "d5b81f7c0a24"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable, and NULL means "runnable now". Every run already queued when
    # this lands keeps working without a backfill.
    op.add_column(
        "pipeline_runs",
        sa.Column("run_after", sa.DateTime(timezone=True), nullable=True),
    )
    # The claim query filters on status and then on this, and it runs on every
    # worker tick. Partial, because the only rows it ever looks at are queued.
    op.create_index(
        "ix_runs_claimable",
        "pipeline_runs",
        ["run_after"],
        postgresql_where=sa.text("status = 'QUEUED'"),
    )

    # ALTER TYPE ... ADD VALUE cannot run in the same transaction as a
    # statement that uses the new value, and on older PostgreSQL cannot run
    # inside an explicit transaction block at all. Autocommit avoids both.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE trigger_type ADD VALUE IF NOT EXISTS 'AUTO_RETRY'")


def downgrade() -> None:
    op.drop_index("ix_runs_claimable", table_name="pipeline_runs")
    op.drop_column("pipeline_runs", "run_after")
    # PostgreSQL cannot drop a single enum value, and rewriting the type would
    # mean rewriting every column using it. Runs already labelled AUTO_RETRY
    # keep a label the older code cannot map; reassign them before downgrading
    # past this point.
