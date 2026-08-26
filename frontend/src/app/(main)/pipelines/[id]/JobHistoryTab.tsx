'use client';

import { Ban, CheckCircle2, Link2, Loader2, ScrollText, XCircle } from 'lucide-react';
import Link from 'next/link';
import * as React from 'react';

import { Card, ModuleOverview } from '@/components/layout/PageLayout';
import { Button } from '@/components/ui/Button';
import { EmptyState, Spinner } from '@/components/ui/Feedback';
import { Select } from '@/components/ui/Input';
import { Menu } from '@/components/ui/Menu';
import { formatBytes, formatDateTime, formatDuration, formatNumber } from '@/lib/format';
import type { Locale } from '@/lib/i18n';
import type { PipelineMetrics, Run } from '@/lib/types';
import { cn } from '@/lib/utils';
import { toastSuccess } from '@/hooks/use-toast';
import { useRouter } from 'next/navigation';
import { useI18n } from '@/providers/LanguageProvider';

export const OUTCOMES = ['ALL', 'SUCCEEDED', 'FAILED', 'CANCELLED'] as const;
export type JobOutcomeFilter = (typeof OUTCOMES)[number];

/**
 * One line per run, reading as a sentence rather than as a row of columns.
 *
 * "Sync Succeeded / 50.11 KB · 33 records · 37s" is scannable in a way that
 * four separate numeric columns are not: the eye follows one line and stops at
 * the one that is wrong, instead of comparing four columns down the page.
 */
export function JobHistoryTab({
  runs, total, isLoading, loadingMore, hasMore, outcome, metrics, syncing,
  onOutcome, onLoadMore, onSyncNow,
}: {
  runs: Run[];
  total: number;
  isLoading: boolean;
  loadingMore: boolean;
  hasMore: boolean;
  outcome: JobOutcomeFilter;
  metrics: PipelineMetrics;
  syncing: boolean;
  onOutcome: (value: JobOutcomeFilter) => void;
  onLoadMore: () => void;
  onSyncNow: () => void;
}) {
  const { t, locale } = useI18n();
  const shown = runs;

  return (
    <div className="space-y-3">
      <ModuleOverview stats={[
        {
          label: t('pipelines.jobs.runs30d'),
          value: formatNumber(metrics.total_runs_30d),
        },
        {
          label: t('pipelines.jobs.successRate30d'),
          value: metrics.success_rate_30d === null ? '—' : `${metrics.success_rate_30d}%`,
          tone: metrics.consecutive_failures > 0 ? 'warning' : 'success',
        },
        {
          label: t('pipelines.jobs.records30d'),
          value: formatNumber(metrics.records_synced_30d),
        },
        {
          label: t('pipelines.jobs.averageDuration'),
          value: formatDuration(metrics.average_duration_seconds),
        },
      ]} />
      <Card
      title={t('pipelines.jobHistory')}
      padded={false}
      action={
        <span className="text-tiny tabular-nums text-text-tertiary">
          {/* "N shown of M" rather than just M. The list is paged, so a bare
              total describes something the reader cannot see and makes a
              filtered view look like it lost rows. */}
          {runs.length < total
            ? t('pipelines.jobs.countOf', {
                n: formatNumber(runs.length), total: formatNumber(total) })
            : t('pipelines.jobs.count', { n: formatNumber(total) })}
        </span>
      }
    >
      <div className="border-b border-[rgb(var(--border-line))] px-4 py-2.5">
        <div className="w-[220px]">
          <Select
            value={outcome}
            aria-label={t('pipelines.jobs.filterLabel')}
            // Filtering happens on the server. Over the loaded page only, a
            // filter answers "failed jobs among the last 50", which is not the
            // question anyone asks it.
            onChange={(event) => onOutcome(event.target.value as JobOutcomeFilter)}
          >
            {OUTCOMES.map((value) => (
              <option key={value} value={value}>
                {t(`pipelines.jobs.filter.${value.toLowerCase()}`)}
              </option>
            ))}
          </Select>
        </div>
      </div>

      {isLoading ? (
        <div className="p-6"><Spinner label={t('common.loading')} /></div>
      ) : shown.length === 0 ? (
        <div className="p-6">
          <EmptyState
            title={t('pipelines.jobs.empty')}
            description={t('pipelines.jobs.emptyHelp')}
            action={outcome === 'ALL' ? (
              <Button size="sm" variant="primary" loading={syncing} onClick={onSyncNow}>
                {t('pipelines.syncNow')}
              </Button>
            ) : (
              <Button size="sm" variant="secondary" onClick={() => onOutcome('ALL')}>
                {t('pipelines.jobs.clearFilter')}
              </Button>
            )}
          />
        </div>
      ) : (
        <>
          <ul>
            {shown.map((run) => (
              <JobRow key={run.id} run={run} locale={locale} t={t} />
            ))}
          </ul>
          {hasMore && (
            <div className="border-t border-[rgb(var(--border-line))] p-3 text-center">
              <Button size="sm" variant="secondary" loading={loadingMore} onClick={onLoadMore}>
                {t('pipelines.jobs.loadMore')}
              </Button>
            </div>
          )}
        </>
      )}
      </Card>
    </div>
  );
}

function JobRow({
  run, locale, t,
}: {
  run: Run;
  locale: Locale;
  t: (key: string, vars?: Record<string, string | number>) => string;
}) {
  const router = useRouter();
  const facts = [
    run.bytes_synced != null ? formatBytes(run.bytes_synced) : null,
    run.records_synced != null
      ? t('pipelines.jobs.records', { n: formatNumber(run.records_synced) })
      : null,
    run.duration_seconds != null ? formatDuration(run.duration_seconds) : null,
  ].filter(Boolean) as string[];

  return (
    <li className="flex items-center gap-3 border-b border-[rgb(var(--border-line))] px-4 py-3 last:border-0 hover:bg-surface-2/40">
      <JobOutcome status={run.status} />
      <div className="min-w-0 flex-1">
        <Link
          href={`/runs/${run.id}`}
          className="text-caption font-strong text-text-primary hover:text-brand hover:underline"
        >
          {t(`pipelines.jobs.outcome.${run.status.toLowerCase()}`)}
        </Link>
        <p className="mt-0.5 truncate text-tiny tabular-nums text-text-tertiary">
          {facts.join('  ·  ') || '—'}
          {run.error?.summary && (
            <span className="ml-2 text-danger">{run.error.summary}</span>
          )}
        </p>
      </div>
      <span className="flex-shrink-0 text-tiny tabular-nums text-text-tertiary">
        {formatDateTime(run.started_at ?? run.created_at, locale)}
      </span>
      <Menu
        label={t('pipelines.jobs.actions')}
        items={[
          {
            id: 'copy',
            label: t('pipelines.jobs.copyLink'),
            icon: <Link2 className="h-3.5 w-3.5" />,
            onSelect: () => {
              const url = `${window.location.origin}/runs/${run.id}`;
              void navigator.clipboard?.writeText(url).then(
                () => toastSuccess(t('pipelines.jobs.linkCopied')),
                () => undefined,
              );
            },
          },
          {
            id: 'logs',
            label: t('pipelines.jobs.viewLogs'),
            icon: <ScrollText className="h-3.5 w-3.5" />,
            onSelect: () => router.push(`/runs/${run.id}?tab=logs`),
          },
        ]}
      />
    </li>
  );
}

function JobOutcome({ status }: { status: string }) {
  const map: Record<string, { icon: React.ReactNode; tone: string }> = {
    SUCCEEDED: { icon: <CheckCircle2 className="h-4 w-4" />, tone: 'text-success' },
    PARTIAL_SUCCESS: { icon: <CheckCircle2 className="h-4 w-4" />, tone: 'text-warning' },
    FAILED: { icon: <XCircle className="h-4 w-4" />, tone: 'text-danger' },
    FAILED_TO_START: { icon: <XCircle className="h-4 w-4" />, tone: 'text-danger' },
    TIMED_OUT: { icon: <XCircle className="h-4 w-4" />, tone: 'text-danger' },
    CANCELLED: { icon: <Ban className="h-4 w-4" />, tone: 'text-text-quaternary' },
    RUNNING: { icon: <Loader2 className="h-4 w-4 animate-spin" />, tone: 'text-info' },
    STARTING: { icon: <Loader2 className="h-4 w-4 animate-spin" />, tone: 'text-info' },
    QUEUED: { icon: <Loader2 className="h-4 w-4" />, tone: 'text-text-tertiary' },
    CANCEL_REQUESTED: { icon: <Ban className="h-4 w-4" />, tone: 'text-warning' },
  };
  const state = map[status] ?? { icon: <Loader2 className="h-4 w-4" />, tone: 'text-text-quaternary' };
  return <span className={cn('flex-shrink-0', state.tone)} aria-hidden>{state.icon}</span>;
}
