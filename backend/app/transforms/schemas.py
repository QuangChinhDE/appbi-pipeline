"""Request and response shapes for the Transform V2 API.

Kept in this package rather than added to ``app/schemas/domain.py``, which every
other module shares.  Transform's own contract changing should not touch a file
Pipelines and Sources depend on.

The response types are deliberately close to what the workbench renders.  A
screen that has to assemble four calls to draw one panel is a screen that
flickers, and the counter-pressure -- one giant project payload -- is worse for a
project with 5,000 resources.  The split follows the React Query boundaries the
blueprint sets out: metadata, tree, file, resources, lineage, git, invocation.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

# ── connections ───────────────────────────────────────────────────────────


class ConnectionView(BaseModel):
    id: uuid.UUID
    name: str
    connector_key: str
    auth_method: str
    destination_id: uuid.UUID | None = None
    destination_name: str | None = None
    account: str | None = None
    catalogs: list[str] = Field(default_factory=list)
    is_default: bool = False
    verification_status: str = "UNVERIFIED"
    verification_message: str | None = None
    last_verified_at: datetime | None = None


class ConnectionCreate(BaseModel):
    connector_key: str
    name: str
    auth_method: Literal["service_account", "oauth", "password"]
    # BigQuery
    project_id: str | None = None
    dataset_location: str | None = None
    credentials_json: str | None = None
    oauth_grant_id: uuid.UUID | None = None
    # Postgres and friends
    host: str | None = None
    port: int | None = None
    database: str | None = None
    username: str | None = None
    password: str | None = None
    ssl_mode: str | None = None


class ConnectionUpdate(BaseModel):
    name: str | None = None
    project_id: str | None = None
    dataset_location: str | None = None
    credentials_json: str | None = None
    host: str | None = None
    port: int | None = None
    database: str | None = None
    username: str | None = None
    password: str | None = None
    ssl_mode: str | None = None


class SystemView(BaseModel):
    connector_key: str
    label: str
    auth_methods: list[str]
    adapter: str | None = None
    adapter_version: str | None = None
    dbt_core: str | None = None


# ── environments ──────────────────────────────────────────────────────────


class EnvironmentView(BaseModel):
    id: uuid.UUID
    name: str
    type: str
    connection: dict[str, Any] | None = None
    target_name: str
    schema_strategy: str
    schema_name: str
    effective_schema: str
    threads: int
    vars: dict[str, Any] = Field(default_factory=dict)
    protected: bool = False


class EnvironmentUpdate(BaseModel):
    name: str | None = None
    connection_id: uuid.UUID | None = None
    target_name: str | None = None
    schema_name: str | None = None
    schema_strategy: Literal["STATIC", "PER_USER"] | None = None
    schema_prefix: str | None = None
    schema_suffix: str | None = None
    threads: int | None = Field(default=None, ge=1, le=32)
    vars: dict[str, Any] | None = None


# ── projects ──────────────────────────────────────────────────────────────


class GitStateView(BaseModel):
    branch: str
    repo_url: str
    head_commit_sha: str | None = None
    behind: bool = False
    last_status: str | None = None


class ReleaseRef(BaseModel):
    id: uuid.UUID
    release_number: int
    activated_at: datetime | None = None


class InvocationRef(BaseModel):
    id: uuid.UUID
    command: str
    selector: str | None = None
    status: str
    ended_at: datetime | None = None


class ProjectView(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None = None
    mode: str
    status: str
    dbt_project_name: str | None = None
    warehouse: dict[str, Any] | None = None
    environment_name: str | None = None
    health_status: str
    health_message: str | None = None
    parse_status: str
    parse_error: str | None = None
    last_parsed_at: datetime | None = None
    revision_number: int | None = None
    file_count: int = 0
    has_unpublished_changes: bool = False
    active_release: ReleaseRef | None = None
    last_invocation: InvocationRef | None = None
    last_success_at: datetime | None = None
    git: GitStateView | None = None
    schedule_type: str = "MANUAL"
    next_run_at: datetime | None = None
    updated_at: datetime | None = None


class RevisionRef(BaseModel):
    id: uuid.UUID
    revision_number: int
    content_hash: str
    file_count: int
    created_at: datetime


class EngineView(BaseModel):
    dbt_core_version: str
    adapter: str
    adapter_version: str


class ProjectPermissions(BaseModel):
    can_edit: bool = False
    can_operate: bool = False
    can_delete: bool = False


class ProjectDetail(ProjectView):
    environments: list[EnvironmentView] = Field(default_factory=list)
    default_environment_id: uuid.UUID | None = None
    production_environment_id: uuid.UUID | None = None
    working_revision: RevisionRef | None = None
    dbt_profile_name: str | None = None
    project_file_valid: bool = False
    project_file_error: str | None = None
    resource_counts: dict[str, int] = Field(default_factory=dict)
    resource_bundle_id: uuid.UUID | None = None
    engine: EngineView
    permissions: ProjectPermissions


class ProjectCreate(BaseModel):
    name: str
    description: str | None = None
    connection_id: uuid.UUID
    #: NEW scaffolds a starter project, GIT clones a repository, UPLOAD takes
    #: an uploaded archive.  All three end with the same thing: a revision full
    #: of real dbt project files.
    source: Literal["NEW", "GIT", "UPLOAD"] = "NEW"
    dbt_project_name: str | None = None
    development_schema: str | None = None
    production_schema: str | None = None
    source_schema: str = "raw"
    per_user_schemas: bool = False
    with_examples: bool = True
    # Git
    repo_url: str | None = None
    branch: str | None = None
    subdirectory: str | None = None
    token: str | None = None
    auto_pull: bool = False
    interval_minutes: int = 15


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    schedule_type: str | None = None
    schedule_config: dict[str, Any] | None = None
    schedule_command: dict[str, Any] | None = None
    timezone: str | None = None


class RepositoryInspectRequest(BaseModel):
    repo_url: str
    branch: str | None = None
    subdirectory: str | None = None
    token: str | None = None


class RepositoryInspectResult(BaseModel):
    """What was found in a repository, before anything is created.

    Deliberately not a conversion plan.  V1's equivalent listed everything it
    would have to drop; this one confirms a dbt project is there and reports its
    shape, because nothing is dropped.
    """

    detected_root: str
    dbt_project_name: str | None = None
    dbt_version_requirement: Any = None
    profile_name: str | None = None
    file_count: int
    model_count: int
    resource_directories: list[str] = Field(default_factory=list)
    packages: list[str] = Field(default_factory=list)
    branch: str | None = None
    commit_sha: str | None = None
    warnings: list[str] = Field(default_factory=list)


# ── files ─────────────────────────────────────────────────────────────────


class FileNode(BaseModel):
    name: str
    path: str
    type: Literal["file", "directory"]
    size: int | None = None
    is_text: bool = True
    children: list["FileNode"] | None = None


class FileTreeView(BaseModel):
    revision_id: uuid.UUID
    revision_number: int
    content_hash: str
    tree: list[FileNode] = Field(default_factory=list)
    file_count: int = 0


class FileContentView(BaseModel):
    path: str
    content: str
    size: int
    sha256: str
    is_text: bool = True
    revision_id: uuid.UUID
    #: The resource this file defines, when the last parse knew of one.  Lets
    #: the editor offer Preview/Build on the right selector without the person
    #: having to know what dbt named it.
    unique_id: str | None = None
    resource_type: str | None = None


class FileSaveRequest(BaseModel):
    path: str
    content: str
    #: The revision the editor was looking at.  A stale value is a 409 carrying
    #: both versions, never a silent overwrite.
    expected_revision_id: uuid.UUID | None = None


class FileBatchItem(BaseModel):
    path: str
    content: str | None = None
    from_path: str | None = None


class FileBatchRequest(BaseModel):
    changes: list[FileBatchItem]
    expected_revision_id: uuid.UUID | None = None


class FileCreateRequest(BaseModel):
    path: str
    content: str = ""
    template: str | None = None
    expected_revision_id: uuid.UUID | None = None


class FileMoveRequest(BaseModel):
    from_path: str
    to_path: str
    expected_revision_id: uuid.UUID | None = None


class FileDeleteRequest(BaseModel):
    paths: list[str]
    expected_revision_id: uuid.UUID | None = None


class SaveResult(BaseModel):
    revision_id: uuid.UUID
    revision_number: int
    content_hash: str
    file_count: int
    saved_paths: list[str] = Field(default_factory=list)
    #: The parse this save queued, so the UI can follow it.
    parse_invocation_id: uuid.UUID | None = None


class TemplateView(BaseModel):
    key: str
    label: str
    path: str
    content: str


# ── resources ─────────────────────────────────────────────────────────────


class ResourceSummary(BaseModel):
    model_config = {"populate_by_name": True}

    unique_id: str
    resource_type: str
    name: str
    package_name: str | None = None
    path: str | None = None
    patch_path: str | None = None
    materialized: str | None = None
    relation_name: str | None = None
    database: str | None = None
    # `schema_` because pydantic's BaseModel already owns `schema`; the wire
    # name stays `schema`, which is what dbt calls it and what the UI reads.
    schema_: str | None = Field(default=None, alias="schema")
    alias: str | None = None
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    group: str | None = None
    enabled: bool = True
    produced_by_pipeline_id: uuid.UUID | None = None


class ResourceColumn(BaseModel):
    name: str
    description: str | None = None
    data_type: str | None = None
    tags: list[str] = Field(default_factory=list)
    constraints: list[Any] = Field(default_factory=list)
    in_warehouse: bool = False
    documented: bool = False


class ResourceDetail(ResourceSummary):
    #: The node's whole parsed config, including keys AppBI has no form for.
    #: Displayed as-is rather than filtered, which is how an unknown config
    #: stays visible instead of appearing not to exist.
    config: dict[str, Any] = Field(default_factory=dict)
    checksum: str | None = None
    columns: list[ResourceColumn] = Field(default_factory=list)
    parents: list[str] = Field(default_factory=list)
    children: list[str] = Field(default_factory=list)
    tests: list[ResourceSummary] = Field(default_factory=list)
    last_result: dict[str, Any] | None = None
    freshness: dict[str, Any] | None = None
    warehouse: dict[str, Any] | None = None


class ResourcePageView(BaseModel):
    items: list[ResourceSummary]
    total: int
    counts: dict[str, int] = Field(default_factory=dict)


class FacetsView(BaseModel):
    resource_types: list[str] = Field(default_factory=list)
    packages: list[str] = Field(default_factory=list)
    materializations: list[str] = Field(default_factory=list)
    groups: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class LineageNode(BaseModel):
    unique_id: str
    name: str
    resource_type: str
    materialized: str | None = None
    package: str | None = None
    path: str | None = None
    tags: list[str] = Field(default_factory=list)
    relation_name: str | None = None
    enabled: bool = True
    is_focus: bool = False
    produced_by_pipeline_id: uuid.UUID | None = None


class LineageEdge(BaseModel):
    parent: str
    child: str


class LineageView(BaseModel):
    nodes: list[LineageNode] = Field(default_factory=list)
    edges: list[LineageEdge] = Field(default_factory=list)
    truncated: bool = False
    total_nodes: int = 0
    #: DRAFT or RELEASE.  Two graphs, never merged: what somebody is editing and
    #: what production is running are different questions.
    scope: str = "DRAFT"


# ── invocations ───────────────────────────────────────────────────────────


class InvocationRequest(BaseModel):
    command: str
    selector: str | None = None
    exclude: str | None = None
    environment_id: uuid.UUID | None = None
    full_refresh: bool = False
    limit: int | None = Field(default=None, ge=1, le=5000)
    macro: str | None = None
    macro_args: dict[str, Any] | None = None
    selector_name: str | None = None
    vars: dict[str, Any] | None = None
    #: DRAFT runs the working revision; RELEASE runs what is live.
    source: Literal["DRAFT", "RELEASE"] = "DRAFT"


class InvocationNodeView(BaseModel):
    unique_id: str
    name: str
    resource_type: str
    status: str
    execution_time: float | None = None
    relation_name: str | None = None
    message: str | None = None
    rows_affected: int | None = None
    bytes_processed: int | None = None
    failures: int | None = None
    error_location: dict[str, Any] = Field(default_factory=dict)


class InvocationView(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    project_name: str | None = None
    command: str
    selector: str | None = None
    exclude: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    status: str
    environment_id: uuid.UUID
    environment_name: str | None = None
    revision_id: uuid.UUID
    revision_number: int | None = None
    release_id: uuid.UUID | None = None
    release_number: int | None = None
    trigger_type: str
    triggered_by: uuid.UUID | None = None
    queue_reason: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    created_at: datetime | None = None
    duration_seconds: float | None = None
    nodes_total: int = 0
    nodes_succeeded: int = 0
    nodes_failed: int = 0
    nodes_skipped: int = 0
    tests_passed: int = 0
    tests_failed: int = 0
    tests_warned: int = 0
    rows_affected: int | None = None
    error_code: str | None = None
    error_summary: str | None = None
    error_location: dict[str, Any] = Field(default_factory=dict)
    technical_message: str | None = None
    remediation_action: str | None = None
    exit_code: int | None = None
    dbt_invocation_id: str | None = None
    is_stale: bool = False
    actions: dict[str, bool] = Field(default_factory=dict)


class InvocationDetail(InvocationView):
    nodes: list[InvocationNodeView] = Field(default_factory=list)
    #: `dbt show` output, when this was a preview.
    preview: dict[str, Any] | None = None


class LogPage(BaseModel):
    invocation_id: uuid.UUID
    lines: list[str] = Field(default_factory=list)
    next_cursor: int = 0
    has_more: bool = False
    total_lines: int = 0


class CompiledView(BaseModel):
    unique_id: str
    compiled_code: str | None = None
    raw_code: str | None = None


class ProblemView(BaseModel):
    """One thing wrong, wherever it came from.

    Parse errors, compile errors, failed nodes and failed tests all land here in
    one shape, because the panel that shows them is one panel and a person
    triaging does not care which subsystem noticed.
    """

    severity: Literal["error", "warning"]
    #: parse | compile | run | test | yaml
    source: str
    message: str
    path: str | None = None
    line: int | None = None
    unique_id: str | None = None
    resource_name: str | None = None


class ProblemsView(BaseModel):
    problems: list[ProblemView] = Field(default_factory=list)
    parse_status: str = "UNKNOWN"
    checked_at: datetime | None = None


# ── releases ──────────────────────────────────────────────────────────────


class ReleaseView(BaseModel):
    id: uuid.UUID
    release_number: int
    status: str
    is_active: bool = False
    revision_number: int | None = None
    project_hash: str
    file_count: int = 0
    git_commit_sha: str | None = None
    environment_name: str | None = None
    dbt_version: str | None = None
    verification_invocation_id: uuid.UUID | None = None
    verification_error: str | None = None
    verified_at: datetime | None = None
    activated_at: datetime | None = None
    notes: str | None = None
    created_at: datetime | None = None
    created_by: uuid.UUID | None = None


class ReleaseCreate(BaseModel):
    notes: str | None = None
    activate: bool = True


class FileChangeView(BaseModel):
    path: str
    change: Literal["A", "M", "D"]
    size_before: int | None = None
    size_after: int | None = None


class PublishPlanView(BaseModel):
    files: list[FileChangeView] = Field(default_factory=list)
    affected_resources: list[dict[str, Any]] = Field(default_factory=list)
    downstream_resources: list[dict[str, Any]] = Field(default_factory=list)
    draft_hash: str
    live_hash: str | None = None
    matches_live: bool = False


class PublishResult(BaseModel):
    release: ReleaseView
    verification_invocation_id: uuid.UUID


# ── git ───────────────────────────────────────────────────────────────────


class GitStatusView(BaseModel):
    branch: str
    repo_url: str
    subdirectory: str = ""
    head_commit_sha: str | None = None
    remote_commit_sha: str | None = None
    behind: bool = False
    changes: list[FileChangeView] = Field(default_factory=list)
    last_pulled_at: datetime | None = None
    last_status: str | None = None
    last_message: str | None = None
    auto_pull: bool = False
    interval_minutes: int = 15


class GitCommitRequest(BaseModel):
    message: str
    #: Which files to stage.  Omitted means everything changed.
    paths: list[str] | None = None


class GitPullRequest(BaseModel):
    force: bool = False
    discard_local: bool = False


class GitCheckoutRequest(BaseModel):
    branch: str
    discard_local: bool = False


class GitConfigureRequest(BaseModel):
    branch: str | None = None
    subdirectory: str | None = None
    token: str | None = None
    auto_pull: bool | None = None
    interval_minutes: int | None = Field(default=None, ge=1, le=1440)


class GitDiffView(BaseModel):
    path: str
    committed: str | None = None
    working: str | None = None


class BranchView(BaseModel):
    name: str
    commit_sha: str | None = None
    current: bool = False
    protected: bool = False


# ── autocomplete and docs ─────────────────────────────────────────────────


class CompletionItem(BaseModel):
    label: str
    #: ref | source | macro | column | config | test
    kind: str
    detail: str | None = None
    insert_text: str | None = None


class CompletionsView(BaseModel):
    refs: list[CompletionItem] = Field(default_factory=list)
    sources: list[CompletionItem] = Field(default_factory=list)
    macros: list[CompletionItem] = Field(default_factory=list)
    tests: list[CompletionItem] = Field(default_factory=list)
    columns: dict[str, list[str]] = Field(default_factory=dict)


class DocEntry(BaseModel):
    unique_id: str
    name: str
    resource_type: str
    description: str | None = None
    path: str | None = None
    relation_name: str | None = None
    columns: list[ResourceColumn] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    group: str | None = None
    tests: list[str] = Field(default_factory=list)
    parents: list[str] = Field(default_factory=list)
    children: list[str] = Field(default_factory=list)


class SearchHit(BaseModel):
    kind: Literal["file", "resource", "column"]
    label: str
    detail: str | None = None
    path: str | None = None
    unique_id: str | None = None
    line: int | None = None
    excerpt: str | None = None


class SearchView(BaseModel):
    hits: list[SearchHit] = Field(default_factory=list)
    truncated: bool = False


FileNode.model_rebuild()
