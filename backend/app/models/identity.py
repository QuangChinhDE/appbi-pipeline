"""Users, organisations, workspaces, membership (sections 10, 22)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean, DateTime, Enum as SAEnum, ForeignKey, Integer, String, UniqueConstraint,
    false as sa_false, text as sa_text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TimestampMixin
from app.core.permissions import OrgRole, Role
from app.models.enums import WorkspaceStatus


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Nullable: an account created for somebody who signs in with Google has
    # no password, and giving it a random one would leave a credential nobody
    # knows sitting in the table.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: How this account proves who it is: "password" or "google". Advisory --
    #: the gate is whether the credential presented actually verifies, not
    #: what this column says.
    auth_provider: Mapped[str] = mapped_column(
        String(16), default="password", server_default="password", nullable=False)
    #: Google's subject claim: stable for the life of the account, unlike the
    #: email, which a workspace administrator can change under it.
    google_sub: Mapped[str | None] = mapped_column(String(64), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Matches the frontend default. An account created without a preference
    # gets English rather than a language the person may not read.
    locale: Mapped[str] = mapped_column(String(8), default="en", nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_login_count: Mapped[int] = mapped_column(default=0, nullable=False)
    # Set when an account is created from a one-time bootstrap secret. The
    # account works exactly once: it can authenticate and change its password,
    # and nothing else, so the secret in the deployment pipeline stops being a
    # standing credential the moment someone uses it.
    password_change_required: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=sa_false(), nullable=False)
    # Incremented on every password change. A session token carries the value
    # it was issued against, so tokens from before the change stop
    # authenticating -- which is what makes "the password was rotated" mean
    # something to sessions that are already open.
    session_version: Mapped[int] = mapped_column(
        Integer, default=0, server_default=sa_text("0"), nullable=False)
    password_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # How many times this account has been locked since it last signed in
    # successfully. Drives exponential backoff: a flat lockout window is a
    # denial-of-service budget against a named administrator.
    lockout_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False)

    memberships: Mapped[list["Membership"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )


class Organization(Base, TimestampMixin):
    """The tenant that owns workspaces, and the unit a customer signs up as.

    Everything the product already scoped by workspace stays scoped by
    workspace. What this adds is the answer to "which workspaces exist and who
    may open them", which previously had no home: a person reached a workspace
    only through a row in `memberships`, so an administrator could not see a
    workspace until somebody added them to it one at a time.
    """

    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), unique=True, nullable=False, index=True)
    status: Mapped[WorkspaceStatus] = mapped_column(
        SAEnum(WorkspaceStatus, name="workspace_status"),
        default=WorkspaceStatus.ACTIVE, nullable=False,
    )

    workspaces: Mapped[list["Workspace"]] = relationship(back_populates="organization")
    memberships: Mapped[list["OrganizationMembership"]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )


class OrganizationMembership(Base, TimestampMixin):
    __tablename__ = "organization_memberships"
    __table_args__ = (
        UniqueConstraint("organization_id", "user_id", name="uq_org_membership_org_user"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    role: Mapped[OrgRole] = mapped_column(SAEnum(OrgRole, name="org_role"), nullable=False)

    user: Mapped["User"] = relationship(lazy="joined")
    organization: Mapped[Organization] = relationship(back_populates="memberships")


class Workspace(Base, TimestampMixin):
    __tablename__ = "workspaces"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    status: Mapped[WorkspaceStatus] = mapped_column(
        SAEnum(WorkspaceStatus, name="workspace_status"),
        default=WorkspaceStatus.ACTIVE, nullable=False,
    )
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Bangkok", nullable=False)
    # Which engine instance serves this tenant (section 64) -- swapping clusters
    # later must not change any product-facing id.
    engine_instance_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("engine_instances.id"), nullable=True
    )
    # Opaque, backend-only handle on the engine side. Never serialised.
    engine_workspace_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    allow_save_without_test: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    auto_accept_additive_schema: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    memberships: Mapped[list["Membership"]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )
    organization: Mapped[Organization] = relationship(back_populates="workspaces")


class Membership(Base, TimestampMixin):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("workspace_id", "user_id", name="uq_membership_ws_user"),)

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[Role] = mapped_column(SAEnum(Role, name="member_role"), nullable=False)
    #: What this membership holds *instead of* what its role's preset says,
    #: as `{module: [action, ...]}`.
    #:
    #: NULL means "exactly the preset", which is what every row meant before
    #: this column existed -- so no membership was migrated and a preset
    #: improved in Python still reaches everybody who never departed from it.
    #: A module missing from a stored map falls back to the preset too, so a
    #: module added to the product later does not arrive silently denied.
    permissions: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    user: Mapped[User] = relationship(back_populates="memberships", lazy="joined")
    workspace: Mapped[Workspace] = relationship(back_populates="memberships", lazy="joined")
