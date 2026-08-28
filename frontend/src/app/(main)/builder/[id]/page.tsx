'use client';

import * as React from 'react';
import { useParams, useRouter } from 'next/navigation';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle, CheckCircle2, Code2, CornerDownRight, Eye, EyeOff, ListTree,
  Play, Plus, Rocket, Save, Settings2, Trash2, Variable, X,
} from 'lucide-react';

import { ApiError, builderApi } from '@/lib/api';
import { qk } from '@/lib/queryKeys';
import { useWorkspaceId } from '@/hooks/use-current-user';
import { usePermissions } from '@/hooks/use-permissions';
import { toastError, toastSuccess } from '@/hooks/use-toast';
import { useI18n } from '@/providers/LanguageProvider';
import { Badge } from '@/components/ui/Badge';
import { Button, IconButton } from '@/components/ui/Button';
import { Input, Select } from '@/components/ui/Input';
import { ErrorState, Skeleton } from '@/components/ui/Feedback';
import { DetailHeader } from '@/components/layout/PageLayout';
import type {
  BuilderDefinition, BuilderStream, BuilderTestResult,
} from '@/lib/types';
import { Field, JinjaInput } from '@/components/builder/BuilderField';
import {
  StreamEditor, type BuilderStreamSection,
} from '@/components/builder/StreamEditor';
import { Tabs } from '@/components/ui/Tabs';
import { useUrlTab } from '@/hooks/use-url-tab';
import { cn } from '@/lib/utils';

const BUILDER_VIEWS = ['api', 'inputs', 'stream'] as const;
const STREAM_SECTIONS: readonly BuilderStreamSection[] = [
  'request', 'pagination', 'incremental', 'partition', 'transform', 'errors',
];
const TEST_VIEWS = ['records', 'schema', 'requests', 'logs'] as const;
type TestView = typeof TEST_VIEWS[number];

const EMPTY_STREAM = (name: string, path: string): BuilderStream => ({
  name,
  path,
  http_method: 'GET',
  record_selector: '',
  record_filter: '',
  primary_key: '',
  // No page size until somebody asks for one. Seeding 50 contradicted the
  // field's own hint and told the paginator a page length the API never
  // agreed to, which stops a sync on the server's first default-sized page.
  pagination: { mode: 'none' },
  incremental: false,
  query_params: [],
  headers: [],
  request_body: { mode: 'json', entries: [] },
  partition: { mode: 'none' },
  transformations: [],
  error_handler: {},
});

/** Stream indices in reading order, children indented under their parent.
 *
 * A flat row of pills hides the one relationship this connector type is about,
 * and stops being readable somewhere past six streams. Ordering by the parent
 * link instead makes `lead_service -> lead -> lead_feed` visible at a glance.
 *
 * Anything unreachable from a root -- an orphan, or a cycle somebody typed --
 * is appended at depth zero rather than dropped, because a stream you cannot
 * select is a stream you cannot fix.
 */
function streamOutline(streams: BuilderStream[]): { index: number; depth: number }[] {
  const byName = new Map(streams.map((s, i) => [s.name, i]));
  const parentOf = (s: BuilderStream) =>
    (s.partition?.mode === 'parent' && s.partition.parent_stream
      && byName.has(s.partition.parent_stream))
      ? byName.get(s.partition.parent_stream)! : null;

  const children = new Map<number, number[]>();
  const roots: number[] = [];
  streams.forEach((s, i) => {
    const parent = parentOf(s);
    if (parent === null || parent === i) roots.push(i);
    else children.set(parent, [...(children.get(parent) ?? []), i]);
  });

  const out: { index: number; depth: number }[] = [];
  const seen = new Set<number>();
  const walk = (index: number, depth: number) => {
    if (seen.has(index) || depth > 8) return;
    seen.add(index);
    out.push({ index, depth });
    for (const child of children.get(index) ?? []) walk(child, depth + 1);
  };
  roots.forEach((index) => walk(index, 0));
  streams.forEach((_, index) => { if (!seen.has(index)) out.push({ index, depth: 0 }); });
  return out;
}

/** One rail entry: a section, or a stream nested under its parent. */
function RailItem({
  label, active, onSelect, count, depth = 0, warn = false, icon, method, subtitle,
}: {
  label: string;
  active: boolean;
  onSelect: () => void;
  count?: number;
  depth?: number;
  warn?: boolean;
  icon?: React.ReactNode;
  method?: BuilderStream['http_method'];
  subtitle?: string;
}) {
  return (
    <button
      type="button"
      aria-current={active ? 'true' : undefined}
      onClick={onSelect}
      style={{ paddingInlineStart: `${8 + depth * 10}px` }}
      className={cn(
        'relative flex min-h-8 w-full items-center gap-1.5 rounded-md py-1.5 pe-2 text-left',
        'text-caption transition-colors',
        active
          ? 'bg-brand/10 font-emphasis text-brand before:absolute before:inset-y-1.5 before:left-0 before:w-0.5 before:rounded-full before:bg-brand'
          : 'text-text-secondary hover:bg-surface-2 hover:text-text-primary',
      )}
    >
      <span className="flex h-4 w-4 shrink-0 items-center justify-center text-text-quaternary">
        {depth > 0 ? <CornerDownRight className="h-3 w-3" aria-hidden /> : icon}
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex min-w-0 items-center gap-1.5">
          <span className="min-w-0 flex-1 truncate">{label}</span>
          {method && (
            <Badge
              variant={method === 'POST' ? 'info' : 'outline'}
              size="xs"
              pill={false}
              className="shrink-0 font-mono"
            >
              {method}
            </Badge>
          )}
        </span>
        {subtitle && (
          <span className="mt-0.5 block truncate font-mono text-tiny text-text-quaternary">
            {subtitle}
          </span>
        )}
      </span>
      {typeof count === 'number' && (
        <Badge variant="subtle" size="xs" className="ms-auto shrink-0">{count}</Badge>
      )}
      {warn && (
        <AlertTriangle className="ms-auto h-3 w-3 shrink-0 text-warning" aria-hidden />
      )}
    </button>
  );
}

/** Why this stream would not run, if it would not.
 *
 * Shown on the rail rather than at Test time. Both cases are silent failures:
 * a path of `/` reads the API root, and a parent chosen without sending its id
 * reads the same first page once per parent and reports success.
 */
function streamWarning(stream: BuilderStream): boolean {
  const path = (stream.path ?? '').replace(/\//g, '').trim();
  if (!path) return true;
  if (stream.partition?.mode === 'parent') {
    const usesPartition = JSON.stringify(stream).includes('stream_partition');
    if (!stream.partition.param && !usesPartition) return true;
    if (!stream.partition.parent_stream) return true;
  }
  return false;
}

export default function BuilderEditorPage() {
  const { t } = useI18n();
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const workspaceId = useWorkspaceId();
  const queryClient = useQueryClient();
  const { can } = usePermissions();
  const canEdit = can('connectors', 'edit');
  const { tab: view, setQuery, queryValue } = useUrlTab(BUILDER_VIEWS, 'api');

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: qk.builderProject(workspaceId, params.id),
    queryFn: () => builderApi.detail(params.id),
  });

  const [draft, setDraft] = React.useState<BuilderDefinition | null>(null);
  const requestedStream = Number.parseInt(queryValue('stream') ?? '0', 10);
  const activeStream = draft && Number.isInteger(requestedStream)
    ? Math.min(Math.max(requestedStream, 0), Math.max(draft.streams.length - 1, 0))
    : 0;
  const requestedSection = queryValue('section') as BuilderStreamSection | null;
  const streamSection = requestedSection && STREAM_SECTIONS.includes(requestedSection)
    ? requestedSection : 'request';
  const requestedTestView = queryValue('result') as TestView | null;
  const testView = requestedTestView && TEST_VIEWS.includes(requestedTestView)
    ? requestedTestView : 'records';
  const [adding, setAdding] = React.useState(false);
  const [newStream, setNewStream] = React.useState({ name: '', path: '' });
  const [dirty, setDirty] = React.useState(false);
  const [testResult, setTestResult] = React.useState<BuilderTestResult | null>(null);
  const [secrets, setSecrets] = React.useState<Record<string, string>>({});
  const [showManifest, setShowManifest] = React.useState(false);

  // Load the server copy once; after that the editor owns the state so typing
  // is never interrupted by a refetch.
  React.useEffect(() => {
    if (data && draft === null) setDraft(data.definition);
  }, [data, draft]);

  const patch = (next: Partial<BuilderDefinition>) => {
    setDraft((current) => (current ? { ...current, ...next } : current));
    setDirty(true);
  };

  const patchStream = (index: number, next: Partial<BuilderStream>) => {
    setDraft((current) => {
      if (!current) return current;
      const streams = current.streams.map((stream, i) =>
        i === index ? { ...stream, ...next } : stream);
      return { ...current, streams };
    });
    setDirty(true);
  };

  const configuredTestInputs = (draft?.user_inputs ?? [])
    .filter((field) => field.key.trim());
  const defaultTestConfig = Object.fromEntries(
    configuredTestInputs
      .filter((field) => field.default !== undefined && field.default !== '')
      .map((field) => [field.key, field.default!]),
  );
  const missingRequiredTestInput = configuredTestInputs.some((field) =>
    field.required && !String(secrets[field.key] ?? field.default ?? '').trim());

  const save = useMutation({
    mutationFn: () => builderApi.update(params.id, { definition: draft! }),
    onSuccess: () => {
      setDirty(false);
      queryClient.invalidateQueries({ queryKey: qk.builderProjects(workspaceId) });
      toastSuccess(t('builder.saved'));
    },
    onError: (caught) => toastError(caught),
  });

  const runTest = useMutation({
    mutationFn: async () => {
      // Save first: the server tests what is stored, so an untested edit would
      // silently produce a result for the previous version.
      if (dirty) await builderApi.update(params.id, { definition: draft! });
      setDirty(false);
      return builderApi.test(params.id, {
        stream_name: draft?.streams[activeStream]?.name,
        config: { ...defaultTestConfig, ...secrets },
      });
    },
    onSuccess: (result) => {
      setTestResult(result);
      setQuery({
        result: result.ok ? 'records' : result.logs.length > 0 ? 'logs' : 'requests',
      }, { replace: true });
      queryClient.invalidateQueries({ queryKey: qk.builderProject(workspaceId, params.id) });
      // A stream with no declared schema discovers zero columns, so the first
      // successful read fills it in. An existing schema is left alone — the
      // user is offered the update instead of having it done to them.
      const current = draft?.streams[activeStream];
      const hasSchema = Boolean(
        current?.schema && Object.keys((current.schema as Record<string, unknown>).properties ?? {}).length,
      );
      if (result.ok && result.inferred_schema && !hasSchema) {
        patchStream(activeStream, { schema: result.inferred_schema });
      }
      if (result.ok) {
        toastSuccess(result.record_preview_supported
          ? t('builder.testOk', { n: String(result.record_count) })
          : t('builder.testOkNoPreview'));
      }
    },
    onError: (caught) => {
      setTestResult(null);
      toastError(caught);
    },
  });

  const publish = useMutation({
    mutationFn: () => builderApi.publish(params.id),
    onSuccess: (project) => {
      queryClient.invalidateQueries({ queryKey: ['workspace', workspaceId] });
      toastSuccess(t('builder.published', { v: String(project.published_version) }));
    },
    onError: (caught) => toastError(caught),
  });

  const remove = useMutation({
    mutationFn: () => builderApi.remove(params.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: qk.builderProjects(workspaceId) });
      router.push('/builder');
    },
    onError: (caught) => toastError(caught),
  });

  // YAML is fetched only when the panel is open: compiling a manifest on every
  // keystroke would be work nobody asked for.
  const manifestYaml = useQuery({
    queryKey: [...qk.builderProject(workspaceId, params.id), 'manifest-yaml', dirty],
    queryFn: () => builderApi.manifestYaml(params.id),
    enabled: showManifest,
  });

  const [yamlDraft, setYamlDraft] = React.useState('');
  React.useEffect(() => {
    if (manifestYaml.data !== undefined) setYamlDraft(manifestYaml.data);
  }, [manifestYaml.data]);

  const importManifest = useMutation({
    mutationFn: (document: string) => builderApi.importManifest(params.id, document),
    onSuccess: (project) => {
      // The editor state is replaced wholesale, so the local draft has to go
      // with it rather than being merged into something that no longer matches.
      setDraft(project.definition);
      setQuery({ tab: 'api', stream: null, section: null, result: null }, { replace: true });
      setDirty(false);
      setTestResult(null);
      queryClient.invalidateQueries({ queryKey: qk.builderProject(workspaceId, params.id) });
      toastSuccess(t('builder.imported'));
    },
    onError: (caught) => toastError(caught),
  });

  if (error) {
    return (
      <div className="px-4 py-6 sm:px-6 xl:px-8">
        <ErrorState
          title={(error as ApiError).status === 404
            ? t('builder.notFound') : t('common.errorTitle')}
          message={(error as Error).message}
          onRetry={() => refetch()}
        />
      </div>
    );
  }

  if (isLoading || !data || !draft) {
    return (
      <div className="space-y-3 px-4 py-6 sm:px-6 xl:px-8">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-[420px] w-full" />
      </div>
    );
  }

  const stream = draft.streams[activeStream];
  const needsApiKey = draft.auth.method === 'api_key' || draft.auth.method === 'bearer';
  const needsBasic = draft.auth.method === 'basic';

  return (
    <div className="flex h-full flex-col">
      <DetailHeader
        backHref="/builder"
        backLabel={t('builder.title')}
        title={data.name}
        subtitle={<span className="font-mono text-tiny">{data.connector_key}</span>}
        badgesInline
        badges={
          <>
            {data.status === 'PUBLISHED' ? (
              <Badge variant="success" size="xs">
                {t('builder.statusPublished', { v: String(data.published_version) })}
              </Badge>
            ) : (
              <Badge variant="subtle" size="xs">{t('builder.statusDraft')}</Badge>
            )}
            {!dirty && data.last_test_ok === true && (
              <Badge variant="success" size="xs" dot>{t('builder.testStatusPassed')}</Badge>
            )}
            {!dirty && data.last_test_ok === false && (
              <Badge variant="danger" size="xs" dot>{t('builder.testStatusFailed')}</Badge>
            )}
            {dirty && <Badge variant="warning" size="xs">{t('builder.unsaved')}</Badge>}
          </>
        }
        actions={
          canEdit ? (
            <div className="flex flex-wrap items-center gap-1.5">
              <Button size="sm" variant="ghost"
                      leadingIcon={<Code2 className="h-3.5 w-3.5" />}
                      onClick={() => setShowManifest((v) => !v)}>
                {t('builder.viewManifest')}
              </Button>
              <Button size="sm" variant="secondary" disabled={!dirty}
                      loading={save.isPending}
                      leadingIcon={<Save className="h-3.5 w-3.5" />}
                      onClick={() => save.mutate()}>
                {t('common.save')}
              </Button>
              <Button size="sm" variant="primary"
                      loading={publish.isPending}
                      disabled={!data.last_test_ok || dirty}
                      title={dirty
                        ? t('builder.publishNeedsCurrentTest')
                        : !data.last_test_ok ? t('builder.publishNeedsTest') : undefined}
                      leadingIcon={<Rocket className="h-3.5 w-3.5" />}
                      onClick={() => publish.mutate()}>
                {t('builder.publish')}
              </Button>
              <IconButton size="sm" variant="ghost"
                          aria-label={t('common.delete')} title={t('common.delete')}
                          disabled={remove.isPending}
                          onClick={() => {
                        if (window.confirm(t('builder.confirmDelete'))) remove.mutate();
                          }}>
                {remove.isPending ? (
                  <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" />
                ) : (
                  <Trash2 className="h-3.5 w-3.5" />
                )}
              </IconButton>
            </div>
          ) : null
        }
      />

      <div className="builder-workspace min-h-0 flex-1 overflow-auto px-4 py-4 sm:px-5 xl:px-6">
        {showManifest && (
          <section className="mb-4 rounded-lg border border-[rgb(var(--border-line))] bg-surface-1">
            <header className="flex flex-wrap items-center justify-between gap-2 border-b border-[rgb(var(--border-line))] px-4 py-2.5">
              <div>
                <h2 className="text-caption font-strong text-text-primary">
                  {t('builder.manifestTitle')}
                </h2>
                <p className="text-tiny text-text-quaternary">{t('builder.manifestHint')}</p>
              </div>
              {canEdit && (
                <div className="flex items-center gap-1.5">
                  <Button size="xs" variant="secondary"
                          loading={importManifest.isPending}
                          disabled={!yamlDraft.trim()}
                          onClick={() => importManifest.mutate(yamlDraft)}>
                    {t('builder.importManifest')}
                  </Button>
                </div>
              )}
            </header>
            {/* Editable: a manifest you can only read is documentation, and the
                CDK has components this editor does not render yet. */}
            <textarea
              aria-label={t('builder.manifestTitle')}
              spellCheck={false}
              value={manifestYaml.isLoading ? t('common.loading') : yamlDraft}
              onChange={(event) => setYamlDraft(event.target.value)}
              readOnly={!canEdit}
              className="h-80 w-full resize-y bg-transparent px-4 py-3 font-mono text-tiny leading-relaxed text-text-secondary outline-none"
            />
          </section>
        )}

        <div className="builder-workspace-grid">
          {/* ── rail ─────────────────────────────────────────── */}
          <nav
            aria-label={t('builder.sectionsNav')}
            className="builder-rail max-h-[calc(100vh-9rem)] space-y-3 self-start overflow-y-auto rounded-lg border border-[rgb(var(--border-line))] bg-surface-1 p-2 shadow-linear-sm"
          >
            <div className="space-y-0.5">
              <RailItem
                active={view === 'api'}
                onSelect={() => setQuery({ tab: 'api', stream: null, section: null })}
                label={t('builder.sectionApi')}
                icon={<Settings2 className="h-3.5 w-3.5" aria-hidden />}
              />
              <RailItem
                active={view === 'inputs'}
                onSelect={() => setQuery({ tab: 'inputs', stream: null, section: null })}
                label={t('builder.sectionInputs')}
                count={(draft.user_inputs ?? []).length}
                icon={<Variable className="h-3.5 w-3.5" aria-hidden />}
              />
            </div>

            <div className="space-y-0.5">
              <div className="flex items-center justify-between gap-2 px-2 pt-1">
                <span className="text-label font-emphasis text-text-quaternary">
                  {t('builder.sectionStreams')} ({draft.streams.length})
                </span>
                {canEdit && (
                  <IconButton
                    size="xs"
                    variant="ghost"
                    aria-label={t('builder.addStream')}
                    title={t('builder.addStream')}
                    onClick={() => {
                      setQuery({ tab: 'stream', stream: String(activeStream), section: 'request' });
                      setAdding(true);
                    }}
                    className="text-text-tertiary"
                  >
                    <Plus className="h-3.5 w-3.5" />
                  </IconButton>
                )}
              </div>
              {streamOutline(draft.streams).map(({ index, depth }) => (
                <RailItem
                  key={index}
                  active={view === 'stream' && index === activeStream}
                  onSelect={() => {
                    if (index !== activeStream) setTestResult(null);
                    setQuery({
                      tab: 'stream', stream: String(index), section: 'request', result: 'records',
                    });
                  }}
                  label={draft.streams[index].name || t('builder.unnamedStream')}
                  depth={depth}
                  warn={streamWarning(draft.streams[index])}
                  method={draft.streams[index].http_method}
                  subtitle={draft.streams[index].path || '/'}
                  icon={<ListTree className="h-3.5 w-3.5" aria-hidden />}
                />
              ))}
            </div>
          </nav>

          {/* ── panel ───────────────────────────────────────── */}
          <div className="min-w-0 space-y-4">
            {view === 'api' && (
            <Section title={t('builder.sectionApi')}>
              <Field label={t('builder.baseUrl')} htmlFor="base-url" required>
                <JinjaInput
                  id="base-url"
                  value={draft.base_url}
                  disabled={!canEdit}
                  userInputs={draft.user_inputs ?? []}
                  onChange={(value) => patch({ base_url: value })}
                  placeholder="https://api.example.com"
                />
              </Field>

              <div className="grid gap-3 sm:grid-cols-2">
                <Field label={t('builder.authMethod')} htmlFor="auth-method">
                  <Select
                    id="auth-method"
                    size="sm"
                    value={draft.auth.method}
                    disabled={!canEdit}
                    onChange={(event) => patch({
                      auth: { ...draft.auth, method: event.target.value as never },
                    })}
                  >
                    <option value="none">{t('builder.authNone')}</option>
                    <option value="api_key">{t('builder.authApiKey')}</option>
                    <option value="bearer">{t('builder.authBearer')}</option>
                    <option value="basic">{t('builder.authBasic')}</option>
                    <option value="oauth2">{t('builder.authOAuth')}</option>
                    <option value="jwt">{t('builder.authJwt')}</option>
                    <option value="session_token">{t('builder.authSession')}</option>
                  </Select>
                </Field>

                {draft.auth.method === 'api_key' && (
                  <Field label={t('builder.authHeader')} htmlFor="auth-header" required>
                    <Input
                      id="auth-header"
                      size="sm"
                      value={draft.auth.header ?? ''}
                      disabled={!canEdit}
                      onChange={(event) => patch({
                        auth: { ...draft.auth, header: event.target.value },
                      })}
                      placeholder="X-API-Key"
                    />
                  </Field>
                )}
              </div>

              {draft.auth.method === 'oauth2' && (
                <div className="grid gap-3 sm:grid-cols-2">
                  <Field label={t('builder.oauthTokenUrl')} htmlFor="oauth-url" required>
                    <Input id="oauth-url" size="sm" disabled={!canEdit}
                           value={draft.auth.oauth?.token_url ?? ''}
                           onChange={(e) => patch({
                             auth: { ...draft.auth,
                                     oauth: { ...draft.auth.oauth, token_url: e.target.value } },
                           })}
                           placeholder="https://api.example.com/oauth/token" />
                  </Field>
                  <Field label={t('builder.oauthScopes')} htmlFor="oauth-scopes"
                         hint={t('builder.oauthScopesHint')}>
                    <Input id="oauth-scopes" size="sm" disabled={!canEdit}
                           value={draft.auth.oauth?.scopes ?? ''}
                           onChange={(e) => patch({
                             auth: { ...draft.auth,
                                     oauth: { ...draft.auth.oauth, scopes: e.target.value } },
                           })}
                           placeholder="read, write" />
                  </Field>
                </div>
              )}

              {draft.auth.method === 'session_token' && (
                <div className="grid gap-3 sm:grid-cols-3">
                  <Field label={t('builder.sessionLoginPath')} htmlFor="session-path" required>
                    <Input id="session-path" size="sm" disabled={!canEdit}
                           value={draft.auth.session?.login_path ?? ''}
                           onChange={(e) => patch({
                             auth: { ...draft.auth,
                                     session: { ...draft.auth.session, login_path: e.target.value } },
                           })}
                           placeholder="/auth/login" />
                  </Field>
                  <Field label={t('builder.sessionTokenPath')} htmlFor="session-token" required>
                    <Input id="session-token" size="sm" disabled={!canEdit}
                           value={draft.auth.session?.token_path ?? ''}
                           onChange={(e) => patch({
                             auth: { ...draft.auth,
                                     session: { ...draft.auth.session, token_path: e.target.value } },
                           })}
                           placeholder="data.token" />
                  </Field>
                  <Field label={t('builder.sessionHeader')} htmlFor="session-header">
                    <Input id="session-header" size="sm" disabled={!canEdit}
                           value={draft.auth.session?.header ?? ''}
                           onChange={(e) => patch({
                             auth: { ...draft.auth,
                                     session: { ...draft.auth.session, header: e.target.value } },
                           })}
                           placeholder="X-Session-Token" />
                  </Field>
                </div>
              )}
            </Section>
            )}

            {view === 'inputs' && (
            <Section
              title={t('builder.sectionInputs')}
              action={canEdit ? (
                <Button size="xs" variant="ghost" leadingIcon={<Plus className="h-3 w-3" />}
                        onClick={() => patch({
                          user_inputs: [...(draft.user_inputs ?? []),
                            { key: '', title: '', type: 'string' }],
                        })}>
                  {t('builder.addInput')}
                </Button>
              ) : null}
            >
              <p className="text-tiny text-text-quaternary">{t('builder.inputsHint')}</p>
              {(draft.user_inputs ?? []).length === 0 ? (
                <p className="text-caption text-text-quaternary">{t('builder.noInputs')}</p>
              ) : (
                <div className="divide-y divide-[rgb(var(--border-line))]">
                  {(draft.user_inputs ?? []).map((field, index) => (
                    <div key={index} className="space-y-3 py-3 first:pt-0 last:pb-0">
                      <div className="grid items-end gap-3 sm:grid-cols-2">
                        <Field label={t('builder.inputKey')}
                               htmlFor={`builder-input-${index}-key`} required>
                          <Input id={`builder-input-${index}-key`} size="sm"
                                 placeholder="access_token" disabled={!canEdit} value={field.key}
                                 onChange={(e) => patch({
                                   user_inputs: (draft.user_inputs ?? []).map((row, i) =>
                                     i === index ? { ...row, key: e.target.value } : row),
                                 })} />
                        </Field>
                        <Field label={t('builder.inputTitle')}
                               htmlFor={`builder-input-${index}-title`} required>
                          <Input id={`builder-input-${index}-title`} size="sm"
                                 placeholder={t('builder.inputTitle')}
                                 disabled={!canEdit} value={field.title ?? ''}
                                 onChange={(e) => patch({
                                   user_inputs: (draft.user_inputs ?? []).map((row, i) =>
                                     i === index ? { ...row, title: e.target.value } : row),
                                 })} />
                        </Field>
                        <Field label={t('builder.inputType')}
                               htmlFor={`builder-input-${index}-type`}>
                          <Select id={`builder-input-${index}-type`} size="sm"
                                  disabled={!canEdit} value={field.type ?? 'string'}
                                  onChange={(e) => patch({
                                    user_inputs: (draft.user_inputs ?? []).map((row, i) =>
                                      i === index ? { ...row, type: e.target.value as never } : row),
                                  })}>
                            <option value="string">string</option>
                            <option value="integer">integer</option>
                            <option value="number">number</option>
                            <option value="boolean">boolean</option>
                          </Select>
                        </Field>
                        <IconButton size="xs" variant="ghost" disabled={!canEdit}
                                    className="justify-self-end"
                                    aria-label={t('builder.removeInput')}
                                    title={t('builder.removeInput')}
                                    onClick={() => patch({
                                  user_inputs: (draft.user_inputs ?? [])
                                    .filter((_, i) => i !== index),
                                    })}>
                          <Trash2 className="h-3.5 w-3.5" />
                        </IconButton>
                      </div>
                      <div className="grid gap-3 sm:grid-cols-2">
                        <Field label={t('builder.inputDescription')}
                               htmlFor={`builder-input-${index}-description`}>
                          <Input id={`builder-input-${index}-description`} size="sm"
                                 disabled={!canEdit} value={field.description ?? ''}
                                 onChange={(e) => patch({
                                   user_inputs: (draft.user_inputs ?? []).map((row, i) =>
                                     i === index ? { ...row, description: e.target.value } : row),
                                 })} />
                        </Field>
                        {!field.secret && (
                          <Field label={t('builder.inputDefault')}
                                 htmlFor={`builder-input-${index}-default`}>
                            <Input id={`builder-input-${index}-default`} size="sm"
                                   disabled={!canEdit} value={field.default ?? ''}
                                   onChange={(e) => patch({
                                     user_inputs: (draft.user_inputs ?? []).map((row, i) =>
                                       i === index ? { ...row, default: e.target.value } : row),
                                   })} />
                          </Field>
                        )}
                      </div>
                      <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
                        <label className="flex items-center gap-1.5 text-caption text-text-secondary">
                          <input type="checkbox" disabled={!canEdit}
                                 checked={Boolean(field.required)}
                                 onChange={(e) => patch({
                                   user_inputs: (draft.user_inputs ?? []).map((row, i) =>
                                     i === index ? { ...row, required: e.target.checked } : row),
                                 })}
                                 className="h-3.5 w-3.5 rounded border-[rgb(var(--border-strong))]" />
                          {t('builder.inputRequired')}
                        </label>
                        <label className="flex items-center gap-1.5 text-caption text-text-secondary">
                          <input type="checkbox" disabled={!canEdit}
                                 checked={Boolean(field.secret)}
                                 onChange={(e) => patch({
                                   user_inputs: (draft.user_inputs ?? []).map((row, i) =>
                                     i === index ? {
                                       ...row, secret: e.target.checked,
                                       default: e.target.checked ? undefined : row.default,
                                     } : row),
                                 })}
                                 className="h-3.5 w-3.5 rounded border-[rgb(var(--border-strong))]" />
                          {t('builder.inputSecret')}
                        </label>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </Section>
            )}

            {view === 'stream' && (
            <Section
              title={stream ? (
                <span className="flex min-w-0 items-center gap-2">
                  <span className="truncate">{stream.name || t('builder.unnamedStream')}</span>
                  <Badge
                    variant={stream.http_method === 'POST' ? 'info' : 'outline'}
                    size="xs"
                    pill={false}
                    className="font-mono"
                  >
                    {stream.http_method}
                  </Badge>
                </span>
              ) : t('builder.sectionStreams')}
              action={canEdit ? (
                <Button
                  size="xs"
                  variant="ghost"
                  leadingIcon={<Plus className="h-3 w-3" />}
                  onClick={() => setAdding((open) => !open)}
                >
                  {t('builder.addStream')}
                </Button>
              ) : null}
            >
              {/* Ask for the name and the path up front.
                *
                * "Add stream" used to make `stream_3` at path `/` and drop the
                * person into it -- a stream that cannot work, named after
                * nothing, which they then have to notice and repair. Both
                * answers are one line each and the person already knows them. */}
              {adding && canEdit && (
                <div className="flex flex-wrap items-end gap-3 rounded-md border
                                border-[rgb(var(--border-line))] p-3">
                  <Field label={t('builder.streamName')} htmlFor="new-stream-name" required>
                    <Input
                      id="new-stream-name" size="sm" value={newStream.name}
                      placeholder="lead_service"
                      onChange={(e) => setNewStream((s) => ({ ...s, name: e.target.value }))}
                    />
                  </Field>
                  <Field label={t('builder.streamPath')} htmlFor="new-stream-path" required
                         hint={t('builder.streamPathHint')}>
                    <Input
                      id="new-stream-path" size="sm" value={newStream.path}
                      placeholder="/lead/services"
                      onChange={(e) => setNewStream((s) => ({ ...s, path: e.target.value }))}
                    />
                  </Field>
                  <Button
                    size="sm"
                    disabled={!newStream.name.trim() || !newStream.path.trim()
                      || draft.streams.some((s) => s.name === newStream.name.trim())}
                    onClick={() => {
                      const next = [...draft.streams,
                        EMPTY_STREAM(newStream.name.trim(), newStream.path.trim())];
                      patch({ streams: next });
                      setTestResult(null);
                      setQuery({
                        tab: 'stream', stream: String(next.length - 1), section: 'request',
                      });
                      setNewStream({ name: '', path: '' });
                      setAdding(false);
                    }}
                  >
                    {t('builder.addStreamConfirm')}
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setAdding(false)}>
                    {t('common.cancel')}
                  </Button>
                </div>
              )}

              {stream && (
                <StreamEditor
                  stream={stream}
                  streamNames={draft.streams.map((item) => item.name)}
                  fields={testResult?.inferred_fields ?? []}
                  userInputs={draft.user_inputs ?? []}
                  activeSection={streamSection}
                  onSectionChange={(section) => setQuery({ section }, { replace: true })}
                  onCreateInput={canEdit ? () => {
                    // Jump to the inputs section with a blank row waiting, so
                    // "the input I want does not exist yet" is one click rather
                    // than a hunt back up the page.
                    patch({
                      user_inputs: [...(draft.user_inputs ?? []),
                        { key: '', title: '', type: 'string' }],
                    });
                    setQuery({ tab: 'inputs', stream: null, section: null });
                  } : undefined}
                  disabled={!canEdit}
                  onChange={(next) => patchStream(activeStream, next)}
                />
              )}

              {draft.streams.length > 1 && canEdit && (
                <Button
                  size="xs"
                  variant="ghost"
                  leadingIcon={<Trash2 className="h-3 w-3" />}
                  onClick={() => {
                    const next = draft.streams.filter((_, i) => i !== activeStream);
                    patch({ streams: next });
                    setTestResult(null);
                    setQuery({
                      tab: 'stream', stream: String(Math.max(0, activeStream - 1)),
                      section: 'request',
                    });
                  }}
                >
                  {t('builder.removeStream')}
                </Button>
              )}
            </Section>
            )}
          </div>

          {/* ── test panel ─────────────────────────────────────────── */}
          <aside className="builder-test-panel space-y-3 self-start">
            <Section
              title={t('builder.sectionTest')}
              action={stream ? (
                <span className="flex min-w-0 items-center gap-1.5">
                  <Badge
                    variant={stream.http_method === 'POST' ? 'info' : 'outline'}
                    size="xs"
                    pill={false}
                    className="font-mono"
                  >
                    {stream.http_method}
                  </Badge>
                  <Badge variant="subtle" size="xs" className="max-w-32 truncate">
                    {stream.name}
                  </Badge>
                </span>
              ) : null}
            >
              {(configuredTestInputs.length > 0 || needsApiKey || needsBasic) && (
                <div className="space-y-2.5 border-b border-[rgb(var(--border-line))] pb-3">
                  <p className="text-tiny text-text-quaternary">{t('builder.secretsHint')}</p>
                  {configuredTestInputs.map((field, index) => (
                    <Field
                      key={`${field.key}-${index}`}
                      label={field.title || field.key}
                      htmlFor={`test-config-${index}`}
                      required={field.required}
                      hint={field.description}
                    >
                      {field.type === 'boolean' ? (
                        <Select
                          id={`test-config-${index}`}
                          size="sm"
                          value={secrets[field.key] ?? field.default ?? 'false'}
                          onChange={(event) => setSecrets({
                            ...secrets, [field.key]: event.target.value,
                          })}
                        >
                          <option value="true">true</option>
                          <option value="false">false</option>
                        </Select>
                      ) : field.secret ? (
                        <SecretInput
                          id={`test-config-${index}`}
                          size="sm"
                          revealLabel={field.title || field.key}
                          value={secrets[field.key] ?? ''}
                          placeholder={field.default ?? ''}
                          autoComplete="off"
                          onChange={(event) => setSecrets({
                            ...secrets, [field.key]: event.target.value,
                          })}
                        />
                      ) : (
                        <Input
                          id={`test-config-${index}`}
                          size="sm"
                          type={['integer', 'number'].includes(field.type ?? '') ? 'number' : 'text'}
                          value={secrets[field.key] ?? ''}
                          placeholder={field.default ?? ''}
                          autoComplete="off"
                          onChange={(event) => setSecrets({
                            ...secrets, [field.key]: event.target.value,
                          })}
                        />
                      )}
                    </Field>
                  ))}
                  {needsApiKey && !configuredTestInputs.some((field) => field.key === 'api_key') && (
                    <Field label={t('builder.apiKey')} htmlFor="test-api-key">
                      <SecretInput id="test-api-key" size="sm" autoComplete="off"
                                   revealLabel={t('builder.apiKey')}
                                   value={secrets.api_key ?? ''}
                                   onChange={(e) => setSecrets({ ...secrets, api_key: e.target.value })} />
                    </Field>
                  )}
                  {needsBasic && (
                    <div className="grid gap-2 sm:grid-cols-2">
                      {!configuredTestInputs.some((field) => field.key === 'username') && (
                        <Field label={t('builder.username')} htmlFor="test-username">
                          <Input id="test-username" size="sm" autoComplete="off"
                                 value={secrets.username ?? ''}
                                 onChange={(e) => setSecrets({ ...secrets, username: e.target.value })} />
                        </Field>
                      )}
                      {!configuredTestInputs.some((field) => field.key === 'password') && (
                        <Field label={t('builder.password')} htmlFor="test-password">
                          <SecretInput id="test-password" size="sm" autoComplete="off"
                                       revealLabel={t('builder.password')}
                                       value={secrets.password ?? ''}
                                       onChange={(e) => setSecrets({ ...secrets, password: e.target.value })} />
                        </Field>
                      )}
                    </div>
                  )}
                </div>
              )}

              <Button
                className="w-full"
                variant="primary"
                loading={runTest.isPending}
                disabled={!stream || missingRequiredTestInput}
                title={missingRequiredTestInput ? t('builder.testMissingRequired') : undefined}
                leadingIcon={<Play className="h-3.5 w-3.5" />}
                onClick={() => runTest.mutate()}
              >
                {t('builder.runTestStream')}
              </Button>

              {runTest.isPending ? (
                <div className="space-y-2 py-2">
                  <Skeleton className="h-4 w-40" />
                  <Skeleton className="h-24 w-full" />
                  <p className="text-tiny text-text-quaternary">{t('builder.testRunning')}</p>
                </div>
              ) : testResult ? (
                <TestOutcome
                  result={testResult}
                  view={testView}
                  onViewChange={(next) => setQuery({ result: next }, { replace: true })}
                  onDismiss={() => setTestResult(null)}
                  t={t}
                  onApplySchema={canEdit && testResult.inferred_schema
                    ? () => {
                        patchStream(activeStream, { schema: testResult.inferred_schema! });
                        toastSuccess(t('builder.schemaApplied'));
                      }
                    : undefined}
                />
              ) : (
                <div className="rounded-md bg-surface-2 px-4 py-6 text-center">
                  <span className="mx-auto mb-2 flex h-8 w-8 items-center justify-center rounded-full bg-surface-1 text-text-tertiary shadow-linear-sm">
                    <Play className="h-3.5 w-3.5" aria-hidden />
                  </span>
                  <p className="text-caption font-emphasis text-text-secondary">
                    {t('builder.testIdleTitle')}
                  </p>
                  <p className="mt-1 text-tiny text-text-quaternary">
                    {t('builder.testIdle')}
                  </p>
                </div>
              )}
            </Section>
          </aside>
        </div>
      </div>
    </div>
  );
}

// ── pieces ─────────────────────────────────────────────────────────────────

function Section({
  title, action, children,
}: {
  title: React.ReactNode;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-lg border border-[rgb(var(--border-line))] bg-surface-1">
      <header className="flex items-center justify-between gap-2 border-b border-[rgb(var(--border-line))] px-4 py-2.5">
        <h2 className="min-w-0 flex-1 text-caption font-strong text-text-primary">{title}</h2>
        {action && <div className="shrink-0">{action}</div>}
      </header>
      <div className="min-w-0 space-y-3 p-4">{children}</div>
    </section>
  );
}

function SecretInput({
  revealLabel, className, ...props
}: React.ComponentProps<typeof Input> & { revealLabel: string }) {
  const { t } = useI18n();
  const [revealed, setRevealed] = React.useState(false);
  return (
    <div className="relative">
      <Input {...props} type={revealed ? 'text' : 'password'} className={cn('pr-9', className)} />
      <IconButton
        size="xs"
        variant="ghost"
        className="absolute right-0.5 top-1/2 -translate-y-1/2 text-text-tertiary"
        aria-label={`${revealed ? t('common.hide') : t('common.show')}: ${revealLabel}`}
        title={revealed ? t('common.hide') : t('common.show')}
        onMouseDown={(event) => event.preventDefault()}
        onClick={() => setRevealed((current) => !current)}
      >
        {revealed ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
      </IconButton>
    </div>
  );
}

/** 2xx is fine, 4xx/5xx is not, and an unknown status must not look like success. */
function statusTone(status: number | null): 'success' | 'danger' | 'subtle' {
  if (status === null || status === undefined) return 'subtle';
  if (status >= 200 && status < 300) return 'success';
  return 'danger';
}

function TestOutcome({
  result, view, onViewChange, onDismiss, t, onApplySchema,
}: {
  result: BuilderTestResult;
  view: TestView;
  onViewChange: (view: TestView) => void;
  onDismiss: () => void;
  t: (key: string, vars?: Record<string, string>) => string;
  onApplySchema?: () => void;
}) {
  const columns = React.useMemo(() => {
    const keys: string[] = [];
    for (const record of result.records) {
      for (const key of Object.keys(record)) if (!keys.includes(key)) keys.push(key);
    }
    return keys.slice(0, 6);
  }, [result.records]);
  const schemaProperties = (result.inferred_schema?.properties ?? {}) as Record<
    string, Record<string, unknown>
  >;

  return (
    <div className="space-y-3">
      {result.ok ? (
        <div className="rounded-md border border-success/30 bg-success/5 p-2.5">
          <div className="flex items-start gap-2">
            <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-success" aria-hidden />
            <p className="min-w-0 flex-1 text-caption font-emphasis text-success">
              {result.record_preview_supported
                ? t('builder.testOk', { n: String(result.record_count) })
                : t('builder.testOkNoPreview')}
            </p>
            <IconButton size="xs" variant="ghost" aria-label={t('common.close')}
                        title={t('common.close')} onClick={onDismiss}
                        className="-mr-1 -mt-1 text-text-tertiary">
              <X className="h-3.5 w-3.5" />
            </IconButton>
          </div>
          {!result.record_preview_supported && (
            <p className="mt-1 pl-6 text-tiny text-text-tertiary">
              {t('builder.noPreviewHint')}
            </p>
          )}
        </div>
      ) : (
        <div className="rounded-md border border-danger/30 bg-danger/5 p-2.5">
          <div className="flex items-start gap-1.5 text-caption text-danger">
            <AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0" />
            <span className="min-w-0 flex-1">{result.error?.summary ?? t('builder.testFailed')}</span>
            <IconButton size="xs" variant="ghost" aria-label={t('common.close')}
                        title={t('common.close')} onClick={onDismiss}
                        className="-mr-1 -mt-1 text-text-tertiary">
              <X className="h-3.5 w-3.5" />
            </IconButton>
          </div>
          {result.error?.technical_message && (
            <p className="mt-1.5 break-words font-mono text-tiny text-text-tertiary">
              {result.error.technical_message.slice(0, 400)}
            </p>
          )}
        </div>
      )}

      {onApplySchema && result.inferred_schema && (
        <div className="flex items-center justify-between gap-2 rounded-md border border-[rgb(var(--border-line))] bg-surface-0 px-2.5 py-2">
          <p className="text-tiny text-text-tertiary">
            {t('builder.schemaDetected', {
              n: String(Object.keys(
                (result.inferred_schema.properties ?? {}) as Record<string, unknown>,
              ).length),
            })}
          </p>
          <Button size="xs" variant="secondary" onClick={onApplySchema}>
            {t('builder.applySchema')}
          </Button>
        </div>
      )}

      <Tabs
        value={view}
        onChange={(next) => onViewChange(next as TestView)}
        items={[
          { id: 'records', label: t('builder.resultRecords'), count: result.records.length },
          { id: 'schema', label: t('builder.resultSchema'), count: Object.keys(schemaProperties).length },
          { id: 'requests', label: t('builder.resultRequests'), count: result.requests.length },
          { id: 'logs', label: t('builder.resultLogs'), count: result.logs.length },
        ]}
      />

      {view === 'schema' && (
        Object.keys(schemaProperties).length > 0 ? (
          <div className="max-h-72 overflow-auto rounded-md border border-[rgb(var(--border-line))]">
            <table className="w-full text-left">
              <thead className="sticky top-0 bg-surface-2 text-tiny text-text-quaternary">
                <tr>
                  <th className="px-2.5 py-1.5 font-emphasis">{t('builder.schemaField')}</th>
                  <th className="px-2.5 py-1.5 font-emphasis">{t('builder.schemaType')}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[rgb(var(--border-line))]">
                {Object.entries(schemaProperties).map(([name, definition]) => (
                  <tr key={name}>
                    <td className="max-w-[12rem] truncate px-2.5 py-1.5 font-mono text-tiny text-text-secondary"
                        title={name}>{name}</td>
                    <td className="px-2.5 py-1.5 text-tiny text-text-tertiary">
                      {Array.isArray(definition.type)
                        ? definition.type.join(' | ')
                        : String(definition.type ?? 'unknown')}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <ResultEmpty text={t('builder.resultNoSchema')} />
      )}

      {view === 'requests' && result.requests.length > 0 && (
        <div className="space-y-1.5">
          {result.requests.map((exchange, index) => (
            <details key={index} className="rounded-md border border-[rgb(var(--border-line))]">
              <summary className="flex cursor-pointer items-center gap-1.5 px-2.5 py-1.5">
                <Badge
                  variant={statusTone(exchange.status)}
                  size="xs"
                >
                  {exchange.status ?? '—'}
                </Badge>
                <span className="min-w-0 flex-1 truncate font-mono text-tiny text-text-tertiary"
                      title={exchange.url ?? ''}>
                  {exchange.url ?? '—'}
                </span>
              </summary>
              <pre className="max-h-40 overflow-auto border-t border-[rgb(var(--border-line))] px-2.5 py-2 font-mono text-tiny leading-relaxed text-text-quaternary">
                {exchange.body || t('builder.noBody')}
              </pre>
            </details>
          ))}
        </div>
      )}
      {view === 'requests' && result.requests.length === 0 && (
        <ResultEmpty text={t('builder.resultNoRequests')} />
      )}

      {view === 'records' && result.records.length > 0 && (
        <div className="overflow-hidden rounded-md border border-[rgb(var(--border-line))]">
          <div className="overflow-x-auto">
            <table className="w-full text-left">
              <caption className="border-b border-[rgb(var(--border-line))] bg-surface-2 px-2 py-1 text-left text-tiny text-text-quaternary">
                {t('builder.previewCaption', {
                  shown: String(Math.min(result.records.length, 8)),
                  total: String(result.record_count),
                })}
              </caption>
              <thead>
                <tr className="border-b border-[rgb(var(--border-line))] bg-surface-2 text-tiny uppercase text-text-quaternary">
                  {columns.map((column) => (
                    <th key={column} scope="col" className="whitespace-nowrap px-2 py-1.5 font-emphasis">
                      {column}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-[rgb(var(--border-line))]">
                {result.records.slice(0, 8).map((record, index) => (
                  <tr key={index}>
                    {columns.map((column) => (
                      <td key={column}
                          className="max-w-[12rem] truncate px-2 py-1.5 text-tiny text-text-secondary"
                          title={String(record[column] ?? '')}>
                        {typeof record[column] === 'object'
                          ? JSON.stringify(record[column])
                          : String(record[column] ?? '—')}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
      {view === 'records' && result.records.length === 0 && (
        <ResultEmpty text={result.record_preview_supported
          ? t('builder.resultNoRecords') : t('builder.noPreviewHint')} />
      )}

      {view === 'logs' && result.logs.length > 0 && (
          <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-md bg-surface-2 px-2.5 py-2 font-mono text-tiny leading-relaxed text-text-tertiary">
            {result.logs.join('\n')}
          </pre>
      )}
      {view === 'logs' && result.logs.length === 0 && (
        <ResultEmpty text={t('builder.resultNoLogs')} />
      )}
    </div>
  );
}

function ResultEmpty({ text }: { text: string }) {
  return (
    <p className="rounded-md border border-dashed border-[rgb(var(--border-strong))] px-3 py-5 text-center text-tiny text-text-quaternary">
      {text}
    </p>
  );
}
