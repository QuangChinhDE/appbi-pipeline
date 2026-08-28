"""Connector Builder endpoints (§23).

The editor is a normal product resource: workspace-scoped, permission-checked,
audited. The only unusual verb is `test`, which runs the connector being edited
against the real API and returns what came back.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Response
from pydantic import Field

from app.api.deps import CtxDep, SessionDep
from app.core.errors import ValidationError
from app.core.permissions import Action, Module
from app.core.db import utcnow
from app.models.builder import BuilderProject
from app.models.enums import BuilderStatus
from app.schemas.common import ORMModel
from app.services import audit, builder

router = APIRouter(prefix="/builder", tags=["builder"])


# ── payloads ───────────────────────────────────────────────────────────────

class BuilderProjectView(ORMModel):
    id: uuid.UUID
    name: str
    description: str | None = None
    connector_key: str
    status: str
    published_version: int
    published_at: Any | None = None
    last_tested_at: Any | None = None
    last_test_ok: bool | None = None
    stream_count: int = 0
    updated_at: Any | None = None


class BuilderProjectDetail(BuilderProjectView):
    definition: dict[str, Any] = Field(default_factory=dict)


class CreateProject(ORMModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)


class UpdateProject(ORMModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    definition: dict[str, Any] | None = None


class TestRequest(ORMModel):
    stream_name: str | None = None
    # Values for the credentials the connector declares. They are used for this
    # one call and never stored: a draft is not a place to keep secrets.
    config: dict[str, Any] = Field(default_factory=dict)


class TestResponse(ORMModel):
    ok: bool
    records: list[dict[str, Any]] = Field(default_factory=list)
    logs: list[str] = Field(default_factory=list)
    error: dict[str, Any] | None = None
    record_count: int = 0
    inferred_fields: list[str] = Field(default_factory=list)
    inferred_schema: dict[str, Any] | None = None
    # What actually went over the wire. A record count alone cannot tell the
    # user that their "1 record" was the API's error envelope.
    requests: list[dict[str, Any]] = Field(default_factory=list)
    # Set by the adapter. Not every engine can read a bounded window of
    # records: Airbyte's Config API runs a whole connector but only reads as
    # part of a sync. When this is false an empty `records` means "this engine
    # does not preview", not "your connector returned nothing" — and the UI has
    # to say so rather than showing an empty table.
    record_preview_supported: bool = True


def _view(project: BuilderProject) -> BuilderProjectView:
    return BuilderProjectView(
        id=project.id,
        name=project.name,
        description=project.description,
        connector_key=project.connector_key,
        status=project.status.value,
        published_version=project.published_version,
        published_at=project.published_at,
        last_tested_at=project.last_tested_at,
        last_test_ok=project.last_test_ok,
        stream_count=len((project.definition or {}).get("streams") or []),
        updated_at=project.updated_at,
    )


def _detail(project: BuilderProject) -> BuilderProjectDetail:
    return BuilderProjectDetail(**_view(project).model_dump(),
                                definition=project.definition or {})


# ── routes ─────────────────────────────────────────────────────────────────

@router.get("/projects", response_model=list[BuilderProjectView])
async def list_projects(session: SessionDep, ctx: CtxDep) -> list[BuilderProjectView]:
    ctx.require(Module.CONNECTORS, Action.VIEW)
    return [_view(p) for p in await builder.list_projects(session, ctx)]


@router.post("/projects", response_model=BuilderProjectDetail, status_code=201)
async def create_project(
    payload: CreateProject, session: SessionDep, ctx: CtxDep
) -> BuilderProjectDetail:
    # Building a connector is creating something the whole workspace can then
    # use, so it needs the same permission as adding one.
    ctx.require(Module.CONNECTORS, Action.CREATE)

    project_id = uuid.uuid4()
    project = BuilderProject(
        id=project_id,
        workspace_id=ctx.workspace_id,
        name=payload.name.strip(),
        description=payload.description,
        connector_key=builder.connector_key_for(payload.name, project_id),
        definition=builder.starter_definition(payload.name.strip()),
        created_by=ctx.user_id,
        updated_by=ctx.user_id,
    )
    session.add(project)
    await session.flush()
    await audit.record(session, ctx, action="builder.project.created",
                       resource_type="builder_project", resource_id=project.id,
                       resource_name=project.name)
    await session.commit()
    await session.refresh(project)
    return _detail(project)


@router.get("/projects/{project_id}", response_model=BuilderProjectDetail)
async def get_project(
    project_id: uuid.UUID, session: SessionDep, ctx: CtxDep
) -> BuilderProjectDetail:
    ctx.require(Module.CONNECTORS, Action.VIEW)
    return _detail(await builder.get_project(session, ctx, project_id))


@router.patch("/projects/{project_id}", response_model=BuilderProjectDetail)
async def update_project(
    project_id: uuid.UUID, payload: UpdateProject, session: SessionDep, ctx: CtxDep
) -> BuilderProjectDetail:
    ctx.require(Module.CONNECTORS, Action.EDIT)
    project = await builder.get_project(session, ctx, project_id)

    if payload.name is not None:
        project.name = payload.name.strip()
    if payload.description is not None:
        project.description = payload.description
    if payload.definition is not None:
        # Saved as given: a draft the user is still shaping should not be
        # rejected for being incomplete. Validation belongs to test and publish.
        definition_changed = payload.definition != project.definition
        project.definition = payload.definition
        if definition_changed:
            # A successful test certifies one exact draft. Keeping it green
            # after the definition changes lets an untested edit be published.
            project.last_test_ok = None
            project.status = BuilderStatus.DRAFT
    project.updated_by = ctx.user_id

    await session.flush()
    await session.commit()
    await session.refresh(project)
    return _detail(project)


@router.delete("/projects/{project_id}", status_code=204)
async def delete_project(
    project_id: uuid.UUID, session: SessionDep, ctx: CtxDep
) -> Response:
    ctx.require(Module.CONNECTORS, Action.DELETE)
    project = await builder.get_project(session, ctx, project_id)
    project.deleted_at = utcnow()
    await audit.record(session, ctx, action="builder.project.deleted",
                       resource_type="builder_project", resource_id=project.id,
                       resource_name=project.name)
    await session.commit()
    return Response(status_code=204)


@router.post("/projects/{project_id}/test", response_model=TestResponse)
async def test_project(
    project_id: uuid.UUID,
    payload: TestRequest,
    session: SessionDep,
    ctx: CtxDep,
) -> TestResponse:
    ctx.require(Module.CONNECTORS, Action.EDIT)
    project = await builder.get_project(session, ctx, project_id)

    outcome = await builder.test_read(
        project.definition or {},
        stream_name=payload.stream_name,
        config_overrides=payload.config,
    )

    project.last_tested_at = utcnow()
    project.last_test_ok = bool(outcome.get("ok"))
    await session.commit()

    records = outcome.get("records") or []
    # The union of keys across the sample, so a field missing from the first
    # record is still offered as a cursor or primary key.
    fields: list[str] = []
    for record in records:
        for key in record:
            if key not in fields:
                fields.append(key)

    return TestResponse(
        ok=bool(outcome.get("ok")),
        records=records,
        logs=outcome.get("logs") or [],
        error=outcome.get("error"),
        record_count=len(records),
        inferred_fields=fields,
        inferred_schema=outcome.get("inferred_schema"),
        requests=outcome.get("requests") or [],
        record_preview_supported=bool(outcome.get("record_preview_supported", True)),
    )


@router.post("/projects/{project_id}/publish", response_model=BuilderProjectDetail)
async def publish_project(
    project_id: uuid.UUID, session: SessionDep, ctx: CtxDep
) -> BuilderProjectDetail:
    ctx.require(Module.CONNECTORS, Action.CREATE)
    project = await builder.get_project(session, ctx, project_id)

    if not project.last_test_ok:
        # Publishing puts the connector in front of everyone in the workspace.
        # A green test read is the cheapest evidence that it does anything.
        raise ValidationError(
            "Hãy chạy thử thành công trước khi phát hành connector.",
            code="BUILDER_TEST_REQUIRED",
        )

    connector = await builder.publish(session, ctx, project)
    await audit.record(session, ctx, action="builder.project.published",
                       resource_type="builder_project", resource_id=project.id,
                       resource_name=project.name,
                       after={"connector_key": connector.connector_key,
                              "revision": project.published_version})
    await session.commit()
    await session.refresh(project)
    return _detail(project)


class ImportManifest(ORMModel):
    manifest: str = Field(min_length=1, max_length=500_000)


@router.post("/projects/{project_id}/import", response_model=BuilderProjectDetail)
async def import_manifest(
    project_id: uuid.UUID, payload: ImportManifest, session: SessionDep, ctx: CtxDep
) -> BuilderProjectDetail:
    """Replace the editor state from a manifest the user pasted in."""
    ctx.require(Module.CONNECTORS, Action.EDIT)
    project = await builder.get_project(session, ctx, project_id)

    definition = builder.definition_from_manifest(payload.manifest)
    definition["name"] = project.name
    # Reject before saving: an import that compiles to nothing runnable should
    # not overwrite a working draft.
    builder.compile_manifest(definition)

    project.definition = definition
    project.updated_by = ctx.user_id
    # The imported connector has not been proven here, whatever it did elsewhere.
    project.last_test_ok = None
    project.status = BuilderStatus.DRAFT
    await audit.record(session, ctx, action="builder.project.imported",
                       resource_type="builder_project", resource_id=project.id,
                       resource_name=project.name)
    await session.commit()
    await session.refresh(project)
    return _detail(project)


@router.get("/projects/{project_id}/manifest.yaml", response_class=Response)
async def project_manifest_yaml(
    project_id: uuid.UUID, session: SessionDep, ctx: CtxDep
) -> Response:
    ctx.require(Module.CONNECTORS, Action.VIEW)
    project = await builder.get_project(session, ctx, project_id)
    return Response(
        content=builder.manifest_yaml(project.definition or {}),
        media_type="application/yaml",
    )


@router.get("/projects/{project_id}/manifest")
async def project_manifest(
    project_id: uuid.UUID, session: SessionDep, ctx: CtxDep
) -> dict[str, Any]:
    """The compiled document, for people who want to read what they built."""
    ctx.require(Module.CONNECTORS, Action.VIEW)
    project = await builder.get_project(session, ctx, project_id)
    return builder.compile_manifest(project.definition or {})
