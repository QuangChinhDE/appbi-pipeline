"""Session invalidation: a password change must end the sessions it replaces.

Separate from d4a1f07c2b18 rather than added to it. That revision had already
been applied to running databases, and editing an applied migration means the
new statements never run there -- Alembic sees the version row and moves on.
The failure is quiet until something selects the column that was never added.

`session_version` is compared against the value carried in each session token,
so incrementing it invalidates every token issued before the change. `exp` does
not revoke, and the `jti` in the token was never checked against anything.

Revision ID: e1b93c7a4d22
Revises: d4a1f07c2b18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "e1b93c7a4d22"
down_revision: str | None = "d4a1f07c2b18"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("session_version", sa.Integer(), nullable=False,
                  server_default=sa.text("0")),
    )
    op.add_column(
        "users",
        sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "password_changed_at")
    op.drop_column("users", "session_version")
