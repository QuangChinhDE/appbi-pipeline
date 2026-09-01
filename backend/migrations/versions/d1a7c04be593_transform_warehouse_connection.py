"""Let a Transform run as its own warehouse account.

A Destination's credentials exist to let a Pipeline write. A Transform often
needs to read somewhere else -- another BigQuery project entirely -- and the
only way to allow that so far was to widen the account the Pipeline uses, which
is a change to production ingestion made for the sake of a report.

So a Transform may carry its own credential. It is a partial configuration
merged over the Destination's, because the warehouse is the same warehouse and
only the account differs. Null means inherit, which is what almost every
Transform will want.

Still exactly one connection: dbt reads its sources and writes its models
through a single profile, so whatever account is named here has to do both.

Revision ID: d1a7c04be593
Revises: c93b1e47af28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "d1a7c04be593"
down_revision: str | None = "c93b1e47af28"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "transforms",
        sa.Column("warehouse_secret_ref", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("transforms", "warehouse_secret_ref")
