"""Add CONNECTOR_DEV to member_role.

The Builder was reachable only with CONNECTORS.CREATE, which only OWNER and
PLATFORM_ADMIN hold. Letting somebody write a connector therefore meant handing
them member management and delete on every pipeline in the workspace.
CONNECTOR_DEV is that authority and nothing else.

Revision ID: c4d9e2f81a63
Revises: b8e4d2f16a09
"""

from __future__ import annotations

from alembic import op

revision: str = "c4d9e2f81a63"
down_revision: str | None = "b8e4d2f16a09"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE cannot run in the same transaction as a
    # statement that uses the new value, and on older PostgreSQL cannot run
    # inside an explicit transaction block at all. Autocommit avoids both.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE member_role ADD VALUE IF NOT EXISTS 'CONNECTOR_DEV'")


def downgrade() -> None:
    # PostgreSQL cannot drop a single enum value, and rewriting the type would
    # mean rewriting every column that uses it. Down-migrating is a no-op, which
    # matches the other enum-add migrations in this tree. Any membership left on
    # CONNECTOR_DEV keeps a label the older code cannot map -- reassign those
    # rows before downgrading past this point.
    pass
