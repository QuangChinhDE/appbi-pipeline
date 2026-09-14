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
from app.models.enums import PipelineStatus, ResourceStatus, RunStatus, WorkspaceStatus
from app.models.integration import Destination, Pipeline, Source
from app.models.run import PipelineRun
from app.models.identity import Membership, Organization, OrganizationMembership, User, Workspace
from app.transforms.models import TransformProject
from app.schemas.domain import (
    MemberInvite, MemberRoleUpdate, MemberView,
    AccessChange, AccessChangeReport, CopyAccessRequest, OrgPersonCreate,
    OrganizationOverview, OrganizationPeople, PersonAcrossWorkspaces,
    WorkspaceHealth, WorkspaceSeat,
    OrganizationSummary, OrganizationUpdate, OrgMemberInvite, OrgMemberRoleUpdate, OrgMemberView,
    WorkspaceCreate, WorkspaceSummary,
)
from app.services import audit, members as member_service

router = APIRouter(tags=["organization"])


def _org_id(ctx) -> uuid.UUID:
    if ctx.organization_id is None:
        raise ForbiddenError(
            "This session is not attached to an organization.",
            code="SESSION_WITHOUT_ORG",
        )
    return ctx.organization_id


def _parse_org_role(raw: str) -> OrgRole:
    try:
        return OrgRole(raw.upper())
    except ValueError as exc:
        raise ValidationError(
            f"'{raw}' is not an organization role.",
            code="ORG_ROLE_INVALID", details={"role": raw},
        ) from exc


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
            "An organization has to keep at least one Org Owner. Name "
            "somebody else first.",
            code="LAST_ORG_OWNER",
        )


@router.get("/organization", response_model=OrganizationSummary)
async def get_organization(session: SessionDep, ctx: CtxDep) -> OrganizationSummary:
    ctx.require_org(Action.VIEW)
    organization = await session.get(Organization, _org_id(ctx))
    if organization is None:
        raise ValidationError("No such organization.", code="ORG_NOT_FOUND")
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
        raise ValidationError("No such organization.", code="ORG_NOT_FOUND")
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


#: A run in one of these states is the pipeline's problem, not a transient.
_BAD_RUN = (RunStatus.FAILED, RunStatus.FAILED_TO_START, RunStatus.TIMED_OUT)
_LIVE_RUN = (RunStatus.QUEUED, RunStatus.STARTING, RunStatus.RUNNING)


@router.get("/organization/overview", response_model=OrganizationOverview)
async def organization_overview(session: SessionDep, ctx: CtxDep) -> OrganizationOverview:
    """Which workspace needs somebody today.

    A workspace is a wall: nothing inside one is visible from another, which is
    the tenancy guarantee and also the reason an administrator of eight of them
    had no way to learn that one was failing without opening all eight. This is
    the one place that looks across, and it is gated on administering the
    organisation rather than on any workspace permission.
    """
    ctx.require_org(Action.ADMIN)
    organization_id = _org_id(ctx)
    workspaces = (await session.scalars(
        select(Workspace).where(Workspace.organization_id == organization_id)
        .order_by(Workspace.name)
    )).all()
    ids = [w.id for w in workspaces]
    if not ids:
        return OrganizationOverview()

    # Grouped aggregates rather than a handful of queries per workspace: this
    # page exists because opening eight workspaces was the old way to answer
    # it, and doing eight round trips server-side would be the same mistake
    # indoors.
    seats = dict((await session.execute(
        select(Membership.workspace_id, func.count())
        .where(Membership.workspace_id.in_(ids))
        .group_by(Membership.workspace_id)
    )).all())

    live_pipeline = Pipeline.status.notin_(
        (PipelineStatus.DELETED, PipelineStatus.DELETE_PENDING)
    )
    pipelines = dict((await session.execute(
        select(Pipeline.workspace_id, func.count())
        .where(Pipeline.workspace_id.in_(ids), live_pipeline)
        .group_by(Pipeline.workspace_id)
    )).all())

    # The latest run per pipeline, then counted by outcome. A pipeline that
    # failed last night and succeeded this morning is not failing.
    latest = (
        select(
            PipelineRun.pipeline_id,
            func.max(PipelineRun.created_at).label("at"),
        )
        .where(PipelineRun.workspace_id.in_(ids))
        .group_by(PipelineRun.pipeline_id)
        .subquery()
    )
    newest = (
        select(PipelineRun.workspace_id, PipelineRun.status)
        .join(
            latest,
            (PipelineRun.pipeline_id == latest.c.pipeline_id)
            & (PipelineRun.created_at == latest.c.at),
        )
        .subquery()
    )
    failing: dict = {}
    running: dict = {}
    for workspace_id, status, count in (await session.execute(
        select(newest.c.workspace_id, newest.c.status, func.count())
        .group_by(newest.c.workspace_id, newest.c.status)
    )).all():
        if status in _BAD_RUN:
            failing[workspace_id] = failing.get(workspace_id, 0) + count
        elif status in _LIVE_RUN:
            running[workspace_id] = running.get(workspace_id, 0) + count

    last_run = dict((await session.execute(
        select(PipelineRun.workspace_id, func.max(PipelineRun.created_at))
        .where(PipelineRun.workspace_id.in_(ids))
        .group_by(PipelineRun.workspace_id)
    )).all())

    mine = {
        m.workspace_id
        for m in (await session.scalars(
            select(Membership).where(Membership.user_id == ctx.user_id)
        )).all()
    }
    people = await session.scalar(
        select(func.count()).select_from(OrganizationMembership)
        .where(OrganizationMembership.organization_id == organization_id)
    )

    rows = [
        WorkspaceHealth(
            id=w.id, name=w.name, slug=w.slug, status=w.status.value,
            member_count=seats.get(w.id, 0),
            pipeline_count=pipelines.get(w.id, 0),
            failing_count=failing.get(w.id, 0),
            running_count=running.get(w.id, 0),
            last_run_at=last_run.get(w.id),
            via_organization=w.id not in mine,
        )
        for w in workspaces
    ]
    return OrganizationOverview(
        workspaces=rows,
        total_workspaces=len(rows),
        total_pipelines=sum(r.pipeline_count for r in rows),
        total_failing=sum(r.failing_count for r in rows),
        total_people=people or 0,
    )


@router.get("/organization/people", response_model=OrganizationPeople)
async def organization_people(session: SessionDep, ctx: CtxDep) -> OrganizationPeople:
    """Everybody, and everywhere they can reach.

    A workspace's member list answers "who is in here"; the organisation's
    answers "who is in the organisation". Neither answers "where can this
    person go", which is the question asked when somebody changes team or
    leaves -- and answering it meant opening every workspace in turn.

    Includes people who hold a workspace seat without an organisation row. They
    exist and they can sign in, and leaving them off a page called People would
    make it a list that quietly disagrees with who has access.
    """
    ctx.require_org(Action.ADMIN)
    organization_id = _org_id(ctx)
    workspaces = (await session.scalars(
        select(Workspace).where(Workspace.organization_id == organization_id)
        .order_by(Workspace.name)
    )).all()
    ids = [w.id for w in workspaces]

    org_rows = {
        m.user_id: m
        for m in (await session.scalars(
            select(OrganizationMembership)
            .where(OrganizationMembership.organization_id == organization_id)
        )).all()
    }
    memberships = list((await session.scalars(
        select(Membership).where(Membership.workspace_id.in_(ids))
    )).all()) if ids else []

    user_ids = set(org_rows) | {m.user_id for m in memberships}
    users = {
        u.id: u
        for u in (await session.scalars(
            select(User).where(User.id.in_(user_ids))
        )).all()
    } if user_ids else {}

    seats: dict = {}
    for membership in memberships:
        seats.setdefault(membership.user_id, []).append(
            WorkspaceSeat(
                workspace_id=membership.workspace_id,
                membership_id=membership.id,
                role=membership.role.value,
                customised=bool(membership.permissions),
            )
        )

    people = [
        PersonAcrossWorkspaces(
            user_id=user.id, email=user.email, full_name=user.full_name,
            is_active=user.is_active, auth_provider=user.auth_provider,
            org_role=(
                org_rows[user.id].role.value if user.id in org_rows else None
            ),
            org_membership_id=org_rows[user.id].id if user.id in org_rows else None,
            seats=seats.get(user.id, []),
        )
        for user in users.values()
    ]
    people.sort(key=lambda person: (person.full_name or person.email).lower())

    return OrganizationPeople(
        people=people,
        workspaces=[
            WorkspaceSummary(
                id=w.id, name=w.name, slug=w.slug, timezone=w.timezone,
                status=w.status.value,
            )
            for w in workspaces
        ],
    )


async def _org_workspaces(session, ctx) -> list[Workspace]:
    return list((await session.scalars(
        select(Workspace).where(Workspace.organization_id == _org_id(ctx))
        .order_by(Workspace.name)
    )).all())


async def _seats_of(session, user_id: uuid.UUID, workspace_ids) -> dict:
    if not workspace_ids:
        return {}
    return {
        m.workspace_id: m
        for m in (await session.scalars(
            select(Membership).where(
                Membership.user_id == user_id,
                Membership.workspace_id.in_(workspace_ids),
            )
        )).all()
    }


@router.post("/organization/people", response_model=PersonAcrossWorkspaces, status_code=201)
async def add_person(
    payload: OrgPersonCreate, session: SessionDep, ctx: CtxDep
) -> PersonAcrossWorkspaces:
    """Somebody joins, and lands everywhere they need to, in one call.

    Onboarding is one decision -- "Minh is on the marketing team, he needs
    these three workspaces" -- and it used to be spelled as one invitation per
    workspace, from inside each workspace in turn. Three screens is how the
    third one gets forgotten.
    """
    ctx.require_org(Action.ADMIN)
    workspaces = {w.id: w for w in await _org_workspaces(session, ctx)}

    wanted: dict[uuid.UUID, Role] = {}
    if payload.like_user_id is not None:
        # "Same as An" -- read An's seats rather than asking somebody to
        # transcribe them, which is where a wrong role comes from.
        for workspace_id, membership in (
            await _seats_of(session, payload.like_user_id, list(workspaces))
        ).items():
            wanted[workspace_id] = membership.role
    for seat in payload.seats:
        if seat.workspace_id not in workspaces:
            raise ValidationError("No such workspace.", code="WORKSPACE_NOT_FOUND")
        wanted[seat.workspace_id] = member_service.parse_assignable_role(seat.role)

    org_role = _parse_org_role(payload.org_role)
    user = await member_service.ensure_user(
        session, email=payload.email, full_name=payload.full_name,
        password=payload.password,
    )

    existing_org = await session.scalar(
        select(OrganizationMembership).where(
            OrganizationMembership.organization_id == _org_id(ctx),
            OrganizationMembership.user_id == user.id,
        )
    )
    if existing_org is None:
        session.add(OrganizationMembership(
            organization_id=_org_id(ctx), user_id=user.id, role=org_role,
        ))
    for workspace_id, role in wanted.items():
        await member_service.grant_seat(session, ctx, workspace_id, user, role)

    await audit.record(
        session, ctx, "org.person.added", resource_type="USER",
        resource_id=user.id, resource_name=user.email,
        after={"org_role": org_role.value,
               "workspaces": [workspaces[i].name for i in wanted]},
    )
    await session.commit()
    return await _person_view(session, ctx, user.id)


@router.get("/organization/people/{user_id}", response_model=PersonAcrossWorkspaces)
async def person_detail(
    user_id: uuid.UUID, session: SessionDep, ctx: CtxDep
) -> PersonAcrossWorkspaces:
    ctx.require_org(Action.ADMIN)
    return await _person_view(session, ctx, user_id)


@router.post(
    "/organization/people/{user_id}/copy-access", response_model=AccessChangeReport,
)
async def copy_access(
    user_id: uuid.UUID, payload: CopyAccessRequest, session: SessionDep, ctx: CtxDep
) -> AccessChangeReport:
    """Make one person's access match another's.

    The sentence an administrator says out loud is "give Minh what An has". It
    was previously eight dropdowns and a memory of what An had.
    """
    ctx.require_org(Action.ADMIN)
    if payload.from_user_id == user_id:
        raise ValidationError(
            "That is the same person.", code="COPY_FROM_SELF",
        )
    workspaces = {w.id: w for w in await _org_workspaces(session, ctx)}
    target = await session.get(User, user_id)
    if target is None:
        raise ValidationError("No such person.", code="PERSON_NOT_FOUND")

    source_seats = await _seats_of(session, payload.from_user_id, list(workspaces))
    target_seats = await _seats_of(session, user_id, list(workspaces))
    changes: list[AccessChange] = []

    for workspace_id, source in source_seats.items():
        current = target_seats.get(workspace_id)
        if current is None:
            await member_service.grant_seat(
                session, ctx, workspace_id, target, source.role, source.permissions,
            )
            changes.append(AccessChange(
                workspace_id=workspace_id, workspace_name=workspaces[workspace_id].name,
                action="granted", role=source.role.value,
            ))
        elif current.role != source.role or current.permissions != source.permissions:
            current.role = source.role
            current.permissions = source.permissions
            changes.append(AccessChange(
                workspace_id=workspace_id, workspace_name=workspaces[workspace_id].name,
                action="changed", role=source.role.value,
            ))

    if payload.mode == "match":
        for workspace_id, membership in target_seats.items():
            if workspace_id in source_seats:
                continue
            await member_service.remove(session, ctx, workspace_id, membership)
            changes.append(AccessChange(
                workspace_id=workspace_id, workspace_name=workspaces[workspace_id].name,
                action="removed",
            ))

    await audit.record(
        session, ctx, "org.person.access_copied", resource_type="USER",
        resource_id=target.id, resource_name=target.email,
        after={"from": str(payload.from_user_id), "mode": payload.mode,
               "changes": [c.model_dump(mode="json") for c in changes]},
    )
    await session.commit()
    return AccessChangeReport(changes=changes)


@router.delete("/organization/people/{user_id}", response_model=AccessChangeReport)
async def offboard_person(
    user_id: uuid.UUID, session: SessionDep, ctx: CtxDep
) -> AccessChangeReport:
    """Somebody leaves: every seat, and the organisation row, in one action.

    Doing it seat by seat is how one gets missed, and a missed seat is an
    account that still opens a warehouse after its owner has left. The report
    says what actually went, because "removed" and "removed from the two places
    I remembered" look identical otherwise.
    """
    ctx.require_org(Action.ADMIN)
    if str(user_id) == str(ctx.user_id):
        raise ValidationError(
            "You cannot remove your own access.", code="CANNOT_REMOVE_SELF",
        )
    target = await session.get(User, user_id)
    if target is None:
        raise ValidationError("No such person.", code="PERSON_NOT_FOUND")

    workspaces = {w.id: w for w in await _org_workspaces(session, ctx)}
    seats = await _seats_of(session, user_id, list(workspaces))
    changes: list[AccessChange] = []
    for workspace_id, membership in seats.items():
        await member_service.remove(session, ctx, workspace_id, membership)
        changes.append(AccessChange(
            workspace_id=workspace_id, workspace_name=workspaces[workspace_id].name,
            action="removed", role=membership.role.value,
        ))

    org_membership = await session.scalar(
        select(OrganizationMembership).where(
            OrganizationMembership.organization_id == _org_id(ctx),
            OrganizationMembership.user_id == user_id,
        )
    )
    if org_membership is not None:
        if org_membership.role is OrgRole.ORG_OWNER:
            await _assert_not_last_org_owner(session, _org_id(ctx), org_membership.id)
        await session.delete(org_membership)

    # Only when this was their last organisation. An account that still belongs
    # somewhere else must keep working -- disabling it here would reach outside
    # the tenant this call is scoped to.
    elsewhere = await session.scalar(
        select(func.count()).select_from(OrganizationMembership).where(
            OrganizationMembership.user_id == user_id,
            OrganizationMembership.organization_id != _org_id(ctx),
        )
    )
    deactivated = False
    if not elsewhere:
        target.is_active = False
        # Sessions already open stop authenticating, which is the half of
        # "removed" that a row deletion alone does not deliver.
        target.session_version = (target.session_version or 0) + 1
        deactivated = True

    await audit.record(
        session, ctx, "org.person.offboarded", resource_type="USER",
        resource_id=target.id, resource_name=target.email,
        after={"removed_from": [c.workspace_name for c in changes],
               "account_deactivated": deactivated},
    )
    await session.commit()
    return AccessChangeReport(changes=changes, account_deactivated=deactivated)


async def _person_view(session, ctx, user_id: uuid.UUID) -> PersonAcrossWorkspaces:
    user = await session.get(User, user_id)
    if user is None:
        raise ValidationError("No such person.", code="PERSON_NOT_FOUND")
    workspaces = await _org_workspaces(session, ctx)
    seats = await _seats_of(session, user_id, [w.id for w in workspaces])
    org_membership = await session.scalar(
        select(OrganizationMembership).where(
            OrganizationMembership.organization_id == _org_id(ctx),
            OrganizationMembership.user_id == user_id,
        )
    )
    return PersonAcrossWorkspaces(
        user_id=user.id, email=user.email, full_name=user.full_name,
        is_active=user.is_active, auth_provider=user.auth_provider,
        org_role=org_membership.role.value if org_membership else None,
        org_membership_id=org_membership.id if org_membership else None,
        seats=[
            WorkspaceSeat(
                workspace_id=membership.workspace_id,
                membership_id=membership.id,
                role=membership.role.value,
                customised=bool(membership.permissions),
            )
            for membership in seats.values()
        ],
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
    # One query for every workspace rather than one per row: the number is the
    # first thing somebody looks at on this screen, and fetching it per row is
    # how a list of eight workspaces becomes nine round trips.
    counts = dict((await session.execute(
        select(Membership.workspace_id, func.count())
        .where(Membership.workspace_id.in_([w.id for w in workspaces]))
        .group_by(Membership.workspace_id)
    )).all()) if workspaces else {}
    return [
        WorkspaceSummary(
            id=w.id, name=w.name, slug=w.slug,
            role=(Role.OWNER.value if reaches_all else
                  (mine[w.id].value if w.id in mine else None)),
            timezone=w.timezone, status=w.status.value,
            member_count=counts.get(w.id, 0),
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
        raise ValidationError(
            f"The slug '{slug}' is already in use.",
            code="ORG_SLUG_TAKEN", details={"slug": slug},
        )

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


async def _workspace_in_org(session, ctx, workspace_id: uuid.UUID) -> Workspace:
    """The workspace, or a refusal -- never another organisation's.

    The organisation scope is what makes these routes safe to address by id. A
    workspace id from somewhere else answers 404 rather than 403, because
    confirming it exists would make this a probe for other tenants.
    """
    workspace = await session.scalar(
        select(Workspace).where(
            Workspace.id == workspace_id,
            Workspace.organization_id == _org_id(ctx),
        )
    )
    if workspace is None:
        raise ValidationError("No such workspace.", code="WORKSPACE_NOT_FOUND")
    return workspace


@router.get(
    "/organization/workspaces/{workspace_id}/members",
    response_model=list[MemberView],
)
async def list_workspace_members(
    workspace_id: uuid.UUID, session: SessionDep, ctx: CtxDep
) -> list[MemberView]:
    """Who is in one workspace, asked from outside it.

    The same question `/workspace/members` answers, addressed by id instead of
    by whichever workspace the session happens to be using. Without it, putting
    somebody into a second workspace meant switching into that workspace first
    -- so an administrator with eight of them did eight round trips to answer
    "who has access to what", and there was no screen that could show the
    answer side by side.
    """
    ctx.require_org(Action.ADMIN)
    await _workspace_in_org(session, ctx, workspace_id)
    memberships = (await session.scalars(
        select(Membership).where(Membership.workspace_id == workspace_id)
    )).all()
    out: list[MemberView] = []
    for membership in memberships:
        user = await session.get(User, membership.user_id)
        if user is not None:
            out.append(member_service.view(membership, user))
    out.sort(key=lambda m: m.full_name.lower())
    return out


@router.post(
    "/organization/workspaces/{workspace_id}/members",
    response_model=MemberView, status_code=201,
)
async def add_workspace_member(
    workspace_id: uuid.UUID, payload: MemberInvite, session: SessionDep, ctx: CtxDep
) -> MemberView:
    """Give somebody a seat in a workspace without being in it yourself."""
    ctx.require_org(Action.ADMIN)
    await _workspace_in_org(session, ctx, workspace_id)
    membership, user = await member_service.invite(session, ctx, workspace_id, payload)
    await session.commit()
    return member_service.view(membership, user)


@router.patch(
    "/organization/workspaces/{workspace_id}/members/{member_id}",
    response_model=MemberView,
)
async def update_workspace_member(
    workspace_id: uuid.UUID, member_id: uuid.UUID, payload: MemberRoleUpdate,
    session: SessionDep, ctx: CtxDep,
) -> MemberView:
    ctx.require_org(Action.ADMIN)
    await _workspace_in_org(session, ctx, workspace_id)
    membership = await session.scalar(
        select(Membership).where(
            Membership.id == member_id, Membership.workspace_id == workspace_id
        )
    )
    if membership is None:
        raise ValidationError("No such member.", code="MEMBER_NOT_FOUND")
    membership, user = await member_service.update(
        session, ctx, workspace_id, membership, payload,
    )
    await session.commit()
    return member_service.view(membership, user)


@router.delete(
    "/organization/workspaces/{workspace_id}/members/{member_id}", status_code=204,
)
async def remove_workspace_member(
    workspace_id: uuid.UUID, member_id: uuid.UUID, session: SessionDep, ctx: CtxDep
) -> Response:
    ctx.require_org(Action.ADMIN)
    await _workspace_in_org(session, ctx, workspace_id)
    membership = await session.scalar(
        select(Membership).where(
            Membership.id == member_id, Membership.workspace_id == workspace_id
        )
    )
    if membership is None:
        return Response(status_code=204)
    await member_service.remove(session, ctx, workspace_id, membership)
    await session.commit()
    return Response(status_code=204)


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
            "An organization has to keep at least one workspace. Create "
            "another one first.",
            code="LAST_WORKSPACE",
        )

    blocking = await _blocking_contents(session, workspace.id)
    if blocking:
        raise ResourceInUseError(
            f"The workspace still holds {len(blocking)} resources. Delete "
            f"them first -- deleting each one also clears what it left on the "
            f"engine, and its credentials.",
            code="WORKSPACE_NOT_EMPTY",
            details={"count": len(blocking)},
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
        raise ValidationError(" ".join(problems), code="PASSWORD_REQUIREMENTS_UNMET")

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
        raise ValidationError(
            "That person is already a member of the organization.",
            code="ALREADY_ORG_MEMBER",
        )

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
        raise ValidationError(
            "No such organization member.", code="ORG_MEMBER_NOT_FOUND",
        )
    role = _parse_org_role(payload.role)

    # An ORG_ADMIN may not mint an owner, nor demote one: that is the boundary
    # between running the organisation and owning it.
    if not ctx.can_org(Action.DELETE) and OrgRole.ORG_OWNER in (role, membership.role):
        raise ForbiddenError(
            "Only an Org Owner can change the Org Owner role.",
            code="ORG_OWNER_ROLE_RESTRICTED",
        )
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
        raise ValidationError(
            "You cannot remove yourself from the organization.",
            code="CANNOT_REMOVE_SELF_ORG",
        )
    if membership.role is OrgRole.ORG_OWNER:
        if not ctx.can_org(Action.DELETE):
            raise ForbiddenError(
                "Only an Org Owner can remove another Org Owner.",
                code="ORG_OWNER_REMOVAL_RESTRICTED",
            )
        await _assert_not_last_org_owner(session, organization_id, membership.id)

    await audit.record(session, ctx, "organization.member.removed", resource_type="ORG_MEMBER",
                       resource_id=membership.user_id)
    await session.delete(membership)
    await session.commit()
    return Response(status_code=204)
