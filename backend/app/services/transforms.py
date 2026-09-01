"""Transform product service and execution lifecycle."""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import and_, delete as sa_delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.context import RequestContext
from app.core.db import utcnow
from app.core.errors import (
    ConflictError, ErrorCategory, NotFoundError, ResourceModifiedError, ValidationError,
)
from app.core.params import as_enum
from app.core.permissions import Action, Module
from app.models.engine import ConnectorDefinition
from app.models.enums import (
    ACTIVE_RUN_STATUSES, HealthLevel, ResourceStatus, RunStatus, ScheduleType,
    TriggerType,
)
from app.models.integration import Destination, Pipeline, PipelineStream, Source
from app.models.run import PipelineRun
from app.models.transform import (
    DataAsset, Transform, TransformArtifact, TransformDependency, TransformInput,
    TransformModel, TransformRelease, TransformRun, TransformRunAttempt,
    TransformRunNode, TransformTest,
)
from app.schemas.common import ActorRef
from app.schemas.domain import (
    DataAssetRegister, DataAssetView, PipelineInputCandidate, ScheduleConfig,
    TransformCreate,
    TransformDestinationCapability, TransformDetail, TransformExecutionView,
    TransformInputCandidates, TransformLineage, TransformModelCreate, TransformModelUpdate,
    TransformModelView, TransformReleaseView, TransformRunNodeView, TransformRunRef,
    TransformTestCreate,
    TransformTestView, TransformUpdate, TransformView,
)
from app.services import actors as actor_service, audit, scheduling
from app.transformation.base import TransformationRequest, TransformationResult
from app.transformation.compatibility import capability, lock
from app.transformation.profiles import build_profile
from app.transformation.project import generate_project, require_identifier
from app.transformation.warehouse import browse_relations, browse_schemas, verify_relation

logger = logging.getLogger(__name__)

# Operations that materialise relations in the warehouse. They share the
# active-build lock and are the only ones that move a Transform's health.
PRODUCTION_OPERATIONS = ("RUN_MODEL", "RUN_UPSTREAM", "BUILD")
# Everything a caller may ask for. TEST re-checks data without rewriting tables;
# RUN_UPSTREAM is dbt's `+model` selector, kept separate from RUN_MODEL so a
# button labelled "Run model" cannot silently rebuild the whole chain.
TRANSFORM_OPERATIONS = (
    "VALIDATE", "COMPILE", "PREVIEW", "TEST", "RUN_MODEL", "RUN_UPSTREAM", "BUILD",
)


def _violated_constraint(error: IntegrityError) -> str:
    original = getattr(error, "orig", None)
    name = getattr(original, "constraint_name", None)
    if not name:
        name = getattr(getattr(original, "diag", None), "constraint_name", None)
    if name:
        return str(name)
    message = str(error)
    for candidate in ("uq_transform_run_idempotency", "uq_transform_active_build"):
        if candidate in message:
            return candidate
    return ""


async def _destination(
    session: AsyncSession, workspace_id: uuid.UUID, destination_id: uuid.UUID,
) -> Destination:
    destination = await session.scalar(select(Destination).where(
        Destination.id == destination_id,
        Destination.workspace_id == workspace_id,
        Destination.deleted_at.is_(None),
    ))
    if destination is None:
        raise NotFoundError("Destination was not found in this workspace.")
    return destination


async def _destination_ref(session: AsyncSession, destination: Destination) -> ActorRef:
    definition = await session.scalar(select(ConnectorDefinition).where(
        ConnectorDefinition.connector_key == destination.connector_key,
    ))
    return ActorRef(
        id=destination.id,
        name=destination.name,
        connector_key=destination.connector_key,
        connector_display_name=definition.display_name if definition else None,
        icon=definition.icon if definition else None,
    )


async def get(
    session: AsyncSession, ctx: RequestContext, transform_id: uuid.UUID,
) -> Transform:
    transform = await session.scalar(select(Transform).where(
        Transform.id == transform_id,
        Transform.workspace_id == ctx.workspace_id,
        Transform.deleted_at.is_(None),
    ).execution_options(populate_existing=True))
    if transform is None:
        raise NotFoundError("Transform was not found in this workspace.")
    return transform


async def _latest_run(session: AsyncSession, transform_id: uuid.UUID) -> TransformRun | None:
    return await session.scalar(
        select(TransformRun).where(TransformRun.transform_id == transform_id)
        .order_by(TransformRun.created_at.desc()).limit(1)
    )


def _run_ref(run: TransformRun | None) -> TransformRunRef | None:
    if run is None:
        return None
    return TransformRunRef(
        id=run.id, operation=run.operation, status=run.status.value,
        started_at=run.started_at, ended_at=run.ended_at, created_at=run.created_at,
        models_built=run.models_built, tests_passed=run.tests_passed,
        tests_failed=run.tests_failed,
    )


def _test_view(test: TransformTest) -> TransformTestView:
    return TransformTestView(
        id=test.id, column_name=test.column_name, rule=test.rule, severity=test.severity,
        config=test.config_json or {}, last_status=test.last_status, last_run_at=test.last_run_at,
    )


def _model_view(model: TransformModel) -> TransformModelView:
    return TransformModelView(
        id=model.id, name=model.name, layer=model.layer, materialization=model.materialization,
        sql=model.sql, output_schema=model.output_schema, relation_name=model.relation_name,
        description=model.description, tags=model.tags or [], config=model.config_json or {},
        tests=[_test_view(test) for test in model.tests if test.deleted_at is None],
        version=model.version, updated_at=model.updated_at,
    )


async def _asset_view(
    session: AsyncSession,
    asset: DataAsset,
    *,
    source_name: str | None = None,
    freshness_state: str | None = None,
) -> DataAssetView:
    pipeline = await session.get(Pipeline, asset.pipeline_id) if asset.pipeline_id else None
    return DataAssetView(
        source_name=source_name, freshness_state=freshness_state,
        id=asset.id, destination_id=asset.destination_id, catalog_name=asset.catalog_name,
        schema_name=asset.schema_name, relation_name=asset.relation_name,
        relation_type=asset.relation_type, asset_type=asset.asset_type,
        owner_type=asset.owner_type, pipeline_id=asset.pipeline_id,
        pipeline_name=pipeline.name if pipeline else None,
        pipeline_stream_id=asset.pipeline_stream_id,
        resolution_status=asset.resolution_status,
        columns=(asset.schema_metadata or {}).get("columns", []),
        last_ready_at=asset.last_ready_at, fresh_at=asset.fresh_at,
    )


async def present(session: AsyncSession, ctx: RequestContext, transform: Transform) -> TransformView:
    destination = await session.get(Destination, transform.destination_id)
    models = [model for model in transform.models if model.deleted_at is None]
    test_count = sum(len([test for test in model.tests if test.deleted_at is None]) for model in models)
    actions = ["view"]
    for action in (Action.EDIT, Action.OPERATE, Action.DELETE):
        if ctx.can(Module.TRANSFORMS, action):
            actions.append(action.value)
    return TransformView(
        id=transform.id, name=transform.name, description=transform.description,
        destination=await _destination_ref(session, destination),
        default_schema=transform.default_schema, status=transform.status,
        health_status=transform.health_status.value, health_message=transform.health_message,
        model_count=len(models), test_count=test_count,
        last_run=_run_ref(await _latest_run(session, transform.id)),
        last_success_at=transform.last_success_at,
        dbt_core_version=transform.dbt_core_version,
        dbt_adapter_name=transform.dbt_adapter_name,
        dbt_adapter_version=transform.dbt_adapter_version,
        version=transform.version, created_at=transform.created_at, updated_at=transform.updated_at,
        available_actions=actions,
    )


# Operations that write relations. Only these need a sandbox; compiling or
# previewing never materialises anything.
_WRITING_OPERATIONS = ("RUN_MODEL", "RUN_UPSTREAM", "BUILD")


def _is_sandboxed(run: TransformRun) -> bool:
    """A draft build is a rehearsal; a released build is the real thing."""
    return run.release_id is None and run.operation in _WRITING_OPERATIONS


def _draft_schema(transform: Transform) -> str:
    """Where rehearsals land. Kept beside the real schema so it is obvious."""
    return f"{transform.default_schema}_draft"


def _draft_differs(transform: Transform, release: TransformRelease | None) -> bool:
    """Whether the editor holds anything the published snapshot does not.

    Comparing version counters cannot work: publishing bumps the Transform's own
    version, so a freshly published draft would immediately look stale. What the
    user is asking is whether the *code* differs, so compare the code.
    """
    models = [model for model in transform.models if model.deleted_at is None]
    if release is None:
        return bool(models)
    published = {
        item.get("name"): item.get("sql")
        for item in (release.model_snapshot or [])
    }
    if len(published) != len(models):
        return True
    return any(published.get(model.name) != model.sql for model in models)


async def draft_diff(
    session: AsyncSession, transform: Transform,
) -> list[dict[str, Any]]:
    """What the draft would change if it were published right now."""
    release = (
        await session.get(TransformRelease, transform.active_release_id)
        if transform.active_release_id else None
    )
    published = {
        str(item.get("name")): str(item.get("sql") or "")
        for item in ((release.model_snapshot if release else None) or [])
    }
    models = [model for model in transform.models if model.deleted_at is None]
    current = {model.name: model.sql for model in models}
    entries: list[dict[str, Any]] = []
    for name in sorted(set(published) | set(current)):
        before, after = published.get(name), current.get(name)
        if before == after:
            continue
        entries.append({
            "name": name,
            "change": "ADDED" if before is None
            else "REMOVED" if after is None else "MODIFIED",
            "before": before,
            "after": after,
        })
    return entries


async def release_view(
    session: AsyncSession, transform: Transform, release: TransformRelease,
) -> TransformReleaseView:
    return TransformReleaseView(
        id=release.id,
        release_number=release.release_number,
        notes=release.notes,
        default_schema=release.default_schema,
        model_count=len(release.model_snapshot or []),
        created_at=release.created_at,
        is_active=transform.active_release_id == release.id,
    )


async def detail(session: AsyncSession, ctx: RequestContext, transform: Transform) -> TransformDetail:
    base = await present(session, ctx, transform)
    release_row = (
        await session.get(TransformRelease, transform.active_release_id)
        if transform.active_release_id else None
    )
    active = await release_view(session, transform, release_row) if release_row else None
    _, readiness = await upstream_readiness(session, transform)
    states = {item["data_asset_id"]: item["state"] for item in readiness}
    assets = [
        await _asset_view(
            session, item.asset, source_name=item.source_name,
            freshness_state=states.get(str(item.asset.id)),
        )
        for item in transform.inputs
    ]
    return TransformDetail(
        **base.model_dump(), inputs=assets,
        models=[_model_view(model) for model in transform.models if model.deleted_at is None],
        execution_trigger=transform.execution_trigger,
        trigger_config=transform.trigger_config or {},
        upstream_ready=all(
            item["state"] == "READY" for item in readiness if item["required"]
        ) if readiness else True,
        schedule=ScheduleConfig(
            type=transform.schedule_type.value,
            interval_seconds=(transform.schedule_config or {}).get("interval_seconds"),
            time_of_day=(transform.schedule_config or {}).get("time_of_day"),
            cron_expression=(transform.schedule_config or {}).get("cron_expression"),
            timezone=transform.timezone,
        ),
        next_run_at=transform.next_run_at,
        active_release=active,
        # The draft has moved on if anything was edited after the release was
        # taken. That is the fact the editor needs to warn about, since the
        # schedule will keep running the older code until somebody publishes.
        draft_has_changes=_draft_differs(transform, release_row),
    )


async def list_transforms(
    session: AsyncSession, ctx: RequestContext, *, search: str | None, limit: int, offset: int,
) -> tuple[list[Transform], int]:
    ctx.require(Module.TRANSFORMS, Action.VIEW)
    stmt = select(Transform).where(
        Transform.workspace_id == ctx.workspace_id, Transform.deleted_at.is_(None),
    )
    if search:
        stmt = stmt.where(Transform.name.ilike(f"%{search.strip()}%"))
    total = await session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list((await session.scalars(
        stmt.order_by(Transform.updated_at.desc()).limit(limit).offset(offset)
    )).all())
    return rows, total


async def destination_capabilities(
    session: AsyncSession, ctx: RequestContext,
) -> list[TransformDestinationCapability]:
    ctx.require(Module.TRANSFORMS, Action.VIEW)
    destinations = list((await session.scalars(select(Destination).where(
        Destination.workspace_id == ctx.workspace_id,
        Destination.deleted_at.is_(None),
    ).order_by(Destination.name))).all())
    items = []
    for destination in destinations:
        cap = capability(destination.connector_key)
        supported = bool(
            cap and cap.get("certification") == "SUPPORTED"
            and destination.status is ResourceStatus.ACTIVE
        )
        reason = None
        if not cap:
            reason = "No certified transformation adapter is available."
        elif destination.status is not ResourceStatus.ACTIVE:
            reason = "Destination must be active."
        items.append(TransformDestinationCapability(
            destination=await _destination_ref(session, destination), supported=supported,
            certification=cap.get("certification") if cap else None,
            adapter=cap.get("package") if cap else None,
            dbt_core_version=cap.get("dbt_core") if cap else None,
            adapter_version=cap.get("version") if cap else None,
            reason=reason,
        ))
    return items


async def input_candidates(
    session: AsyncSession, ctx: RequestContext, destination_id: uuid.UUID,
) -> TransformInputCandidates:
    ctx.require(Module.TRANSFORMS, Action.VIEW)
    await _destination(session, ctx.workspace_id, destination_id)
    pipelines = list((await session.scalars(select(Pipeline).where(
        Pipeline.workspace_id == ctx.workspace_id,
        Pipeline.destination_id == destination_id,
        Pipeline.deleted_at.is_(None),
    ).order_by(Pipeline.name))).all())
    assets = list((await session.scalars(select(DataAsset).where(
        DataAsset.workspace_id == ctx.workspace_id,
        DataAsset.destination_id == destination_id,
        DataAsset.deleted_at.is_(None),
    ).order_by(DataAsset.schema_name, DataAsset.relation_name))).all())
    pipeline_items = []
    for pipeline in pipelines:
        source = await session.get(Source, pipeline.source_id)
        definition = await session.scalar(select(ConnectorDefinition).where(
            ConnectorDefinition.connector_key == source.connector_key,
        )) if source else None
        pipeline_items.append(PipelineInputCandidate(
            pipeline=ActorRef(
                id=pipeline.id, name=pipeline.name,
                connector_key=source.connector_key if source else "",
                connector_display_name=definition.display_name if definition else None,
                icon=definition.icon if definition else None,
            ),
            last_success_at=pipeline.last_success_at,
            streams=[{
                "id": str(stream.id), "name": stream.stream_name,
                "namespace": stream.namespace, "selected": stream.selected,
                "asset_id": str(next((asset.id for asset in assets
                                      if asset.pipeline_stream_id == stream.id), "")) or None,
            } for stream in pipeline.streams if stream.selected],
        ))
    return TransformInputCandidates(
        destination_id=destination_id, pipelines=pipeline_items,
        assets=[await _asset_view(session, asset) for asset in assets],
    )


async def browse_warehouse(
    session: AsyncSession,
    ctx: RequestContext,
    destination_id: uuid.UUID,
    schema_name: str | None,
) -> dict[str, Any]:
    """List what is physically in the warehouse, with what is already an asset.

    Marking the relations that are already registered matters: without it the
    same table gets added twice under two names and the lineage forks.
    """
    ctx.require(Module.TRANSFORMS, Action.VIEW)
    destination = await _destination(session, ctx.workspace_id, destination_id)
    configuration = await actor_service.resolve_configuration(session, destination)
    catalog = configuration.get("project_id") or configuration.get("dataset_project_id")
    if schema_name is None:
        return {
            "catalog_name": catalog,
            "schemas": await browse_schemas(
                destination.connector_key, configuration, catalog_name=catalog,
            ),
            "relations": [],
        }
    relations = await browse_relations(
        destination.connector_key, configuration,
        catalog_name=catalog, schema_name=schema_name,
    )
    known = {
        (asset.schema_name, asset.relation_name): asset.id
        for asset in (await session.scalars(select(DataAsset).where(
            DataAsset.workspace_id == ctx.workspace_id,
            DataAsset.destination_id == destination_id,
            DataAsset.schema_name == schema_name,
            DataAsset.deleted_at.is_(None),
        ))).all()
    }
    return {
        "catalog_name": catalog,
        "schemas": [],
        "relations": [
            {
                "schema_name": item.schema_name,
                "relation_name": item.relation_name,
                "relation_type": item.relation_type,
                "asset_id": known.get((item.schema_name, item.relation_name)),
            }
            for item in relations
        ],
    }


def _stream_matches(stream_name: str, relation_name: str) -> bool:
    """Whether a destination relation plausibly came from this stream.

    Not equality: a destination applies its own naming -- lowercasing, prefixes,
    a namespace folded into the table name -- so the two rarely match character
    for character. Containment after normalising is the test that accepts every
    real naming convention while still catching a relation paired with an
    unrelated stream.
    """
    left = re.sub(r"[^a-z0-9]", "", (stream_name or "").lower())
    right = re.sub(r"[^a-z0-9]", "", (relation_name or "").lower())
    if not left or not right:
        return True
    return left in right or right in left


def _physical_identity(
    destination_id: uuid.UUID, catalog_name: str | None, schema_name: str, relation_name: str,
) -> str:
    value = "|".join((str(destination_id), catalog_name or "", schema_name, relation_name))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


async def register_asset(
    session: AsyncSession,
    ctx: RequestContext,
    destination_id: uuid.UUID,
    payload: DataAssetRegister,
) -> DataAsset:
    ctx.require(Module.TRANSFORMS, Action.CREATE)
    destination = await _destination(session, ctx.workspace_id, destination_id)
    cap = capability(destination.connector_key)
    if not cap or cap.get("certification") != "SUPPORTED":
        raise ValidationError(
            "This Destination is not certified for Transform.",
            code="TRANSFORM_DESTINATION_UNSUPPORTED",
        )
    pipeline = None
    stream = None
    if payload.pipeline_id:
        pipeline = await session.scalar(select(Pipeline).where(
            Pipeline.id == payload.pipeline_id,
            Pipeline.workspace_id == ctx.workspace_id,
            Pipeline.destination_id == destination_id,
            Pipeline.deleted_at.is_(None),
        ))
        if pipeline is None:
            raise ValidationError("The selected Pipeline does not load this Destination.")
    if payload.pipeline_stream_id:
        if pipeline is None:
            raise ValidationError("pipeline_id is required with pipeline_stream_id.")
        stream = await session.scalar(select(PipelineStream).where(
            PipelineStream.id == payload.pipeline_stream_id,
            PipelineStream.pipeline_id == pipeline.id,
        ))
        if stream is None:
            raise ValidationError("The selected stream does not belong to this Pipeline.")
        if not _stream_matches(stream.stream_name, payload.relation_name):
            # Linking a relation to the wrong stream is not a cosmetic error: the
            # asset's freshness, the AFTER_UPSTREAM trigger and the lineage graph
            # all read the flow from this one field, so a mismatch makes the whole
            # Source -> Pipeline -> Transform picture quietly wrong.
            raise ValidationError(
                f"Bảng `{payload.relation_name}` không khớp với stream "
                f"`{stream.stream_name}`. Nếu bảng này thật sự không do Pipeline "
                "nào sinh ra, hãy thêm nó như một bảng có sẵn trong kho dữ liệu.",
                code="TRANSFORM_STREAM_RELATION_MISMATCH",
            )

    configuration = await actor_service.resolve_configuration(session, destination)
    verified = await verify_relation(
        destination.connector_key, configuration,
        catalog_name=payload.catalog_name,
        schema_name=payload.schema_name,
        relation_name=payload.relation_name,
    )
    identity = _physical_identity(
        destination.id, verified.catalog_name, verified.schema_name, verified.relation_name,
    )
    asset = await session.scalar(select(DataAsset).where(
        DataAsset.destination_id == destination.id,
        DataAsset.physical_identity == identity,
        DataAsset.deleted_at.is_(None),
    ))
    if asset is None:
        asset = DataAsset(
            workspace_id=ctx.workspace_id, destination_id=destination.id,
            physical_identity=identity,
        )
        session.add(asset)
    asset.catalog_name = verified.catalog_name
    asset.schema_name = verified.schema_name
    asset.relation_name = verified.relation_name
    asset.relation_type = verified.relation_type
    asset.asset_type = "RAW"
    asset.owner_type = "PIPELINE" if pipeline else "WAREHOUSE"
    asset.owner_resource_id = pipeline.id if pipeline else destination.id
    asset.pipeline_id = pipeline.id if pipeline else None
    asset.pipeline_stream_id = stream.id if stream else None
    asset.resolution_status = "READY"
    asset.schema_metadata = {"columns": verified.columns}
    asset.last_ready_at = utcnow()
    asset.fresh_at = pipeline.last_success_at if pipeline else utcnow()
    await session.flush()
    await audit.record(
        session, ctx, "transform.asset.registered", resource_type="DATA_ASSET",
        resource_id=asset.id,
        resource_name=f"{asset.schema_name}.{asset.relation_name}",
        after={"destination_id": str(destination.id), "pipeline_id": str(asset.pipeline_id or "")},
    )
    return asset


def _source_name(asset: DataAsset, assigned: dict[tuple[str | None, str], str]) -> str:
    """One dbt source per physical schema, shared by every table inside it.

    In dbt a source is the schema and its tables are entries within it, so three
    relations from one dataset must resolve to the same alias. Numbering them
    `src_x`, `src_x_2`, `src_x_3` produced three sources over one schema and made
    the reference a user has to type essentially unguessable.
    """
    scope = (asset.catalog_name, asset.schema_name)
    existing = assigned.get(scope)
    if existing is not None:
        return existing
    base = re.sub(r"[^A-Za-z0-9_]", "_", f"src_{asset.schema_name}").lower()
    if not base[0].isalpha() and base[0] != "_":
        base = f"src_{base}"
    candidate = base[:110]
    suffix = 2
    while candidate in assigned.values():
        candidate = f"{base[:104]}_{suffix}"
        suffix += 1
    assigned[scope] = candidate
    return candidate


async def _replace_inputs(
    session: AsyncSession,
    transform: Transform,
    asset_ids: list[uuid.UUID],
) -> None:
    assets = list((await session.scalars(select(DataAsset).where(
        DataAsset.id.in_(asset_ids),
        DataAsset.workspace_id == transform.workspace_id,
        DataAsset.destination_id == transform.destination_id,
        DataAsset.resolution_status == "READY",
        DataAsset.deleted_at.is_(None),
    ))).all()) if asset_ids else []
    if len({asset.id for asset in assets}) != len(set(asset_ids)):
        raise ValidationError(
            "Every Transform input must be a verified relation in the selected Destination.",
            code="TRANSFORM_INPUT_INVALID",
        )
    await session.execute(sa_delete(TransformInput).where(TransformInput.transform_id == transform.id))
    assigned: dict[tuple[str | None, str], str] = {}
    for asset in sorted(assets, key=lambda item: (item.schema_name, item.relation_name)):
        session.add(TransformInput(
            transform_id=transform.id, data_asset_id=asset.id,
            source_name=_source_name(asset, assigned), required=True,
        ))
    await session.flush()


async def create(
    session: AsyncSession, ctx: RequestContext, payload: TransformCreate,
) -> Transform:
    ctx.require(Module.TRANSFORMS, Action.CREATE)
    require_identifier(payload.default_schema, "Output schema")
    destination = await _destination(session, ctx.workspace_id, payload.destination_id)
    cap = capability(destination.connector_key)
    if not cap or cap.get("certification") != "SUPPORTED":
        raise ValidationError(
            "This Destination is not certified for Transform.",
            code="TRANSFORM_DESTINATION_UNSUPPORTED",
        )
    transform = Transform(
        workspace_id=ctx.workspace_id, destination_id=destination.id,
        name=payload.name.strip(), description=payload.description,
        default_schema=payload.default_schema,
        dbt_core_version=cap["dbt_core"], dbt_adapter_name=cap["package"],
        dbt_adapter_version=cap["version"], created_by=ctx.user_id, updated_by=ctx.user_id,
    )
    session.add(transform)
    await session.flush()
    await _replace_inputs(session, transform, payload.input_asset_ids)
    await audit.record(
        session, ctx, "transform.created", resource_type="TRANSFORM",
        resource_id=transform.id, resource_name=transform.name,
        after={"destination_id": str(destination.id), "input_count": len(payload.input_asset_ids)},
    )
    return transform


async def update(
    session: AsyncSession, ctx: RequestContext, transform: Transform, payload: TransformUpdate,
) -> Transform:
    ctx.require(Module.TRANSFORMS, Action.EDIT)
    if payload.version is not None and payload.version != transform.version:
        raise ResourceModifiedError()
    changes = payload.model_dump(
        exclude_unset=True, exclude={"version", "input_asset_ids", "schedule"},
    )
    if "default_schema" in changes:
        require_identifier(changes["default_schema"], "Output schema")
    for key, value in changes.items():
        setattr(transform, key, value)
    if payload.schedule is not None:
        # Validated and advanced through the same helper pipelines use, so DST
        # and cron edge cases behave identically in both places.
        schedule = scheduling.validate(payload.schedule.model_dump())
        transform.schedule_type = ScheduleType(schedule["type"])
        transform.schedule_config = schedule
        transform.timezone = schedule.get("timezone", transform.timezone)
        transform.next_run_at = scheduling.next_run_at(
            transform.schedule_type, schedule, transform.timezone,
        )
    if transform.execution_trigger != "SCHEDULE":
        transform.next_run_at = None
    if payload.input_asset_ids is not None:
        await _replace_inputs(session, transform, payload.input_asset_ids)
        # The rows behind `transform.inputs` were just deleted and re-inserted,
        # so the loaded collection no longer matches the database.
        session.expire(transform, ["inputs"])
    transform.version += 1
    transform.updated_by = ctx.user_id
    await audit.record(
        session, ctx, "transform.updated", resource_type="TRANSFORM",
        resource_id=transform.id, resource_name=transform.name, after=changes,
    )
    await session.flush()
    return transform


async def remove(session: AsyncSession, ctx: RequestContext, transform: Transform) -> None:
    ctx.require(Module.TRANSFORMS, Action.DELETE)
    active = await session.scalar(select(func.count()).select_from(TransformRun).where(
        TransformRun.transform_id == transform.id,
        TransformRun.status.in_(list(ACTIVE_RUN_STATUSES)),
    )) or 0
    if active:
        raise ConflictError(
            "Transform has an active run and cannot be deleted.",
            code="TRANSFORM_RUN_ACTIVE",
        )
    transform.deleted_at = utcnow()
    transform.status = "DELETED"
    await audit.record(
        session, ctx, "transform.deleted", resource_type="TRANSFORM",
        resource_id=transform.id, resource_name=transform.name,
    )


async def create_model(
    session: AsyncSession, ctx: RequestContext, transform: Transform, payload: TransformModelCreate,
) -> TransformModel:
    ctx.require(Module.TRANSFORMS, Action.EDIT)
    require_identifier(payload.name, "Model name")
    sql = payload.sql
    if not sql:
        if transform.inputs:
            item = transform.inputs[0]
            sql = (
                "select *\nfrom {{ source('" + item.source_name + "', '"
                + item.asset.relation_name + "') }}"
            )
        else:
            sql = "select 1 as example_value"
    model = TransformModel(
        transform_id=transform.id, name=payload.name, layer=payload.layer,
        materialization=payload.materialization, sql=sql,
        created_by=ctx.user_id, updated_by=ctx.user_id,
    )
    session.add(model)
    transform.version += 1
    await session.flush()
    await audit.record(
        session, ctx, "transform.model.created", resource_type="TRANSFORM_MODEL",
        resource_id=model.id, resource_name=model.name,
        after={"transform_id": str(transform.id), "materialization": model.materialization},
    )
    return model


async def _model(
    session: AsyncSession, transform: Transform, model_id: uuid.UUID,
) -> TransformModel:
    model = await session.scalar(select(TransformModel).where(
        TransformModel.id == model_id, TransformModel.transform_id == transform.id,
        TransformModel.deleted_at.is_(None),
    ).execution_options(populate_existing=True))
    if model is None:
        raise NotFoundError("Transform model was not found.")
    return model


def _validate_incremental(transform: Transform, config: dict[str, Any]) -> None:
    """Reject incremental settings the warehouse's dbt adapter cannot run.

    Each adapter implements its own strategies: `delete+insert` is a Postgres
    strategy and BigQuery has no such thing, while `insert_overwrite` is the
    one BigQuery users reach for. Offering a strategy the adapter does not
    implement turns into a compilation error on the next production build,
    which is a slow and confusing way to learn about a typo in a dropdown.
    """
    strategy = (config or {}).get("incremental_strategy")
    if not strategy:
        return
    destination_key = next(
        (key for key, item in lock().get("adapters", {}).items()
         if item.get("package") == transform.dbt_adapter_name),
        None,
    )
    allowed = (lock().get("adapters", {}).get(destination_key or "", {})
               .get("incremental_strategies", []))
    if allowed and strategy not in allowed:
        raise ValidationError(
            f"{transform.dbt_adapter_name} does not support the "
            f"'{strategy}' incremental strategy.",
            code="TRANSFORM_STRATEGY_UNSUPPORTED",
            details={"supported": allowed},
        )


async def update_model(
    session: AsyncSession, ctx: RequestContext, transform: Transform,
    model_id: uuid.UUID, payload: TransformModelUpdate,
) -> TransformModel:
    ctx.require(Module.TRANSFORMS, Action.EDIT)
    model = await _model(session, transform, model_id)
    if payload.version is not None and payload.version != model.version:
        raise ResourceModifiedError()
    changes = payload.model_dump(exclude_unset=True, exclude={"version"})
    if changes.get("output_schema"):
        require_identifier(changes["output_schema"], "Output schema")
    if changes.get("relation_name"):
        require_identifier(changes["relation_name"], "Relation name")
    if "config" in changes:
        changes["config_json"] = changes.pop("config")
    materialization = changes.get("materialization", model.materialization)
    if materialization == "INCREMENTAL":
        _validate_incremental(
            transform, changes.get("config_json", model.config_json) or {},
        )
    for key, value in changes.items():
        setattr(model, key, value)
    model.updated_by = ctx.user_id
    model.version += 1
    transform.version += 1
    await audit.record(
        session, ctx, "transform.model.changed", resource_type="TRANSFORM_MODEL",
        resource_id=model.id, resource_name=model.name,
        after={key: value for key, value in changes.items() if key != "sql"},
    )
    await session.flush()
    return model


async def remove_model(
    session: AsyncSession, ctx: RequestContext, transform: Transform, model_id: uuid.UUID,
) -> None:
    ctx.require(Module.TRANSFORMS, Action.EDIT)
    model = await _model(session, transform, model_id)
    model.deleted_at = utcnow()
    transform.version += 1
    await audit.record(
        session, ctx, "transform.model.deleted", resource_type="TRANSFORM_MODEL",
        resource_id=model.id, resource_name=model.name,
    )


async def add_test(
    session: AsyncSession, ctx: RequestContext, transform: Transform,
    model_id: uuid.UUID, payload: TransformTestCreate,
) -> TransformTest:
    ctx.require(Module.TRANSFORMS, Action.EDIT)
    model = await _model(session, transform, model_id)
    if payload.rule == "ACCEPTED_VALUES" and not payload.config.get("values"):
        raise ValidationError("Accepted values test requires at least one value.")
    if payload.rule == "RELATIONSHIPS" and not {
        "to", "field"
    }.issubset(payload.config):
        raise ValidationError("Relationship test requires target model and field.")
    test = TransformTest(
        model_id=model.id, column_name=payload.column_name, rule=payload.rule,
        severity=payload.severity, config_json=payload.config, created_by=ctx.user_id,
    )
    session.add(test)
    transform.version += 1
    await session.flush()
    await audit.record(
        session, ctx, "transform.test.created", resource_type="TRANSFORM_TEST",
        resource_id=test.id, resource_name=f"{model.name}:{payload.rule}",
    )
    return test


async def remove_test(
    session: AsyncSession, ctx: RequestContext, transform: Transform,
    model_id: uuid.UUID, test_id: uuid.UUID,
) -> None:
    ctx.require(Module.TRANSFORMS, Action.EDIT)
    model = await _model(session, transform, model_id)
    test = await session.scalar(select(TransformTest).where(
        TransformTest.id == test_id, TransformTest.model_id == model.id,
        TransformTest.deleted_at.is_(None),
    ))
    if test is None:
        raise NotFoundError("Transform test was not found.")
    test.deleted_at = utcnow()
    transform.version += 1
    await audit.record(
        session, ctx, "transform.test.deleted", resource_type="TRANSFORM_TEST",
        resource_id=test.id, resource_name=f"{model.name}:{test.rule}",
    )


async def enqueue(
    session: AsyncSession,
    ctx: RequestContext,
    transform: Transform,
    *,
    operation: str,
    model_id: uuid.UUID | None,
    full_refresh: bool = False,
    source: str = "DRAFT",
    idempotency_key: str | None = None,
    trigger_type: TriggerType = TriggerType.MANUAL,
    retry_of_run_id: uuid.UUID | None = None,
    enforce_permission: bool = True,
) -> TransformRun:
    if enforce_permission:
        ctx.require(Module.TRANSFORMS, Action.OPERATE)
    if idempotency_key:
        if len(idempotency_key) > 120:
            raise ValidationError(
                "Idempotency-Key must not exceed 120 characters.",
                code="IDEMPOTENCY_KEY_TOO_LONG",
            )
        existing = await session.scalar(select(TransformRun).where(
            TransformRun.workspace_id == ctx.workspace_id,
            TransformRun.idempotency_key == idempotency_key,
        ))
        if existing is not None:
            return existing
    operation = operation.upper()
    if operation not in TRANSFORM_OPERATIONS:
        raise ValidationError("Unsupported Transform operation.")
    selected = None
    if operation in {"PREVIEW", "RUN_MODEL", "RUN_UPSTREAM"}:
        if model_id is None:
            raise ValidationError("A model is required for this operation.")
        selected = await _model(session, transform, model_id)
    elif model_id is not None:
        selected = await _model(session, transform, model_id)
    if operation in PRODUCTION_OPERATIONS:
        active = await session.scalar(select(TransformRun).where(
            TransformRun.transform_id == transform.id,
            TransformRun.operation.in_(list(PRODUCTION_OPERATIONS)),
            TransformRun.status.in_(list(ACTIVE_RUN_STATUSES)),
        ).limit(1))
        if active:
            raise ConflictError(
                "A production Transform run is already active.",
                code="TRANSFORM_ALREADY_RUNNING",
                details={"run_id": str(active.id)},
            )
    run = TransformRun(
        workspace_id=ctx.workspace_id, transform_id=transform.id,
        operation=operation, selected_model_id=selected.id if selected else None,
        trigger_type=trigger_type, triggered_by=ctx.user_id,
        # A DRAFT run compiles whatever the editor holds now; a RELEASE run
        # executes the published snapshot and ignores later edits.
        release_id=transform.active_release_id if source == "RELEASE" else None,
        retry_of_run_id=retry_of_run_id,
        idempotency_key=idempotency_key,
        technical_metadata={"trace_id": ctx.trace_id, "full_refresh": full_refresh},
    )
    try:
        async with session.begin_nested():
            session.add(run)
            await session.flush()
    except IntegrityError as exc:
        constraint = _violated_constraint(exc)
        if constraint == "uq_transform_run_idempotency" and idempotency_key:
            duplicate = await session.scalar(select(TransformRun).where(
                TransformRun.workspace_id == ctx.workspace_id,
                TransformRun.idempotency_key == idempotency_key,
            ))
            if duplicate is not None:
                return duplicate
        if constraint == "uq_transform_active_build":
            active = await session.scalar(select(TransformRun).where(
                TransformRun.transform_id == transform.id,
                TransformRun.operation.in_(list(PRODUCTION_OPERATIONS)),
                TransformRun.status.in_(list(ACTIVE_RUN_STATUSES)),
            ).limit(1))
            raise ConflictError(
                "A production Transform run is already active.",
                code="TRANSFORM_ALREADY_RUNNING",
                details={"run_id": str(active.id) if active else None},
            ) from exc
        raise
    await audit.record(
        session, ctx, "transform.run.triggered", resource_type="TRANSFORM_RUN",
        resource_id=run.id, resource_name=transform.name,
        after={
            "operation": operation, "model_id": str(model_id or ""),
            "trigger_type": run.trigger_type.value,
            "retry_of_run_id": str(retry_of_run_id) if retry_of_run_id else None,
        },
    )
    return run


async def upstream_readiness(
    session: AsyncSession, transform: Transform,
) -> tuple[bool, list[dict[str, Any]]]:
    """Per-input freshness for an AFTER_UPSTREAM Transform.

    A Transform fed by several Pipelines must not build when only one of them
    has landed -- the build would mix today's rows with yesterday's. Readiness
    is therefore evaluated across every required input, not the one that just
    finished.
    """
    inputs = list((await session.scalars(select(TransformInput).where(
        TransformInput.transform_id == transform.id,
    ).order_by(TransformInput.source_name))).all())
    threshold = transform.last_success_at
    report: list[dict[str, Any]] = []
    ready = True
    for item in inputs:
        asset = item.asset
        fresh_at = asset.fresh_at
        # An asset never loaded by AppBI has no freshness signal of its own; a
        # plain warehouse relation is treated as always available because
        # nothing in this product can tell us when it last changed.
        if asset.pipeline_id is None:
            state = "READY" if asset.resolution_status == "READY" else "UNRESOLVED"
        elif asset.resolution_status != "READY" or fresh_at is None:
            state = "UNRESOLVED"
        elif threshold is not None and fresh_at <= threshold:
            state = "STALE"
        else:
            state = "READY"
        if item.required and state != "READY":
            ready = False
        report.append({
            "data_asset_id": str(asset.id),
            "source_name": item.source_name,
            "relation": f"{asset.schema_name}.{asset.relation_name}",
            "pipeline_id": str(asset.pipeline_id) if asset.pipeline_id else None,
            "required": item.required,
            "state": state,
            "fresh_at": fresh_at.isoformat() if fresh_at else None,
        })
    return (ready and bool(inputs)), report


async def enqueue_after_upstream(
    session: AsyncSession, pipeline_run: PipelineRun,
) -> list[TransformRun]:
    """Queue builds whose *every* required input is fresh after this sync."""
    if pipeline_run.status is not RunStatus.SUCCEEDED:
        return []
    assets = list((await session.scalars(select(DataAsset).where(
        DataAsset.pipeline_id == pipeline_run.pipeline_id,
        DataAsset.deleted_at.is_(None),
    ))).all())
    if not assets:
        return []
    now = pipeline_run.ended_at or utcnow()
    for asset in assets:
        asset.resolution_status = "READY"
        asset.last_ready_at = now
        asset.fresh_at = now
    # The freshness gate below reads asset.fresh_at through the relationship on
    # TransformInput, so the writes above must be visible to that query.
    await session.flush()
    transforms = list((await session.scalars(
        select(Transform)
        .join(TransformInput, TransformInput.transform_id == Transform.id)
        .where(
            TransformInput.data_asset_id.in_([asset.id for asset in assets]),
            Transform.execution_trigger == "AFTER_UPSTREAM",
            Transform.status == "ACTIVE",
            Transform.deleted_at.is_(None),
        )
        .distinct()
    )).all())
    ctx = RequestContext.system(
        pipeline_run.workspace_id, f"upstream:{pipeline_run.id}",
    )
    queued: list[TransformRun] = []
    for transform in transforms:
        ready, report = await upstream_readiness(session, transform)
        if not ready:
            waiting = [item["relation"] for item in report
                       if item["required"] and item["state"] != "READY"]
            transform.health_message = (
                "Waiting for upstream data: " + ", ".join(waiting[:5])
            )
            logger.info(
                "Transform is waiting for other required inputs.",
                extra={
                    "transform_id": str(transform.id),
                    "pipeline_run_id": str(pipeline_run.id),
                    "waiting_on": waiting,
                },
            )
            continue
        idempotency_key = f"upstream:{pipeline_run.id}:{transform.id}"
        already_queued = await session.scalar(select(TransformRun.id).where(
            TransformRun.workspace_id == pipeline_run.workspace_id,
            TransformRun.idempotency_key == idempotency_key,
        ))
        if already_queued is not None:
            continue
        try:
            queued.append(await enqueue(
                session, ctx, transform, operation="BUILD", model_id=None,
                idempotency_key=idempotency_key,
                # Unattended, so it runs published code. Otherwise a user still
                # mid-edit when the upstream sync lands ships a half-finished
                # model into the warehouse.
                source="RELEASE" if transform.active_release_id else "DRAFT",
                trigger_type=TriggerType.AFTER_UPSTREAM, enforce_permission=False,
            ))
        except ConflictError:
            logger.info(
                "Skipping after-upstream Transform because a build is active.",
                extra={"transform_id": str(transform.id), "pipeline_run_id": str(pipeline_run.id)},
            )
    return queued


async def get_run(
    session: AsyncSession, ctx: RequestContext, run_id: uuid.UUID,
) -> TransformRun:
    run = await session.scalar(select(TransformRun).where(
        TransformRun.id == run_id, TransformRun.workspace_id == ctx.workspace_id,
    ))
    if run is None:
        raise NotFoundError("Transform run was not found.")
    return run


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
) -> tuple[list[TransformRun], int, dict[str, int]]:
    stmt = select(TransformRun).where(TransformRun.workspace_id == ctx.workspace_id)
    if transform_id:
        stmt = stmt.where(TransformRun.transform_id == transform_id)
    if status:
        if status.strip().upper() == "ACTIVE":
            stmt = stmt.where(TransformRun.status.in_(list(ACTIVE_RUN_STATUSES)))
        else:
            stmt = stmt.where(TransformRun.status == as_enum(status, RunStatus, field="status"))
    if trigger_type:
        stmt = stmt.where(
            TransformRun.trigger_type == as_enum(trigger_type, TriggerType, field="trigger_type")
        )
    if error_category:
        stmt = stmt.where(
            TransformRun.error_category
            == as_enum(error_category, ErrorCategory, field="error_category")
        )
    if since:
        stmt = stmt.where(TransformRun.created_at >= since)
    if until:
        stmt = stmt.where(TransformRun.created_at <= until)
    total = await session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list((await session.scalars(
        stmt.order_by(TransformRun.created_at.desc()).limit(limit).offset(offset)
    )).all())
    counts = (await session.execute(
        stmt.order_by(None)
        .with_only_columns(TransformRun.status, func.count())
        .group_by(TransformRun.status)
    )).all()
    summary = {key.value.lower(): value for key, value in counts}
    summary["total"] = total
    return rows, total, summary


async def get_claimed_run(session: AsyncSession, run_id: uuid.UUID) -> TransformRun:
    """Worker-only lookup after the row was claimed under a database lock."""
    run = await session.get(TransformRun, run_id)
    if run is None:
        raise RuntimeError("Claimed Transform run disappeared.")
    return run


async def execution_view(session: AsyncSession, run: TransformRun) -> TransformExecutionView:
    artifact = await session.get(TransformArtifact, run.id)
    nodes = list((await session.scalars(
        select(TransformRunNode).where(TransformRunNode.run_id == run.id)
    )).all())
    error = None
    if run.error_code or run.error_summary:
        error = {
            "code": run.error_code,
            "category": run.error_category.value if run.error_category else None,
            "summary": run.error_summary,
            "remediation_action": run.remediation_action,
            "technical_message": (run.technical_metadata or {}).get("technical_message"),
            "location": (run.technical_metadata or {}).get("error_location"),
        }
    return TransformExecutionView(
        id=run.id, transform_id=run.transform_id, operation=run.operation,
        selected_model_id=run.selected_model_id, status=run.status.value,
        trigger_type=run.trigger_type.value, created_at=run.created_at,
        started_at=run.started_at, ended_at=run.ended_at,
        models_built=run.models_built, tests_passed=run.tests_passed,
        tests_failed=run.tests_failed, tests_warned=run.tests_warned,
        rows_affected=run.rows_affected, error=error,
        preview=artifact.preview if artifact else None,
        compiled_sql=artifact.compiled_sql if artifact else {},
        nodes=[TransformRunNodeView(
            name=node.name, resource_type=node.resource_type, status=node.status,
            execution_time=node.execution_time, relation_name=node.relation_name,
            message=node.message,
        ) for node in nodes],
    )


async def claim_next(session: AsyncSession, worker_id: str) -> TransformRun | None:
    running = await session.scalar(select(func.count()).select_from(TransformRun).where(
        TransformRun.status.in_([RunStatus.STARTING, RunStatus.RUNNING]),
    )) or 0
    if running >= settings.transform_worker_max_parallel:
        return None
    run = await session.scalar(
        select(TransformRun).where(TransformRun.status == RunStatus.QUEUED)
        .order_by(TransformRun.created_at)
        .with_for_update(skip_locked=True).limit(1)
    )
    if run is None:
        return None
    run.status = RunStatus.STARTING
    run.claimed_by = worker_id
    run.started_at = utcnow()
    run.heartbeat_at = utcnow()
    run.attempt_count += 1
    session.add(TransformRunAttempt(
        run_id=run.id, attempt_number=run.attempt_count,
        status=RunStatus.STARTING, started_at=run.started_at,
    ))
    await session.commit()
    return run


async def _request_from_release(
    session: AsyncSession, run: TransformRun, transform: Transform,
    destination: Destination, release: TransformRelease,
) -> TransformationRequest:
    """Build a run from a published release rather than the live models."""
    configuration = await actor_service.resolve_configuration(session, destination)
    profile, secrets = build_profile(
        destination.connector_key, configuration, release.default_schema,
    )
    selected = (
        await session.get(TransformModel, run.selected_model_id)
        if run.selected_model_id else None
    )
    return TransformationRequest(
        run_id=str(run.id),
        operation=run.operation,
        project_files=dict(release.project_files),
        profile=profile,
        secret_values=secrets,
        output_schema=release.default_schema,
        selected_model=selected.name if selected else None,
        full_refresh=bool((run.technical_metadata or {}).get('full_refresh')),
    )


async def publish_release(
    session: AsyncSession, ctx: RequestContext, transform: Transform,
    notes: str | None = None, activate: bool = True,
) -> TransformRelease:
    """Freeze the current draft into an immutable, runnable release."""
    ctx.require(Module.TRANSFORMS, Action.EDIT)
    models = list((await session.scalars(select(TransformModel).where(
        TransformModel.transform_id == transform.id, TransformModel.deleted_at.is_(None),
    ).order_by(TransformModel.name))).all())
    if not models:
        raise ValidationError(
            "Add at least one model before publishing.",
            code="TRANSFORM_NO_MODELS",
        )
    inputs = list((await session.scalars(select(TransformInput).where(
        TransformInput.transform_id == transform.id,
    ).order_by(TransformInput.source_name))).all())
    # Generating here means a release is known to compile, so publishing cannot
    # hand the scheduler a project that fails on its first unattended run.
    generated = generate_project(transform, models, inputs)
    highest = await session.scalar(select(func.max(TransformRelease.release_number)).where(
        TransformRelease.transform_id == transform.id,
    ))
    release = TransformRelease(
        transform_id=transform.id,
        release_number=(highest or 0) + 1,
        project_files=dict(generated.files),
        model_snapshot=[
            {
                "name": model.name, "layer": model.layer,
                "materialization": model.materialization, "sql": model.sql,
                "tests": [
                    {"column_name": test.column_name, "rule": test.rule,
                     "severity": test.severity, "config": test.config_json or {}}
                    for test in model.tests if test.deleted_at is None
                ],
            }
            for model in models
        ],
        source_version=transform.version,
        default_schema=transform.default_schema,
        notes=notes,
        created_by=ctx.user_id,
    )
    session.add(release)
    await session.flush()
    if activate:
        transform.active_release_id = release.id
    transform.version += 1
    await audit.record(
        session, ctx, "transform.released", resource_type="TRANSFORM",
        resource_id=transform.id, resource_name=transform.name,
        after={"release_number": release.release_number, "activated": activate},
    )
    await session.flush()
    return release


async def activate_release(
    session: AsyncSession, ctx: RequestContext, transform: Transform, release_id: uuid.UUID,
) -> TransformRelease:
    """Point the schedule at an existing release -- also the rollback path."""
    ctx.require(Module.TRANSFORMS, Action.EDIT)
    release = await session.scalar(select(TransformRelease).where(
        TransformRelease.id == release_id,
        TransformRelease.transform_id == transform.id,
    ))
    if release is None:
        raise NotFoundError("Release was not found for this Transform.")
    transform.active_release_id = release.id
    transform.version += 1
    await audit.record(
        session, ctx, "transform.release.activated", resource_type="TRANSFORM",
        resource_id=transform.id, resource_name=transform.name,
        after={"release_number": release.release_number},
    )
    await session.flush()
    return release


async def release_models(
    session: AsyncSession, transform: Transform, release_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """The SQL a release froze, and how it differs from the one before it.

    Reverting is only safe if you can read what you are reverting to, so the
    comparison is against the previous release rather than the draft: that is
    the question somebody browsing history is asking.
    """
    release = await session.scalar(select(TransformRelease).where(
        TransformRelease.id == release_id,
        TransformRelease.transform_id == transform.id,
    ))
    if release is None:
        raise NotFoundError("Release was not found for this Transform.")
    previous = await session.scalar(select(TransformRelease).where(
        TransformRelease.transform_id == transform.id,
        TransformRelease.release_number < release.release_number,
    ).order_by(TransformRelease.release_number.desc()).limit(1))
    before = {
        str(item.get("name")): str(item.get("sql") or "")
        for item in ((previous.model_snapshot if previous else None) or [])
    }
    entries: list[dict[str, Any]] = []
    for item in (release.model_snapshot or []):
        name = str(item.get("name"))
        after = str(item.get("sql") or "")
        entries.append({
            "name": name,
            "sql": after,
            "previous_sql": before.get(name),
            "change": "ADDED" if name not in before
            else "MODIFIED" if before[name] != after else "UNCHANGED",
        })
    for name, sql_text in before.items():
        if not any(entry["name"] == name for entry in entries):
            entries.append({
                "name": name, "sql": None,
                "previous_sql": sql_text, "change": "REMOVED",
            })
    return sorted(entries, key=lambda entry: entry["name"])


async def restore_release(
    session: AsyncSession, ctx: RequestContext, transform: Transform, release_id: uuid.UUID,
) -> Transform:
    """Copy a release's SQL back into the draft, ready to review and publish.

    Deliberately not the same as activating it: this puts the old code in front
    of the author so they can read it, adjust it, and publish on purpose --
    rather than silently swapping what production runs.
    """
    ctx.require(Module.TRANSFORMS, Action.EDIT)
    release = await session.scalar(select(TransformRelease).where(
        TransformRelease.id == release_id,
        TransformRelease.transform_id == transform.id,
    ))
    if release is None:
        raise NotFoundError("Release was not found for this Transform.")
    snapshot = {
        str(item.get("name")): str(item.get("sql") or "")
        for item in (release.model_snapshot or [])
    }
    models = [model for model in transform.models if model.deleted_at is None]
    for model in models:
        if model.name in snapshot and model.sql != snapshot[model.name]:
            model.sql = snapshot[model.name]
            model.updated_by = ctx.user_id
            model.version += 1
    transform.version += 1
    transform.updated_by = ctx.user_id
    await audit.record(
        session, ctx, "transform.release.restored", resource_type="TRANSFORM",
        resource_id=transform.id, resource_name=transform.name,
        after={"release_number": release.release_number},
    )
    await session.flush()
    return transform


async def list_releases(
    session: AsyncSession, transform: Transform, limit: int = 30,
) -> list[TransformRelease]:
    return list((await session.scalars(select(TransformRelease).where(
        TransformRelease.transform_id == transform.id,
    ).order_by(TransformRelease.release_number.desc()).limit(limit))).all())


async def build_request(session: AsyncSession, run: TransformRun) -> TransformationRequest:
    transform = await session.get(Transform, run.transform_id)
    if transform is None or transform.deleted_at is not None:
        raise ValidationError("Transform no longer exists.", code="TRANSFORM_DELETED")
    destination = await session.get(Destination, transform.destination_id)
    if destination is None or destination.deleted_at is not None:
        raise ValidationError("Destination no longer exists.", code="TRANSFORM_DESTINATION_MISSING")
    if destination.status is not ResourceStatus.ACTIVE:
        raise ValidationError("Destination is disabled.", code="TRANSFORM_DESTINATION_DISABLED")
    # A released run executes the project frozen at publish time. Compiling the
    # live rows here is what lets an edit made after the run was queued end up
    # in the warehouse -- fine for a draft the author is watching, wrong for a
    # schedule that fires at 03:00 with nobody present.
    release = await session.get(TransformRelease, run.release_id) if run.release_id else None
    if release is not None:
        return await _request_from_release(session, run, transform, destination, release)
    models = list((await session.scalars(select(TransformModel).where(
        TransformModel.transform_id == transform.id, TransformModel.deleted_at.is_(None),
    ).order_by(TransformModel.name))).all())
    inputs = list((await session.scalars(select(TransformInput).where(
        TransformInput.transform_id == transform.id,
    ).order_by(TransformInput.source_name))).all())
    for item in inputs:
        if item.asset.destination_id != transform.destination_id or item.asset.resolution_status != "READY":
            raise ValidationError(
                "A Transform input is no longer ready in this Destination.",
                code="TRANSFORM_INPUT_NOT_READY",
            )
    generated = generate_project(transform, models, inputs)
    configuration = await actor_service.resolve_configuration(session, destination)
    # A draft build writes to its own schema so trying something out cannot
    # overwrite the tables a dashboard is reading. Dataform does this with a
    # schema suffix, dbt with a per-developer target; the effect is the same.
    output_schema = _draft_schema(transform) if _is_sandboxed(run) else transform.default_schema
    profile, secrets = build_profile(
        destination.connector_key, configuration, output_schema,
    )
    selected = await session.get(TransformModel, run.selected_model_id) if run.selected_model_id else None
    # Probe the draft schema too: a connection test that passes and a draft build
    # that then fails on permissions would be the worst of both answers.
    schemas = [transform.default_schema, _draft_schema(transform)] + [
        model.output_schema for model in models
        if model.output_schema and model.output_schema != transform.default_schema
    ]
    return TransformationRequest(
        run_id=str(run.id), operation=run.operation, project_files=generated.files,
        profile=profile, secret_values=secrets,
        selected_model=selected.name if selected else None,
        preview_limit=settings.transform_preview_limit,
        output_schema=output_schema,
        # A sandbox build always starts clean. Left incremental, the trial
        # tables accumulate across rehearsals and a uniqueness test starts
        # failing on duplicates the real table never had.
        full_refresh=bool((run.technical_metadata or {}).get("full_refresh"))
        or _is_sandboxed(run),
        validate_schemas=list(dict.fromkeys(schemas)),
        validate_relations=[{
            "database": item.asset.catalog_name or "",
            "schema": item.asset.schema_name,
            "identifier": item.asset.relation_name,
        } for item in inputs],
    )


async def heartbeat(session: AsyncSession, run_id: uuid.UUID) -> bool:
    run = await session.get(TransformRun, run_id)
    if run is None:
        return True
    run.heartbeat_at = utcnow()
    await session.commit()
    return run.status is RunStatus.CANCEL_REQUESTED


async def mark_running(session: AsyncSession, run_id: uuid.UUID) -> None:
    run = await session.get(TransformRun, run_id)
    if run is None:
        return
    run.status = RunStatus.RUNNING
    run.heartbeat_at = utcnow()
    attempt = await session.scalar(select(TransformRunAttempt).where(
        TransformRunAttempt.run_id == run.id,
        TransformRunAttempt.attempt_number == run.attempt_count,
    ))
    if attempt:
        attempt.status = RunStatus.RUNNING
    await session.commit()


async def complete(
    session: AsyncSession, run_id: uuid.UUID, result: TransformationResult,
) -> None:
    run = await session.get(TransformRun, run_id)
    if run is None:
        return
    transform = await session.get(Transform, run.transform_id)
    now = utcnow()
    if result.cancelled:
        run.status = RunStatus.CANCELLED
        run.error_category = ErrorCategory.CANCELLED
    elif result.timed_out:
        run.status = RunStatus.TIMED_OUT
        run.error_category = ErrorCategory.TIMEOUT
    elif result.succeeded:
        run.status = RunStatus.SUCCEEDED
    else:
        run.status = RunStatus.FAILED
        run.error_category = _failure_category(result)
    run.ended_at = now
    run.heartbeat_at = now
    run.error_code = result.error_code
    run.error_summary = result.error_summary
    if run.error_summary:
        run.error_fingerprint = hashlib.sha256(
            f"{run.error_code}|{run.error_summary}".encode("utf-8")
        ).hexdigest()
    run.technical_metadata = {
        **(run.technical_metadata or {}),
        "exit_code": result.exit_code,
        "technical_message": (result.technical_message or "")[:4000],
        "artifact_available": bool(result.manifest or result.run_results),
        "error_location": result.error_location,
    }
    attempt = await session.scalar(select(TransformRunAttempt).where(
        TransformRunAttempt.run_id == run.id,
        TransformRunAttempt.attempt_number == run.attempt_count,
    ))
    if attempt:
        attempt.status = run.status
        attempt.ended_at = now
        attempt.failure_summary = result.error_summary
        attempt.log_path = result.log_path

    session.add(TransformArtifact(
        run_id=run.id, manifest=result.manifest, run_results=result.run_results,
        compiled_sql=result.compiled_sql, preview=result.preview,
        log_text=result.log_text, generated_at=now,
    ))
    await _index_run_results(session, run, result)
    # Depends on the test counters that _index_run_results has just written.
    run.remediation_action = _remediation(run, result)
    await _index_dependencies(session, run, result.manifest)
    if transform:
        transform.last_run_id = run.id
        if run.operation in PRODUCTION_OPERATIONS:
            _apply_health(transform, run, result)
    await session.commit()


def _apply_health(
    transform: Transform, run: TransformRun, result: TransformationResult,
) -> None:
    """Build outcome plus test outcome, per severity.

    A green process exit is not by itself health: dbt can exit 0 with failed
    tests under several flag combinations, and a Transform whose data fails its
    own ERROR-severity tests is not healthy just because the SQL ran.
    """
    if run.status is not RunStatus.SUCCEEDED:
        transform.health_status = HealthLevel.ERROR
        transform.health_message = result.error_summary
        return
    transform.last_success_at = run.ended_at
    if run.tests_failed:
        transform.health_status = HealthLevel.ERROR
        transform.health_message = (
            f"{run.tests_failed} test(s) failed in the latest build."
        )
    elif run.tests_warned:
        transform.health_status = HealthLevel.WARNING
        transform.health_message = (
            f"{run.tests_warned} test(s) raised a warning in the latest build."
        )
    else:
        transform.health_status = HealthLevel.HEALTHY
        transform.health_message = None


def _remediation(run: TransformRun, result: TransformationResult) -> str | None:
    """What the operator should do next, not just what broke."""
    if run.status is RunStatus.SUCCEEDED:
        return None
    if run.status is RunStatus.CANCELLED:
        return "RETRY_RUN"
    if run.status is RunStatus.TIMED_OUT:
        return "SPLIT_OR_RETRY"
    if run.tests_failed:
        return "REVIEW_TEST_FAILURES"
    category = run.error_category
    if category is ErrorCategory.PERMISSION:
        return "CHECK_DESTINATION_CREDENTIALS"
    if category is ErrorCategory.NETWORK:
        return "CHECK_DESTINATION_CONNECTIVITY"
    if category is ErrorCategory.VALIDATION:
        return "FIX_MODEL_SQL"
    return "VIEW_LOGS"


def _failure_category(result: TransformationResult) -> ErrorCategory:
    text = f"{result.error_summary or ''} {result.technical_message or ''}".lower()
    if "permission" in text or "access denied" in text:
        return ErrorCategory.PERMISSION
    if "connect" in text or "network" in text:
        return ErrorCategory.NETWORK
    if "compilation" in text or "syntax" in text or "depends on a node" in text:
        return ErrorCategory.VALIDATION
    return ErrorCategory.ENGINE


async def _index_run_results(
    session: AsyncSession, run: TransformRun, result: TransformationResult,
) -> None:
    await session.execute(sa_delete(TransformRunNode).where(TransformRunNode.run_id == run.id))
    manifest_nodes = (result.manifest or {}).get("nodes", {})
    model_rows = list((await session.scalars(select(TransformModel).where(
        TransformModel.transform_id == run.transform_id,
    ))).all())
    by_name = {model.name: model for model in model_rows}
    model_names_by_unique_id = {
        unique_id: node.get("name")
        for unique_id, node in manifest_nodes.items()
        if node.get("resource_type") == "model"
    }
    models_built = tests_passed = tests_failed = tests_warned = 0
    rows_affected = 0
    warehouse_usage: dict[str, int] = {}
    for item in (result.run_results or {}).get("results", []):
        unique_id = item.get("unique_id") or ""
        manifest_node = manifest_nodes.get(unique_id, {})
        resource_type = manifest_node.get("resource_type") or unique_id.split(".", 1)[0] or "unknown"
        name = manifest_node.get("name") or unique_id.rsplit(".", 1)[-1]
        status = str(item.get("status") or "unknown")
        response = item.get("adapter_response") or {}
        affected = response.get("rows_affected")
        # dbt reports -1 when a row count does not apply -- creating a view, for
        # one -- so summing blindly turns a clean build of five views into "-5
        # rows".
        if isinstance(affected, int) and affected >= 0:
            rows_affected += affected
        # BigQuery answers in bytes and slot time rather than rows; that is the
        # number a warehouse bill is made of, so it is worth keeping.
        for key in ("bytes_processed", "bytes_billed", "slot_ms"):
            value = response.get(key)
            if isinstance(value, int):
                warehouse_usage[key] = warehouse_usage.get(key, 0) + value
        if resource_type == "model" and status == "success":
            models_built += 1
        if resource_type == "test":
            if status in ("pass", "success"):
                tests_passed += 1
            elif status == "warn":
                tests_warned += 1
            elif status not in ("skipped",):
                tests_failed += 1
            metadata = manifest_node.get("test_metadata") or {}
            kwargs = metadata.get("kwargs") or {}
            parent_id = manifest_node.get("attached_node") or next(
                (
                    item for item in (manifest_node.get("depends_on") or {}).get("nodes", [])
                    if item in model_names_by_unique_id
                ),
                None,
            )
            parent = by_name.get(model_names_by_unique_id.get(parent_id, ""))
            rule = str(metadata.get("name") or "").upper()
            column = kwargs.get("column_name")
            if parent and rule:
                configured = next((
                    test for test in parent.tests
                    if test.deleted_at is None and test.rule == rule
                    and (test.column_name or None) == (column or None)
                ), None)
                if configured:
                    configured.last_status = (
                        "PASSED" if status in ("pass", "success")
                        else "WARNING" if status == "warn"
                        else "SKIPPED" if status == "skipped"
                        else "FAILED"
                    )
                    configured.last_run_at = utcnow()
        session.add(TransformRunNode(
            run_id=run.id, model_id=by_name.get(name).id if name in by_name else None,
            dbt_unique_id=unique_id, name=name, resource_type=resource_type.upper(),
            status=status.upper(), execution_time=item.get("execution_time"),
            relation_name=manifest_node.get("relation_name"), message=item.get("message"),
            adapter_response=response,
        ))
        if resource_type == "model" and status == "success" and name in by_name:
            await _upsert_model_asset(session, run, by_name[name], manifest_node)
    run.models_built = models_built
    run.tests_passed = tests_passed
    run.tests_failed = tests_failed
    run.tests_warned = tests_warned
    run.rows_affected = rows_affected or None
    if warehouse_usage:
        run.technical_metadata = {
            **(run.technical_metadata or {}), "warehouse_usage": warehouse_usage,
        }


async def _upsert_model_asset(
    session: AsyncSession, run: TransformRun, model: TransformModel, node: dict[str, Any],
) -> None:
    database = node.get("database")
    schema = node.get("schema")
    relation = node.get("alias") or node.get("name")
    if not schema or not relation:
        return
    transform = await session.get(Transform, run.transform_id)
    # Rehearsal output is not a catalogued asset: registering it would offer the
    # draft copy as an input to other Transforms and show it in lineage as if it
    # were the real table.
    if _is_sandboxed(run):
        return
    identity = _physical_identity(transform.destination_id, database, schema, relation)
    asset = await session.scalar(select(DataAsset).where(
        DataAsset.destination_id == transform.destination_id,
        DataAsset.physical_identity == identity,
        DataAsset.deleted_at.is_(None),
    ))
    if asset is None:
        asset = DataAsset(
            workspace_id=run.workspace_id, destination_id=transform.destination_id,
            physical_identity=identity,
        )
        session.add(asset)
    asset.catalog_name = database
    asset.schema_name = schema
    asset.relation_name = relation
    asset.relation_type = "VIEW" if model.materialization == "VIEW" else "TABLE"
    asset.asset_type = "MART" if model.layer == "MART" else "MODEL"
    asset.owner_type = "TRANSFORM"
    asset.owner_resource_id = model.id
    asset.transform_id = transform.id
    asset.transform_model_id = model.id
    asset.resolution_status = "READY"
    asset.last_ready_at = utcnow()
    asset.fresh_at = utcnow()


async def _index_dependencies(
    session: AsyncSession, run: TransformRun, manifest: dict[str, Any] | None,
) -> None:
    # parent_map is what the graph is rebuilt from. Without it a delete would
    # drop the existing lineage and put nothing back, so a partial artifact
    # (a failed compile writes no parent_map) leaves the last good graph alone.
    parent_map = manifest.get("parent_map") if manifest else None
    if not parent_map:
        return
    models = list((await session.scalars(select(TransformModel).where(
        TransformModel.transform_id == run.transform_id,
        TransformModel.deleted_at.is_(None),
    ))).all())
    by_name = {model.name: model.id for model in models}
    # Keyed on the manifest's own unique_id in a single pass. Building keys from
    # a templated project name silently produced entries matching nothing when
    # artifact metadata omitted it.
    model_ids: dict[str, uuid.UUID] = {
        unique_id: by_name[node["name"]]
        for unique_id, node in (manifest.get("nodes") or {}).items()
        if node.get("resource_type") == "model" and node.get("name") in by_name
    }
    await session.execute(sa_delete(TransformDependency).where(
        TransformDependency.transform_id == run.transform_id,
    ))
    inputs = list((await session.scalars(select(TransformInput).where(
        TransformInput.transform_id == run.transform_id,
    ))).all())
    source_assets: dict[str, uuid.UUID] = {}
    for unique_id, source in (manifest.get("sources") or {}).items():
        match = next((item for item in inputs if item.source_name == source.get("source_name")), None)
        if match:
            source_assets[unique_id] = match.data_asset_id
    now = utcnow()
    for child, parents in parent_map.items():
        downstream = model_ids.get(child)
        if not downstream:
            continue
        for parent in parents:
            upstream_model = model_ids.get(parent)
            upstream_asset = source_assets.get(parent)
            if not upstream_model and not upstream_asset:
                continue
            session.add(TransformDependency(
                transform_id=run.transform_id, upstream_asset_id=upstream_asset,
                upstream_model_id=upstream_model, downstream_model_id=downstream,
                dbt_unique_id=parent, created_at=now,
            ))


async def fail_start(session: AsyncSession, run_id: uuid.UUID, exc: Exception) -> None:
    result = TransformationResult(
        succeeded=False, error_code="TRANSFORM_PREPARE_FAILED",
        error_summary=str(exc)[:1000], technical_message=f"{type(exc).__name__}: {exc}",
    )
    await complete(session, run_id, result)


async def request_cancel(
    session: AsyncSession, ctx: RequestContext, run: TransformRun,
) -> TransformRun:
    ctx.require(Module.TRANSFORMS, Action.OPERATE)
    if not run.status.is_active:
        raise ConflictError("This run is already complete.")
    run.status = RunStatus.CANCEL_REQUESTED
    await audit.record(
        session, ctx, "transform.run.cancel_requested", resource_type="TRANSFORM_RUN",
        resource_id=run.id,
    )
    return run


async def retry_run(
    session: AsyncSession, ctx: RequestContext, run: TransformRun,
) -> TransformRun:
    if run.status not in {RunStatus.FAILED, RunStatus.FAILED_TO_START, RunStatus.CANCELLED,
                          RunStatus.TIMED_OUT}:
        raise ConflictError("Only a failed, cancelled, or timed out run can be retried.")
    transform = await get(session, ctx, run.transform_id)
    # Both fields are set on the INSERT rather than patched afterwards: enqueue
    # can legitimately return a pre-existing row (idempotency hit), and mutating
    # that row would rewrite an unrelated run's lineage.
    return await enqueue(
        session, ctx, transform, operation=run.operation, model_id=run.selected_model_id,
        trigger_type=TriggerType.RETRY, retry_of_run_id=run.id,
        full_refresh=bool((run.technical_metadata or {}).get("full_refresh")),
    )


async def fetch_logs(
    session: AsyncSession, ctx: RequestContext, run_id: uuid.UUID,
    *, cursor: int, limit: int,
) -> tuple[list[str], int | None, bool, int]:
    run = await get_run(session, ctx, run_id)
    attempt = await session.scalar(select(TransformRunAttempt).where(
        TransformRunAttempt.run_id == run.id,
    ).order_by(TransformRunAttempt.attempt_number.desc()).limit(1))
    artifact = await session.get(TransformArtifact, run.id)
    if artifact and artifact.log_text is not None:
        lines = artifact.log_text.splitlines()
    elif not attempt or not attempt.log_path:
        return [], None, False, 0
    else:
        try:
            with open(attempt.log_path, "r", encoding="utf-8", errors="replace") as handle:
                lines = handle.read().splitlines()
        except OSError:
            return ["[transform] Log file is no longer available."], None, False, 1
    window = lines[cursor:cursor + limit]
    next_cursor = cursor + len(window) if cursor + len(window) < len(lines) else None
    return window, next_cursor, next_cursor is not None, len(lines)


async def export_project(session: AsyncSession, ctx: RequestContext, transform: Transform) -> bytes:
    ctx.require(Module.TRANSFORMS, Action.VIEW)
    models = [model for model in transform.models if model.deleted_at is None]
    return generate_project(transform, models, transform.inputs).export_zip()


async def generated_project(
    session: AsyncSession, ctx: RequestContext, transform: Transform,
) -> dict[str, str]:
    ctx.require(Module.TRANSFORMS, Action.VIEW)
    models = [model for model in transform.models if model.deleted_at is None]
    return generate_project(transform, models, transform.inputs).files


async def lineage(
    session: AsyncSession, ctx: RequestContext, transform: Transform,
) -> TransformLineage:
    ctx.require(Module.TRANSFORMS, Action.VIEW)
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_node(node_id: str, **payload: Any) -> None:
        if node_id not in seen:
            nodes.append({"id": node_id, **payload})
            seen.add(node_id)

    for item in transform.inputs:
        asset = item.asset
        asset_id = f"asset:{asset.id}"
        add_node(asset_id, type="DATA_ASSET", label=f"{asset.schema_name}.{asset.relation_name}")
        if asset.pipeline_id:
            pipeline = await session.get(Pipeline, asset.pipeline_id)
            if pipeline:
                pipeline_id = f"pipeline:{pipeline.id}"
                add_node(pipeline_id, type="PIPELINE", label=pipeline.name)
                edges.append({"from": pipeline_id, "to": asset_id, "type": "LOADS"})
                source = await session.get(Source, pipeline.source_id)
                if source:
                    source_id = f"source:{source.id}"
                    new_source = source_id not in seen
                    add_node(source_id, type="SOURCE", label=source.name)
                    # One Pipeline can load several assets; its Source edge is a
                    # property of the Pipeline, not of each asset, so emit it once.
                    if new_source:
                        edges.append({
                            "from": source_id, "to": pipeline_id, "type": "SYNCED_BY",
                        })
    for model in transform.models:
        if model.deleted_at is None:
            add_node(
                f"model:{model.id}", type="MODEL", label=model.name,
                layer=model.layer, materialization=model.materialization,
            )
    dependencies = list((await session.scalars(select(TransformDependency).where(
        TransformDependency.transform_id == transform.id,
    ))).all())
    for dependency in dependencies:
        upstream = (
            f"asset:{dependency.upstream_asset_id}" if dependency.upstream_asset_id
            else f"model:{dependency.upstream_model_id}"
        )
        edges.append({"from": upstream, "to": f"model:{dependency.downstream_model_id}",
                      "type": "DEPENDS_ON"})
    return TransformLineage(nodes=nodes, edges=edges)


async def stale_runs(session: AsyncSession) -> int:
    """Reap runs no worker will ever finish.

    QUEUED is included deliberately. It sits inside `uq_transform_active_build`,
    so a queued row that no worker ever claimed -- the process died between the
    INSERT and the claim -- blocks every future build of that Transform with a
    409 and cannot be cleared from the UI. Such a row also has no heartbeat yet,
    so age is measured from created_at whenever heartbeat_at is NULL.
    """
    now = utcnow()
    deadline = now - timedelta(seconds=settings.transform_timeout_seconds + 60)
    queue_deadline = now - timedelta(seconds=settings.transform_stale_queue_seconds)
    rows = list((await session.scalars(select(TransformRun).where(
        or_(
            and_(
                TransformRun.status.in_([
                    RunStatus.STARTING, RunStatus.RUNNING, RunStatus.CANCEL_REQUESTED,
                ]),
                func.coalesce(TransformRun.heartbeat_at, TransformRun.created_at) < deadline,
            ),
            and_(
                TransformRun.status == RunStatus.QUEUED,
                func.coalesce(TransformRun.heartbeat_at, TransformRun.created_at)
                < queue_deadline,
            ),
        )
    ))).all())
    for run in rows:
        queued_only = run.status is RunStatus.QUEUED
        run.status = RunStatus.FAILED_TO_START if queued_only else RunStatus.FAILED
        run.ended_at = now
        run.heartbeat_at = now
        run.error_category = ErrorCategory.ENGINE
        run.error_code = "TRANSFORM_WORKER_LOST"
        run.error_summary = (
            "No Transform worker picked this run up. It was released so the "
            "Transform can run again."
            if queued_only else
            "The Transform worker stopped before reporting a final result."
        )
        run.remediation_action = "RETRY_RUN"
    if rows:
        await session.commit()
    return len(rows)
