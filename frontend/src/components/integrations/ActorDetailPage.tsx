'use client';

import * as React from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  CheckCircle2, GitBranch, Power, RefreshCw, Save, Trash2, Wand2,
} from 'lucide-react';

import { ApiError, destinationApi, sourceApi } from '@/lib/api';
import { qk } from '@/lib/queryKeys';
import { formatDateTime, formatRelative } from '@/lib/format';
import { useWorkspaceId } from '@/hooks/use-current-user';
import { useUrlTab } from '@/hooks/use-url-tab';
import { toastError, toastSuccess } from '@/hooks/use-toast';
import { useI18n } from '@/providers/LanguageProvider';
import { Button } from '@/components/ui/Button';
import { Input, Label, Textarea } from '@/components/ui/Input';
import { Badge } from '@/components/ui/Badge';
import { ConfirmDialog, Modal } from '@/components/ui/Modal';
import { EmptyState, ErrorState, Spinner } from '@/components/ui/Feedback';
import { Tabs } from '@/components/ui/Tabs';
import { Card, DetailBody, DetailHeader, ModuleOverview } from '@/components/layout/PageLayout';
import { HealthBadge } from './Badges';
import { ConnectorIcon } from './ConnectorIcon';
import { ErrorRemediationCard, fromApiError, type RemediationInput } from './ErrorRemediationCard';
import {
  DynamicConnectorForm, applyDefaults, splitSecrets, validateAgainstSpec, type FormValues,
} from './DynamicConnectorForm';

type Kind = 'source' | 'destination';
const ACTOR_TABS = ['overview', 'configuration', 'pipelines'] as const;

export function ActorDetailPage({ kind, actorId }: { kind: Kind; actorId: string }) {
  const { t, locale } = useI18n();
  const router = useRouter();
  const queryClient = useQueryClient();
  const workspaceId = useWorkspaceId();

  const isSource = kind === 'source';
  const api = isSource ? sourceApi : destinationApi;
  const basePath = isSource ? '/sources' : '/destinations';
  const detailKey = isSource ? qk.source(workspaceId, actorId) : qk.destination(workspaceId, actorId);

  const { tab, setTab, hrefForTab } = useUrlTab(ACTOR_TABS, 'overview');
  const [failure, setFailure] = React.useState<RemediationInput | null>(null);
  const [confirmDelete, setConfirmDelete] = React.useState(false);
  const [dependencies, setDependencies] = React.useState<{ id: string; name: string }[] | null>(null);

  const { data: actor, isLoading, error, refetch } = useQuery({
    queryKey: detailKey,
    queryFn: () => api.detail(actorId),
  });

  const pipelines = useQuery({
    queryKey: [...detailKey, 'pipelines'],
    queryFn: () => api.pipelines(actorId),
    enabled: Boolean(actor),
  });

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ['workspace', workspaceId] });

  const test = useMutation({
    mutationFn: () => api.test(actorId),
    onSuccess: (result) => {
      if (result.succeeded) {
        toastSuccess(t('sources.testSuccess'));
        setFailure(null);
      } else {
        setFailure({
          code: result.error_code, message: result.message ?? t('sources.testFailed'),
          category: result.category, technicalMessage: result.technical_message,
          affects: actor?.name,
        });
      }
      invalidate();
    },
    onError: (caught) => setFailure(fromApiError(caught, actor?.name)),
  });

  const toggleEnabled = useMutation({
    mutationFn: () => (actor?.status === 'ACTIVE' ? api.disable(actorId) : api.enable(actorId)),
    onSuccess: () => { invalidate(); toastSuccess(t('actor.statusUpdated')); },
    onError: (caught) => {
      if (caught instanceof ApiError && caught.constraints) setDependencies(caught.constraints);
      else toastError(caught);
    },
  });

  const discover = useMutation({
    mutationFn: () => sourceApi.discover(actorId, true),
    onSuccess: (snapshot) => {
      invalidate();
      toastSuccess(t('sources.schemaRefreshed'),
        t('sources.streamCount', { n: snapshot.stream_count }));
    },
    onError: (caught) => setFailure(fromApiError(caught, actor?.name)),
  });

  const remove = useMutation({
    mutationFn: () => api.remove(actorId),
    onSuccess: () => {
      invalidate();
      toastSuccess(t('actor.deleted'));
      router.push(basePath);
    },
    onError: (caught) => {
      setConfirmDelete(false);
      if (caught instanceof ApiError && caught.constraints) setDependencies(caught.constraints);
      else toastError(caught);
    },
  });

  if (isLoading) return <Spinner label={t('common.loading')} />;
  if (error) {
    return (
      <div className="p-6">
        <ErrorState title={t('common.errorTitle')} message={(error as Error).message}
                    onRetry={() => refetch()} />
      </div>
    );
  }
  if (!actor) return null;

  const actions = actor.available_actions;
  const linkedPipelines = pipelines.data ?? [];
  const nextRun = linkedPipelines
    .filter((pipeline) => pipeline.next_run_at)
    .sort((left, right) => String(left.next_run_at).localeCompare(String(right.next_run_at)))[0]
    ?.next_run_at;

  return (
    <div>
      <DetailHeader
        backHref={basePath}
        backLabel={isSource ? t('sources.title') : t('destinations.title')}
        icon={<ConnectorIcon icon={actor.connector_icon} connectorKey={actor.connector_key} size="lg" />}
        title={actor.name}
        subtitle={
          <span className="text-caption text-text-tertiary">
            {actor.connector_display_name}
            <span className="ml-1.5 font-mono text-tiny text-text-quaternary">
              v{actor.connector_version}
            </span>
          </span>
        }
        badges={
          <>
            <HealthBadge health={actor.health} />
            {actor.status !== 'ACTIVE' && <Badge variant="neutral" size="sm">{actor.status}</Badge>}
          </>
        }
        actions={
          <>
            {/* The way onward. A healthy source with nothing pointing at it
                moves no data, and this page previously offered only test,
                discover, enable and delete -- so the journey ended here and
                the user had to work out for themselves that a destination and
                a pipeline were still needed. */}
            {actor.health.level === 'HEALTHY' && (
              <Button
                variant="primary"
                size="sm"
                onClick={() => router.push(
                  isSource
                    ? `/destinations/new?source=${actor.id}`
                    : `/pipelines/new?destination=${actor.id}`)}
              >
                {t(isSource ? 'actor.continueToDestination' : 'actor.continueToPipeline')}
              </Button>
            )}
            {actions.includes('TEST') && (
              <Button
                variant="secondary"
                loading={test.isPending}
                onClick={() => test.mutate()}
                leadingIcon={<RefreshCw className="h-3.5 w-3.5" />}
              >
                {t('sources.testConnection')}
              </Button>
            )}
            {isSource && actions.includes('DISCOVER') && (
              <Button
                variant="secondary"
                loading={discover.isPending}
                onClick={() => discover.mutate()}
                leadingIcon={<Wand2 className="h-3.5 w-3.5" />}
              >
                {t('sources.discover')}
              </Button>
            )}
            {(actions.includes('ENABLE') || actions.includes('DISABLE')) && (
              <Button
                variant="ghost"
                loading={toggleEnabled.isPending}
                onClick={() => toggleEnabled.mutate()}
                leadingIcon={<Power className="h-3.5 w-3.5" />}
              >
                {actor.status === 'ACTIVE' ? t('common.disable') : t('common.enable')}
              </Button>
            )}
            {actions.includes('DELETE') && (
              <Button
                variant="ghost"
                onClick={() => setConfirmDelete(true)}
                leadingIcon={<Trash2 className="h-3.5 w-3.5" />}
              >
                {t('common.delete')}
              </Button>
            )}
          </>
        }
      />

      <DetailBody>
        {failure && (
          <div className="mb-4">
            <ErrorRemediationCard
              error={{ ...failure, onAction: () => setTab('configuration'),
                       onRetry: () => test.mutate() }}
            />
          </div>
        )}

        <Tabs
          className="mb-4"
          value={tab}
          items={[
            {
              id: 'overview', label: t('pipelines.tab.overview'),
              href: hrefForTab('overview'),
            },
            {
              id: 'configuration', label: t('actor.tabConfig'),
              href: hrefForTab('configuration'),
            },
            {
              id: 'pipelines', label: t('actor.tabPipelines'), count: actor.pipeline_count,
              href: hrefForTab('pipelines'),
            },
          ]}
        />

        {tab === 'overview' && (
          <div className="grid gap-4 lg:grid-cols-3">
            <Card title={t('actor.sectionHealth')}>
              <dl className="space-y-2.5">
                <Row label={t('actor.healthLabel')}
                     value={<HealthBadge health={actor.health} size="xs" />} />
                <Row label={t('actor.lastTest')}
                     value={actor.last_test_at
                       ? formatDateTime(actor.last_test_at, locale) : t('common.never')} />
                <Row label={t('actor.testResult')} value={actor.last_test_result} />
                {actor.health.message && (
                  <Row label={t('actor.note')} value={
                    <span className="text-danger">{actor.health.message}</span>
                  } />
                )}
              </dl>
            </Card>

            <Card title={t('actor.sectionConnector')}>
              <dl className="space-y-2.5">
                <Row label={t('actor.connectorType')}
                     value={actor.connector_display_name ?? actor.connector_key} />
                <Row label={t('actor.pinnedVersion')} value={
                  <span className="font-mono text-tiny">{actor.connector_version}</span>
                } />
                <Row label={t('actor.credential')} value={
                  actor.credentials.configured
                    ? <Badge variant="success" size="xs">{t('actor.credentialConfigured')}</Badge>
                    : <Badge variant="neutral" size="xs">{t('actor.credentialNone')}</Badge>
                } />
                {actor.credentials.rotated_at && (
                  <Row label={t('actor.credentialRotated')}
                       value={formatRelative(actor.credentials.rotated_at, locale)} />
                )}
              </dl>
            </Card>

            <Card title={t('actor.sectionInfo')}>
              <dl className="space-y-2.5">
                <Row label={t('common.owner')} value={actor.owner?.full_name ?? '—'} />
                <Row label={t('common.created')} value={formatDateTime(actor.created_at, locale)} />
                <Row label={t('common.updated')} value={formatRelative(actor.updated_at, locale)} />
                <Row label={t('actor.pipelinesUsing')} value={actor.pipeline_count} />
                {isSource && (
                  <Row
                    label={t('sources.lastDiscovered')}
                    value={actor.last_discovered_at
                      ? formatRelative(actor.last_discovered_at, locale)
                      : t('common.never')}
                  />
                )}
              </dl>
            </Card>
          </div>
        )}

        {tab === 'configuration' && <ConfigurationTab kind={kind} actor={actor} />}

        {tab === 'pipelines' && (
          <div className="space-y-3">
            <ModuleOverview stats={[
              { label: t('actor.pipelineSummary.total'), value: linkedPipelines.length },
              {
                label: t('actor.pipelineSummary.active'),
                value: linkedPipelines.filter((pipeline) => pipeline.status === 'ACTIVE').length,
                tone: 'success',
              },
              {
                label: t('actor.pipelineSummary.paused'),
                value: linkedPipelines.filter((pipeline) => pipeline.status === 'PAUSED').length,
              },
              {
                label: t('actor.pipelineSummary.next'),
                value: nextRun ? formatRelative(nextRun, locale) : t('common.none'),
              },
            ]} />
            <Card title={t('actor.tabPipelines')} padded={false}>
              {pipelines.isLoading ? (
                <Spinner label={t('common.loading')} />
              ) : linkedPipelines.length === 0 ? (
                <div className="p-4">
                  <EmptyState
                    icon={GitBranch}
                    title={t('actor.noPipelines')}
                    action={
                      <Button
                        size="sm"
                        variant="primary"
                        onClick={() => router.push(
                          isSource
                            ? `/destinations/new?source=${actor.id}`
                            : `/pipelines/new?destination=${actor.id}`)}
                      >
                        {t(isSource ? 'actor.continueToDestination' : 'actor.continueToPipeline')}
                      </Button>
                    }
                    compact
                  />
                </div>
              ) : (
                <ul className="divide-y divide-[rgb(var(--border-line))]">
                  {linkedPipelines.map((pipeline) => (
                  <li key={pipeline.id}>
                    <Link
                      href={`/pipelines/${pipeline.id}?tab=status`}
                      className="flex items-center justify-between gap-3 px-4 py-2.5 hover:bg-surface-2"
                    >
                      <span className="text-caption font-emphasis text-text-primary">
                        {pipeline.name}
                      </span>
                      <span className="flex items-center gap-2">
                        <Badge variant="subtle" size="xs">{pipeline.status}</Badge>
                        {pipeline.next_run_at && (
                          <span className="text-tiny text-text-quaternary">
                            {t('pipelines.nextRun')}: {formatRelative(pipeline.next_run_at, locale)}
                          </span>
                        )}
                      </span>
                    </Link>
                  </li>
                  ))}
                </ul>
              )}
            </Card>
          </div>
        )}
      </DetailBody>

      <ConfirmDialog
        open={confirmDelete}
        onClose={() => setConfirmDelete(false)}
        onConfirm={() => remove.mutate()}
        loading={remove.isPending}
        destructive
        title={t('actor.deleteTitle')}
        confirmLabel={t('common.delete')}
        message={
          <>
            {t('actor.deleteBody', { name: actor.name })}
            {actor.pipeline_count > 0 && (
              <span className="mt-2 block text-danger">
                {t('actor.deleteBlocked', { n: actor.pipeline_count })}
              </span>
            )}
          </>
        }
      />

      <DependencyModal
        constraints={dependencies}
        onClose={() => setDependencies(null)}
      />
    </div>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-caption text-text-tertiary">{label}</dt>
      <dd className="text-right text-caption text-text-primary">{value}</dd>
    </div>
  );
}

/** Shows the exact list from the 409 envelope (section 77). */
export function DependencyModal({
  constraints, onClose,
}: {
  constraints: { id: string; name: string; type?: string }[] | null;
  onClose: () => void;
}) {
  const { t } = useI18n();
  return (
    <Modal
      open={Boolean(constraints)}
      onClose={onClose}
      title={t('actor.dependencyTitle')}
      description={t('actor.dependencyBody')}
      footer={<Button variant="secondary" size="sm" onClick={onClose}>{t('common.close')}</Button>}
    >
      <ul className="divide-y divide-[rgb(var(--border-line))]">
        {(constraints ?? []).map((item) => (
          <li key={item.id} className="flex items-center justify-between gap-3 py-2">
            <span className="text-caption text-text-primary">{item.name}</span>
            <Link
              href={`/pipelines/${item.id}?tab=status`}
              className="text-caption text-brand hover:underline"
              onClick={onClose}
            >
              {t('actor.openPipeline')}
            </Link>
          </li>
        ))}
      </ul>
    </Modal>
  );
}

function ConfigurationTab({ kind, actor }: { kind: Kind; actor: import('@/lib/types').ActorDetail }) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const workspaceId = useWorkspaceId();
  const api = kind === 'source' ? sourceApi : destinationApi;

  const [values, setValues] = React.useState<FormValues>(() =>
    applyDefaults(actor.spec_schema, { ...actor.configuration }));
  const [name, setName] = React.useState(actor.name);
  const [description, setDescription] = React.useState(actor.description ?? '');
  const [errors, setErrors] = React.useState<Record<string, string>>({});
  const [failure, setFailure] = React.useState<RemediationInput | null>(null);

  // Which secret fields already exist server-side, so the form can show them as
  // stored instead of demanding a re-entry (section 21.3).
  const secretsConfigured = React.useMemo(() => {
    const map: Record<string, boolean> = {};
    for (const key of Object.keys(actor.credentials.fields ?? {})) map[key] = true;
    return map;
  }, [actor.credentials.fields]);

  const save = useMutation({
    mutationFn: async () => {
      const found = validateAgainstSpec(actor.spec_schema, values, t);
      setErrors(found);
      if (Object.keys(found).length > 0) {
        throw new Error(t('wizard.missingFields', { n: Object.keys(found).length }));
      }
      const { configuration, credentials } = splitSecrets(actor.spec_schema, values);
      // Empty secret means "unchanged"; only send what the user actually typed.
      const changed = Object.fromEntries(
        Object.entries(credentials).filter(([, value]) => value !== '' && value !== undefined),
      );
      return api.update(actor.id, {
        name: name.trim(),
        description: description || null,
        configuration,
        credentials: changed,
        test_before_save: true,
        version: actor.version,
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['workspace', workspaceId] });
      toastSuccess(t('actor.configSaved'));
      setFailure(null);
    },
    onError: (caught) => setFailure(fromApiError(caught, actor.name)),
  });

  return (
    <div className="max-w-3xl space-y-4">
      {failure && <ErrorRemediationCard error={failure} />}

      <Card title={t('actor.sectionGeneral')}>
        <div className="space-y-3">
          <div>
            <Label htmlFor="cfg-name" required>{t('common.name')}</Label>
            <Input id="cfg-name" value={name} onChange={(event) => setName(event.target.value)} />
          </div>
          <div>
            <Label htmlFor="cfg-desc" hint={t('common.optional')}>{t('common.description')}</Label>
            <Textarea id="cfg-desc" rows={2} value={description}
                      onChange={(event) => setDescription(event.target.value)} />
          </div>
        </div>
      </Card>

      <Card
        title={t('actor.sectionConnection')}
        description={t('actor.connectionHint')}
      >
        <DynamicConnectorForm
          spec={actor.spec_schema}
          values={values}
          errors={errors}
          onChange={setValues}
          secretsConfigured={secretsConfigured}
        />
      </Card>

      <div className="flex items-center gap-2">
        <Button
          variant="primary"
          loading={save.isPending}
          onClick={() => save.mutate()}
          leadingIcon={<Save className="h-3.5 w-3.5" />}
        >
          {t('actor.saveAndTest')}
        </Button>
        {save.isSuccess && !failure && (
          <span className="flex items-center gap-1 text-caption text-success">
            <CheckCircle2 className="h-3.5 w-3.5" /> {t('actor.saved')}
          </span>
        )}
      </div>
    </div>
  );
}
