'use client';

/**
 * The Transform list.
 *
 * One row per dbt project. The columns are the four things somebody scanning
 * this page actually wants: which warehouse it runs on, whether it parses,
 * whether the draft differs from what production runs, and when it last built.
 */

import * as React from 'react';
import Link from 'next/link';
import { useQuery } from '@tanstack/react-query';
import {
  ChevronRight, CircleDot, FolderGit2, GitBranch, Package, Plus,
} from 'lucide-react';

import { EmptyState, ErrorState, TableSkeleton, isPermissionDenied } from '@/components/ui/Feedback';
import { Badge, type BadgeVariant } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { ModuleOverview, PageListLayout } from '@/components/layout/PageLayout';
import { useWorkspaceId } from '@/hooks/use-current-user';
import { usePermissions } from '@/hooks/use-permissions';
import { transformApi } from '@/lib/api';
import { formatRelative } from '@/lib/format';
import { qk } from '@/lib/queryKeys';
import type { Transform } from '@/lib/types';
import { cn } from '@/lib/utils';
import { useI18n } from '@/providers/LanguageProvider';

function healthVariant(status: string): BadgeVariant {
  switch (status) {
    case 'HEALTHY': return 'success';
    case 'WARNING': return 'warning';
    case 'ERROR': return 'danger';
    default: return 'subtle';
  }
}

// A plain function, not a component: it takes `t` rather than calling the
// hook, which React only allows inside a component or another hook.
function healthLabel(status: string, t: (key: string) => string): string {
  switch (status) {
    case 'HEALTHY': return t('tf.list.healthy');
    case 'WARNING': return t('tf.list.warning');
    case 'ERROR': return t('tf.list.failing');
    default: return t('tf.list.neverRun');
  }
}

export default function TransformsPage() {
  const { t } = useI18n();
  const workspaceId = useWorkspaceId();
  const { can } = usePermissions();

  const [search, setSearch] = React.useState('');
  const [debounced, setDebounced] = React.useState('');

  React.useEffect(() => {
    const timer = setTimeout(() => setDebounced(search), 300);
    return () => clearTimeout(timer);
  }, [search]);

  const filters = { search: debounced || undefined };
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: qk.transforms(workspaceId, filters),
    queryFn: () => transformApi.list(filters),
    refetchInterval: 20_000,
  });

  // Memoised because `?? []` allocates a fresh array each render, which would
  // make the stats below recompute on every keystroke in the search box.
  const projects = React.useMemo(() => data?.items ?? [], [data?.items]);
  const stats = React.useMemo(() => {
    const unpublished = projects.filter((item) => item.has_unpublished_changes).length;
    const broken = projects.filter((item) => item.parse_status === 'ERROR').length;
    return [
      { label: t('tf.list.projects'), value: data?.page.total ?? projects.length },
      ...(unpublished
        ? [{ label: t('tf.list.unpublished'), value: unpublished, tone: 'warning' as const }]
        : []),
      ...(broken
        ? [{ label: t('tf.list.failing'), value: broken, tone: 'danger' as const }]
        : []),
    ];
  }, [projects, data?.page.total]);

  return (
    <PageListLayout
      title="Transform"
      description={t('tf.list.intro')}
      overview={<ModuleOverview stats={stats} />}
      searchValue={search}
      onSearchChange={setSearch}
      searchPlaceholder={t('tf.list.search')}
      action={
        can('transforms', 'create') ? (
          <Link href="/transforms/new">
            <Button variant="primary" leadingIcon={<Plus className="h-4 w-4" />}>{t('tf.list.new')}</Button>
          </Link>
        ) : undefined
      }
    >
      {isLoading ? (
        <TableSkeleton rows={5} columns={6} />
      ) : error ? (
        <ErrorState
          title={isPermissionDenied(error)
            ? t('tf.list.forbidden')
            : t('tf.list.loadFailed')}
          message={(error as Error).message}
          error={error}
          onRetry={() => refetch()}
        />
      ) : projects.length === 0 ? (
        <EmptyState
          icon={Package}
          title={debounced ? t('tf.list.noMatch') : t('tf.list.empty')}
          description={
            debounced
              ? t('tf.list.tryAnother')
              : t('tf.list.emptyHint')
          }
          action={
            !debounced && can('transforms', 'create') ? (
              <Link href="/transforms/new">
                <Button variant="primary">{t('tf.list.new')}</Button>
              </Link>
            ) : undefined
          }
        />
      ) : (
        <ul className="space-y-1.5 pb-6">
          {projects.map((project) => (
            <ProjectRow key={project.id} project={project} />
          ))}
        </ul>
      )}
    </PageListLayout>
  );
}

function ProjectRow({ project }: { project: Transform }) {
  const { t, locale } = useI18n();
  return (
    <li>
      <Link
        href={`/transforms/${project.id}`}
        className={cn(
          'flex items-center gap-3 rounded-lg border border-[rgb(var(--border-line))]',
          'bg-surface-1 px-3.5 py-3 transition-colors hover:bg-surface-2',
        )}
      >
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-small font-emphasis text-text-primary">
              {project.name}
            </span>
            {project.mode === 'GIT' ? (
              <Badge variant="subtle" size="xs">
                <FolderGit2 className="h-2.5 w-2.5" /> Git
              </Badge>
            ) : null}
            {/* A project that will not parse is broken in a way that no other
                badge conveys: nothing can run until it is fixed. */}
            {project.parse_status === 'ERROR' && (
              <Badge variant="danger" size="xs">{t('tf.list.parseFailed')}</Badge>
            )}
          </div>
          <p className="mt-0.5 flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-tiny text-text-tertiary">
            {project.warehouse && (
              <span>
                {project.warehouse.connector_display_name ?? project.warehouse.connector_key}
                {' · '}
                {project.warehouse.name}
              </span>
            )}
            {project.dbt_project_name && (
              <span className="font-mono">{project.dbt_project_name}</span>
            )}
            {project.git && (
              <span className="flex items-center gap-1">
                <GitBranch className="h-3 w-3" />
                {project.git.branch}
                {project.git.behind && (
                  <span className="text-warning">{t('tf.list.behind')}</span>
                )}
              </span>
            )}
            <span>{t('tf.list.fileCount', { n: project.file_count })}</span>
          </p>
        </div>

        {project.has_unpublished_changes && (
          <Badge variant="warning" size="xs" className="shrink-0">
            <CircleDot className="h-2.5 w-2.5" /> {t('tf.list.unpublished')}
          </Badge>
        )}

        {project.active_release ? (
          <span className="hidden shrink-0 text-tiny text-text-tertiary sm:block">
            {t('tf.list.releaseShort', { n: project.active_release.release_number })}
          </span>
        ) : (
          <span className="hidden shrink-0 text-tiny text-text-quaternary sm:block">
            {t('tf.list.unpublished')}
          </span>
        )}

        <div className="hidden w-28 shrink-0 text-right md:block">
          <Badge variant={healthVariant(project.health_status)} size="sm">
            {healthLabel(project.health_status, t)}
          </Badge>
          {project.last_success_at && (
            <p className="mt-0.5 text-tiny text-text-quaternary">
              {formatRelative(project.last_success_at, locale)}
            </p>
          )}
        </div>

        <ChevronRight className="h-4 w-4 shrink-0 text-text-quaternary" />
      </Link>
    </li>
  );
}
