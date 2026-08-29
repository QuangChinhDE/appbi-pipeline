"""Connector Builder: projects, test runs, and publishing.

The compiler lives in `builder_manifest.py` and knows nothing about databases.
This module is the half that needs a session, and it re-exports the pure names
so callers have one place to import from.

Nothing here talks to Docker: running the connector is the adapter's job
(guardrail 5). This module decides *what* to run, never *how*.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.registry import get_adapter, spec_hash
from app.core.context import RequestContext
from app.core.db import utcnow
from app.core.errors import NotFoundError, ValidationError
from app.core.logging import log_event
from app.models.builder import BuilderProject
from app.models.engine import ConnectorDefinition
from app.models.enums import BuilderStatus, Certification, ConnectorStatus, ConnectorType
from app.services.builder_manifest import (  # noqa: F401 - re-exported surface
    AUTH_METHODS,
    MANIFEST_VERSION,
    PAGINATION_MODES,
    RUNNER_REPOSITORY,
    RUNNER_VERSION,
    TEST_PAGE_LIMIT,
    TEST_RECORD_LIMIT,
    compile_manifest,
    connector_key_for,
    definition_from_manifest,
    descriptor,
    infer_schema,
    manifest_yaml,
    outbound_urls,
    slugify,
    starter_definition,
    validate,
)
from app.services.builder_manifest import egress

logger = logging.getLogger(__name__)


# ── persistence ────────────────────────────────────────────────────────────

async def list_projects(session: AsyncSession, ctx: RequestContext) -> list[BuilderProject]:
    rows = await session.scalars(
        select(BuilderProject)
        .where(BuilderProject.workspace_id == ctx.workspace_id,
               BuilderProject.deleted_at.is_(None))
        .order_by(BuilderProject.updated_at.desc())
    )
    return list(rows.all())


async def get_project(
    session: AsyncSession, ctx: RequestContext, project_id: uuid.UUID
) -> BuilderProject:
    project = await session.scalar(
        select(BuilderProject).where(
            BuilderProject.id == project_id,
            BuilderProject.workspace_id == ctx.workspace_id,
            BuilderProject.deleted_at.is_(None),
        )
    )
    if project is None:
        # 404 rather than 403: a project in another workspace must not be
        # distinguishable from one that never existed (§10).
        raise NotFoundError("Không tìm thấy dự án connector.", code="BUILDER_NOT_FOUND")
    return project


# ── test read ──────────────────────────────────────────────────────────────

async def test_read(
    definition: dict[str, Any],
    *,
    stream_name: str | None = None,
    config_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the connector against the real API and report what came back.

    Bounded on purpose: a builder test hits an endpoint nobody has reviewed, so
    it reads one stream, a couple of pages, a handful of records.
    """
    manifest = compile_manifest(definition)
    target = stream_name or manifest["streams"][0]["name"]
    known = {s["name"] for s in manifest["streams"]}
    if target not in known:
        raise ValidationError(
            f"Stream '{target}' không tồn tại trong connector này.",
            code="BUILDER_STREAM_UNKNOWN", details={"allowed": sorted(known)},
        )

    # About to make the request for real, so this is where the resolving checks
    # belong — for every URL the connector may call, not just the base.
    for field, url in outbound_urls(definition):
        egress.check_url(url, field=field)

    config: dict[str, Any] = {"base_url": definition["base_url"]}
    config.update(config_overrides or {})

    adapter = get_adapter()
    outcome = await adapter.test_declarative_read(
        descriptor(),
        manifest=manifest,
        config=config,
        stream_name=target,
        record_limit=TEST_RECORD_LIMIT,
        page_limit=TEST_PAGE_LIMIT,
    )
    records = outcome.get("records") or []
    outcome.setdefault("requests", [])
    # Offered, not applied: replacing a schema the user has edited would throw
    # away their work every time they press Test.
    #
    # Inferred only as a fallback. An engine that ran `discover` already knows
    # the real schema, including fields absent from a short sample — guessing
    # over the top of that would be strictly worse.
    if not outcome.get("inferred_schema"):
        outcome["inferred_schema"] = infer_schema(records) if records else None
    log_event(logger, logging.INFO, "builder.test_read",
              stream=target, records=len(records), ok=outcome.get("ok"))
    return outcome


# ── publish ────────────────────────────────────────────────────────────────

async def publish(
    session: AsyncSession, ctx: RequestContext, project: BuilderProject
) -> ConnectorDefinition:
    """Snapshot the project into the connector catalogue.

    After this the connector behaves like any other: it appears in the source
    wizard, renders its own config form, and is executed through the same
    adapter. What makes it different is only that its behaviour is carried in a
    document instead of a purpose-built image.
    """
    manifest = compile_manifest(project.definition)
    spec = manifest["spec"]["connection_specification"]

    connector = await session.scalar(
        select(ConnectorDefinition).where(
            ConnectorDefinition.connector_key == project.connector_key
        )
    )
    project.published_version += 1
    project.status = BuilderStatus.PUBLISHED
    project.published_at = utcnow()

    # Which image runs a manifest is an engine fact, so the engine is asked.
    # Two different answers, kept distinct:
    #   no such method -> an adapter written before this existed; the Airbyte
    #                     runner is the historical default and nothing changes.
    #   returns None   -> the engine is saying it cannot run connectors built
    #                     here. Publishing then fails with that reason, rather
    #                     than recording an image the engine will never pull
    #                     and surfacing it as a mysterious sync failure later.
    declare = getattr(get_adapter(), "declarative_runner", None)
    if declare is None:
        runner = (RUNNER_REPOSITORY, RUNNER_VERSION)
    else:
        runner = declare()
        if runner is None:
            raise ValidationError(
                "Engine hiện tại không chạy được connector tự build.",
                code="BUILDER_UNSUPPORTED_BY_ENGINE",
            )
    runner_repository, runner_version = runner

    fields = {
        "display_name": project.name,
        "connector_type": ConnectorType.SOURCE,
        "category": "Custom",
        "description": project.description,
        "icon": project.icon,
        "docker_repository": runner_repository,
        # `version` is the runner tag because it becomes the image tag the
        # engine pulls. What the user reasons about is their own revision, which
        # is carried separately so the two can never be confused.
        "version": runner_version,
        "latest_version": runner_version,
        "display_version": f"v{project.published_version}",
        "release_stage": "custom",
        "support_level": "workspace",
        "spec_schema": spec,
        "spec_hash": spec_hash(spec),
        "spec_source": "BUILDER",
        # Not certified: the workspace built it, so the workspace owns it.
        "certification": Certification.BETA,
        "status": ConnectorStatus.ACTIVE,
        "supports_incremental": any(
            s.get("incremental") for s in project.definition.get("streams") or []
        ),
        "supports_cdc": False,
        "supports_oauth": False,
        "supports_namespaces": False,
        "supported_destination_sync_modes": [],
        "declarative_manifest": manifest,
        "owner_workspace_id": project.workspace_id,
        "image_pulled": True,
    }

    if connector is None:
        connector = ConnectorDefinition(connector_key=project.connector_key, **fields)
        session.add(connector)
    else:
        for key, value in fields.items():
            setattr(connector, key, value)

    await session.flush()
    log_event(logger, logging.INFO, "builder.published",
              project=str(project.id), connector=project.connector_key,
              revision=project.published_version)
    return connector
