"""FastAPI dependencies: session, authentication, tenant context."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Cookie, Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.context import RequestContext
from app.core.db import get_session
from app.core.errors import ForbiddenError, UnauthorizedError
from app.core.logging import actor_id_var, workspace_id_var
from app.core.security import decode_session_token
from app.models.enums import WorkspaceStatus
from app.models.identity import User
from app.services import access

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _bearer(authorization: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


async def current_user(
    session: SessionDep,
    authorization: Annotated[str | None, Header()] = None,
    appbi_session: Annotated[str | None, Cookie(alias=settings.session_cookie_name)] = None,
) -> User:
    token = _bearer(authorization) or appbi_session
    if not token:
        raise UnauthorizedError("Bạn cần đăng nhập.")
    claims = decode_session_token(token)
    user = await session.get(User, uuid.UUID(claims["sub"]))
    if user is None or not user.is_active:
        raise UnauthorizedError("Tài khoản không còn hoạt động.")
    # A token issued before the last password change is no longer a session.
    # Tokens minted before this field existed carry no `sv`; they are treated
    # as version 0, which is what every untouched account still has.
    if int(claims.get("sv", 0)) != user.session_version:
        raise UnauthorizedError(
            "Phiên đăng nhập đã hết hiệu lực do mật khẩu được thay đổi.",
            code="SESSION_REVOKED")
    actor_id_var.set(str(user.id))
    return user


UserDep = Annotated[User, Depends(current_user)]


async def request_context(
    request: Request,
    session: SessionDep,
    user: UserDep,
    authorization: Annotated[str | None, Header()] = None,
    appbi_session: Annotated[str | None, Cookie(alias=settings.session_cookie_name)] = None,
    x_workspace_id: Annotated[str | None, Header(alias="X-Workspace-Id")] = None,
) -> RequestContext:
    """Resolve the tenant from the session, or from an explicit header naming a
    workspace the caller can reach. Never from a request body.

    "Can reach" is broader than "is a member of" since organisations arrived:
    an ORG_OWNER or ORG_ADMIN reaches every workspace their organisation holds
    without a membership row in any of them. `access.reachable` is the single
    place that decides it.

    Also the chokepoint for a forced password change. An account created from
    the bootstrap one-time secret can authenticate and change its password;
    everything else refuses until it has. Enforced here rather than per-route
    because "the one route somebody forgot" is how this control fails.
    """
    if user.password_change_required:
        raise ForbiddenError(
            "Tài khoản này phải đổi mật khẩu trước khi sử dụng.",
            code="PASSWORD_CHANGE_REQUIRED",
        )
    token = _bearer(authorization) or appbi_session
    claims = decode_session_token(token) if token else {}

    # Reach comes from a workspace membership *or* from administering the
    # organisation that owns it. Resolving both here is what keeps the switcher
    # and the permission check from disagreeing.
    accesses = await access.reachable(session, user)
    if not accesses:
        raise ForbiddenError("Tài khoản chưa thuộc workspace nào.")

    # An explicit header and a workspace remembered in the token are not the
    # same request, and must not fail the same way.
    #
    # `X-Workspace-Id` is the caller saying "operate on this one". If they
    # cannot reach it, falling back to another workspace would answer a
    # question nobody asked -- and the caller, believing they addressed the
    # workspace they named, would go on to write into a different tenant's.
    # Refuse instead.
    #
    # The `ws` claim is only a memory of where they were last. Access can be
    # withdrawn between sessions, and treating a stale claim as an error would
    # lock somebody out of a product they still have workspaces in, with no way
    # to clear it but deleting a cookie they cannot see.
    chosen = None
    if x_workspace_id:
        try:
            wanted_id = uuid.UUID(str(x_workspace_id))
        except (ValueError, TypeError):
            raise ForbiddenError("X-Workspace-Id không hợp lệ.") from None
        chosen = next((a for a in accesses if a.workspace.id == wanted_id), None)
        if chosen is None:
            raise ForbiddenError("Bạn không truy cập được workspace này.")
    elif claims.get("ws"):
        try:
            remembered = uuid.UUID(str(claims["ws"]))
            chosen = next((a for a in accesses if a.workspace.id == remembered), None)
        except (ValueError, TypeError):
            chosen = None
    chosen = chosen or accesses[0]

    workspace = chosen.workspace
    if workspace.status is not WorkspaceStatus.ACTIVE:
        raise ForbiddenError("Workspace đang không hoạt động.")

    org_role = await access.org_role_of(session, user, workspace.organization_id)
    workspace_id_var.set(str(workspace.id))
    return RequestContext(
        user_id=user.id,
        workspace_id=workspace.id,
        role=chosen.role,
        organization_id=workspace.organization_id,
        org_role=org_role,
        trace_id=getattr(request.state, "trace_id", ""),
        email=user.email,
        full_name=user.full_name,
        is_platform_admin=user.is_platform_admin,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        timezone=workspace.timezone,
        workspace_settings={
            "allow_save_without_test": workspace.allow_save_without_test,
            "auto_accept_additive_schema": workspace.auto_accept_additive_schema,
        },
    )


CtxDep = Annotated[RequestContext, Depends(request_context)]


async def platform_admin(ctx: CtxDep) -> RequestContext:
    if not ctx.is_platform_admin:
        raise ForbiddenError("Chỉ platform admin mới truy cập được khu vực này.")
    return ctx


AdminDep = Annotated[RequestContext, Depends(platform_admin)]
