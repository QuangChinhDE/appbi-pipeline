/** Mirrors the Product API contract. No engine identifier appears anywhere here. */

export type HealthLevel = 'HEALTHY' | 'WARNING' | 'ERROR' | 'UNKNOWN' | 'RUNNING';

export interface HealthBlock {
  level: HealthLevel;
  code: string | null;
  label: string;
  last_checked_at: string | null;
  message: string | null;
}

export interface UserRef {
  id: string | null;
  full_name: string | null;
  email: string | null;
}

export interface ActorRef {
  id: string;
  name: string;
  connector_key: string;
  connector_display_name: string | null;
  icon: string | null;
}

export interface PageInfo {
  next_cursor: string | null;
  has_more: boolean;
  total: number | null;
  limit: number;
  offset: number;
}

export interface Paginated<T> {
  items: T[];
  page: PageInfo;
  summary: Record<string, number>;
}

export interface WorkspaceSummary {
  id: string;
  name: string;
  slug: string;
  role: string | null;
  timezone: string;
  status: string;
}

export type PermissionMap = Record<string, string[]>;

export interface CurrentUser {
  id: string;
  email: string;
  full_name: string;
  locale: string;
  is_platform_admin: boolean;
  workspace: WorkspaceSummary | null;
  workspaces: WorkspaceSummary[];
  role: string | null;
  permissions: PermissionMap;
  /**
   * Set for an account created from the bootstrap one-time secret, or invited
   * by somebody else. Until it is cleared, every product route answers
   * `403 PASSWORD_CHANGE_REQUIRED` -- so the app has to route on this rather
   * than discover it one failed request at a time.
   */
  password_change_required: boolean;
}

export interface Connector {
  connector_key: string;
  display_name: string;
  connector_type: 'SOURCE' | 'DESTINATION';
  category: string;
  description: string | null;
  icon: string | null;
  icon_url: string | null;
  documentation_url: string | null;
  version: string;
  latest_version: string | null;
  release_stage: string;
  support_level: string;
  certification: 'SUPPORTED' | 'BETA' | 'HIDDEN' | 'BLOCKED';
  status: string;
  disabled_reason: string | null;
  supports_oauth: boolean;
  supports_incremental: boolean;
  supports_cdc: boolean;
  supports_namespaces: boolean;
  supported_destination_sync_modes: string[];
  image_pulled: boolean;
  last_refreshed_at: string | null;
  usage_count: number;
  update_available: boolean;
  selectable: boolean;
}

export interface ConnectorDetail extends Connector {
  spec_schema: JsonSchema;
  spec_source: string;
  /** Known only for connectors this product defines; null for Airbyte images,
   *  where the stream list comes from a discover against real credentials. */
  stream_count: number | null;
  /** The tables this connector reads, when known without a discover. */
  stream_names: string[];
}

export interface JsonSchema {
  type?: string | string[];
  title?: string;
  description?: string;
  properties?: Record<string, JsonSchema>;
  required?: string[];
  items?: JsonSchema;
  enum?: unknown[];
  const?: unknown;
  default?: unknown;
  format?: string;
  /** Explicit multi-line hint. The renderer never infers this from how
   *  long the description is — good help text should not change the widget. */
  multiline?: boolean;
  minimum?: number;
  maximum?: number;
  pattern?: string;
  oneOf?: JsonSchema[];
  airbyte_secret?: boolean;
  airbyte_advanced?: boolean;
  order?: number;
  deprecated?: boolean;
  [key: string]: unknown;
}

export interface CredentialsView {
  configured: boolean;
  provider?: string | null;
  rotated_at?: string | null;
  version?: number | null;
  fields: Record<string, string>;
}

export interface Actor {
  id: string;
  name: string;
  description: string | null;
  connector_key: string;
  connector_display_name: string | null;
  connector_icon: string | null;
  connector_version: string | null;
  status: string;
  health: HealthBlock;
  last_test_at: string | null;
  last_test_result: 'PASSED' | 'FAILED' | 'NOT_TESTED';
  pipeline_count: number;
  owner: UserRef | null;
  created_at: string;
  updated_at: string;
  version: number;
  available_actions: string[];
}

export interface ActorDetail extends Actor {
  configuration: Record<string, unknown>;
  credentials: CredentialsView;
  spec_schema: JsonSchema;
  active_schema_snapshot_id: string | null;
  last_discovered_at: string | null;
}

export interface ActorTestResult {
  succeeded: boolean;
  /** Server-signed proof of this exact config passing; lets save skip a re-check. */
  check_token: string | null;
  message: string | null;
  error_code: string | null;
  category: string | null;
  technical_message: string | null;
  duration_ms: number | null;
  tested_at: string;
}

export interface StreamCapability {
  name: string;
  namespace: string | null;
  supported_sync_modes: string[];
  source_defined_cursor: boolean;
  default_cursor_field: string[];
  source_defined_primary_key: string[][];
  fields: { name: string; type: string; nullable: boolean }[];
  unsupported_reason: string | null;
}

export interface SchemaSnapshot {
  id: string;
  source_id: string;
  discovered_at: string;
  catalog_hash: string;
  stream_count: number;
  connector_version: string | null;
  streams: StreamCapability[];
}

export interface SchemaChange {
  kind: string;
  severity: 'INFO' | 'WARNING' | 'BREAKING';
  namespace: string | null;
  stream_name: string;
  field_name: string | null;
  before: string | null;
  after: string | null;
  message: string;
}

export interface SchemaDiff {
  pipeline_id: string;
  from_snapshot_id: string | null;
  to_snapshot_id: string | null;
  generated_at: string;
  has_breaking: boolean;
  added: SchemaChange[];
  removed: SchemaChange[];
  changed: SchemaChange[];
}

export type ScheduleType = 'MANUAL' | 'INTERVAL' | 'DAILY' | 'CRON';

export interface ScheduleConfig {
  type: ScheduleType;
  interval_seconds?: number | null;
  time_of_day?: string | null;
  cron_expression?: string | null;
  timezone: string;
}

export interface StreamSelection {
  name: string;
  namespace: string | null;
  selected: boolean;
  sync_mode: 'full_refresh' | 'incremental';
  destination_sync_mode: 'overwrite' | 'append' | 'append_dedup';
  cursor_fields: string[];
  primary_key_fields: string[][];
  selected_fields?: string[] | null;
}

export interface RunRef {
  id: string;
  status: string;
  trigger_type: string | null;
  started_at: string | null;
  ended_at: string | null;
  duration_seconds: number | null;
  records_synced: number | null;
  error_category: string | null;
}

export interface Pipeline {
  id: string;
  name: string;
  description: string | null;
  status: string;
  health: HealthBlock;
  source: ActorRef;
  destination: ActorRef;
  schedule: ScheduleConfig;
  next_run_at: string | null;
  last_run: RunRef | null;
  stream_count: number;
  owner: UserRef | null;
  created_at: string;
  updated_at: string;
  version: number;
  available_actions: string[];
}

export interface PipelineStreamView {
  id: string;
  name: string;
  namespace: string | null;
  selected: boolean;
  sync_mode: string;
  destination_sync_mode: string;
  cursor_fields: string[];
  primary_key_fields: string[][];
  selected_fields: string[] | null;
  field_count: number;
  /**
   * The full field tree, nested objects included. `path` is the dotted route
   * and `depth` the indent level -- computed server-side, because a field
   * legitimately named `a.b` would otherwise be drawn as if it were nested.
   */
  fields: { name: string; type: string; nullable: boolean; path: string; depth: number }[];
  last_sync: StreamSyncState | null;
}

export interface StreamSyncState {
  status: string;
  records_loaded: number;
  bytes_loaded: number;
  synced_at: string | null;
}

export interface ConnectionStateView {
  supported: boolean;
  state: Record<string, unknown>[];
  fetched_at: string | null;
  unavailable_reason: string | null;
}

export interface PipelineMetrics {
  success_rate_7d: number | null;
  success_rate_30d: number | null;
  average_duration_seconds: number | null;
  total_runs_30d: number;
  records_synced_30d: number;
  last_success_at: string | null;
  consecutive_failures: number;
}

export interface PipelineDetail extends Pipeline {
  streams: PipelineStreamView[];
  metrics: PipelineMetrics;
  recent_runs: RunRef[];
  active_schema_snapshot_id: string | null;
  schema_snapshot_at: string | null;
  schema_change_pending: boolean;
  needs_review_reason: string | null;
  namespace_format: string | null;
  stream_prefix: string | null;
  overlap_policy: string;
}

export interface RunError {
  code: string | null;
  category: string | null;
  summary: string | null;
  remediation_action: string | null;
  technical_message: string | null;
  /** Where the engine says the failure is, when it could tell. */
  location?: {
    name?: string | null;
    resource_type?: string | null;
    path?: string | null;
    line?: number | null;
  } | null;
}

export interface Run {
  id: string;
  short_id: string;
  run_type: 'PIPELINE' | 'TRANSFORM';
  pipeline: ActorRef | null;
  transform: ActorRef | null;
  operation: string | null;
  status: string;
  trigger_type: string;
  triggered_by: UserRef | null;
  retry_of_run_id: string | null;
  queue_reason: string | null;
  started_at: string | null;
  ended_at: string | null;
  created_at: string;
  duration_seconds: number | null;
  records_synced: number | null;
  bytes_synced: number | null;
  models_built: number | null;
  tests_passed: number | null;
  tests_failed: number | null;
  tests_warned: number | null;
  rows_affected: number | null;
  error: RunError | null;
  is_stale: boolean;
  actions: { can_cancel?: boolean; can_retry?: boolean; can_view_logs?: boolean };
}

export interface RunDetail extends Run {
  attempts: {
    attempt_number: number;
    status: string;
    started_at: string | null;
    ended_at: string | null;
    duration_seconds: number | null;
    records_synced: number | null;
    bytes_synced: number | null;
    failure_summary: string | null;
  }[];
  stream_stats: {
    stream_name: string;
    namespace: string | null;
    records_emitted: number;
    bytes_emitted: number;
    status: string;
  }[];
  source: ActorRef | null;
  destination: ActorRef | null;
  trace_id: string | null;
  technical_metadata: Record<string, unknown>;
  transform_nodes: {
    name: string;
    resource_type: string;
    status: string;
    execution_time: number | null;
    relation_name: string | null;
    message: string | null;
  }[];
}

export interface RunLogPage {
  run_id: string;
  lines: string[];
  next_cursor: number | null;
  has_more: boolean;
  total_lines: number | null;
}

/* ── Transform V2 ─────────────────────────────────────────────────────────
 *
 * A Transform is a dbt project. These types describe what the backend reads
 * out of dbt's own artifacts, so most of them carry dbt's vocabulary rather
 * than a product translation of it: a dbt engineer should be able to look up
 * `materialized`, `unique_id` or `resource_type` in dbt's docs and find the
 * same meaning here.
 *
 * `resource_type` is a string, never a union. dbt adds resource types, and a
 * closed union here would mean a code change before a new one could even be
 * listed -- which is the pattern the rework exists to remove.
 */

/** A kind of warehouse a project can run on, and how it authenticates. */
export interface TransformSystem {
  connector_key: string;
  label: string;
  auth_methods: string[];
  adapter: string | null;
  adapter_version: string | null;
  dbt_core: string | null;
}

/** A named warehouse key. The credential itself never reaches the browser. */
export interface WarehouseConnection {
  id: string;
  name: string;
  connector_key: string;
  auth_method: string;
  destination_id: string | null;
  destination_name: string | null;
  account: string | null;
  catalogs: string[];
  is_default: boolean;
  verification_status: 'UNVERIFIED' | 'OK' | 'FAILED';
  verification_message: string | null;
  last_verified_at: string | null;
}

/** Where a project runs, and as whom. Development and production are real. */
export interface TransformEnvironment {
  id: string;
  name: string;
  type: 'DEVELOPMENT' | 'PRODUCTION';
  connection: {
    connection_id: string;
    name: string;
    connector_key: string;
    connector_display_name: string | null;
    icon: string | null;
    destination_id: string | null;
  } | null;
  target_name: string;
  schema_strategy: 'STATIC' | 'PER_USER';
  schema_name: string;
  /** What this person's builds actually write to, after PER_USER is applied. */
  effective_schema: string;
  threads: number;
  vars: Record<string, unknown>;
  /** True for production: commands here need OPERATE, not EDIT. */
  protected: boolean;
}

export interface TransformReleaseRef {
  id: string;
  release_number: number;
  activated_at: string | null;
}

export interface TransformInvocationRef {
  id: string;
  command: string;
  selector: string | null;
  status: string;
  ended_at: string | null;
}

export interface TransformGitState {
  branch: string;
  repo_url: string;
  head_commit_sha: string | null;
  behind: boolean;
  last_status: string | null;
}

export interface Transform {
  id: string;
  name: string;
  description: string | null;
  /** MANAGED: AppBI's revisions are canonical. GIT: the checkout is. */
  mode: 'MANAGED' | 'GIT';
  status: string;
  dbt_project_name: string | null;
  warehouse: {
    connection_id: string;
    name: string;
    connector_key: string;
    connector_display_name: string | null;
    icon: string | null;
    destination_id: string | null;
  } | null;
  environment_name: string | null;
  health_status: string;
  health_message: string | null
  parse_status: 'UNKNOWN' | 'PENDING' | 'OK' | 'ERROR';
  parse_error: string | null;
  last_parsed_at: string | null;
  revision_number: number | null;
  file_count: number;
  /** Compared by content hash, so a save with no edit does not set it. */
  has_unpublished_changes: boolean;
  active_release: TransformReleaseRef | null;
  last_invocation: TransformInvocationRef | null;
  last_success_at: string | null;
  git: TransformGitState | null;
  schedule_type: string;
  next_run_at: string | null;
  updated_at: string | null;
}

export interface TransformRevisionRef {
  id: string;
  revision_number: number;
  content_hash: string;
  file_count: number;
  created_at: string;
}

export interface TransformDetail extends Transform {
  environments: TransformEnvironment[];
  default_environment_id: string | null;
  production_environment_id: string | null;
  working_revision: TransformRevisionRef | null;
  dbt_profile_name: string | null;
  project_file_valid: boolean;
  project_file_error: string | null;
  /** Counts by dbt resource type, straight from the manifest. */
  resource_counts: Record<string, number>;
  resource_bundle_id: string | null;
  engine: {
    dbt_core_version: string;
    adapter: string;
    adapter_version: string;
  };
  permissions: {
    can_edit: boolean;
    can_operate: boolean;
    can_delete: boolean;
  };
}

/* ── files ─────────────────────────────────────────────────────────────── */

export interface FileNode {
  name: string;
  path: string;
  type: 'file' | 'directory';
  size: number | null;
  is_text: boolean;
  children: FileNode[] | null;
}

export interface FileTree {
  revision_id: string;
  revision_number: number;
  content_hash: string;
  tree: FileNode[];
  file_count: number;
}

export interface FileContent {
  path: string;
  content: string;
  size: number;
  sha256: string;
  is_text: boolean;
  revision_id: string;
  /** The resource this file defines, so Preview/Build know what to select. */
  unique_id: string | null;
  resource_type: string | null;
}

export interface SaveResult {
  revision_id: string;
  revision_number: number;
  content_hash: string;
  file_count: number;
  saved_paths: string[];
  parse_invocation_id: string | null;
}

export interface FileTemplate {
  key: string;
  label: string;
  path: string;
  content: string;
}

/* ── resources ─────────────────────────────────────────────────────────── */

export interface ResourceSummary {
  unique_id: string;
  resource_type: string;
  name: string;
  package_name: string | null;
  path: string | null;
  patch_path: string | null;
  materialized: string | null;
  relation_name: string | null;
  database: string | null;
  schema: string | null;
  alias: string | null;
  description: string | null;
  tags: string[];
  group: string | null;
  enabled: boolean;
  /** Set when an AppBI Pipeline populates this source. Enrichment only. */
  produced_by_pipeline_id: string | null;
}

export interface ResourceColumn {
  name: string;
  description: string | null;
  data_type: string | null;
  tags: string[];
  constraints: unknown[];
  /** The warehouse actually has it. */
  in_warehouse: boolean;
  /** The YAML documents it. Both false-positives are visible drift. */
  documented: boolean;
}

export interface ResourceDetail extends ResourceSummary {
  /**
   * The node's whole parsed config, keys AppBI has no form for included.
   * Shown as-is, so a `contract` or a package option is visible rather than
   * appearing not to exist.
   */
  config: Record<string, unknown>;
  checksum: string | null;
  columns: ResourceColumn[];
  parents: string[];
  children: string[];
  tests: ResourceSummary[];
  last_result: {
    status: string;
    execution_time: number | null;
    message: string | null;
    rows_affected: number | null;
    bytes_processed: number | null;
    relation_name: string | null;
  } | null;
  freshness: {
    status: string;
    max_loaded_at: string | null;
    snapshotted_at: string | null;
    age_seconds: number | null;
    warn_after: Record<string, unknown>;
    error_after: Record<string, unknown>;
    message: string | null;
  } | null;
  warehouse: {
    database: string | null;
    schema: string | null;
    name: string | null;
    type: string | null;
    owner: string | null;
    comment: string | null;
    stats: Record<string, { label: string; value: unknown; description: string | null }>;
  } | null;
}

export interface ResourcePage {
  items: ResourceSummary[];
  total: number;
  counts: Record<string, number>;
}

export interface ResourceFacets {
  resource_types: string[];
  packages: string[];
  materializations: string[];
  groups: string[];
  tags: string[];
}

/* ── lineage ───────────────────────────────────────────────────────────── */

export interface LineageNode {
  unique_id: string;
  name: string;
  resource_type: string;
  materialized: string | null;
  package: string | null;
  path: string | null;
  tags: string[];
  relation_name: string | null;
  enabled: boolean;
  is_focus: boolean;
  produced_by_pipeline_id: string | null;
}

export interface TransformLineage {
  nodes: LineageNode[];
  edges: { parent: string; child: string }[];
  truncated: boolean;
  total_nodes: number;
  /** Two graphs, never merged: what is being edited vs what production runs. */
  scope: 'DRAFT' | 'RELEASE';
}

/* ── invocations ───────────────────────────────────────────────────────── */

/** Every dbt command the product runs. Not a product operation name. */
export type DbtCommand =
  | 'parse' | 'deps' | 'debug' | 'ls' | 'compile' | 'show'
  | 'run' | 'build' | 'test' | 'seed' | 'snapshot'
  | 'source-freshness' | 'docs-generate' | 'clone' | 'run-operation' | 'retry';

export interface InvocationRequest {
  command: DbtCommand;
  selector?: string | null;
  exclude?: string | null;
  environment_id?: string | null;
  full_refresh?: boolean;
  limit?: number | null;
  macro?: string | null;
  macro_args?: Record<string, unknown> | null;
  selector_name?: string | null;
  vars?: Record<string, unknown> | null;
  source?: 'DRAFT' | 'RELEASE';
}

export interface InvocationNode {
  unique_id: string;
  name: string;
  resource_type: string;
  status: string;
  execution_time: number | null;
  relation_name: string | null;
  message: string | null;
  rows_affected: number | null;
  bytes_processed: number | null;
  failures: number | null;
  error_location: { path?: string; line?: number };
}

export interface TransformInvocation {
  id: string;
  project_id: string;
  project_name: string | null;
  command: string;
  selector: string | null;
  exclude: string | null;
  args: Record<string, unknown>;
  status: string;
  environment_id: string;
  environment_name: string | null;
  revision_id: string;
  revision_number: number | null;
  release_id: string | null;
  release_number: number | null;
  trigger_type: string;
  triggered_by: string | null;
  queue_reason: string | null;
  started_at: string | null;
  ended_at: string | null;
  created_at: string | null;
  duration_seconds: number | null;
  nodes_total: number;
  nodes_succeeded: number;
  nodes_failed: number;
  nodes_skipped: number;
  tests_passed: number;
  tests_failed: number;
  tests_warned: number;
  rows_affected: number | null;
  error_code: string | null;
  error_summary: string | null;
  error_location: { path?: string; line?: number; name?: string; unique_id?: string };
  technical_message: string | null;
  remediation_action: string | null;
  exit_code: number | null;
  dbt_invocation_id: string | null;
  is_stale: boolean;
  actions: { can_cancel?: boolean; can_retry?: boolean; can_view_logs?: boolean };
}

/** What `dbt show` returned. Column order is the query's own. */
export interface PreviewResult {
  show?: string;
  data?: Record<string, unknown>[];
  rows?: unknown[][];
  [key: string]: unknown;
}

export interface TransformInvocationDetail extends TransformInvocation {
  nodes: InvocationNode[];
  preview: PreviewResult | null;
}

export interface TransformLogPage {
  invocation_id: string;
  lines: string[];
  next_cursor: number;
  has_more: boolean;
  total_lines: number;
}

export interface CompiledCode {
  unique_id: string;
  compiled_code: string | null;
  raw_code: string | null;
}

/** Parse errors, failed nodes and failed tests, in one shape. */
export interface TransformProblem {
  severity: 'error' | 'warning';
  source: 'parse' | 'compile' | 'run' | 'test' | 'yaml';
  message: string;
  path: string | null;
  line: number | null;
  unique_id: string | null;
  resource_name: string | null;
}

export interface TransformProblems {
  problems: TransformProblem[];
  parse_status: string;
  checked_at: string | null;
}

/* ── releases ──────────────────────────────────────────────────────────── */

export interface TransformRelease {
  id: string;
  release_number: number;
  /** VERIFYING until proven runnable; only READY or ACTIVE can go live. */
  status: 'VERIFYING' | 'READY' | 'FAILED' | 'ACTIVE' | 'RETIRED';
  is_active: boolean;
  revision_number: number | null;
  project_hash: string;
  file_count: number;
  git_commit_sha: string | null;
  environment_name: string | null;
  dbt_version: string | null;
  verification_invocation_id: string | null;
  verification_error: string | null;
  verified_at: string | null;
  activated_at: string | null;
  notes: string | null;
  created_at: string | null;
  created_by: string | null;
}

export interface FileChange {
  path: string;
  change: 'A' | 'M' | 'D';
  size_before: number | null;
  size_after: number | null;
}

export interface PublishPlan {
  files: FileChange[];
  /** Resources whose own file changed. */
  affected_resources: {
    unique_id: string; name: string; resource_type: string;
    path: string | null; materialized: string | null;
  }[];
  /** Resources downstream of those, which rebuild even though their code did not change. */
  downstream_resources: {
    unique_id: string; name: string; resource_type: string;
    path: string | null; materialized: string | null;
  }[];
  draft_hash: string;
  live_hash: string | null;
  matches_live: boolean;
}

export interface PublishResult {
  release: TransformRelease;
  verification_invocation_id: string;
}

/* ── git ───────────────────────────────────────────────────────────────── */

export interface GitStatus {
  branch: string;
  repo_url: string;
  subdirectory: string;
  head_commit_sha: string | null;
  remote_commit_sha: string | null;
  behind: boolean;
  /** Saved here but not committed. Save and Commit are different states. */
  changes: FileChange[];
  last_pulled_at: string | null;
  last_status: string | null;
  last_message: string | null;
  auto_pull: boolean;
  interval_minutes: number;
}

export interface GitDiff {
  path: string;
  committed: string | null;
  working: string | null;
}

export interface GitBranch {
  name: string;
  commit_sha: string | null;
  current: boolean;
  protected: boolean;
}

export interface GitPullResult {
  changed: boolean;
  commit_sha: string | null;
  files_changed: number;
  changes?: { path: string; change: string }[];
}

export interface GitCommitResult {
  commit_sha: string;
  files_committed: number;
  branch: string;
  url: string;
}

/* ── repository inspection ─────────────────────────────────────────────── */

export interface RepositoryInspectResult {
  detected_root: string;
  dbt_project_name: string | null;
  dbt_version_requirement: unknown;
  profile_name: string | null;
  file_count: number;
  model_count: number;
  resource_directories: string[];
  packages: string[];
  branch: string | null;
  commit_sha: string | null;
  /** Notes, not losses -- nothing is dropped on import any more. */
  warnings: string[];
}

/* ── autocomplete and docs ─────────────────────────────────────────────── */

export interface CompletionItem {
  label: string;
  kind: string;
  detail: string | null;
  insert_text: string | null;
}

export interface Completions {
  refs: CompletionItem[];
  sources: CompletionItem[];
  macros: CompletionItem[];
  tests: CompletionItem[];
  columns: Record<string, string[]>;
}

export interface DocEntry {
  unique_id: string;
  name: string;
  resource_type: string;
  description: string | null;
  path: string | null;
  relation_name: string | null;
  columns: ResourceColumn[];
  tags: string[];
  group: string | null;
  tests: string[];
  parents: string[];
  children: string[];
}

export interface SearchHit {
  kind: 'file' | 'resource' | 'column';
  label: string;
  detail: string | null;
  path: string | null;
  unique_id: string | null;
  line: number | null;
  excerpt: string | null;
}

export interface TransformSearch {
  hits: SearchHit[];
  truncated: boolean;
}

/** A relation the warehouse physically holds, for writing a `source()`. */
export interface BrowsedRelation {
  name: string;
  type: string;
  schema: string;
}

export interface WarehouseBrowse {
  catalogs?: string[];
  schemas?: string[];
  catalog?: string | null;
  schema?: string | null;
  relations?: BrowsedRelation[];
}

export interface WarehouseColumn {
  name: string;
  data_type: string;
  nullable: boolean;
}

export interface WarehouseColumns {
  schema: string;
  table: string;
  columns: WarehouseColumn[];
}

/** One column as the generator form describes it. */
export interface GenerateColumn {
  name: string;
  alias?: string | null;
  selected: boolean;
  unique: boolean;
  not_null: boolean;
}

export interface GenerateModelRequest {
  source_name: string;
  schema_name: string;
  table_name: string;
  model_name: string;
  columns: GenerateColumn[];
  materialized: 'view' | 'table';
  description?: string | null;
  expected_revision_id?: string | null;
}

export interface OverviewKpis {
  active_pipelines: number;
  running_now: number;
  failed_last_24h: number;
  success_rate_7d: number | null;
  sources_needing_attention: number;
  destinations_needing_attention: number;
  total_sources: number;
  total_destinations: number;
  records_synced_24h: number;
}

export interface Overview {
  kpis: OverviewKpis;
  recent_failures: Run[];
  running: Run[];
  recent_successes: Run[];
  attention_pipelines: Pipeline[];
  connector_updates: Connector[];
  onboarding: Record<string, boolean>;
}

export interface MonitoringRow {
  pipeline: Pipeline;
  freshness_deadline: string | null;
  freshness_breached: boolean;
  failure_streak: number;
  last_success_age_seconds: number | null;
}

export interface MonitoringResponse {
  engine: EngineStatus;
  pipelines: MonitoringRow[];
  counts: Record<string, number>;
}

export interface EngineStatus {
  label: string;
  operational: boolean;
  engine_type?: string | null;
  version?: string | null;
  detail?: string | null;
  checked_at?: string | null;
  metrics?: Record<string, unknown>;
  adapter_contract_version?: string | null;
  product_version?: string | null;
  reconciliation_lag_seconds?: number | null;
  active_runs?: number | null;
  queued_runs?: number | null;
}

export interface AlertRule {
  id: string;
  name: string;
  event_type: string;
  resource_id: string | null;
  threshold: number;
  channel: string;
  channel_config: Record<string, unknown>;
  cooldown_seconds: number;
  enabled: boolean;
  created_at: string;
}

export interface AppNotification {
  id: string;
  event_type: string;
  severity: string;
  title: string;
  body: string | null;
  resource_type: string | null;
  resource_id: string | null;
  run_id: string | null;
  remediation_action: string | null;
  occurrence_count: number;
  status: string;
  created_at: string;
  last_seen_at: string | null;
}

export interface AuditEvent {
  id: string;
  actor_type: string;
  actor_id: string | null;
  actor_label: string | null;
  action: string;
  resource_type: string | null;
  resource_id: string | null;
  resource_name: string | null;
  result: string;
  before_summary: Record<string, unknown> | null;
  after_summary: Record<string, unknown> | null;
  trace_id: string | null;
  created_at: string;
}

export interface Member {
  id: string;
  user_id: string;
  email: string;
  full_name: string;
  role: string;
  created_at: string;
}

export interface WorkspaceSettings {
  id: string;
  name: string;
  slug: string;
  timezone: string;
  allow_save_without_test: boolean;
  auto_accept_additive_schema: boolean;
  min_schedule_interval_seconds: number;
  max_concurrent_runs_per_workspace: number;
}


// ── Connector Builder ──────────────────────────────────────────────────────

export interface BuilderKeyValue {
  key: string;
  value: string;
}

/** Where a value is placed on the outgoing request. */
export type BuilderInjectInto =
  | 'request_parameter' | 'header' | 'body_data' | 'body_json';

export interface BuilderPagination {
  mode: 'none' | 'page' | 'offset' | 'cursor' | 'link_header';
  /** Blank means the API pages but takes no size. Never defaulted. */
  page_size?: number | null;
  page_param?: string;
  size_param?: string;
  start_from?: number;
  inject_on_first_request?: boolean;
  inject_into?: BuilderInjectInto;
  cursor_path?: string;
  stop_condition?: string;
}

export interface BuilderPartition {
  mode: 'none' | 'list' | 'parent';
  values?: string;
  /** Request field carrying the partition value, so choosing a parent sends it. */
  param?: string;
  inject_into?: BuilderInjectInto;
  cursor_field?: string;
  parent_stream?: string;
  parent_key?: string;
  partition_field?: string;
  /** `incremental_dependency`. Off by default; see the warning in the form. */
  incremental_parent?: boolean;
}

export interface BuilderTransformation {
  type: 'add' | 'remove';
  path: string;
  value?: string;
}

export interface BuilderBackoff {
  mode: 'none' | 'constant' | 'exponential' | 'header';
  seconds?: number;
  factor?: number;
  header?: string;
}

export interface BuilderErrorHandler {
  max_retries?: number;
  backoff?: BuilderBackoff;
  filters?: { http_codes: number[]; predicate?: string; action: string; message?: string }[];
}

export interface BuilderStream {
  name: string;
  path: string;
  http_method: 'GET' | 'POST';
  record_selector: string;
  record_filter?: string;
  primary_key: string;
  pagination: BuilderPagination;
  incremental: boolean;
  cursor_field?: string;
  cursor_param?: string;
  cursor_end_param?: string;
  cursor_format?: string;
  cursor_inject_into?: BuilderInjectInto;
  /** 'server': the API takes the bounds. 'client': it does not, so we filter. */
  cursor_filter_mode?: 'server' | 'client';
  step?: string;
  lookback?: string;
  query_params: BuilderKeyValue[];
  headers: BuilderKeyValue[];
  request_body?: { mode: 'json' | 'form'; entries: BuilderKeyValue[] };
  partition?: BuilderPartition;
  transformations?: BuilderTransformation[];
  error_handler?: BuilderErrorHandler;
  schema?: Record<string, unknown>;
}

export interface BuilderOAuth {
  token_url?: string;
  scopes?: string;
  grant_type?: string;
}

export interface BuilderAuth {
  method: 'none' | 'api_key' | 'bearer' | 'basic' | 'oauth2' | 'jwt' | 'session_token';
  header?: string;
  inject_into?: 'header' | 'request_parameter';
  oauth?: BuilderOAuth;
  jwt?: { algorithm?: string; token_duration?: number };
  session?: { login_path?: string; token_path?: string; header?: string; expiration?: string };
}

export interface BuilderUserInput {
  key: string;
  title?: string;
  type?: 'string' | 'integer' | 'number' | 'boolean';
  secret?: boolean;
  required?: boolean;
  default?: string;
  description?: string;
}

export interface BuilderDefinition {
  name: string;
  base_url: string;
  auth: BuilderAuth;
  user_inputs?: BuilderUserInput[];
  streams: BuilderStream[];
}

export type BuilderIconKey =
  | 'api' | 'database' | 'users' | 'commerce' | 'finance' | 'analytics'
  | 'workflow' | 'support' | 'files' | 'custom';

export interface BuilderProject {
  id: string;
  name: string;
  description: string | null;
  connector_key: string;
  icon: BuilderIconKey;
  status: 'DRAFT' | 'PUBLISHED' | 'ARCHIVED';
  published_version: number;
  published_at: string | null;
  last_tested_at: string | null;
  last_test_ok: boolean | null;
  stream_count: number;
  updated_at: string | null;
}

export interface BuilderProjectDetail extends BuilderProject {
  definition: BuilderDefinition;
}

export interface BuilderTestResult {
  ok: boolean;
  records: Record<string, unknown>[];
  logs: string[];
  error: { summary: string; code: string; category: string; technical_message?: string } | null;
  record_count: number;
  inferred_fields: string[];
  inferred_schema: Record<string, unknown> | null;
  requests: { url: string | null; status: number | null; body: string | null }[];
  /** False when the engine validated the connector but cannot return sample
   *  rows, so an empty `records` is a property of the engine, not a result. */
  record_preview_supported: boolean;
  test_run_id: string;
  test_session_id: string | null;
}

export type BuilderAIConfidence = 'confirmed' | 'likely' | 'unknown';

export interface BuilderAISource {
  id: string;
  name: string;
  source_type: 'FILE' | 'URL';
  mime_type: string | null;
  source_url: string | null;
  size_bytes: number;
  status: 'UPLOADED' | 'ANALYZED' | 'FAILED';
  knowledge: Record<string, unknown> | null;
  created_at: string;
}

export interface BuilderAIPlanStream {
  name: string;
  path: string;
  http_method: 'GET' | 'POST';
  confidence: BuilderAIConfidence;
  evidence: { source_id: string; location: string; detail: string }[];
}

export interface BuilderAIPlan {
  id: string;
  status: string;
  plan: {
    name: string;
    description: string;
    icon: BuilderIconKey;
    base_url: string;
    auth: { method: string; confidence: BuilderAIConfidence };
    streams: BuilderAIPlanStream[];
    unknowns: string[];
    assumptions: string[];
  };
}

export interface BuilderAIChangeSet {
  id: string;
  project_id: string;
  base_hash: string;
  status: 'PROPOSED' | 'APPLIED' | 'REJECTED' | 'UNDONE';
  operations: { op: 'add' | 'replace' | 'remove'; path: string; value_json: string; label: string }[];
  reason: string;
  evidence: { source_id: string; location: string; detail: string }[];
  model: string;
  prompt_version: string;
  created_at: string;
}

export interface BuilderAISession {
  id: string;
  available: boolean;
  sources: {
    id: string;
    name: string;
    source_type: 'FILE' | 'URL';
    status: 'UPLOADED' | 'ANALYZED' | 'FAILED';
  }[];
  messages: {
    id: string;
    role: 'user' | 'assistant';
    content: string;
    context: Record<string, unknown>;
    created_at: string;
  }[];
  change_set: BuilderAIChangeSet | null;
}

export interface BuilderAIChangeResult {
  project: BuilderProjectDetail;
  change_set: BuilderAIChangeSet;
}
