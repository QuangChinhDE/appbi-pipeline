'use client';

import * as React from 'react';
import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  CheckCircle2, Clock, Pause, Play, Save, Trash2, TrendingUp, Wand2, XCircle,
} from 'lucide-react';

import { ApiError, opsApi, pipelineApi, runApi } from '@/lib/api';
import { qk } from '@/lib/queryKeys';
import {
  describeSchedule, formatDateTime, formatDuration, formatNumber, formatPercent, formatRelative,
} from '@/lib/format';
import { useWorkspaceId } from '@/hooks/use-current-user';
import { toastError, toastSuccess } from '@/hooks/use-toast';
import { useI18n } from '@/providers/LanguageProvider';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { ConfirmDialog } from '@/components/ui/Modal';
import { ErrorState, Spinner } from '@/components/ui/Feedback';
import { Tabs } from '@/components/ui/Tabs';
import { Card, DetailBody, DetailHeader, StatTile } from '@/components/layout/PageLayout';
import {
  HealthBadge, PipelineStatusBadge, RunStatusBadge, SyncModeBadge, TriggerBadge,
} from '@/components/integrations/Badges';
import { SourceDestinationPath } from '@/components/integrations/ConnectorIcon';
import {
  ErrorRemediationCard, fromApiError, type RemediationInput,
} from '@/components/integrations/ErrorRemediationCard';
import { SchemaDiffViewer } from '@/components/integrations/SchemaDiffViewer';
import { ScheduleEditor } from '@/components/integrations/ScheduleEditor';
import type { ScheduleConfig } from '@/lib/types';

export default function PipelineDetailPage() {
  const params = useParams<{ id: string }>();
  const pipelineId = params.id;
  const { t, locale } = useI18n();
  const router = useRouter();
  const queryClient = useQueryClient();
  const workspaceId = useWorkspaceId();

  const [tab, setTab] = React.useState('overview');
  const [failure, setFailure] = React.useState<RemediationInput | null>(null);
  const [confirmDelete, setConfirmDelete] = React.useState(false);

  const { data: pipeline, isLoading, error, refetch } = useQuery({
    queryKey: qk.pipeline(workspaceId, pipelineId),
    queryFn: () => pipelineApi.detail(pipelineId),
    // While a run is active, poll the product API (never the engine, section 34.3).
    refetchInterval: (query) =>
      query.state.data?.health.code === 'RUNNING' ? 4_000 : 30_000,
  });

  const runs = useQuery({
    queryKey: qk.runs(workspaceId, { pipeline_id: pipelineId }),
    queryFn: () => runApi.list({ pipeline_id: pipelineId, limit: 25 }),
    enabled: tab === 'runs' || tab === 'overview',
    refetchInterval: pipeline?.health.code === 'RUNNING' ? 5_000 : false,
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['workspace', workspaceId] });

  const runNow = useMutation({
    mutationFn: () => pipelineApi.run(pipelineId, `manual-${pipelineId}-${Date.now()}`),
    onSuccess: (run) => {
      setFailure(null);
      toastSuccess(t('pipelines.queued'), `Run ${run.short_id}`);
      invalidate();
    },
    onError: (caught) => setFailure(fromApiError(caught, pipeline?.name)),
  });

  const togglePause = useMutation({
    mutationFn: () =>
      pipeline?.status === 'PAUSED' ? pipelineApi.enable(pipelineId) : pipelineApi.pause(pipelineId),
    onSuccess: () => { toastSuccess(t('pipelines.scheduleUpdated')); invalidate(); },
    onError: (caught) => setFailure(fromApiError(caught, pipeline?.name)),
  });

  const rediscover = useMutation({
    mutationFn: () => pipelineApi.rediscover(pipelineId),
    onSuccess: () => {
      toastSuccess(t('pipelines.schemaRefreshed'));
      invalidate();
      setTab('schema');
    },
    onError: (caught) => setFailure(fromApiError(caught, pipeline?.name)),
  });

  const remove = useMutation({
    mutationFn: () => pipelineApi.remove(pipelineId),
    onSuccess: () => {
      invalidate();
      toastSuccess(t('pipelines.deleted'));
      router.push('/pipelines');
    },
    onError: (caught) => { setConfirmDelete(false); toastError(caught); },
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
  if (!pipeline) return null;

  const actions = pipeline.available_actions;
  const lastRun = pipeline.last_run;
  const failedLastRun = lastRun && ['FAILED', 'FAILED_TO_START', 'TIMED_OUT'].includes(lastRun.status);

  return (
    <div>
      <DetailHeader
        backHref="/pipelines"
        backLabel={t('pipelines.title')}
        title={pipeline.name}
        subtitle={
          <SourceDestinationPath source={pipeline.source} destination={pipeline.destination} />
        }
        badges={
          <>
            <PipelineStatusBadge status={pipeline.status} />
            <HealthBadge health={pipeline.health} />
          </>
        }
        actions={
          <>
            {actions.includes('RUN_NOW') && (
              <Button
                variant="primary"
                loading={runNow.isPending}
                onClick={() => runNow.mutate()}
                leadingIcon={<Play className="h-3.5 w-3.5" />}
              >
                {t('pipelines.runNow')}
              </Button>
            )}
            {(actions.includes('PAUSE') || actions.includes('RESUME')) && (
              <Button
                variant="secondary"
                loading={togglePause.isPending}
                onClick={() => togglePause.mutate()}
                leadingIcon={pipeline.status === 'PAUSED'
                  ? <Play className="h-3.5 w-3.5" />
                  : <Pause className="h-3.5 w-3.5" />}
              >
                {pipeline.status === 'PAUSED' ? t('pipelines.resume') : t('pipelines.pause')}
              </Button>
            )}
            {actions.includes('REDISCOVER_SCHEMA') && (
              <Button
                variant="ghost"
                loading={rediscover.isPending}
                onClick={() => rediscover.mutate()}
                leadingIcon={<Wand2 className="h-3.5 w-3.5" />}
              >
                {t('pipelines.refreshSchema')}
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
        {/* The error card comes before the metrics: when something is broken,
            that is what the operator needs first (section 33.5). */}
        {failure && (
          <div className="mb-4">
            <ErrorRemediationCard error={{ ...failure, onRetry: () => runNow.mutate() }} />
          </div>
        )}

        {pipeline.status === 'NEEDS_REVIEW' && (
          <div className="mb-4">
            <ErrorRemediationCard
              error={{
                code: 'PIPELINE_NEEDS_REVIEW',
                message: pipeline.needs_review_reason ?? t('pipelines.needsReviewDefault'),
                category: 'SCHEMA',
                affects: pipeline.name,
                action: 'REVIEW_SCHEMA',
                onAction: () => setTab('schema'),
              }}
            />
          </div>
        )}

        {failedLastRun && !failure && lastRun && (
          <div className="mb-4">
            <FailedRunCard runId={lastRun.id} pipelineName={pipeline.name} />
          </div>
        )}

        <Tabs
          className="mb-4"
          value={tab}
          onChange={setTab}
          items={[
            { id: 'overview', label: t('pipelines.tab.overview') },
            { id: 'schema', label: t('pipelines.tab.schema'), count: pipeline.stream_count },
            { id: 'runs', label: t('pipelines.tab.runs') },
            { id: 'settings', label: t('pipelines.tab.settings') },
          ]}
        />

        {tab === 'overview' && (
          <div className="space-y-4">
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4 2xl:grid-cols-6">
              <StatTile
                label={t('pipelines.lastRun')}
                value={lastRun ? <RunStatusBadge status={lastRun.status} /> : '—'}
                helper={lastRun ? formatRelative(lastRun.ended_at ?? lastRun.started_at, locale) : undefined}
              />
              <StatTile
                label={t('pipelines.nextRun')}
                value={pipeline.next_run_at ? formatRelative(pipeline.next_run_at, locale) : '—'}
                helper={describeSchedule(pipeline.schedule, t)}
                icon={<Clock className="h-3.5 w-3.5" />}
              />
              <StatTile
                label={t('pipelines.successRate7d')}
                value={formatPercent(pipeline.metrics.success_rate_7d)}
                tone={pipeline.metrics.success_rate_7d === null ? 'default'
                  : pipeline.metrics.success_rate_7d >= 95 ? 'success' : 'warning'}
                icon={<TrendingUp className="h-3.5 w-3.5" />}
              />
              <StatTile
                label={t('pipelines.avgDuration')}
                value={formatDuration(pipeline.metrics.average_duration_seconds)}
              />
              <StatTile
                label={t('pipelines.records30d')}
                value={formatNumber(pipeline.metrics.records_synced_30d)}
              />
              <StatTile
                label={t('pipelines.streams')}
                value={pipeline.stream_count}
                helper={t('pipelines.runsPer30d', { n: pipeline.metrics.total_runs_30d })}
              />
            </div>

            <Card title={t('pipelines.recentRuns')} padded={false}
                  action={<Link href={`/runs?pipeline_id=${pipeline.id}`}
                                className="inline-block py-1 text-tiny text-brand hover:underline">
                    {t('common.viewAll')}
                  </Link>}>
              <RunTable runs={pipeline.recent_runs} locale={locale} />
            </Card>
          </div>
        )}

        {tab === 'schema' && <SchemaTab pipelineId={pipelineId} />}

        {tab === 'runs' && (
          <Card padded={false}>
            <RunTable runs={(runs.data?.items ?? []).map((run) => ({
              id: run.id, status: run.status, trigger_type: run.trigger_type,
              started_at: run.started_at, ended_at: run.ended_at,
              duration_seconds: run.duration_seconds, records_synced: run.records_synced,
              error_category: run.error?.category ?? null,
            }))} locale={locale} />
          </Card>
        )}

        {tab === 'settings' && <SettingsTab pipeline={pipeline} />}
      </DetailBody>

      <ConfirmDialog
        open={confirmDelete}
        onClose={() => setConfirmDelete(false)}
        onConfirm={() => remove.mutate()}
        loading={remove.isPending}
        destructive
        title={t('pipelines.deleteTitle')}
        confirmLabel={t('common.delete')}
        message={t('pipelines.deleteBody', { name: pipeline.name })}
      />
    </div>
  );
}

function RunTable({
  runs, locale,
}: {
  runs: { id: string; status: string; trigger_type: string | null; started_at: string | null;
          ended_at: string | null; duration_seconds: number | null;
          records_synced: number | null; error_category: string | null }[];
  locale: 'vi' | 'en';
}) {
  const { t } = useI18n();
  if (runs.length === 0) {
    return <p className="px-4 py-6 text-center text-caption text-text-quaternary">
      {t('pipelines.noRuns')}
    </p>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[680px] text-left">
        <thead>
          <tr className="border-b border-[rgb(var(--border-line))] text-tiny uppercase tracking-[0.08em] text-text-quaternary">
            <th scope="col" className="px-4 py-2 font-emphasis">Run</th>
            <th scope="col" className="px-3 py-2 font-emphasis">{t('common.status')}</th>
            <th scope="col" className="px-3 py-2 font-emphasis">{t('runs.trigger')}</th>
            <th scope="col" className="px-3 py-2 font-emphasis">{t('runs.started')}</th>
            <th scope="col" className="px-3 py-2 font-emphasis">{t('runs.duration')}</th>
            <th scope="col" className="px-3 py-2 font-emphasis">{t('runs.records')}</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-[rgb(var(--border-line))]">
          {runs.map((run) => (
            <tr key={run.id} className="transition-colors hover:bg-surface-2/60">
              <td className="px-4 py-2">
                <Link href={`/runs/${run.id}`}
                      className="inline-block py-1 font-mono text-caption text-brand hover:underline">
                  {run.id.slice(0, 8)}
                </Link>
              </td>
              <td className="px-3 py-2"><RunStatusBadge status={run.status} size="xs" /></td>
              <td className="px-3 py-2">
                {run.trigger_type && <TriggerBadge trigger={run.trigger_type} />}
              </td>
              <td className="px-3 py-2 text-caption text-text-tertiary">
                {formatRelative(run.started_at, locale)}
              </td>
              <td className="px-3 py-2 text-caption tabular-nums text-text-secondary">
                {formatDuration(run.duration_seconds)}
              </td>
              <td className="px-3 py-2 text-caption tabular-nums text-text-secondary">
                {formatNumber(run.records_synced)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function FailedRunCard({ runId, pipelineName }: { runId: string; pipelineName: string }) {
  const { t } = useI18n();
  const workspaceId = useWorkspaceId();
  const router = useRouter();
  const queryClient = useQueryClient();
  const { data } = useQuery({
    queryKey: qk.run(workspaceId, runId),
    queryFn: () => runApi.detail(runId),
  });

  const retry = useMutation({
    mutationFn: () => runApi.retry(runId),
    onSuccess: (run) => {
      queryClient.invalidateQueries({ queryKey: ['workspace', workspaceId] });
      toastSuccess(t('runs.retryCreated'));
      router.push(`/runs/${run.id}`);
    },
    onError: (caught) => toastError(caught),
  });

  if (!data?.error) return null;
  return (
    <ErrorRemediationCard
      error={{
        code: data.error.code,
        message: data.error.summary ?? t('pipelines.lastRunFailed'),
        category: data.error.category,
        affects: pipelineName,
        action: data.error.remediation_action,
        technicalMessage: data.error.technical_message,
        traceId: data.trace_id,
        onAction: () => router.push(`/runs/${runId}`),
        onRetry: () => retry.mutate(),
      }}
    />
  );
}

function SchemaTab({ pipelineId }: { pipelineId: string }) {
  const workspaceId = useWorkspaceId();
  const queryClient = useQueryClient();
  const { t, locale } = useI18n();

  const pipeline = useQuery({
    queryKey: qk.pipeline(workspaceId, pipelineId),
    queryFn: () => pipelineApi.detail(pipelineId),
  });
  const diff = useQuery({
    queryKey: qk.schemaDiff(workspaceId, pipelineId),
    queryFn: () => pipelineApi.schemaDiff(pipelineId),
  });

  const approve = useMutation({
    mutationFn: (snapshotId: string) => pipelineApi.approveSchema(pipelineId, snapshotId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['workspace', workspaceId] });
      toastSuccess(t('pipelines.schemaApproved'));
    },
    onError: (caught) => toastError(caught),
  });

  const streams = pipeline.data?.streams ?? [];
  const pendingSnapshot = diff.data?.to_snapshot_id;
  const hasChanges = diff.data
    && (diff.data.added.length + diff.data.removed.length + diff.data.changed.length) > 0;

  return (
    <div className="space-y-4">
      <Card
        title={t('pipelines.schemaChanges')}
        description={pipeline.data?.schema_snapshot_at
          ? t('pipelines.schemaCurrentAt', {
              time: formatDateTime(pipeline.data.schema_snapshot_at, locale) })
          : undefined}
        action={
          hasChanges && pendingSnapshot ? (
            <Button
              size="xs"
              variant="primary"
              loading={approve.isPending}
              onClick={() => approve.mutate(pendingSnapshot)}
              leadingIcon={<CheckCircle2 className="h-3 w-3" />}
            >
              {t('pipelines.approveSchema')}
            </Button>
          ) : null
        }
      >
        {diff.isLoading ? <Spinner /> : diff.data ? <SchemaDiffViewer diff={diff.data} /> : null}
      </Card>

      <Card title={t('pipelines.syncedStreams', { n: streams.length })} padded={false}>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[720px] text-left">
            <thead>
              <tr className="border-b border-[rgb(var(--border-line))] text-tiny uppercase tracking-[0.08em] text-text-quaternary">
                <th scope="col" className="px-4 py-2 font-emphasis">{t('stream.colName')}</th>
                <th scope="col" className="px-3 py-2 font-emphasis">{t('stream.colRead')}</th>
                <th scope="col" className="px-3 py-2 font-emphasis">{t('stream.colWrite')}</th>
                <th scope="col" className="px-3 py-2 font-emphasis">{t('stream.colCursor')}</th>
                <th scope="col" className="px-3 py-2 font-emphasis">{t('stream.colPk')}</th>
                <th scope="col" className="px-3 py-2 font-emphasis">
                  {t('stream.fieldCount', { n: '' }).trim()}
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[rgb(var(--border-line))]">
              {streams.map((stream) => (
                <tr key={stream.id}>
                  <td className="px-4 py-2 text-caption text-text-primary">
                    {stream.namespace ? `${stream.namespace}.` : ''}{stream.name}
                  </td>
                  <td className="px-3 py-2"><SyncModeBadge mode={stream.sync_mode} /></td>
                  <td className="px-3 py-2">
                    <SyncModeBadge mode={stream.destination_sync_mode} dim />
                  </td>
                  <td className="px-3 py-2 font-mono text-tiny text-text-tertiary">
                    {stream.cursor_fields.join(', ') || '—'}
                  </td>
                  <td className="px-3 py-2 font-mono text-tiny text-text-tertiary">
                    {stream.primary_key_fields.flat().join(', ') || '—'}
                  </td>
                  <td className="px-3 py-2 text-caption tabular-nums text-text-secondary">
                    {stream.field_count}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}

function SettingsTab({ pipeline }: { pipeline: import('@/lib/types').PipelineDetail }) {
  const workspaceId = useWorkspaceId();
  const queryClient = useQueryClient();
  const { t } = useI18n();
  const [schedule, setSchedule] = React.useState<ScheduleConfig>(pipeline.schedule);

  const save = useMutation({
    mutationFn: () => pipelineApi.update(pipeline.id, { schedule, version: pipeline.version }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['workspace', workspaceId] });
      toastSuccess(t('pipelines.scheduleUpdated'));
    },
    onError: (caught) => toastError(caught),
  });

  return (
    <div className="max-w-3xl space-y-4">
      <Card title={t('pipelines.schedule')}>
        <ScheduleEditor value={schedule} onChange={setSchedule} />
        <div className="mt-4">
          <Button
            variant="primary"
            loading={save.isPending}
            onClick={() => save.mutate()}
            leadingIcon={<Save className="h-3.5 w-3.5" />}
          >
            {t('common.save')}
          </Button>
        </div>
      </Card>

      <Card title={t('pipelines.opsPolicy')}>
        <dl className="space-y-2.5">
          <div className="flex items-baseline justify-between gap-3">
            <dt className="text-caption text-text-tertiary">{t('pipelines.overlapLabel')}</dt>
            <dd className="text-caption text-text-primary">
              {t(pipeline.overlap_policy === 'SKIP_IF_RUNNING'
                ? 'pipelines.overlapSkip' : 'pipelines.overlapQueue')}
            </dd>
          </div>
          <div className="flex items-baseline justify-between gap-3">
            <dt className="text-caption text-text-tertiary">{t('pipelines.schemaPolicy')}</dt>
            <dd className="text-caption text-text-primary">
              {t('pipelines.schemaPolicyValue')}
            </dd>
          </div>
          <div className="flex items-baseline justify-between gap-3">
            <dt className="text-caption text-text-tertiary">{t('pipelines.prefix')}</dt>
            <dd className="text-caption text-text-primary">{pipeline.stream_prefix || '—'}</dd>
          </div>
          <div className="flex items-baseline justify-between gap-3">
            <dt className="text-caption text-text-tertiary">{t('pipelines.configVersion')}</dt>
            <dd className="text-caption text-text-primary">
              <Badge variant="subtle" size="xs">v{pipeline.version}</Badge>
            </dd>
          </div>
        </dl>
      </Card>
    </div>
  );
}
