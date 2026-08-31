"""Let every relation in one schema share a dbt source alias.

A dbt source *is* a schema; its tables are entries inside it. Requiring
`source_name` to be unique per Transform forced three relations from one dataset
into `src_x`, `src_x_2`, `src_x_3` -- three sources over one schema, and an
alias nobody could guess when writing `{{ source(...) }}`.

Revision ID: e8b2d4f19a37
Revises: d7f3a81c9e20
"""

from __future__ import annotations

from alembic import op

revision: str = "e8b2d4f19a37"
down_revision: str | None = "d7f3a81c9e20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_transform_input_source_name", "transform_inputs", type_="unique",
    )
    op.create_index(
        "ix_transform_inputs_source_name", "transform_inputs",
        ["transform_id", "source_name"],
    )


def downgrade() -> None:
    op.drop_index("ix_transform_inputs_source_name", table_name="transform_inputs")
    # Existing rows may legitimately share an alias, so collapse duplicates onto
    # numbered names before the old constraint can be restored.
    op.execute(
        """
        UPDATE transform_inputs AS t
        SET source_name = t.source_name || '_' || t.rn
        FROM (
            SELECT id, ROW_NUMBER() OVER (
                PARTITION BY transform_id, source_name ORDER BY id
            ) AS rn
            FROM transform_inputs
        ) AS t2
        WHERE t.id = t2.id AND t2.rn > 1
        """
    )
    op.create_unique_constraint(
        "uq_transform_input_source_name", "transform_inputs",
        ["transform_id", "source_name"],
    )
