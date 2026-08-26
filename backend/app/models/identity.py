"""Users, workspaces, membership (sections 10, 22)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean, DateTime, Enum as SAEnum, ForeignKey, Integer, String, UniqueConstraint,
    false as sa_false, text as sa_text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TimestampMixin
from app.core.permissions import Role
from app.models.enums import WorkspaceStatus


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    locale: Mapped[str] = mapped_column(String(8), default="vi", nullable=False)
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


class Workspace(Base, TimestampMixin):
    __tablename__ = "workspaces"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
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

    user: Mapped[User] = relationship(back_populates="memberships", lazy="joined")
    workspace: Mapped[Workspace] = relationship(back_populates="memberships", lazy="joined")
