"""Anchor a Transform on its connection rather than on a Destination.

A Destination is a place a Pipeline writes. A Transform often reads somewhere no
Pipeline touches, and until now that was impossible to express: a connection
held only a credential *override* merged over a Destination's configuration, so
it could not exist without one. Choosing a system and connecting to it -- which
is what a person is actually doing -- had nowhere to live.

So the connection becomes the thing that owns a warehouse: its own connector
kind, its own configuration, its own credential, and how that credential
authenticates. A connection made from a Destination keeps a back-link, which is
what lets `Source → Pipeline → table` still resolve in the lineage graph; a
connection somebody made by pasting a key has no back-link and no upstream,
which is the honest answer for a table AppBI does not load.

Every existing Destination that Transform supports becomes a connection row, so
nothing that runs today stops running. Data assets and Transforms are re-pointed
at those rows, and the uniqueness that kept two identities off one table moves
with them.

Revision ID: f7d31a8e6c02
Revises: e5c82b17d940
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f7d31a8e6c02"
down_revision: str | None = "e5c82b17d940"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- the connection becomes a warehouse in its own right ---------------
    op.add_column(
        "transform_connections",
        sa.Column("connector_key", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "transform_connections",
        sa.Column("auth_method", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "transform_connections",
        sa.Column(
            "configuration_json", postgresql.JSONB(astext_type=sa.Text()),
            nullable=False, server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "transform_connections",
        sa.Column("is_default", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
    )
    op.alter_column("transform_connections", "destination_id", nullable=True)
    # A connection made by pasting a key has no credential to inherit, so the
    # reference is only optional once the configuration travels with the row.
    op.alter_column("transform_connections", "secret_ref", nullable=True)

    # --- one connection per Destination Transform already supports ---------
    # `is_default` marks it: it is the key that Destination already uses, and
    # the one a person picking a warehouse expects to see first.
    op.execute(
        """
        INSERT INTO transform_connections
            (id, workspace_id, destination_id, name, secret_ref, account,
             catalogs, connector_key, auth_method, configuration_json,
             is_default, created_at, updated_at)
        SELECT gen_random_uuid(), d.workspace_id, d.id, d.name, NULL, NULL,
               '[]'::jsonb, d.connector_key, 'inherited', '{}'::jsonb,
               true, now(), now()
        FROM destinations d
        WHERE d.deleted_at IS NULL
          AND d.connector_key IN ('destination-bigquery', 'destination-postgres')
        """
    )

    # --- assets and Transforms move onto connections -----------------------
    op.add_column(
        "data_assets",
        sa.Column("connection_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_data_assets_connection", "data_assets", "transform_connections",
        ["connection_id"], ["id"],
    )
    op.execute(
        """
        UPDATE data_assets a SET connection_id = c.id
        FROM transform_connections c
        WHERE c.destination_id = a.destination_id AND c.is_default
        """
    )
    op.execute(
        """
        UPDATE transforms t SET warehouse_connection_id = c.id
        FROM transform_connections c
        WHERE c.destination_id = t.destination_id AND c.is_default
          AND t.warehouse_connection_id IS NULL
        """
    )

    # Uniqueness follows the anchor: two identities for one physical table is
    # what forks a lineage graph, and the scope that has to stay unique is now
    # the connection.
    op.drop_index("uq_data_asset_physical_live", table_name="data_assets")
    op.create_index(
        "uq_data_asset_physical_live", "data_assets",
        ["connection_id", "physical_identity"], unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index("ix_data_assets_connection", "data_assets", ["connection_id"])
    # A table read through a connection that has no Destination has no
    # Destination either, so the column stops being required.
    op.alter_column("data_assets", "destination_id", nullable=True)
    # Same for the Transform: its warehouse is the connection now.
    op.alter_column("transforms", "destination_id", nullable=True)


def downgrade() -> None:
    op.drop_index("ix_data_assets_connection", table_name="data_assets")
    op.drop_index("uq_data_asset_physical_live", table_name="data_assets")
    op.create_index(
        "uq_data_asset_physical_live", "data_assets",
        ["destination_id", "physical_identity"], unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.alter_column("transforms", "destination_id", nullable=False)
    op.alter_column("data_assets", "destination_id", nullable=False)
    op.drop_constraint("fk_data_assets_connection", "data_assets", type_="foreignkey")
    op.drop_column("data_assets", "connection_id")
    op.execute("DELETE FROM transform_connections WHERE is_default")
    op.alter_column("transform_connections", "secret_ref", nullable=False)
    op.alter_column("transform_connections", "destination_id", nullable=False)
    op.drop_column("transform_connections", "is_default")
    op.drop_column("transform_connections", "configuration_json")
    op.drop_column("transform_connections", "auth_method")
    op.drop_column("transform_connections", "connector_key")
