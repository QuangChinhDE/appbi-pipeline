'use client';

import * as React from 'react';
import Link from 'next/link';
import { useQuery } from '@tanstack/react-query';
import {
  Activity, ArrowRight, CheckCircle2, Circle, Database, GitBranch, PlayCircle, Plus,
  TrendingUp, Warehouse, XCircle,
} from 'lucide-react';

import { opsApi } from '@/lib/api';
import { qk } from '@/lib/queryKeys';
import { formatNumber, formatPercent, formatRelative } from '@/lib/format';
import { useWorkspaceId } from '@/hooks/use-current-user';
import { usePermissions } from '@/hooks/use-permissions';
import { useI18n } from '@/providers/LanguageProvider';
import { Button } from '@/components/ui/Button';
import { CardSkeleton, EmptyState, ErrorState, Skeleton } from '@/components/ui/Feedback';
import { Card, PageListLayout, StatTile } from '@/components/layout/PageLayout';
import { HealthBadge, RunStatusBadge } from '@/components/integrations/Badges';
import { ConnectorIcon, SourceDestinationPath } from '@/components/integrations/ConnectorIcon';
import type { Run } from '@/lib/types';

export default function OverviewPage() {
  const { t, locale } = useI18n();
  const workspaceId = useWorkspaceId();
  const { can } = usePermissions();

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: qk.overview(workspaceId),
    queryFn: opsApi.overview,
    // Cheap enough to keep warm; the dashboard is the operator's default tab.
    refetchInterval: 20_000,
  });

  const onboarding = data?.onboarding ?? {};
  const onboardingIncomplete = !onboarding.has_successful_run;

  return (
    <PageListLayout
      title={t('overview.title')}
      description={t('overview.subtitle')}
      searchable={false}
      action={
        can('pipelines', 'create') ? (
          <Link href="/pipelines/new">
            <Button variant="primary" leadingIcon={<Plus className="h-3.5 w-3.5" />}>
              {t('pipelines.add')}
            </Button>
          </Link>
        ) : null
      }
    >
      {error ? (
        <ErrorState
          title={t('common.errorTitle')}
          message={(error as Error).message}
          onRetry={() => refetch()}
        />
      ) : isLoading ? (
        <div className="space-y-4">
          <div className="grid auto-rows-fr gap-3 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-6">
            {Array.from({ length: 6 }).map((_, index) => (
              <Skeleton key={index} className="h-[86px]" />
            ))}
          </div>
          <CardSkeleton count={3} />
        </div>
      ) : data ? (
        <div className="space-y-3">
          {onboardingIncomplete && <OnboardingChecklist state={onboarding} />}

          <div className="grid auto-rows-fr gap-3 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-6">
            <StatTile
              label={t('overview.kpi.activePipelines')}
              value={data.kpis.active_pipelines}
              icon={<GitBranch className="h-3.5 w-3.5" />}
            />
            <StatTile
              label={t('overview.kpi.running')}
              value={data.kpis.running_now}
              tone={data.kpis.running_now > 0 ? 'info' : 'default'}
              icon={<PlayCircle className="h-3.5 w-3.5" />}
            />
            <StatTile
              label={t('overview.kpi.failed24h')}
              value={data.kpis.failed_last_24h}
              tone={data.kpis.failed_last_24h > 0 ? 'danger' : 'default'}
              icon={<XCircle className="h-3.5 w-3.5" />}
            />
            <StatTile
              label={t('overview.kpi.successRate')}
              value={formatPercent(data.kpis.success_rate_7d)}
              tone={
                data.kpis.success_rate_7d === null ? 'default'
                  : data.kpis.success_rate_7d >= 95 ? 'success'
                  : data.kpis.success_rate_7d >= 80 ? 'warning' : 'danger'
              }
              icon={<TrendingUp className="h-3.5 w-3.5" />}
            />
            <StatTile
              label={t('overview.kpi.sourcesAttention')}
              value={data.kpis.sources_needing_attention}
              tone={data.kpis.sources_needing_attention > 0 ? 'danger' : 'default'}
              helper={t('overview.sourcesCount', { n: data.kpis.total_sources })}
              icon={<Database className="h-3.5 w-3.5" />}
            />
            <StatTile
              label={t('overview.kpi.destinationsAttention')}
              value={data.kpis.destinations_needing_attention}
              tone={data.kpis.destinations_needing_attention > 0 ? 'danger' : 'default'}
              helper={t('overview.destinationsCount', { n: data.kpis.total_destinations })}
              icon={<Warehouse className="h-3.5 w-3.5" />}
            />
          </div>

          <div className="grid items-start gap-3 xl:grid-cols-2">
            <Card
              title={t('overview.recentFailures')}
              action={
                <Link href="/runs?status=FAILED"
                      className="inline-block py-1 text-tiny text-brand hover:underline">
                  {t('common.viewAll')}
                </Link>
              }
              padded={false}
            >
              <RunList runs={data.recent_failures} emptyText={t('overview.allQuiet')} locale={locale} />
            </Card>

            <Card
              title={t('overview.running')}
              action={
                <Link href="/runs?status=ACTIVE"
                      className="inline-block py-1 text-tiny text-brand hover:underline">
                  {t('common.viewAll')}
                </Link>
              }
              padded={false}
            >
              <RunList runs={data.running} emptyText={t('overview.noneRunning')} locale={locale} />
            </Card>

            <Card title={t('overview.attention')} padded={false}>
              {data.attention_pipelines.length === 0 ? (
                <p className="px-4 py-5 text-center text-caption text-text-quaternary">
                  {t('overview.allHealthy')}
                </p>
              ) : (
                <ul className="divide-y divide-[rgb(var(--border-line))]">
                  {data.attention_pipelines.map((pipeline) => (
                    <li key={pipeline.id}>
                      <Link
                        href={`/pipelines/${pipeline.id}`}
                        className="flex items-center gap-3 px-4 py-2.5 transition-colors hover:bg-surface-2"
                      >
                        <div className="min-w-0 flex-1">
                          <p className="truncate text-caption font-emphasis text-text-primary">
                            {pipeline.name}
                          </p>
                          <SourceDestinationPath
                            source={pipeline.source}
                            destination={pipeline.destination}
                            size="xs"
                          />
                        </div>
                        <HealthBadge health={pipeline.health} size="xs" />
                        <ArrowRight className="h-3.5 w-3.5 flex-shrink-0 text-text-quaternary" />
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
            </Card>

            <Card title={t('overview.recentSuccesses')} padded={false}>
              <RunList runs={data.recent_successes} emptyText={t('overview.noSuccess')} locale={locale} />
            </Card>
          </div>

          {data.connector_updates.length > 0 && (
            <Card title={t('overview.connectorUpdates')}>
              <ul className="flex flex-wrap gap-2">
                {data.connector_updates.map((connector) => (
                  <li
                    key={connector.connector_key}
                    className="flex items-center gap-2 rounded-md border border-[rgb(var(--border-line))] px-2.5 py-1.5"
                  >
                    <ConnectorIcon icon={connector.icon} connectorKey={connector.connector_key} size="xs" />
                    <span className="text-caption text-text-secondary">
                      {connector.display_name}
                    </span>
                    <span className="font-mono text-tiny text-text-quaternary">
                      {connector.version} → {connector.latest_version}
                    </span>
                  </li>
                ))}
              </ul>
            </Card>
          )}
        </div>
      ) : null}
    </PageListLayout>
  );
}

function RunList({
  runs, emptyText, locale,
}: {
  runs: Run[];
  emptyText: string;
  locale: 'vi' | 'en';
}) {
  const { t } = useI18n();
  if (runs.length === 0) {
    return <p className="px-4 py-5 text-center text-caption text-text-quaternary">{emptyText}</p>;
  }
  return (
    <ul className="divide-y divide-[rgb(var(--border-line))]">
      {runs.map((run) => (
        <li key={run.id}>
          <Link
            href={`/runs/${run.id}`}
            className="flex items-center gap-3 px-4 py-2.5 transition-colors hover:bg-surface-2"
          >
            <div className="min-w-0 flex-1">
              <p className="truncate text-caption font-emphasis text-text-primary">
                {run.pipeline?.name ?? run.short_id}
              </p>
              <p className="truncate text-tiny text-text-tertiary">
                {run.error?.summary
                  ?? `${t('overview.recordsSuffix', { n: formatNumber(run.records_synced) })} · ${formatRelative(run.created_at, locale)}`}
              </p>
            </div>
            <RunStatusBadge status={run.status} size="xs" />
          </Link>
        </li>
      ))}
    </ul>
  );
}

function OnboardingChecklist({ state }: { state: Record<string, boolean> }) {
  const { t } = useI18n();
  const steps = [
    { key: 'has_source', label: t('overview.onboarding.source'), href: '/sources/new' },
    { key: 'has_destination', label: t('overview.onboarding.destination'), href: '/destinations/new' },
    { key: 'has_pipeline', label: t('overview.onboarding.pipeline'), href: '/pipelines/new' },
    { key: 'has_successful_run', label: t('overview.onboarding.run'), href: '/pipelines' },
  ];
  const nextStep = steps.find((step) => !state[step.key]);

  return (
    <div className="rounded-lg border border-brand/25 bg-brand-soft/60 p-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="flex items-center gap-1.5 text-caption font-strong text-text-primary">
            <Activity className="h-3.5 w-3.5 text-brand" />
            {t('overview.onboarding.title')}
          </p>
          <ol className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1.5">
            {steps.map((step) => {
              const done = Boolean(state[step.key]);
              return (
                <li key={step.key} className="flex items-center gap-1.5">
                  {done ? (
                    <CheckCircle2 className="h-3.5 w-3.5 text-success" />
                  ) : (
                    <Circle className="h-3.5 w-3.5 text-text-quaternary" />
                  )}
                  <span
                    className={done
                      ? 'text-caption text-text-tertiary line-through'
                      : 'text-caption text-text-secondary'}
                  >
                    {step.label}
                  </span>
                </li>
              );
            })}
          </ol>
        </div>
        {nextStep && (
          <Link href={nextStep.href} className="flex-shrink-0">
            <Button variant="primary" size="sm" trailingIcon={<ArrowRight className="h-3.5 w-3.5" />}>
              {nextStep.label}
            </Button>
          </Link>
        )}
      </div>
    </div>
  );
}
