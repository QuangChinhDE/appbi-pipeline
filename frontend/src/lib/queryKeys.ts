/**
 * Query key convention from section 34.1. Every key is workspace-scoped so a
 * workspace switch can evict an entire tenant's cache in one call.
 */
export const qk = {
  me: () => ['me'] as const,
  workspace: (ws: string) => ['workspace', ws] as const,
  settings: (ws: string) => ['workspace', ws, 'settings'] as const,
  members: (ws: string) => ['workspace', ws, 'members'] as const,
  connectors: (ws: string, filters?: unknown) => ['workspace', ws, 'connectors', filters] as const,
  connector: (ws: string, key: string) => ['workspace', ws, 'connector', key] as const,
  sources: (ws: string, filters?: unknown) => ['workspace', ws, 'sources', filters] as const,
  source: (ws: string, id: string) => ['workspace', ws, 'source', id] as const,
  sourceSchema: (ws: string, id: string) => ['workspace', ws, 'source', id, 'schema'] as const,
  destinations: (ws: string, filters?: unknown) => ['workspace', ws, 'destinations', filters] as const,
  destination: (ws: string, id: string) => ['workspace', ws, 'destination', id] as const,
  pipelines: (ws: string, filters?: unknown) => ['workspace', ws, 'pipelines', filters] as const,
  pipeline: (ws: string, id: string) => ['workspace', ws, 'pipeline', id] as const,
  // ── Transform V2 ────────────────────────────────────────────────────────
  // One key per React Query boundary. A project with 5,000 resources must be
  // able to refresh its file tree without refetching its lineage, so these are
  // deliberately not nested under a single `transform` key.
  transforms: (ws: string, filters?: unknown) => ['workspace', ws, 'transforms', filters] as const,
  transform: (ws: string, id: string) => ['workspace', ws, 'transform', id] as const,
  transformFiles: (ws: string, id: string) =>
    ['workspace', ws, 'transform', id, 'files'] as const,
  transformFile: (ws: string, id: string, path: string) =>
    ['workspace', ws, 'transform', id, 'file', path] as const,
  transformTemplates: (ws: string, id: string) =>
    ['workspace', ws, 'transform', id, 'templates'] as const,
  transformResources: (ws: string, id: string, filters?: unknown) =>
    ['workspace', ws, 'transform', id, 'resources', filters] as const,
  transformResource: (ws: string, id: string, uniqueId: string, scope?: string) =>
    ['workspace', ws, 'transform', id, 'resource', uniqueId, scope ?? 'DRAFT'] as const,
  transformFacets: (ws: string, id: string, scope?: string) =>
    ['workspace', ws, 'transform', id, 'facets', scope ?? 'DRAFT'] as const,
  transformLineage: (ws: string, id: string, options?: unknown) =>
    ['workspace', ws, 'transform', id, 'lineage', options] as const,
  transformCompiled: (ws: string, id: string, uniqueId: string) =>
    ['workspace', ws, 'transform', id, 'compiled', uniqueId] as const,
  transformProblems: (ws: string, id: string) =>
    ['workspace', ws, 'transform', id, 'problems'] as const,
  transformCompletions: (ws: string, id: string) =>
    ['workspace', ws, 'transform', id, 'completions'] as const,
  transformDocs: (ws: string, id: string, filters?: unknown) =>
    ['workspace', ws, 'transform', id, 'docs', filters] as const,
  transformSearch: (ws: string, id: string, q: string, content?: boolean) =>
    ['workspace', ws, 'transform', id, 'search', q, content ?? false] as const,
  transformEnvironments: (ws: string, id: string) =>
    ['workspace', ws, 'transform', id, 'environments'] as const,
  transformInvocations: (ws: string, id: string, filters?: unknown) =>
    ['workspace', ws, 'transform', id, 'invocations', filters] as const,
  transformReleases: (ws: string, id: string) =>
    ['workspace', ws, 'transform', id, 'releases'] as const,
  transformPublishPlan: (ws: string, id: string) =>
    ['workspace', ws, 'transform', id, 'publish-plan'] as const,
  transformGitStatus: (ws: string, id: string) =>
    ['workspace', ws, 'transform', id, 'git-status'] as const,
  transformGitDiff: (ws: string, id: string, path: string) =>
    ['workspace', ws, 'transform', id, 'git-diff', path] as const,
  transformGitBranches: (ws: string, id: string) =>
    ['workspace', ws, 'transform', id, 'git-branches'] as const,
  // Not scoped to one project: an invocation is addressable on its own so the
  // Runs page and the workbench share one cache entry for it.
  transformInvocation: (ws: string, invocationId: string) =>
    ['workspace', ws, 'transform-invocation', invocationId] as const,
  transformInvocationLogs: (ws: string, invocationId: string) =>
    ['workspace', ws, 'transform-invocation', invocationId, 'logs'] as const,
  transformSystems: (ws: string) => ['workspace', ws, 'transform-systems'] as const,
  transformConnections: (ws: string, connectorKey?: string) =>
    ['workspace', ws, 'transform-connections', connectorKey ?? ''] as const,
  transformWarehouseAll: (ws: string, id: string) =>
    ['workspace', ws, 'transform-warehouse', id] as const,
  transformWarehouse: (ws: string, id: string, catalog?: string, schema?: string) =>
    ['workspace', ws, 'transform-warehouse', id, catalog ?? '', schema ?? ''] as const,

  // Not workspace-scoped: it is fetched from the engine on demand and never
  // part of the pipeline payload, so it must not be invalidated with it.
  pipelineState: (id: string) => ['pipeline-state', id] as const,
  schemaDiff: (ws: string, id: string) => ['workspace', ws, 'pipeline', id, 'schema-diff'] as const,
  runs: (ws: string, filters?: unknown) => ['workspace', ws, 'runs', filters] as const,
  run: (ws: string, id: string) => ['workspace', ws, 'run', id] as const,
  runLogs: (ws: string, id: string) => ['workspace', ws, 'run', id, 'logs'] as const,
  overview: (ws: string) => ['workspace', ws, 'overview'] as const,
  monitoring: (ws: string) => ['workspace', ws, 'monitoring'] as const,
  engine: (ws: string) => ['workspace', ws, 'engine'] as const,
  alertRules: (ws: string) => ['workspace', ws, 'alert-rules'] as const,
  notifications: (ws: string, filters?: unknown) => ['workspace', ws, 'notifications', filters] as const,
  unread: (ws: string) => ['workspace', ws, 'unread'] as const,
  audit: (ws: string, filters?: unknown) => ['workspace', ws, 'audit', filters] as const,
  builderProjects: (ws: string) => ['workspace', ws, 'builder'] as const,
  builderProject: (ws: string, id: string) => ['workspace', ws, 'builder', id] as const,
};
