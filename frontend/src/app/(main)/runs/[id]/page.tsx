'use client';

import * as React from 'react';
import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Ban, Copy, RotateCcw } from 'lucide-react';

import { runApi } from '@/lib/api';
import { qk } from '@/lib/queryKeys';
import { formatBytes, formatDateTime, formatDuration, formatNumber } from '@/lib/format';
import { useWorkspaceId } from '@/hooks/use-current-user';
import { toastError, toastSuccess } from '@/hooks/use-toast';
import { useI18n } from '@/providers/LanguageProvider';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { EmptyState, ErrorState, Spinner } from '@/components/ui/Feedback';
import { Tabs } from '@/components/ui/Tabs';
import { Card, DetailBody, DetailHeader, StatTile } from '@/components/layout/PageLayout';
import { RunStatusBadge, TriggerBadge } from '@/components/integrations/Badges';
import { SourceDestinationPath } from '@/components/integrations/ConnectorIcon';
import { ErrorRemediationCard } from '@/components/integrations/ErrorRemediationCard';
import { LogViewer } from '@/components/integrations/LogViewer';

const ACTIVE = ['QUEUED', 'STARTING', 'RUNNING', 'CANCEL_REQUESTED'];

export default function RunDetailPage() {
  const params = useParams<{ id: string }>();
  const runId = params.id;
  const { t, locale } = useI18n();
  const router = useRouter();
  const queryClient = useQueryClient();
  const workspaceId = useWorkspaceId();
  const [tab, setTab] = React.useState('summary');

  const { data: run, isLoading, error, refetch } = useQuery({
    queryKey: qk.run(workspaceId, runId),
    queryFn: () => runApi.detail(runId),
    // Poll the product API only while the run is active (section 34.3).
    refetchInterval: (query) =>
      query.state.data && ACTIVE.includes(query.state.data.status) ? 4_000 : false,
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['workspace', workspaceId] });

  const cancel = useMutation({
    mutationFn: () => runApi.cancel(runId),
    onSuccess: (updated) => {
      invalidate();
      toastSuccess(
        updated.status === 'CANCEL_REQUESTED'
          ? t('runs.cancelRequested')
          : t('runs.cancelled'),
      );
    },
    onError: (caught) => toastError(caught),
  });

  const retry = useMutation({
    mutationFn: () => runApi.retry(runId),
    onSuccess: (created) => {
      invalidate();
      toastSuccess(t('runs.retryCreated'), `Run ${created.short_id}`);
      router.push(`/runs/${created.id}`);
    },
    onError: (caught) => toastError(caught),
  });

  const copyContext = async () => {
    if (!run) return;
    const payload = [
      `run_id: ${run.id}`,
      `pipeline_id: ${run.pipeline?.id ?? '-'}`,
      `status: ${run.status}`,
      `trace_id: ${run.trace_id ?? '-'}`,
      `started_at: ${run.started_at ?? '-'}`,
      run.error?.code ? `error_code: ${run.error.code}` : '',
    ].filter(Boolean).join('\n');
    await navigator.clipboard.writeText(payload);
    toastSuccess(t('common.copied'));
  };

  if (isLoading) return <Spinner label={t('common.loading')} />;
  if (error) {
    return (
      <div className="p-6">
        <ErrorState title={t('common.errorTitle')} message={(error as Error).message}
                    onRetry={() => refetch()} />
      </div>
    );
  }
  if (!run) return null;

  const isActive = ACTIVE.includes(run.status);

  return (
    <div>
      <DetailHeader
        backHref={run.pipeline ? `/pipelines/${run.pipeline.id}` : '/runs'}
        backLabel={run.pipeline?.name ?? t('runs.title')}
        title={`Run ${run.short_id}`}
        subtitle={
          run.source && run.destination ? (
            <SourceDestinationPath source={run.source} destination={run.destination} size="xs" />
          ) : null
        }
        badges={
          <>
            <RunStatusBadge status={run.status} />
            <TriggerBadge trigger={run.trigger_type} />
            {run.is_stale && <Badge variant="warning" size="sm">{t('runs.stale')}</Badge>}
            {run.retry_of_run_id && (
              <Link href={`/runs/${run.retry_of_run_id}`}>
                <Badge variant="subtle" size="sm">
                  {t('runs.retryOf')} {run.retry_of_run_id.slice(0, 8)}
                </Badge>
              </Link>
            )}
          </>
        }
        actions={
          <>
            {run.actions.can_cancel && (
              <Button
                variant="secondary"
                loading={cancel.isPending}
                onClick={() => cancel.mutate()}
                leadingIcon={<Ban className="h-3.5 w-3.5" />}
              >
                {t('runs.cancel')}
              </Button>
            )}
            {run.actions.can_retry && (
              <Button
                variant="primary"
                loading={retry.isPending}
                onClick={() => retry.mutate()}
                leadingIcon={<RotateCcw className="h-3.5 w-3.5" />}
              >
                {t('runs.retry')}
              </Button>
            )}
            <Button variant="ghost" onClick={copyContext}
                    leadingIcon={<Copy className="h-3.5 w-3.5" />}>
              {t('runs.copySupport')}
            </Button>
          </>
        }
      />

      <DetailBody>
        {run.error && (
          <div className="mb-4">
            <ErrorRemediationCard
              error={{
                code: run.error.code,
                message: run.error.summary ?? t('runs.failedDefault'),
                category: run.error.category,
                affects: run.pipeline?.name,
                action: run.error.remediation_action,
                technicalMessage: run.error.technical_message,
                traceId: run.trace_id,
                onAction: run.error.remediation_action === 'UPDATE_CREDENTIALS' && run.source
                  ? () => router.push(`/sources/${run.source!.id}`)
                  : run.error.remediation_action === 'REDISCOVER_SCHEMA' && run.pipeline
                  ? () => router.push(`/pipelines/${run.pipeline!.id}`)
                  : undefined,
                onRetry: run.actions.can_retry ? () => retry.mutate() : undefined,
              }}
            />
          </div>
        )}

        <div className="mb-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
          <StatTile label={t('common.status')} value={<RunStatusBadge status={run.status} />} />
          <StatTile label={t('runs.duration')}
                    value={formatDuration(run.duration_seconds)}
                    helper={run.started_at ? formatDateTime(run.started_at, locale) : undefined} />
          <StatTile label={t('runs.records')} value={formatNumber(run.records_synced)} />
          <StatTile label={t('runs.bytes')} value={formatBytes(run.bytes_synced)} />
          <StatTile label={t('runs.trigger')}
                    value={<TriggerBadge trigger={run.trigger_type} />}
                    helper={run.triggered_by?.full_name ?? t('runs.bySystem')} />
        </div>

        <Tabs
          className="mb-4"
          value={tab}
          onChange={setTab}
          items={[
            { id: 'summary', label: t('runs.summary'), count: run.stream_stats.length },
            { id: 'attempts', label: t('runs.attempts'), count: run.attempts.length },
            { id: 'logs', label: t('runs.logs') },
          ]}
        />

        {tab === 'summary' && (
          <Card title={t('runs.streamResults')} padded={false}>
            {run.stream_stats.length === 0 ? (
              <EmptyState
                title={t(isActive ? 'runs.noStreamStatsRunning' : 'runs.noStreamStats')}
                compact
              />
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[560px] text-left">
                  <thead>
                    <tr className="border-b border-[rgb(var(--border-line))] text-tiny uppercase tracking-[0.08em] text-text-quaternary">
                      <th scope="col" className="px-4 py-2 font-emphasis">
                        {t('stream.colName')}
                      </th>
                      <th scope="col" className="px-3 py-2 font-emphasis">{t('runs.records')}</th>
                      <th scope="col" className="px-3 py-2 font-emphasis">{t('runs.bytes')}</th>
                      <th scope="col" className="px-3 py-2 font-emphasis">
                        {t('common.status')}
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[rgb(var(--border-line))]">
                    {run.stream_stats.map((stat) => (
                      <tr key={`${stat.namespace ?? ''}.${stat.stream_name}`}>
                        <td className="px-4 py-2 text-caption text-text-primary">
                          {stat.namespace ? `${stat.namespace}.` : ''}{stat.stream_name}
                        </td>
                        <td className="px-3 py-2 text-caption tabular-nums text-text-secondary">
                          {formatNumber(stat.records_emitted)}
                        </td>
                        <td className="px-3 py-2 text-caption tabular-nums text-text-secondary">
                          {formatBytes(stat.bytes_emitted)}
                        </td>
                        <td className="px-3 py-2">
                          <Badge variant={stat.status === 'COMPLETED' ? 'success' : 'info'} size="xs">
                            {stat.status}
                          </Badge>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        )}

        {tab === 'attempts' && (
          <Card padded={false}>
            <ol className="divide-y divide-[rgb(var(--border-line))]">
              {run.attempts.map((attempt) => (
                <li key={attempt.attempt_number} className="flex items-start gap-3 px-4 py-3">
                  <span className="mt-0.5 flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-full bg-surface-2 text-tiny font-strong text-text-secondary">
                    {attempt.attempt_number}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <RunStatusBadge status={attempt.status} size="xs" />
                      <span className="text-tiny text-text-quaternary">
                        {formatDateTime(attempt.started_at, locale)}
                        {attempt.ended_at && ` → ${formatDateTime(attempt.ended_at, locale)}`}
                      </span>
                      <span className="text-tiny text-text-quaternary">
                        {formatDuration(attempt.duration_seconds)}
                      </span>
                    </div>
                    <p className="mt-0.5 text-caption text-text-secondary">
                      {t('overview.recordsSuffix', {
                        n: formatNumber(attempt.records_synced) })} ·{' '}
                      {formatBytes(attempt.bytes_synced)}
                    </p>
                    {attempt.failure_summary && (
                      <p className="mt-1 text-caption text-danger">{attempt.failure_summary}</p>
                    )}
                  </div>
                </li>
              ))}
            </ol>
          </Card>
        )}

        {tab === 'logs' && (
          <Card>
            <LogViewer runId={runId} live={isActive} />
          </Card>
        )}
      </DetailBody>
    </div>
  );
}
