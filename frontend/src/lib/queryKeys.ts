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
