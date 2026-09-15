'use client';

/**
 * Data Health — the first screen of the morning.
 *
 * It answers four questions, in this order, and the order is the design:
 *
 *   Is my data OK?  →  What is wrong?  →  What does it hurt?  →  What do I do?
 *
 * What it replaced showed seven counts and three lists of runs. Those are
 * facts about runs, not answers about data: "7 failed, 91% success" leaves the
 * reader to work out whether the seven share one cause, whether any reporting
 * table is affected, and which to fix first — and the only way to find out was
 * to open seven of them.
 *
 * Status comes before any number, because a number invites arithmetic and a
 * status invites a decision. The joining happens on the server, in
 * `services/health.py`, where the run history, freshness deadlines,
 * notification dedup keys and transform health actually live; this file is a
 * layout.
 */

import * as React from 'react';
import Link from 'next/link';
import { useQuery } from '@tanstack/react-query';
import {
  AlertTriangle, CheckCircle2, Clock, Database, GitBranch, RefreshCw, Server,
  Warehouse, Workflow,
} from 'lucide-react';

import { opsApi } from '@/lib/api';
import { qk } from '@/lib/queryKeys';
import { formatRelative } from '@/lib/format';
import { cn } from '@/lib/utils';
import { useWorkspaceId, useWorkspacePath } from '@/hooks/use-workspace-path';
import { useI18n } from '@/providers/LanguageProvider';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { CardSkeleton, ErrorState } from '@/components/ui/Feedback';
import { Card, PageListLayout } from '@/components/layout/PageLayout';
import {
  AnomalyList, CauseBars, FreshnessList, IssueCard, MetricTile, ReliabilityChart,
} from '@/components/overview/HealthCards';
import { OnboardingChecklist } from '@/components/overview/OnboardingChecklist';

//: How many incidents the card shows before folding. Four fits the column
//: beside the supporting cards at 1440 without pushing the trend sections off
//: the first screen, which is the whole reason the fold exists.
const ISSUES_BEFORE_FOLD = 4;

const STAGE_ICON: Record<string, React.ReactNode> = {
  sources: <Database className="h-3.5 w-3.5" />,
  pipelines: <GitBranch className="h-3.5 w-3.5" />,
  destinations: <Warehouse className="h-3.5 w-3.5" />,
  transforms: <Workflow className="h-3.5 w-3.5" />,
};

export default function OverviewPage() {
  const { t, tf, locale } = useI18n();
  const ws = useWorkspacePath();
  const workspaceId = useWorkspaceId();

  const overview = useQuery({
    queryKey: qk.overview(workspaceId),
    queryFn: opsApi.overview,
    refetchInterval: 60_000,
  });

  // Above the error return on purpose: a hook after an early return is called
  // in a different order on the render that errors, which is a crash rather
  // than a lint opinion.
  const [allIssues, setAllIssues] = React.useState(false);

  if (overview.error) {
    return (
      <PageListLayout title={t('health.title')} searchable={false}>
        <ErrorState
          title={t('common.errorTitle')}
          message={(overview.error as Error).message}
          onRetry={() => overview.refetch()}
        />
      </PageListLayout>
    );
  }

  const data = overview.data;
  const health = data?.health;
  const issues = health?.issues ?? [];
  const critical = issues.filter((issue) => issue.severity === 'CRITICAL');

  /**
   * The list is bounded, because a bad morning is exactly when it should not
   * be a scroll. Five incidents pushed everything below them off the screen,
   * and the reader who most needs the reliability trend and the freshness list
   * is the reader least likely to reach them.
   *
   * Safe to cut because the server sorts by severity and then by how many
   * pipelines each one touches, so the first four are the four to act on. The
   * rest are one click away and the count says how many there are.
   */
  // Folds at four, every time. An earlier version skipped the fold when it
  // would hide only one card, which spared a nearly-pointless control and cost
  // the thing the fold is for: with five incidents the card was unbounded
  // again and the column ran 227px past the one beside it. A card that is
  // always four tall is worth more than never showing "Show 1 more".
  const folds = issues.length > ISSUES_BEFORE_FOLD;
  const shownIssues = allIssues || !folds ? issues : issues.slice(0, ISSUES_BEFORE_FOLD);
  const hiddenIssues = issues.length - shownIssues.length;
  // A workspace with nothing in it yet is not a healthy workspace; it is an
  // empty one, and what it needs is the four steps, not a green tick.
  const onboarding = data?.onboarding ?? {};
  const settingUp = Object.keys(onboarding).length > 0
    && !Object.values(onboarding).every(Boolean);

  return (
    <PageListLayout
      title={t('health.title')}
      searchable={false}
      description={t('health.subtitle')}
      action={
        health?.generated_at ? (
          <span className="flex items-center gap-2 text-tiny text-text-quaternary">
            <Clock className="h-3 w-3" />
            {/* The stamp is the server's clock, so a browser running a second
                behind renders "in 1 second". Never claim the future. */}
            {t('health.updated', {
              when: formatRelative(
                new Date(Math.min(Date.parse(health.generated_at), Date.now())).toISOString(),
                locale,
              ),
            })}
            <button
              type="button"
              onClick={() => overview.refetch()}
              aria-label={t('common.refresh')}
              className="rounded p-1 text-text-tertiary transition-colors hover:text-text-primary"
            >
              <RefreshCw className={cn('h-3 w-3', overview.isFetching && 'animate-spin')} />
            </button>
          </span>
        ) : null
      }
    >
      {overview.isLoading || !health ? (
        <CardSkeleton count={4} />
      ) : settingUp ? (
        <OnboardingChecklist state={onboarding} />
      ) : (
        <div className="space-y-3">
          {/* ── Is my data OK? ─────────────────────────────────────────── */}
          <section
            className={cn(
              'flex flex-wrap items-center gap-x-4 gap-y-2 rounded-lg border px-4 py-3',
              health.status === 'CRITICAL' ? 'border-danger/40 bg-danger/5'
                : health.status === 'ATTENTION' ? 'border-warning/40 bg-warning/5'
                  : 'border-success/40 bg-success/5',
            )}
          >
            {health.status === 'HEALTHY'
              ? <CheckCircle2 className="h-5 w-5 flex-shrink-0 text-success" aria-hidden />
              : <AlertTriangle
                  className={cn('h-5 w-5 flex-shrink-0',
                    health.status === 'CRITICAL' ? 'text-danger' : 'text-warning')}
                  aria-hidden
                />}
            <div className="min-w-0 flex-1 basis-72">
              <p className="text-small font-strong text-text-primary">
                {t(`health.status.${health.status}`)}
              </p>
              {health.headline_code && (
                <p className="mt-0.5 text-caption text-text-secondary">
                  {tf([health.headline_code], '', health.headline_vars)}
                </p>
              )}
            </div>
            {issues.length > 0 && (
              <a href="#issues">
                <Button size="sm" variant={critical.length ? 'primary' : 'secondary'}>
                  {critical.length
                    ? t('health.reviewCritical', { n: critical.length })
                    : t('health.review', { n: issues.length })}
                </Button>
              </a>
            )}
          </section>

          <div className="grid auto-rows-fr gap-3 grid-cols-2 xl:grid-cols-4">
            {health.metrics.map((metric) => (
              <MetricTile key={metric.key} metric={metric} />
            ))}
          </div>

          {/* ── What is wrong, and the state of everything else ──────────
               Left: the incidents, and whether things are trending better or
               worse. Right: the status of each part at a glance.

               Split this way because the left column is the only one that
               grows without bound, and a column that grows beside three fixed
               ones leaves a hole. Freshness sits on the right because it is a
               status list like the three below it, not because it matters
               less. */}
          <div className="grid items-start gap-3 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
            <div className="space-y-3">
              {/* The anchor the banner's button jumps to. Card takes no id,
                  and the scroll margin keeps the heading clear of the top. */}
              <div id="issues" className="scroll-mt-4">
                <Card title={t('health.needsAttention')}>
                  {issues.length === 0 ? (
                    <p className="flex items-center gap-2 py-2 text-caption text-text-tertiary">
                      <CheckCircle2 className="h-4 w-4 text-success" />
                      {t('health.allClear')}
                    </p>
                  ) : (
                    <div className="space-y-2">
                      {shownIssues.map((issue) => <IssueCard key={issue.key} issue={issue} />)}
                      {/* Shown whenever the list folds, not only while it is
                          folded -- gating on the hidden count made expanding a
                          one-way door, because expanding is what takes the
                          count to zero. */}
                      {folds && (
                        <button
                          type="button"
                          onClick={() => setAllIssues((open) => !open)}
                          aria-expanded={allIssues}
                          className="w-full rounded-md border border-dashed border-[rgb(var(--border-line))]
                                     py-2 text-caption text-text-tertiary transition-colors
                                     hover:border-[rgb(var(--border-strong))] hover:text-text-primary"
                        >
                          {allIssues
                            ? t('health.issues.showLess')
                            : t('health.issues.showMore', { n: hiddenIssues })}
                        </button>
                      )}
                    </div>
                  )}
                </Card>
              </div>

              <Card title={t('health.reliability')} description={t('health.reliabilityHint')}>
                <ReliabilityChart days={health.reliability} />
                <div className="mt-3 border-t border-[rgb(var(--border-line))] pt-3">
                  <p className="mb-1.5 text-tiny uppercase tracking-[0.08em] text-text-quaternary">
                    {t('health.failureCauses')}
                  </p>
                  <CauseBars causes={health.failure_causes} />
                </div>
              </Card>
              <Card title={t('health.volume')} description={t('health.volumeHint')}>
                <AnomalyList rows={health.volume} labelKey="health.volume" />
                {health.duration.length > 0 && (
                  <div className="mt-3 border-t border-[rgb(var(--border-line))] pt-3">
                    <p className="mb-1.5 text-tiny uppercase tracking-[0.08em] text-text-quaternary">
                      {t('health.slower')}
                    </p>
                    <AnomalyList rows={health.duration} labelKey="health.duration" />
                  </div>
                )}
              </Card>
            </div>

            <div className="space-y-3">
              <Card title={t('health.freshness')} description={t('health.freshnessHint')}>
                <FreshnessList rows={health.freshness} />
              </Card>
              <Card title={t('health.journey')} description={t('health.journeyHint')}>
                <ul className="space-y-1.5">
                  {health.stages.map((stage) => (
                    <li key={stage.stage} className="flex items-center gap-2">
                      <span className="text-text-quaternary">{STAGE_ICON[stage.stage]}</span>
                      <span className="min-w-0 flex-1 truncate text-caption text-text-secondary">
                        {t(`health.stage.${stage.stage}`)}
                      </span>
                      <span className="flex-shrink-0 text-tiny tabular-nums text-text-tertiary">
                        {stage.healthy}/{stage.total}
                      </span>
                      {stage.problem > 0
                        ? <Badge variant="danger" size="xs">{stage.problem}</Badge>
                        : <CheckCircle2 className="h-3 w-3 flex-shrink-0 text-success" />}
                    </li>
                  ))}
                </ul>
              </Card>
              <Card title={t('health.transform')} description={t('health.transformHint')}>
                {health.transforms.length === 0 ? (
                  <p className="text-tiny text-text-quaternary">{t('health.noTransforms')}</p>
                ) : (
                  <>
                    <div className="mb-2 flex flex-wrap items-center gap-3 text-caption">
                      {(['healthy', 'warning', 'failing'] as const).map((state) => {
                        const n = health.transforms.filter((row) => row.state === state).length;
                        if (!n) return null;
                        return (
                          <span key={state} className="flex items-center gap-1.5">
                            <span className={cn('h-1.5 w-1.5 rounded-full',
                              state === 'healthy' ? 'bg-success'
                                : state === 'warning' ? 'bg-warning' : 'bg-danger')} />
                            <span className="text-text-secondary">
                              {n} {t(`health.transformState.${state}`)}
                            </span>
                          </span>
                        );
                      })}
                    </div>
                    <ul className="space-y-1">
                      {health.transforms
                        .filter((row) => row.state !== 'healthy')
                        .slice(0, 4)
                        .map((row) => (
                          <li key={row.project_id} className="flex items-center gap-2">
                            <span className={cn('h-1.5 w-1.5 flex-shrink-0 rounded-full',
                              row.state === 'failing' ? 'bg-danger' : 'bg-warning')} />
                            <Link
                              href={ws(`/transforms/${row.project_id}`)}
                              className="min-w-0 flex-1 truncate text-caption text-text-secondary hover:text-text-primary"
                            >
                              {row.name}
                            </Link>
                            {row.detail_code && (
                              <span className="flex-shrink-0 text-tiny text-text-quaternary">
                                {tf([row.detail_code], '', row.detail_vars)}
                              </span>
                            )}
                          </li>
                        ))}
                    </ul>
                  </>
                )}
              </Card>
              <Card title={t('health.platform')} description={t('health.platformHint')}>
                <ul className="space-y-1.5">
                  {health.platform.map((signal) => (
                    <li key={signal.key} className="flex items-center gap-2">
                      <Server className="h-3.5 w-3.5 flex-shrink-0 text-text-quaternary" />
                      <span className="min-w-0 flex-1 truncate text-caption text-text-secondary">
                        {t(`health.signal.${signal.key}`)}
                      </span>
                      {signal.detail_code && (
                        <span className="flex-shrink-0 text-tiny text-text-quaternary">
                          {tf([signal.detail_code], '', signal.detail_vars)}
                        </span>
                      )}
                      {/* A backlog is work piling up, not a component that has
                          stopped. Painting both red made a queue look like an
                          outage and left nothing louder for the worker that has
                          actually gone. */}
                      <Badge
                        size="xs"
                        variant={signal.state === 'operational' || signal.state === 'normal'
                          ? 'success' : signal.state === 'backlog' ? 'warning' : 'danger'}
                      >
                        {t(`health.signalState.${signal.state}`)}
                      </Badge>
                    </li>
                  ))}
                </ul>
              </Card>
            </div>
          </div>
        </div>
      )}
    </PageListLayout>
  );
}
