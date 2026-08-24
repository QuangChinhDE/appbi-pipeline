'use client';

import * as React from 'react';
import Link from 'next/link';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ChevronRight, GitBranch, Pause, Play, Plus } from 'lucide-react';

import { ApiError, pipelineApi } from '@/lib/api';
import { qk } from '@/lib/queryKeys';
import { describeSchedule, formatRelative } from '@/lib/format';
import { useWorkspaceId } from '@/hooks/use-current-user';
import { usePermissions } from '@/hooks/use-permissions';
import { toastError, toastSuccess } from '@/hooks/use-toast';
import { useI18n } from '@/providers/LanguageProvider';
import { Button } from '@/components/ui/Button';
import { Select } from '@/components/ui/Input';
import { EmptyState, ErrorState, TableSkeleton } from '@/components/ui/Feedback';
import { ModuleOverview, PageListLayout } from '@/components/layout/PageLayout';
import { HealthBadge, PipelineStatusBadge, RunStatusBadge } from '@/components/integrations/Badges';
import { SourceDestinationPath } from '@/components/integrations/ConnectorIcon';

const QUICK_FILTERS: { id: string; labelKey: string; health?: string; status?: string }[] = [
  { id: '', labelKey: 'pipelines.filterAll' },
  { id: 'FAILED', labelKey: 'pipelines.filterFailed', health: 'FAILED' },
  { id: 'RUNNING', labelKey: 'pipelines.filterRunning', health: 'RUNNING' },
  { id: 'ACTION_REQUIRED', labelKey: 'pipelines.filterAction', health: 'ACTION_REQUIRED' },
  { id: 'PAUSED', labelKey: 'pipelines.filterPaused', status: 'PAUSED' },
  { id: 'NEVER_RUN', labelKey: 'pipelines.filterNeverRun', health: 'NEVER_RUN' },
];

export default function PipelinesPage() {
  const { t, locale } = useI18n();
  const workspaceId = useWorkspaceId();
  const queryClient = useQueryClient();
  const { can } = usePermissions();

  const [search, setSearch] = React.useState('');
  const [debounced, setDebounced] = React.useState('');
  const [quick, setQuick] = React.useState('');

  React.useEffect(() => {
    const timer = setTimeout(() => setDebounced(search), 300);
    return () => clearTimeout(timer);
  }, [search]);

  const active = QUICK_FILTERS.find((f) => f.id === quick);
  const filters = {
    q: debounced || undefined,
    health: active?.health,
    status: active?.status,
  };

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: qk.pipelines(workspaceId, filters),
    queryFn: () => pipelineApi.list(filters),
    // Cheap poll so a RUNNING row updates without a manual refresh.
    refetchInterval: 15_000,
  });

  const [busyId, setBusyId] = React.useState<string | null>(null);

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['workspace', workspaceId] });

  const runNow = useMutation({
    mutationFn: (id: string) => pipelineApi.run(id, `manual-${id}-${Date.now()}`),
    onMutate: (id) => setBusyId(id),
    onSettled: () => setBusyId(null),
    onSuccess: () => { toastSuccess(t('pipelines.queued')); invalidate(); },
    onError: (caught) => toastError(caught),
  });

  const togglePause = useMutation({
    mutationFn: ({ id, paused }: { id: string; paused: boolean }) =>
      paused ? pipelineApi.enable(id) : pipelineApi.pause(id),
    onMutate: ({ id }) => setBusyId(id),
    onSettled: () => setBusyId(null),
    onSuccess: () => { toastSuccess(t('pipelines.scheduleUpdated')); invalidate(); },
    onError: (caught) => toastError(caught),
  });

  const items = data?.items ?? [];
  const summary = data?.summary ?? {};

  return (
    <PageListLayout
      title={t('pipelines.title')}
      description={t('pipelines.subtitle')}
      searchValue={search}
      onSearchChange={setSearch}
      searchPlaceholder={t('pipelines.search')}
      action={
        can('pipelines', 'create') ? (
          <Link href="/pipelines/new">
            <Button variant="primary" leadingIcon={<Plus className="h-3.5 w-3.5" />}>
              {t('pipelines.add')}
            </Button>
          </Link>
        ) : null
      }
      overview={
        <ModuleOverview
          stats={[
            { label: t('pipelines.statTotal'), value: summary.total ?? 0 },
            { label: t('pipelines.statHealthy'), value: summary.healthy ?? 0, tone: 'success' },
            { label: t('pipelines.statRunning'), value: summary.running ?? 0 },
            { label: t('pipelines.statFailed'), value: summary.failed ?? 0,
              tone: (summary.failed ?? 0) > 0 ? 'danger' : 'default' },
            { label: t('pipelines.statAction'), value: summary.action_required ?? 0,
              tone: (summary.action_required ?? 0) > 0 ? 'warning' : 'default' },
            { label: t('pipelines.statPaused'), value: summary.paused ?? 0 },
          ]}
        />
      }
      filters={
        <div className="flex flex-wrap gap-1.5">
          {QUICK_FILTERS.map((filter) => (
            <button
              key={filter.id}
              type="button"
              aria-pressed={quick === filter.id}
              onClick={() => setQuick(filter.id)}
              className={
                quick === filter.id
                  ? 'rounded-full border border-brand bg-brand/10 px-2.5 py-1 text-tiny font-emphasis text-brand'
                  : 'rounded-full border border-[rgb(var(--border-line))] px-2.5 py-1 text-tiny font-emphasis text-text-tertiary hover:text-text-primary'
              }
            >
              {t(filter.labelKey)}
            </button>
          ))}
        </div>
      }
    >
      {error ? (
        <ErrorState title={t('common.errorTitle')} message={(error as Error).message}
                    onRetry={() => refetch()} />
      ) : isLoading ? (
        <TableSkeleton rows={5} columns={7} />
      ) : items.length === 0 ? (
        <EmptyState
          icon={GitBranch}
          title={debounced || quick ? t('common.noResults') : t('pipelines.emptyTitle')}
          description={debounced || quick ? undefined : t('pipelines.empty')}
          action={
            can('pipelines', 'create') && !debounced && !quick ? (
              <Link href="/pipelines/new">
                <Button variant="primary" leadingIcon={<Plus className="h-3.5 w-3.5" />}>
                  {t('pipelines.add')}
                </Button>
              </Link>
            ) : null
          }
        />
      ) : (
        <div className="overflow-hidden rounded-lg border border-[rgb(var(--border-line))] bg-surface-1">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[1000px] text-left">
              <thead>
                <tr className="border-b border-[rgb(var(--border-line))] text-tiny uppercase tracking-[0.08em] text-text-quaternary">
                  <th scope="col" className="px-4 py-2.5 font-emphasis">Pipeline</th>
                  <th scope="col" className="px-3 py-2.5 font-emphasis">{t('common.status')}</th>
                  <th scope="col" className="px-3 py-2.5 font-emphasis">{t('pipelines.syncHealth')}</th>
                  <th scope="col" className="whitespace-nowrap px-3 py-2.5 font-emphasis">
                    {t('pipelines.schedule')}
                  </th>
                  <th scope="col" className="whitespace-nowrap px-3 py-2.5 font-emphasis">
                    {t('pipelines.lastRun')}
                  </th>
                  <th scope="col" className="whitespace-nowrap px-3 py-2.5 font-emphasis">
                    {t('pipelines.nextRun')}
                  </th>
                  <th scope="col" className="px-3 py-2.5 text-right font-emphasis">
                    {t('pipelines.streams')}
                  </th>
                  <th
                    scope="col"
                    className="sticky right-0 bg-surface-1 px-4 py-2.5 text-right font-emphasis"
                  >
                    {t('common.actions')}
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[rgb(var(--border-line))]">
                {items.map((pipeline) => {
                  const paused = pipeline.status === 'PAUSED';
                  return (
                    <tr key={pipeline.id} className="transition-colors hover:bg-surface-2/60">
                      <td className="px-4 py-2.5">
                        <Link href={`/pipelines/${pipeline.id}`} className="block min-w-0">
                          <span className="block truncate text-caption font-emphasis text-text-primary hover:text-brand">
                            {pipeline.name}
                          </span>
                          <SourceDestinationPath
                            source={pipeline.source}
                            destination={pipeline.destination}
                            size="xs"
                          />
                        </Link>
                      </td>
                      <td className="px-3 py-2.5">
                        <PipelineStatusBadge status={pipeline.status} />
                      </td>
                      <td className="px-3 py-2.5">
                        <HealthBadge health={pipeline.health} size="xs" />
                      </td>
                      <td className="whitespace-nowrap px-3 py-2.5 text-caption text-text-secondary">
                        {describeSchedule(pipeline.schedule, t)}
                      </td>
                      <td className="whitespace-nowrap px-3 py-2.5">
                        {pipeline.last_run ? (
                          <Link href={`/runs/${pipeline.last_run.id}`}
                                className="inline-flex items-center gap-1.5 py-1">
                            <RunStatusBadge status={pipeline.last_run.status} size="xs" />
                            <span className="text-tiny text-text-quaternary">
                              {formatRelative(pipeline.last_run.ended_at
                                ?? pipeline.last_run.started_at, locale)}
                            </span>
                          </Link>
                        ) : (
                          <span className="text-caption text-text-quaternary">{t('common.never')}</span>
                        )}
                      </td>
                      <td className="whitespace-nowrap px-3 py-2.5 text-caption text-text-tertiary">
                        {pipeline.next_run_at ? formatRelative(pipeline.next_run_at, locale) : '—'}
                      </td>
                      <td className="px-3 py-2.5 text-right text-caption tabular-nums text-text-secondary">
                        {pipeline.stream_count}
                      </td>
                      <td className="sticky right-0 bg-surface-1 px-4 py-2.5">
                        <div className="flex items-center justify-end gap-0.5">
                          {pipeline.available_actions.includes('RUN_NOW') && (
                            <Button
                              size="xs"
                              variant="ghost"
                              aria-label={t('pipelines.runNow')}
                              title={t('pipelines.runNow')}
                              loading={busyId === pipeline.id && runNow.isPending}
                              onClick={() => runNow.mutate(pipeline.id)}
                              leadingIcon={<Play className="h-3.5 w-3.5" />}
                            />
                          )}
                          {(pipeline.available_actions.includes('PAUSE')
                            || pipeline.available_actions.includes('RESUME')) && (
                            <Button
                              size="xs"
                              variant="ghost"
                              aria-label={paused ? t('pipelines.resume') : t('pipelines.pause')}
                              title={paused ? t('pipelines.resume') : t('pipelines.pause')}
                              loading={busyId === pipeline.id && togglePause.isPending}
                              onClick={() => togglePause.mutate({ id: pipeline.id, paused })}
                              leadingIcon={paused
                                ? <Play className="h-3.5 w-3.5" />
                                : <Pause className="h-3.5 w-3.5" />}
                            />
                          )}
                          <Link
                            href={`/pipelines/${pipeline.id}`}
                            aria-label={t('common.detail')}
                            title={t('common.detail')}
                            className="inline-flex h-6 w-6 items-center justify-center rounded-md text-text-tertiary transition-colors hover:bg-surface-2 hover:text-text-primary"
                          >
                            <ChevronRight className="h-3.5 w-3.5" />
                          </Link>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </PageListLayout>
  );
}
