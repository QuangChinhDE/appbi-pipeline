"""oauth grants

The handoff between a provider's redirect and the connector wizard. The refresh
token is not stored here -- it goes into the ordinary envelope-encrypted secret
store, and this row holds the reference plus enough to check that whoever
redeems the handle is the workspace that asked for it.

Short-lived and single-use on purpose: an unconsumed grant is a live refresh
token with nothing pointing at it.

Revision ID: c5e1a9f37d24
Revises: b4f8c21d7e93
Create Date: 2026-08-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c5e1a9f37d24"
down_revision = "b4f8c21d7e93"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "oauth_grants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("connector_key", sa.String(length=120), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("secret_ref", sa.String(length=255), nullable=False),
        sa.Column("account_label", sa.String(length=255), nullable=False,
                  server_default=""),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_oauth_grants_workspace_id", "oauth_grants", ["workspace_id"])
    # The purge query, which is what stops an abandoned consent from becoming a
    # standing credential.
    op.create_index("ix_oauth_grants_expires_at", "oauth_grants", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_oauth_grants_expires_at", table_name="oauth_grants")
    op.drop_index("ix_oauth_grants_workspace_id", table_name="oauth_grants")
    op.drop_table("oauth_grants")
