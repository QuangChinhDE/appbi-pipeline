"""Source and destination lifecycle (sections 12, 13, 25).

Sources and destinations differ only in which engine operation they call and
what "in use" means, so one module drives both and the API layer picks a side.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.dto import ConnectionCheckResult, EngineActorRequest
from app.adapters.registry import get_adapter
from app.core.config import settings
from app.core.context import RequestContext
from app.core.db import utcnow
from app.core.errors import (
    AppError, ErrorCategory, NotFoundError, ResourceInUseError, ResourceModifiedError,
    ValidationError, error_from_matrix,
)
from app.core.logging import log_event
from app.core.params import as_enum
from app.core.permissions import Action, Module
from app.core.secrets import secret_store
from app.models.engine import ConnectorDefinition, EngineMapping
from app.models.enums import (
    ConnectorType, EngineResourceType, HealthLevel, PipelineStatus, ProductResourceType,
    ResourceStatus, TestResult,
)
from app.models.identity import User
from app.models.integration import Destination, Pipeline, SchemaSnapshot, Source
from app.services import audit, catalog, oauth, outbox

logger = logging.getLogger(__name__)

Side = Literal["SOURCE", "DESTINATION"]

# A successful "Test connection" is worth something: re-running the same
# connector check at save time can cost minutes of container startup for no new
# information. `/test` therefore returns a short-lived, server-signed token
# bound to the exact configuration that passed; save accepts it in place of a
# second check. The token proves *this server* saw *this config* succeed
# recently, so it cannot be forged or replayed against different credentials.
CHECK_TOKEN_TTL_SECONDS = 900


def _config_digest(side: Side, connector_key: str, configuration: dict[str, Any]) -> str:
    material = json.dumps(
        {"side": side, "connector": connector_key, "config": configuration},
        sort_keys=True, separators=(",", ":"), default=str,
    )
    return hashlib.sha256(material.encode()).hexdigest()


def issue_check_token(side: Side, connector_key: str, configuration: dict[str, Any]) -> str:
    expiry = int(time.time()) + CHECK_TOKEN_TTL_SECONDS
    digest = _config_digest(side, connector_key, configuration)
    signature = hmac.new(
        settings.jwt_secret.encode(), f"{digest}.{expiry}".encode(), hashlib.sha256
    ).hexdigest()[:32]
    return f"{expiry}.{signature}"


def verify_check_token(
    token: str | None, side: Side, connector_key: str, configuration: dict[str, Any]
) -> bool:
    if not token or "." not in token:
        return False
    raw_expiry, _, signature = token.partition(".")
    try:
        expiry = int(raw_expiry)
    except ValueError:
        return False
    if expiry < time.time():
        return False
    # Recompute against the caller's own expiry so the digest, not the clock,
    # is what has to match.
    digest = _config_digest(side, connector_key, configuration)
    expected = hmac.new(
        settings.jwt_secret.encode(), f"{digest}.{expiry}".encode(), hashlib.sha256
    ).hexdigest()[:32]
    return hmac.compare_digest(expected, signature)


@dataclass(frozen=True, slots=True)
class ActorKind:
    side: Side
    model: type
    module: Module
    connector_type: ConnectorType
    engine_resource: EngineResourceType
    product_resource: ProductResourceType
    audit_prefix: str


SOURCE = ActorKind("SOURCE", Source, Module.SOURCES, ConnectorType.SOURCE,
                   EngineResourceType.SOURCE, ProductResourceType.SOURCE, "source")
DESTINATION = ActorKind("DESTINATION", Destination, Module.DESTINATIONS, ConnectorType.DESTINATION,
                        EngineResourceType.DESTINATION, ProductResourceType.DESTINATION, "destination")


# ── reads ──────────────────────────────────────────────────────────────────

async def get(session: AsyncSession, ctx: RequestContext, kind: ActorKind, actor_id: uuid.UUID):
    """Always scoped by workspace; a cross-tenant id is a 404, not a 403."""
    row = await session.scalar(
        select(kind.model).where(
            kind.model.id == actor_id,
            kind.model.workspace_id == ctx.workspace_id,
            kind.model.deleted_at.is_(None),
        )
    )
    if row is None:
        raise NotFoundError("Không tìm thấy kết nối này trong workspace.")
    return row


async def list_actors(
    session: AsyncSession,
    ctx: RequestContext,
    kind: ActorKind,
    *,
    query: str | None = None,
    connector_key: str | None = None,
    health: str | None = None,
    status: str | None = None,
    usage: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Any], int, dict[str, int]]:
    base = select(kind.model).where(
        kind.model.workspace_id == ctx.workspace_id,
        kind.model.deleted_at.is_(None),
    )
    if query:
        needle = f"%{query.lower()}%"
        base = base.where(or_(
            func.lower(kind.model.name).like(needle),
            func.lower(kind.model.connector_key).like(needle),
        ))
    if connector_key:
        base = base.where(kind.model.connector_key == connector_key)
    if health:
        base = base.where(kind.model.health_status == as_enum(health, HealthLevel, field="health"))
    if status:
        base = base.where(kind.model.status == as_enum(status, ResourceStatus, field="status"))

    rows = list((await session.scalars(base.order_by(kind.model.updated_at.desc()))).all())
    usage_map = await pipeline_usage(session, ctx.workspace_id, kind)
    if usage == "used":
        rows = [r for r in rows if usage_map.get(r.id)]
    elif usage == "unused":
        rows = [r for r in rows if not usage_map.get(r.id)]

    summary = {
        "total": len(rows),
        "healthy": sum(1 for r in rows if r.health_status is HealthLevel.HEALTHY),
        "error": sum(1 for r in rows if r.health_status is HealthLevel.ERROR),
        "warning": sum(1 for r in rows if r.health_status is HealthLevel.WARNING),
        "not_tested": sum(1 for r in rows if r.last_test_result is TestResult.NOT_TESTED),
    }
    return rows[offset: offset + limit], len(rows), summary


async def pipeline_usage(
    session: AsyncSession, workspace_id: uuid.UUID, kind: ActorKind
) -> dict[uuid.UUID, int]:
    column = Pipeline.source_id if kind.side == "SOURCE" else Pipeline.destination_id
    rows = await session.execute(
        select(column, func.count(Pipeline.id))
        .where(Pipeline.workspace_id == workspace_id, Pipeline.deleted_at.is_(None))
        .group_by(column)
    )
    return {actor_id: count for actor_id, count in rows.all()}


async def dependent_pipelines(
    session: AsyncSession, workspace_id: uuid.UUID, kind: ActorKind, actor_id: uuid.UUID
) -> list[Pipeline]:
    column = Pipeline.source_id if kind.side == "SOURCE" else Pipeline.destination_id
    return list((await session.scalars(
        select(Pipeline).where(
            Pipeline.workspace_id == workspace_id,
            column == actor_id,
            Pipeline.deleted_at.is_(None),
        )
    )).all())


async def owner_of(session: AsyncSession, user_id: uuid.UUID | None) -> User | None:
    if user_id is None:
        return None
    return await session.get(User, user_id)


# ── engine plumbing ────────────────────────────────────────────────────────

async def resolve_configuration(session: AsyncSession, actor) -> dict[str, Any]:
    """Non-secret config + decrypted credentials, assembled at the last moment."""
    secrets = await secret_store.read(session, actor.secret_ref) if actor.secret_ref else {}
    return catalog.merge_configuration(actor.configuration_json, secrets)


async def engine_ref(session: AsyncSession, kind: ActorKind, actor_id: uuid.UUID) -> str | None:
    mapping = await session.scalar(
        select(EngineMapping).where(
            EngineMapping.product_resource_type == kind.product_resource,
            EngineMapping.product_resource_id == actor_id,
            EngineMapping.engine_resource_type == kind.engine_resource,
        )
    )
    return mapping.engine_resource_ref if mapping else None


async def _upsert_mapping(
    session: AsyncSession, ctx: RequestContext, kind: ActorKind, actor_id: uuid.UUID, ref, version: str | None
) -> None:
    existing = await session.scalar(
        select(EngineMapping).where(
            EngineMapping.product_resource_type == kind.product_resource,
            EngineMapping.product_resource_id == actor_id,
            EngineMapping.engine_resource_type == kind.engine_resource,
        )
    )
    if existing is None:
        session.add(EngineMapping(
            workspace_id=ctx.workspace_id,
            product_resource_type=kind.product_resource,
            product_resource_id=actor_id,
            engine_type=ref.engine_type,
            engine_resource_type=kind.engine_resource,
            engine_resource_ref=ref.ref,
            engine_version=version,
        ))
    else:
        existing.engine_resource_ref = ref.ref
        existing.engine_version = version
    await session.flush()


async def _check(kind: ActorKind, connector: ConnectorDefinition, configuration: dict
                 ) -> ConnectionCheckResult:
    adapter = get_adapter()
    descriptor = catalog.descriptor(connector)
    if kind.side == "SOURCE":
        return await adapter.check_source(descriptor, configuration)
    return await adapter.check_destination(descriptor, configuration)


def _apply_check_result(actor, result: ConnectionCheckResult) -> None:
    actor.last_test_at = utcnow()
    actor.last_test_result = TestResult.PASSED if result.succeeded else TestResult.FAILED
    if result.succeeded:
        actor.health_status = HealthLevel.HEALTHY
        actor.health_code = None
        actor.health_message = None
    else:
        actor.health_status = HealthLevel.ERROR
        actor.health_code = result.error_code
        actor.health_message = result.message


# ── mutations ──────────────────────────────────────────────────────────────

async def test_payload(
    session: AsyncSession,
    ctx: RequestContext,
    kind: ActorKind,
    connector_key: str,
    configuration: dict,
    credentials: dict,
) -> tuple[ConnectionCheckResult, str | None]:
    """Test an unsaved form -- nothing is persisted, nothing is written to the
    engine, and the credentials never leave this call. On success we hand back a
    signed token so the follow-up save does not have to pay for the check twice."""
    ctx.require(kind.module, Action.OPERATE)
    connector = await catalog.require_usable(session, connector_key, kind.connector_type)
    merged = catalog.apply_spec_defaults(
        connector.spec_schema, catalog.merge_configuration(configuration, credentials)
    )
    catalog.validate_against_spec(connector.spec_schema, merged)
    result = await _check(kind, connector, merged)
    token = (
        issue_check_token(kind.side, connector.connector_key, merged)
        if result.succeeded else None
    )
    return result, token


async def create(
    session: AsyncSession, ctx: RequestContext, kind: ActorKind, payload
) -> Any:
    """Create saga (section 25.1): secret -> engine -> product row -> audit."""
    ctx.require(kind.module, Action.CREATE)
    connector = await catalog.require_usable(session, payload.connector_key, kind.connector_type)

    clash = await session.scalar(
        select(kind.model).where(
            kind.model.workspace_id == ctx.workspace_id,
            func.lower(kind.model.name) == payload.name.lower(),
            kind.model.deleted_at.is_(None),
        )
    )
    if clash is not None:
        raise ValidationError(f"Đã có kết nối tên '{payload.name}' trong workspace.")

    config, spec_secrets = catalog.split_configuration(connector.spec_schema, payload.configuration)
    secrets = {**spec_secrets, **(payload.credentials or {})}
    # A completed OAuth consent, resolved here rather than in the browser. The
    # grant is single-use and scoped to this workspace and this connector, so a
    # handle leaked from one form cannot attach somebody else's account to a
    # different resource.
    grant_id = getattr(payload, "oauth_grant_id", None)
    if grant_id is not None:
        secrets = {**secrets, **await oauth.consume_grant(
            session, grant_id, workspace_id=ctx.workspace_id,
            connector_key=connector.connector_key)}
    merged = catalog.apply_spec_defaults(
        connector.spec_schema, catalog.merge_configuration(config, secrets)
    )
    catalog.validate_against_spec(connector.spec_schema, merged)

    allow_untested = bool(ctx.workspace_settings.get("allow_save_without_test"))
    prechecked = verify_check_token(
        getattr(payload, "check_token", None), kind.side, connector.connector_key, merged
    )
    check: ConnectionCheckResult | None = (
        ConnectionCheckResult(succeeded=True, message="Đã kiểm tra ở bước trước.")
        if prechecked else None
    )
    if check is None and (payload.test_before_save or not allow_untested):
        check = await _check(kind, connector, merged)
        if not check.succeeded:
            raise error_from_matrix(
                check.error_code or f"{kind.side}_CONFIGURATION_INVALID",
                message=check.message,
                technical_message=check.technical_message,
            )

    actor_id = uuid.uuid4()
    secret_ref = (
        await secret_store.write(session, ctx.workspace_id, secrets) if secrets else None
    )

    adapter = get_adapter()
    request = EngineActorRequest(
        workspace_id=ctx.workspace_id, product_resource_id=actor_id, name=payload.name,
        connector=catalog.descriptor(connector), configuration=merged,
    )
    # Durable intent, committed on its own connection *before* the engine is
    # touched. Everything below this line can die -- the process, the
    # transaction, the database connection -- and the ledger still knows an
    # engine resource may exist holding this customer's credentials. Without it
    # a rollback after a successful engine call left that resource invisible:
    # in no list, deletable by nobody, known to nothing.
    operation_id, is_retry = await outbox.begin(
        ctx.workspace_id, kind.side, "CREATE", actor_id,
        {"connector_key": connector.connector_key, "name": payload.name})
    try:
        # On a retry, the previous attempt may already have created the engine
        # resource before dying. The engine has no idempotency key -- calling
        # create again would simply make a second one, and the first would be
        # the orphan this whole mechanism exists to prevent.
        ref = None
        if is_retry:
            finder = getattr(adapter, "find_by_product_id", None)
            if finder is not None:
                existing_ref = await finder(kind.side, str(actor_id))
                if existing_ref:
                    ref = existing_ref
        if ref is None:
            ref = (await adapter.create_source(request) if kind.side == "SOURCE"
                   else await adapter.create_destination(request))
    except AppError as exc:
        # The engine call itself failed, so there is nothing on the engine to
        # compensate. The secret is the only thing written so far.
        await outbox.failed(operation_id, str(exc))
        if secret_ref:
            await secret_store.delete(session, secret_ref)
        raise
    await outbox.engine_created(operation_id, ref)

    actor = kind.model(
        id=actor_id,
        workspace_id=ctx.workspace_id,
        name=payload.name,
        description=payload.description,
        connector_key=connector.connector_key,
        connector_version=connector.version,
        configuration_json=config,
        secret_ref=secret_ref,
        status=ResourceStatus.ACTIVE,
        created_by=ctx.user_id,
        updated_by=ctx.user_id,
    )
    if check is not None:
        _apply_check_result(actor, check)
    session.add(actor)
    connector.usage_count += 1
    await session.flush()

    await _upsert_mapping(session, ctx, kind, actor_id, ref, connector.version)
    await audit.record(
        session, ctx, f"{kind.audit_prefix}.created",
        resource_type=kind.side, resource_id=actor.id, resource_name=actor.name,
        after={"connector_key": connector.connector_key, "configuration": config,
               "tested": check.succeeded if check else False},
    )
    log_event(logger, logging.INFO, "actor.created", side=kind.side,
              resource_id=str(actor.id), connector=connector.connector_key)
    # Closed only once the route's transaction lands. If it never does, the row
    # stays ENGINE_CREATED and the sweeper removes the engine resource.
    outbox.close_on_commit(session, operation_id)
    return actor


async def update(session: AsyncSession, ctx: RequestContext, kind: ActorKind,
                 actor_id: uuid.UUID, payload) -> Any:
    ctx.require(kind.module, Action.EDIT)
    actor = await get(session, ctx, kind, actor_id)
    if payload.version is not None and payload.version != actor.version:
        raise ResourceModifiedError()

    connector = await catalog.get_connector(session, actor.connector_key)
    before = {"name": actor.name, "configuration": actor.configuration_json}

    if payload.name and payload.name != actor.name:
        clash = await session.scalar(
            select(kind.model).where(
                kind.model.workspace_id == ctx.workspace_id,
                func.lower(kind.model.name) == payload.name.lower(),
                kind.model.id != actor.id,
                kind.model.deleted_at.is_(None),
            )
        )
        if clash is not None:
            raise ValidationError(f"Đã có kết nối tên '{payload.name}' trong workspace.")
        actor.name = payload.name
    if payload.description is not None:
        actor.description = payload.description

    config = actor.configuration_json
    inline_secrets: dict[str, Any] = {}
    if payload.configuration is not None:
        config, inline_secrets = catalog.split_configuration(
            connector.spec_schema, payload.configuration
        )

    # Omitted credentials mean "unchanged"; a present-but-masked value is also
    # treated as unchanged so re-submitting the loaded form is safe.
    incoming = dict(payload.credentials or {})
    incoming.update(inline_secrets)
    incoming = {k: v for k, v in incoming.items() if v not in ("", "********")}

    stored = await secret_store.read(session, actor.secret_ref) if actor.secret_ref else {}
    secrets = {**stored, **incoming}
    merged = catalog.apply_spec_defaults(
        connector.spec_schema, catalog.merge_configuration(config, secrets)
    )
    catalog.validate_against_spec(connector.spec_schema, merged)

    prechecked = verify_check_token(
        getattr(payload, "check_token", None), kind.side, connector.connector_key, merged
    )
    check: ConnectionCheckResult | None = (
        ConnectionCheckResult(succeeded=True, message="Đã kiểm tra ở bước trước.")
        if prechecked else None
    )
    if check is None and (payload.test_before_save or incoming):
        check = await _check(kind, connector, merged)
        if not check.succeeded and payload.test_before_save:
            raise error_from_matrix(
                check.error_code or f"{kind.side}_CONFIGURATION_INVALID",
                message=check.message, technical_message=check.technical_message,
            )

    actor.configuration_json = config
    if incoming:
        actor.secret_ref = await secret_store.write(
            session, ctx.workspace_id, secrets, ref=actor.secret_ref
        )
    if check is not None:
        _apply_check_result(actor, check)
    actor.updated_by = ctx.user_id
    actor.version += 1
    await session.flush()

    ref = await engine_ref(session, kind, actor.id)
    if ref:
        adapter = get_adapter()
        request = EngineActorRequest(
            workspace_id=ctx.workspace_id, product_resource_id=actor.id, name=actor.name,
            connector=catalog.descriptor(connector), configuration=merged,
        )
        updated = (await adapter.update_source(ref, request) if kind.side == "SOURCE"
                   else await adapter.update_destination(ref, request))
        await _upsert_mapping(session, ctx, kind, actor.id, updated, connector.version)

    await audit.record(
        session, ctx,
        f"{kind.audit_prefix}.credentials.updated" if incoming else f"{kind.audit_prefix}.updated",
        resource_type=kind.side, resource_id=actor.id, resource_name=actor.name,
        before=before, after={"name": actor.name, "configuration": config},
    )
    return actor


async def test_existing(session: AsyncSession, ctx: RequestContext, kind: ActorKind,
                        actor_id: uuid.UUID) -> tuple[Any, ConnectionCheckResult]:
    ctx.require(kind.module, Action.OPERATE)
    actor = await get(session, ctx, kind, actor_id)
    connector = await catalog.get_connector(session, actor.connector_key)
    configuration = catalog.apply_spec_defaults(
        connector.spec_schema, await resolve_configuration(session, actor)
    )
    result = await _check(kind, connector, configuration)
    _apply_check_result(actor, result)
    await session.flush()
    await audit.record(
        session, ctx,
        f"{kind.audit_prefix}.test.{'succeeded' if result.succeeded else 'failed'}",
        resource_type=kind.side, resource_id=actor.id, resource_name=actor.name,
        after={"error_code": result.error_code} if not result.succeeded else None,
    )
    return actor, result


async def set_enabled(session: AsyncSession, ctx: RequestContext, kind: ActorKind,
                      actor_id: uuid.UUID, enabled: bool) -> Any:
    ctx.require(kind.module, Action.EDIT)
    actor = await get(session, ctx, kind, actor_id)
    if not enabled:
        active = [p for p in await dependent_pipelines(session, ctx.workspace_id, kind, actor_id)
                  if p.status is PipelineStatus.ACTIVE]
        if active:
            raise ResourceInUseError(
                f"Kết nối đang được {len(active)} pipeline đang bật sử dụng.",
                constraints=[{"type": "PIPELINE", "id": str(p.id), "name": p.name} for p in active],
            )
    actor.status = ResourceStatus.ACTIVE if enabled else ResourceStatus.DISABLED
    actor.updated_by = ctx.user_id
    await session.flush()
    await audit.record(
        session, ctx, f"{kind.audit_prefix}.{'enabled' if enabled else 'disabled'}",
        resource_type=kind.side, resource_id=actor.id, resource_name=actor.name,
    )
    return actor


async def delete(session: AsyncSession, ctx: RequestContext, kind: ActorKind,
                 actor_id: uuid.UUID, *, force: bool = False) -> None:
    """Delete saga (section 25.2). Dependencies block with a 409 carrying the
    exact list the FE's DeleteConstraintModal renders."""
    ctx.require(kind.module, Action.DELETE)
    actor = await get(session, ctx, kind, actor_id)
    dependents = await dependent_pipelines(session, ctx.workspace_id, kind, actor_id)
    if dependents and not force:
        raise ResourceInUseError(
            f"{'Source' if kind.side == 'SOURCE' else 'Destination'} đang được "
            f"{len(dependents)} pipeline sử dụng.",
            constraints=[{"type": "PIPELINE", "id": str(p.id), "name": p.name} for p in dependents],
        )
    if dependents and force and not ctx.can(Module.PIPELINES, Action.DELETE):
        raise ResourceInUseError("Cần quyền xóa pipeline để xóa cưỡng bức.")

    actor.status = ResourceStatus.DELETE_PENDING
    await session.flush()

    ref = await engine_ref(session, kind, actor.id)
    adapter = get_adapter()
    try:
        if ref:
            if kind.side == "SOURCE":
                await adapter.delete_source(ref)
            else:
                await adapter.delete_destination(ref)
    except AppError as exc:
        # Stay DELETE_PENDING and let the cleanup worker retry; never pretend.
        log_event(logger, logging.WARNING, "actor.engine_delete_failed",
                  resource_id=str(actor.id), error=str(exc))
        await audit.record(
            session, ctx, f"{kind.audit_prefix}.delete.pending",
            resource_type=kind.side, resource_id=actor.id, resource_name=actor.name,
        )
        return

    if actor.secret_ref:
        await secret_store.delete(session, actor.secret_ref)
        actor.secret_ref = None
    actor.status = ResourceStatus.DELETED
    actor.deleted_at = utcnow()
    await session.flush()
    await audit.record(
        session, ctx, f"{kind.audit_prefix}.deleted",
        resource_type=kind.side, resource_id=actor.id, resource_name=actor.name,
        before={"name": actor.name, "connector_key": actor.connector_key},
    )


async def last_discovery(session: AsyncSession, source_id: uuid.UUID) -> datetime | None:
    return await session.scalar(
        select(func.max(SchemaSnapshot.discovered_at)).where(SchemaSnapshot.source_id == source_id)
    )


def available_actions(ctx: RequestContext, kind: ActorKind, actor) -> list[str]:
    """Backend-computed action list (section 76) so the FE never re-derives it."""
    actions: list[str] = []
    if ctx.can(kind.module, Action.OPERATE):
        actions.append("TEST")
    if ctx.can(kind.module, Action.EDIT):
        actions.append("EDIT")
        actions.append("UPDATE_CREDENTIALS")
        actions.append("DISABLE" if actor.status is ResourceStatus.ACTIVE else "ENABLE")
    if ctx.can(kind.module, Action.DELETE):
        actions.append("DELETE")
    if kind.side == "SOURCE" and ctx.can(Module.SOURCES, Action.OPERATE):
        actions.append("DISCOVER")
    return actions


def health_block(actor) -> dict[str, Any]:
    labels = {
        HealthLevel.HEALTHY: "Hoạt động tốt",
        HealthLevel.WARNING: "Cần theo dõi",
        HealthLevel.ERROR: "Cần xử lý",
        HealthLevel.UNKNOWN: "Chưa kiểm tra",
    }
    level = actor.health_status
    if actor.status is ResourceStatus.DISABLED:
        return {"level": "UNKNOWN", "code": "DISABLED", "label": "Đã tắt",
                "last_checked_at": actor.last_test_at, "message": None}
    return {
        "level": level.value,
        "code": actor.health_code,
        "label": labels.get(level, "Không rõ"),
        "last_checked_at": actor.last_test_at,
        "message": actor.health_message,
    }


def category_of(result: ConnectionCheckResult) -> str | None:
    return result.category.value if isinstance(result.category, ErrorCategory) else None
