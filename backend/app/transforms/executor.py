"""Run one invocation end to end.

The worker owns the queue; this module owns what happens to a single claimed
invocation.  Keeping them apart means the whole execution path can be exercised
without a queue, which is what the behavioural fixture tests do.

The sequence is fixed and each step has a reason:

1. Load the revision named by the invocation -- never "the current files".
2. Materialise it into a private workspace.
3. `dbt deps` first if the project declares packages, because `parse` fails on a
   missing package with an error about a macro.
4. Run the command.
5. Store the artifacts, index them, record per-node results.
6. Write the terminal state, and settle any release waiting on this run.

Step 1 is the one that matters most.  A production failure has to be
reproducible, and it is only reproducible if the run named its bytes.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import utcnow
from app.core.logging import log_event
from app.transforms import (
    connections as connection_service, environments as environment_service,
    files as file_service, indexer, projects as project_service,
    releases as release_service,
)
from app.transforms.models import (
    TransformEnvironment, TransformInvocation, TransformProject,
    TransformProjectRevision,
)
from app.transforms.runtime.commands import DbtCommand, validate_command
from app.transforms.runtime.dbt import DbtResult, DbtRuntime
from app.transforms.runtime.profiles import ResolvedProfile, build_profile
from app.transforms.runtime.workspace import MaterialisedWorkspace, materialise
from app.transforms.storage import object_store

logger = logging.getLogger(__name__)

#: Timeouts by command class.  A `show` that takes ten minutes is a mistake, and
#: failing it fast tells somebody that sooner than a spinner does.  A `build`
#: legitimately takes a long time.
TIMEOUTS = {
    "parse": 300,
    "deps": 600,
    "debug": 120,
    "ls": 300,
    "compile": 900,
    "show": 300,
    "docs-generate": 1800,
}


@dataclass(slots=True)
class PreparedRun:
    invocation_id: uuid.UUID
    project_id: uuid.UUID
    revision_id: uuid.UUID
    command: DbtCommand
    files: dict[str, Any]
    profile: ResolvedProfile
    target_name: str
    timeout_seconds: int
    install_packages: bool
    project_profile_name: str | None


async def prepare(
    session: AsyncSession, invocation: TransformInvocation,
) -> PreparedRun:
    """Assemble everything needed to run, in one read-only pass.

    Deliberately separate from execution and holding no database session
    afterwards: the subprocess can run for half an hour, and a session held open
    across it would occupy a pool slot for the duration.
    """
    project = await session.get(TransformProject, invocation.project_id)
    if project is None:
        raise LookupError("The project for this run no longer exists.")
    environment = await session.get(TransformEnvironment, invocation.environment_id)
    if environment is None:
        raise LookupError("The environment for this run no longer exists.")
    revision = await session.get(TransformProjectRevision, invocation.revision_id)
    if revision is None:
        raise LookupError("The project version for this run no longer exists.")

    if environment.connection_id is None:
        raise LookupError(
            "This environment has no warehouse connection, so nothing can run."
        )
    connection = await connection_service.get(
        session, project.workspace_id, environment.connection_id,
    )
    configuration = await connection_service.resolve_configuration(session, connection)

    facts = await file_service.project_facts(revision)
    profile = build_profile(
        connector_key=connection_service.connector_key(connection),
        configuration=configuration,
        schema=environment_service.effective_schema(
            environment, user_id=invocation.triggered_by,
        ),
        target_name=environment.target_name,
        threads=environment.threads,
        # The project's own profile name as well as ours, so an unmodified Git
        # project runs without its `dbt_project.yml` being rewritten.
        profile_names=[facts.profile] if facts.profile else None,
    )

    args = invocation.args_json or {}
    command = validate_command(
        invocation.command,
        selector=invocation.selector,
        exclude=invocation.exclude,
        full_refresh=bool(args.get("full_refresh")),
        limit=args.get("limit"),
        macro=args.get("macro"),
        macro_args=args.get("macro_args") or {},
        selector_name=args.get("selector_name"),
        vars=args.get("vars") or {},
    )

    return PreparedRun(
        invocation_id=invocation.id,
        project_id=project.id,
        revision_id=revision.id,
        command=command,
        files=dict(revision.manifest_index or {}),
        profile=profile,
        target_name=environment.target_name,
        timeout_seconds=TIMEOUTS.get(command.command, settings.transform_timeout_seconds),
        install_packages=_declares_packages(revision),
        project_profile_name=facts.profile,
    )


def _declares_packages(revision: TransformProjectRevision) -> bool:
    """Whether `dbt deps` needs to run before anything else.

    Checked by presence of the file rather than by parsing it: an empty
    `packages.yml` makes `deps` a no-op, and the cost of a no-op is far below
    the cost of a `parse` that fails with a message about a missing macro when
    the real problem is an uninstalled package.
    """
    index = revision.manifest_index or {}
    return "packages.yml" in index or "dependencies.yml" in index


async def run(
    prepared: PreparedRun,
    *,
    runtime: DbtRuntime,
    cancel_check,
    log_sink=None,
) -> tuple[DbtResult, dict[str, Any]]:
    """Materialise, optionally install packages, then run the command.

    Returns the command's result plus the artifacts of the whole workspace, so
    a `build` that also produced a manifest contributes both.
    """
    store = object_store()
    workspace: MaterialisedWorkspace | None = None
    try:
        workspace = await materialise(
            store=store,
            files=prepared.files,
            workspace_root=Path(settings.transform_workspace_dir),
            prefix=str(prepared.invocation_id)[:8],
        )

        artifacts: dict[str, Any] = {}

        if prepared.install_packages and prepared.command.command != "deps":
            deps = await runtime.execute(
                invocation_id=f"{prepared.invocation_id}-deps",
                command=validate_command("deps"),
                workspace=workspace,
                profile=prepared.profile,
                target_name=prepared.target_name,
                cancel_check=cancel_check,
                log_sink=log_sink,
                timeout_seconds=TIMEOUTS["deps"],
            )
            if not deps.succeeded:
                # Returned as the run's own failure rather than raised: the
                # person asked for a build and needs to see that packages could
                # not be installed, with dbt's own message.
                return deps, {}

        result = await runtime.execute(
            invocation_id=str(prepared.invocation_id),
            command=prepared.command,
            workspace=workspace,
            profile=prepared.profile,
            target_name=prepared.target_name,
            cancel_check=cancel_check,
            log_sink=log_sink,
            timeout_seconds=prepared.timeout_seconds,
        )
        artifacts.update(result.artifacts)
        return result, artifacts
    finally:
        if workspace is not None:
            workspace.cleanup()


async def record(
    session: AsyncSession,
    invocation_id: uuid.UUID,
    *,
    result: DbtResult,
    artifacts: dict[str, Any],
) -> None:
    """Persist everything one finished invocation produced.

    Artifacts go to object storage and are indexed before the terminal status is
    written, so a UI that reacts to the status change finds the results already
    there rather than an empty panel it has to poll for.
    """
    invocation = await session.get(TransformInvocation, invocation_id)
    if invocation is None:
        return
    project = await session.get(TransformProject, invocation.project_id)
    if project is None:
        return

    scope = "RELEASE" if invocation.release_id is not None else "DRAFT"
    bundle = None
    if artifacts or result.log_text:
        bundle = await indexer.store_bundle(
            session,
            project_id=invocation.project_id,
            invocation_id=invocation.id,
            revision_id=invocation.revision_id,
            scope=scope,
            artifacts=artifacts,
            log_text=result.log_text,
        )

    manifest_document = artifacts.get("manifest")
    if bundle is not None and isinstance(manifest_document, dict):
        pipeline_sources = await _pipeline_sources(session, project)
        index = await indexer.index_manifest(
            session,
            bundle=bundle,
            manifest_document=manifest_document,
            pipeline_sources=pipeline_sources,
        )
        if scope == "DRAFT":
            await project_service.record_parse(
                session, project,
                revision_id=invocation.revision_id,
                succeeded=True,
                error=None,
                bundle=bundle,
                project_name=index.project_name,
            )
        if index.dbt_version:
            project.dbt_core_version = index.dbt_version
    elif scope == "DRAFT" and invocation.command in ("parse", "compile", "build", "run"):
        # No manifest means dbt could not parse the project at all.  Recording
        # that is the whole value of the Problems panel; leaving the previous
        # verdict in place would show a green project with a broken YAML in it.
        await project_service.record_parse(
            session, project,
            revision_id=invocation.revision_id,
            succeeded=False,
            error=result.error_summary,
            bundle=bundle,
        )

    run_results_document = artifacts.get("run_results")
    if isinstance(run_results_document, dict):
        await indexer.index_run_results(
            session,
            invocation=invocation,
            run_results_document=run_results_document,
            manifest_document=manifest_document if isinstance(manifest_document, dict) else None,
        )

    if result.preview is not None:
        invocation.technical_metadata = {
            **(invocation.technical_metadata or {}),
            "preview": result.preview,
        }
    if result.listing:
        invocation.technical_metadata = {
            **(invocation.technical_metadata or {}),
            "listing": result.listing[:2000],
        }

    from app.transforms import invocations as invocation_service

    await invocation_service.complete(
        session, invocation_id,
        succeeded=result.succeeded,
        cancelled=result.cancelled,
        timed_out=result.timed_out,
        exit_code=result.exit_code,
        error_code=result.error_code,
        error_summary=result.error_summary,
        technical_message=result.technical_message,
        error_location=result.error_location,
        bundle_id=bundle.id if bundle else None,
    )

    # A release waiting on this run learns its fate here.  Doing it in the same
    # transaction as the terminal status means a crash between the two cannot
    # leave a release VERIFYING forever against a run that finished.
    if invocation.release_id is not None:
        await release_service.settle(session, invocation)

    if scope == "DRAFT":
        await indexer.prune_draft_bundles(session, invocation.project_id)

    log_event(
        logger, logging.INFO, "transform_invocation.recorded",
        invocation_id=str(invocation_id), command=invocation.command,
        succeeded=result.succeeded, resources=bool(manifest_document),
    )


async def _pipeline_sources(
    session: AsyncSession, project: TransformProject,
) -> dict[str, uuid.UUID]:
    """Which warehouse tables an AppBI Pipeline populates.

    Keyed by `schema.identifier`, which is how a dbt source names a relation.
    This is the one thing dbt cannot know and AppBI can, and it is the whole
    substance of the "Produced by CRM → BigQuery Pipeline" line on a source.

    Enrichment only: an empty mapping changes nothing except that the line is
    absent, which is the honest answer for a table AppBI does not load.
    """
    from app.transforms.models import DataAsset

    rows = list((await session.scalars(select(DataAsset).where(
        DataAsset.workspace_id == project.workspace_id,
        DataAsset.pipeline_id.is_not(None),
        DataAsset.deleted_at.is_(None),
    ))).all())
    return {
        f"{(row.schema_name or '').lower()}.{(row.relation_name or '').lower()}":
            row.pipeline_id
        for row in rows if row.pipeline_id is not None
    }


async def store_partial_log(
    session: AsyncSession, invocation_id: uuid.UUID, text: str,
) -> None:
    """Publish a running log so the Logs panel has something to show.

    Into the database, not onto the worker's disk.  The API serving the Logs
    panel is a different container with a different filesystem, so a path on the
    worker is unreadable from there -- which is why a live log used to show
    nothing until the run had finished.

    Only the tail is kept.  The full log goes to object storage when the run
    ends; holding an unbounded string in a JSONB column and rewriting it every
    two seconds is a way to make a long build slow down the whole database.
    """
    invocation = await session.get(TransformInvocation, invocation_id)
    if invocation is None:
        return
    invocation.technical_metadata = {
        **(invocation.technical_metadata or {}),
        "partial_log": text[-200_000:],
        "partial_log_at": utcnow().isoformat(),
    }
    await session.commit()


async def fetch_logs(
    session: AsyncSession,
    invocation: TransformInvocation,
    *,
    cursor: int = 0,
    limit: int = 500,
) -> tuple[list[str], int, bool, int]:
    """Paged log lines, live or finished.

    A finished run's log comes from object storage, which is where the complete
    text is.  A running one comes from the partial log in the row, because the
    complete text does not exist yet.
    """
    text: str | None = None
    if invocation.artifact_bundle_id is not None:
        from app.transforms.models import TransformArtifactBundle
        from app.transforms.storage import ObjectNotFound

        bundle = await session.get(TransformArtifactBundle, invocation.artifact_bundle_id)
        if bundle is not None and bundle.log_storage_key:
            try:
                text = (await object_store().get(bundle.log_storage_key)).decode(
                    "utf-8", errors="replace",
                )
            except ObjectNotFound:
                text = None
    if text is None:
        text = (invocation.technical_metadata or {}).get("partial_log") or ""

    lines = text.splitlines()
    total = len(lines)
    window = lines[cursor:cursor + limit]
    next_cursor = cursor + len(window)
    return window, next_cursor, next_cursor < total, total


def profile_yaml_preview(profile: ResolvedProfile) -> str:
    """A redacted profiles.yml, for the connection-troubleshooting panel.

    Shows the shape without the secrets, so somebody can confirm the target and
    dataset are what they expected without the credential appearing on screen.
    """
    import copy

    document = copy.deepcopy(profile.document)
    for entry in document.values():
        for output in (entry.get("outputs") or {}).values():
            for key in list(output):
                if key in (
                    "password", "refresh_token", "client_secret", "keyfile",
                    "keyfile_json", "token",
                ):
                    output[key] = "[REDACTED]"
    return yaml.safe_dump(document, sort_keys=False)
