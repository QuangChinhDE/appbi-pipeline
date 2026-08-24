"""Restore the index that startup DDL used to drop.

`app.bootstrap` carried `DROP INDEX IF EXISTS ix_connector_definitions_display_name`
in its fixup list and ran it after every `alembic upgrade head`. The model
declares the index and the baseline migration creates it, so every boot undid
the migration: `alembic current` reported head while `alembic check` reported
the index missing.

Removing the offending statement stops the damage but does not repair it. The
index was created by the baseline, which has already run everywhere, so nothing
would ever recreate it -- an existing deployment would sit at head, drift-free
in Alembic's own accounting only because autogenerate had nothing new to say
after this migration adds it back.

`IF NOT EXISTS` because a database that never ran the buggy startup still has
it, and this must be a no-op there.

Revision ID: f2c0a15b8e37
Revises: e1b93c7a4d22
"""

from __future__ import annotations

from alembic import op

revision: str = "f2c0a15b8e37"
down_revision: str | None = "e1b93c7a4d22"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        'CREATE INDEX IF NOT EXISTS "ix_connector_definitions_display_name" '
        'ON "connector_definitions" ("display_name")'
    )


def downgrade() -> None:
    # Deliberately not dropped: the model declares this index, so removing it
    # on downgrade would recreate the drift this migration exists to repair.
    pass
