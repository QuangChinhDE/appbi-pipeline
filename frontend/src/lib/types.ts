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
  field_count: number;
  fields: { name: string; type: string; nullable: boolean }[];
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
}

export interface Run {
  id: string;
  short_id: string;
  pipeline: ActorRef | null;
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
}

export interface RunLogPage {
  run_id: string;
  lines: string[];
  next_cursor: number | null;
  has_more: boolean;
  total_lines: number | null;
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

export interface BuilderPagination {
  mode: 'none' | 'page' | 'offset' | 'cursor' | 'link_header';
  page_size?: number;
  page_param?: string;
  size_param?: string;
  start_from?: number;
  inject_into?: 'request_parameter' | 'header' | 'body_json';
  cursor_path?: string;
  stop_condition?: string;
}

export interface BuilderPartition {
  mode: 'none' | 'list' | 'parent';
  values?: string;
  param?: string;
  cursor_field?: string;
  parent_stream?: string;
  parent_key?: string;
  partition_field?: string;
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
  filters?: { http_codes: number[]; action: string; message?: string }[];
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

export interface BuilderProject {
  id: string;
  name: string;
  description: string | null;
  connector_key: string;
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
}
