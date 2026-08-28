'use client';

import * as React from 'react';
import { useParams, useRouter } from 'next/navigation';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle, CheckCircle2, Code2, Play, Plus, Rocket, Save, Trash2,
} from 'lucide-react';

import { ApiError, builderApi } from '@/lib/api';
import { qk } from '@/lib/queryKeys';
import { useWorkspaceId } from '@/hooks/use-current-user';
import { usePermissions } from '@/hooks/use-permissions';
import { toastError, toastSuccess } from '@/hooks/use-toast';
import { useI18n } from '@/providers/LanguageProvider';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Input, Select } from '@/components/ui/Input';
import { ErrorState, Skeleton } from '@/components/ui/Feedback';
import { DetailHeader } from '@/components/layout/PageLayout';
import type {
  BuilderDefinition, BuilderKeyValue, BuilderStream, BuilderTestResult,
} from '@/lib/types';
import { Field, JinjaInput } from '@/components/builder/BuilderField';
import { StreamEditor } from '@/components/builder/StreamEditor';
import { cn } from '@/lib/utils';

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
  label, active, onSelect, count, depth = 0, warn = false,
}: {
  label: string;
  active: boolean;
  onSelect: () => void;
  count?: number;
  depth?: number;
  warn?: boolean;
}) {
  return (
    <button
      type="button"
      aria-current={active ? 'true' : undefined}
      onClick={onSelect}
      style={{ paddingInlineStart: `${8 + depth * 12}px` }}
      className={cn(
        'flex w-full items-center gap-1.5 rounded-md py-1.5 pe-2 text-left',
        'text-caption transition-colors',
        active
          ? 'bg-brand/10 text-brand font-emphasis'
          : 'text-text-secondary hover:bg-surface-2 hover:text-text-primary',
      )}
    >
      {depth > 0 && <span aria-hidden className="text-text-quaternary">└</span>}
      <span className="truncate">{label}</span>
      {typeof count === 'number' && (
        <span className="ms-auto text-tiny text-text-quaternary">{count}</span>
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

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: qk.builderProject(workspaceId, params.id),
    queryFn: () => builderApi.detail(params.id),
  });

  const [draft, setDraft] = React.useState<BuilderDefinition | null>(null);
  const [activeStream, setActiveStream] = React.useState(0);
  // One section on screen at a time, chosen from the rail.
  //
  // Everything used to be a single column: global config, user inputs and every
  // stream stacked into one 2,400px scroll for a connector with *one* stream.
  // Finding the retry settings meant scrolling past the whole form, and the
  // stream being edited was wherever the page happened to be scrolled to.
  const [view, setView] = React.useState<'api' | 'inputs' | 'stream'>('api');
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
        config: secrets,
      });
    },
    onSuccess: (result) => {
      setTestResult(result);
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
      setActiveStream(0);
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
        badges={
          <>
            {data.status === 'PUBLISHED' ? (
              <Badge variant="success" size="xs">
                {t('builder.statusPublished', { v: String(data.published_version) })}
              </Badge>
            ) : (
              <Badge variant="subtle" size="xs">{t('builder.statusDraft')}</Badge>
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
                      loading={runTest.isPending}
                      leadingIcon={<Play className="h-3.5 w-3.5" />}
                      onClick={() => runTest.mutate()}>
                {t('builder.runTest')}
              </Button>
              <Button size="sm" variant="secondary"
                      loading={publish.isPending}
                      disabled={!data.last_test_ok}
                      title={!data.last_test_ok ? t('builder.publishNeedsTest') : undefined}
                      leadingIcon={<Rocket className="h-3.5 w-3.5" />}
                      onClick={() => publish.mutate()}>
                {t('builder.publish')}
              </Button>
              <Button size="sm" variant="ghost"
                      aria-label={t('common.delete')} title={t('common.delete')}
                      loading={remove.isPending}
                      leadingIcon={<Trash2 className="h-3.5 w-3.5" />}
                      onClick={() => {
                        if (window.confirm(t('builder.confirmDelete'))) remove.mutate();
                      }} />
            </div>
          ) : null
        }
      />

      <div className="min-h-0 flex-1 overflow-auto px-4 py-4 sm:px-6 xl:px-8">
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

        <div className="grid gap-4 lg:grid-cols-[13rem_minmax(0,1fr)]
                        xl:grid-cols-[13rem_minmax(0,1fr)_minmax(0,24rem)]">
          {/* ── rail ─────────────────────────────────────────── */}
          <nav aria-label={t('builder.sectionsNav')} className="space-y-3 self-start">
            <div className="space-y-0.5">
              <RailItem
                active={view === 'api'}
                onSelect={() => setView('api')}
                label={t('builder.sectionApi')}
              />
              <RailItem
                active={view === 'inputs'}
                onSelect={() => setView('inputs')}
                label={t('builder.sectionInputs')}
                count={(draft.user_inputs ?? []).length}
              />
            </div>

            <div className="space-y-0.5">
              <div className="flex items-center justify-between gap-2 px-2 pt-1">
                <span className="text-tiny font-emphasis uppercase tracking-wide
                                 text-text-quaternary">
                  {t('builder.sectionStreams')} ({draft.streams.length})
                </span>
                {canEdit && (
                  <button
                    type="button"
                    aria-label={t('builder.addStream')}
                    title={t('builder.addStream')}
                    onClick={() => { setView('stream'); setAdding(true); }}
                    className="rounded p-0.5 text-text-tertiary hover:bg-surface-2
                               hover:text-text-primary"
                  >
                    <Plus className="h-3.5 w-3.5" />
                  </button>
                )}
              </div>
              {streamOutline(draft.streams).map(({ index, depth }) => (
                <RailItem
                  key={index}
                  active={view === 'stream' && index === activeStream}
                  onSelect={() => { setView('stream'); setActiveStream(index); }}
                  label={draft.streams[index].name || t('builder.unnamedStream')}
                  depth={depth}
                  warn={streamWarning(draft.streams[index])}
                />
              ))}
            </div>
          </nav>

          {/* ── panel ───────────────────────────────────────── */}
          <div className="space-y-4">
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
                <div className="space-y-1.5">
                  {(draft.user_inputs ?? []).map((field, index) => (
                    <div key={index} className="flex flex-wrap gap-1.5">
                      <Input size="sm" className="w-40" placeholder="account_id"
                             aria-label={`${t('builder.inputKey')} ${index + 1}`}
                             disabled={!canEdit} value={field.key}
                             onChange={(e) => patch({
                               user_inputs: (draft.user_inputs ?? []).map((row, i) =>
                                 i === index ? { ...row, key: e.target.value } : row),
                             })} />
                      <Input size="sm" className="min-w-[8rem] flex-1"
                             placeholder={t('builder.inputTitle')}
                             aria-label={`${t('builder.inputTitle')} ${index + 1}`}
                             disabled={!canEdit} value={field.title ?? ''}
                             onChange={(e) => patch({
                               user_inputs: (draft.user_inputs ?? []).map((row, i) =>
                                 i === index ? { ...row, title: e.target.value } : row),
                             })} />
                      <Select size="sm" className="w-28" disabled={!canEdit}
                              aria-label={`${t('builder.inputType')} ${index + 1}`}
                              value={field.type ?? 'string'}
                              onChange={(e) => patch({
                                user_inputs: (draft.user_inputs ?? []).map((row, i) =>
                                  i === index ? { ...row, type: e.target.value as never } : row),
                              })}>
                        <option value="string">string</option>
                        <option value="integer">integer</option>
                        <option value="number">number</option>
                        <option value="boolean">boolean</option>
                      </Select>
                      <label className="flex items-center gap-1 text-tiny text-text-tertiary">
                        <input type="checkbox" disabled={!canEdit}
                               checked={Boolean(field.secret)}
                               onChange={(e) => patch({
                                 user_inputs: (draft.user_inputs ?? []).map((row, i) =>
                                   i === index ? { ...row, secret: e.target.checked } : row),
                               })}
                               className="h-3.5 w-3.5 rounded border-[rgb(var(--border-strong))]" />
                        {t('builder.inputSecret')}
                      </label>
                      <Button size="xs" variant="ghost" disabled={!canEdit}
                              aria-label={t('builder.removeInput')}
                              leadingIcon={<Trash2 className="h-3 w-3" />}
                              onClick={() => patch({
                                user_inputs: (draft.user_inputs ?? [])
                                  .filter((_, i) => i !== index),
                              })} />
                    </div>
                  ))}
                </div>
              )}
            </Section>
            )}

            {view === 'stream' && (
            <Section
              title={t('builder.sectionStreams')}
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
                      setActiveStream(next.length - 1);
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
                  onCreateInput={canEdit ? () => {
                    // Jump to the inputs section with a blank row waiting, so
                    // "the input I want does not exist yet" is one click rather
                    // than a hunt back up the page.
                    patch({
                      user_inputs: [...(draft.user_inputs ?? []),
                        { key: '', title: '', type: 'string' }],
                    });
                    setView('inputs');
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
                    setActiveStream(0);
                  }}
                >
                  {t('builder.removeStream')}
                </Button>
              )}
            </Section>
            )}
          </div>

          {/* ── test panel ─────────────────────────────────────────── */}
          <aside className="space-y-3">
            <Section title={t('builder.sectionTest')}>
              {(needsApiKey || needsBasic) && (
                <div className="space-y-2 rounded-md border border-[rgb(var(--border-line))] bg-surface-0 p-2.5">
                  <p className="text-tiny text-text-quaternary">{t('builder.secretsHint')}</p>
                  {needsApiKey && (
                    <Field label={t('builder.apiKey')} htmlFor="test-api-key">
                      <Input id="test-api-key" size="sm" type="password"
                             value={secrets.api_key ?? ''}
                             onChange={(e) => setSecrets({ ...secrets, api_key: e.target.value })} />
                    </Field>
                  )}
                  {needsBasic && (
                    <div className="grid gap-2 sm:grid-cols-2">
                      <Field label={t('builder.username')} htmlFor="test-username">
                        <Input id="test-username" size="sm" value={secrets.username ?? ''}
                               onChange={(e) => setSecrets({ ...secrets, username: e.target.value })} />
                      </Field>
                      <Field label={t('builder.password')} htmlFor="test-password">
                        <Input id="test-password" size="sm" type="password"
                               value={secrets.password ?? ''}
                               onChange={(e) => setSecrets({ ...secrets, password: e.target.value })} />
                      </Field>
                    </div>
                  )}
                </div>
              )}

              {runTest.isPending ? (
                <div className="space-y-2">
                  <Skeleton className="h-4 w-40" />
                  <Skeleton className="h-24 w-full" />
                  <p className="text-tiny text-text-quaternary">{t('builder.testRunning')}</p>
                </div>
              ) : testResult ? (
                <TestOutcome
                  result={testResult}
                  t={t}
                  onApplySchema={canEdit && testResult.inferred_schema
                    ? () => {
                        patchStream(activeStream, { schema: testResult.inferred_schema! });
                        toastSuccess(t('builder.schemaApplied'));
                      }
                    : undefined}
                />
              ) : (
                <p className="rounded-md border border-dashed border-[rgb(var(--border-strong))] px-3 py-6 text-center text-caption text-text-quaternary">
                  {t('builder.testIdle')}
                </p>
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
  title: string;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-lg border border-[rgb(var(--border-line))] bg-surface-1">
      <header className="flex items-center justify-between gap-2 border-b border-[rgb(var(--border-line))] px-4 py-2.5">
        <h2 className="text-caption font-strong text-text-primary">{title}</h2>
        {action}
      </header>
      <div className="space-y-3 p-4">{children}</div>
    </section>
  );
}


/**
 * A free-text field that becomes a picker once a test read has shown which
 * fields the API actually returns — typing a column name from memory is where
 * most builder mistakes come from.
 */
function FieldPicker({
  id, value, options, disabled, onChange,
}: {
  id: string;
  value: string;
  options: string[];
  disabled?: boolean;
  onChange: (value: string) => void;
}) {
  return (
    <>
      <Input
        id={id}
        size="sm"
        value={value}
        disabled={disabled}
        list={options.length ? `${id}-options` : undefined}
        onChange={(event) => onChange(event.target.value)}
      />
      {options.length > 0 && (
        <datalist id={`${id}-options`}>
          {options.map((option) => <option key={option} value={option} />)}
        </datalist>
      )}
    </>
  );
}

function KeyValueEditor({
  label, rows, disabled, onChange,
}: {
  label: string;
  rows: BuilderKeyValue[];
  disabled?: boolean;
  onChange: (rows: BuilderKeyValue[]) => void;
}) {
  const { t } = useI18n();
  return (
    <div>
      <p className="mb-1 text-label text-text-secondary">{label}</p>
      <div className="space-y-1.5">
        {rows.map((row, index) => (
          <div key={index} className="flex gap-1.5">
            <Input
              size="sm"
              aria-label={`${label} — key ${index + 1}`}
              value={row.key}
              disabled={disabled}
              placeholder="key"
              onChange={(event) => onChange(rows.map((r, i) =>
                i === index ? { ...r, key: event.target.value } : r))}
            />
            <Input
              size="sm"
              aria-label={`${label} — value ${index + 1}`}
              value={row.value}
              disabled={disabled}
              placeholder="value"
              onChange={(event) => onChange(rows.map((r, i) =>
                i === index ? { ...r, value: event.target.value } : r))}
            />
            <Button
              size="xs"
              variant="ghost"
              aria-label={t('builder.removeParam')}
              disabled={disabled}
              leadingIcon={<Trash2 className="h-3 w-3" />}
              onClick={() => onChange(rows.filter((_, i) => i !== index))}
            />
          </div>
        ))}
        {!disabled && (
          <Button size="xs" variant="ghost" leadingIcon={<Plus className="h-3 w-3" />}
                  onClick={() => onChange([...rows, { key: '', value: '' }])}>
            {t('builder.addParam')}
          </Button>
        )}
      </div>
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
  result, t, onApplySchema,
}: {
  result: BuilderTestResult;
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

  return (
    <div className="space-y-3">
      {result.ok ? (
        <div className="space-y-1">
          <p className="flex items-center gap-1.5 text-caption text-success">
            <CheckCircle2 className="h-4 w-4" />
            {result.record_preview_supported
              ? t('builder.testOk', { n: String(result.record_count) })
              : t('builder.testOkNoPreview')}
          </p>
          {!result.record_preview_supported && (
            <p className="pl-[1.375rem] text-tiny text-text-tertiary">
              {t('builder.noPreviewHint')}
            </p>
          )}
        </div>
      ) : (
        <div className="rounded-md border border-danger/30 bg-danger/5 p-2.5">
          <p className="flex items-start gap-1.5 text-caption text-danger">
            <AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0" />
            <span>{result.error?.summary ?? t('builder.testFailed')}</span>
          </p>
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

      {result.requests.length > 0 && (
        <div className="space-y-1.5">
          {result.requests.slice(0, 3).map((exchange, index) => (
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

      {result.records.length > 0 && (
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
                <tr className="border-b border-[rgb(var(--border-line))] bg-surface-2 text-tiny uppercase tracking-[0.06em] text-text-quaternary">
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

      {result.logs.length > 0 && (
        <details className="rounded-md border border-[rgb(var(--border-line))]">
          <summary className="cursor-pointer px-2.5 py-1.5 text-tiny font-emphasis text-text-tertiary">
            {t('builder.testLogs', { n: String(result.logs.length) })}
          </summary>
          <pre className="max-h-48 overflow-auto px-2.5 py-2 font-mono text-tiny leading-relaxed text-text-quaternary">
            {result.logs.join('\n')}
          </pre>
        </details>
      )}
    </div>
  );
}
