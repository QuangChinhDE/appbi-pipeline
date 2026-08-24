'use client';

import * as React from 'react';
import Link from 'next/link';
import { useQuery } from '@tanstack/react-query';
import { Activity, AlertTriangle, CheckCircle2 } from 'lucide-react';

import { opsApi } from '@/lib/api';
import { qk } from '@/lib/queryKeys';
import { describeSchedule, formatDuration, formatRelative } from '@/lib/format';
import { useWorkspaceId } from '@/hooks/use-current-user';
import { usePermissions } from '@/hooks/use-permissions';
import { useI18n } from '@/providers/LanguageProvider';
import { Badge } from '@/components/ui/Badge';
import { EmptyState, ErrorState, TableSkeleton } from '@/components/ui/Feedback';
import { Card, ModuleOverview, PageListLayout } from '@/components/layout/PageLayout';
import { HealthBadge, RunStatusBadge } from '@/components/integrations/Badges';
import { SourceDestinationPath } from '@/components/integrations/ConnectorIcon';

export default function MonitoringPage() {
  const { t, locale } = useI18n();
  const workspaceId = useWorkspaceId();
  const { isPlatformAdmin } = usePermissions();

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: qk.monitoring(workspaceId),
    queryFn: opsApi.monitoring,
    refetchInterval: 20_000,
  });

  const rows = data?.pipelines ?? [];
  const counts = data?.counts ?? {};

  return (
    <PageListLayout
      title={t('monitoring.title')}
      description={t('monitoring.subtitle')}
      searchable={false}
      overview={
        <ModuleOverview
          stats={[
            { label: t('monitoring.pipelineCount'), value: counts.total ?? 0 },
            { label: t('pipelines.statHealthy'), value: counts.healthy ?? 0, tone: 'success' },
            { label: t('pipelines.statRunning'), value: counts.running ?? 0 },
            { label: t('pipelines.statAction'), value: counts.action_required ?? 0,
              tone: (counts.action_required ?? 0) > 0 ? 'warning' : 'default' },
            { label: t('pipelines.statFailed'), value: counts.failed ?? 0,
              tone: (counts.failed ?? 0) > 0 ? 'danger' : 'default' },
            { label: t('pipelines.statPaused'), value: counts.paused ?? 0 },
          ]}
        />
      }
    >
      {error ? (
        <ErrorState title={t('common.errorTitle')} message={(error as Error).message}
                    onRetry={() => refetch()} />
      ) : isLoading ? (
        <TableSkeleton rows={6} columns={6} />
      ) : (
        <div className="space-y-4">
          <Card title={t('monitoring.engineCard')}>
            <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
              <span className="flex items-center gap-2">
                {data?.engine.operational ? (
                  <CheckCircle2 className="h-4 w-4 text-success" />
                ) : (
                  <AlertTriangle className="h-4 w-4 text-danger" />
                )}
                <span className="text-caption font-emphasis text-text-primary">
                  {data?.engine.label}
                </span>
              </span>
              <Metric label={t('monitoring.running')} value={data?.engine.active_runs ?? 0} />
              <Metric label={t('monitoring.queued')} value={data?.engine.queued_runs ?? 0} />
              {isPlatformAdmin && (
                <>
                  <Metric label={t('monitoring.engine')} value={data?.engine.engine_type ?? '—'} />
                  <Metric label={t('monitoring.version')} value={data?.engine.version ?? '—'} />
                  <Metric
                    label={t('monitoring.lag')}
                    value={formatDuration(data?.engine.reconciliation_lag_seconds ?? null)}
                  />
                  <Metric label={t('monitoring.adapterContract')}
                          value={`v${data?.engine.adapter_contract_version ?? '—'}`} />
                </>
              )}
            </div>
          </Card>

          {rows.length === 0 ? (
            <EmptyState icon={Activity} title={t('monitoring.noPipelines')} />
          ) : (
            <div className="overflow-hidden rounded-lg border border-[rgb(var(--border-line))] bg-surface-1">
              <div className="overflow-x-auto">
                <table className="w-full min-w-[960px] text-left">
                  <thead>
                    <tr className="border-b border-[rgb(var(--border-line))] text-tiny uppercase tracking-[0.08em] text-text-quaternary">
                      <th scope="col" className="px-4 py-2.5 font-emphasis">{t('runs.pipeline')}</th>
                      <th scope="col" className="px-3 py-2.5 font-emphasis">{t('actor.health')}</th>
                      <th scope="col" className="px-3 py-2.5 font-emphasis">
                        {t('pipelines.schedule')}
                      </th>
                      <th scope="col" className="px-3 py-2.5 font-emphasis">
                        {t('monitoring.lastSuccess')}
                      </th>
                      <th scope="col" className="px-3 py-2.5 font-emphasis">
                        {t('monitoring.freshness')}
                      </th>
                      <th scope="col" className="px-3 py-2.5 font-emphasis">
                        {t('monitoring.failureStreak')}
                      </th>
                      <th scope="col" className="px-4 py-2.5 font-emphasis">
                        {t('monitoring.lastRunCol')}
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[rgb(var(--border-line))]">
                    {rows.map((row) => (
                      <tr key={row.pipeline.id}
                          className={row.freshness_breached ? 'bg-warning/[0.05]' : undefined}>
                        <td className="px-4 py-2.5">
                          <Link href={`/pipelines/${row.pipeline.id}`} className="block min-w-0">
                            <span className="block truncate text-caption font-emphasis text-text-primary hover:text-brand">
                              {row.pipeline.name}
                            </span>
                            <SourceDestinationPath
                              source={row.pipeline.source}
                              destination={row.pipeline.destination}
                              size="xs"
                            />
                          </Link>
                        </td>
                        <td className="px-3 py-2.5">
                          <HealthBadge health={row.pipeline.health} size="xs" />
                        </td>
                        <td className="px-3 py-2.5 text-caption text-text-secondary">
                          {describeSchedule(row.pipeline.schedule, t)}
                        </td>
                        <td className="px-3 py-2.5 text-caption text-text-tertiary">
                          {row.last_success_age_seconds === null
                            ? t('common.never')
                            : t('monitoring.ago', {
                                duration: formatDuration(row.last_success_age_seconds) })}
                        </td>
                        <td className="px-3 py-2.5">
                          {row.freshness_deadline ? (
                            row.freshness_breached ? (
                              <Badge variant="warning" size="xs">
                                {t('monitoring.overdueBy', {
                                  when: formatRelative(row.freshness_deadline, locale) })}
                              </Badge>
                            ) : (
                              <span className="text-caption text-text-tertiary">
                                {formatRelative(row.freshness_deadline, locale)}
                              </span>
                            )
                          ) : (
                            <span className="text-caption text-text-quaternary">—</span>
                          )}
                        </td>
                        <td className="px-3 py-2.5">
                          {row.failure_streak > 0 ? (
                            <Badge variant="danger" size="xs">
                              {t('monitoring.streakTimes', { n: row.failure_streak })}
                            </Badge>
                          ) : (
                            <span className="text-caption text-text-quaternary">0</span>
                          )}
                        </td>
                        <td className="px-4 py-2.5">
                          {row.pipeline.last_run ? (
                            <Link href={`/runs/${row.pipeline.last_run.id}`}>
                              <RunStatusBadge status={row.pipeline.last_run.status} size="xs" />
                            </Link>
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
          )}
        </div>
      )}
    </PageListLayout>
  );
}

function Metric({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <span className="flex items-baseline gap-1.5">
      <span className="text-tiny uppercase tracking-[0.08em] text-text-quaternary">{label}</span>
      <span className="text-caption font-emphasis text-text-primary">{value}</span>
    </span>
  );
}
