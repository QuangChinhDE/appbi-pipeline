"""Authentication, current user and workspace switching (section 10)."""

from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi import APIRouter, Response
from sqlalchemy import select

from app.api.deps import CtxDep, SessionDep, UserDep
from app.core.config import settings
from app.core.db import utcnow
from app.core.errors import ForbiddenError, RateLimitedError, UnauthorizedError, ValidationError
from app.core.permissions import Action, Module, Role, permission_map
from app.core.security import (
    hash_password, issue_session_token, password_problems, verify_password,
)
from app.models.enums import AuditResult
from app.models.identity import Membership, User, Workspace
from app.schemas.common import Acknowledged
from app.schemas.domain import (
    ChangePasswordRequest, CurrentUser, LoginRequest, MemberInvite, MemberRoleUpdate, MemberView,
    WorkspaceSettingsUpdate, WorkspaceSummary,
)
from app.services import audit

router = APIRouter(tags=["auth"])

MAX_FAILED_LOGINS = 5
LOCK_MINUTES = 15


async def _memberships(session, user: User) -> list[Membership]:
    return list((await session.scalars(
        select(Membership).where(Membership.user_id == user.id)
    )).all())


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        settings.session_cookie_name, token,
        httponly=True, samesite="lax", secure=settings.cookie_secure,
        max_age=settings.session_ttl_seconds, path="/",
    )


@router.post("/auth/login", response_model=CurrentUser)
async def login(payload: LoginRequest, response: Response, session: SessionDep) -> CurrentUser:
    user = await session.scalar(select(User).where(User.email == payload.email.lower()))
    if user is None:
        # Same wording either way so the endpoint is not a user-enumeration oracle.
        raise UnauthorizedError("Email hoặc mật khẩu không đúng.")
    if user.locked_until and user.locked_until > utcnow():
        raise RateLimitedError("Tài khoản tạm khóa do đăng nhập sai nhiều lần. Thử lại sau ít phút.")

    if not verify_password(payload.password, user.password_hash):
        user.failed_login_count += 1
        if user.failed_login_count >= MAX_FAILED_LOGINS:
            user.locked_until = utcnow() + timedelta(minutes=LOCK_MINUTES)
            user.failed_login_count = 0
        await audit.record(session, None, "auth.login.failed", resource_type="USER",
                           resource_id=user.id, result=AuditResult.FAILURE)
        await session.commit()
        raise UnauthorizedError("Email hoặc mật khẩu không đúng.")
    if not user.is_active:
        raise ForbiddenError("Tài khoản đã bị vô hiệu hóa.")

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = utcnow()
    memberships = await _memberships(session, user)
    workspace_id = memberships[0].workspace_id if memberships else None

    token = issue_session_token(user.id, workspace_id, user.session_version)
    _set_cookie(response, token)
    await audit.record(session, None, "auth.login.succeeded", resource_type="USER",
                       resource_id=user.id, workspace_id=workspace_id)
    await session.commit()
    return await _current_user_payload(session, user, workspace_id)


@router.post("/auth/logout", response_model=Acknowledged)
async def logout(response: Response) -> Acknowledged:
    response.delete_cookie(settings.session_cookie_name, path="/")
    return Acknowledged(message="Đã đăng xuất.")


@router.get("/auth/me", response_model=CurrentUser)
async def me(session: SessionDep, user: UserDep, ctx: CtxDep) -> CurrentUser:
    return await _current_user_payload(session, user, ctx.workspace_id)


@router.post("/auth/switch-workspace/{workspace_id}", response_model=CurrentUser)
async def switch_workspace(
    workspace_id: uuid.UUID, response: Response, session: SessionDep, user: UserDep
) -> CurrentUser:
    memberships = await _memberships(session, user)
    if not any(m.workspace_id == workspace_id for m in memberships):
        raise ForbiddenError("Bạn không thuộc workspace này.")
    _set_cookie(response, issue_session_token(user.id, workspace_id, user.session_version))
    return await _current_user_payload(session, user, workspace_id)


async def _current_user_payload(session, user: User, workspace_id) -> CurrentUser:
    memberships = await _memberships(session, user)
    summaries: list[WorkspaceSummary] = []
    active: WorkspaceSummary | None = None
    role: Role | None = None
    for membership in memberships:
        workspace = await session.get(Workspace, membership.workspace_id)
        if workspace is None:
            continue
        summary = WorkspaceSummary(
            id=workspace.id, name=workspace.name, slug=workspace.slug,
            role=membership.role.value, timezone=workspace.timezone,
            status=workspace.status.value,
        )
        summaries.append(summary)
        if workspace.id == workspace_id:
            active = summary
            role = membership.role
    if active is None and summaries:
        active = summaries[0]
        role = Role(active.role)

    effective = Role.PLATFORM_ADMIN if user.is_platform_admin else (role or Role.ANALYST)
    return CurrentUser(
        id=user.id, email=user.email, full_name=user.full_name, locale=user.locale,
        is_platform_admin=user.is_platform_admin, workspace=active, workspaces=summaries,
        role=effective.value, permissions=permission_map(effective),
        password_change_required=user.password_change_required,
    )


@router.post("/auth/change-password", response_model=CurrentUser)
async def change_password(
    payload: ChangePasswordRequest, response: Response, session: SessionDep, user: UserDep
) -> CurrentUser:
    """The only thing an account created by the bootstrap secret may do.

    Deliberately on `UserDep` rather than `CtxDep`: the tenant-resolving
    dependency is where the forced-change guard lives, so routing this through
    it would lock the account out of the one action that unlocks it.
    """
    if not verify_password(payload.current_password, user.password_hash):
        await audit.record(session, None, "auth.password.change_failed",
                           resource_type="USER", resource_id=user.id,
                           result=AuditResult.FAILURE)
        await session.commit()
        raise UnauthorizedError("Mật khẩu hiện tại không đúng.")

    problems = password_problems(payload.new_password)
    if problems:
        raise ValidationError(" ".join(problems))
    if payload.new_password == payload.current_password:
        raise ValidationError("Mật khẩu mới phải khác mật khẩu hiện tại.")

    user.password_hash = hash_password(payload.new_password)
    user.password_change_required = False
    user.password_changed_at = utcnow()
    # Every session issued before this point was issued against the old
    # credential -- including whatever the deployment pipeline used, and
    # including anyone else who signed in with the same one-time secret. This
    # line is what makes that true; the previous version said so in a comment
    # and did nothing, so a second holder of the bootstrap password kept a live
    # platform-admin session the moment the first one cleared the flag.
    user.session_version += 1
    user.failed_login_count = 0
    await audit.record(session, None, "auth.password.changed",
                       resource_type="USER", resource_id=user.id)
    await session.commit()

    memberships = await _memberships(session, user)
    workspace_id = memberships[0].workspace_id if memberships else None
    # The caller's own token was just revoked along with everyone else's, so
    # hand back a new one. Without this the change succeeds and the next
    # request from the same browser is a 401.
    _set_cookie(response, issue_session_token(user.id, workspace_id, user.session_version))
    return await _current_user_payload(session, user, workspace_id)


# ── workspace settings & members ───────────────────────────────────────────

@router.get("/workspace", response_model=WorkspaceSummary)
async def get_workspace(session: SessionDep, ctx: CtxDep) -> WorkspaceSummary:
    ctx.require(Module.SETTINGS, Action.VIEW)
    workspace = await session.get(Workspace, ctx.workspace_id)
    return WorkspaceSummary(
        id=workspace.id, name=workspace.name, slug=workspace.slug, role=ctx.role.value,
        timezone=workspace.timezone, status=workspace.status.value,
    )


@router.get("/workspace/settings")
async def workspace_settings(session: SessionDep, ctx: CtxDep) -> dict:
    ctx.require(Module.SETTINGS, Action.VIEW)
    workspace = await session.get(Workspace, ctx.workspace_id)
    return {
        "id": str(workspace.id),
        "name": workspace.name,
        "slug": workspace.slug,
        "timezone": workspace.timezone,
        "allow_save_without_test": workspace.allow_save_without_test,
        "auto_accept_additive_schema": workspace.auto_accept_additive_schema,
        "min_schedule_interval_seconds": settings.min_schedule_interval_seconds,
        "max_concurrent_runs_per_workspace": settings.max_concurrent_runs_per_workspace,
    }


@router.patch("/workspace/settings")
async def update_workspace_settings(
    payload: WorkspaceSettingsUpdate, session: SessionDep, ctx: CtxDep
) -> dict:
    ctx.require(Module.SETTINGS, Action.EDIT)
    workspace = await session.get(Workspace, ctx.workspace_id)
    before = {"name": workspace.name, "timezone": workspace.timezone,
              "allow_save_without_test": workspace.allow_save_without_test}
    if payload.name is not None:
        workspace.name = payload.name
    if payload.timezone is not None:
        from app.services.scheduling import require_zone

        workspace.timezone = require_zone(payload.timezone)
    if payload.allow_save_without_test is not None:
        workspace.allow_save_without_test = payload.allow_save_without_test
    if payload.auto_accept_additive_schema is not None:
        workspace.auto_accept_additive_schema = payload.auto_accept_additive_schema
    await audit.record(session, ctx, "workspace.settings.updated", resource_type="WORKSPACE",
                       resource_id=workspace.id, resource_name=workspace.name,
                       before=before, after={"name": workspace.name,
                                             "timezone": workspace.timezone})
    await session.commit()
    return await workspace_settings(session, ctx)


@router.get("/workspace/members", response_model=list[MemberView])
async def list_members(session: SessionDep, ctx: CtxDep) -> list[MemberView]:
    ctx.require(Module.MEMBERS, Action.VIEW)
    memberships = list((await session.scalars(
        select(Membership).where(Membership.workspace_id == ctx.workspace_id)
    )).all())
    out = []
    for membership in memberships:
        user = await session.get(User, membership.user_id)
        if user is None:
            continue
        out.append(MemberView(
            id=membership.id, user_id=user.id, email=user.email, full_name=user.full_name,
            role=membership.role.value, created_at=membership.created_at,
        ))
    out.sort(key=lambda m: m.full_name.lower())
    return out


@router.post("/workspace/members", response_model=MemberView, status_code=201)
async def invite_member(payload: MemberInvite, session: SessionDep, ctx: CtxDep) -> MemberView:
    ctx.require(Module.MEMBERS, Action.CREATE)
    try:
        role = Role(payload.role.upper())
    except ValueError as exc:
        raise ValidationError(f"Vai trò '{payload.role}' không hợp lệ.") from exc

    email = payload.email.lower()
    user = await session.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(email=email, full_name=payload.full_name,
                    password_hash=hash_password(payload.password))
        session.add(user)
        await session.flush()

    existing = await session.scalar(
        select(Membership).where(
            Membership.workspace_id == ctx.workspace_id, Membership.user_id == user.id
        )
    )
    if existing is not None:
        raise ValidationError("Người dùng đã là thành viên của workspace.")

    membership = Membership(workspace_id=ctx.workspace_id, user_id=user.id, role=role)
    session.add(membership)
    await session.flush()
    await audit.record(session, ctx, "member.invited", resource_type="MEMBER",
                       resource_id=user.id, resource_name=user.email,
                       after={"role": role.value})
    await session.commit()
    return MemberView(id=membership.id, user_id=user.id, email=user.email,
                      full_name=user.full_name, role=role.value,
                      created_at=membership.created_at)


@router.patch("/workspace/members/{member_id}", response_model=MemberView)
async def update_member_role(
    member_id: uuid.UUID, payload: MemberRoleUpdate, session: SessionDep, ctx: CtxDep
) -> MemberView:
    ctx.require(Module.MEMBERS, Action.EDIT)
    membership = await session.scalar(
        select(Membership).where(
            Membership.id == member_id, Membership.workspace_id == ctx.workspace_id
        )
    )
    if membership is None:
        raise ValidationError("Không tìm thấy thành viên.")
    try:
        role = Role(payload.role.upper())
    except ValueError as exc:
        raise ValidationError(f"Vai trò '{payload.role}' không hợp lệ.") from exc

    before = membership.role.value
    membership.role = role
    user = await session.get(User, membership.user_id)
    await audit.record(session, ctx, "member.role.changed", resource_type="MEMBER",
                       resource_id=membership.user_id, resource_name=user.email if user else None,
                       before={"role": before}, after={"role": role.value})
    await session.commit()
    return MemberView(id=membership.id, user_id=user.id, email=user.email,
                      full_name=user.full_name, role=role.value,
                      created_at=membership.created_at)


@router.delete("/workspace/members/{member_id}", status_code=204)
async def remove_member(member_id: uuid.UUID, session: SessionDep, ctx: CtxDep) -> Response:
    ctx.require(Module.MEMBERS, Action.DELETE)
    membership = await session.scalar(
        select(Membership).where(
            Membership.id == member_id, Membership.workspace_id == ctx.workspace_id
        )
    )
    if membership is None:
        return Response(status_code=204)
    if membership.user_id == ctx.user_id:
        raise ValidationError("Không thể tự xóa chính mình khỏi workspace.")
    await audit.record(session, ctx, "member.removed", resource_type="MEMBER",
                       resource_id=membership.user_id)
    await session.delete(membership)
    await session.commit()
    return Response(status_code=204)
