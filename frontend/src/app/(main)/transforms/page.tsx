'use client';

import * as React from 'react';
import Link from 'next/link';
import { useQuery } from '@tanstack/react-query';
import { Github, Plus, Workflow } from 'lucide-react';

import { transformApi } from '@/lib/api';
import { qk } from '@/lib/queryKeys';
import { formatRelative } from '@/lib/format';
import { cn } from '@/lib/utils';
import { useWorkspaceId } from '@/hooks/use-current-user';
import { usePermissions } from '@/hooks/use-permissions';
import { useI18n } from '@/providers/LanguageProvider';
import { Badge, type BadgeVariant } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { EmptyState, ErrorState, TableSkeleton } from '@/components/ui/Feedback';
import { ModuleOverview, PageListLayout } from '@/components/layout/PageLayout';

const tone: Record<string, BadgeVariant> = {
  HEALTHY: 'success', WARNING: 'warning', ERROR: 'danger', UNKNOWN: 'neutral',
};

export default function TransformsPage() {
  const workspaceId = useWorkspaceId();
  const { locale } = useI18n();
  const { can } = usePermissions();
  const [search, setSearch] = React.useState('');
  const copy = locale === 'vi' ? {
    title: 'Transform', description: 'Biến đổi dữ liệu trong warehouse thành dữ liệu sẵn sàng cho BI.',
    create: 'Transform mới', importFromGit: 'Import từ GitHub', empty: 'Chưa có Transform', emptyDescription: 'Tạo Transform đầu tiên từ một Destination warehouse đã có.',
    name: 'Tên', warehouse: 'Warehouse', models: 'Model', tests: 'Test', health: 'Sức khỏe', lastRun: 'Lần chạy cuối', open: 'Mở',
    total: 'Tổng', healthy: 'Ổn định', attention: 'Cần chú ý', search: 'Tìm Transform', never: 'Chưa chạy', loadError: 'Không tải được Transform',
    healthLabel: {
      HEALTHY: 'Ổn định', WARNING: 'Cảnh báo', ERROR: 'Lỗi', UNKNOWN: 'Chưa rõ',
    } as Record<string, string>,
  } : {
    title: 'Transform', description: 'Turn warehouse data into business-ready datasets.',
    create: 'New transform', importFromGit: 'Import from GitHub', empty: 'No transforms yet', emptyDescription: 'Create the first Transform from an existing warehouse Destination.',
    name: 'Name', warehouse: 'Warehouse', models: 'Models', tests: 'Tests', health: 'Health', lastRun: 'Last run', open: 'Open',
    total: 'Total', healthy: 'Healthy', attention: 'Needs attention', search: 'Search transforms', never: 'Never run', loadError: 'Could not load transforms',
    healthLabel: {
      HEALTHY: 'Healthy', WARNING: 'Warning', ERROR: 'Error', UNKNOWN: 'Unknown',
    } as Record<string, string>,
  };
  const query = useQuery({
    queryKey: qk.transforms(workspaceId, { search }),
    queryFn: () => transformApi.list({ search: search || undefined, limit: 100 }),
  });
  const items = query.data?.items ?? [];
  const healthy = items.filter((item) => item.health_status === 'HEALTHY').length;
  const attention = items.filter((item) => ['WARNING', 'ERROR'].includes(item.health_status)).length;

  return (
    <PageListLayout
      title={copy.title}
      description={copy.description}
      searchValue={search}
      onSearchChange={setSearch}
      searchPlaceholder={copy.search}
      action={can('transforms', 'create') ? (
        <div className="flex items-center gap-2">
          {/* Somebody arriving from Dataform has the work already written; the
              import is their first step, not an advanced option. */}
          <Link href="/transforms/import">
            <Button variant="secondary" size="sm" leadingIcon={<Github className="h-4 w-4" />}>
              {copy.importFromGit}
            </Button>
          </Link>
          <Link href="/transforms/new">
            <Button variant="primary" size="sm" leadingIcon={<Plus className="h-4 w-4" />}>
              {copy.create}
            </Button>
          </Link>
        </div>
      ) : null}
      overview={<ModuleOverview stats={[
        { label: copy.total, value: query.data?.page.total ?? 0 },
        { label: copy.healthy, value: healthy, tone: 'success' },
        { label: copy.attention, value: attention, tone: attention ? 'warning' : 'default' },
      ]} />}
    >
      {query.isLoading ? <TableSkeleton rows={6} columns={6} /> : query.error ? (
        <ErrorState title={copy.loadError} message={(query.error as Error).message} onRetry={() => query.refetch()} />
      ) : items.length === 0 ? (
        <EmptyState
          icon={Workflow}
          title={copy.empty}
          description={copy.emptyDescription}
          action={can('transforms', 'create') ? (
            <Link href="/transforms/new"><Button variant="primary" size="sm">{copy.create}</Button></Link>
          ) : undefined}
        />
      ) : (
        <div className="overflow-hidden rounded-lg border border-[rgb(var(--border-line))] bg-surface-1">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[840px] text-left">
              <thead>
                <tr className="border-b border-[rgb(var(--border-line))] text-tiny uppercase tracking-[0.08em] text-text-quaternary">
                  {[copy.name, copy.warehouse, copy.models, copy.tests, copy.health, copy.lastRun, ''].map((label) => (
                    <th key={label} scope="col" className="px-4 py-2 font-emphasis">{label}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-[rgb(var(--border-line))]">
                {items.map((item) => (
                  <tr key={item.id} className="hover:bg-surface-2/60">
                    <td className="px-4 py-3">
                      <Link href={`/transforms/${item.id}`} className="font-emphasis text-text-primary hover:text-brand">
                        {item.name}
                      </Link>
                      <span className="mt-0.5 block text-tiny text-text-quaternary">{item.default_schema}</span>
                    </td>
                    <td className="px-4 py-3 text-caption text-text-secondary">{item.destination.name}</td>
                    <td className="px-4 py-3 text-caption tabular-nums text-text-secondary">{item.model_count}</td>
                    <td className="px-4 py-3 text-caption tabular-nums text-text-secondary">{item.test_count}</td>
                    <td className="px-4 py-3">
                      <Badge size="xs" variant={tone[item.health_status] ?? 'neutral'}
                        title={item.health_message ?? undefined}>
                        {copy.healthLabel[item.health_status] ?? item.health_status}
                      </Badge>
                    </td>
                    <td className="px-4 py-3 text-caption text-text-tertiary">
                      {item.last_run ? (
                        <span className="flex items-center gap-1.5">
                          <span className={cn(
                            'h-1.5 w-1.5 shrink-0 rounded-full',
                            item.last_run.status === 'SUCCEEDED' ? 'bg-success'
                              : item.last_run.status === 'RUNNING' ? 'bg-info'
                                : 'bg-danger',
                          )} />
                          {formatRelative(item.last_run.started_at ?? item.last_run.created_at, locale)}
                        </span>
                      ) : copy.never}
                    </td>
                    <td className="px-4 py-3 text-right">
                      <Link href={`/transforms/${item.id}`} className="text-caption font-emphasis text-brand hover:underline">{copy.open}</Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </PageListLayout>
  );
}
