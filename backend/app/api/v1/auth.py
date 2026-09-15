"""Authentication, current user and workspace switching (section 10)."""

from __future__ import annotations

import uuid
from datetime import timedelta

from typing import Annotated

from fastapi import APIRouter, Cookie, Header, Response
from sqlalchemy import func, select

from app.api.deps import CtxDep, SessionDep, UserDep, _bearer
from app.core.config import settings
from app.core.db import utcnow
from app.core.errors import ForbiddenError, UnauthorizedError, ValidationError
from app.core import google_identity
from app.core.permissions import (
    Action, Module, OrgRole, Role, catalogue, effective, level_of, org_permissions, serialise,
)
from app.core.security import (
    decode_session_token,
    hash_password, issue_session_token, password_problems, verify_password,
)
from app.models.enums import AuditResult
from app.models.identity import Membership, Organization, User, Workspace
from app.schemas.common import Acknowledged
from app.schemas.domain import (
    AuthMethods, ChangePasswordRequest, CurrentUser, EngineCapabilities,
    GoogleLoginRequest, LoginRequest, MemberInvite, MemberRoleUpdate, MemberView,
    OrganizationSummary, WorkspaceSettingsUpdate, WorkspaceSummary,
)
from app.services import access, audit, members as member_service

router = APIRouter(tags=["auth"])

MAX_FAILED_LOGINS = 5
LOCK_MINUTES = 15
#: Ceiling on the doubling, so a lockout is always recoverable by waiting.
MAX_LOCK_MINUTES = 240


async def _reachable_ids(session, user: User) -> list[uuid.UUID]:
    """Workspaces this account can open, membership or organisation.

    Was `_memberships`, and reading only the membership table is what would
    have locked an organisation administrator out of a workspace they
    administer: /auth/me would pick nothing, and switch-workspace would answer
    "you are not a member of this workspace" about a workspace they own.
    """
    return [entry.workspace.id for entry in await access.reachable(session, user)]


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        settings.session_cookie_name, token,
        httponly=True, samesite="lax", secure=settings.cookie_secure,
        max_age=settings.session_ttl_seconds, path="/",
    )


@router.post("/auth/login", response_model=CurrentUser)
async def login(payload: LoginRequest, response: Response, session: SessionDep) -> CurrentUser:
    if not settings.auth_password_login_enabled:
        raise ForbiddenError(
            "This deployment signs in with Google.",
            code="PASSWORD_LOGIN_DISABLED",
        )
    user = await session.scalar(select(User).where(User.email == payload.email.lower()))
    if user is None:
        # Same wording either way so the endpoint is not a user-enumeration oracle.
        raise UnauthorizedError(
            "That email and password do not match an account.",
            code="CREDENTIALS_INVALID",
        )
    if user.locked_until and user.locked_until > utcnow():
        # Deliberately the same 401 an unknown account gets, not a distinct
        # 429. A lockout response that only appears for real accounts turns
        # this endpoint into an enumeration oracle -- and worse, into a way to
        # lock a named administrator out on demand by failing five times.
        #
        # The lock still applies; it just does not announce itself. An operator
        # can see and clear it from the audit trail, which is where that
        # information belongs.
        await audit.record(session, None, "auth.login.locked_out",
                           resource_type="USER", resource_id=user.id,
                           result=AuditResult.FAILURE)
        await session.commit()
        raise UnauthorizedError(
            "That email and password do not match an account.",
            code="CREDENTIALS_INVALID",
        )

    # An account created for somebody who signs in with Google has no password
    # hash at all. Saying so is safe -- the caller already named an address that
    # exists -- and it is the difference between "try again" and "use the other
    # button", which is the whole of their problem.
    if not user.password_hash:
        raise UnauthorizedError(
            "This account signs in with Google.", code="USE_GOOGLE_SIGN_IN",
        )

    if not verify_password(payload.password, user.password_hash):
        user.failed_login_count += 1
        if user.failed_login_count >= MAX_FAILED_LOGINS:
            # Back off further each time rather than applying a flat 15
            # minutes. A fixed window is a denial-of-service budget: fail five
            # times every fifteen minutes and a named account is unusable
            # indefinitely for the cost of one request every three minutes.
            # Doubling makes sustained targeting expensive while leaving the
            # first genuine mistake cheap to recover from.
            rounds = user.lockout_count = (user.lockout_count or 0) + 1
            minutes = min(LOCK_MINUTES * (2 ** (rounds - 1)), MAX_LOCK_MINUTES)
            user.locked_until = utcnow() + timedelta(minutes=minutes)
            user.failed_login_count = 0
        await audit.record(session, None, "auth.login.failed", resource_type="USER",
                           resource_id=user.id, result=AuditResult.FAILURE)
        await session.commit()
        raise UnauthorizedError(
            "That email and password do not match an account.",
            code="CREDENTIALS_INVALID",
        )
    if not user.is_active:
        raise ForbiddenError(
            "This account has been disabled.", code="ACCOUNT_DISABLED",
        )

    user.failed_login_count = 0
    user.locked_until = None
    # A successful sign-in proves the owner is back; the escalation resets so
    # yesterday's typos do not make tomorrow's lockout an hour long.
    user.lockout_count = 0
    user.last_login_at = utcnow()
    reachable = await _reachable_ids(session, user)
    workspace_id = reachable[0] if reachable else None

    token = issue_session_token(user.id, workspace_id, user.session_version)
    _set_cookie(response, token)
    await audit.record(session, None, "auth.login.succeeded", resource_type="USER",
                       resource_id=user.id, workspace_id=workspace_id)
    await session.commit()
    return await _current_user_payload(session, user, workspace_id)


@router.post("/auth/logout", response_model=Acknowledged)
async def logout(response: Response) -> Acknowledged:
    response.delete_cookie(settings.session_cookie_name, path="/")
    return Acknowledged(message="Signed out.")


@router.get("/auth/config", response_model=AuthMethods)
async def auth_config() -> AuthMethods:
    """What the sign-in page should offer.

    Public, and deliberately so: it is asked before anybody has proved who they
    are, and it discloses nothing a sign-in form does not already show. The
    alternative -- compiling the client id into the frontend bundle -- makes the
    same value public and ties it to a rebuild.
    """
    return AuthMethods(
        password=settings.auth_password_login_enabled,
        google=settings.google_login_ready,
        google_client_id=(
            settings.auth_google_client_id.strip() if settings.google_login_ready else ""
        ),
        google_domains=settings.google_domains,
        default_locale=settings.locale,
    )


@router.post("/auth/google", response_model=CurrentUser)
async def google_login(
    payload: GoogleLoginRequest, response: Response, session: SessionDep
) -> CurrentUser:
    """Sign in with a Google account an administrator has already provisioned.

    Google proves the identity; it does not grant access. An address nobody has
    added to a workspace is refused, so the set of people who can reach this
    deployment stays a list an administrator wrote -- not everybody at a domain,
    and not everybody with a Google account.

    The Google subject is stored the first time an account signs in this way and
    matched on from then on. Matching on the email alone would mean that
    changing somebody's address in the members list hands their seat to whoever
    later proves ownership of the old one.
    """
    identity = await google_identity.verify(payload.credential)

    user = await session.scalar(
        select(User).where(User.google_sub == identity.subject)
    )
    if user is None:
        user = await session.scalar(
            select(User).where(func.lower(User.email) == identity.email)
        )
        if user is None:
            raise ForbiddenError(
                "That Google account has not been added to a workspace yet. "
                "Ask an administrator to invite this email address.",
                code="GOOGLE_ACCOUNT_NOT_PROVISIONED",
                details={"email": identity.email},
            )
        if user.google_sub and user.google_sub != identity.subject:
            # The address matches an account already linked to a *different*
            # Google identity. Somebody re-registered a freed address, or an
            # administrator typed an address that was already claimed; either
            # way, linking silently would hand over a seat.
            raise ForbiddenError(
                "That email belongs to an account linked to a different "
                "Google identity.",
                code="GOOGLE_IDENTITY_MISMATCH",
            )

    if not user.is_active:
        raise ForbiddenError("This account has been disabled.", code="ACCOUNT_DISABLED")

    first_link = user.google_sub is None
    user.google_sub = identity.subject
    user.auth_provider = "both" if user.password_hash else "google"
    if identity.picture:
        user.avatar_url = identity.picture
    if not (user.full_name or "").strip():
        user.full_name = identity.full_name
    # A handover password is a credential somebody else typed. Proving the
    # Google identity is a better proof of ownership than typing it would be,
    # so the forced change has served its purpose and stops blocking.
    user.password_change_required = False
    user.failed_login_count = 0
    user.locked_until = None
    user.lockout_count = 0
    user.last_login_at = utcnow()

    reachable = await _reachable_ids(session, user)
    if not reachable:
        raise ForbiddenError(
            "This account does not belong to a workspace yet.", code="NO_WORKSPACE",
        )
    workspace_id = reachable[0]

    token = issue_session_token(user.id, workspace_id, user.session_version)
    _set_cookie(response, token)
    if first_link:
        await audit.record(session, None, "auth.google.linked", resource_type="USER",
                           resource_id=user.id, workspace_id=workspace_id,
                           after={"email": identity.email})
    await audit.record(session, None, "auth.login.succeeded", resource_type="USER",
                       resource_id=user.id, workspace_id=workspace_id,
                       after={"provider": "google"})
    await session.commit()
    return await _current_user_payload(session, user, workspace_id)


@router.get("/auth/me", response_model=CurrentUser)
async def me(
    session: SessionDep,
    user: UserDep,
    appbi_session: Annotated[str | None, Cookie(alias=settings.session_cookie_name)] = None,
    authorization: Annotated[str | None, Header()] = None,
    x_workspace_id: Annotated[str | None, Header(alias="X-Workspace-Id")] = None,
) -> CurrentUser:
    """Who am I, readable even by an account that must change its password.

    This used to take `CtxDep`, which is where the forced-change guard lives.
    So the bootstrap admin could sign in, and then the very first thing the app
    does on load -- read the session back -- returned
    `403 PASSWORD_CHANGE_REQUIRED`. The frontend saw no user and rendered
    nothing: a white screen, on the one account a fresh production deployment
    has. The account could not even discover *why* it was blocked.

    `UserDep` authenticates and checks session revocation; it does not enforce
    the password gate. That gate stays on `request_context`, so every product
    route is still refused -- this returns identity and the flag, and nothing
    that reads or writes tenant data.
    """
    reachable = await _reachable_ids(session, user)
    workspace_id = reachable[0] if reachable else None
    token = _bearer(authorization) or appbi_session
    claims = decode_session_token(token) if token else {}

    # Same rule as `request_context`, so one header means one thing across the
    # API: naming a workspace you cannot reach is refused, while a stale
    # workspace remembered in the token quietly gives way to one you can.
    if x_workspace_id:
        try:
            wanted_id = uuid.UUID(str(x_workspace_id))
        except (ValueError, TypeError):
            raise ForbiddenError(
                "X-Workspace-Id is not a valid value.",
                code="WORKSPACE_HEADER_INVALID",
            ) from None
        if wanted_id not in reachable:
            raise ForbiddenError(
            "Your account cannot reach that workspace.",
            code="WORKSPACE_ACCESS_DENIED",
        )
        workspace_id = wanted_id
    elif claims.get("ws"):
        try:
            remembered = uuid.UUID(str(claims["ws"]))
        except (ValueError, TypeError):
            remembered = None
        if remembered in reachable:
            workspace_id = remembered
    return await _current_user_payload(session, user, workspace_id)


@router.post("/auth/switch-workspace/{workspace_id}", response_model=CurrentUser)
async def switch_workspace(
    workspace_id: uuid.UUID, response: Response, session: SessionDep, user: UserDep
) -> CurrentUser:
    if workspace_id not in await _reachable_ids(session, user):
        raise ForbiddenError(
            "Your account cannot reach that workspace.",
            code="WORKSPACE_ACCESS_DENIED",
        )
    _set_cookie(response, issue_session_token(user.id, workspace_id, user.session_version))
    return await _current_user_payload(session, user, workspace_id)


async def _current_user_payload(session, user: User, workspace_id) -> CurrentUser:
    # The same resolver the request context uses, so the switcher never offers
    # a workspace that would 403 -- nor hides one that would open.
    accesses = await access.reachable(session, user)
    summaries: list[WorkspaceSummary] = []
    active: WorkspaceSummary | None = None
    role: Role | None = None
    overrides: dict | None = None
    for entry in accesses:
        workspace = entry.workspace
        summary = WorkspaceSummary(
            id=workspace.id, name=workspace.name, slug=workspace.slug,
            role=entry.role.value, timezone=workspace.timezone,
            status=workspace.status.value, via_organization=entry.via_organization,
        )
        summaries.append(summary)
        if workspace.id == workspace_id:
            active = summary
            role = entry.role
            overrides = entry.permissions
    if active is None and summaries:
        active = summaries[0]
        role = Role(active.role)
        overrides = next(
            (a.permissions for a in accesses if a.workspace.id == active.id), None
        )

    role_here = Role.PLATFORM_ADMIN if user.is_platform_admin else (role or Role.ANALYST)
    # The same resolver the gate uses. The browser being told one thing and the
    # endpoint deciding another is the failure this replaces: a menu item that
    # appears, a page that opens, and a 403 behind it.
    resolved = effective(
        role_here, overrides, is_platform_admin=user.is_platform_admin,
    )

    # Prefer the organisation that owns the workspace being used; fall back to
    # the account's own, so a platform admin with no membership still sees one.
    org_role = None
    organization = None
    if active is not None:
        workspace = next(a.workspace for a in accesses if a.workspace.id == active.id)
        organization = await session.get(Organization, workspace.organization_id)
        org_role = await access.org_role_of(session, user, workspace.organization_id)
    if organization is None:
        organization, org_role = await access.primary_organization(session, user)

    return CurrentUser(
        id=user.id, email=user.email, full_name=user.full_name, locale=user.locale,
        is_platform_admin=user.is_platform_admin, workspace=active, workspaces=summaries,
        role=role_here.value, permissions=serialise(resolved),
        levels={m.value: level_of(m, resolved.get(m, set())) for m in Module},
        organization=(
            OrganizationSummary(
                id=organization.id, name=organization.name, slug=organization.slug,
                role=org_role.value if org_role else None,
                status=organization.status.value,
            ) if organization is not None else None
        ),
        organization_permissions=org_permissions(org_role) if not user.is_platform_admin
        else org_permissions(OrgRole.ORG_OWNER),
        password_change_required=user.password_change_required,
        engine_capabilities=EngineCapabilities(
            destination_naming=settings.supports_destination_naming,
        ),
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
        raise UnauthorizedError(
            "The current password is not right.",
            code="CURRENT_PASSWORD_WRONG",
        )

    problems = password_problems(payload.new_password)
    if problems:
        raise ValidationError(" ".join(problems), code="PASSWORD_REQUIREMENTS_UNMET")
    if payload.new_password == payload.current_password:
        raise ValidationError(
            "The new password has to differ from the current one.",
            code="NEW_PASSWORD_SAME",
        )

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

    reachable = await _reachable_ids(session, user)
    workspace_id = reachable[0] if reachable else None
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


@router.get("/workspace/permission-catalog")
async def permission_catalog(ctx: CtxDep) -> dict:
    """Every module, the actions it can be asked to perform, and the presets.

    Served rather than compiled into the frontend because the browser keeping
    its own copy of this list is exactly how a module comes to be editable in
    the admin screen while the backend has never heard of it -- or worse, how a
    module the backend gates on goes missing from the editor and can never be
    granted.
    """
    ctx.require(Module.MEMBERS, Action.VIEW)
    return catalogue()


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
        out.append(member_service.view(membership, user))
    out.sort(key=lambda m: m.full_name.lower())
    return out


@router.post("/workspace/members", response_model=MemberView, status_code=201)
async def invite_member(payload: MemberInvite, session: SessionDep, ctx: CtxDep) -> MemberView:
    ctx.require(Module.MEMBERS, Action.CREATE)
    membership, user = await member_service.invite(
        session, ctx, ctx.workspace_id, payload,
    )
    await session.commit()
    return member_service.view(membership, user)


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
        raise ValidationError("No such member.", code="MEMBER_NOT_FOUND")
    membership, user = await member_service.update(
        session, ctx, ctx.workspace_id, membership, payload,
    )
    await session.commit()
    return member_service.view(membership, user)


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
    await member_service.remove(session, ctx, ctx.workspace_id, membership)
    await session.commit()
    return Response(status_code=204)
