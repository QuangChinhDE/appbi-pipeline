"""Queue, claim, execute and record dbt invocations.

The API only ever writes a QUEUED row; a dedicated worker claims it and runs one
isolated subprocess.  That split is inherited from V1 and kept deliberately: a
`dbt build` takes minutes, and an HTTP request is the wrong thing to hold open
for it.

What changed is what a row means.  V1's run named a product operation
(`RUN_MODEL`) and a `TransformModel` row.  This one names a dbt command, a
selector, an environment and a revision -- so every dbt command is expressible,
and every run can say exactly which bytes it executed.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import func, select, text as sa_text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.context import RequestContext
from app.core.db import utcnow
from app.core.errors import (
    ConflictError, ErrorCategory, NotFoundError, ValidationError,
)
from app.core.permissions import Action, Module
from app.models.enums import ACTIVE_RUN_STATUSES, HealthLevel, RunStatus, TriggerType
from app.services import audit
from app.transforms import environments as environment_service
from app.transforms.models import (
    TransformEnvironment, TransformInvocation, TransformProject,
    TransformProjectRevision, TransformRelease,
)
from app.transforms.runtime.commands import (
    COMMANDS, PRODUCTION_ALLOWED, DbtCommand, validate_command,
)

logger = logging.getLogger(__name__)

#: One namespace for the claim lock, distinct from Pipeline's.
_CLAIM_LOCK_KEY = 0x7B_5F_00_02

#: Commands whose outcome moves the project's health badge.
#:
#: A failed `parse` while somebody is mid-edit is not an outage; a failed
#: production `build` is. Only the second should turn a project red on the list
#: page, or the badge stops meaning anything.
HEALTH_BEARING = frozenset({"build", "run", "test", "seed", "snapshot"})


async def get(
    session: AsyncSession, ctx: RequestContext, invocation_id: uuid.UUID,
) -> TransformInvocation:
    row = await session.scalar(select(TransformInvocation).where(
        TransformInvocation.id == invocation_id,
        TransformInvocation.workspace_id == ctx.workspace_id,
    ))
    if row is None:
        raise NotFoundError("That run was not found in this workspace.")
    return row


async def enqueue(
    session: AsyncSession,
    ctx: RequestContext,
    project: TransformProject,
    *,
    command: str,
    environment: TransformEnvironment,
    revision: TransformProjectRevision,
    selector: str | None = None,
    exclude: str | None = None,
    full_refresh: bool = False,
    limit: int | None = None,
    macro: str | None = None,
    macro_args: dict[str, Any] | None = None,
    selector_name: str | None = None,
    vars: dict[str, Any] | None = None,
    release: TransformRelease | None = None,
    trigger_type: TriggerType = TriggerType.MANUAL,
    idempotency_key: str | None = None,
    retry_of: uuid.UUID | None = None,
    enforce_permission: bool = True,
) -> TransformInvocation:
    """Queue one dbt command.

    The command is validated into a :class:`DbtCommand` here rather than in the
    worker, so a request that could never run is rejected while somebody is
    still looking at the screen.
    """
    validated = validate_command(
        command,
        selector=selector,
        exclude=exclude,
        full_refresh=full_refresh,
        limit=limit,
        macro=macro,
        macro_args=macro_args,
        selector_name=selector_name,
        vars={**(environment.vars_json or {}), **(vars or {})},
    )

    if enforce_permission:
        _authorise(ctx, environment, validated)

    if environment.protected and validated.command not in PRODUCTION_ALLOWED:
        raise ValidationError(
            f"`dbt {validated.spec.name}` cannot be run against production from here.",
            code="TRANSFORM_COMMAND_NOT_ALLOWED_IN_PRODUCTION",
        )

    if idempotency_key:
        if len(idempotency_key) > 120:
            raise ValidationError(
                "Idempotency-Key must not exceed 120 characters.",
                code="IDEMPOTENCY_KEY_TOO_LONG",
            )
        existing = await session.scalar(select(TransformInvocation).where(
            TransformInvocation.workspace_id == ctx.workspace_id,
            TransformInvocation.idempotency_key == idempotency_key,
        ))
        if existing is not None:
            return existing

    if validated.writes:
        await _assert_no_active_write(session, project.id, environment.id)

    invocation = TransformInvocation(
        id=uuid.uuid4(),
        workspace_id=ctx.workspace_id,
        project_id=project.id,
        environment_id=environment.id,
        revision_id=revision.id,
        release_id=release.id if release is not None else None,
        command=validated.command,
        selector=validated.selector,
        exclude=validated.exclude,
        args_json={
            "full_refresh": validated.full_refresh,
            "limit": validated.limit,
            "macro": validated.macro,
            "macro_args": validated.macro_args,
            "selector_name": validated.selector_name,
            "vars": validated.vars,
        },
        trigger_type=trigger_type,
        triggered_by=ctx.user_id,
        retry_of_invocation_id=retry_of,
        idempotency_key=idempotency_key,
        status=RunStatus.QUEUED,
        technical_metadata={"trace_id": ctx.trace_id},
    )

    try:
        async with session.begin_nested():
            session.add(invocation)
            await session.flush()
    except IntegrityError as exc:
        constraint = _violated_constraint(exc)
        if constraint == "uq_transform_invocation_idempotency" and idempotency_key:
            duplicate = await session.scalar(select(TransformInvocation).where(
                TransformInvocation.workspace_id == ctx.workspace_id,
                TransformInvocation.idempotency_key == idempotency_key,
            ))
            if duplicate is not None:
                return duplicate
        if constraint == "uq_transform_active_write":
            await _assert_no_active_write(
                session, project.id, environment.id, raise_always=True,
            )
        raise

    await audit.record(
        session, ctx, "transform.invocation.triggered",
        resource_type="TRANSFORM_RUN", resource_id=invocation.id,
        resource_name=project.name,
        after={
            "command": validated.command,
            "selector": validated.selector,
            "environment": environment.name,
            "revision": revision.revision_number,
            "release_id": str(release.id) if release else None,
        },
    )
    return invocation


def _authorise(
    ctx: RequestContext, environment: TransformEnvironment, command: DbtCommand,
) -> None:
    """Which permission this command needs.

    Reads need VIEW: the editor parses and compiles constantly, and requiring
    OPERATE for that would make a read-only role unable to open a project.
    Writes need EDIT in development and OPERATE in production.  A privileged
    command -- one that can execute arbitrary maintenance SQL -- needs OPERATE
    wherever it runs.
    """
    if command.privileged:
        ctx.require(Module.TRANSFORMS, Action.OPERATE)
        return
    if not command.writes:
        ctx.require(Module.TRANSFORMS, Action.VIEW)
        return
    environment_service.require_operate(ctx, environment)


async def _assert_no_active_write(
    session: AsyncSession,
    project_id: uuid.UUID,
    environment_id: uuid.UUID,
    *,
    raise_always: bool = False,
) -> None:
    active = await session.scalar(select(TransformInvocation).where(
        TransformInvocation.project_id == project_id,
        TransformInvocation.environment_id == environment_id,
        TransformInvocation.status.in_(list(ACTIVE_RUN_STATUSES)),
        TransformInvocation.command.in_(
            [name for name, spec in COMMANDS.items() if spec.writes]
        ),
    ).limit(1))
    if active is not None or raise_always:
        raise ConflictError(
            "Something is already building in this environment. Wait for it to "
            "finish, or cancel it.",
            code="TRANSFORM_ALREADY_RUNNING",
            details={"invocation_id": str(active.id) if active else None},
        )


def _violated_constraint(error: IntegrityError) -> str:
    original = getattr(error, "orig", None)
    name = getattr(original, "constraint_name", None)
    if not name:
        name = getattr(getattr(original, "diag", None), "constraint_name", None)
    if name:
        return str(name)
    message = str(error)
    for candidate in ("uq_transform_invocation_idempotency", "uq_transform_active_write"):
        if candidate in message:
            return candidate
    return ""


# ── worker lifecycle ──────────────────────────────────────────────────────


async def claim_next(session: AsyncSession, worker_id: str) -> TransformInvocation | None:
    """Take one queued invocation, respecting the deployment-wide parallelism cap.

    The cap is counted and then consumed, which is only safe if no other worker
    can count between those two steps.  `SKIP LOCKED` protects the row but not
    the count -- two workers each reading "one running, cap is two" both claim,
    and the deployment runs three.  A transaction-scoped advisory lock closes
    that window; it is held for the microseconds of claiming, not for the run,
    so workers still execute in parallel.
    """
    await session.execute(
        sa_text("SELECT pg_advisory_xact_lock(:key)"), {"key": _CLAIM_LOCK_KEY},
    )
    running = await session.scalar(
        select(func.count()).select_from(TransformInvocation).where(
            TransformInvocation.status.in_([RunStatus.STARTING, RunStatus.RUNNING]),
        )
    ) or 0
    if running >= settings.transform_worker_max_parallel:
        await session.rollback()
        return None

    invocation = await session.scalar(
        select(TransformInvocation)
        .where(TransformInvocation.status == RunStatus.QUEUED)
        # Interactive work first. A person waiting on a Preview should not queue
        # behind a nightly full refresh; both are fair, but one has somebody
        # watching a spinner.
        .order_by(
            TransformInvocation.command.in_(["show", "compile", "parse", "ls"]).desc(),
            TransformInvocation.created_at,
        )
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if invocation is None:
        await session.rollback()
        return None

    invocation.status = RunStatus.STARTING
    invocation.claimed_by = worker_id
    invocation.started_at = utcnow()
    invocation.heartbeat_at = utcnow()
    invocation.attempt_count += 1
    await session.commit()
    return invocation


async def get_claimed(
    session: AsyncSession, invocation_id: uuid.UUID,
) -> TransformInvocation:
    invocation = await session.get(TransformInvocation, invocation_id)
    if invocation is None:
        raise NotFoundError("That run no longer exists.")
    return invocation


async def mark_running(session: AsyncSession, invocation_id: uuid.UUID) -> None:
    invocation = await session.get(TransformInvocation, invocation_id)
    if invocation is None:
        return
    if invocation.status == RunStatus.STARTING:
        invocation.status = RunStatus.RUNNING
    invocation.heartbeat_at = utcnow()
    await session.commit()


async def heartbeat(session: AsyncSession, invocation_id: uuid.UUID) -> bool:
    """Record liveness and report whether a cancel was requested.

    One statement doing both, because the worker calls it every half second and
    two round trips per tick is two round trips too many.
    """
    invocation = await session.get(TransformInvocation, invocation_id)
    if invocation is None:
        return True
    invocation.heartbeat_at = utcnow()
    await session.commit()
    return invocation.status == RunStatus.CANCEL_REQUESTED


async def request_cancel(
    session: AsyncSession, ctx: RequestContext, invocation: TransformInvocation,
) -> None:
    ctx.require(Module.TRANSFORMS, Action.OPERATE)
    if not invocation.status.is_active:
        raise ValidationError(
            "That run has already finished.", code="TRANSFORM_RUN_NOT_ACTIVE",
        )
    invocation.status = RunStatus.CANCEL_REQUESTED
    await audit.record(
        session, ctx, "transform.invocation.cancelled", resource_type="TRANSFORM_RUN",
        resource_id=invocation.id,
    )


async def complete(
    session: AsyncSession,
    invocation_id: uuid.UUID,
    *,
    succeeded: bool,
    cancelled: bool,
    timed_out: bool,
    exit_code: int | None,
    error_code: str | None,
    error_summary: str | None,
    technical_message: str | None,
    error_location: dict[str, Any] | None,
    bundle_id: uuid.UUID | None = None,
) -> TransformInvocation | None:
    """Write the terminal state.  Artifact indexing has already happened."""
    invocation = await session.get(TransformInvocation, invocation_id)
    if invocation is None:
        return None

    invocation.status = (
        RunStatus.CANCELLED if cancelled
        else RunStatus.TIMED_OUT if timed_out
        else RunStatus.SUCCEEDED if succeeded
        else RunStatus.FAILED
    )
    invocation.ended_at = utcnow()
    invocation.exit_code = exit_code
    invocation.error_code = error_code
    invocation.error_summary = error_summary
    invocation.artifact_bundle_id = bundle_id
    if error_location:
        invocation.technical_metadata = {
            **(invocation.technical_metadata or {}),
            "error_location": error_location,
        }
    if technical_message:
        invocation.technical_metadata = {
            **(invocation.technical_metadata or {}),
            "technical_message": technical_message[:8000],
        }
    if not succeeded:
        invocation.error_category = _category(cancelled, timed_out, error_code)
        invocation.remediation_action = _remediation(invocation, error_code)

    project = await session.get(TransformProject, invocation.project_id)
    if project is not None:
        _apply_health(project, invocation, succeeded=succeeded)
    return invocation


def _apply_health(
    project: TransformProject, invocation: TransformInvocation, *, succeeded: bool,
) -> None:
    project.last_invocation_id = invocation.id
    if invocation.command not in HEALTH_BEARING:
        return
    if succeeded:
        project.last_success_at = invocation.ended_at
        project.health_status = (
            HealthLevel.WARNING if invocation.tests_failed or invocation.tests_warned
            else HealthLevel.HEALTHY
        )
        project.health_message = (
            f"{invocation.tests_failed} test(s) failed in the last build."
            if invocation.tests_failed else None
        )
    else:
        project.health_status = HealthLevel.ERROR
        project.health_message = invocation.error_summary


def _category(
    cancelled: bool, timed_out: bool, error_code: str | None,
) -> ErrorCategory:
    if cancelled:
        return ErrorCategory.USER_ACTION
    if timed_out:
        return ErrorCategory.TRANSIENT
    if error_code and "PERMISSION" in error_code:
        return ErrorCategory.PERMISSION
    return ErrorCategory.USER_CONFIG


def _remediation(invocation: TransformInvocation, error_code: str | None) -> str | None:
    if error_code == "TRANSFORM_TIMEOUT":
        return "NARROW_SELECTOR"
    if invocation.command in ("parse", "compile"):
        return "FIX_PROJECT"
    if invocation.tests_failed:
        return "REVIEW_TESTS"
    return "OPEN_LOGS"


async def fail_start(
    session: AsyncSession, invocation_id: uuid.UUID, exc: Exception,
) -> None:
    invocation = await session.get(TransformInvocation, invocation_id)
    if invocation is None:
        return
    invocation.status = RunStatus.FAILED_TO_START
    invocation.ended_at = utcnow()
    invocation.error_code = "TRANSFORM_START_FAILED"
    invocation.error_category = ErrorCategory.SYSTEM
    invocation.error_summary = f"{type(exc).__name__}: {exc}"[:1000]
    await session.commit()


async def stale(session: AsyncSession) -> int:
    """Release runs no worker is tending.

    A run orphaned by a worker restart holds its project's write slot until
    something says otherwise, so the sweep runs on a timer rather than only at
    startup.  Queued runs are released far sooner than running ones: a queued
    run nobody ever claimed is definitely dead, whereas a running one may simply
    be slow.
    """
    now = utcnow()
    released = 0

    queued_cutoff = now.timestamp() - settings.transform_stale_queue_seconds
    running_cutoff = now.timestamp() - settings.stale_run_seconds

    rows = list((await session.scalars(select(TransformInvocation).where(
        TransformInvocation.status.in_(list(ACTIVE_RUN_STATUSES)),
    ))).all())
    for row in rows:
        reference = row.heartbeat_at or row.started_at or row.created_at
        cutoff = queued_cutoff if row.status == RunStatus.QUEUED else running_cutoff
        if reference.timestamp() >= cutoff:
            continue
        row.status = RunStatus.FAILED_TO_START if row.status == RunStatus.QUEUED \
            else RunStatus.TIMED_OUT
        row.ended_at = now
        row.error_code = "TRANSFORM_ABANDONED"
        row.error_category = ErrorCategory.SYSTEM
        row.error_summary = (
            "This run was not picked up by a worker." if row.status == RunStatus.FAILED_TO_START
            else "This run stopped reporting and was abandoned."
        )
        released += 1
    if released:
        await session.commit()
    return released


async def retry(
    session: AsyncSession, ctx: RequestContext, invocation: TransformInvocation,
) -> TransformInvocation:
    """Re-run exactly what ran before.

    Same revision, same release, same environment, same command, same selector,
    same vars.  A retry that quietly picked up the current draft would be a
    different run wearing the same name, and the whole point of retrying a
    production failure is to re-execute the thing that failed.
    """
    ctx.require(Module.TRANSFORMS, Action.OPERATE)
    project = await session.get(TransformProject, invocation.project_id)
    if project is None:
        raise NotFoundError("That project no longer exists.")
    environment = await session.get(TransformEnvironment, invocation.environment_id)
    revision = await session.get(TransformProjectRevision, invocation.revision_id)
    if environment is None or revision is None:
        raise ValidationError(
            "The environment or version this run used is gone, so it cannot be "
            "repeated exactly.",
            code="TRANSFORM_RETRY_UNAVAILABLE",
        )
    release = (
        await session.get(TransformRelease, invocation.release_id)
        if invocation.release_id else None
    )
    args = invocation.args_json or {}
    return await enqueue(
        session, ctx, project,
        command=invocation.command,
        environment=environment,
        revision=revision,
        selector=invocation.selector,
        exclude=invocation.exclude,
        full_refresh=bool(args.get("full_refresh")),
        limit=args.get("limit"),
        macro=args.get("macro"),
        macro_args=args.get("macro_args") or {},
        selector_name=args.get("selector_name"),
        vars=args.get("vars") or {},
        release=release,
        trigger_type=TriggerType.MANUAL,
        retry_of=invocation.id,
    )


async def list_runs_for_global(
    session: AsyncSession,
    ctx: RequestContext,
    *,
    transform_id: uuid.UUID | None = None,
    status: str | None = None,
    trigger_type: str | None = None,
    error_category: str | None = None,
    since: Any | None = None,
    until: Any | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[TransformInvocation], int, dict[str, int]]:
    """Invocations for the workspace-wide Runs page.

    Shares that page with `PipelineRun`, so the filter vocabulary and the
    summary shape are the Pipeline ones -- a person filtering "failed since
    Tuesday" should not have to know which subsystem produced a row.
    """
    from app.core.params import as_enum

    stmt = select(TransformInvocation).where(
        TransformInvocation.workspace_id == ctx.workspace_id,
    )
    if transform_id:
        stmt = stmt.where(TransformInvocation.project_id == transform_id)
    if status:
        if status.strip().upper() == "ACTIVE":
            stmt = stmt.where(TransformInvocation.status.in_(list(ACTIVE_RUN_STATUSES)))
        else:
            stmt = stmt.where(
                TransformInvocation.status == as_enum(status, RunStatus, field="status")
            )
    if trigger_type:
        stmt = stmt.where(
            TransformInvocation.trigger_type
            == as_enum(trigger_type, TriggerType, field="trigger_type")
        )
    if error_category:
        stmt = stmt.where(
            TransformInvocation.error_category
            == as_enum(error_category, ErrorCategory, field="error_category")
        )
    if since:
        stmt = stmt.where(TransformInvocation.created_at >= since)
    if until:
        stmt = stmt.where(TransformInvocation.created_at <= until)

    total = await session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list((await session.scalars(
        stmt.order_by(TransformInvocation.created_at.desc()).limit(limit).offset(offset)
    )).all())
    counts = (await session.execute(
        stmt.order_by(None)
        .with_only_columns(TransformInvocation.status, func.count())
        .group_by(TransformInvocation.status)
    )).all()
    summary = {key.value.lower(): value for key, value in counts}
    summary["total"] = total
    return rows, total, summary
