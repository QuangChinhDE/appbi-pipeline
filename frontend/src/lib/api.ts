/**
 * Product API client.
 *
 * The FE never calls the engine — everything goes through /api/v1 (guardrail 1).
 * Errors always arrive in the normalized envelope, so `ApiError` carries the
 * remediation action a screen turns into its primary CTA.
 */

import type {
  Actor, ActorDetail, ActorTestResult, AlertRule, AppNotification, AuditEvent,
  BuilderAIChangeResult, BuilderAIPlan, BuilderAISession, BuilderAISource,
  BuilderDefinition, BuilderIconKey, BuilderProject, BuilderProjectDetail, BuilderTestResult, Connector,
  ConnectorDetail, CurrentUser, EngineStatus, Member, MonitoringResponse, Overview, Paginated,
  ConnectionStateView, Pipeline, PipelineDetail, Run, RunDetail, RunLogPage, SchemaDiff,
  SchemaSnapshot, DataAsset, Transform, TransformDestinationCapability, TransformDetail,
  TransformExecution, TransformInputCandidates, TransformLineage, TransformModel,
  TransformDiffEntry, TransformOperation, TransformRelease, TransformReleaseModel,
  TransformTest,
  ColumnProfile,
  WarehouseBrowse,
  WarehouseConnection,
  TransformSystem,
  RepositoryImportPreview,
  GitSourceState,
  GitPullResult,
  DraftedModel,
  ScheduleConfig, WorkspaceSettings,
} from './types';

const BASE = '/api/v1';

export interface CompatibilityMatrix {
  product_version: string;
  engine: {
    type: string;
    version: string | null;
    adapter_contract_version: string;
    reachable: boolean;
  };
  connectors: Record<string, {
    image: string;
    certification: string;
    spec_source: string;
    last_refreshed_at: string | null;
  }>;
}

export interface ErrorConstraint {
  type: string;
  id: string;
  name: string;
}

export interface ErrorEnvelope {
  code: string;
  message: string;
  category: string;
  trace_id: string;
  remediation?: { action: string; resource_id?: string };
  technical_message?: string;
  constraints?: ErrorConstraint[];
  details?: Record<string, unknown>;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly category: string;
  readonly traceId: string;
  readonly remediation?: { action: string; resource_id?: string };
  readonly technicalMessage?: string;
  readonly constraints?: ErrorConstraint[];
  readonly details?: Record<string, unknown>;

  constructor(status: number, envelope: ErrorEnvelope) {
    super(envelope.message);
    this.name = 'ApiError';
    this.status = status;
    this.code = envelope.code;
    this.category = envelope.category;
    this.traceId = envelope.trace_id;
    this.remediation = envelope.remediation;
    this.technicalMessage = envelope.technical_message;
    this.constraints = envelope.constraints;
    this.details = envelope.details;
  }
}

type Query = Record<string, string | number | boolean | null | undefined>;

function withQuery(path: string, query?: Query): string {
  if (!query) return `${BASE}${path}`;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === '') continue;
    params.set(key, String(value));
  }
  const qs = params.toString();
  return qs ? `${BASE}${path}?${qs}` : `${BASE}${path}`;
}

async function request<T>(
  method: string,
  path: string,
  options: { body?: unknown; query?: Query; headers?: Record<string, string> } = {},
): Promise<T> {
  const response = await fetch(withQuery(path, options.query), {
    method,
    credentials: 'include',
    headers: {
      ...(options.body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      ...options.headers,
    },
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
    cache: 'no-store',
  });

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  const payload = text ? JSON.parse(text) : null;

  if (!response.ok) {
    const envelope: ErrorEnvelope = payload?.error ?? {
      code: 'NETWORK_ERROR',
      message: 'Could not reach the server.',
      category: 'UNKNOWN',
      trace_id: '',
    };
    throw new ApiError(response.status, envelope);
  }
  return payload as T;
}

const get = <T>(path: string, query?: Query) => request<T>('GET', path, { query });
const post = <T>(path: string, body?: unknown, extra?: { query?: Query; headers?: Record<string, string> }) =>
  request<T>('POST', path, { body, ...extra });
const patch = <T>(path: string, body?: unknown) => request<T>('PATCH', path, { body });
const put = <T>(path: string, body?: unknown) => request<T>('PUT', path, { body });
const del = <T>(path: string, query?: Query) => request<T>('DELETE', path, { query });

// ── auth ───────────────────────────────────────────────────────────────────
export const authApi = {
  login: (email: string, password: string) =>
    post<CurrentUser>('/auth/login', { email, password }),
  logout: () => post<{ ok: boolean }>('/auth/logout'),
  me: () => get<CurrentUser>('/auth/me'),
  // Returns a fresh session: changing the password revokes every token issued
  // before it, including the caller's own.
  changePassword: (currentPassword: string, newPassword: string) =>
    post<CurrentUser>('/auth/change-password', {
      current_password: currentPassword,
      new_password: newPassword,
    }),
  switchWorkspace: (workspaceId: string) =>
    post<CurrentUser>(`/auth/switch-workspace/${workspaceId}`),
};

// ── workspace ──────────────────────────────────────────────────────────────
export const workspaceApi = {
  settings: () => get<WorkspaceSettings>('/workspace/settings'),
  updateSettings: (body: Partial<WorkspaceSettings>) =>
    patch<WorkspaceSettings>('/workspace/settings', body),
  members: () => get<Member[]>('/workspace/members'),
  invite: (body: { email: string; full_name: string; role: string; password: string }) =>
    post<Member>('/workspace/members', body),
  updateRole: (memberId: string, role: string) =>
    patch<Member>(`/workspace/members/${memberId}`, { role }),
  removeMember: (memberId: string) => del<void>(`/workspace/members/${memberId}`),
};

// ── connector builder ──────────────────────────────────────────────────────
export const builderApi = {
  list: () => get<BuilderProject[]>('/builder/projects'),
  detail: (id: string) => get<BuilderProjectDetail>(`/builder/projects/${id}`),
  create: (body: { name: string; description?: string; icon?: BuilderIconKey }) =>
    post<BuilderProjectDetail>('/builder/projects', body),
  update: (id: string, body: { name?: string; description?: string; icon?: BuilderIconKey; definition?: BuilderDefinition }) =>
    patch<BuilderProjectDetail>(`/builder/projects/${id}`, body),
  remove: (id: string) => del<void>(`/builder/projects/${id}`),
  test: (id: string, body: { stream_name?: string; config?: Record<string, unknown>; test_session_id?: string | null }) =>
    post<BuilderTestResult>(`/builder/projects/${id}/test`, body),
  publish: (id: string) => post<BuilderProjectDetail>(`/builder/projects/${id}/publish`),
  manifest: (id: string) => get<Record<string, unknown>>(`/builder/projects/${id}/manifest`),
  manifestYaml: async (id: string) => {
    const response = await fetch(`/api/v1/builder/projects/${id}/manifest.yaml`, {
      credentials: 'include', cache: 'no-store',
    });
    if (!response.ok) throw new Error('manifest');
    return response.text();
  },
  importManifest: (id: string, manifest: string) =>
    post<BuilderProjectDetail>(`/builder/projects/${id}/import`, { manifest }),
};

async function requestForm<T>(method: string, path: string, body: FormData): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    method, credentials: 'include', body, cache: 'no-store',
  });
  const text = await response.text();
  const payload = text ? JSON.parse(text) : null;
  if (!response.ok) {
    throw new ApiError(response.status, payload?.error ?? {
      code: 'NETWORK_ERROR', message: 'Could not reach the server.',
      category: 'UNKNOWN', trace_id: '',
    });
  }
  return payload as T;
}

export const builderAiApi = {
  uploadSource: (file: File, projectId?: string) => {
    const form = new FormData();
    form.set('file', file);
    if (projectId) form.set('project_id', projectId);
    return requestForm<BuilderAISource>('POST', '/builder/ai/sources', form);
  },
  addUrl: (url: string, projectId?: string) =>
    post<BuilderAISource>('/builder/ai/sources/url', { url, project_id: projectId }),
  removeSource: (id: string) => del<void>(`/builder/ai/sources/${id}`),
  analyzeSource: (id: string) =>
    post<{ source: BuilderAISource; knowledge: Record<string, unknown> }>(`/builder/ai/sources/${id}/analyze`),
  createPlan: (sourceIds: string[], intent?: string) =>
    post<BuilderAIPlan>('/builder/ai/plans', { source_ids: sourceIds, intent }),
  removePlan: (id: string) => del<void>(`/builder/ai/plans/${id}`),
  createProject: (
    planId: string,
    review: {
      name: string;
      description: string;
      icon: import('./types').BuilderIconKey;
      streams: { source_name: string; name: string; enabled: boolean }[];
    },
  ) => post<BuilderProjectDetail>('/builder/projects/from-plan', {
    plan_id: planId, ...review,
  }),
  session: (projectId: string) =>
    get<BuilderAISession>(`/builder/projects/${projectId}/ai/session`),
  changeSet: (projectId: string, changeSetId: string) =>
    get<import('./types').BuilderAIChangeSet>(
      `/builder/projects/${projectId}/ai/change-sets/${changeSetId}`,
    ),
  apply: (projectId: string, changeSetId: string) =>
    post<BuilderAIChangeResult>(
      `/builder/projects/${projectId}/ai/change-sets/${changeSetId}/apply`,
    ),
  reject: (projectId: string, changeSetId: string) =>
    post<BuilderAIChangeResult>(
      `/builder/projects/${projectId}/ai/change-sets/${changeSetId}/reject`,
    ),
  undo: (projectId: string, changeSetId: string) =>
    post<BuilderAIChangeResult>(
      `/builder/projects/${projectId}/ai/change-sets/${changeSetId}/undo`,
    ),
  chat: async (
    projectId: string,
    body: { message: string; stream_name?: string; section?: string; test_run_id?: string },
    onEvent: (event: 'progress' | 'final', data: Record<string, unknown>) => void,
  ) => {
    const response = await fetch(`${BASE}/builder/projects/${projectId}/ai/chat`, {
      method: 'POST', credentials: 'include', cache: 'no-store',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    if (!response.ok || !response.body) {
      const payload = await response.json().catch(() => null);
      throw new ApiError(response.status, payload?.error ?? {
        code: 'NETWORK_ERROR', message: 'Could not reach the server.',
        category: 'UNKNOWN', trace_id: '',
      });
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done });
      const blocks = buffer.split('\n\n');
      buffer = blocks.pop() ?? '';
      for (const block of blocks) {
        const event = block.split('\n').find((line) => line.startsWith('event:'))?.slice(6).trim();
        const raw = block.split('\n').find((line) => line.startsWith('data:'))?.slice(5).trim();
        if (!event || !raw) continue;
        const data = JSON.parse(raw) as Record<string, unknown>;
        if (event === 'error') {
          throw new ApiError(502, data as unknown as ErrorEnvelope);
        }
        if (event === 'progress' || event === 'final') onEvent(event, data);
      }
      if (done) break;
    }
  },
};

// ── connector OAuth ────────────────────────────────────────────────────────
// The refresh token never comes back here. `start` returns a consent URL, the
// provider redirects to the API, and the page is handed an opaque grant id
// which the wizard passes to the save call.
export const oauthApi = {
  providers: () =>
    get<{ connector_key: string; provider: string; label: string; scopes: string[] }[]>(
      '/oauth/providers'),
  start: (connectorKey: string) =>
    post<{ authorize_url: string; state: string }>(`/oauth/${connectorKey}/start`),
  grant: (grantId: string) =>
    get<{
      id: string; connector_key: string; provider: string;
      account_label: string; consumed: boolean;
    }>(`/oauth/grant/${grantId}`),
};

// ── connectors ─────────────────────────────────────────────────────────────
export const connectorApi = {
  list: (query?: {
    type?: string;
    q?: string;
    category?: string;
    /** Only what this deployment offers. The create wizard sets this; the
     *  admin catalogue deliberately does not. */
    selectable?: string;
  }) =>
    get<Connector[]>('/connectors', query),
  detail: (connectorKey: string) => get<ConnectorDetail>(`/connectors/${connectorKey}`),
  refresh: (connectorKey?: string) =>
    post<{ result: Record<string, string> }>('/admin/connectors/refresh', undefined, {
      query: { connector_key: connectorKey },
    }),
  setCertification: (connectorKey: string, certification: string, reason?: string) =>
    post<ConnectorDetail>(`/admin/connectors/${connectorKey}/certification`, undefined, {
      query: { certification, reason },
    }),
  pin: (connectorKey: string, version: string) =>
    post<ConnectorDetail>(`/admin/connectors/${connectorKey}/pin`, undefined, { query: { version } }),
  compatibility: () => get<CompatibilityMatrix>('/admin/compatibility'),
};

// ── sources / destinations ─────────────────────────────────────────────────
export interface ActorListQuery {
  q?: string;
  connector_key?: string;
  health?: string;
  status?: string;
  usage?: string;
  limit?: number;
  offset?: number;
}

export interface ActorWritePayload {
  name: string;
  connector_key: string;
  description?: string | null;
  configuration: Record<string, unknown>;
  credentials: Record<string, unknown>;
  test_before_save: boolean;
  check_token?: string | null;
  /**
   * Opaque handle to a completed OAuth consent. The refresh token behind it is
   * held server-side and never passes through this client.
   */
  oauth_grant_id?: string | null;
}

function actorApi(base: 'sources' | 'destinations') {
  return {
    list: (query?: ActorListQuery) => get<Paginated<Actor>>(`/${base}`, query as Query),
    detail: (id: string) => get<ActorDetail>(`/${base}/${id}`),
    create: (body: ActorWritePayload) => post<ActorDetail>(`/${base}`, body),
    update: (
      id: string,
      body: Partial<ActorWritePayload> & { version?: number; test_before_save?: boolean },
    ) => patch<ActorDetail>(`/${base}/${id}`, body),
    testDraft: (body: {
      connector_key: string;
      configuration: Record<string, unknown>;
      credentials: Record<string, unknown>;
    }) => post<ActorTestResult>(`/${base}/test`, body),
    test: (id: string) => post<ActorTestResult>(`/${base}/${id}/test`),
    enable: (id: string) => post<ActorDetail>(`/${base}/${id}/enable`),
    disable: (id: string) => post<ActorDetail>(`/${base}/${id}/disable`),
    remove: (id: string, force = false) => del<void>(`/${base}/${id}`, { force }),
    pipelines: (id: string) =>
      get<{ id: string; name: string; status: string; next_run_at: string | null }[]>(
        `/${base}/${id}/pipelines`,
      ),
  };
}

export const sourceApi = {
  ...actorApi('sources'),
  discover: (id: string, force = false) =>
    post<SchemaSnapshot>(`/sources/${id}/discover`, undefined, { query: { force } }),
  schema: (id: string) => get<SchemaSnapshot | null>(`/sources/${id}/schema`),
};

export const destinationApi = actorApi('destinations');

// ── pipelines ──────────────────────────────────────────────────────────────
export const pipelineApi = {
  list: (query?: {
    q?: string; status?: string; health?: string; source_id?: string;
    destination_id?: string; limit?: number; offset?: number;
  }) => get<Paginated<Pipeline>>('/pipelines', query as Query),
  detail: (id: string) => get<PipelineDetail>(`/pipelines/${id}`),
  create: (body: unknown) => post<PipelineDetail>('/pipelines', body),
  update: (id: string, body: unknown) => patch<PipelineDetail>(`/pipelines/${id}`, body),
  enable: (id: string) => post<PipelineDetail>(`/pipelines/${id}/enable`),
  pause: (id: string) => post<PipelineDetail>(`/pipelines/${id}/pause`),
  remove: (id: string) => del<void>(`/pipelines/${id}`),
  rediscover: (id: string) => post<SchemaSnapshot>(`/pipelines/${id}/rediscover`),
  schemaDiff: (id: string) => get<SchemaDiff>(`/pipelines/${id}/schema-diff`),
  // Lazy on purpose: the answer comes from the engine, and the settings page
  // has to render whether or not the engine is reachable.
  state: (id: string) => get<ConnectionStateView>(`/pipelines/${id}/state`),
  // PUT, not PATCH: the body is the whole cursor. Sending a subset would drop
  // the streams it omits, which reads as "those streams reset" on the next run.
  setState: (id: string, state: Record<string, unknown>[]) =>
    put<ConnectionStateView>(`/pipelines/${id}/state`, { state }),
  approveSchema: (id: string, snapshotId: string, dropRemoved = true) =>
    post<PipelineDetail>(`/pipelines/${id}/schema-approve`, {
      snapshot_id: snapshotId,
      drop_removed_streams: dropRemoved,
    }),
  run: (id: string, idempotencyKey?: string) =>
    post<Run>(`/pipelines/${id}/runs`, undefined, {
      headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
    }),
  previewSchedule: (schedule: ScheduleConfig) =>
    post<{ description: string; timezone: string; next_runs: string[] }>(
      '/pipelines/schedule/preview',
      schedule,
    ),
};

// ── runs ───────────────────────────────────────────────────────────────────
export const transformApi = {
  list: (query?: { search?: string; limit?: number; offset?: number }) =>
    get<Paginated<Transform>>('/transforms', query as Query),
  detail: (id: string) => get<TransformDetail>(`/transforms/${id}`),
  destinations: () => get<TransformDestinationCapability[]>('/transforms/destinations'),
  inputCandidates: (destinationId: string) =>
    get<TransformInputCandidates>(`/transforms/destinations/${destinationId}/inputs`),
  registerAsset: (connectionId: string, body: {
    catalog_name?: string; schema_name: string; relation_name: string;
    pipeline_id?: string; pipeline_stream_id?: string;
  }) => post<DataAsset>(`/transforms/connections/${connectionId}/assets`, body),
  create: (body: {
    name: string; description?: string; warehouse_connection_id: string;
    default_schema: string; input_asset_ids: string[];
  }) => post<TransformDetail>('/transforms', body),
  update: (id: string, body: unknown) => patch<TransformDetail>(`/transforms/${id}`, body),
  remove: (id: string) => del<void>(`/transforms/${id}`),
  createModel: (id: string, body: unknown) =>
    post<TransformModel>(`/transforms/${id}/models`, body),
  updateModel: (id: string, modelId: string, body: unknown) =>
    patch<TransformModel>(`/transforms/${id}/models/${modelId}`, body),
  removeModel: (id: string, modelId: string) =>
    del<void>(`/transforms/${id}/models/${modelId}`),
  systems: () => get<TransformSystem[]>('/transforms/systems'),
  connections: () => get<WarehouseConnection[]>('/transforms/connections'),
  createConnection: (body: {
    connector_key: string; name: string; auth_method: string;
    project_id?: string; dataset_location?: string; credentials_json?: string;
    host?: string; port?: number; database?: string;
    username?: string; password?: string; ssl_mode?: string;
    oauth_grant_id?: string;
  }) => post<WarehouseConnection>('/transforms/connections', body),
  removeConnection: (id: string) => del<void>(`/transforms/connections/${id}`),
  startOauth: (connectorKey: string) =>
    post<{ authorize_url: string; state: string }>(`/oauth/${connectorKey}/start`),
  oauthGrant: (grantId: string) => get<{
    id: string; connector_key: string; provider: string;
    account_label: string; consumed: boolean;
  }>(`/oauth/grant/${grantId}`),
  browseWarehouse: (
    connectionId: string,
    options: { catalog?: string; schema?: string } = {},
  ) => {
    const query = new URLSearchParams();
    if (options.catalog) query.set('catalog', options.catalog);
    if (options.schema) query.set('schema', options.schema);
    const suffix = query.toString();
    return get<WarehouseBrowse>(
      `/transforms/connections/${connectionId}/warehouse${suffix ? `?${suffix}` : ''}`,
    );
  },
  inspectRepository: (body: {
    repo_url: string; ref?: string; subdirectory?: string; token?: string;
  }) => post<RepositoryImportPreview>('/transforms/imports/inspect', body),
  importRepository: (body: {
    repo_url: string; ref?: string; subdirectory?: string; token?: string;
    name: string; warehouse_connection_id: string; default_schema: string;
    auto_pull?: boolean; interval_minutes?: number;
  }) => post<{ transform: TransformDetail; warnings: string[] }>('/transforms/imports', body),
  configureGit: (id: string, body: {
    repo_url?: string; ref?: string | null; subdirectory?: string;
    token?: string; auto_pull?: boolean; interval_minutes?: number;
    auto_publish?: boolean;
  }) => put<GitSourceState>(`/transforms/${id}/git`, body),
  /** Reads the repository into this Transform. There is no write counterpart. */
  pullGit: (id: string, force = false) =>
    post<GitPullResult>(`/transforms/${id}/git/pull${force ? '?force=true' : ''}`, {}),
  profileInput: (id: string, assetId: string) =>
    post<{ columns: ColumnProfile[] }>(`/transforms/${id}/inputs/${assetId}/profile`, {}),
  draftModel: (id: string, body: { asset_id: string; intent: string }) =>
    post<DraftedModel>(`/transforms/${id}/ai/draft-model`, body),
  addTest: (id: string, modelId: string, body: unknown) =>
    post<TransformTest>(`/transforms/${id}/models/${modelId}/tests`, body),
  removeTest: (id: string, modelId: string, testId: string) =>
    del<void>(`/transforms/${id}/models/${modelId}/tests/${testId}`),
  run: (
    id: string,
    operation: TransformOperation,
    modelId?: string,
    options?: { fullRefresh?: boolean; source?: 'DRAFT' | 'RELEASE' },
  ) => post<TransformExecution>(`/transforms/${id}/runs`, {
    operation, model_id: modelId, full_refresh: options?.fullRefresh ?? false,
    source: options?.source ?? 'DRAFT',
  }),
  releases: (id: string) => get<TransformRelease[]>(`/transforms/${id}/releases`),
  diff: (id: string) =>
    get<{ changes: TransformDiffEntry[] }>(`/transforms/${id}/diff`),
  publish: (id: string, body: { notes?: string | null; activate?: boolean }) =>
    post<TransformRelease>(`/transforms/${id}/releases`, body),
  activateRelease: (id: string, releaseId: string) =>
    post<TransformRelease>(`/transforms/${id}/releases/${releaseId}/activate`, {}),
  releaseModels: (id: string, releaseId: string) =>
    get<{ models: TransformReleaseModel[] }>(
      `/transforms/${id}/releases/${releaseId}/models`),
  restoreRelease: (id: string, releaseId: string) =>
    post<TransformDetail>(`/transforms/${id}/releases/${releaseId}/restore`, {}),
  execution: (runId: string) => get<TransformExecution>(`/transforms/runs/${runId}`),
  cancel: (runId: string) => post<TransformExecution>(`/transforms/runs/${runId}/cancel`),
  lineage: (id: string) => get<TransformLineage>(`/transforms/${id}/lineage`),
  project: (id: string) => get<Record<string, string>>(`/transforms/${id}/project`),
  exportUrl: (id: string) => `/api/v1/transforms/${id}/export`,
};

export const runApi = {
  list: (query?: {
    type?: string; pipeline_id?: string; transform_id?: string; status?: string;
    trigger_type?: string; error_category?: string;
    since?: string; until?: string; limit?: number; offset?: number;
  }) => get<Paginated<Run>>('/runs', query as Query),
  detail: (id: string) => get<RunDetail>(`/runs/${id}`),
  cancel: (id: string) => post<RunDetail>(`/runs/${id}/cancel`),
  retry: (id: string) => post<RunDetail>(`/runs/${id}/retry`),
  logs: (id: string, cursor = 0, limit = 500) =>
    get<RunLogPage>(`/runs/${id}/logs`, { cursor, limit }),
};

// ── monitoring / alerts / audit ────────────────────────────────────────────
export const opsApi = {
  overview: () => get<Overview>('/overview'),
  monitoring: () => get<MonitoringResponse>('/monitoring'),
  engineStatus: () => get<EngineStatus>('/engine/status'),
  alertRules: () => get<AlertRule[]>('/alerts/rules'),
  createRule: (body: unknown) => post<AlertRule>('/alerts/rules', body),
  updateRule: (id: string, body: unknown) => patch<AlertRule>(`/alerts/rules/${id}`, body),
  deleteRule: (id: string) => del<void>(`/alerts/rules/${id}`),
  notifications: (query?: { status?: string; limit?: number }) =>
    get<AppNotification[]>('/alerts/notifications', query as Query),
  unreadCount: () => get<{ count: number }>('/alerts/unread-count'),
  acknowledge: (notificationId?: string) =>
    post<{ ok: boolean; message: string | null }>(
      '/alerts/notifications/acknowledge',
      undefined,
      { query: { notification_id: notificationId } },
    ),
  audit: (query?: {
    action?: string; resource_type?: string; resource_id?: string; actor_id?: string;
    since?: string; limit?: number; offset?: number;
  }) => get<Paginated<AuditEvent>>('/audit', query as Query),
};

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return 'Unknown error.';
}
