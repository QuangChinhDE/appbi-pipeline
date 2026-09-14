"""Permissions a person can edit, and a second way to prove who you are.

TWO CHANGES, ONE MIGRATION, because they are the same feature: an administrator
deciding who gets in and what they may do once they are.

`memberships.permissions` is nullable and that is the design, not an oversight.
NULL means "exactly the preset named by `role`", which is what every existing
row means today -- so nothing is back-filled, nothing is rewritten, and a preset
improved in Python still reaches everybody who never departed from it. A row
gains a map only when somebody edits it, and from that moment the map is the
answer.

`users.password_hash` becomes nullable for the same reason it was NOT NULL: it
described the only way in. An account an administrator creates for somebody who
signs in with Google has no password, and giving it a random one would leave a
credential nobody knows lying in the table.

Revision ID: c3a58f1d97e4
Revises: b7d3f9a15c28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c3a58f1d97e4"
down_revision: str | None = "b7d3f9a15c28"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "memberships",
        sa.Column("permissions", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )

    op.add_column(
        "users",
        sa.Column(
            "auth_provider", sa.String(16),
            nullable=False, server_default="password",
        ),
    )
    op.add_column("users", sa.Column("google_sub", sa.String(64), nullable=True))
    op.add_column("users", sa.Column("avatar_url", sa.String(512), nullable=True))
    # Unique so one Google identity cannot be attached to two accounts, and a
    # partial index so the many rows that have no Google identity do not all
    # collide on NULL -- Postgres treats NULLs as distinct, but the index is
    # smaller and the intent is clearer stated.
    op.create_index(
        "uq_users_google_sub", "users", ["google_sub"],
        unique=True, postgresql_where=sa.text("google_sub IS NOT NULL"),
    )

    op.alter_column("users", "password_hash", existing_type=sa.String(255), nullable=True)


def downgrade() -> None:
    # An account with no password cannot be represented once the column is NOT
    # NULL again, and inventing one would be worse than refusing. Deactivate
    # them instead: the row survives, the person is told to ask an admin, and
    # nobody is handed a credential they did not choose.
    op.execute(
        "UPDATE users SET is_active = false "
        "WHERE password_hash IS NULL"
    )
    op.execute(
        "UPDATE users SET password_hash = '!' WHERE password_hash IS NULL"
    )
    op.alter_column("users", "password_hash", existing_type=sa.String(255), nullable=False)

    op.drop_index("uq_users_google_sub", table_name="users")
    op.drop_column("users", "avatar_url")
    op.drop_column("users", "google_sub")
    op.drop_column("users", "auth_provider")
    op.drop_column("memberships", "permissions")
