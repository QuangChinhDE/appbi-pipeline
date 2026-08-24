"""ORM -> API shapes.

Keeping serialization here means no route ever leaks an engine ref or a secret
by accident: what the FE receives is defined in exactly one place.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import RequestContext
from app.models.engine import ConnectorDefinition
from app.models.enums import PipelineHealth, ScheduleType
from app.models.identity import User
from app.models.integration import Pipeline, PipelineStream
from app.models.run import PipelineRun
from app.schemas.common import ActorRef, HealthBlock, UserRef
from app.schemas.domain import (
    ActorDetail, ActorView, ConnectorDetail, ConnectorView, PipelineDetail, PipelineMetrics,
    PipelineStreamView, PipelineView, RunAttemptView, RunDetail, RunError, RunRef, RunStreamStat,
    RunView, ScheduleConfig,
)
from app.services import actors as actor_service, pipelines as pipeline_service, schema_service


def user_ref(user: User | None) -> UserRef | None:
    if user is None:
        return None
    return UserRef(id=user.id, full_name=user.full_name, email=user.email)


def connector_view(connector: ConnectorDefinition) -> ConnectorView:
    from app.models.enums import Certification, ConnectorStatus

    from app.core.config import settings

    # Whether the catalogue offers it, not merely whether the engine could run
    # it. The two were the same thing while every connector was selectable, and
    # that is how a product ends up promising 654 connectors it has certified 3
    # of. `require_usable` applies the identical rule on the create path -- a
    # greyed-out card with a working endpoint behind it is decoration.
    offered = settings.connector_is_offered(
        connector.connector_key, connector.certification.value)
    selectable = connector.status is ConnectorStatus.ACTIVE and offered

    reason = connector.disabled_reason
    if reason is None and not offered and connector.certification is Certification.BETA:
        reason = ("Connector này chưa được chứng nhận cho bản phát hành hiện "
                  "tại. Quản trị viên có thể bật riêng từng connector.")
    return ConnectorView(
        connector_key=connector.connector_key,
        display_name=connector.display_name,
        connector_type=connector.connector_type.value,
        category=connector.category,
        description=connector.description,
        icon=connector.icon,
        icon_url=connector.icon_url,
        documentation_url=connector.documentation_url,
        version=connector.display_version or connector.version,
        latest_version=connector.latest_version,
        release_stage=connector.release_stage,
        support_level=connector.support_level,
        certification=connector.certification.value,
        status=connector.status.value,
        disabled_reason=reason,
        supports_oauth=connector.supports_oauth,
        supports_incremental=connector.supports_incremental,
        supports_cdc=connector.supports_cdc,
        supports_namespaces=connector.supports_namespaces,
        supported_destination_sync_modes=connector.supported_destination_sync_modes or [],
        image_pulled=connector.image_pulled,
        last_refreshed_at=connector.last_refreshed_at,
        usage_count=connector.usage_count,
        update_available=bool(
            connector.latest_version and connector.latest_version != connector.version
        ),
        selectable=selectable,
    )


def connector_detail(connector: ConnectorDefinition) -> ConnectorDetail:
    base = connector_view(connector).model_dump()
    return ConnectorDetail(**base, spec_schema=connector.spec_schema,
                           spec_source=connector.spec_source)


def actor_view(
    ctx: RequestContext,
    kind,
    actor,
    *,
    connector: ConnectorDefinition | None,
    pipeline_count: int,
    owner: User | None,
) -> ActorView:
    return ActorView(
        id=actor.id,
        name=actor.name,
        description=actor.description,
        connector_key=actor.connector_key,
        connector_display_name=connector.display_name if connector else None,
        connector_icon=connector.icon if connector else None,
        connector_version=actor.connector_version,
        status=actor.status.value,
        health=HealthBlock(**actor_service.health_block(actor)),
        last_test_at=actor.last_test_at,
        last_test_result=actor.last_test_result.value,
        pipeline_count=pipeline_count,
        owner=user_ref(owner),
        created_at=actor.created_at,
        updated_at=actor.updated_at,
        version=actor.version,
        available_actions=actor_service.available_actions(ctx, kind, actor),
    )


def actor_detail(
    ctx: RequestContext,
    kind,
    actor,
    *,
    connector: ConnectorDefinition | None,
    pipeline_count: int,
    owner: User | None,
    credentials: dict[str, Any],
    last_discovered_at=None,
) -> ActorDetail:
    base = actor_view(ctx, kind, actor, connector=connector,
                      pipeline_count=pipeline_count, owner=owner).model_dump()
    return ActorDetail(
        **base,
        configuration=actor.configuration_json or {},
        credentials=credentials,
        spec_schema=connector.spec_schema if connector else {},
        active_schema_snapshot_id=getattr(actor, "active_schema_snapshot_id", None),
        last_discovered_at=last_discovered_at,
    )


def actor_ref(actor, connector: ConnectorDefinition | None) -> ActorRef:
    return ActorRef(
        id=actor.id,
        name=actor.name,
        connector_key=actor.connector_key,
        connector_display_name=connector.display_name if connector else None,
        icon=connector.icon if connector else None,
    )


def schedule_config(pipeline: Pipeline) -> ScheduleConfig:
    config = pipeline.schedule_config or {}
    return ScheduleConfig(
        type=pipeline.schedule_type.value,
        interval_seconds=config.get("interval_seconds"),
        time_of_day=config.get("time_of_day"),
        cron_expression=config.get("cron_expression"),
        timezone=config.get("timezone") or pipeline.timezone,
    )


def run_ref(run: PipelineRun | None) -> RunRef | None:
    if run is None:
        return None
    duration = (
        (run.ended_at - run.started_at).total_seconds()
        if run.started_at and run.ended_at else None
    )
    return RunRef(
        id=run.id, status=run.status.value, trigger_type=run.trigger_type.value,
        started_at=run.started_at, ended_at=run.ended_at,
        duration_seconds=round(duration, 1) if duration is not None else None,
        records_synced=run.records_synced,
        error_category=run.error_category.value if run.error_category else None,
    )


def pipeline_view(
    ctx: RequestContext,
    pipeline: Pipeline,
    *,
    health: PipelineHealth,
    source,
    destination,
    source_connector,
    destination_connector,
    last_run: PipelineRun | None,
    stream_count: int,
    owner: User | None,
) -> PipelineView:
    return PipelineView(
        id=pipeline.id,
        name=pipeline.name,
        description=pipeline.description,
        status=pipeline.status.value,
        health=HealthBlock(**pipeline_service.health_block(pipeline, health)),
        source=actor_ref(source, source_connector),
        destination=actor_ref(destination, destination_connector),
        schedule=schedule_config(pipeline),
        next_run_at=pipeline.next_run_at,
        last_run=run_ref(last_run),
        stream_count=stream_count,
        owner=user_ref(owner),
        created_at=pipeline.created_at,
        updated_at=pipeline.updated_at,
        version=pipeline.version,
        available_actions=pipeline_service.available_actions(ctx, pipeline, health),
    )


def stream_view(stream: PipelineStream) -> PipelineStreamView:
    fields = schema_service.field_list(stream.json_schema or {})
    return PipelineStreamView(
        id=stream.id,
        name=stream.stream_name,
        namespace=stream.namespace,
        selected=stream.selected,
        sync_mode=stream.sync_mode.value,
        destination_sync_mode=stream.destination_sync_mode.value,
        cursor_fields=list(stream.cursor_fields or []),
        primary_key_fields=[list(pk) for pk in (stream.primary_key_fields or [])],
        field_count=len(fields),
        fields=fields,
    )


def pipeline_detail(
    base: PipelineView,
    pipeline: Pipeline,
    *,
    metrics: dict[str, Any],
    recent: list[PipelineRun],
    snapshot_at=None,
    schema_change_pending: bool = False,
) -> PipelineDetail:
    return PipelineDetail(
        **base.model_dump(),
        streams=[stream_view(s) for s in sorted(
            pipeline.streams, key=lambda s: (s.namespace or "", s.stream_name)
        )],
        metrics=PipelineMetrics(**metrics),
        recent_runs=[run_ref(r) for r in recent if r is not None],
        active_schema_snapshot_id=pipeline.active_schema_snapshot_id,
        schema_snapshot_at=snapshot_at,
        schema_change_pending=schema_change_pending,
        needs_review_reason=pipeline.needs_review_reason,
        namespace_format=pipeline.namespace_format,
        stream_prefix=pipeline.stream_prefix,
        overlap_policy=pipeline.overlap_policy.value,
    )


def run_error(run: PipelineRun, *, include_technical: bool) -> RunError | None:
    if not run.error_code and not run.error_summary:
        return None
    technical = None
    if include_technical:
        technical = (run.technical_metadata or {}).get("technical_message")
    return RunError(
        code=run.error_code,
        category=run.error_category.value if run.error_category else None,
        summary=run.error_summary,
        remediation_action=run.remediation_action,
        technical_message=technical,
    )


def run_view(
    ctx: RequestContext,
    run: PipelineRun,
    *,
    pipeline_ref: ActorRef | None,
    triggered_by: User | None,
    is_stale: bool,
) -> RunView:
    from app.services import runs as run_service

    duration = (
        (run.ended_at - run.started_at).total_seconds()
        if run.started_at and run.ended_at else None
    )
    return RunView(
        id=run.id,
        short_id=str(run.id)[:8],
        pipeline=pipeline_ref,
        status=run.status.value,
        trigger_type=run.trigger_type.value,
        triggered_by=user_ref(triggered_by),
        retry_of_run_id=run.retry_of_run_id,
        queue_reason=run.queue_reason,
        started_at=run.started_at,
        ended_at=run.ended_at,
        created_at=run.created_at,
        duration_seconds=round(duration, 1) if duration is not None else None,
        records_synced=run.records_synced,
        bytes_synced=run.bytes_synced,
        error=run_error(run, include_technical=True),
        is_stale=is_stale,
        actions=run_service.actions_for(ctx, run),
    )


def run_detail(
    base: RunView,
    run: PipelineRun,
    *,
    stream_stats: list[Any],
    source_ref: ActorRef | None,
    destination_ref: ActorRef | None,
) -> RunDetail:
    attempts = [
        RunAttemptView(
            attempt_number=a.attempt_number,
            status=a.status.value,
            started_at=a.started_at,
            ended_at=a.ended_at,
            duration_seconds=(
                round((a.ended_at - a.started_at).total_seconds(), 1)
                if a.started_at and a.ended_at else None
            ),
            records_synced=a.records_synced,
            bytes_synced=a.bytes_synced,
            failure_summary=a.failure_summary,
        )
        for a in run.attempts
    ]
    metadata = {
        key: value for key, value in (run.technical_metadata or {}).items()
        if key in ("engine_status", "trace_id")
    }
    return RunDetail(
        **base.model_dump(),
        attempts=attempts,
        stream_stats=[
            RunStreamStat(
                stream_name=s.stream_name, namespace=s.namespace,
                records_emitted=s.records_emitted, bytes_emitted=s.bytes_emitted,
                status=s.status,
            )
            for s in stream_stats
        ],
        source=source_ref,
        destination=destination_ref,
        trace_id=(run.technical_metadata or {}).get("trace_id"),
        technical_metadata=metadata,
    )
