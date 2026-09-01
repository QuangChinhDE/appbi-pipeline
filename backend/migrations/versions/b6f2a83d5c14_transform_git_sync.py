"""Keep a Transform tied to the Git repository it was imported from.

An import is a one-off; a repository is not. A team that keeps modelling in Git
expects the Transform to follow, so the connection has to survive the import --
where the code came from, which commit was last applied, and when to look again.

The token is not stored here. It goes to the encrypted secret store like every
other credential and only its reference is kept in this column.

Revision ID: b6f2a83d5c14
Revises: a4c71d29f8b6
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b6f2a83d5c14"
down_revision: str | None = "a4c71d29f8b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "transforms",
        sa.Column(
            "git_sync", postgresql.JSONB(astext_type=sa.Text()),
            nullable=False, server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "transforms",
        sa.Column("git_next_sync_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Partial: the scheduler only ever asks for rows that have a due time, and
    # on a workspace where nobody uses Git sync that is none of them.
    op.create_index(
        "ix_transforms_git_next_sync",
        "transforms",
        ["git_next_sync_at"],
        postgresql_where=sa.text("git_next_sync_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_transforms_git_next_sync", table_name="transforms")
    op.drop_column("transforms", "git_next_sync_at")
    op.drop_column("transforms", "git_sync")
