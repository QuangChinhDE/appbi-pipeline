"""exponential lockout backoff

A flat 15-minute lockout after five failures is a denial-of-service budget: an
attacker who knows an administrator's email can keep that account unusable
indefinitely for the cost of one request every three minutes. Counting the
lockouts lets the window double, so sustained targeting becomes expensive while
a first genuine mistake stays cheap to recover from.

Revision ID: b4f8c21d7e93
Revises: a7d3e9b41c05
Create Date: 2026-08-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b4f8c21d7e93"
down_revision = "a7d3e9b41c05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("lockout_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("users", "lockout_count")
