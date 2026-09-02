"""Publishing, verifying and activating releases.

A release freezes *a project revision*.  Not a set of product rows, not a
regenerated project -- the exact bytes.  That single change is what makes every
other promise in the rework keepable: a config AppBI has no form for still runs
in production, a restore restores the project rather than an approximation of
it, and a diff is a file diff.

Two properties are easy to get wrong and are enforced here rather than by
convention:

*Nothing runs unverified.*  Publishing froze the code; it did not prove the code
works.  A release is VERIFYING until an actual dbt invocation against its own
frozen revision has passed, and only READY can be activated.

*Late verification cannot win.*  If R1's verification finishes after R2 has gone
live, R1 must not activate over it.  A monotonic activation sequence decides,
rather than the order two asynchronous jobs happen to complete in.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import RequestContext
from app.core.db import utcnow
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.permissions import Action, Module
from app.models.enums import TriggerType
from app.services import audit
from app.transforms import (
    environments as environment_service, files as file_service,
    invocations as invocation_service,
)
from app.transforms.models import (
    TransformArtifactBundle, TransformEnvironment, TransformInvocation,
    TransformProject, TransformProjectRevision, TransformRelease,
    TransformResourceEdge, TransformResourceIndex,
)

#: The command a release is verified with.
#:
#: `build` rather than `compile`: compiling proves the graph and the Jinja, and
#: says nothing about whether the SQL the warehouse receives is valid or whether
#: the credential can write.  Those are exactly the failures a 03:00 schedule
#: should not be the first to discover.  It runs in the *verification* target,
#: not production, so proving a release does not publish its output.
VERIFY_COMMAND = "build"


async def get(
    session: AsyncSession, project: TransformProject, release_id: uuid.UUID,
) -> TransformRelease:
    row = await session.scalar(select(TransformRelease).where(
        TransformRelease.id == release_id,
        TransformRelease.project_id == project.id,
    ))
    if row is None:
        raise NotFoundError("That release was not found in this project.")
    return row


async def list_all(
    session: AsyncSession, project: TransformProject, *, limit: int = 50,
) -> list[TransformRelease]:
    return list((await session.scalars(
        select(TransformRelease)
        .where(TransformRelease.project_id == project.id)
        .order_by(TransformRelease.release_number.desc())
        .limit(limit)
    )).all())


async def active(
    session: AsyncSession, project: TransformProject,
) -> TransformRelease | None:
    if project.active_release_id is None:
        return None
    return await session.get(TransformRelease, project.active_release_id)


@dataclass(slots=True)
class PublishPlan:
    """What publishing would change, shown before it happens."""

    files: list[file_service.FileDiff]
    #: Resources whose own file changed.
    affected_resources: list[dict[str, Any]]
    #: Resources downstream of those, which will rebuild even though their own
    #: code did not change.  This is the number that surprises people.
    downstream_resources: list[dict[str, Any]]
    draft_hash: str
    live_hash: str | None
    matches_live: bool


async def plan(
    session: AsyncSession, project: TransformProject,
) -> PublishPlan:
    """Diff the working revision against what is live, by file and by resource.

    The resource half is what makes the dialog worth reading.  A one-line change
    to a staging model can rebuild forty marts, and a file list does not say so.
    """
    working = await file_service.working_revision(session, project)
    live = await active(session, project)
    live_revision = (
        await session.get(TransformProjectRevision, live.revision_id) if live else None
    )
    changes = file_service.diff_revisions(live_revision, working)
    changed_paths = {item.path for item in changes}

    affected: list[dict[str, Any]] = []
    downstream: list[dict[str, Any]] = []

    from app.transforms.indexer import latest_bundle

    bundle = await latest_bundle(
        session, project.id, scope="DRAFT", revision_id=working.id,
    )
    if bundle is not None and changed_paths:
        rows = list((await session.scalars(select(TransformResourceIndex).where(
            TransformResourceIndex.bundle_id == bundle.id,
        ))).all())
        by_id = {row.unique_id: row for row in rows}
        direct = {
            row.unique_id for row in rows
            if row.original_file_path in changed_paths
            or (row.patch_path or "").split("://")[-1] in changed_paths
        }
        affected = [_resource_ref(by_id[item]) for item in sorted(direct) if item in by_id]

        edges = list((await session.scalars(select(TransformResourceEdge).where(
            TransformResourceEdge.bundle_id == bundle.id,
        ))).all())
        children: dict[str, list[str]] = {}
        for edge in edges:
            children.setdefault(edge.parent_unique_id, []).append(edge.child_unique_id)

        # Transitive closure, breadth first, so a cycle in a malformed graph
        # cannot loop forever.
        seen = set(direct)
        frontier = list(direct)
        while frontier:
            current = frontier.pop()
            for child in children.get(current, []):
                if child in seen:
                    continue
                seen.add(child)
                frontier.append(child)
        downstream = [
            _resource_ref(by_id[item]) for item in sorted(seen - direct) if item in by_id
        ]

    return PublishPlan(
        files=changes,
        affected_resources=affected,
        downstream_resources=downstream,
        draft_hash=working.content_hash,
        live_hash=live.project_hash if live else None,
        matches_live=bool(live and live.project_hash == working.content_hash),
    )


def _resource_ref(row: TransformResourceIndex) -> dict[str, Any]:
    return {
        "unique_id": row.unique_id,
        "name": row.name,
        "resource_type": row.resource_type,
        "path": row.original_file_path,
        "materialized": row.materialized,
    }


async def publish(
    session: AsyncSession,
    ctx: RequestContext,
    project: TransformProject,
    *,
    notes: str | None = None,
    activate: bool = True,
    environment_id: uuid.UUID | None = None,
) -> tuple[TransformRelease, TransformInvocation]:
    """Freeze the working revision and queue its verification.

    Returns the release and the invocation that will decide its fate.  The
    release is not live yet and must not be: whether it becomes live is what
    that invocation determines.
    """
    ctx.require(Module.TRANSFORMS, Action.OPERATE)

    working = await file_service.working_revision(session, project)
    if not working.manifest_index:
        raise ValidationError(
            "There is nothing to publish -- this project has no files.",
            code="TRANSFORM_PUBLISH_EMPTY",
        )

    live = await active(session, project)
    if live is not None and live.project_hash == working.content_hash:
        raise ValidationError(
            "This version is already live. Nothing has changed since the last "
            "release.",
            code="TRANSFORM_PUBLISH_UNCHANGED",
        )

    # Serialise publishing per project.  Two people publishing at once would
    # otherwise produce two releases racing for the same activation slot.
    pending = await session.scalar(select(TransformRelease).where(
        TransformRelease.project_id == project.id,
        TransformRelease.status == "VERIFYING",
    ).limit(1))
    if pending is not None:
        raise ConflictError(
            f"Release {pending.release_number} is still being checked. "
            "Wait for it to finish before publishing again.",
            code="TRANSFORM_PUBLISH_IN_FLIGHT",
            details={"release_id": str(pending.id)},
        )

    await file_service.freeze(session, working)

    number = (await session.scalar(
        select(func.max(TransformRelease.release_number))
        .where(TransformRelease.project_id == project.id)
    ) or 0) + 1
    sequence = (await session.scalar(
        select(func.max(TransformRelease.activation_sequence))
        .where(TransformRelease.project_id == project.id)
    ) or 0) + 1

    verification_environment = await environment_service.resolve(
        session, project, environment_id, default_to=environment_service.DEVELOPMENT,
    )

    release = TransformRelease(
        id=uuid.uuid4(),
        project_id=project.id,
        release_number=number,
        revision_id=working.id,
        project_hash=working.content_hash,
        git_commit_sha=working.git_commit_sha,
        environment_id=project.production_environment_id,
        dbt_version=project.dbt_core_version,
        adapter_version=project.dbt_adapter_version,
        status="VERIFYING",
        activate_on_success=activate,
        activation_sequence=sequence,
        notes=(notes or "").strip() or None,
        created_by=ctx.user_id,
    )
    session.add(release)
    await session.flush()

    invocation = await invocation_service.enqueue(
        session, ctx, project,
        command=VERIFY_COMMAND,
        environment=verification_environment,
        revision=working,
        release=release,
        trigger_type=TriggerType.MANUAL,
        # The publisher has OPERATE, which was checked above.  Re-checking
        # against the verification environment would reject a publish by
        # somebody allowed to publish but not to run production by hand.
        enforce_permission=False,
    )
    release.verification_invocation_id = invocation.id

    # A new working revision, so a save a minute from now cannot alter what is
    # being verified.  The editor continues from an identical file set.
    from app.transforms.files import replace_all
    from app.transforms.storage import object_store

    store = object_store()
    contents = await file_service.read_all(working, store=store)
    await replace_all(
        session, project, files=contents, actor_id=ctx.user_id, store=store,
        parent=working,
    )

    await audit.record(
        session, ctx, "transform.release.published", resource_type="TRANSFORM",
        resource_id=project.id, resource_name=project.name,
        after={
            "release_number": number, "revision": working.revision_number,
            "project_hash": working.content_hash, "activate": activate,
        },
    )
    return release, invocation


async def settle(
    session: AsyncSession, invocation: TransformInvocation,
) -> TransformRelease | None:
    """Record a verification's verdict, and activate if it earned it.

    Called by the worker when a verification invocation reaches a terminal
    state.  This is the only place a release becomes live without somebody
    pressing a button, and the sequence check is what keeps that safe.
    """
    if invocation.release_id is None:
        return None
    release = await session.get(TransformRelease, invocation.release_id)
    if release is None or release.status != "VERIFYING":
        return release
    if release.verification_invocation_id != invocation.id:
        # A later verification of the same release; the first one's verdict is
        # not this invocation's to write.
        return release

    from app.models.enums import RunStatus

    if invocation.status != RunStatus.SUCCEEDED:
        release.status = "FAILED"
        release.verification_error = (
            invocation.error_summary or "The published version did not build."
        )
        return release

    release.status = "READY"
    release.verified_at = utcnow()
    release.verification_error = None

    if not release.activate_on_success:
        return release

    project = await session.get(TransformProject, release.project_id)
    if project is None:
        return release
    await _activate(session, project, release, reason="verification")
    return release


async def activate(
    session: AsyncSession,
    ctx: RequestContext,
    project: TransformProject,
    release_id: uuid.UUID,
) -> TransformRelease:
    ctx.require(Module.TRANSFORMS, Action.OPERATE)
    release = await get(session, project, release_id)
    if release.status not in ("READY", "RETIRED"):
        raise ValidationError(
            "Only a version that has been checked successfully can be made live."
            + (
                f" Release {release.release_number} did not build: "
                f"{release.verification_error}"
                if release.status == "FAILED" and release.verification_error else ""
            ),
            code="TRANSFORM_RELEASE_NOT_READY",
            details={"status": release.status},
        )
    await _activate(session, project, release, reason="manual")
    await audit.record(
        session, ctx, "transform.release.activated", resource_type="TRANSFORM",
        resource_id=project.id, resource_name=project.name,
        after={"release_number": release.release_number},
    )
    return release


async def _activate(
    session: AsyncSession,
    project: TransformProject,
    release: TransformRelease,
    *,
    reason: str,
) -> None:
    """Make a release live, unless something newer already is.

    The guard is the reason this is a function and not two assignments.  A
    verification that completes late must not overwrite a newer live release,
    and comparing activation sequences is what decides -- not the order the jobs
    finished in, which is not something either job can observe.
    """
    current = await active(session, project)
    if (
        current is not None
        and current.id != release.id
        and current.activation_sequence > release.activation_sequence
    ):
        release.status = "RETIRED"
        release.verification_error = (
            f"Release {current.release_number} went live while this one was "
            "being checked, so this version was not activated."
        )
        return

    if current is not None and current.id != release.id:
        current.status = "RETIRED"

    release.status = "ACTIVE"
    release.activated_at = utcnow()
    project.active_release_id = release.id


async def restore(
    session: AsyncSession,
    ctx: RequestContext,
    project: TransformProject,
    release_id: uuid.UUID,
) -> TransformProjectRevision:
    """Copy a release's files back into the working revision.

    A file-level restore, so a config the product does not understand comes back
    exactly as it was published.  V1 reconstructed models from rows, which could
    only restore what it had modelled.

    The release itself is untouched -- restoring puts the code in the editor, it
    does not make it live.
    """
    ctx.require(Module.TRANSFORMS, Action.EDIT)
    release = await get(session, project, release_id)
    revision = await session.get(TransformProjectRevision, release.revision_id)
    if revision is None:
        raise ValidationError(
            "The files for that release are no longer available.",
            code="TRANSFORM_RELEASE_REVISION_MISSING",
        )

    from app.transforms.storage import object_store

    store = object_store()
    contents = await file_service.read_all(revision, store=store)
    restored = await file_service.replace_all(
        session, project, files=contents, actor_id=ctx.user_id, store=store,
        git_commit_sha=revision.git_commit_sha, git_branch=revision.git_branch,
    )
    await audit.record(
        session, ctx, "transform.release.restored", resource_type="TRANSFORM",
        resource_id=project.id, resource_name=project.name,
        after={"release_number": release.release_number,
               "revision": restored.revision_number},
    )
    return restored


async def view(
    session: AsyncSession, project: TransformProject, release: TransformRelease,
) -> dict[str, Any]:
    revision = await session.get(TransformProjectRevision, release.revision_id)
    environment = (
        await session.get(TransformEnvironment, release.environment_id)
        if release.environment_id else None
    )
    return {
        "id": release.id,
        "release_number": release.release_number,
        "status": release.status,
        "is_active": project.active_release_id == release.id,
        "revision_number": revision.revision_number if revision else None,
        "project_hash": release.project_hash,
        "file_count": revision.file_count if revision else 0,
        "git_commit_sha": release.git_commit_sha,
        "environment_name": environment.name if environment else None,
        "dbt_version": release.dbt_version,
        "verification_invocation_id": release.verification_invocation_id,
        "verification_error": release.verification_error,
        "verified_at": release.verified_at,
        "activated_at": release.activated_at,
        "notes": release.notes,
        "created_at": release.created_at,
        "created_by": release.created_by,
    }


async def release_bundle(
    session: AsyncSession, release: TransformRelease,
) -> TransformArtifactBundle | None:
    """The artifacts produced when this release was verified.

    Production lineage and the production resource tree come from here, not
    from a draft parse -- which is what keeps the graph somebody is editing
    separate from the graph production is running.
    """
    if release.verification_invocation_id is None:
        return None
    return await session.scalar(select(TransformArtifactBundle).where(
        TransformArtifactBundle.invocation_id == release.verification_invocation_id,
        TransformArtifactBundle.manifest_storage_key.is_not(None),
    ).order_by(TransformArtifactBundle.created_at.desc()).limit(1))
