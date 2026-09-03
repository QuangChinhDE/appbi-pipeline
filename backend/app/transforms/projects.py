"""Project lifecycle: create, present, schedule, delete.

The thin layer that ties the others together.  It deliberately holds no dbt
knowledge -- it does not know what a model is -- and no file knowledge beyond
handing a starter file set to the file service once, at creation.

Compare with V1's ``services/transforms.py``, which was 2,500 lines because it
owned the domain, the generator, the runner, the indexer, the importer and the
release logic at once.  Each of those is now a module with one job, and this one
is the only place that has to know they exist.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import RequestContext
from app.core.db import utcnow
from app.core.errors import NotFoundError, ValidationError
from app.core.permissions import Action, Module
from app.models.enums import HealthLevel, ScheduleType
from app.services import audit, scheduling
from app.transforms.compatibility import capability, lock
from app.transforms import (
    connections as connection_service, environments as environment_service,
    files as file_service, releases as release_service, scaffold,
)
from app.transforms.models import (
    TransformArtifactBundle, TransformConnection, TransformEnvironment,
    TransformInvocation, TransformProject, TransformProjectRevision, TransformRelease,
)
from app.transforms.runtime.commands import SCHEDULABLE, validate_command
from app.transforms.storage import object_store

MANAGED = "MANAGED"
GIT = "GIT"


async def get(
    session: AsyncSession, ctx: RequestContext, project_id: uuid.UUID,
) -> TransformProject:
    project = await session.scalar(select(TransformProject).where(
        TransformProject.id == project_id,
        TransformProject.workspace_id == ctx.workspace_id,
        TransformProject.deleted_at.is_(None),
    ).execution_options(populate_existing=True))
    if project is None:
        raise NotFoundError("That project was not found in this workspace.")
    return project


async def list_projects(
    session: AsyncSession,
    ctx: RequestContext,
    *,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[TransformProject], int]:
    ctx.require(Module.TRANSFORMS, Action.VIEW)
    base = select(TransformProject).where(
        TransformProject.workspace_id == ctx.workspace_id,
        TransformProject.deleted_at.is_(None),
    )
    if search:
        pattern = f"%{search.strip().lower()}%"
        base = base.where(or_(
            func.lower(TransformProject.name).like(pattern),
            func.lower(TransformProject.description).like(pattern),
        ))
    total = await session.scalar(
        select(func.count()).select_from(base.subquery())
    ) or 0
    rows = list((await session.scalars(
        base.order_by(TransformProject.updated_at.desc()).limit(limit).offset(offset)
    )).all())
    return rows, int(total)


async def create(
    session: AsyncSession,
    ctx: RequestContext,
    *,
    name: str,
    description: str | None,
    connection_id: uuid.UUID,
    mode: str = MANAGED,
    dbt_project_name: str | None = None,
    development_schema: str | None = None,
    production_schema: str | None = None,
    source_schema: str = "raw",
    per_user_schemas: bool = False,
    with_examples: bool = True,
    files: dict[str, bytes] | None = None,
    git_commit_sha: str | None = None,
    git_branch: str | None = None,
) -> TransformProject:
    """Create a project and its first revision.

    ``files`` is supplied when the project comes from Git or an upload -- those
    arrive as a complete dbt project and are stored exactly as they are.  Left
    empty, a managed project is scaffolded from :mod:`app.transforms.scaffold`.

    That is the whole of the "import" story now.  There is no conversion step,
    because there is nothing to convert into.
    """
    ctx.require(Module.TRANSFORMS, Action.CREATE)

    label = (name or "").strip()
    if not label:
        raise ValidationError("Give this project a name.", code="TRANSFORM_NO_NAME")
    clash = await session.scalar(select(TransformProject).where(
        TransformProject.workspace_id == ctx.workspace_id,
        TransformProject.name == label,
        TransformProject.deleted_at.is_(None),
    ))
    if clash is not None:
        raise ValidationError(
            f"There is already a project called `{label}`.",
            code="TRANSFORM_NAME_TAKEN",
        )

    connection = await connection_service.get(session, ctx.workspace_id, connection_id)
    connector_key = connection_service.connector_key(connection)
    cap = capability(connector_key)
    if not cap or cap.get("certification") != "SUPPORTED":
        raise ValidationError(
            "Transform cannot run on this kind of warehouse yet.",
            code="TRANSFORM_SYSTEM_UNSUPPORTED",
        )

    project = TransformProject(
        id=uuid.uuid4(),
        workspace_id=ctx.workspace_id,
        name=label,
        description=(description or "").strip() or None,
        mode=mode,
        status="ACTIVE",
        health_status=HealthLevel.UNKNOWN,
        parse_status="PENDING",
        dbt_core_version=lock()["dbt_core"],
        dbt_adapter_name=str(cap.get("package") or ""),
        dbt_adapter_version=str(cap.get("version") or ""),
        schedule_type=ScheduleType.MANUAL,
        schedule_config={},
        schedule_command={"command": "build"},
        timezone=ctx.timezone,
        created_by=ctx.user_id,
        updated_by=ctx.user_id,
    )
    session.add(project)
    await session.flush()

    if files is None:
        project_name = scaffold.validate_project_name(
            dbt_project_name or _default_project_name(label),
        )
        files = scaffold.starter_files(
            project_name=project_name,
            display_name=label,
            source_schema=source_schema,
            with_examples=with_examples,
        )
        project.dbt_project_name = project_name

    revision = await file_service.replace_all(
        session, project, files=files, actor_id=ctx.user_id,
        store=object_store(), git_commit_sha=git_commit_sha, git_branch=git_branch,
    )

    # A Git or uploaded project names its own dbt project and profile; read them
    # rather than assume, because the runtime has to write a profile that project
    # will accept.
    facts = await file_service.project_facts(revision)
    if facts.name:
        project.dbt_project_name = facts.name

    default_schema = _default_schema(label)
    await environment_service.create_defaults(
        session, project,
        connection_id=connection.id,
        development_schema=development_schema or f"{default_schema}_dev",
        production_schema=production_schema or default_schema,
        per_user_schemas=per_user_schemas,
    )

    await audit.record(
        session, ctx, "transform.project.created", resource_type="TRANSFORM",
        resource_id=project.id, resource_name=label,
        after={"mode": mode, "files": len(files), "connection": connection.name},
    )
    return project


def _slugify(label: str) -> str:
    """A project label reduced to something dbt and a warehouse will accept.

    Accents are folded to their base letter before non-alphanumerics are
    replaced, because this product's users name things in Vietnamese: dropping
    the accented characters instead turns "Phân tích bán hàng" into
    `ph_n_t_ch_b_n_h_ng`, which is what a person then sees as their schema
    name and in `dbt_project.yml`.  `đ`/`Đ` decompose to nothing under NFKD, so
    they are mapped by hand.

    dbt requires the project name to be a valid identifier, so a leading digit
    is prefixed rather than dropped -- "2024 Revenue" is `p_2024_revenue`, not
    `_2024_revenue`.
    """
    import re
    import unicodedata

    folded = label.replace("đ", "d").replace("Đ", "D")
    folded = unicodedata.normalize("NFKD", folded)
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    slug = re.sub(r"[^A-Za-z0-9]+", "_", folded).strip("_").lower()
    slug = re.sub(r"_+", "_", slug)
    if slug and slug[0].isdigit():
        slug = f"p_{slug}"
    return slug


def _default_project_name(label: str) -> str:
    return _slugify(label) or "appbi_project"


def _default_schema(label: str) -> str:
    return (_slugify(label) or "analytics")[:50]


async def update(
    session: AsyncSession,
    ctx: RequestContext,
    project: TransformProject,
    *,
    name: str | None = None,
    description: str | None = None,
    schedule_type: str | None = None,
    schedule_config: dict[str, Any] | None = None,
    schedule_command: dict[str, Any] | None = None,
    timezone: str | None = None,
) -> TransformProject:
    ctx.require(Module.TRANSFORMS, Action.EDIT)

    if name is not None:
        label = name.strip()
        if not label:
            raise ValidationError("Give this project a name.", code="TRANSFORM_NO_NAME")
        if label != project.name:
            clash = await session.scalar(select(TransformProject).where(
                TransformProject.workspace_id == ctx.workspace_id,
                TransformProject.name == label,
                TransformProject.id != project.id,
                TransformProject.deleted_at.is_(None),
            ))
            if clash is not None:
                raise ValidationError(
                    f"There is already a project called `{label}`.",
                    code="TRANSFORM_NAME_TAKEN",
                )
            project.name = label
    if description is not None:
        project.description = description.strip() or None
    if timezone is not None:
        project.timezone = timezone

    if schedule_command is not None:
        command = str(schedule_command.get("command") or "build")
        if command not in SCHEDULABLE:
            raise ValidationError(
                f"`dbt {command}` cannot be put on a schedule.",
                code="TRANSFORM_COMMAND_NOT_SCHEDULABLE",
                details={"schedulable": sorted(SCHEDULABLE)},
            )
        # Validated now rather than at 03:00: a selector that dbt would reject
        # should fail while the person who typed it is still on the page.
        validate_command(
            command,
            selector=schedule_command.get("selector"),
            exclude=schedule_command.get("exclude"),
            full_refresh=bool(schedule_command.get("full_refresh")),
        )
        project.schedule_command = {
            "command": command,
            "selector": schedule_command.get("selector") or None,
            "exclude": schedule_command.get("exclude") or None,
            "full_refresh": bool(schedule_command.get("full_refresh")),
        }

    if schedule_type is not None or schedule_config is not None:
        project.schedule_type = ScheduleType(
            (schedule_type or project.schedule_type.value).upper()
        )
        if schedule_config is not None:
            project.schedule_config = schedule_config
        project.next_run_at = scheduling.next_run_at(
            project.schedule_type, project.schedule_config, project.timezone,
        )
        if project.schedule_type != ScheduleType.MANUAL and project.active_release_id is None:
            # Deliberately a warning rather than a rejection: somebody may set
            # the schedule before publishing.  What must not happen is a
            # schedule quietly running the draft, which it cannot.
            project.health_message = (
                "This project is scheduled but nothing has been published, so "
                "there is nothing for it to run."
            )

    project.updated_by = ctx.user_id
    project.version += 1
    await audit.record(
        session, ctx, "transform.project.updated", resource_type="TRANSFORM",
        resource_id=project.id, resource_name=project.name,
    )
    return project


async def remove(
    session: AsyncSession, ctx: RequestContext, project: TransformProject,
) -> None:
    """Retire a project.

    Soft delete.  Its invocations are the audit record of what ran against a
    warehouse, and those should survive somebody tidying up a project list.
    """
    ctx.require(Module.TRANSFORMS, Action.DELETE)
    project.deleted_at = utcnow()
    project.status = "DELETED"
    project.next_run_at = None
    await audit.record(
        session, ctx, "transform.project.deleted", resource_type="TRANSFORM",
        resource_id=project.id, resource_name=project.name,
    )


# ── presentation ──────────────────────────────────────────────────────────


async def present(
    session: AsyncSession, ctx: RequestContext, project: TransformProject,
) -> dict[str, Any]:
    """A list row: what the project is, and whether it is well."""
    warehouse = None
    environment = None
    if project.default_environment_id is not None:
        environment = await session.get(TransformEnvironment, project.default_environment_id)
        if environment is not None and environment.connection_id is not None:
            connection = await session.get(TransformConnection, environment.connection_id)
            if connection is not None:
                warehouse = await connection_service.connector_display(session, connection)

    release = await release_service.active(session, project)
    revision = (
        await session.get(TransformProjectRevision, project.working_revision_id)
        if project.working_revision_id else None
    )
    last = (
        await session.get(TransformInvocation, project.last_invocation_id)
        if project.last_invocation_id else None
    )
    git = project.git_binding

    return {
        "id": project.id,
        "name": project.name,
        "description": project.description,
        "mode": project.mode,
        "status": project.status,
        "dbt_project_name": project.dbt_project_name,
        "warehouse": warehouse,
        "environment_name": environment.name if environment else None,
        "health_status": project.health_status.value,
        "health_message": project.health_message,
        "parse_status": project.parse_status,
        "parse_error": project.parse_error,
        "last_parsed_at": project.last_parsed_at,
        "revision_number": revision.revision_number if revision else None,
        "file_count": revision.file_count if revision else 0,
        "has_unpublished_changes": _has_unpublished(revision, release),
        "active_release": (
            {
                "id": release.id,
                "release_number": release.release_number,
                "activated_at": release.activated_at,
            } if release else None
        ),
        "last_invocation": (
            {
                "id": last.id,
                "command": last.command,
                "selector": last.selector,
                "status": last.status.value,
                "ended_at": last.ended_at,
            } if last else None
        ),
        "last_success_at": project.last_success_at,
        "git": (
            {
                "branch": git.branch,
                "repo_url": git.repo_url,
                "head_commit_sha": git.head_commit_sha,
                "behind": bool(
                    git.remote_commit_sha
                    and git.remote_commit_sha != git.head_commit_sha
                ),
                "last_status": git.last_status,
            } if git else None
        ),
        "schedule_type": project.schedule_type.value,
        "next_run_at": project.next_run_at,
        "updated_at": project.updated_at,
    }


def _has_unpublished(
    revision: TransformProjectRevision | None, release: TransformRelease | None,
) -> bool:
    """Whether the editor holds anything production is not running.

    Compared by content hash rather than by timestamp: saving a file with no
    change should not make a project look like it has pending work, and
    publishing then editing back to the published state should clear the flag.
    """
    if revision is None:
        return False
    if release is None:
        return True
    return revision.content_hash != release.project_hash


async def detail(
    session: AsyncSession, ctx: RequestContext, project: TransformProject,
) -> dict[str, Any]:
    """Everything the workbench header needs on open."""
    row = await present(session, ctx, project)

    environments = []
    for environment in await environment_service.list_all(session, project):
        connection = (
            await session.get(TransformConnection, environment.connection_id)
            if environment.connection_id else None
        )
        environments.append(environment_service.view(
            environment,
            connection=(
                await connection_service.connector_display(session, connection)
                if connection else None
            ),
            user_id=ctx.user_id,
        ))

    revision = (
        await session.get(TransformProjectRevision, project.working_revision_id)
        if project.working_revision_id else None
    )
    facts = await file_service.project_facts(revision) if revision else None

    from app.transforms.indexer import latest_bundle, resource_counts

    bundle = await latest_bundle(session, project.id, scope="DRAFT")
    counts = await resource_counts(session, bundle.id) if bundle else {}

    return {
        **row,
        "environments": environments,
        "default_environment_id": project.default_environment_id,
        "production_environment_id": project.production_environment_id,
        "working_revision": (
            {
                "id": revision.id,
                "revision_number": revision.revision_number,
                "content_hash": revision.content_hash,
                "file_count": revision.file_count,
                "created_at": revision.created_at,
            } if revision else None
        ),
        "dbt_profile_name": facts.profile if facts else None,
        "project_file_valid": facts.valid if facts else False,
        "project_file_error": facts.error if facts else None,
        "resource_counts": counts,
        "resource_bundle_id": bundle.id if bundle else None,
        "engine": {
            "dbt_core_version": project.dbt_core_version,
            "adapter": project.dbt_adapter_name,
            "adapter_version": project.dbt_adapter_version,
        },
        "permissions": {
            "can_edit": ctx.can(Module.TRANSFORMS, Action.EDIT),
            "can_operate": ctx.can(Module.TRANSFORMS, Action.OPERATE),
            "can_delete": ctx.can(Module.TRANSFORMS, Action.DELETE),
        },
    }


async def record_parse(
    session: AsyncSession,
    project: TransformProject,
    *,
    revision_id: uuid.UUID,
    succeeded: bool,
    error: str | None,
    bundle: TransformArtifactBundle | None,
    project_name: str | None = None,
) -> None:
    """Store the verdict of a parse against the working revision.

    Only advances ``parsed_revision_id`` on success, so a failed parse leaves
    the last good resource tree in place rather than emptying the explorer while
    somebody is halfway through fixing a YAML file.
    """
    project.parse_status = "OK" if succeeded else "ERROR"
    project.parse_error = None if succeeded else (error or "")[:4000]
    project.last_parsed_at = utcnow()
    if succeeded:
        project.parsed_revision_id = revision_id
        if project_name:
            project.dbt_project_name = project_name
    await session.flush()
