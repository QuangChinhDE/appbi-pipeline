/**
 * Product API client.
 *
 * The FE never calls the engine — everything goes through /api/v1 (guardrail 1).
 * Errors always arrive in the normalized envelope, so `ApiError` carries the
 * remediation action a screen turns into its primary CTA.
 */

import type {
  Actor, ActorDetail, ActorTestResult, AlertRule, AppNotification, AuditEvent,
  BuilderDefinition, BuilderProject, BuilderProjectDetail, BuilderTestResult, Connector,
  ConnectorDetail, CurrentUser, EngineStatus, Member, MonitoringResponse, Overview, Paginated,
  ConnectionStateView, Pipeline, PipelineDetail, Run, RunDetail, RunLogPage, SchemaDiff,
  SchemaSnapshot,
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
  create: (body: { name: string; description?: string }) =>
    post<BuilderProjectDetail>('/builder/projects', body),
  update: (id: string, body: { name?: string; description?: string; definition?: BuilderDefinition }) =>
    patch<BuilderProjectDetail>(`/builder/projects/${id}`, body),
  remove: (id: string) => del<void>(`/builder/projects/${id}`),
  test: (id: string, body: { stream_name?: string; config?: Record<string, unknown> }) =>
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
export const runApi = {
  list: (query?: {
    pipeline_id?: string; status?: string; trigger_type?: string; error_category?: string;
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
