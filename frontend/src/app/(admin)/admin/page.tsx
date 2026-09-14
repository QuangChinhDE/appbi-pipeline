'use client';

/**
 * Which workspace needs somebody today.
 *
 * A workspace is a wall — nothing inside one is visible from another, which is
 * the tenancy guarantee and also the reason an administrator of eight of them
 * had no way to learn that one was failing without opening all eight. This is
 * the one screen that looks across, so failures come first and everything else
 * is context for them.
 */

import * as React from 'react';
import Link from 'next/link';
import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, ArrowRight, CheckCircle2, PlayCircle } from 'lucide-react';

import { organizationApi } from '@/lib/api';
import { formatRelative } from '@/lib/format';
import { cn } from '@/lib/utils';
import { useWorkspaceSwitch } from '@/hooks/use-current-user';
import { useI18n } from '@/providers/LanguageProvider';
import { Badge } from '@/components/ui/Badge';
import { Card, StatTile } from '@/components/layout/PageLayout';
import { CardSkeleton, EmptyState, ErrorState } from '@/components/ui/Feedback';

export default function AdminOverviewPage() {
  const { t, locale } = useI18n();
  const switchWorkspace = useWorkspaceSwitch();

  const overview = useQuery({
    queryKey: ['org-overview'],
    queryFn: organizationApi.overview,
    refetchInterval: 60_000,
  });

  const open = async (id: string) => {
    // Entering a workspace from here moves the session, which is the whole
    // point of the door: everything past it is scoped to that workspace.
    await switchWorkspace(id);
  };

  if (overview.error) {
    return (
      <ErrorState
        title={t('common.errorTitle')}
        message={(overview.error as Error).message}
        onRetry={() => overview.refetch()}
      />
    );
  }

  const data = overview.data;
  // Failing first, then busiest: the order somebody scanning this page reads in.
  const rows = [...(data?.workspaces ?? [])].sort((a, b) =>
    (b.failing_count - a.failing_count) || (b.pipeline_count - a.pipeline_count));

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-h3 font-strong text-text-primary">{t('admin.overviewTitle')}</h1>
        <p className="mt-1 max-w-2xl text-caption text-text-tertiary">
          {t('admin.overviewSubtitle')}
        </p>
      </header>

      {overview.isLoading ? (
        <CardSkeleton count={4} />
      ) : (
        <>
          <div className="grid auto-rows-fr gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <StatTile label={t('admin.kpiWorkspaces')} value={data?.total_workspaces ?? 0} />
            <StatTile label={t('admin.kpiPipelines')} value={data?.total_pipelines ?? 0} />
            <StatTile
              label={t('admin.kpiFailing')}
              value={data?.total_failing ?? 0}
              tone={(data?.total_failing ?? 0) > 0 ? 'danger' : 'default'}
              icon={<AlertTriangle className="h-3.5 w-3.5" />}
            />
            <StatTile label={t('admin.kpiPeople')} value={data?.total_people ?? 0} />
          </div>

          <Card title={t('admin.workspaceHealth')} padded={false}>
            {rows.length === 0 ? (
              <EmptyState title={t('admin.noWorkspaces')} compact />
            ) : (
              <ul className="divide-y divide-[rgb(var(--border-line))]">
                {rows.map((workspace) => (
                  <li key={workspace.id}>
                    <button
                      type="button"
                      onClick={() => open(workspace.id)}
                      className="flex w-full flex-wrap items-center gap-x-4 gap-y-1.5 px-4 py-3 text-left transition-colors hover:bg-surface-2"
                    >
                      <span className="min-w-0 flex-1 basis-48">
                        <span className="flex flex-wrap items-center gap-2">
                          <span className="truncate text-small font-emphasis text-text-primary">
                            {workspace.name}
                          </span>
                          {workspace.failing_count > 0 ? (
                            <Badge variant="danger" size="xs">
                              <AlertTriangle className="h-2.5 w-2.5" />
                              {t('admin.failingCount', { n: workspace.failing_count })}
                            </Badge>
                          ) : workspace.pipeline_count > 0 ? (
                            <Badge variant="success" size="xs">
                              <CheckCircle2 className="h-2.5 w-2.5" />
                              {t('admin.allHealthy')}
                            </Badge>
                          ) : null}
                          {workspace.running_count > 0 && (
                            <Badge variant="brand" size="xs">
                              <PlayCircle className="h-2.5 w-2.5" />
                              {t('admin.runningCount', { n: workspace.running_count })}
                            </Badge>
                          )}
                          {workspace.status !== 'ACTIVE' && (
                            <Badge variant="warning" size="xs">{workspace.status}</Badge>
                          )}
                        </span>
                        <span className="mt-0.5 flex flex-wrap items-center gap-x-3 text-tiny text-text-quaternary">
                          <span className="font-mono">{workspace.slug}</span>
                          <span>{t('org.seatCount', { n: workspace.member_count })}</span>
                          <span>{t('admin.pipelineCount', { n: workspace.pipeline_count })}</span>
                          {/* An empty workspace is not a healthy one; it is one
                              nobody has built anything in yet, and saying so
                              stops it reading as a green tick. */}
                          {workspace.member_count === 0 && (
                            <span className="text-warning">{t('admin.nobodyInside')}</span>
                          )}
                        </span>
                      </span>

                      <span className="text-tiny text-text-tertiary">
                        {workspace.last_run_at
                          ? t('admin.lastRun', {
                              when: formatRelative(workspace.last_run_at, locale),
                            })
                          : t('admin.neverRun')}
                      </span>

                      <span
                        className={cn(
                          'inline-flex items-center gap-1 text-caption text-brand',
                        )}
                      >
                        {t('admin.openWorkspace')}
                        <ArrowRight className="h-3.5 w-3.5" />
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <p className="text-tiny text-text-quaternary">
            {t('admin.overviewFootnote')}{' '}
            <Link href="/admin/people" className="text-brand hover:underline">
              {t('admin.people')}
            </Link>
          </p>
        </>
      )}
    </div>
  );
}
