"""Make a warehouse key something you pick, not something you paste again.

A credential was being stored anonymously per Transform: no name, not listed,
not reusable. So there was never an existing key to choose -- every Transform
meant pasting a service account JSON again, and nobody could tell two of them
apart afterwards.

Both reference products model this the other way round. dbt Cloud has named,
reusable connections and profiles chosen from a list; Dataform has you *select*
a service account when creating a repository. A key is a thing you keep.

So a key gets a row: a name, the warehouse it reaches, who it turned out to be,
and the projects it could see when it was last checked. The secret itself stays
in the encrypted store and only its reference is here.

Revision ID: e5c82b17d940
Revises: d1a7c04be593
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e5c82b17d940"
down_revision: str | None = "d1a7c04be593"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "transform_connections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "workspace_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "destination_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("destinations.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("name", sa.String(length=200), nullable=False),
        #: Reference into the encrypted secret store. Never the credential.
        sa.Column("secret_ref", sa.String(length=255), nullable=False),
        #: Who the credential turned out to be, so a wrong key is obvious in a list.
        sa.Column("account", sa.String(length=320), nullable=True),
        #: Projects visible at the last check -- shown so a key that cannot reach
        #: the data is recognisable before it is chosen rather than after.
        sa.Column(
            "catalogs", postgresql.JSONB(astext_type=sa.Text()),
            nullable=False, server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "uq_transform_connection_name",
        "transform_connections", ["workspace_id", "name"], unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_transform_connections_destination",
        "transform_connections", ["destination_id"],
    )

    op.add_column(
        "transforms",
        sa.Column(
            "warehouse_connection_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("transform_connections.id"), nullable=True,
        ),
    )
    # The anonymous per-Transform credential this replaces. Nothing references
    # one that is worth keeping: it had no name, so it could never be picked.
    op.drop_column("transforms", "warehouse_secret_ref")


def downgrade() -> None:
    op.add_column(
        "transforms",
        sa.Column("warehouse_secret_ref", sa.String(length=255), nullable=True),
    )
    op.drop_column("transforms", "warehouse_connection_id")
    op.drop_index("ix_transform_connections_destination", table_name="transform_connections")
    op.drop_index("uq_transform_connection_name", table_name="transform_connections")
    op.drop_table("transform_connections")
