"""Name the Git connection for what it is: a source, read one way.

"Sync" promises two directions. This one has never had a second: the only calls
made to GitHub are two GETs, and nothing in the product can write to a
repository. A name that suggests otherwise makes people hesitate to edit their
own models here, which is the opposite of what the feature is for.

Revision ID: c93b1e47af28
Revises: b6f2a83d5c14
"""

from __future__ import annotations

from alembic import op

revision: str = "c93b1e47af28"
down_revision: str | None = "b6f2a83d5c14"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("transforms", "git_sync", new_column_name="git_source")
    op.alter_column("transforms", "git_next_sync_at", new_column_name="git_next_pull_at")
    op.execute(
        "ALTER INDEX IF EXISTS ix_transforms_git_next_sync "
        "RENAME TO ix_transforms_git_next_pull"
    )
    # The keys inside the column carry the old names too. Renaming the column
    # and leaving them would read as "auto-pull was never on" on every row that
    # had it on, which is a setting silently reversing itself on deploy.
    op.execute(
        """
        UPDATE transforms SET git_source =
            (git_source - 'enabled' - 'last_synced_at')
            || CASE WHEN git_source ? 'enabled'
                    THEN jsonb_build_object('auto_pull', git_source -> 'enabled')
                    ELSE '{}'::jsonb END
            || CASE WHEN git_source ? 'last_synced_at'
                    THEN jsonb_build_object('last_pulled_at', git_source -> 'last_synced_at')
                    ELSE '{}'::jsonb END
        WHERE git_source ? 'enabled' OR git_source ? 'last_synced_at'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE transforms SET git_source =
            (git_source - 'auto_pull' - 'last_pulled_at')
            || CASE WHEN git_source ? 'auto_pull'
                    THEN jsonb_build_object('enabled', git_source -> 'auto_pull')
                    ELSE '{}'::jsonb END
            || CASE WHEN git_source ? 'last_pulled_at'
                    THEN jsonb_build_object('last_synced_at', git_source -> 'last_pulled_at')
                    ELSE '{}'::jsonb END
        WHERE git_source ? 'auto_pull' OR git_source ? 'last_pulled_at'
        """
    )
    op.execute(
        "ALTER INDEX IF EXISTS ix_transforms_git_next_pull "
        "RENAME TO ix_transforms_git_next_sync"
    )
    op.alter_column("transforms", "git_next_pull_at", new_column_name="git_next_sync_at")
    op.alter_column("transforms", "git_source", new_column_name="git_sync")
