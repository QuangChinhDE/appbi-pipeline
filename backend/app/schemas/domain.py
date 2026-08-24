"""Request/response models for the whole product API surface."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import (
    ActorRef, CredentialsView, HealthBlock, ORMModel, UserRef,
)

# Deliberately not pydantic's EmailStr: it rejects special-use domains such as
# `.local`, which are exactly what a self-hosted deployment uses. The address is
# an identifier here, not a delivery target, so shape validation is enough.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class _EmailMixin:
    @field_validator("email", check_fields=False)
    @classmethod
    def _validate_email(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if not _EMAIL_RE.match(cleaned):
            raise ValueError("Email không hợp lệ.")
        return cleaned

# ── auth / workspace ───────────────────────────────────────────────────────


class LoginRequest(_EmailMixin, BaseModel):
    email: str = Field(max_length=255)
    password: str = Field(min_length=1, max_length=200)


class WorkspaceSummary(ORMModel):
    id: uuid.UUID
    name: str
    slug: str
    role: str | None = None
    timezone: str = "Asia/Bangkok"
    status: str = "ACTIVE"


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class CurrentUser(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    locale: str
    is_platform_admin: bool
    workspace: WorkspaceSummary | None = None
    workspaces: list[WorkspaceSummary] = Field(default_factory=list)
    role: str | None = None
    permissions: dict[str, list[str]] = Field(default_factory=dict)
    # True for an account created from the bootstrap one-time secret. The FE
    # sends the user straight to the change-password screen; the API refuses
    # everything else regardless of what the FE does.
    password_change_required: bool = False


class WorkspaceSettingsUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=100)
    timezone: str | None = None
    allow_save_without_test: bool | None = None
    auto_accept_additive_schema: bool | None = None


class MemberView(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    email: str
    full_name: str
    role: str
    created_at: datetime


class MemberInvite(_EmailMixin, BaseModel):
    email: str = Field(max_length=255)
    full_name: str = Field(min_length=1, max_length=255)
    role: str
    password: str = Field(min_length=8, max_length=200)


class MemberRoleUpdate(BaseModel):
    role: str


# ── connectors ─────────────────────────────────────────────────────────────


class ConnectorView(ORMModel):
    connector_key: str
    display_name: str
    connector_type: str
    category: str
    description: str | None = None
    icon: str | None = None
    icon_url: str | None = None
    documentation_url: str | None = None
    version: str
    latest_version: str | None = None
    release_stage: str
    support_level: str = "community"
    certification: str
    status: str
    disabled_reason: str | None = None
    supports_oauth: bool
    supports_incremental: bool
    supports_cdc: bool
    supports_namespaces: bool
    supported_destination_sync_modes: list[str] = Field(default_factory=list)
    image_pulled: bool = False
    last_refreshed_at: datetime | None = None
    usage_count: int = 0
    update_available: bool = False
    selectable: bool = True


class ConnectorDetail(ConnectorView):
    spec_schema: dict[str, Any] = Field(default_factory=dict)
    spec_source: str = "BUNDLED"


# ── sources / destinations ─────────────────────────────────────────────────


class ActorCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    connector_key: str
    description: str | None = Field(default=None, max_length=2000)
    configuration: dict[str, Any] = Field(default_factory=dict)
    credentials: dict[str, Any] = Field(default_factory=dict)
    test_before_save: bool = True
    # Signed proof that this exact configuration already passed a check in the
    # wizard, so saving does not repeat a slow connector run.
    check_token: str | None = None

    @field_validator("name")
    @classmethod
    def _trim(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("Tên không được để trống.")
        return trimmed


class ActorUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    configuration: dict[str, Any] | None = None
    # Omitted => credential unchanged. Present => replace (section 21.3).
    credentials: dict[str, Any] | None = None
    test_before_save: bool = False
    check_token: str | None = None
    version: int | None = None


class ActorTestRequest(BaseModel):
    """Test an unsaved form, or re-test what is stored."""

    connector_key: str | None = None
    configuration: dict[str, Any] | None = None
    credentials: dict[str, Any] | None = None


class ActorTestResult(BaseModel):
    succeeded: bool
    check_token: str | None = None
    message: str | None = None
    error_code: str | None = None
    category: str | None = None
    technical_message: str | None = None
    duration_ms: int | None = None
    tested_at: datetime


class ActorView(ORMModel):
    id: uuid.UUID
    name: str
    description: str | None = None
    connector_key: str
    connector_display_name: str | None = None
    connector_icon: str | None = None
    connector_version: str | None = None
    status: str
    health: HealthBlock
    last_test_at: datetime | None = None
    last_test_result: str
    pipeline_count: int = 0
    owner: UserRef | None = None
    created_at: datetime
    updated_at: datetime
    version: int = 1
    available_actions: list[str] = Field(default_factory=list)


class ActorDetail(ActorView):
    configuration: dict[str, Any] = Field(default_factory=dict)
    credentials: CredentialsView = Field(default_factory=CredentialsView)
    spec_schema: dict[str, Any] = Field(default_factory=dict)
    active_schema_snapshot_id: uuid.UUID | None = None
    last_discovered_at: datetime | None = None


# ── schema ─────────────────────────────────────────────────────────────────


class StreamCapability(BaseModel):
    name: str
    namespace: str | None = None
    supported_sync_modes: list[str]
    source_defined_cursor: bool = False
    default_cursor_field: list[str] = Field(default_factory=list)
    source_defined_primary_key: list[list[str]] = Field(default_factory=list)
    fields: list[dict[str, Any]] = Field(default_factory=list)
    unsupported_reason: str | None = None


class SchemaSnapshotView(BaseModel):
    id: uuid.UUID
    source_id: uuid.UUID
    discovered_at: datetime
    catalog_hash: str
    stream_count: int
    connector_version: str | None = None
    streams: list[StreamCapability] = Field(default_factory=list)


class SchemaChange(BaseModel):
    kind: Literal["STREAM_ADDED", "STREAM_REMOVED", "FIELD_ADDED", "FIELD_REMOVED",
                  "FIELD_TYPE_CHANGED", "CURSOR_REMOVED", "PRIMARY_KEY_CHANGED"]
    severity: str
    namespace: str | None = None
    stream_name: str
    field_name: str | None = None
    before: str | None = None
    after: str | None = None
    message: str


class SchemaDiffView(BaseModel):
    pipeline_id: uuid.UUID
    from_snapshot_id: uuid.UUID | None
    to_snapshot_id: uuid.UUID | None
    generated_at: datetime
    has_breaking: bool
    added: list[SchemaChange] = Field(default_factory=list)
    removed: list[SchemaChange] = Field(default_factory=list)
    changed: list[SchemaChange] = Field(default_factory=list)


class SchemaApproveRequest(BaseModel):
    snapshot_id: uuid.UUID
    drop_removed_streams: bool = True


# ── pipelines ──────────────────────────────────────────────────────────────


class ScheduleConfig(BaseModel):
    type: Literal["MANUAL", "INTERVAL", "DAILY", "CRON"] = "MANUAL"
    interval_seconds: int | None = None
    time_of_day: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    cron_expression: str | None = None
    timezone: str = "Asia/Bangkok"


class StreamSelection(BaseModel):
    name: str
    namespace: str | None = None
    selected: bool = True
    sync_mode: Literal["full_refresh", "incremental"] = "full_refresh"
    destination_sync_mode: Literal["overwrite", "append", "append_dedup"] = "overwrite"
    cursor_fields: list[str] = Field(default_factory=list)
    primary_key_fields: list[list[str]] = Field(default_factory=list)
    selected_fields: list[str] | None = None


class PipelineCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    source_id: uuid.UUID
    destination_id: uuid.UUID
    schema_snapshot_id: uuid.UUID | None = None
    streams: list[StreamSelection] = Field(default_factory=list)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    overlap_policy: Literal["SKIP_IF_RUNNING", "QUEUE"] = "SKIP_IF_RUNNING"
    namespace_format: str | None = None
    stream_prefix: str | None = None
    run_first_sync: bool = True


class PipelineUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    streams: list[StreamSelection] | None = None
    schedule: ScheduleConfig | None = None
    overlap_policy: Literal["SKIP_IF_RUNNING", "QUEUE"] | None = None
    namespace_format: str | None = None
    stream_prefix: str | None = None
    version: int | None = None


class RunRef(BaseModel):
    id: uuid.UUID
    status: str
    trigger_type: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_seconds: float | None = None
    records_synced: int | None = None
    error_category: str | None = None


class PipelineView(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None = None
    status: str
    health: HealthBlock
    source: ActorRef
    destination: ActorRef
    schedule: ScheduleConfig
    next_run_at: datetime | None = None
    last_run: RunRef | None = None
    stream_count: int = 0
    owner: UserRef | None = None
    created_at: datetime
    updated_at: datetime
    version: int = 1
    available_actions: list[str] = Field(default_factory=list)


class PipelineStreamView(BaseModel):
    id: uuid.UUID
    name: str
    namespace: str | None = None
    selected: bool
    sync_mode: str
    destination_sync_mode: str
    cursor_fields: list[str] = Field(default_factory=list)
    primary_key_fields: list[list[str]] = Field(default_factory=list)
    field_count: int = 0
    fields: list[dict[str, Any]] = Field(default_factory=list)


class PipelineMetrics(BaseModel):
    success_rate_7d: float | None = None
    success_rate_30d: float | None = None
    average_duration_seconds: float | None = None
    total_runs_30d: int = 0
    records_synced_30d: int = 0
    last_success_at: datetime | None = None
    consecutive_failures: int = 0


class PipelineDetail(PipelineView):
    streams: list[PipelineStreamView] = Field(default_factory=list)
    metrics: PipelineMetrics = Field(default_factory=PipelineMetrics)
    recent_runs: list[RunRef] = Field(default_factory=list)
    active_schema_snapshot_id: uuid.UUID | None = None
    schema_snapshot_at: datetime | None = None
    schema_change_pending: bool = False
    needs_review_reason: str | None = None
    namespace_format: str | None = None
    stream_prefix: str | None = None
    overlap_policy: str = "SKIP_IF_RUNNING"


# ── runs ───────────────────────────────────────────────────────────────────


class RunTriggerRequest(BaseModel):
    reason: str | None = None


class RunStreamStat(BaseModel):
    stream_name: str
    namespace: str | None = None
    records_emitted: int
    bytes_emitted: int
    status: str


class RunAttemptView(BaseModel):
    attempt_number: int
    status: str
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_seconds: float | None = None
    records_synced: int | None = None
    bytes_synced: int | None = None
    failure_summary: str | None = None


class RunError(BaseModel):
    code: str | None = None
    category: str | None = None
    summary: str | None = None
    remediation_action: str | None = None
    technical_message: str | None = None


class RunView(BaseModel):
    id: uuid.UUID
    short_id: str
    pipeline: ActorRef | None = None
    status: str
    trigger_type: str
    triggered_by: UserRef | None = None
    retry_of_run_id: uuid.UUID | None = None
    queue_reason: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    created_at: datetime
    duration_seconds: float | None = None
    records_synced: int | None = None
    bytes_synced: int | None = None
    error: RunError | None = None
    is_stale: bool = False
    actions: dict[str, bool] = Field(default_factory=dict)


class RunDetail(RunView):
    attempts: list[RunAttemptView] = Field(default_factory=list)
    stream_stats: list[RunStreamStat] = Field(default_factory=list)
    source: ActorRef | None = None
    destination: ActorRef | None = None
    trace_id: str | None = None
    technical_metadata: dict[str, Any] = Field(default_factory=dict)


class RunLogPage(BaseModel):
    run_id: uuid.UUID
    lines: list[str]
    next_cursor: int | None = None
    has_more: bool = False
    total_lines: int | None = None


# ── monitoring / alerts / audit ────────────────────────────────────────────


class OverviewKpis(BaseModel):
    active_pipelines: int = 0
    running_now: int = 0
    failed_last_24h: int = 0
    success_rate_7d: float | None = None
    sources_needing_attention: int = 0
    destinations_needing_attention: int = 0
    total_sources: int = 0
    total_destinations: int = 0
    records_synced_24h: int = 0


class OverviewResponse(BaseModel):
    kpis: OverviewKpis
    recent_failures: list[RunView] = Field(default_factory=list)
    running: list[RunView] = Field(default_factory=list)
    recent_successes: list[RunView] = Field(default_factory=list)
    attention_pipelines: list[PipelineView] = Field(default_factory=list)
    connector_updates: list[ConnectorView] = Field(default_factory=list)
    onboarding: dict[str, bool] = Field(default_factory=dict)


class MonitoringPipelineRow(BaseModel):
    pipeline: PipelineView
    freshness_deadline: datetime | None = None
    freshness_breached: bool = False
    failure_streak: int = 0
    last_success_age_seconds: float | None = None


class MonitoringResponse(BaseModel):
    engine: dict[str, Any] = Field(default_factory=dict)
    pipelines: list[MonitoringPipelineRow] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)


class AlertRuleView(ORMModel):
    id: uuid.UUID
    name: str
    event_type: str
    resource_id: uuid.UUID | None = None
    threshold: int
    channel: str
    channel_config: dict[str, Any] = Field(default_factory=dict)
    cooldown_seconds: int
    enabled: bool
    created_at: datetime


class AlertRuleWrite(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    event_type: str
    resource_id: uuid.UUID | None = None
    threshold: int = Field(default=1, ge=1, le=100)
    channel: Literal["IN_APP", "EMAIL", "WEBHOOK"] = "IN_APP"
    channel_config: dict[str, Any] = Field(default_factory=dict)
    cooldown_seconds: int = Field(default=900, ge=0, le=86400)
    enabled: bool = True


class NotificationView(ORMModel):
    id: uuid.UUID
    event_type: str
    severity: str
    title: str
    body: str | None = None
    resource_type: str | None = None
    resource_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None
    remediation_action: str | None = None
    occurrence_count: int
    status: str
    created_at: datetime
    last_seen_at: datetime | None = None


class AuditEventView(ORMModel):
    id: uuid.UUID
    actor_type: str
    actor_id: uuid.UUID | None = None
    actor_label: str | None = None
    action: str
    resource_type: str | None = None
    resource_id: uuid.UUID | None = None
    resource_name: str | None = None
    result: str
    before_summary: dict[str, Any] | None = None
    after_summary: dict[str, Any] | None = None
    trace_id: str | None = None
    created_at: datetime


class EngineReconcileItem(BaseModel):
    resource_type: str
    resource_id: uuid.UUID
    name: str


class EngineReconcileView(BaseModel):
    """What a restore beside a different engine deployment looks like.

    Carries product ids and names only. The engine handle that was actually
    checked stays below the adapter boundary -- an operator needs to know which
    source is gone, not what the engine calls it (guardrail 3).
    """

    consistent: bool
    engine_reachable: bool
    checked: int
    present: int
    foreign: int = 0
    missing: list[EngineReconcileItem] = Field(default_factory=list)
    detail: str


class EngineStatusView(BaseModel):
    """Tenant users see a generic status; admins see the detail (section 18.3)."""

    label: str
    operational: bool
    engine_type: str | None = None
    version: str | None = None
    detail: str | None = None
    checked_at: datetime | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    adapter_contract_version: str | None = None
    product_version: str | None = None
    reconciliation_lag_seconds: float | None = None
    active_runs: int | None = None
    queued_runs: int | None = None
