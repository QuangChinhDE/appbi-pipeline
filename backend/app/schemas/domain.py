"""Request/response models for the whole product API surface."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any, Literal

from app.core.security import MIN_PASSWORD_LENGTH
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
    # The route runs `password_problems()`, which is the real policy. This
    # bound only stops an obviously-too-short value early; it said 8 while the
    # policy said 12, so an invite could set a password the owner of that
    # account would never be allowed to choose for themselves.
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=200)


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
    # How many tables this connector can read, when that is knowable before a
    # discover. It is for connectors this product defines, because the manifest
    # lists them; for an Airbyte image nobody knows until the connector is run
    # against real credentials, and guessing would be worse than saying nothing.
    stream_count: int | None = None
    #: The tables this connector reads, when they are known without running a
    #: discover. "What will I actually get?" is the question the setup form
    #: cannot answer, and the one people ask before spending a sync finding out.
    stream_names: list[str] = Field(default_factory=list)


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
    # An opaque handle to a completed OAuth consent. The refresh token itself
    # never reaches the browser, so the form sends this instead and the server
    # resolves it into the connector's credentials at save time.
    oauth_grant_id: uuid.UUID | None = None

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
    namespace_format: str | None = Field(default=None, max_length=120)
    stream_prefix: str | None = Field(default=None, max_length=64)
    run_first_sync: bool = True


class PipelineUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    streams: list[StreamSelection] | None = None
    schedule: ScheduleConfig | None = None
    overlap_policy: Literal["SKIP_IF_RUNNING", "QUEUE"] | None = None
    namespace_format: str | None = Field(default=None, max_length=120)
    stream_prefix: str | None = Field(default=None, max_length=64)
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


class StreamSyncState(BaseModel):
    """What the last sync did to one stream.

    The Status view answers "is this stream healthy, how much landed, and how
    stale is it" per stream, not per pipeline -- a pipeline that reports
    SUCCEEDED can still have one stream that read nothing, and a pipeline-level
    record count hides exactly that.

    Every field is already recorded in `pipeline_stream_stats`; until now it
    was written on every run and read by nothing.
    """

    status: str
    records_loaded: int = 0
    bytes_loaded: int = 0
    #: When the stream last landed data. Named for the question it answers on
    #: screen -- "data fresh as of" -- rather than for the run that produced it.
    synced_at: datetime | None = None


class PipelineStreamView(BaseModel):
    id: uuid.UUID
    name: str
    namespace: str | None = None
    selected: bool
    sync_mode: str
    destination_sync_mode: str
    cursor_fields: list[str] = Field(default_factory=list)
    primary_key_fields: list[list[str]] = Field(default_factory=list)
    selected_fields: list[str] | None = None
    field_count: int = 0
    fields: list[dict[str, Any]] = Field(default_factory=list)
    #: None when the stream has never been part of a completed run.
    last_sync: StreamSyncState | None = None


class ConnectionStateView(BaseModel):
    """The engine's replication cursor for one pipeline.

    Answers the question a stalled incremental sync always raises: what
    high-water mark will the next run resume from. Fetched from the engine on
    request rather than stored, because the engine owns it -- the `sync_state`
    column exists and has never been written, so serving it would have shown an
    empty panel for every pipeline forever.

    Lazy on purpose. It hangs off its own endpoint so a slow or unreachable
    engine degrades one collapsed panel instead of the whole settings page.
    """

    supported: bool = True
    state: list[dict[str, Any]] = Field(default_factory=list)
    fetched_at: datetime | None = None
    #: Set when the engine could not answer; the panel says so rather than
    #: rendering an empty state that reads as "no cursor yet".
    unavailable_reason: str | None = None


class ConnectionStateUpdate(BaseModel):
    """A replacement cursor, as the panel hands it back.

    `state` is the whole list, not a patch: the panel edits the document the
    read returned, so a partial update would silently drop the streams the
    editor did not mention -- which reads as "those streams reset" the next
    time a sync runs.
    """

    state: list[dict[str, Any]] = Field(
        description="Empty list means forget the cursor and read from the start.")

    @field_validator("state")
    @classmethod
    def _entries_are_objects(cls, value: list[Any]) -> list[Any]:
        # The panel is a free-text editor over JSON. A list of strings parses
        # fine and is meaningless to every engine, so it is refused here rather
        # than at replication time, hours later, inside a job log.
        for index, entry in enumerate(value):
            if not isinstance(entry, dict):
                raise ValueError(
                    f"phần tử {index} phải là một object, nhận được "
                    f"{type(entry).__name__}")
        return value


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


class TransformDestinationCapability(BaseModel):
    destination: ActorRef
    supported: bool
    certification: str | None = None
    adapter: str | None = None
    dbt_core_version: str | None = None
    adapter_version: str | None = None
    reason: str | None = None


class DataAssetView(BaseModel):
    id: uuid.UUID
    destination_id: uuid.UUID
    catalog_name: str | None = None
    schema_name: str
    relation_name: str
    relation_type: str
    asset_type: str
    owner_type: str
    pipeline_id: uuid.UUID | None = None
    pipeline_name: str | None = None
    pipeline_stream_id: uuid.UUID | None = None
    resolution_status: str
    columns: list[dict[str, Any]] = Field(default_factory=list)
    last_ready_at: datetime | None = None
    fresh_at: datetime | None = None
    # The dbt alias this relation is reachable by, so the editor can offer
    # `{{ source('<source_name>', '<relation_name>') }}` instead of leaving the
    # user to guess it from the warehouse name.
    source_name: str | None = None
    freshness_state: str | None = None


class PipelineInputCandidate(BaseModel):
    pipeline: ActorRef
    last_success_at: datetime | None = None
    streams: list[dict[str, Any]] = Field(default_factory=list)


class TransformInputCandidates(BaseModel):
    destination_id: uuid.UUID
    pipelines: list[PipelineInputCandidate] = Field(default_factory=list)
    assets: list[DataAssetView] = Field(default_factory=list)


class DataAssetRegister(BaseModel):
    catalog_name: str | None = Field(default=None, max_length=200)
    schema_name: str = Field(min_length=1, max_length=200)
    relation_name: str = Field(min_length=1, max_length=300)
    pipeline_id: uuid.UUID | None = None
    pipeline_stream_id: uuid.UUID | None = None



class TransformCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    #: The connection this Transform reads and writes through. It carries the
    #: warehouse, so no Destination is asked for separately.
    warehouse_connection_id: uuid.UUID
    default_schema: str = Field(min_length=1, max_length=200)
    input_asset_ids: list[uuid.UUID] = Field(default_factory=list)


class TransformUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    default_schema: str | None = Field(default=None, min_length=1, max_length=200)
    execution_trigger: Literal["MANUAL", "AFTER_UPSTREAM", "SCHEDULE"] | None = None
    schedule: ScheduleConfig | None = None
    trigger_config: dict[str, Any] | None = None
    input_asset_ids: list[uuid.UUID] | None = None
    version: int | None = None


class RepositoryImportRequest(BaseModel):
    """Where the project lives. The token is used once and never stored."""

    repo_url: str = Field(min_length=1, max_length=500)
    ref: str | None = Field(default=None, max_length=200)
    subdirectory: str | None = Field(default=None, max_length=300)
    token: str | None = Field(default=None, max_length=500)


class RepositoryImportCreate(RepositoryImportRequest):
    name: str = Field(min_length=1, max_length=200)
    destination_id: uuid.UUID
    default_schema: str = Field(min_length=1, max_length=200)
    #: Keep the connection and poll it, so the import is a start not a snapshot.
    auto_pull: bool = False
    interval_minutes: int = Field(default=30, ge=5, le=10080)
    #: The connection to read and run through.
    warehouse_connection_id: uuid.UUID


class ImportedModelView(BaseModel):
    name: str
    path: str
    layer: str
    materialization: str
    sql: str
    description: str | None = None


class ImportedSourceView(BaseModel):
    alias: str
    table: str
    catalog: str | None = None
    schema_name: str
    relation: str
    #: True when the SQL names this table literally instead of declaring it.
    direct: bool = False


class ImportedTestView(BaseModel):
    model: str
    rule: str
    column: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class GitSourceUpdate(BaseModel):
    """Attach, adjust or detach the repository. Omitted fields stay as they are.

    There is no counterpart that writes to the repository: this configures a
    read, and the product has no way to push.
    """

    repo_url: str | None = Field(default=None, max_length=500)
    ref: str | None = Field(default=None, max_length=200)
    subdirectory: str | None = Field(default=None, max_length=300)
    #: Written to the secret store. Omit to keep the stored one, "" to remove it.
    token: str | None = Field(default=None, max_length=500)
    #: Check for new commits on a timer instead of only when asked.
    auto_pull: bool | None = None
    interval_minutes: int | None = Field(default=None, ge=5, le=10080)
    auto_publish: bool | None = None


class GitSourceView(BaseModel):
    connected: bool = False
    repo_url: str | None = None
    ref: str | None = None
    subdirectory: str = ""
    auto_pull: bool = False
    interval_minutes: int = 30
    auto_publish: bool = False
    has_token: bool = False
    last_commit: str | None = None
    last_pulled_at: str | None = None
    last_status: str | None = None
    last_message: str | None = None
    #: Models the repository produced. Only these are replaced or removed.
    managed: list[str] = Field(default_factory=list)
    next_pull_at: datetime | None = None


class GitPullResult(BaseModel):
    #: APPLIED, UNCHANGED or FAILED.
    status: str
    message: str
    last_commit: str | None = None
    changed: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class RepositoryImportPreview(BaseModel):
    """What the conversion would produce, before anything is created."""

    kind: Literal["DBT", "DATAFORM"]
    project_name: str | None = None
    models: list[ImportedModelView] = Field(default_factory=list)
    sources: list[ImportedSourceView] = Field(default_factory=list)
    tests: list[ImportedTestView] = Field(default_factory=list)
    #: Everything the conversion could not carry across, in the user's words.
    warnings: list[str] = Field(default_factory=list)
    origin: dict[str, Any] = Field(default_factory=dict)


class TransformSystemView(BaseModel):
    """A kind of warehouse a Transform can run on, and how it authenticates."""

    connector_key: str
    label: str
    #: service_account | oauth | password -- what the create form should offer.
    auth_methods: list[str] = Field(default_factory=list)
    adapter: str | None = None
    adapter_version: str | None = None


class WarehouseConnectionCreate(BaseModel):
    """A connection to keep, so it is chosen next time instead of re-entered.

    It carries the whole warehouse: which system, where it is, and how to
    authenticate. BigQuery takes a service account JSON (or arrives through
    OAuth); Postgres takes host, port, database, user and password.
    """

    connector_key: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=200)
    auth_method: Literal["service_account", "oauth", "password"] = "service_account"
    #: BigQuery
    project_id: str | None = Field(default=None, max_length=200)
    dataset_location: str | None = Field(default=None, max_length=64)
    credentials_json: str | None = Field(default=None, max_length=20000)
    #: Postgres and other databases
    host: str | None = Field(default=None, max_length=300)
    port: int | None = Field(default=None, ge=1, le=65535)
    database: str | None = Field(default=None, max_length=200)
    username: str | None = Field(default=None, max_length=200)
    password: str | None = Field(default=None, max_length=500)
    ssl_mode: str | None = Field(default=None, max_length=32)
    #: Set by the OAuth callback, never by a person.
    oauth_grant_id: uuid.UUID | None = None


class WarehouseConnectionView(BaseModel):
    """One row of the connection list."""

    id: uuid.UUID
    name: str
    connector_key: str = ""
    auth_method: str = "inherited"
    #: Set when this connection came from a Destination, which is what keeps
    #: Pipeline lineage resolvable through it.
    destination_id: uuid.UUID | None = None
    destination_name: str | None = None
    #: Who the connection turned out to be, so a wrong one is obvious.
    account: str | None = None
    #: Projects or databases it could read when last checked.
    catalogs: list[str] = Field(default_factory=list)
    #: True for the connection a Destination already uses -- nothing to enter.
    is_default: bool = False
    last_verified_at: datetime | None = None


class BrowsedRelationView(BaseModel):
    catalog_name: str | None = None
    schema_name: str
    relation_name: str
    relation_type: str
    #: Set when this relation is already a registered asset for the Destination.
    asset_id: uuid.UUID | None = None
    #: Set when a Pipeline writes this relation, so a source AppBI keeps fresh
    #: is distinguishable from one that merely exists.
    pipeline_id: uuid.UUID | None = None
    pipeline_name: str | None = None


class WarehouseBrowseView(BaseModel):
    """What the Destination's warehouse physically holds.

    Returned schema-at-a-time: listing every table in every dataset of a real
    warehouse is a slow call nobody asked for.
    """

    catalog_name: str | None = None
    #: Projects or databases visible to the connection, when none was asked for.
    catalogs: list[str] = Field(default_factory=list)
    schemas: list[str] = Field(default_factory=list)
    relations: list[BrowsedRelationView] = Field(default_factory=list)


class TransformTestCreate(BaseModel):
    column_name: str | None = Field(default=None, max_length=200)
    rule: Literal["NOT_NULL", "UNIQUE", "ACCEPTED_VALUES", "RELATIONSHIPS"]
    severity: Literal["ERROR", "WARN"] = "ERROR"
    config: dict[str, Any] = Field(default_factory=dict)


class TransformTestView(BaseModel):
    id: uuid.UUID
    column_name: str | None = None
    rule: str
    severity: str
    config: dict[str, Any] = Field(default_factory=dict)
    last_status: str
    last_run_at: datetime | None = None


class TransformModelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    layer: Literal["STAGING", "CORE", "MART"] = "STAGING"
    materialization: Literal["VIEW", "TABLE", "INCREMENTAL"] = "VIEW"
    sql: str | None = None


class TransformModelUpdate(BaseModel):
    sql: str | None = None
    layer: Literal["STAGING", "CORE", "MART"] | None = None
    materialization: Literal["VIEW", "TABLE", "INCREMENTAL"] | None = None
    output_schema: str | None = Field(default=None, max_length=200)
    relation_name: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    tags: list[str] | None = None
    config: dict[str, Any] | None = None
    version: int | None = None


class TransformModelView(BaseModel):
    id: uuid.UUID
    name: str
    layer: str
    materialization: str
    sql: str
    output_schema: str | None = None
    relation_name: str | None = None
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)
    tests: list[TransformTestView] = Field(default_factory=list)
    version: int
    updated_at: datetime


class TransformRunRef(BaseModel):
    id: uuid.UUID
    operation: str
    status: str
    started_at: datetime | None = None
    ended_at: datetime | None = None
    created_at: datetime
    models_built: int = 0
    tests_passed: int = 0
    tests_failed: int = 0


class TransformView(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None = None
    destination: ActorRef
    default_schema: str
    status: str
    health_status: str
    health_message: str | None = None
    model_count: int = 0
    test_count: int = 0
    last_run: TransformRunRef | None = None
    last_success_at: datetime | None = None
    dbt_core_version: str
    dbt_adapter_name: str
    dbt_adapter_version: str
    version: int
    created_at: datetime
    updated_at: datetime
    available_actions: list[str] = Field(default_factory=list)


class TransformDraftRequest(BaseModel):
    asset_id: uuid.UUID
    intent: str = Field(min_length=3, max_length=2000)


class TransformReleaseView(BaseModel):
    id: uuid.UUID
    release_number: int
    notes: str | None
    default_schema: str
    model_count: int
    created_at: datetime
    created_by: UserRef | None = None
    is_active: bool = False


class TransformReleaseCreate(BaseModel):
    notes: str | None = Field(default=None, max_length=500)
    activate: bool = True


class TransformDetail(TransformView):
    inputs: list[DataAssetView] = Field(default_factory=list)
    models: list[TransformModelView] = Field(default_factory=list)
    execution_trigger: str = "MANUAL"
    trigger_config: dict[str, Any] = Field(default_factory=dict)
    upstream_ready: bool = True
    schedule: ScheduleConfig | None = None
    next_run_at: datetime | None = None
    # The published snapshot a schedule executes, and whether the draft has
    # moved on since -- the two facts a user needs to answer "will tonight's
    # run include what I just typed?"
    active_release: TransformReleaseView | None = None
    draft_has_changes: bool = False
    #: The repository behind these models, when there is one.
    git: GitSourceView = Field(default_factory=GitSourceView)


class RepositoryImportResult(BaseModel):
    transform: TransformDetail
    warnings: list[str] = Field(default_factory=list)


class TransformRunRequest(BaseModel):
    operation: Literal[
        "VALIDATE", "COMPILE", "PREVIEW", "TEST", "RUN_MODEL", "RUN_UPSTREAM", "BUILD",
    ]
    model_id: uuid.UUID | None = None
    # dbt's `--full-refresh`: rebuild incremental models from scratch. Without
    # it an incremental whose SQL or columns changed cannot be corrected.
    full_refresh: bool = False
    # DRAFT compiles what the editor holds now; RELEASE executes the published
    # snapshot, which is what a schedule and an unattended trigger use.
    source: Literal["DRAFT", "RELEASE"] = "DRAFT"


class TransformRunNodeView(BaseModel):
    name: str
    resource_type: str
    status: str
    execution_time: float | None = None
    relation_name: str | None = None
    message: str | None = None


class TransformExecutionView(BaseModel):
    id: uuid.UUID
    transform_id: uuid.UUID
    operation: str
    selected_model_id: uuid.UUID | None = None
    status: str
    trigger_type: str
    created_at: datetime
    started_at: datetime | None = None
    ended_at: datetime | None = None
    models_built: int = 0
    tests_passed: int = 0
    tests_failed: int = 0
    tests_warned: int = 0
    rows_affected: int | None = None
    error: dict[str, Any] | None = None
    preview: dict[str, Any] | None = None
    compiled_sql: dict[str, str] = Field(default_factory=dict)
    nodes: list[TransformRunNodeView] = Field(default_factory=list)


class TransformLineage(BaseModel):
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)


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
    run_type: Literal["PIPELINE", "TRANSFORM"] = "PIPELINE"
    pipeline: ActorRef | None = None
    transform: ActorRef | None = None
    operation: str | None = None
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
    models_built: int | None = None
    tests_passed: int | None = None
    tests_failed: int | None = None
    tests_warned: int | None = None
    rows_affected: int | None = None
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
    transform_nodes: list[TransformRunNodeView] = Field(default_factory=list)


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
