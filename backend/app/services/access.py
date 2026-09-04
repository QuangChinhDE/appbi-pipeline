"""Which workspaces a person can reach, and with what authority.

One module because two callers need the same answer and used to derive it
separately: `api/deps.py` builds the request context, `api/v1/auth.py` builds
the workspace list the UI shows. When those two disagree the symptom is a
workspace that appears in the switcher and 403s when opened, or the reverse --
a workspace somebody can use but cannot see.

Two sources of authority, and the order matters:

1. An organisation role of ORG_OWNER or ORG_ADMIN reaches every workspace the
   organisation holds, as OWNER. This is what makes a workspace created today
   administrable today.
2. A row in `memberships` reaches exactly that workspace, with exactly that
   role.

A person can have both. The organisation grant wins, because it is the broader
statement and demoting it silently would make an administrator less able to act
in one workspace than in its siblings for no visible reason.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import ORG_ROLES_WITH_WORKSPACE_ACCESS, OrgRole, Role
from app.models.enums import WorkspaceStatus
from app.models.identity import Membership, Organization, OrganizationMembership, User, Workspace


@dataclass(slots=True, frozen=True)
class WorkspaceAccess:
    """A workspace the user can open, and how they got there."""

    workspace: Workspace
    role: Role
    #: Set when the reach came from the organisation rather than a membership.
    #: The UI says "through the organisation" instead of implying somebody was
    #: added to this workspace by hand.
    via_organization: bool


async def org_role_of(
    session: AsyncSession, user: User, organization_id: uuid.UUID
) -> OrgRole | None:
    return await session.scalar(
        select(OrganizationMembership.role).where(
            OrganizationMembership.user_id == user.id,
            OrganizationMembership.organization_id == organization_id,
        )
    )


async def organizations_of(session: AsyncSession, user: User) -> dict[uuid.UUID, OrgRole]:
    rows = await session.execute(
        select(OrganizationMembership.organization_id, OrganizationMembership.role)
        .where(OrganizationMembership.user_id == user.id)
    )
    return {org_id: role for org_id, role in rows.all()}


async def primary_organization(
    session: AsyncSession, user: User
) -> tuple[Organization | None, OrgRole | None]:
    """The organisation this account belongs to.

    The data model already allows several -- an agency administering more than
    one customer is the obvious case -- but the product surfaces one at a time,
    so this returns the one that owns the workspace being used. Callers that
    know the workspace should prefer `org_role_of`.
    """
    membership = await session.scalar(
        select(OrganizationMembership)
        .where(OrganizationMembership.user_id == user.id)
        .order_by(OrganizationMembership.created_at)
        .limit(1)
    )
    if membership is None:
        return None, None
    organization = await session.get(Organization, membership.organization_id)
    return organization, membership.role


def effective_role(user: User, workspace_role: Role | None, org_role: OrgRole | None) -> Role:
    """The role that actually applies inside a workspace.

    Platform admin outranks everything; then the organisation grant; then the
    membership. Returning ANALYST for "no claim at all" would be wrong -- the
    caller must not reach a workspace it has no claim on, so that case is the
    caller's to refuse, not this function's to paper over.
    """
    if user.is_platform_admin:
        return Role.PLATFORM_ADMIN
    if org_role in ORG_ROLES_WITH_WORKSPACE_ACCESS:
        return Role.OWNER
    if workspace_role is not None:
        return workspace_role
    raise LookupError("no claim on this workspace")


async def reachable(session: AsyncSession, user: User) -> list[WorkspaceAccess]:
    """Every workspace the user can open, ordered so the list is stable.

    Includes suspended workspaces: the caller decides what to do with them, and
    hiding one here made a workspace vanish from the switcher with no
    explanation the moment somebody suspended it.
    """
    memberships = {
        m.workspace_id: m.role
        for m in (await session.scalars(
            select(Membership).where(Membership.user_id == user.id)
        )).all()
    }
    org_roles = await organizations_of(session, user)
    admin_orgs = [
        org_id for org_id, role in org_roles.items()
        if role in ORG_ROLES_WITH_WORKSPACE_ACCESS
    ]

    workspaces: dict[uuid.UUID, Workspace] = {}
    if memberships:
        for workspace in (await session.scalars(
            select(Workspace).where(Workspace.id.in_(memberships.keys()))
        )).all():
            workspaces[workspace.id] = workspace
    if admin_orgs:
        for workspace in (await session.scalars(
            select(Workspace).where(Workspace.organization_id.in_(admin_orgs))
        )).all():
            workspaces[workspace.id] = workspace

    out: list[WorkspaceAccess] = []
    for workspace in workspaces.values():
        org_role = org_roles.get(workspace.organization_id)
        via_org = org_role in ORG_ROLES_WITH_WORKSPACE_ACCESS
        try:
            role = effective_role(user, memberships.get(workspace.id), org_role)
        except LookupError:                                   # pragma: no cover
            continue
        out.append(WorkspaceAccess(workspace=workspace, role=role, via_organization=via_org))

    # Active first, then by name: the switcher should open on something usable.
    out.sort(key=lambda a: (a.workspace.status is not WorkspaceStatus.ACTIVE,
                            a.workspace.name.lower()))
    return out
