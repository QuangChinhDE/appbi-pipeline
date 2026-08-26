'use client';

import { CheckCircle2, Clock, CircleDashed, RefreshCw, Search, XCircle } from 'lucide-react';
import * as React from 'react';

import { Card, ModuleOverview } from '@/components/layout/PageLayout';
import { Button } from '@/components/ui/Button';
import { EmptyState } from '@/components/ui/Feedback';
import { Input } from '@/components/ui/Input';
import { Menu } from '@/components/ui/Menu';
import { formatBytes, formatNumber, formatRelative } from '@/lib/format';
import type { Locale } from '@/lib/i18n';
import type { PipelineStreamView } from '@/lib/types';
import { cn } from '@/lib/utils';
import { useI18n } from '@/providers/LanguageProvider';

/**
 * Per-stream health, which is the question this page is actually asked.
 *
 * The old overview answered at pipeline granularity — one status, one record
 * count, one timestamp — and that hides the failure people care about most: a
 * pipeline that reports SUCCEEDED while one of its twenty-five streams read
 * nothing. `last_sync` is per stream for exactly that reason.
 */
export function StatusTab({
  streams, onOpenStream, onRefreshStream, onClearStream, onManageStreams,
}: {
  streams: PipelineStreamView[];
  onOpenStream: (stream: PipelineStreamView) => void;
  onRefreshStream: (stream: PipelineStreamView) => void;
  onClearStream: (stream: PipelineStreamView) => void;
  onManageStreams: () => void;
}) {
  const { t, locale } = useI18n();
  const [query, setQuery] = React.useState('');

  const active = streams.filter((stream) => stream.selected);
  const term = query.trim().toLowerCase();
  const shown = term
    ? active.filter((stream) => stream.name.toLowerCase().includes(term))
    : active;
  const synced = active.filter((stream) => stream.last_sync?.status === 'COMPLETED').length;
  const pending = active.filter((stream) => !stream.last_sync).length;
  const failed = active.filter((stream) => stream.last_sync?.status === 'FAILED').length;
  const latestRecords = active.reduce(
    (total, stream) => total + (stream.last_sync?.records_loaded ?? 0), 0,
  );

  return (
    <div className="space-y-3">
      <ModuleOverview stats={[
        { label: t('pipelines.summary.active'), value: formatNumber(active.length) },
        { label: t('pipelines.summary.synced'), value: formatNumber(synced), tone: 'success' },
        {
          label: t('pipelines.summary.attention'), value: formatNumber(failed + pending),
          tone: failed > 0 ? 'danger' : pending > 0 ? 'warning' : 'default',
        },
        { label: t('pipelines.summary.latestRecords'), value: formatNumber(latestRecords) },
      ]} />
      <Card
        title={t('pipelines.activeStreams')}
        padded={false}
        action={
          <div className="w-[min(320px,45vw)]">
            <Input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder={t('common.search')}
              aria-label={t('pipelines.searchStreams')}
              leadingIcon={<Search className="h-3.5 w-3.5" />}
            />
          </div>
        }
      >
        {shown.length === 0 ? (
          <div className="p-6">
            <EmptyState
              title={term ? t('pipelines.noStreamMatch') : t('pipelines.noActiveStreams')}
              description={term ? t('pipelines.noStreamMatchHelp') : t('pipelines.noActiveStreamsHelp')}
              action={term ? (
                <Button size="sm" variant="secondary" onClick={() => setQuery('')}>
                  {t('pipelines.clearSearch')}
                </Button>
              ) : (
                <Button size="sm" variant="primary" onClick={onManageStreams}>
                  {t('pipelines.manageStreams')}
                </Button>
              )}
            />
          </div>
        ) : (
          <div className="overflow-x-auto">
          <table className="w-full min-w-[720px] text-left">
            <thead>
              <tr className="border-b border-[rgb(var(--border-line))] text-tiny uppercase tracking-[0.08em] text-text-tertiary">
                <th scope="col" className="px-4 py-2 font-normal">{t('pipelines.streamStatus')}</th>
                <th scope="col" className="px-3 py-2 font-normal">{t('pipelines.streamName')}</th>
                <th scope="col" className="px-3 py-2 font-normal">{t('pipelines.latestSync')}</th>
                <th scope="col" className="px-3 py-2 font-normal">
                  <span className="inline-flex items-center gap-1">
                    {t('pipelines.freshAsOf')}
                    <Clock className="h-3 w-3" aria-hidden />
                  </span>
                </th>
                <th scope="col" className="w-10 px-3 py-2" />
              </tr>
            </thead>
            <tbody>
              {shown.map((stream) => (
                <StreamRow
                  key={stream.id}
                  stream={stream}
                  locale={locale}
                  t={t}
                  onOpen={() => onOpenStream(stream)}
                  onRefresh={() => onRefreshStream(stream)}
                  onClear={() => onClearStream(stream)}
                />
              ))}
            </tbody>
          </table>
          </div>
        )}
      </Card>
    </div>
  );
}

function StreamRow({
  stream, locale, t, onOpen, onRefresh, onClear,
}: {
  stream: PipelineStreamView;
  locale: Locale;
  t: (key: string, vars?: Record<string, string | number>) => string;
  onOpen: () => void;
  onRefresh: () => void;
  onClear: () => void;
}) {
  const last = stream.last_sync;
  return (
    <tr className="border-b border-[rgb(var(--border-line))] last:border-0 hover:bg-surface-2/40">
      <td className="px-4 py-2.5">
        <StreamStatus status={last?.status ?? null} recordsLoaded={last?.records_loaded} t={t} />
      </td>
      <td className="px-3 py-2.5">
        <button
          type="button"
          onClick={onOpen}
          className="max-w-[320px] truncate text-caption text-text-primary hover:text-brand hover:underline"
        >
          {stream.name}
        </button>
        {stream.namespace && (
          <p className="truncate text-tiny text-text-quaternary">{stream.namespace}</p>
        )}
      </td>
      <td className="px-3 py-2.5 text-caption tabular-nums text-text-secondary">
        {last
          ? t('pipelines.recordsLoaded', { n: formatNumber(last.records_loaded) })
          : <span className="text-text-quaternary">—</span>}
        {last && last.bytes_loaded > 0 && (
          <span className="ml-1.5 text-tiny text-text-quaternary">
            {formatBytes(last.bytes_loaded)}
          </span>
        )}
      </td>
      <td className="px-3 py-2.5 text-caption text-text-tertiary">
        {last?.synced_at
          ? formatRelative(last.synced_at, locale)
          : <span className="text-text-quaternary">{t('pipelines.neverSynced')}</span>}
      </td>
      <td className="px-3 py-2.5 text-right">
        <Menu
          label={t('pipelines.streamActions', { name: stream.name })}
          items={[
            { id: 'details', label: t('pipelines.streamMenu.details'), onSelect: onOpen },
            {
              id: 'refresh',
              label: t('pipelines.streamMenu.refresh'),
              icon: <RefreshCw className="h-3.5 w-3.5" />,
              onSelect: onRefresh,
            },
            {
              id: 'clear',
              label: t('pipelines.streamMenu.clear'),
              destructive: true,
              onSelect: onClear,
            },
          ]}
        />
      </td>
    </tr>
  );
}

/**
 * A stream's state, in the words the operator uses.
 *
 * Deliberately not a raw engine status: `COMPLETED` is engine vocabulary and
 * means "this stream finished", which a reader hears as "the sync is done".
 */
function StreamStatus({
  status, recordsLoaded, t,
}: {
  status: string | null;
  recordsLoaded?: number;
  t: (key: string) => string;
}) {
  const normalised = (status ?? '').toUpperCase();
  const map: Record<string, { icon: React.ReactNode; label: string; tone: string }> = {
    COMPLETED: {
      icon: <CheckCircle2 className="h-4 w-4" />,
      label: t('pipelines.streamState.synced'),
      tone: 'text-success',
    },
    RUNNING: {
      icon: <RefreshCw className="h-4 w-4 animate-spin" />,
      label: t('pipelines.streamState.syncing'),
      tone: 'text-info',
    },
    FAILED: {
      icon: <XCircle className="h-4 w-4" />,
      label: t('pipelines.streamState.failed'),
      tone: 'text-danger',
    },
  };
  const state = map[normalised] ?? {
    icon: <CircleDashed className="h-4 w-4" />,
    label: t('pipelines.streamState.pending'),
    tone: 'text-text-quaternary',
  };
  const shown = normalised === 'COMPLETED' && recordsLoaded === 0
    ? { ...state, label: t('pipelines.streamState.noNewData') }
    : state;
  return (
    <span className={cn('inline-flex items-center gap-1.5 text-caption', shown.tone)}>
      {shown.icon}
      {shown.label}
    </span>
  );
}
