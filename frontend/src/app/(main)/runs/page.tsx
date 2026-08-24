'use client';

import * as React from 'react';
import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, PlayCircle } from 'lucide-react';

import { runApi } from '@/lib/api';
import { qk } from '@/lib/queryKeys';
import { cn } from '@/lib/utils';
import { formatBytes, formatDuration, formatNumber, formatRelative } from '@/lib/format';
import { useWorkspaceId } from '@/hooks/use-current-user';
import { useI18n } from '@/providers/LanguageProvider';
import { Select } from '@/components/ui/Input';
import { Button } from '@/components/ui/Button';
import { EmptyState, ErrorState, TableSkeleton } from '@/components/ui/Feedback';
import { ModuleOverview, PageListLayout } from '@/components/layout/PageLayout';
import { RunStatusBadge, TriggerBadge } from '@/components/integrations/Badges';

const ERROR_CATEGORIES = [
  'AUTHENTICATION', 'NETWORK', 'PERMISSION', 'CONFIGURATION', 'SCHEMA',
  'RATE_LIMIT', 'DESTINATION_WRITE', 'SOURCE_READ', 'ENGINE', 'TIMEOUT', 'UNKNOWN',
];

export default function RunsPage() {
  const { t, tf, locale } = useI18n();
  const workspaceId = useWorkspaceId();
  const searchParams = useSearchParams();

  const [status, setStatus] = React.useState(searchParams.get('status') ?? '');
  const [trigger, setTrigger] = React.useState('');
  const [category, setCategory] = React.useState('');
  const [page, setPage] = React.useState(0);
  const pipelineId = searchParams.get('pipeline_id') ?? undefined;
  const limit = 50;

  const filters = {
    pipeline_id: pipelineId,
    status: status || undefined,
    trigger_type: trigger || undefined,
    error_category: category || undefined,
    limit,
    offset: page * limit,
  };

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: qk.runs(workspaceId, filters),
    queryFn: () => runApi.list(filters),
    refetchInterval: 15_000,
  });

  const items = data?.items ?? [];
  const summary = data?.summary ?? {};
  const total = data?.page.total ?? 0;

  return (
    <PageListLayout
      title={t('runs.title')}
      description={t('runs.subtitle')}
      searchable={false}
      overview={
        <ModuleOverview
          stats={[
            { label: t('pipelines.statTotal'), value: summary.total ?? 0 },
            { label: t('runs.statSucceeded'), value: summary.succeeded ?? 0, tone: 'success' },
            { label: t('runs.statFailed'), value: summary.failed ?? 0,
              tone: (summary.failed ?? 0) > 0 ? 'danger' : 'default' },
            { label: t('runs.statRunning'),
              value: (summary.running ?? 0) + (summary.starting ?? 0) },
            { label: t('runs.statQueued'), value: summary.queued ?? 0 },
            { label: t('runs.statCancelled'), value: summary.cancelled ?? 0 },
          ]}
        />
      }
      filters={
        <>
          <Select size="sm" className="w-44" value={status}
                  aria-label={t('runs.filterStatus', { value: '' })}
                  onChange={(e) => { setStatus(e.target.value); setPage(0); }}>
            <option value="">{t('runs.filterStatus', { value: t('common.all') })}</option>
            <option value="ACTIVE">{t('runs.filterActive')}</option>
            <option value="SUCCEEDED">{t('run.SUCCEEDED')}</option>
            <option value="FAILED">{t('run.FAILED')}</option>
            <option value="CANCELLED">{t('run.CANCELLED')}</option>
            <option value="TIMED_OUT">{t('run.TIMED_OUT')}</option>
            <option value="QUEUED">{t('run.QUEUED')}</option>
          </Select>
          <Select size="sm" className="w-40" value={trigger}
                  aria-label={t('runs.filterTrigger', { value: '' })}
                  onChange={(e) => { setTrigger(e.target.value); setPage(0); }}>
            <option value="">{t('runs.filterTrigger', { value: t('common.all') })}</option>
            <option value="MANUAL">{t('trigger.MANUAL')}</option>
            <option value="SCHEDULE">{t('trigger.SCHEDULE')}</option>
            <option value="RETRY">{t('trigger.RETRY')}</option>
            <option value="SYSTEM">{t('trigger.SYSTEM')}</option>
          </Select>
          <Select size="sm" className="w-48" value={category}
                  aria-label={t('runs.filterCategory', { value: '' })}
                  onChange={(e) => { setCategory(e.target.value); setPage(0); }}>
            <option value="">{t('runs.filterCategory', { value: t('common.all') })}</option>
            {ERROR_CATEGORIES.map((value) => (
              <option key={value} value={value}>{value}</option>
            ))}
          </Select>
          {pipelineId && (
            <Link href="/runs" className="text-caption text-brand hover:underline">
              {t('runs.clearPipelineFilter')}
            </Link>
          )}
        </>
      }
    >
      {error ? (
        <ErrorState title={t('common.errorTitle')} message={(error as Error).message}
                    onRetry={() => refetch()} />
      ) : isLoading ? (
        <TableSkeleton rows={8} columns={7} />
      ) : items.length === 0 ? (
        <EmptyState icon={PlayCircle} title={t('runs.empty')} />
      ) : (
        <>
          <div className="overflow-hidden rounded-lg border border-[rgb(var(--border-line))] bg-surface-1">
            <div className="overflow-x-auto">
              <table className="w-full min-w-[1040px] text-left">
                <thead>
                  <tr className="border-b border-[rgb(var(--border-line))] text-tiny uppercase tracking-[0.08em] text-text-quaternary">
                    <th scope="col" className="px-4 py-2.5 font-emphasis">Run</th>
                    <th scope="col" className="px-3 py-2.5 font-emphasis">{t('runs.pipeline')}</th>
                    <th scope="col" className="px-3 py-2.5 font-emphasis">{t('common.status')}</th>
                    <th scope="col" className="px-3 py-2.5 font-emphasis">{t('runs.trigger')}</th>
                    <th scope="col" className="px-3 py-2.5 font-emphasis">{t('runs.started')}</th>
                    <th scope="col" className="px-3 py-2.5 font-emphasis">{t('runs.duration')}</th>
                    <th scope="col" className="px-3 py-2.5 font-emphasis">{t('runs.records')}</th>
                    <th scope="col" className="px-3 py-2.5 font-emphasis">{t('runs.bytes')}</th>
                    <th scope="col" className="px-4 py-2.5 font-emphasis">{t('runs.error')}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[rgb(var(--border-line))]">
                  {items.map((run) => (
                    <tr key={run.id} className="transition-colors hover:bg-surface-2/60">
                      <td className="px-4 py-2.5">
                        <Link href={`/runs/${run.id}`}
                              className="inline-block py-1 font-mono text-caption text-brand hover:underline">
                          {run.short_id}
                        </Link>
                        {run.retry_of_run_id && (
                          <span className="ml-1.5 text-tiny text-text-quaternary">
                            ({t('runs.retryOf')})
                          </span>
                        )}
                      </td>
                      <td className="px-3 py-2.5">
                        {run.pipeline ? (
                          <Link href={`/pipelines/${run.pipeline.id}`}
                                className="inline-block py-1 text-caption text-text-primary hover:text-brand">
                            {run.pipeline.name}
                          </Link>
                        ) : '—'}
                      </td>
                      <td className="px-3 py-2.5">
                        <span className="inline-flex items-center gap-1.5">
                          <RunStatusBadge status={run.status} size="xs" />
                          {run.is_stale && (
                            <span title={t('runs.stale')}>
                              <AlertTriangle className="h-3 w-3 text-warning" />
                            </span>
                          )}
                        </span>
                        {run.queue_reason && (
                          <span className="mt-0.5 block text-tiny text-text-quaternary">
                            {run.queue_reason === 'WAITING_GLOBAL_CAPACITY'
                              ? t('runs.waitingGlobal')
                              : t('runs.waitingWorkspace')}
                          </span>
                        )}
                      </td>
                      <td className="px-3 py-2.5"><TriggerBadge trigger={run.trigger_type} /></td>
                      <td className="px-3 py-2.5 text-caption text-text-tertiary">
                        {formatRelative(run.started_at ?? run.created_at, locale)}
                      </td>
                      <td className="px-3 py-2.5 text-caption tabular-nums text-text-secondary">
                        {formatDuration(run.duration_seconds)}
                      </td>
                      <td className="px-3 py-2.5 text-caption tabular-nums text-text-secondary">
                        {formatNumber(run.records_synced)}
                      </td>
                      <td className="px-3 py-2.5 text-caption tabular-nums text-text-secondary">
                        {formatBytes(run.bytes_synced)}
                      </td>
                      <td className="max-w-[240px] px-4 py-2.5">
                        {run.error ? (
                          <span
                            className={cn(
                              'block truncate text-caption',
                              // A cancellation is an outcome the user chose, not a fault.
                              run.error.category === 'CANCELLED'
                                ? 'text-text-tertiary' : 'text-danger',
                            )}
                            title={run.error.summary ?? run.error.category ?? ''}
                          >
                            {tf(
                              [`errorCategory.${run.error.category ?? 'UNKNOWN'}`],
                              run.error.category ?? t('errorCategory.UNKNOWN'),
                            )}
                          </span>
                        ) : (
                          <span className="text-caption text-text-quaternary">—</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {total > limit && (
            <div className="mt-3 flex items-center justify-between">
              <span className="text-caption text-text-tertiary">
                {t('common.showing', { n: items.length, total })}
              </span>
              <div className="flex gap-2">
                <Button size="sm" variant="secondary" disabled={page === 0}
                        onClick={() => setPage((p) => Math.max(0, p - 1))}>
                  {t('common.prevPage')}
                </Button>
                <Button size="sm" variant="secondary" disabled={!data?.page.has_more}
                        onClick={() => setPage((p) => p + 1)}>
                  {t('common.nextPage')}
                </Button>
              </div>
            </div>
          )}
        </>
      )}
    </PageListLayout>
  );
}
