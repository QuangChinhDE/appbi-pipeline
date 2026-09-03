"""The Transform V2 HTTP surface.

Route shapes follow the blueprint's §60 proposal.  The prefix stays
``/transforms`` -- a project is still what the module is about -- but a
"transform" is now a dbt project rather than a bag of models, and every route
below reflects that: files, resources, invocations, releases, git.

Nothing here contains dbt knowledge.  Handlers validate the request shape, call
one service, and present the result.  The rule that keeps this file readable is
that any function long enough to need a comment about *what dbt does* belongs in
the service layer instead.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Header, Query, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.api.deps import CtxDep, SessionDep
from app.core.config import settings
from app.core.db import utcnow
from app.core.errors import NotFoundError, ValidationError
from app.core.permissions import Action, Module
from app.models.enums import TriggerType
from app.schemas.common import PageInfo, Paginated
from app.transforms import (
    connections as connection_service, environments as environment_service,
    executor, export as export_service, files as file_service, generator,
    git as git_service, indexer, invocations as invocation_service,
    projects as project_service, releases as release_service,
    resources as resource_service, scaffold,
)
from app.transforms.models import (
    TransformArtifactBundle, TransformEnvironment, TransformInvocation,
    TransformProject, TransformProjectRevision, TransformRelease,
    TransformResourceIndex,
)
from app.transforms.schemas import (
    BranchView, CompiledView, CompletionsView, ConnectionCreate, ConnectionUpdate,
    ConnectionView, DocEntry, EnvironmentUpdate, EnvironmentView, FacetsView,
    FileBatchRequest, FileContentView, FileCreateRequest, FileDeleteRequest,
    FileMoveRequest, FileSaveRequest, FileTreeView, GenerateModelRequest,
    GitCheckoutRequest, GitCommitRequest, GitConfigureRequest, GitDiffView,
    GitPullRequest,
    GitStatusView, InvocationDetail, InvocationRequest, InvocationView, LineageView,
    LogPage, ProblemsView, ProjectCreate, ProjectDetail, ProjectUpdate, ProjectView,
    PublishPlanView, PublishResult, ReleaseCreate, ReleaseView, RepositoryInspectRequest,
    RepositoryInspectResult, ResourceDetail, ResourcePageView, SaveResult, SearchView,
    SystemView, TemplateView,
)
from app.transforms.storage import object_store

router = APIRouter(prefix="/transforms", tags=["transforms"])


# ── systems and connections ───────────────────────────────────────────────


@router.get("/systems", response_model=list[SystemView])
async def systems(ctx: CtxDep):
    """The warehouse kinds a project can run on, and how each authenticates."""
    ctx.require(Module.TRANSFORMS, Action.VIEW)
    return connection_service.supported_systems()


@router.get("/connections", response_model=list[ConnectionView])
async def list_connections(
    session: SessionDep,
    ctx: CtxDep,
    connector_key: Annotated[str | None, Query()] = None,
):
    # Destinations created since the last visit become selectable here rather
    # than needing a migration, so the list is never missing a warehouse the
    # workspace already has.
    await connection_service.ensure_destination_connections(session, ctx)
    result = await connection_service.list_all(session, ctx, connector_key)
    await session.commit()
    return result


@router.post(
    "/connections", response_model=ConnectionView, status_code=status.HTTP_201_CREATED,
)
async def create_connection(
    payload: ConnectionCreate, session: SessionDep, ctx: CtxDep,
):
    from app.services import oauth as oauth_service

    credentials = payload.model_dump(
        include={"credentials_json", "username", "password"}, exclude_none=True,
    )
    if payload.auth_method == "oauth":
        if payload.oauth_grant_id is None:
            raise ValidationError(
                "The Google sign-in has not been completed.",
                code="TRANSFORM_OAUTH_MISSING",
            )
        # The grant is redeemed here, once, and what comes back is already
        # shaped for a dbt profile. Nothing about it passed through the browser
        # except an opaque handle.
        credentials = await oauth_service.consume_grant(
            session, payload.oauth_grant_id,
            workspace_id=ctx.workspace_id, connector_key=payload.connector_key,
        )
    result = await connection_service.create(
        session, ctx,
        connector_key=payload.connector_key,
        name=payload.name,
        auth_method=payload.auth_method,
        configuration=payload.model_dump(
            include={"project_id", "dataset_location", "host", "port",
                     "database", "ssl_mode"},
            exclude_none=True,
        ),
        credentials=credentials,
    )
    await session.commit()
    return result


@router.patch("/connections/{connection_id}", response_model=ConnectionView)
async def update_connection(
    connection_id: uuid.UUID,
    payload: ConnectionUpdate,
    session: SessionDep,
    ctx: CtxDep,
):
    result = await connection_service.update(
        session, ctx, connection_id,
        name=payload.name,
        configuration=payload.model_dump(
            include={"project_id", "dataset_location", "host", "port",
                     "database", "ssl_mode"},
            exclude_none=True,
        ) or None,
        credentials=payload.model_dump(
            include={"credentials_json", "username", "password"}, exclude_none=True,
        ) or None,
    )
    await session.commit()
    return result


@router.post("/connections/{connection_id}/verify", response_model=ConnectionView)
async def verify_connection(
    connection_id: uuid.UUID, session: SessionDep, ctx: CtxDep,
):
    result = await connection_service.verify(session, ctx, connection_id)
    await session.commit()
    return result


@router.delete("/connections/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connection(
    connection_id: uuid.UUID, session: SessionDep, ctx: CtxDep,
) -> Response:
    await connection_service.delete(session, ctx, connection_id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/connections/{connection_id}/warehouse")
async def browse_warehouse(
    connection_id: uuid.UUID,
    session: SessionDep,
    ctx: CtxDep,
    catalog: Annotated[str | None, Query(max_length=200)] = None,
    schema: Annotated[str | None, Query(max_length=200)] = None,
):
    """What this connection can see: projects, then datasets, then tables.

    Used when writing a `source()` -- somebody needs the real schema and table
    names, and guessing them is how a project gets a source that compiles and
    returns nothing.
    """
    ctx.require(Module.TRANSFORMS, Action.VIEW)
    from app.transforms.warehouse import (
        browse_catalogs, browse_relations, browse_schemas,
    )

    connection = await connection_service.get(session, ctx.workspace_id, connection_id)
    configuration = await connection_service.resolve_configuration(session, connection)
    key = connection_service.connector_key(connection)

    if schema:
        relations = await browse_relations(
            key, configuration, catalog_name=catalog, schema_name=schema,
        )
        return {
            "catalog": catalog, "schema": schema,
            "relations": [
                {
                    "name": item.relation_name,
                    "type": item.relation_type,
                    "schema": item.schema_name,
                }
                for item in relations
            ],
        }
    return {
        "catalogs": await browse_catalogs(key, configuration),
        "schemas": await browse_schemas(key, configuration, catalog_name=catalog),
    }


@router.get("/connections/{connection_id}/warehouse/columns")
async def browse_warehouse_columns(
    connection_id: uuid.UUID,
    session: SessionDep,
    ctx: CtxDep,
    schema: Annotated[str, Query(max_length=200)],
    table: Annotated[str, Query(max_length=200)],
    catalog: Annotated[str | None, Query(max_length=200)] = None,
):
    """The columns of one table, so a model can be written against real names."""
    ctx.require(Module.TRANSFORMS, Action.VIEW)
    from app.transforms.warehouse import browse_columns

    connection = await connection_service.get(session, ctx.workspace_id, connection_id)
    configuration = await connection_service.resolve_configuration(session, connection)
    key = connection_service.connector_key(connection)
    columns = await browse_columns(
        key, configuration,
        catalog_name=catalog, schema_name=schema, relation_name=table,
    )
    return {
        "schema": schema, "table": table,
        "columns": [
            {"name": item.name, "data_type": item.data_type, "nullable": item.nullable}
            for item in columns
        ],
    }


@router.post("/{project_id}/generate-model", response_model=SaveResult)
async def generate_model(
    project_id: uuid.UUID,
    body: GenerateModelRequest,
    session: SessionDep,
    ctx: CtxDep,
):
    """Write a staging model and its YAML from a description of a source table.

    The output is ordinary dbt in the conventional places, so the next edit is
    a normal edit -- this runs once and leaves nothing behind.
    """
    project = await project_service.get(session, ctx, project_id)
    ctx.require(Module.TRANSFORMS, Action.EDIT)

    revision, written = await generator.generate_staging_model(
        session, ctx, project,
        source_name=body.source_name,
        schema_name=body.schema_name,
        table_name=body.table_name,
        model_name=body.model_name,
        columns=[column.model_dump() for column in body.columns],
        materialized=body.materialized,
        description=body.description,
        expected_revision_id=body.expected_revision_id,
        store=object_store(),
    )
    parse = await _queue_parse(session, ctx, project)
    await session.commit()
    return _save_result(revision, written, parse)


# ── repository inspection ─────────────────────────────────────────────────


@router.post("/inspect-repository", response_model=RepositoryInspectResult)
async def inspect_repository(
    payload: RepositoryInspectRequest, ctx: CtxDep,
):
    """Confirm a repository holds a dbt project, and describe it.

    Nothing is created and nothing is converted.  V1's equivalent produced a
    list of everything it would have to drop; there is nothing to drop now, so
    this reports shape rather than losses.
    """
    ctx.require(Module.TRANSFORMS, Action.CREATE)
    import yaml

    ref = git_service.parse_repo_url(payload.repo_url)
    branch = payload.branch or ref.ref
    commit = await git_service.head_commit(ref, branch, payload.token)
    tree = await git_service.fetch_tree(ref, branch, payload.token)
    scoped, root = git_service.scope_to_project(
        tree, payload.subdirectory or ref.subdirectory,
    )

    warnings: list[str] = []
    project_name = profile = None
    requirement: Any = None
    try:
        document = yaml.safe_load(scoped["dbt_project.yml"].decode("utf-8"))
        if isinstance(document, dict):
            project_name = document.get("name")
            profile = document.get("profile")
            requirement = document.get("require-dbt-version")
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        warnings.append(f"`dbt_project.yml` could not be read: {exc}")

    if requirement and not _version_satisfied(requirement, settings.dbt_core_version):
        warnings.append(
            f"This project asks for dbt {requirement}; AppBI runs "
            f"{settings.dbt_core_version}. It may not parse."
        )
    if any(path.endswith(".py") for path in scoped):
        warnings.append(
            "This project contains Python models, which this version of AppBI "
            "stores and shows but does not execute."
        )

    directories = sorted({
        path.split("/", 1)[0] for path in scoped if "/" in path
    })
    packages: list[str] = []
    if "packages.yml" in scoped:
        try:
            document = yaml.safe_load(scoped["packages.yml"].decode("utf-8")) or {}
            packages = [
                str(item.get("package") or item.get("git") or item.get("local"))
                for item in (document.get("packages") or [])
                if isinstance(item, dict)
            ]
        except (UnicodeDecodeError, yaml.YAMLError):
            warnings.append("`packages.yml` could not be read.")

    return RepositoryInspectResult(
        detected_root=root,
        dbt_project_name=project_name,
        dbt_version_requirement=requirement,
        profile_name=profile,
        file_count=len(scoped),
        model_count=sum(
            1 for path in scoped
            if path.startswith("models/") and path.endswith((".sql", ".py"))
        ),
        resource_directories=directories,
        packages=packages,
        branch=branch,
        commit_sha=commit,
        warnings=warnings,
    )


def _version_satisfied(requirement: Any, version: str) -> bool:
    """A best-effort check of `require-dbt-version` against the pinned runtime.

    Deliberately permissive: this decides whether to show a warning, and dbt
    itself enforces the constraint at parse time.  Refusing an import on a
    version string this could not interpret would block a project that would in
    fact run.
    """
    clauses = requirement if isinstance(requirement, list) else [requirement]
    current = tuple(int(part) for part in version.split(".")[:3] if part.isdigit())
    for clause in clauses:
        text = str(clause).strip()
        for operator in (">=", "<=", "==", ">", "<"):
            if text.startswith(operator):
                raw = text[len(operator):].strip()
                bound = tuple(
                    int(part) for part in raw.split(".")[:3] if part.isdigit()
                )
                if not bound:
                    continue
                comparison = {
                    ">=": current >= bound, "<=": current <= bound,
                    "==": current[:len(bound)] == bound,
                    ">": current > bound, "<": current < bound,
                }[operator]
                if not comparison:
                    return False
                break
    return True


# ── projects ──────────────────────────────────────────────────────────────


@router.get("", response_model=Paginated[ProjectView])
async def list_projects(
    session: SessionDep,
    ctx: CtxDep,
    search: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    rows, total = await project_service.list_projects(
        session, ctx, search=search, limit=limit, offset=offset,
    )
    return Paginated[ProjectView](
        items=[await project_service.present(session, ctx, row) for row in rows],
        page=PageInfo(
            has_more=offset + len(rows) < total, total=total, limit=limit, offset=offset,
        ),
    )


@router.post("", response_model=ProjectDetail, status_code=status.HTTP_201_CREATED)
async def create_project(payload: ProjectCreate, session: SessionDep, ctx: CtxDep):
    """Create a project from a starter, a repository, or an upload.

    All three paths end identically: a revision holding real dbt project files.
    A Git project is stored exactly as cloned, which is the whole point -- there
    is no "convert repository to AppBI models" step any more.
    """
    files: dict[str, bytes] | None = None
    commit = branch = None

    if payload.source == "GIT":
        if not payload.repo_url:
            raise ValidationError(
                "A repository address is required.", code="TRANSFORM_GIT_URL_REQUIRED",
            )
        ref = git_service.parse_repo_url(payload.repo_url)
        branch = payload.branch or ref.ref or "main"
        commit = await git_service.head_commit(ref, branch, payload.token)
        tree = await git_service.fetch_tree(ref, branch, payload.token)
        files, root = git_service.scope_to_project(
            tree, payload.subdirectory or ref.subdirectory,
        )

    project = await project_service.create(
        session, ctx,
        name=payload.name,
        description=payload.description,
        connection_id=payload.connection_id,
        mode=project_service.GIT if payload.source == "GIT" else project_service.MANAGED,
        dbt_project_name=payload.dbt_project_name,
        development_schema=payload.development_schema,
        production_schema=payload.production_schema,
        source_schema=payload.source_schema,
        per_user_schemas=payload.per_user_schemas,
        with_examples=payload.with_examples,
        files=files,
        git_commit_sha=commit,
        git_branch=branch,
    )

    if payload.source == "GIT" and payload.repo_url:
        binding = await git_service.bind(
            session, ctx, project,
            repo_url=payload.repo_url,
            branch=branch or "main",
            subdirectory=root,
            token=payload.token,
            auto_pull=payload.auto_pull,
            interval_minutes=payload.interval_minutes,
        )
        binding.head_commit_sha = commit
        binding.remote_commit_sha = commit

    # Parse immediately.  A project that lands in the workbench with an empty
    # resource tree looks broken even when it is fine, and the answer is one
    # cheap dbt command away.
    await _queue_parse(session, ctx, project)
    await session.commit()

    project = await project_service.get(session, ctx, project.id)
    return await project_service.detail(session, ctx, project)


@router.post(
    "/upload", response_model=ProjectDetail, status_code=status.HTTP_201_CREATED,
)
async def upload_project(
    session: SessionDep,
    ctx: CtxDep,
    file: UploadFile,
    name: Annotated[str, Query(max_length=200)],
    connection_id: Annotated[uuid.UUID, Query()],
    development_schema: Annotated[str | None, Query()] = None,
    production_schema: Annotated[str | None, Query()] = None,
):
    """Create a project from an uploaded dbt project archive."""
    import io
    import zipfile

    ctx.require(Module.TRANSFORMS, Action.CREATE)
    payload = await file.read()
    if len(payload) > git_service.MAX_ARCHIVE_BYTES:
        raise ValidationError(
            "That archive is too large.", code="TRANSFORM_UPLOAD_TOO_LARGE",
        )
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as exc:
        raise ValidationError(
            "That file is not a ZIP archive.", code="TRANSFORM_UPLOAD_INVALID",
        ) from exc

    raw: dict[str, bytes] = {}
    for entry in archive.infolist():
        if entry.is_dir() or len(raw) >= git_service.MAX_FILES:
            continue
        if entry.filename.startswith("/") or ".." in entry.filename.split("/"):
            continue
        if entry.filename.startswith(git_service.SKIP_PREFIXES):
            continue
        raw[entry.filename] = archive.read(entry)

    files, _ = git_service.scope_to_project(raw, "")
    project = await project_service.create(
        session, ctx,
        name=name, description=None, connection_id=connection_id,
        mode=project_service.MANAGED,
        development_schema=development_schema,
        production_schema=production_schema,
        files=files,
    )
    await _queue_parse(session, ctx, project)
    await session.commit()
    project = await project_service.get(session, ctx, project.id)
    return await project_service.detail(session, ctx, project)


@router.get("/{project_id}", response_model=ProjectDetail)
async def project_detail(project_id: uuid.UUID, session: SessionDep, ctx: CtxDep):
    project = await project_service.get(session, ctx, project_id)
    return await project_service.detail(session, ctx, project)


@router.patch("/{project_id}", response_model=ProjectDetail)
async def update_project(
    project_id: uuid.UUID, payload: ProjectUpdate, session: SessionDep, ctx: CtxDep,
):
    project = await project_service.get(session, ctx, project_id)
    await project_service.update(
        session, ctx, project, **payload.model_dump(exclude_unset=True),
    )
    await session.commit()
    return await project_service.detail(session, ctx, project)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: uuid.UUID, session: SessionDep, ctx: CtxDep,
) -> Response:
    project = await project_service.get(session, ctx, project_id)
    await project_service.remove(session, ctx, project)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{project_id}/export")
async def export_project(
    project_id: uuid.UUID,
    session: SessionDep,
    ctx: CtxDep,
    release_id: Annotated[uuid.UUID | None, Query()] = None,
):
    """Download the project as a standard dbt project.

    `release_id` downloads exactly what that release runs -- the backup to take
    before anything destructive.
    """
    project = await project_service.get(session, ctx, project_id)
    ctx.require(Module.TRANSFORMS, Action.VIEW)
    revision = None
    if release_id is not None:
        release = await release_service.get(session, project, release_id)
        revision = await session.get(TransformProjectRevision, release.revision_id)
    content = await export_service.export_zip(session, project, revision=revision)
    filename = export_service.safe_filename(project.name) + ".zip"
    return StreamingResponse(
        iter([content]), media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── environments ──────────────────────────────────────────────────────────


@router.get("/{project_id}/environments", response_model=list[EnvironmentView])
async def list_environments(project_id: uuid.UUID, session: SessionDep, ctx: CtxDep):
    project = await project_service.get(session, ctx, project_id)
    ctx.require(Module.TRANSFORMS, Action.VIEW)
    out = []
    for environment in await environment_service.list_all(session, project):
        connection = (
            await connection_service.get(
                session, ctx.workspace_id, environment.connection_id,
            ) if environment.connection_id else None
        )
        out.append(environment_service.view(
            environment,
            connection=(
                await connection_service.connector_display(session, connection)
                if connection else None
            ),
            user_id=ctx.user_id,
        ))
    return out


@router.patch(
    "/{project_id}/environments/{environment_id}", response_model=EnvironmentView,
)
async def update_environment(
    project_id: uuid.UUID,
    environment_id: uuid.UUID,
    payload: EnvironmentUpdate,
    session: SessionDep,
    ctx: CtxDep,
):
    project = await project_service.get(session, ctx, project_id)
    fields = payload.model_dump(exclude_unset=True)
    environment = await environment_service.update(
        session, ctx, project, environment_id,
        vars_json=fields.pop("vars", None),
        **fields,
    )
    await session.commit()
    connection = (
        await connection_service.get(session, ctx.workspace_id, environment.connection_id)
        if environment.connection_id else None
    )
    return environment_service.view(
        environment,
        connection=(
            await connection_service.connector_display(session, connection)
            if connection else None
        ),
        user_id=ctx.user_id,
    )


# ── files ─────────────────────────────────────────────────────────────────


@router.get("/{project_id}/files", response_model=FileTreeView)
async def file_tree(project_id: uuid.UUID, session: SessionDep, ctx: CtxDep):
    project = await project_service.get(session, ctx, project_id)
    ctx.require(Module.TRANSFORMS, Action.VIEW)
    revision = await file_service.working_revision(session, project)
    return FileTreeView(
        revision_id=revision.id,
        revision_number=revision.revision_number,
        content_hash=revision.content_hash,
        tree=[_node(item) for item in file_service.file_tree(revision)],
        file_count=revision.file_count,
    )


def _node(item) -> dict[str, Any]:
    return {
        "name": item.name,
        "path": item.path,
        "type": item.type,
        "size": item.size,
        "is_text": item.is_text,
        "children": (
            [_node(child) for child in item.children] if item.children is not None else None
        ),
    }


@router.get("/{project_id}/files/content", response_model=FileContentView)
async def file_content(
    project_id: uuid.UUID,
    session: SessionDep,
    ctx: CtxDep,
    path: Annotated[str, Query(max_length=1000)],
    revision_id: Annotated[uuid.UUID | None, Query()] = None,
):
    project = await project_service.get(session, ctx, project_id)
    ctx.require(Module.TRANSFORMS, Action.VIEW)
    revision = (
        await file_service.get_revision(session, revision_id) if revision_id
        else await file_service.working_revision(session, project)
    )
    if revision.project_id != project.id:
        raise NotFoundError("That version does not belong to this project.")

    data, entry = await file_service.read_file(revision, path)
    if not entry.is_text:
        raise ValidationError(
            f"`{entry.path}` is not a text file. Download it instead.",
            code="TRANSFORM_FILE_NOT_TEXT",
        )

    # Which resource this file defines, so the editor can offer Preview and
    # Build against the right selector without the person having to know what
    # dbt named it.
    unique_id = resource_type = None
    bundle = await indexer.latest_bundle(session, project.id, scope="DRAFT")
    if bundle is not None:
        row = await session.scalar(select(TransformResourceIndex).where(
            TransformResourceIndex.bundle_id == bundle.id,
            TransformResourceIndex.original_file_path == entry.path,
        ).limit(1))
        if row is not None:
            unique_id, resource_type = row.unique_id, row.resource_type

    return FileContentView(
        path=entry.path,
        content=data.decode("utf-8", errors="replace"),
        size=entry.size,
        sha256=entry.sha256,
        is_text=entry.is_text,
        revision_id=revision.id,
        unique_id=unique_id,
        resource_type=resource_type,
    )


@router.get("/{project_id}/files/raw")
async def file_raw(
    project_id: uuid.UUID,
    session: SessionDep,
    ctx: CtxDep,
    path: Annotated[str, Query(max_length=1000)],
):
    """Download one file as-is, for anything the editor will not render."""
    project = await project_service.get(session, ctx, project_id)
    ctx.require(Module.TRANSFORMS, Action.VIEW)
    revision = await file_service.working_revision(session, project)
    data, entry = await file_service.read_file(revision, path)
    return StreamingResponse(
        iter([data]), media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{entry.name}"'},
    )


@router.put("/{project_id}/files/content", response_model=SaveResult)
async def save_file(
    project_id: uuid.UUID, payload: FileSaveRequest, session: SessionDep, ctx: CtxDep,
):
    project = await project_service.get(session, ctx, project_id)
    ctx.require(Module.TRANSFORMS, Action.EDIT)
    revision = await file_service.apply_changes(
        session, project,
        changes=[file_service.FileChange(
            path=payload.path, content=payload.content.encode("utf-8"),
        )],
        expected_revision_id=payload.expected_revision_id,
        actor_id=ctx.user_id,
    )
    parse = await _queue_parse(session, ctx, project)
    await session.commit()
    return _save_result(revision, [payload.path], parse)


@router.post("/{project_id}/files/batch", response_model=SaveResult)
async def save_batch(
    project_id: uuid.UUID, payload: FileBatchRequest, session: SessionDep, ctx: CtxDep,
):
    """Save All: one revision, one parse.

    Splitting a six-file save into six revisions would give six parses, six
    chances to catch the project half-updated, and a history nobody can read.
    """
    project = await project_service.get(session, ctx, project_id)
    ctx.require(Module.TRANSFORMS, Action.EDIT)
    revision = await file_service.apply_changes(
        session, project,
        changes=[
            file_service.FileChange(
                path=item.path,
                content=item.content.encode("utf-8") if item.content is not None else None,
                from_path=item.from_path,
            )
            for item in payload.changes
        ],
        expected_revision_id=payload.expected_revision_id,
        actor_id=ctx.user_id,
    )
    parse = await _queue_parse(session, ctx, project)
    await session.commit()
    return _save_result(revision, [item.path for item in payload.changes], parse)


@router.post(
    "/{project_id}/files", response_model=SaveResult, status_code=status.HTTP_201_CREATED,
)
async def create_file(
    project_id: uuid.UUID, payload: FileCreateRequest, session: SessionDep, ctx: CtxDep,
):
    project = await project_service.get(session, ctx, project_id)
    ctx.require(Module.TRANSFORMS, Action.EDIT)
    content = payload.content
    if payload.template:
        template = scaffold.TEMPLATES.get(payload.template)
        if template is None:
            raise ValidationError(
                "That template does not exist.", code="TRANSFORM_TEMPLATE_UNKNOWN",
            )
        content = content or template["content"]

    working = await file_service.working_revision(session, project)
    from app.transforms.runtime.workspace import validate_path

    safe = validate_path(payload.path)
    if safe.value in (working.manifest_index or {}):
        raise ValidationError(
            f"`{safe.value}` already exists.", code="TRANSFORM_FILE_EXISTS",
        )

    revision = await file_service.apply_changes(
        session, project,
        changes=[file_service.FileChange(
            path=safe.value, content=content.encode("utf-8"),
        )],
        expected_revision_id=payload.expected_revision_id,
        actor_id=ctx.user_id,
    )
    parse = await _queue_parse(session, ctx, project)
    await session.commit()
    return _save_result(revision, [safe.value], parse)


@router.post("/{project_id}/files/move", response_model=SaveResult)
async def move_file(
    project_id: uuid.UUID, payload: FileMoveRequest, session: SessionDep, ctx: CtxDep,
):
    project = await project_service.get(session, ctx, project_id)
    ctx.require(Module.TRANSFORMS, Action.EDIT)
    revision = await file_service.apply_changes(
        session, project,
        changes=[file_service.FileChange(
            path=payload.to_path, from_path=payload.from_path,
        )],
        expected_revision_id=payload.expected_revision_id,
        actor_id=ctx.user_id,
    )
    parse = await _queue_parse(session, ctx, project)
    await session.commit()
    return _save_result(revision, [payload.to_path], parse)


@router.post("/{project_id}/files/delete", response_model=SaveResult)
async def delete_files(
    project_id: uuid.UUID, payload: FileDeleteRequest, session: SessionDep, ctx: CtxDep,
):
    """Delete one or more files.

    A POST rather than a DELETE with a body: several HTTP clients and proxies
    drop a DELETE body, and losing the list of paths would turn a two-file
    delete into a request with no target.
    """
    project = await project_service.get(session, ctx, project_id)
    ctx.require(Module.TRANSFORMS, Action.EDIT)
    revision = await file_service.apply_changes(
        session, project,
        changes=[file_service.FileChange(path=path) for path in payload.paths],
        expected_revision_id=payload.expected_revision_id,
        actor_id=ctx.user_id,
    )
    parse = await _queue_parse(session, ctx, project)
    await session.commit()
    return _save_result(revision, payload.paths, parse)


@router.get("/{project_id}/file-templates", response_model=list[TemplateView])
async def file_templates(project_id: uuid.UUID, session: SessionDep, ctx: CtxDep):
    await project_service.get(session, ctx, project_id)
    ctx.require(Module.TRANSFORMS, Action.VIEW)
    return [
        TemplateView(key=key, label=item["label"], path=item["path"],
                     content=item["content"])
        for key, item in scaffold.TEMPLATES.items()
    ]


def _save_result(
    revision: TransformProjectRevision,
    paths: list[str],
    parse: TransformInvocation | None,
) -> SaveResult:
    return SaveResult(
        revision_id=revision.id,
        revision_number=revision.revision_number,
        content_hash=revision.content_hash,
        file_count=revision.file_count,
        saved_paths=paths,
        parse_invocation_id=parse.id if parse else None,
    )


async def _queue_parse(
    session: SessionDep, ctx: CtxDep, project: TransformProject,
) -> TransformInvocation | None:
    """Queue a parse of the working revision, coalescing with one in flight.

    Saving ten files in quick succession should cost one parse of the last
    revision, not ten.  A parse already queued for this project is cancelled in
    favour of the newer one, which is cheaper and gives a more useful answer.
    """
    from app.models.enums import RunStatus

    revision = await file_service.working_revision(session, project)
    environment = await environment_service.resolve(session, project, None)

    existing = list((await session.scalars(select(TransformInvocation).where(
        TransformInvocation.project_id == project.id,
        TransformInvocation.command == "parse",
        TransformInvocation.status == RunStatus.QUEUED,
    ))).all())
    for stale in existing:
        stale.status = RunStatus.CANCELLED
        stale.ended_at = utcnow()
        stale.queue_reason = "Superseded by a newer save."

    try:
        return await invocation_service.enqueue(
            session, ctx, project,
            command="parse",
            environment=environment,
            revision=revision,
            trigger_type=TriggerType.SYSTEM,
            enforce_permission=False,
        )
    except ValidationError:
        # A project with no environment yet, or a command the runtime refuses.
        # A save must still succeed: the parse is a convenience, and its absence
        # shows as an unknown parse status rather than a failed save.
        return None


# ── resources, lineage, docs ──────────────────────────────────────────────


async def _bundle_for(
    session: SessionDep,
    project: TransformProject,
    scope: str,
) -> TransformArtifactBundle | None:
    """The artifact bundle a read should answer from.

    DRAFT is the last parse of the working revision; RELEASE is the active
    release's own verified artifacts.  Keeping them apart is what stops a draft
    parse changing the graph production is running.
    """
    if scope == "RELEASE":
        release = await release_service.active(session, project)
        if release is None:
            return None
        return await release_service.release_bundle(session, release)
    return await indexer.latest_bundle(session, project.id, scope="DRAFT")


@router.get("/{project_id}/resources", response_model=ResourcePageView)
async def list_resources(
    project_id: uuid.UUID,
    session: SessionDep,
    ctx: CtxDep,
    resource_type: Annotated[list[str] | None, Query()] = None,
    search: Annotated[str | None, Query(max_length=200)] = None,
    tag: Annotated[str | None, Query()] = None,
    package: Annotated[str | None, Query()] = None,
    path: Annotated[str | None, Query()] = None,
    materialized: Annotated[str | None, Query()] = None,
    group: Annotated[str | None, Query()] = None,
    # Default on: a parsed manifest lists every macro dbt itself ships, which
    # is 477 rows against the 50 a bare project owns, and each one's path
    # points inside the installed package rather than the project. Set false
    # to see them -- the tree offers that as an explicit choice.
    own_only: Annotated[bool, Query()] = True,
    scope: Annotated[str, Query(pattern="^(DRAFT|RELEASE)$")] = "DRAFT",
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    project = await project_service.get(session, ctx, project_id)
    ctx.require(Module.TRANSFORMS, Action.VIEW)
    bundle = await _bundle_for(session, project, scope)
    if bundle is None:
        return ResourcePageView(items=[], total=0, counts={})
    page = await resource_service.list_resources(
        session, bundle.id,
        resource_types=resource_type, search=search, tag=tag, package=package,
        own_package=project.dbt_project_name if own_only else None,
        path_prefix=path, materialized=materialized, group=group,
        limit=limit, offset=offset,
    )
    return ResourcePageView(items=page.items, total=page.total, counts=page.counts)


@router.get("/{project_id}/resources/facets", response_model=FacetsView)
async def resource_facets(
    project_id: uuid.UUID,
    session: SessionDep,
    ctx: CtxDep,
    scope: Annotated[str, Query(pattern="^(DRAFT|RELEASE)$")] = "DRAFT",
):
    project = await project_service.get(session, ctx, project_id)
    ctx.require(Module.TRANSFORMS, Action.VIEW)
    bundle = await _bundle_for(session, project, scope)
    if bundle is None:
        return FacetsView()
    return FacetsView(**await resource_service.facets(session, bundle.id))


@router.get("/{project_id}/resources/detail", response_model=ResourceDetail)
async def resource_detail(
    project_id: uuid.UUID,
    session: SessionDep,
    ctx: CtxDep,
    unique_id: Annotated[str, Query(max_length=500)],
    scope: Annotated[str, Query(pattern="^(DRAFT|RELEASE)$")] = "DRAFT",
):
    project = await project_service.get(session, ctx, project_id)
    ctx.require(Module.TRANSFORMS, Action.VIEW)
    bundle = await _bundle_for(session, project, scope)
    if bundle is None:
        raise NotFoundError("This project has not been parsed yet.")

    catalog = await indexer.load_catalog(bundle)
    freshness = await indexer.load_sources(bundle)
    last = None
    if project.last_invocation_id:
        results = await resource_service.last_results(session, project.last_invocation_id)
        last = results.get(unique_id)

    return await resource_service.detail(
        session, bundle, unique_id,
        catalog=catalog, freshness=freshness, last_result=last,
    )


@router.get("/{project_id}/lineage", response_model=LineageView)
async def lineage(
    project_id: uuid.UUID,
    session: SessionDep,
    ctx: CtxDep,
    focus: Annotated[str | None, Query(max_length=500)] = None,
    upstream: Annotated[int, Query(ge=0, le=10)] = 2,
    downstream: Annotated[int, Query(ge=0, le=10)] = 2,
    full: Annotated[bool, Query()] = False,
    scope: Annotated[str, Query(pattern="^(DRAFT|RELEASE)$")] = "DRAFT",
    max_nodes: Annotated[int, Query(ge=10, le=5000)] = 400,
):
    """The dependency graph, from dbt's own parent/child maps.

    Defaults to a neighbourhood around `focus` because a 5,000-node graph
    rendered whole is unreadable and slow; `full=true` is the explicit choice.
    """
    project = await project_service.get(session, ctx, project_id)
    ctx.require(Module.TRANSFORMS, Action.VIEW)
    bundle = await _bundle_for(session, project, scope)
    if bundle is None:
        return LineageView(scope=scope)
    graph = await resource_service.lineage(
        session, bundle.id,
        focus=None if full else focus,
        upstream_depth=upstream, downstream_depth=downstream,
        max_nodes=max_nodes,
    )
    return LineageView(
        nodes=graph.nodes, edges=graph.edges, truncated=graph.truncated,
        total_nodes=graph.total_nodes, scope=scope,
    )


@router.get("/{project_id}/docs", response_model=list[DocEntry])
async def docs(
    project_id: uuid.UUID,
    session: SessionDep,
    ctx: CtxDep,
    search: Annotated[str | None, Query(max_length=200)] = None,
    resource_type: Annotated[list[str] | None, Query()] = None,
    scope: Annotated[str, Query(pattern="^(DRAFT|RELEASE)$")] = "DRAFT",
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
):
    """Documentation, rendered natively from manifest plus catalog."""
    project = await project_service.get(session, ctx, project_id)
    ctx.require(Module.TRANSFORMS, Action.VIEW)
    bundle = await _bundle_for(session, project, scope)
    if bundle is None:
        return []
    catalog = await indexer.load_catalog(bundle)
    page = await resource_service.list_resources(
        session, bundle.id,
        resource_types=resource_type or ["model", "source", "seed", "snapshot"],
        search=search, limit=limit,
    )
    entries: list[DocEntry] = []
    for item in page.items:
        detail = await resource_service.detail(
            session, bundle, item["unique_id"], catalog=catalog,
        )
        entries.append(DocEntry(
            unique_id=detail["unique_id"],
            name=detail["name"],
            resource_type=detail["resource_type"],
            description=detail.get("description"),
            path=detail.get("path"),
            relation_name=detail.get("relation_name"),
            columns=detail.get("columns") or [],
            tags=detail.get("tags") or [],
            group=detail.get("group"),
            tests=[test["name"] for test in detail.get("tests") or []],
            parents=detail.get("parents") or [],
            children=detail.get("children") or [],
        ))
    return entries


@router.get("/{project_id}/completions", response_model=CompletionsView)
async def completions(project_id: uuid.UUID, session: SessionDep, ctx: CtxDep):
    """What the editor offers inside `ref()`, `source()` and a YAML test list.

    Sourced from the last parse rather than from a regex over open files, so a
    model contributed by a package is offered too.
    """
    project = await project_service.get(session, ctx, project_id)
    ctx.require(Module.TRANSFORMS, Action.VIEW)
    bundle = await indexer.latest_bundle(session, project.id, scope="DRAFT")
    if bundle is None:
        return CompletionsView()

    rows = list((await session.scalars(select(TransformResourceIndex).where(
        TransformResourceIndex.bundle_id == bundle.id,
    ))).all())

    refs, sources, macros, tests = [], [], [], []
    columns: dict[str, list[str]] = {}
    for row in rows:
        if row.resource_type in ("model", "seed", "snapshot"):
            refs.append({
                "label": row.name, "kind": "ref",
                "detail": f"{row.resource_type} · {row.materialized or ''}".strip(" ·"),
                "insert_text": f"{{{{ ref('{row.name}') }}}}",
            })
            names = [
                str(column.get("name")) for column in (row.columns_json or [])
                if column.get("name")
            ]
            if names:
                columns[row.name] = names
        elif row.resource_type == "source":
            # A source is named by two parts; offering only the table would
            # produce a `source()` call that does not resolve.
            source_name = (row.config_json or {}).get("source_name") or row.schema_name
            refs_label = f"{source_name}.{row.name}"
            sources.append({
                "label": refs_label, "kind": "source",
                "detail": row.relation_name,
                "insert_text": f"{{{{ source('{source_name}', '{row.name}') }}}}",
            })
        elif row.resource_type == "macro":
            macros.append({
                "label": row.name, "kind": "macro",
                "detail": row.package_name,
                "insert_text": f"{{{{ {row.name}() }}}}",
            })
        elif row.resource_type == "test":
            # Every test in the manifest, not four built-ins: a package's tests
            # are as available to the author as dbt's own.
            metadata = (row.config_json or {}).get("test_name")
            if metadata:
                tests.append({"label": str(metadata), "kind": "test"})

    builtin = [
        {"label": name, "kind": "test", "detail": "dbt built-in"}
        for name in ("not_null", "unique", "accepted_values", "relationships")
    ]
    seen = {item["label"] for item in tests}
    tests.extend(item for item in builtin if item["label"] not in seen)

    return CompletionsView(
        refs=refs, sources=sources, macros=macros, tests=tests, columns=columns,
    )


@router.get("/{project_id}/search", response_model=SearchView)
async def search(
    project_id: uuid.UUID,
    session: SessionDep,
    ctx: CtxDep,
    q: Annotated[str, Query(min_length=1, max_length=200)],
    include_content: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
):
    """Quick open and project-wide search.

    Filenames and resource names are matched from the index, which is instant.
    Content search reads the files, so it is opt-in -- a project with 5,000
    files should not fetch all of them because somebody typed one character.
    """
    project = await project_service.get(session, ctx, project_id)
    ctx.require(Module.TRANSFORMS, Action.VIEW)
    needle = q.strip().lower()
    revision = await file_service.working_revision(session, project)

    hits: list[dict[str, Any]] = []
    for entry in file_service.list_files(revision):
        if needle in entry.path.lower():
            hits.append({
                "kind": "file", "label": entry.name, "detail": entry.path,
                "path": entry.path,
            })
        if len(hits) >= limit:
            break

    bundle = await indexer.latest_bundle(session, project.id, scope="DRAFT")
    if bundle is not None and len(hits) < limit:
        page = await resource_service.list_resources(
            session, bundle.id, search=needle, limit=limit - len(hits),
        )
        hits.extend(
            {
                "kind": "resource", "label": item["name"],
                "detail": f"{item['resource_type']} · {item.get('path') or ''}".strip(" ·"),
                "path": item.get("path"), "unique_id": item["unique_id"],
            }
            for item in page.items
        )

    truncated = False
    if include_content and len(hits) < limit:
        store = object_store()
        for entry in file_service.list_files(revision):
            if len(hits) >= limit:
                truncated = True
                break
            if not entry.is_text or entry.size > 400_000:
                continue
            data, _ = await file_service.read_file(revision, entry.path, store=store)
            text = data.decode("utf-8", errors="replace")
            for number, line in enumerate(text.splitlines(), start=1):
                if needle in line.lower():
                    hits.append({
                        "kind": "file", "label": entry.name, "detail": entry.path,
                        "path": entry.path, "line": number, "excerpt": line.strip()[:200],
                    })
                    break
    return SearchView(hits=hits[:limit], truncated=truncated or len(hits) > limit)


@router.get("/{project_id}/problems", response_model=ProblemsView)
async def problems(project_id: uuid.UUID, session: SessionDep, ctx: CtxDep):
    """Everything currently wrong, in one list.

    Parse errors, failed nodes and failed tests arrive in one shape because the
    panel is one panel and somebody triaging does not care which subsystem
    noticed.
    """
    project = await project_service.get(session, ctx, project_id)
    ctx.require(Module.TRANSFORMS, Action.VIEW)
    found: list[dict[str, Any]] = []

    if project.parse_status == "ERROR" and project.parse_error:
        location = {}
        last = (
            await session.get(TransformInvocation, project.last_invocation_id)
            if project.last_invocation_id else None
        )
        if last is not None:
            location = (last.technical_metadata or {}).get("error_location") or {}
        found.append({
            "severity": "error", "source": "parse",
            "message": project.parse_error,
            "path": location.get("path"),
            "line": location.get("line"),
            "resource_name": location.get("name"),
        })

    if project.last_invocation_id:
        from app.transforms.models import TransformInvocationNode

        nodes = list((await session.scalars(select(TransformInvocationNode).where(
            TransformInvocationNode.invocation_id == project.last_invocation_id,
        ))).all())
        for node in nodes:
            status_text = node.status.lower()
            if status_text in ("error", "fail", "runtime error", "warn"):
                found.append({
                    "severity": "warning" if status_text == "warn" else "error",
                    "source": "test" if node.resource_type in ("test", "unit_test") else "run",
                    "message": node.message or f"{node.name} {node.status}",
                    "path": None,
                    "line": (node.error_location or {}).get("line"),
                    "unique_id": node.unique_id,
                    "resource_name": node.name,
                })

    # Resolve a file path for anything that named a resource but not a file, so
    # every row in the panel can be clicked through to a line in the editor.
    bundle = await indexer.latest_bundle(session, project.id, scope="DRAFT")
    if bundle is not None:
        for problem in found:
            if problem.get("path") or not problem.get("unique_id"):
                continue
            row = await session.scalar(select(TransformResourceIndex).where(
                TransformResourceIndex.bundle_id == bundle.id,
                TransformResourceIndex.unique_id == problem["unique_id"],
            ))
            if row is not None:
                problem["path"] = row.original_file_path

    return ProblemsView(
        problems=found,
        parse_status=project.parse_status,
        checked_at=project.last_parsed_at,
    )


@router.get("/{project_id}/compiled", response_model=CompiledView)
async def compiled(
    project_id: uuid.UUID,
    session: SessionDep,
    ctx: CtxDep,
    unique_id: Annotated[str, Query(max_length=500)],
):
    """The SQL dbt would actually send to the warehouse."""
    project = await project_service.get(session, ctx, project_id)
    ctx.require(Module.TRANSFORMS, Action.VIEW)
    bundle = await indexer.latest_bundle(session, project.id, scope="DRAFT")
    if bundle is None:
        raise NotFoundError("This project has not been compiled yet.")
    document = await indexer.load_raw(bundle.manifest_storage_key)
    if not document:
        raise NotFoundError("The compiled output for this version is no longer stored.")
    node = (document.get("nodes") or {}).get(unique_id)
    if not isinstance(node, dict):
        raise NotFoundError("That resource was not in the last compile.")
    return CompiledView(
        unique_id=unique_id,
        compiled_code=node.get("compiled_code"),
        raw_code=node.get("raw_code"),
    )


# ── invocations ───────────────────────────────────────────────────────────


@router.post(
    "/{project_id}/invocations", response_model=InvocationView,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_invocation(
    project_id: uuid.UUID,
    payload: InvocationRequest,
    session: SessionDep,
    ctx: CtxDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    """Run one dbt command.

    A structured command, never a shell string.  The selector is passed to dbt
    verbatim because dbt's node selection is dbt's to interpret; everything else
    is a typed field validated before anything is queued.
    """
    project = await project_service.get(session, ctx, project_id)

    if payload.source == "RELEASE":
        release = await release_service.active(session, project)
        if release is None:
            raise ValidationError(
                "Nothing has been published, so there is no live version to run.",
                code="TRANSFORM_NO_ACTIVE_RELEASE",
            )
        revision = await session.get(TransformProjectRevision, release.revision_id)
        environment = await environment_service.resolve(
            session, project, payload.environment_id,
            default_to=environment_service.PRODUCTION,
        )
    else:
        release = None
        revision = await file_service.working_revision(session, project)
        environment = await environment_service.resolve(
            session, project, payload.environment_id,
        )

    invocation = await invocation_service.enqueue(
        session, ctx, project,
        command=payload.command,
        environment=environment,
        revision=revision,
        selector=payload.selector,
        exclude=payload.exclude,
        full_refresh=payload.full_refresh,
        limit=payload.limit or settings.transform_preview_limit,
        macro=payload.macro,
        macro_args=payload.macro_args,
        selector_name=payload.selector_name,
        vars=payload.vars,
        release=release,
        idempotency_key=idempotency_key,
    )
    await session.commit()
    return await _invocation_view(session, ctx, invocation)


@router.get("/{project_id}/invocations", response_model=Paginated[InvocationView])
async def list_invocations(
    project_id: uuid.UUID,
    session: SessionDep,
    ctx: CtxDep,
    command: Annotated[list[str] | None, Query()] = None,
    invocation_status: Annotated[list[str] | None, Query(alias="status")] = None,
    environment_id: Annotated[uuid.UUID | None, Query()] = None,
    trigger_type: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    project = await project_service.get(session, ctx, project_id)
    ctx.require(Module.TRANSFORMS, Action.VIEW)
    from sqlalchemy import func

    base = select(TransformInvocation).where(
        TransformInvocation.project_id == project.id,
    )
    if command:
        base = base.where(TransformInvocation.command.in_(command))
    if invocation_status:
        base = base.where(TransformInvocation.status.in_(invocation_status))
    if environment_id:
        base = base.where(TransformInvocation.environment_id == environment_id)
    if trigger_type:
        base = base.where(TransformInvocation.trigger_type == trigger_type)

    total = await session.scalar(
        select(func.count()).select_from(base.subquery())
    ) or 0
    rows = list((await session.scalars(
        base.order_by(TransformInvocation.created_at.desc()).limit(limit).offset(offset)
    )).all())
    return Paginated[InvocationView](
        items=[await _invocation_view(session, ctx, row) for row in rows],
        page=PageInfo(
            has_more=offset + len(rows) < total, total=int(total),
            limit=limit, offset=offset,
        ),
    )


@router.get("/{project_id}/publish-plan", response_model=PublishPlanView)
async def publish_plan(project_id: uuid.UUID, session: SessionDep, ctx: CtxDep):
    project = await project_service.get(session, ctx, project_id)
    ctx.require(Module.TRANSFORMS, Action.VIEW)
    result = await release_service.plan(session, project)
    return PublishPlanView(
        files=[
            {"path": item.path, "change": item.change,
             "size_before": item.size_before, "size_after": item.size_after}
            for item in result.files
        ],
        affected_resources=result.affected_resources,
        downstream_resources=result.downstream_resources,
        draft_hash=result.draft_hash,
        live_hash=result.live_hash,
        matches_live=result.matches_live,
    )


# ── releases ──────────────────────────────────────────────────────────────


@router.post(
    "/{project_id}/releases", response_model=PublishResult,
    status_code=status.HTTP_201_CREATED,
)
async def publish_release(
    project_id: uuid.UUID, payload: ReleaseCreate, session: SessionDep, ctx: CtxDep,
):
    """Freeze the working version and start proving it runs."""
    project = await project_service.get(session, ctx, project_id)
    release, invocation = await release_service.publish(
        session, ctx, project, notes=payload.notes, activate=payload.activate,
    )
    await session.commit()
    return PublishResult(
        release=await release_service.view(session, project, release),
        verification_invocation_id=invocation.id,
    )


@router.get("/{project_id}/releases", response_model=list[ReleaseView])
async def list_releases(project_id: uuid.UUID, session: SessionDep, ctx: CtxDep):
    project = await project_service.get(session, ctx, project_id)
    ctx.require(Module.TRANSFORMS, Action.VIEW)
    return [
        await release_service.view(session, project, item)
        for item in await release_service.list_all(session, project)
    ]


@router.post(
    "/{project_id}/releases/{release_id}/activate", response_model=ReleaseView,
)
async def activate_release(
    project_id: uuid.UUID, release_id: uuid.UUID, session: SessionDep, ctx: CtxDep,
):
    project = await project_service.get(session, ctx, project_id)
    release = await release_service.activate(session, ctx, project, release_id)
    await session.commit()
    return await release_service.view(session, project, release)


@router.post(
    "/{project_id}/releases/{release_id}/restore", response_model=ProjectDetail,
)
async def restore_release(
    project_id: uuid.UUID, release_id: uuid.UUID, session: SessionDep, ctx: CtxDep,
):
    """Put a published version's files back in the editor.

    The release stays live; this changes what is being edited, not what is
    running.
    """
    project = await project_service.get(session, ctx, project_id)
    await release_service.restore(session, ctx, project, release_id)
    await _queue_parse(session, ctx, project)
    await session.commit()
    return await project_service.detail(
        session, ctx, await project_service.get(session, ctx, project_id),
    )


# ── git ───────────────────────────────────────────────────────────────────


@router.get("/{project_id}/git/status", response_model=GitStatusView)
async def git_status(
    project_id: uuid.UUID,
    session: SessionDep,
    ctx: CtxDep,
    check_remote: Annotated[bool, Query()] = False,
):
    project = await project_service.get(session, ctx, project_id)
    ctx.require(Module.TRANSFORMS, Action.VIEW)
    state = await git_service.status(session, project, check_remote=check_remote)
    await session.commit()
    return GitStatusView(
        branch=state.branch, repo_url=state.repo_url, subdirectory=state.subdirectory,
        head_commit_sha=state.head_commit_sha, remote_commit_sha=state.remote_commit_sha,
        behind=state.behind,
        changes=[
            {"path": item.path, "change": item.change,
             "size_before": item.size_before, "size_after": item.size_after}
            for item in state.changes
        ],
        last_pulled_at=state.last_pulled_at, last_status=state.last_status,
        last_message=state.last_message, auto_pull=state.auto_pull,
        interval_minutes=state.interval_minutes,
    )


@router.get("/{project_id}/git/diff", response_model=GitDiffView)
async def git_diff(
    project_id: uuid.UUID,
    session: SessionDep,
    ctx: CtxDep,
    path: Annotated[str, Query(max_length=1000)],
):
    project = await project_service.get(session, ctx, project_id)
    ctx.require(Module.TRANSFORMS, Action.VIEW)
    return await git_service.diff_file(session, project, path)


@router.post("/{project_id}/git/pull")
async def git_pull(
    project_id: uuid.UUID, payload: GitPullRequest, session: SessionDep, ctx: CtxDep,
):
    project = await project_service.get(session, ctx, project_id)
    result = await git_service.pull(
        session, ctx, project,
        force=payload.force, discard_local=payload.discard_local,
    )
    if result.get("changed"):
        await _queue_parse(session, ctx, project)
    await session.commit()
    return result


@router.post("/{project_id}/git/commit")
async def git_commit(
    project_id: uuid.UUID, payload: GitCommitRequest, session: SessionDep, ctx: CtxDep,
):
    """Commit the working version and push it to the branch."""
    project = await project_service.get(session, ctx, project_id)
    result = await git_service.commit_and_push(
        session, ctx, project, message=payload.message, paths=payload.paths,
    )
    await session.commit()
    return result


@router.get("/{project_id}/git/branches", response_model=list[BranchView])
async def git_branches(project_id: uuid.UUID, session: SessionDep, ctx: CtxDep):
    project = await project_service.get(session, ctx, project_id)
    ctx.require(Module.TRANSFORMS, Action.VIEW)
    return await git_service.list_branches(session, project)


@router.post("/{project_id}/git/checkout")
async def git_checkout(
    project_id: uuid.UUID, payload: GitCheckoutRequest, session: SessionDep, ctx: CtxDep,
):
    project = await project_service.get(session, ctx, project_id)
    result = await git_service.checkout(
        session, ctx, project,
        branch=payload.branch, discard_local=payload.discard_local,
    )
    await _queue_parse(session, ctx, project)
    await session.commit()
    return result


@router.put("/{project_id}/git", response_model=GitStatusView)
async def configure_git(
    project_id: uuid.UUID, payload: GitConfigureRequest, session: SessionDep, ctx: CtxDep,
):
    project = await project_service.get(session, ctx, project_id)
    await git_service.configure(
        session, ctx, project, **payload.model_dump(exclude_unset=True),
    )
    await session.commit()
    return await git_status(project_id, session, ctx, check_remote=False)


# ── invocation detail (project-independent) ───────────────────────────────

invocation_router = APIRouter(prefix="/transform-invocations", tags=["transforms"])


@invocation_router.get("/{invocation_id}", response_model=InvocationDetail)
async def invocation_detail(
    invocation_id: uuid.UUID, session: SessionDep, ctx: CtxDep,
):
    invocation = await invocation_service.get(session, ctx, invocation_id)
    ctx.require(Module.TRANSFORMS, Action.VIEW)
    base = await _invocation_view(session, ctx, invocation)
    return InvocationDetail(
        **base.model_dump(),
        nodes=[
            {
                "unique_id": node.unique_id, "name": node.name,
                "resource_type": node.resource_type, "status": node.status,
                "execution_time": node.execution_time,
                "relation_name": node.relation_name, "message": node.message,
                "rows_affected": node.rows_affected,
                "bytes_processed": node.bytes_processed,
                "failures": node.failures,
                "error_location": node.error_location or {},
            }
            for node in sorted(
                invocation.nodes,
                key=lambda item: (item.status.lower() not in ("error", "fail"), item.name),
            )
        ],
        preview=(invocation.technical_metadata or {}).get("preview"),
    )


@invocation_router.post("/{invocation_id}/cancel", response_model=InvocationView)
async def cancel_invocation(
    invocation_id: uuid.UUID, session: SessionDep, ctx: CtxDep,
):
    invocation = await invocation_service.get(session, ctx, invocation_id)
    await invocation_service.request_cancel(session, ctx, invocation)
    await session.commit()
    return await _invocation_view(session, ctx, invocation)


@invocation_router.post(
    "/{invocation_id}/retry", response_model=InvocationView,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_invocation(
    invocation_id: uuid.UUID, session: SessionDep, ctx: CtxDep,
):
    """Re-run exactly what ran before: same revision, release and command."""
    invocation = await invocation_service.get(session, ctx, invocation_id)
    created = await invocation_service.retry(session, ctx, invocation)
    await session.commit()
    return await _invocation_view(session, ctx, created)


@invocation_router.get("/{invocation_id}/logs", response_model=LogPage)
async def invocation_logs(
    invocation_id: uuid.UUID,
    session: SessionDep,
    ctx: CtxDep,
    cursor: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
):
    invocation = await invocation_service.get(session, ctx, invocation_id)
    ctx.require(Module.TRANSFORMS, Action.VIEW)
    lines, next_cursor, has_more, total = await executor.fetch_logs(
        session, invocation, cursor=cursor, limit=limit,
    )
    return LogPage(
        invocation_id=invocation_id, lines=lines, next_cursor=next_cursor,
        has_more=has_more, total_lines=total,
    )


async def _invocation_view(
    session: SessionDep, ctx: CtxDep, invocation: TransformInvocation,
) -> InvocationView:
    project = await session.get(TransformProject, invocation.project_id)
    environment = await session.get(TransformEnvironment, invocation.environment_id)
    revision = await session.get(TransformProjectRevision, invocation.revision_id)
    release = (
        await session.get(TransformRelease, invocation.release_id)
        if invocation.release_id else None
    )

    duration = None
    if invocation.started_at:
        duration = ((invocation.ended_at or utcnow()) - invocation.started_at).total_seconds()

    reference = invocation.heartbeat_at or invocation.started_at or invocation.created_at
    is_stale = (
        invocation.status.is_active
        and (utcnow() - reference).total_seconds() > settings.stale_run_seconds
    )
    can_operate = ctx.can(Module.TRANSFORMS, Action.OPERATE)
    metadata = invocation.technical_metadata or {}

    return InvocationView(
        id=invocation.id,
        project_id=invocation.project_id,
        project_name=project.name if project else None,
        command=invocation.command,
        selector=invocation.selector,
        exclude=invocation.exclude,
        args=invocation.args_json or {},
        status=invocation.status.value,
        environment_id=invocation.environment_id,
        environment_name=environment.name if environment else None,
        revision_id=invocation.revision_id,
        revision_number=revision.revision_number if revision else None,
        release_id=invocation.release_id,
        release_number=release.release_number if release else None,
        trigger_type=invocation.trigger_type.value,
        triggered_by=invocation.triggered_by,
        queue_reason=invocation.queue_reason,
        started_at=invocation.started_at,
        ended_at=invocation.ended_at,
        created_at=invocation.created_at,
        duration_seconds=duration,
        nodes_total=invocation.nodes_total,
        nodes_succeeded=invocation.nodes_succeeded,
        nodes_failed=invocation.nodes_failed,
        nodes_skipped=invocation.nodes_skipped,
        tests_passed=invocation.tests_passed,
        tests_failed=invocation.tests_failed,
        tests_warned=invocation.tests_warned,
        rows_affected=invocation.rows_affected,
        error_code=invocation.error_code,
        error_summary=invocation.error_summary,
        error_location=metadata.get("error_location") or {},
        technical_message=metadata.get("technical_message"),
        remediation_action=invocation.remediation_action,
        exit_code=invocation.exit_code,
        dbt_invocation_id=invocation.dbt_invocation_id,
        is_stale=is_stale,
        actions={
            "can_cancel": can_operate and invocation.status.is_active
                          and invocation.status.value != "CANCEL_REQUESTED",
            "can_retry": can_operate and invocation.status.value in (
                "FAILED", "FAILED_TO_START", "CANCELLED", "TIMED_OUT",
            ),
            "can_view_logs": True,
        },
    )
