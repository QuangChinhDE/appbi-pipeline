"""Who belongs to a workspace, and what they may do there.

ONE IMPLEMENTATION, TWO DOORS. The same person is managed from inside the
workspace they are a member of, and from the organisation screen that lists
every workspace the organisation holds. Those are two questions -- "who is in
here" and "who is in each of these" -- but one answer, and writing it twice is
how the two come to disagree about what an Owner is or which guard applies.

Every function here takes the workspace explicitly rather than reading it off
the request context, because the organisation door is precisely the one that
operates on a workspace the caller is not currently in.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.context import RequestContext
from app.core.errors import ValidationError
from app.core.permissions import (
    ASSIGNABLE_ROLES, Action, Module, Role, ValidationErrorLike, effective,
    level_of, parse_overrides, serialise,
)
from app.core.security import hash_password, password_problems
from app.models.identity import Membership, User
from app.schemas.domain import MemberInvite, MemberRoleUpdate, MemberView
from app.services import audit


def parse_assignable_role(raw: str) -> Role:
    try:
        role = Role(raw)
    except ValueError:
        raise ValidationError(
            f"{raw} is not a role this workspace can hand out.",
            code="ROLE_UNKNOWN",
        ) from None
    if role not in ASSIGNABLE_ROLES:
        raise ValidationError(
            f"{role.value} is not a role this workspace can hand out.",
            code="ROLE_NOT_ASSIGNABLE",
        )
    return role


def validated_overrides(raw: dict | None) -> dict | None:
    if raw is None:
        return None
    try:
        return parse_overrides(raw)
    except ValidationErrorLike as exc:
        raise ValidationError(exc.message, code=exc.code) from None


def view(membership: Membership, user: User) -> MemberView:
    """One row of a members list, with permissions already resolved.

    Resolved here rather than sent as "role plus a patch" so the editor and the
    gate cannot disagree about what a preset means -- and so the browser never
    needs its own copy of the preset table.
    """
    perms = effective(membership.role, membership.permissions)
    return MemberView(
        id=membership.id, user_id=user.id, email=user.email,
        full_name=user.full_name, role=membership.role.value,
        created_at=membership.created_at,
        permissions=serialise(perms),
        levels={m.value: level_of(m, perms.get(m, set())) for m in Module},
        customised=bool(membership.permissions),
        auth_provider=user.auth_provider,
    )


async def assert_not_last_owner(
    session: AsyncSession, workspace_id: uuid.UUID, membership_id: uuid.UUID,
) -> None:
    """Refuse to leave a workspace with nobody who can administer it.

    Demoting or removing the last OWNER is not recoverable from inside the
    product: members, settings and the role picker itself all sit behind
    permissions only an OWNER holds, so the workspace becomes a room whose door
    locks from the outside.
    """
    remaining = await session.scalar(
        select(func.count()).select_from(Membership).where(
            Membership.workspace_id == workspace_id,
            Membership.role == Role.OWNER,
            Membership.id != membership_id,
        )
    )
    if not remaining:
        raise ValidationError(
            "A workspace has to keep at least one Owner. Name another one first.",
            code="LAST_OWNER",
        )


async def assert_someone_can_administer(
    session: AsyncSession, workspace_id: uuid.UUID, *,
    changing: uuid.UUID | None = None, prospective: dict | None = None,
    removing: uuid.UUID | None = None,
) -> None:
    """Refuse a change that leaves nobody able to manage members.

    Counting Owners was enough while a role *was* the permission set. It is not
    any more: an Owner whose stored map revokes `members` is an Owner in name
    over a workspace whose door has locked from the outside -- the role picker,
    the invite form and this endpoint all sit behind the permission that was
    just removed.

    Platform administrators are not counted. They can always get in, and
    counting them would let a workspace be left with no administrator of its
    own on the grounds that somebody at the vendor could fix it.
    """
    rows = (await session.scalars(
        select(Membership).where(Membership.workspace_id == workspace_id)
    )).all()
    for membership in rows:
        # A membership being deleted is gone, not merely changed. Counting it
        # as still present would let the last administrator remove themselves
        # -- the exact door this guard exists to keep open.
        if removing is not None and membership.id == removing:
            continue
        overrides = (
            prospective if changing is not None and membership.id == changing
            else membership.permissions
        )
        perms = effective(membership.role, overrides)
        if Action.EDIT in perms.get(Module.MEMBERS, set()):
            return
    raise ValidationError(
        "Somebody in this workspace has to keep permission to manage members. "
        "Grant it to another person first.",
        code="LAST_MEMBER_ADMIN",
    )


async def invite(
    session: AsyncSession, ctx: RequestContext, workspace_id: uuid.UUID,
    payload: MemberInvite,
) -> tuple[Membership, User]:
    """Put somebody in a workspace, creating the account if it is new."""
    role = parse_assignable_role(payload.role)
    overrides = validated_overrides(payload.permissions)

    email = payload.email.lower()
    user = await session.scalar(select(User).where(User.email == email))

    # Whether a password is wanted at all depends on whether an account is
    # being created. Adding a colleague who already works here to a second
    # workspace used to demand one anyway -- which was then thrown away,
    # because their real credential already exists. Asking for a secret in
    # order to ignore it teaches people that the field does not matter.
    if user is None:
        if payload.password is None and not settings.google_login_ready:
            # An account with no password and no Google is an account nobody can
            # sign in to. Refusing here is kinder than creating it and leaving an
            # administrator to work out why the person they invited cannot get in.
            raise ValidationError(
                "Set a password for this account, or configure Google sign-in first.",
                code="NO_SIGN_IN_METHOD",
            )
        if payload.password is not None:
            # The same policy the account holder will face when they change it,
            # and the same one bootstrap enforces.
            problems = password_problems(payload.password)
            if problems:
                raise ValidationError(" ".join(problems), code="PASSWORD_REQUIREMENTS_UNMET")

    if user is None:
        user = User(
            email=email, full_name=payload.full_name,
            password_hash=hash_password(payload.password) if payload.password else None,
            auth_provider="password" if payload.password else "google",
            # Whoever typed this password is not the person who will use the
            # account. It is a handover secret, not a credential, and it stops
            # working the moment it is used. Nothing to hand over when they
            # sign in with Google.
            password_change_required=bool(payload.password),
        )
        session.add(user)
        await session.flush()

    existing = await session.scalar(
        select(Membership).where(
            Membership.workspace_id == workspace_id, Membership.user_id == user.id
        )
    )
    if existing is not None:
        raise ValidationError(
            "That person is already a member of the workspace.",
            code="ALREADY_WORKSPACE_MEMBER",
        )

    membership = Membership(
        workspace_id=workspace_id, user_id=user.id, role=role, permissions=overrides,
    )
    session.add(membership)
    await session.flush()
    await audit.record(
        session, ctx, "member.invited", resource_type="MEMBER",
        resource_id=user.id, resource_name=user.email,
        workspace_id=workspace_id,
        after={"role": role.value,
               "permissions": serialise(effective(role, overrides))},
    )
    return membership, user


async def update(
    session: AsyncSession, ctx: RequestContext, workspace_id: uuid.UUID,
    membership: Membership, payload: MemberRoleUpdate,
) -> tuple[Membership, User | None]:
    """Change a role, a permission map, or both."""
    if payload.role is None and payload.permissions is None:
        raise ValidationError(
            "Send a role, a permission map, or both.", code="NOTHING_TO_CHANGE",
        )

    role = parse_assignable_role(payload.role) if payload.role else membership.role
    # Picking a role from the dropdown and sending nothing else means "this
    # preset, as written" -- so the departures from the old preset are cleared
    # rather than silently re-applied on top of a role that never had them.
    overrides = (
        validated_overrides(payload.permissions)
        if payload.permissions is not None
        else (None if payload.role else membership.permissions)
    )

    if membership.role is Role.OWNER and role is not Role.OWNER:
        await assert_not_last_owner(session, workspace_id, membership.id)
    await assert_someone_can_administer(
        session, workspace_id, changing=membership.id, prospective=overrides,
    )

    before_role = membership.role.value
    before_perms = serialise(effective(membership.role, membership.permissions))
    membership.role = role
    membership.permissions = overrides
    after_perms = serialise(effective(role, overrides))

    user = await session.get(User, membership.user_id)
    # The diff, not just the new state. "Who granted this, and what did they
    # change it from" is the question an investigation actually asks, and the
    # most security-relevant action in the product used to record only half of
    # the answer.
    moved = {
        module: {"from": before_perms.get(module, []), "to": after_perms.get(module, [])}
        for module in set(before_perms) | set(after_perms)
        if before_perms.get(module, []) != after_perms.get(module, [])
    }
    await audit.record(
        session, ctx, "member.permissions.changed", resource_type="MEMBER",
        resource_id=membership.user_id,
        resource_name=user.email if user else None,
        workspace_id=workspace_id,
        before={"role": before_role, "permissions": before_perms},
        after={"role": role.value, "permissions": after_perms, "changed": moved,
               "self_change": str(ctx.user_id) == str(membership.user_id)},
    )
    return membership, user


async def remove(
    session: AsyncSession, ctx: RequestContext, workspace_id: uuid.UUID,
    membership: Membership,
) -> None:
    if membership.user_id == ctx.user_id:
        raise ValidationError(
            "You cannot remove yourself from the workspace.",
            code="CANNOT_REMOVE_SELF",
        )
    if membership.role is Role.OWNER:
        await assert_not_last_owner(session, workspace_id, membership.id)
    # The same question the permission editor is held to. Removing the only
    # person who can manage members locks the door just as thoroughly as
    # revoking the permission, and only one of the two used to be guarded.
    await assert_someone_can_administer(session, workspace_id, removing=membership.id)
    await audit.record(
        session, ctx, "member.removed", resource_type="MEMBER",
        resource_id=membership.user_id, workspace_id=workspace_id,
    )
    await session.delete(membership)
