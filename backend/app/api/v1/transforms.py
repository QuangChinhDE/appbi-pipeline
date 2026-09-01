"""AppBI Transform API. dbt process details stay behind the service boundary."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Header, Query, Response, status
from fastapi.responses import StreamingResponse

from app.api.deps import CtxDep, SessionDep
from app.schemas.common import PageInfo, Paginated
from app.schemas.domain import (
    DataAssetRegister, DataAssetView, TransformCreate, TransformDestinationCapability,
    TransformDetail, TransformExecutionView, TransformInputCandidates, TransformLineage,
    TransformModelCreate, TransformModelUpdate, TransformModelView,
    TransformDraftRequest, TransformReleaseCreate, TransformReleaseView,
    TransformRunRequest, WarehouseBrowseView,
    RepositoryImportCreate, RepositoryImportPreview, RepositoryImportRequest,
    RepositoryImportResult, GitSourceUpdate, GitSourceView, GitPullResult,
    TransformTestCreate, TransformTestView, TransformUpdate, TransformView,
)
from app.services import transform_ai, transform_import, transforms as service

router = APIRouter(prefix="/transforms", tags=["transforms"])


@router.post("/imports/inspect", response_model=RepositoryImportPreview)
async def inspect_repository(payload: RepositoryImportRequest, ctx: CtxDep):
    """Read a dbt or Dataform repository and report what it would become.

    Nothing is created. A conversion that silently dropped a macro or a JS block
    would be worse than no conversion at all, so the warnings this returns are
    the point of the step, not a footnote to it.
    """
    plan = await transform_import.inspect(
        ctx, repo_url=payload.repo_url, ref=payload.ref,
        subdirectory=payload.subdirectory, token=payload.token,
    )
    return plan


@router.post(
    "/imports", response_model=RepositoryImportResult,
    status_code=status.HTTP_201_CREATED,
)
async def import_repository(
    payload: RepositoryImportCreate, session: SessionDep, ctx: CtxDep,
):
    transform, warnings = await transform_import.create_from_repository(
        session, ctx,
        repo_url=payload.repo_url, ref=payload.ref, subdirectory=payload.subdirectory,
        token=payload.token, name=payload.name,
        destination_id=payload.destination_id, default_schema=payload.default_schema,
    )
    # Remember the repository so the import is a starting point rather than a
    # snapshot -- the token included, since polling needs it after this request.
    await transform_import.configure_git(
        session, ctx, transform,
        repo_url=payload.repo_url, ref=payload.ref,
        subdirectory=payload.subdirectory, token=payload.token,
        auto_pull=payload.auto_pull, interval_minutes=payload.interval_minutes,
    )
    await session.commit()
    session.expunge(transform)
    fresh = await service.get(session, ctx, transform.id)
    return {"transform": await service.detail(session, ctx, fresh), "warnings": warnings}


@router.get("/destinations", response_model=list[TransformDestinationCapability])
async def destinations(session: SessionDep, ctx: CtxDep):
    return await service.destination_capabilities(session, ctx)


@router.get(
    "/destinations/{destination_id}/inputs", response_model=TransformInputCandidates,
)
async def candidates(destination_id: uuid.UUID, session: SessionDep, ctx: CtxDep):
    return await service.input_candidates(session, ctx, destination_id)


@router.get(
    "/destinations/{destination_id}/warehouse", response_model=WarehouseBrowseView,
)
async def browse_warehouse(
    destination_id: uuid.UUID,
    session: SessionDep,
    ctx: CtxDep,
    schema: Annotated[str | None, Query(max_length=200)] = None,
):
    """Datasets in the warehouse, or the tables inside one of them.

    Separate from `/inputs`, which answers "what has AppBI already loaded here".
    This one answers "what is actually in there", which is the question a user
    with a hand-built dataset is asking.
    """
    return await service.browse_warehouse(session, ctx, destination_id, schema)


@router.post(
    "/destinations/{destination_id}/assets", response_model=DataAssetView,
    status_code=status.HTTP_201_CREATED,
)
async def register_asset(
    destination_id: uuid.UUID,
    payload: DataAssetRegister,
    session: SessionDep,
    ctx: CtxDep,
):
    asset = await service.register_asset(session, ctx, destination_id, payload)
    await session.commit()
    return await service._asset_view(session, asset)


@router.get("/runs/{run_id}", response_model=TransformExecutionView)
async def execution(run_id: uuid.UUID, session: SessionDep, ctx: CtxDep):
    run = await service.get_run(session, ctx, run_id)
    return await service.execution_view(session, run)


@router.post("/runs/{run_id}/cancel", response_model=TransformExecutionView)
async def cancel(run_id: uuid.UUID, session: SessionDep, ctx: CtxDep):
    run = await service.get_run(session, ctx, run_id)
    await service.request_cancel(session, ctx, run)
    await session.commit()
    return await service.execution_view(session, run)


@router.post(
    "/runs/{run_id}/retry", response_model=TransformExecutionView,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry(run_id: uuid.UUID, session: SessionDep, ctx: CtxDep):
    run = await service.get_run(session, ctx, run_id)
    created = await service.retry_run(session, ctx, run)
    await session.commit()
    return await service.execution_view(session, created)


@router.get("/runs/{run_id}/logs")
async def logs(
    run_id: uuid.UUID,
    session: SessionDep,
    ctx: CtxDep,
    cursor: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
):
    lines, next_cursor, has_more, total = await service.fetch_logs(
        session, ctx, run_id, cursor=cursor, limit=limit,
    )
    return {
        "run_id": run_id, "lines": lines, "next_cursor": next_cursor,
        "has_more": has_more, "total_lines": total,
    }


@router.get("", response_model=Paginated[TransformView])
async def list_transforms(
    session: SessionDep,
    ctx: CtxDep,
    search: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    rows, total = await service.list_transforms(
        session, ctx, search=search, limit=limit, offset=offset,
    )
    return Paginated[TransformView](
        items=[await service.present(session, ctx, row) for row in rows],
        page=PageInfo(
            has_more=offset + len(rows) < total, total=total, limit=limit, offset=offset,
        ),
    )


@router.post("", response_model=TransformDetail, status_code=status.HTTP_201_CREATED)
async def create_transform(payload: TransformCreate, session: SessionDep, ctx: CtxDep):
    transform = await service.create(session, ctx, payload)
    await session.commit()
    transform = await service.get(session, ctx, transform.id)
    return await service.detail(session, ctx, transform)


@router.get("/{transform_id}", response_model=TransformDetail)
async def transform_detail(transform_id: uuid.UUID, session: SessionDep, ctx: CtxDep):
    transform = await service.get(session, ctx, transform_id)
    return await service.detail(session, ctx, transform)


@router.patch("/{transform_id}", response_model=TransformDetail)
async def update_transform(
    transform_id: uuid.UUID, payload: TransformUpdate, session: SessionDep, ctx: CtxDep,
):
    transform = await service.get(session, ctx, transform_id)
    await service.update(session, ctx, transform, payload)
    await session.commit()
    # Replacing the inputs leaves the loaded `inputs` collection stale, and the
    # detail view walks it -- drop the instance and re-read so the relationships
    # come back eagerly instead of lazy-loading inside the async request.
    session.expunge(transform)
    return await service.detail(session, ctx, await service.get(session, ctx, transform_id))


@router.delete("/{transform_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_transform(
    transform_id: uuid.UUID, session: SessionDep, ctx: CtxDep,
) -> Response:
    transform = await service.get(session, ctx, transform_id)
    await service.remove(session, ctx, transform)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{transform_id}/models", response_model=TransformModelView,
    status_code=status.HTTP_201_CREATED,
)
async def create_model(
    transform_id: uuid.UUID,
    payload: TransformModelCreate,
    session: SessionDep,
    ctx: CtxDep,
):
    transform = await service.get(session, ctx, transform_id)
    model = await service.create_model(session, ctx, transform, payload)
    await session.commit()
    model = await service._model(session, transform, model.id)
    return service._model_view(model)


@router.patch("/{transform_id}/models/{model_id}", response_model=TransformModelView)
async def update_model(
    transform_id: uuid.UUID,
    model_id: uuid.UUID,
    payload: TransformModelUpdate,
    session: SessionDep,
    ctx: CtxDep,
):
    transform = await service.get(session, ctx, transform_id)
    model = await service.update_model(session, ctx, transform, model_id, payload)
    await session.commit()
    # Re-read after the commit: the view walks `model.tests`, and a relationship
    # that was not already loaded would otherwise lazy-load inside the async
    # request and raise MissingGreenlet.
    return service._model_view(await service._model(session, transform, model.id))


@router.delete(
    "/{transform_id}/models/{model_id}", status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_model(
    transform_id: uuid.UUID, model_id: uuid.UUID, session: SessionDep, ctx: CtxDep,
) -> Response:
    transform = await service.get(session, ctx, transform_id)
    await service.remove_model(session, ctx, transform, model_id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{transform_id}/models/{model_id}/tests", response_model=TransformTestView,
    status_code=status.HTTP_201_CREATED,
)
async def add_test(
    transform_id: uuid.UUID,
    model_id: uuid.UUID,
    payload: TransformTestCreate,
    session: SessionDep,
    ctx: CtxDep,
):
    transform = await service.get(session, ctx, transform_id)
    test = await service.add_test(session, ctx, transform, model_id, payload)
    await session.commit()
    return service._test_view(test)


@router.delete(
    "/{transform_id}/models/{model_id}/tests/{test_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_test(
    transform_id: uuid.UUID,
    model_id: uuid.UUID,
    test_id: uuid.UUID,
    session: SessionDep,
    ctx: CtxDep,
) -> Response:
    transform = await service.get(session, ctx, transform_id)
    await service.remove_test(session, ctx, transform, model_id, test_id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{transform_id}/runs", response_model=TransformExecutionView,
    status_code=status.HTTP_202_ACCEPTED,
)
async def run_transform(
    transform_id: uuid.UUID,
    payload: TransformRunRequest,
    session: SessionDep,
    ctx: CtxDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    transform = await service.get(session, ctx, transform_id)
    run = await service.enqueue(
        session, ctx, transform, operation=payload.operation,
        model_id=payload.model_id, full_refresh=payload.full_refresh,
        source=payload.source, idempotency_key=idempotency_key,
    )
    await session.commit()
    return await service.execution_view(session, run)


@router.post(
    "/{transform_id}/releases", response_model=TransformReleaseView,
    status_code=status.HTTP_201_CREATED,
)
async def publish_release(
    transform_id: uuid.UUID,
    payload: TransformReleaseCreate,
    session: SessionDep,
    ctx: CtxDep,
):
    transform = await service.get(session, ctx, transform_id)
    release = await service.publish_release(
        session, ctx, transform, notes=payload.notes, activate=payload.activate,
    )
    await session.commit()
    return await service.release_view(session, transform, release)


@router.get("/{transform_id}/releases", response_model=list[TransformReleaseView])
async def list_releases(transform_id: uuid.UUID, session: SessionDep, ctx: CtxDep):
    transform = await service.get(session, ctx, transform_id)
    releases = await service.list_releases(session, transform)
    return [await service.release_view(session, transform, item) for item in releases]


@router.get("/{transform_id}/releases/{release_id}/models")
async def release_models(
    transform_id: uuid.UUID, release_id: uuid.UUID, session: SessionDep, ctx: CtxDep,
):
    transform = await service.get(session, ctx, transform_id)
    return {"models": await service.release_models(session, transform, release_id)}


@router.post(
    "/{transform_id}/releases/{release_id}/restore", response_model=TransformDetail,
)
async def restore_release(
    transform_id: uuid.UUID, release_id: uuid.UUID, session: SessionDep, ctx: CtxDep,
):
    transform = await service.get(session, ctx, transform_id)
    await service.restore_release(session, ctx, transform, release_id)
    await session.commit()
    session.expunge(transform)
    return await service.detail(session, ctx, await service.get(session, ctx, transform_id))


@router.post(
    "/{transform_id}/releases/{release_id}/activate", response_model=TransformReleaseView,
)
async def activate_release(
    transform_id: uuid.UUID, release_id: uuid.UUID, session: SessionDep, ctx: CtxDep,
):
    transform = await service.get(session, ctx, transform_id)
    release = await service.activate_release(session, ctx, transform, release_id)
    await session.commit()
    return await service.release_view(session, transform, release)


@router.get("/{transform_id}/diff")
async def draft_diff(transform_id: uuid.UUID, session: SessionDep, ctx: CtxDep):
    transform = await service.get(session, ctx, transform_id)
    return {"changes": await service.draft_diff(session, transform)}


@router.post("/{transform_id}/inputs/{asset_id}/profile")
async def profile_input(
    transform_id: uuid.UUID, asset_id: uuid.UUID, session: SessionDep, ctx: CtxDep,
):
    transform = await service.get(session, ctx, transform_id)
    profile = await transform_ai.profile_input(session, ctx, transform, asset_id)
    await session.commit()
    return {"columns": profile}


@router.post("/{transform_id}/ai/draft-model")
async def draft_model(
    transform_id: uuid.UUID,
    payload: TransformDraftRequest,
    session: SessionDep,
    ctx: CtxDep,
):
    transform = await service.get(session, ctx, transform_id)
    draft = await transform_ai.draft_model(
        session, ctx, transform, asset_id=payload.asset_id, intent=payload.intent,
    )
    # The profile may have been measured and cached during the draft.
    await session.commit()
    return draft.model_dump()


@router.put("/{transform_id}/git", response_model=GitSourceView)
async def configure_git(
    transform_id: uuid.UUID, payload: GitSourceUpdate, session: SessionDep, ctx: CtxDep,
):
    """Point this Transform at a repository to read from.

    Read only. There is no endpoint that writes to a repository, because there
    is no code anywhere in the product that could.
    """
    transform = await service.get(session, ctx, transform_id)
    state = await transform_import.configure_git(
        session, ctx, transform,
        **payload.model_dump(exclude_unset=True),
    )
    await session.commit()
    return state


@router.post("/{transform_id}/git/pull", response_model=GitPullResult)
async def pull_git(
    transform_id: uuid.UUID,
    session: SessionDep,
    ctx: CtxDep,
    force: Annotated[bool, Query()] = False,
):
    """Read the repository now and apply it here.

    `force` re-applies the recorded commit rather than reporting it unchanged,
    which is what somebody who has just fixed warehouse permissions wants.
    """
    transform = await service.get(session, ctx, transform_id)
    result = await transform_import.pull_now(session, ctx, transform, force=force)
    await session.commit()
    return result


@router.get("/{transform_id}/lineage", response_model=TransformLineage)
async def lineage(transform_id: uuid.UUID, session: SessionDep, ctx: CtxDep):
    transform = await service.get(session, ctx, transform_id)
    return await service.lineage(session, ctx, transform)


@router.get("/{transform_id}/project", response_model=dict[str, str])
async def generated_project(transform_id: uuid.UUID, session: SessionDep, ctx: CtxDep):
    transform = await service.get(session, ctx, transform_id)
    return await service.generated_project(session, ctx, transform)


@router.get("/{transform_id}/export")
async def export(transform_id: uuid.UUID, session: SessionDep, ctx: CtxDep):
    transform = await service.get(session, ctx, transform_id)
    content = await service.export_project(session, ctx, transform)
    filename = re_safe_filename(transform.name) + ".zip"
    return StreamingResponse(
        iter([content]), media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def re_safe_filename(value: str) -> str:
    return "".join(char if char.isalnum() or char in ("-", "_") else "_" for char in value)[:100]
