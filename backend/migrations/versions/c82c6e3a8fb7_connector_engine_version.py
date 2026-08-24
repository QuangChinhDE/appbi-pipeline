"""connector_definitions.engine_version

Records the connector tag the *engine* will run, separately from the tag this
product bundled and locked.

They are the same thing in embedded mode, where the product picks the image. In
AIRBYTE_API mode Airbyte pins its own connector versions, so the two diverge —
the product locks `destination-postgres:3.0.17` while the deployment actually
runs `2.0.10` — and reporting a single version made the compatibility endpoint
state the wrong one confidently.

Nullable with no backfill: the value is not knowable without asking the engine,
and inventing one here would recreate exactly the false certainty this column
exists to remove. It fills in on the next catalog refresh.

Revision ID: c82c6e3a8fb7
Revises: 2fc7499a99b9
Create Date: 2026-08-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "c82c6e3a8fb7"
down_revision: str | None = "2fc7499a99b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "connector_definitions",
        sa.Column("engine_version", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("connector_definitions", "engine_version")
