"""A Vietnamese connector description beside the English one.

The frontend switched language correctly and everything the server produced
did not, so an English reader got English headings around Vietnamese connector
text. Field labels and help travel inside `spec_schema`, which is JSON and
could carry both without a schema change. The connector's own one-line
description is a column, so it needs one.

English stays in `description`, which is also what an Airbyte image supplies
and what the form falls back to. Nullable: a connector with no translation
shows the English, which is better than showing nothing.

Revision ID: b7d3f9a15c28
Revises: f4e1c8b27d93
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "b7d3f9a15c28"
down_revision: str | None = "f4e1c8b27d93"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "connector_definitions",
        sa.Column("description_vi", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("connector_definitions", "description_vi")
