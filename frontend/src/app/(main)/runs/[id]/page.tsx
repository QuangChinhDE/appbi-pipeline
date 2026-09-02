'use client';

import * as React from 'react';
import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Ban, Copy, RotateCcw } from 'lucide-react';

import { runApi } from '@/lib/api';
import { cn } from '@/lib/utils';
import { qk } from '@/lib/queryKeys';
import { formatBytes, formatDateTime, formatDuration, formatNumber } from '@/lib/format';
import type { RunDetail } from '@/lib/types';
import { useWorkspaceId } from '@/hooks/use-current-user';
import { useUrlTab } from '@/hooks/use-url-tab';
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
const RUN_TABS = ['summary', 'attempts', 'logs'] as const;

export default function RunDetailPage() {
  const params = useParams<{ id: string }>();
  const runId = params.id;
  const { t, locale } = useI18n();
  const router = useRouter();
  const queryClient = useQueryClient();
  const workspaceId = useWorkspaceId();
  const { tab, hrefForTab } = useUrlTab(RUN_TABS, 'summary');
  const { data: run, isLoading, error, refetch } = useQuery({
    queryKey: qk.run(workspaceId, runId),
    queryFn: () => runApi.detail(runId),
    refetchInterval: (query) => query.state.data && ACTIVE.includes(query.state.data.status) ? 4_000 : false,
  });
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['workspace', workspaceId] });
  const cancel = useMutation({
    mutationFn: () => runApi.cancel(runId),
    onSuccess: (updated) => {
      invalidate();
      toastSuccess(updated.status === 'CANCEL_REQUESTED' ? t('runs.cancelRequested') : t('runs.cancelled'));
    },
    onError: (caught) => toastError(caught),
  });
  const retry = useMutation({
    mutationFn: () => runApi.retry(runId),
    onSuccess: (created) => {
      invalidate();
      toastSuccess(t('runs.retryCreated'), `Run ${created.short_id}`);
      router.push(`/runs/${created.id}?tab=summary`);
    },
    onError: (caught) => toastError(caught),
  });

  const copyContext = async () => {
    if (!run) return;
    await navigator.clipboard.writeText([
      `run_id: ${run.id}`,
      `run_type: ${run.run_type}`,
      `resource_id: ${run.pipeline?.id ?? run.transform?.id ?? '-'}`,
      `operation: ${run.operation ?? '-'}`,
      `status: ${run.status}`,
      `trace_id: ${run.trace_id ?? '-'}`,
      `started_at: ${run.started_at ?? '-'}`,
      run.error?.code ? `error_code: ${run.error.code}` : '',
    ].filter(Boolean).join('\n'));
    toastSuccess(t('common.copied'));
  };

  if (isLoading) return <Spinner label={t('common.loading')} />;
  if (error) return <div className="p-6"><ErrorState title={t('common.errorTitle')} message={(error as Error).message} onRetry={() => refetch()} /></div>;
  if (!run) return null;

  const isTransform = run.run_type === 'TRANSFORM';
  const isActive = ACTIVE.includes(run.status);
  const resource = isTransform ? run.transform : run.pipeline;
  const backHref = isTransform && run.transform
    ? `/transforms/${run.transform.id}`
    : run.pipeline ? `/pipelines/${run.pipeline.id}?tab=jobs` : '/runs';
  const resultCount = isTransform ? run.transform_nodes.length : run.stream_stats.length;

  return <div>
    <DetailHeader
      backHref={backHref}
      backLabel={resource?.name ?? t('runs.title')}
      title={`Run ${run.short_id}`}
      subtitle={isTransform ? (
        <span className="text-caption text-text-tertiary">
          {run.operation?.replaceAll('_', ' ') ?? 'BUILD'}
          {run.destination ? ` / ${run.destination.name}` : ''}
        </span>
      ) : run.source && run.destination ? (
        <SourceDestinationPath source={run.source} destination={run.destination} size="xs" />
      ) : null}
      badges={<>
        <Badge variant={isTransform ? 'info' : 'subtle'} size="sm">{isTransform ? 'Transform' : 'Pipeline'}</Badge>
        <RunStatusBadge status={run.status} />
        <TriggerBadge trigger={run.trigger_type} />
        {run.is_stale && <Badge variant="warning" size="sm">{t('runs.stale')}</Badge>}
        {run.retry_of_run_id && <Link href={`/runs/${run.retry_of_run_id}?tab=summary`}>
          <Badge variant="subtle" size="sm">{t('runs.retryOf')} {run.retry_of_run_id.slice(0, 8)}</Badge>
        </Link>}
      </>}
      actions={<>
        {run.actions.can_cancel && <Button variant="secondary" loading={cancel.isPending}
          onClick={() => cancel.mutate()} leadingIcon={<Ban className="h-3.5 w-3.5" />}>
          {t('runs.cancel')}
        </Button>}
        {run.actions.can_retry && <Button variant="primary" loading={retry.isPending}
          onClick={() => retry.mutate()} leadingIcon={<RotateCcw className="h-3.5 w-3.5" />}>
          {t('runs.retry')}
        </Button>}
        <Button variant="ghost" onClick={copyContext} leadingIcon={<Copy className="h-3.5 w-3.5" />}>
          {t('runs.copySupport')}
        </Button>
      </>}
    />

    <DetailBody>
      {run.error && <div className="mb-4"><ErrorRemediationCard error={{
        code: run.error.code,
        message: run.error.summary ?? t('runs.failedDefault'),
        category: run.error.category,
        affects: resource?.name,
        action: run.error.remediation_action,
        technicalMessage: run.error.technical_message,
        traceId: run.trace_id,
        onAction: run.error.remediation_action === 'UPDATE_CREDENTIALS' && run.source
          ? () => router.push(`/sources/${run.source!.id}?tab=configuration`)
          : run.error.remediation_action === 'UPDATE_CREDENTIALS' && run.destination
            ? () => router.push(`/destinations/${run.destination!.id}?tab=configuration`)
            : run.error.remediation_action === 'REDISCOVER_SCHEMA' && run.pipeline
              ? () => router.push(`/pipelines/${run.pipeline!.id}?tab=schema`)
              : undefined,
        onRetry: run.actions.can_retry ? () => retry.mutate() : undefined,
      }} /></div>}

      <RunStats run={run} />
      <Tabs className="mb-4" value={tab} items={[
        { id: 'summary', label: t('runs.summary'), count: resultCount, href: hrefForTab('summary') },
        { id: 'attempts', label: t('runs.attempts'), count: run.attempts.length, href: hrefForTab('attempts') },
        { id: 'logs', label: t('runs.logs'), href: hrefForTab('logs') },
      ]} />

      {tab === 'summary' && (isTransform
        ? <TransformResults run={run} active={isActive} />
        : <PipelineResults run={run} active={isActive} />)}

      {tab === 'attempts' && <Card padded={false}>
        {run.attempts.length === 0 ? <div className="p-4"><EmptyState
          title={t(isActive ? 'runs.noAttemptsRunning' : 'runs.noAttempts')} compact
        /></div> : <ol className="divide-y divide-[rgb(var(--border-line))]">
          {run.attempts.map((attempt) => <li key={attempt.attempt_number} className="flex items-start gap-3 px-4 py-3">
            <span className="mt-0.5 flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-full bg-surface-2 text-tiny font-strong text-text-secondary">
              {attempt.attempt_number}
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <RunStatusBadge status={attempt.status} size="xs" />
                <span className="text-tiny text-text-quaternary">
                  {attempt.started_at ? formatDateTime(attempt.started_at, locale) : '-'}
                  {attempt.ended_at ? ` -> ${formatDateTime(attempt.ended_at, locale)}` : ''}
                </span>
                <span className="text-tiny text-text-quaternary">{formatDuration(attempt.duration_seconds)}</span>
              </div>
              {!isTransform && <p className="mt-0.5 text-caption text-text-secondary">
                {t('overview.recordsSuffix', { n: formatNumber(attempt.records_synced) })} / {formatBytes(attempt.bytes_synced)}
              </p>}
              {attempt.failure_summary && <p className="mt-1 text-caption text-danger">{attempt.failure_summary}</p>}
            </div>
          </li>)}
        </ol>}
      </Card>}

      {tab === 'logs' && <Card><LogViewer runId={runId} live={isActive} /></Card>}
    </DetailBody>
  </div>;
}

function RunStats({ run }: { run: RunDetail }) {
  const { t, locale } = useI18n();
  const transform = run.run_type === 'TRANSFORM';
  return <div className="mb-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-6">
    <StatTile label={t('common.status')} value={<RunStatusBadge status={run.status} />} />
    <StatTile label={t('runs.duration')} value={formatDuration(run.duration_seconds)}
              helper={run.started_at ? formatDateTime(run.started_at, locale) : undefined} />
    {transform ? <>
      <StatTile label={locale === 'vi' ? 'Model đã build' : 'Models built'} value={formatNumber(run.models_built)} />
      <StatTile label={locale === 'vi' ? 'Test đạt' : 'Tests passed'} value={formatNumber(run.tests_passed)}
                helper={(run.tests_failed ?? 0) > 0 ? `${run.tests_failed} failed` : undefined} />
      <StatTile label={locale === 'vi' ? 'Dòng bị ảnh hưởng' : 'Rows affected'} value={formatNumber(run.rows_affected)} />
    </> : <>
      <StatTile label={t('runs.records')} value={formatNumber(run.records_synced)} />
      <StatTile label={t('runs.bytes')} value={formatBytes(run.bytes_synced)} />
      <StatTile label={locale === 'vi' ? 'Stream' : 'Streams'} value={formatNumber(run.stream_stats.length)} />
    </>}
    <StatTile label={t('runs.trigger')} value={<TriggerBadge trigger={run.trigger_type} />}
              helper={run.triggered_by?.full_name ?? t('runs.bySystem')} />
  </div>;
}

function PipelineResults({ run, active }: { run: RunDetail; active: boolean }) {
  const { t, locale } = useI18n();
  return <Card title={t('runs.streamResults')} padded={false}>
    {run.stream_stats.length === 0 ? <EmptyState
      title={t(active ? 'runs.noStreamStatsRunning' : 'runs.noStreamStats')} compact
    /> : <div className="overflow-x-auto"><table className="w-full min-w-[560px] text-left">
      <thead><tr className="border-b border-[rgb(var(--border-line))] text-tiny uppercase tracking-[0.08em] text-text-quaternary">
        <th scope="col" className="px-4 py-2 font-emphasis">{t('stream.colName')}</th>
        <th scope="col" className="px-3 py-2 font-emphasis">{t('runs.records')}</th>
        <th scope="col" className="px-3 py-2 font-emphasis">{t('runs.bytes')}</th>
        <th scope="col" className="px-3 py-2 font-emphasis">{t('common.status')}</th>
      </tr></thead>
      <tbody className="divide-y divide-[rgb(var(--border-line))]">{run.stream_stats.map((stat) => <tr key={`${stat.namespace ?? ''}.${stat.stream_name}`}>
        <td className="px-4 py-2 text-caption text-text-primary">{stat.namespace ? `${stat.namespace}.` : ''}{stat.stream_name}</td>
        <td className="px-3 py-2 text-caption tabular-nums text-text-secondary">{formatNumber(stat.records_emitted)}</td>
        <td className="px-3 py-2 text-caption tabular-nums text-text-secondary">{formatBytes(stat.bytes_emitted)}</td>
        <td className="px-3 py-2"><Badge variant={stat.status === 'COMPLETED' ? 'success' : 'info'} size="xs">{streamStatus(locale)[stat.status] ?? stat.status}</Badge></td>
      </tr>)}</tbody>
    </table></div>}
  </Card>;
}

/** Airbyte's own words for a stream's outcome, in the user's. */
const streamStatus = (locale: string): Record<string, string> => locale === 'vi'
  ? { COMPLETED: 'Xong', RUNNING: 'Đang chạy', INCOMPLETE: 'Chưa xong', PENDING: 'Đang chờ' }
  : { COMPLETED: 'Done', RUNNING: 'Running', INCOMPLETE: 'Incomplete', PENDING: 'Pending' };

/** dbt calls everything it runs a node. A user calls them tables and checks. */
const nodeKind = (locale: string): Record<string, string> => locale === 'vi'
  ? { MODEL: 'Bảng', TEST: 'Kiểm tra', SEED: 'Dữ liệu nạp sẵn', SNAPSHOT: 'Bản chụp' }
  : { MODEL: 'Table', TEST: 'Check', SEED: 'Seed data', SNAPSHOT: 'Snapshot' };

function TransformResults({ run, active }: { run: RunDetail; active: boolean }) {
  const { locale } = useI18n();
  const kind = nodeKind(locale);
  const head = locale === 'vi'
    ? ['Bảng', 'Loại', 'Trạng thái', 'Bảng trong kho', 'Thời gian', 'Ghi chú']
    : ['Table', 'Kind', 'Status', 'Warehouse table', 'Duration', 'Message'];
  return <Card title={locale === 'vi' ? 'Kết quả từng bảng' : 'Results by table'} padded={false}>
    {run.transform_nodes.length === 0 ? <EmptyState
      title={active
        ? (locale === 'vi' ? 'Đang chuẩn bị kết quả.' : 'Preparing results.')
        : (locale === 'vi' ? 'Lần chạy này không có kết quả nào.' : 'This run produced no results.')}
      compact
    /> : <div className="overflow-x-auto"><table className="w-full min-w-[720px] text-left">
      <thead><tr className="border-b border-[rgb(var(--border-line))] text-tiny uppercase tracking-[0.08em] text-text-quaternary">
        {head.map((label, index) => (
          <th key={label} scope="col"
            className={cn('py-2 font-emphasis', index === 0 || index === head.length - 1 ? 'px-4' : 'px-3')}>
            {label}
          </th>
        ))}
      </tr></thead>
      <tbody className="divide-y divide-[rgb(var(--border-line))]">{run.transform_nodes.map((node, index) => <tr key={`${node.resource_type}.${node.name}.${index}`}>
        <td className="px-4 py-2 font-mono text-caption text-text-primary">{node.name}</td>
        <td className="px-3 py-2"><Badge variant="subtle" size="xs">{kind[node.resource_type] ?? node.resource_type}</Badge></td>
        <td className="px-3 py-2"><RunStatusBadge status={node.status} size="xs" /></td>
        <td className="max-w-[220px] truncate px-3 py-2 font-mono text-tiny text-text-secondary" title={node.relation_name ?? ''}>{node.relation_name ?? '-'}</td>
        <td className="px-3 py-2 text-caption tabular-nums text-text-secondary">{formatDuration(node.execution_time)}</td>
        <td className="max-w-[300px] truncate px-4 py-2 text-caption text-text-tertiary" title={node.message ?? ''}>{node.message ?? '-'}</td>
      </tr>)}</tbody>
    </table></div>}
  </Card>;
}
