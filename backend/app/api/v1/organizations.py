"""The organisation: its members, and the workspaces it owns.

Scoped to `ctx.organization_id`, which comes from the workspace the session is
using -- never from the request body. That is the same tenant-isolation rule the
rest of the API follows, one level up: an organisation admin administers their
own organisation, and there is no request shape that reaches another one.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Response
from sqlalchemy import func, select

from app.api.deps import CtxDep, SessionDep
from app.core.errors import ForbiddenError, ResourceInUseError, ValidationError
from app.core.permissions import Action, OrgRole, Role
from app.core.security import hash_password, password_problems
from app.models.enums import PipelineStatus, ResourceStatus, WorkspaceStatus
from app.models.integration import Destination, Pipeline, Source
from app.models.identity import Membership, Organization, OrganizationMembership, User, Workspace
from app.transforms.models import TransformProject
from app.schemas.domain import (
    OrganizationSummary, OrganizationUpdate, OrgMemberInvite, OrgMemberRoleUpdate, OrgMemberView,
    WorkspaceCreate, WorkspaceSummary,
)
from app.services import audit

router = APIRouter(tags=["organization"])


def _org_id(ctx) -> uuid.UUID:
    if ctx.organization_id is None:
        raise ForbiddenError("Phiên làm việc chưa gắn với tổ chức nào.")
    return ctx.organization_id


def _parse_org_role(raw: str) -> OrgRole:
    try:
        return OrgRole(raw.upper())
    except ValueError as exc:
        raise ValidationError(f"Vai trò tổ chức '{raw}' không hợp lệ.") from exc


async def _assert_not_last_org_owner(session, organization_id, membership_id: uuid.UUID) -> None:
    """The organisation equivalent of the workspace last-owner rule.

    Losing the last ORG_OWNER is worse than losing the last workspace OWNER:
    nobody can create a workspace, admit a member, or hand ownership on, and
    there is no level above it inside the product to repair it from.
    """
    remaining = await session.scalar(
        select(func.count()).select_from(OrganizationMembership).where(
            OrganizationMembership.organization_id == organization_id,
            OrganizationMembership.role == OrgRole.ORG_OWNER,
            OrganizationMembership.id != membership_id,
        )
    )
    if not remaining:
        raise ValidationError(
            "Tổ chức phải còn ít nhất một Org Owner. Hãy chỉ định người khác trước.",
            code="LAST_ORG_OWNER",
        )


@router.get("/organization", response_model=OrganizationSummary)
async def get_organization(session: SessionDep, ctx: CtxDep) -> OrganizationSummary:
    ctx.require_org(Action.VIEW)
    organization = await session.get(Organization, _org_id(ctx))
    if organization is None:
        raise ValidationError("Không tìm thấy tổ chức.")
    count = await session.scalar(
        select(func.count()).select_from(Workspace)
        .where(Workspace.organization_id == organization.id)
    )
    return OrganizationSummary(
        id=organization.id, name=organization.name, slug=organization.slug,
        role=ctx.org_role.value if ctx.org_role else None,
        status=organization.status.value, workspace_count=count or 0,
    )


@router.patch("/organization", response_model=OrganizationSummary)
async def update_organization(
    payload: OrganizationUpdate, session: SessionDep, ctx: CtxDep
) -> OrganizationSummary:
    ctx.require_org(Action.EDIT)
    organization = await session.get(Organization, _org_id(ctx))
    if organization is None:
        raise ValidationError("Không tìm thấy tổ chức.")
    before = organization.name
    organization.name = payload.name.strip()
    await audit.record(session, ctx, "organization.renamed", resource_type="ORGANIZATION",
                       resource_id=organization.id, resource_name=organization.name,
                       before={"name": before}, after={"name": organization.name})
    await session.commit()
    return OrganizationSummary(
        id=organization.id, name=organization.name, slug=organization.slug,
        role=ctx.org_role.value if ctx.org_role else None,
        status=organization.status.value,
    )


@router.get("/organization/workspaces", response_model=list[WorkspaceSummary])
async def list_workspaces(session: SessionDep, ctx: CtxDep) -> list[WorkspaceSummary]:
    """Every workspace the organisation holds.

    Not the same list as `/auth/me`.`workspaces`, which is what the caller may
    open. An ORG_MEMBER sees the organisation has six workspaces here and can
    open the two they were added to -- that difference is the point.
    """
    ctx.require_org(Action.VIEW)
    workspaces = (await session.scalars(
        select(Workspace).where(Workspace.organization_id == _org_id(ctx))
        .order_by(Workspace.name)
    )).all()
    mine = {
        m.workspace_id: m.role
        for m in (await session.scalars(
            select(Membership).where(Membership.user_id == ctx.user_id)
        )).all()
    }
    reaches_all = ctx.can_org(Action.ADMIN)
    return [
        WorkspaceSummary(
            id=w.id, name=w.name, slug=w.slug,
            role=(Role.OWNER.value if reaches_all else
                  (mine[w.id].value if w.id in mine else None)),
            timezone=w.timezone, status=w.status.value,
            via_organization=reaches_all and w.id not in mine,
        )
        for w in workspaces
    ]


@router.post("/organization/workspaces", response_model=WorkspaceSummary, status_code=201)
async def create_workspace(
    payload: WorkspaceCreate, session: SessionDep, ctx: CtxDep
) -> WorkspaceSummary:
    ctx.require_org(Action.CREATE)
    slug = payload.slug.strip().lower()
    clash = await session.scalar(select(Workspace).where(Workspace.slug == slug))
    if clash is not None:
        raise ValidationError(f"Slug '{slug}' đã được dùng.")

    workspace = Workspace(
        organization_id=_org_id(ctx), name=payload.name.strip(), slug=slug,
        timezone=payload.timezone, status=WorkspaceStatus.ACTIVE,
    )
    session.add(workspace)
    await session.flush()

    # The creator gets an explicit membership as well as their organisation
    # grant. Without it, handing the organisation to somebody else later would
    # silently take this workspace away from the person who built it.
    session.add(Membership(workspace_id=workspace.id, user_id=ctx.user_id, role=Role.OWNER))
    await audit.record(session, ctx, "workspace.created", resource_type="WORKSPACE",
                       resource_id=workspace.id, resource_name=workspace.name,
                       after={"slug": slug})
    await session.commit()
    return WorkspaceSummary(
        id=workspace.id, name=workspace.name, slug=workspace.slug,
        role=Role.OWNER.value, timezone=workspace.timezone, status=workspace.status.value,
    )


async def _blocking_contents(session, workspace_id: uuid.UUID) -> list[dict]:
    """What still lives in this workspace and owns state outside the database.

    Every table cascades on `workspaces.id`, so deleting the row would take
    sources, destinations, pipelines and dbt projects with it -- silently, and
    without the teardown each of them needs. An Airbyte connection nobody
    deleted keeps running against a customer's warehouse, and a secret nobody
    revoked stays in the credential store with nothing left pointing at it.

    So the workspace refuses while any of them remain, and names them. Deleting
    a source already tears down its engine resource and its secret; this simply
    insists that path is used rather than bypassed. It is the same contract a
    source uses when a pipeline still depends on it, and the FE already renders
    these constraints.
    """
    blocking: list[dict] = []
    for model, kind, live in (
        (Pipeline, "PIPELINE", Pipeline.status != PipelineStatus.DELETED),
        (Source, "SOURCE", Source.status != ResourceStatus.DELETED),
        (Destination, "DESTINATION", Destination.status != ResourceStatus.DELETED),
        (TransformProject, "TRANSFORM", TransformProject.deleted_at.is_(None)),
    ):
        rows = (await session.scalars(
            select(model).where(model.workspace_id == workspace_id, live).limit(25)
        )).all()
        blocking += [{"type": kind, "id": str(r.id), "name": r.name} for r in rows]
    return blocking


@router.delete("/organization/workspaces/{workspace_id}", status_code=204)
async def delete_workspace(
    workspace_id: uuid.UUID, session: SessionDep, ctx: CtxDep
) -> Response:
    """Remove an empty workspace from the organisation.

    `delete` rather than `create`: an ORG_ADMIN may add workspaces all day, but
    removing one destroys run history that belongs to whoever built it. That is
    an owner's decision.
    """
    ctx.require_org(Action.DELETE)
    organization_id = _org_id(ctx)
    workspace = await session.scalar(
        select(Workspace).where(
            Workspace.id == workspace_id, Workspace.organization_id == organization_id
        )
    )
    if workspace is None:
        # Idempotent: deleting something already gone is not an error, and the
        # organisation scope above is what stops this being a probe for other
        # tenants' workspace ids.
        return Response(status_code=204)

    # An organisation with no workspaces is a locked room. Every route builds a
    # request context from a workspace, so the administrator left holding an
    # empty organisation could not reach the screen that creates the next one.
    remaining = await session.scalar(
        select(func.count()).select_from(Workspace).where(
            Workspace.organization_id == organization_id, Workspace.id != workspace.id
        )
    )
    if not remaining:
        raise ValidationError(
            "Tổ chức phải còn ít nhất một workspace. Hãy tạo workspace khác trước.",
            code="LAST_WORKSPACE",
        )

    blocking = await _blocking_contents(session, workspace.id)
    if blocking:
        raise ResourceInUseError(
            f"Workspace còn {len(blocking)} tài nguyên. Hãy xóa chúng trước — "
            "xóa từng cái sẽ dọn cả tài nguyên phía engine và thông tin đăng nhập.",
            constraints=blocking,
        )

    # Audit rows carry no foreign key to the workspace precisely so the record
    # of what happened outlives the thing it happened to.
    await audit.record(session, ctx, "workspace.deleted", resource_type="WORKSPACE",
                       resource_id=workspace.id, resource_name=workspace.name,
                       before={"slug": workspace.slug, "name": workspace.name})
    await session.delete(workspace)
    await session.commit()
    return Response(status_code=204)


@router.get("/organization/members", response_model=list[OrgMemberView])
async def list_org_members(session: SessionDep, ctx: CtxDep) -> list[OrgMemberView]:
    ctx.require_org(Action.VIEW)
    memberships = (await session.scalars(
        select(OrganizationMembership)
        .where(OrganizationMembership.organization_id == _org_id(ctx))
    )).all()
    out = []
    for membership in memberships:
        user = await session.get(User, membership.user_id)
        if user is None:
            continue
        out.append(OrgMemberView(
            id=membership.id, user_id=user.id, email=user.email, full_name=user.full_name,
            role=membership.role.value, created_at=membership.created_at,
        ))
    out.sort(key=lambda m: m.full_name.lower())
    return out


@router.post("/organization/members", response_model=OrgMemberView, status_code=201)
async def invite_org_member(
    payload: OrgMemberInvite, session: SessionDep, ctx: CtxDep
) -> OrgMemberView:
    ctx.require_org(Action.ADMIN)
    role = _parse_org_role(payload.role)
    problems = password_problems(payload.password)
    if problems:
        raise ValidationError(" ".join(problems))

    email = payload.email.lower()
    user = await session.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(email=email, full_name=payload.full_name,
                    password_hash=hash_password(payload.password),
                    # A handover secret, not a credential: whoever typed it is
                    # not the person who will use the account.
                    password_change_required=True)
        session.add(user)
        await session.flush()

    organization_id = _org_id(ctx)
    existing = await session.scalar(
        select(OrganizationMembership).where(
            OrganizationMembership.organization_id == organization_id,
            OrganizationMembership.user_id == user.id,
        )
    )
    if existing is not None:
        raise ValidationError("Người dùng đã là thành viên của tổ chức.")

    membership = OrganizationMembership(
        organization_id=organization_id, user_id=user.id, role=role
    )
    session.add(membership)
    await session.flush()
    await audit.record(session, ctx, "organization.member.invited", resource_type="ORG_MEMBER",
                       resource_id=user.id, resource_name=user.email,
                       after={"role": role.value})
    await session.commit()
    return OrgMemberView(
        id=membership.id, user_id=user.id, email=user.email, full_name=user.full_name,
        role=role.value, created_at=membership.created_at,
    )


@router.patch("/organization/members/{member_id}", response_model=OrgMemberView)
async def update_org_member_role(
    member_id: uuid.UUID, payload: OrgMemberRoleUpdate, session: SessionDep, ctx: CtxDep
) -> OrgMemberView:
    ctx.require_org(Action.ADMIN)
    organization_id = _org_id(ctx)
    membership = await session.scalar(
        select(OrganizationMembership).where(
            OrganizationMembership.id == member_id,
            OrganizationMembership.organization_id == organization_id,
        )
    )
    if membership is None:
        raise ValidationError("Không tìm thấy thành viên tổ chức.")
    role = _parse_org_role(payload.role)

    # An ORG_ADMIN may not mint an owner, nor demote one: that is the boundary
    # between running the organisation and owning it.
    if not ctx.can_org(Action.DELETE) and OrgRole.ORG_OWNER in (role, membership.role):
        raise ForbiddenError("Chỉ Org Owner mới thay đổi được vai trò Org Owner.")
    if membership.role is OrgRole.ORG_OWNER and role is not OrgRole.ORG_OWNER:
        await _assert_not_last_org_owner(session, organization_id, membership.id)

    before = membership.role.value
    membership.role = role
    user = await session.get(User, membership.user_id)
    await audit.record(session, ctx, "organization.member.role_changed",
                       resource_type="ORG_MEMBER", resource_id=membership.user_id,
                       resource_name=user.email if user else None,
                       before={"role": before}, after={"role": role.value})
    await session.commit()
    return OrgMemberView(
        id=membership.id, user_id=user.id, email=user.email, full_name=user.full_name,
        role=role.value, created_at=membership.created_at,
    )


@router.delete("/organization/members/{member_id}", status_code=204)
async def remove_org_member(
    member_id: uuid.UUID, session: SessionDep, ctx: CtxDep
) -> Response:
    ctx.require_org(Action.ADMIN)
    organization_id = _org_id(ctx)
    membership = await session.scalar(
        select(OrganizationMembership).where(
            OrganizationMembership.id == member_id,
            OrganizationMembership.organization_id == organization_id,
        )
    )
    if membership is None:
        return Response(status_code=204)
    if membership.user_id == ctx.user_id:
        raise ValidationError("Không thể tự xóa chính mình khỏi tổ chức.")
    if membership.role is OrgRole.ORG_OWNER:
        if not ctx.can_org(Action.DELETE):
            raise ForbiddenError("Chỉ Org Owner mới xóa được một Org Owner khác.")
        await _assert_not_last_org_owner(session, organization_id, membership.id)

    await audit.record(session, ctx, "organization.member.removed", resource_type="ORG_MEMBER",
                       resource_id=membership.user_id)
    await session.delete(membership)
    await session.commit()
    return Response(status_code=204)
