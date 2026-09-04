"""Organisations own workspaces.

Until now a workspace was the top of the tree and a person reached one only
through a row in `memberships`. That works for a single tenant and falls over
the moment one customer has several workspaces: every administrator needs a
membership row per workspace, a workspace created on Tuesday is invisible to
them until somebody remembers to add them, and there is nothing that answers
"which workspaces does this customer have".

The backfill is written so an existing deployment keeps exactly what it had:
every current membership survives untouched, and the organisation layer is
added above it. Whoever already owned a workspace becomes an organisation
owner, because they are the person who was already administering it.

Revision ID: d5b81f7c0a24
Revises: c4d9e2f81a63
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d5b81f7c0a24"
down_revision: str | None = "c4d9e2f81a63"
branch_labels = None
depends_on = None

#: The organisation an existing deployment's workspaces are moved into.
DEFAULT_SLUG = "default"
DEFAULT_NAME = "Tổ chức mặc định"


def upgrade() -> None:
    conn = op.get_bind()

    op.execute("CREATE TYPE org_role AS ENUM ('ORG_OWNER', 'ORG_ADMIN', 'ORG_MEMBER')")

    # `workspace_status` already exists; reuse it rather than minting a second
    # enum that means the same thing.
    status = postgresql.ENUM(name="workspace_status", create_type=False)

    op.create_table(
        "organizations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("slug", sa.String(120), nullable=False, unique=True),
        sa.Column("status", status, nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_organizations_slug", "organizations", ["slug"])

    op.create_table(
        "organization_memberships",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", postgresql.ENUM(name="org_role", create_type=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("organization_id", "user_id", name="uq_org_membership_org_user"),
    )
    op.create_index("ix_org_memberships_org", "organization_memberships", ["organization_id"])
    op.create_index("ix_org_memberships_user", "organization_memberships", ["user_id"])

    # Nullable first: the column has to exist before there is an organisation
    # to point it at.
    op.add_column(
        "workspaces",
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True),
    )

    has_workspaces = conn.execute(sa.text("SELECT EXISTS (SELECT 1 FROM workspaces)")).scalar()
    if has_workspaces:
        org_id = conn.execute(
            sa.text(
                """
                INSERT INTO organizations (id, name, slug, status, created_at, updated_at)
                VALUES (gen_random_uuid(), :name, :slug, 'ACTIVE', now(), now())
                RETURNING id
                """
            ),
            {"name": DEFAULT_NAME, "slug": DEFAULT_SLUG},
        ).scalar_one()

        conn.execute(
            sa.text("UPDATE workspaces SET organization_id = :org WHERE organization_id IS NULL"),
            {"org": org_id},
        )

        # Anyone who already owned a workspace, or who is a platform admin, was
        # already administering this deployment. Demoting them to ORG_MEMBER
        # here would take away access they have today.
        conn.execute(
            sa.text(
                """
                INSERT INTO organization_memberships
                    (id, organization_id, user_id, role, created_at, updated_at)
                SELECT gen_random_uuid(), :org, u.id, 'ORG_OWNER'::org_role, now(), now()
                FROM users u
                WHERE u.is_platform_admin
                   OR EXISTS (SELECT 1 FROM memberships m
                              WHERE m.user_id = u.id AND m.role = 'OWNER')
                """
            ),
            {"org": org_id},
        )
        # Everyone else who can reach any workspace joins as a plain member:
        # their existing workspace memberships keep working and they gain
        # nothing they did not have.
        conn.execute(
            sa.text(
                """
                INSERT INTO organization_memberships
                    (id, organization_id, user_id, role, created_at, updated_at)
                SELECT gen_random_uuid(), :org, u.id, 'ORG_MEMBER'::org_role, now(), now()
                FROM users u
                WHERE EXISTS (SELECT 1 FROM memberships m WHERE m.user_id = u.id)
                  AND NOT EXISTS (SELECT 1 FROM organization_memberships om
                                  WHERE om.organization_id = :org AND om.user_id = u.id)
                """
            ),
            {"org": org_id},
        )

    # Now that every row has one, the column becomes the invariant it is meant
    # to be: a workspace without an organisation is unreachable.
    op.alter_column("workspaces", "organization_id", nullable=False)
    op.create_foreign_key(
        "fk_workspaces_organization", "workspaces", "organizations",
        ["organization_id"], ["id"], ondelete="RESTRICT",
    )
    op.create_index("ix_workspaces_organization", "workspaces", ["organization_id"])


def downgrade() -> None:
    op.drop_index("ix_workspaces_organization", table_name="workspaces")
    op.drop_constraint("fk_workspaces_organization", "workspaces", type_="foreignkey")
    op.drop_column("workspaces", "organization_id")
    op.drop_index("ix_org_memberships_user", table_name="organization_memberships")
    op.drop_index("ix_org_memberships_org", table_name="organization_memberships")
    op.drop_table("organization_memberships")
    op.drop_index("ix_organizations_slug", table_name="organizations")
    op.drop_table("organizations")
    op.execute("DROP TYPE org_role")
