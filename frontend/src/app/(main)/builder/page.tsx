'use client';

import * as React from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CheckCircle2, CircleDashed, Hammer, Plus } from 'lucide-react';

import { builderApi } from '@/lib/api';
import { qk } from '@/lib/queryKeys';
import { formatRelative } from '@/lib/format';
import { useWorkspaceId } from '@/hooks/use-current-user';
import { usePermissions } from '@/hooks/use-permissions';
import { toastError } from '@/hooks/use-toast';
import { useI18n } from '@/providers/LanguageProvider';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { EmptyState, ErrorState, TableSkeleton } from '@/components/ui/Feedback';
import { ModuleOverview, PageListLayout } from '@/components/layout/PageLayout';

export default function BuilderPage() {
  const { t, locale } = useI18n();
  const workspaceId = useWorkspaceId();
  const queryClient = useQueryClient();
  const router = useRouter();
  const { can } = usePermissions();
  const canCreate = can('connectors', 'create');

  const [search, setSearch] = React.useState('');
  const [creating, setCreating] = React.useState(false);
  const [name, setName] = React.useState('');

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: qk.builderProjects(workspaceId),
    queryFn: () => builderApi.list(),
  });

  const create = useMutation({
    mutationFn: () => builderApi.create({ name: name.trim() }),
    onSuccess: (project) => {
      queryClient.invalidateQueries({ queryKey: qk.builderProjects(workspaceId) });
      // Straight into the editor: the point of creating is to start building.
      router.push(`/builder/${project.id}`);
    },
    onError: (caught) => toastError(caught),
  });

  const items = (data ?? []).filter((project) =>
    !search || project.name.toLowerCase().includes(search.toLowerCase()));

  const published = (data ?? []).filter((p) => p.status === 'PUBLISHED').length;

  return (
    <PageListLayout
      title={t('builder.title')}
      description={t('builder.subtitle')}
      searchValue={search}
      onSearchChange={setSearch}
      searchPlaceholder={t('builder.search')}
      action={
        canCreate ? (
          <Button
            variant="primary"
            leadingIcon={<Plus className="h-3.5 w-3.5" />}
            onClick={() => setCreating(true)}
          >
            {t('builder.add')}
          </Button>
        ) : null
      }
      overview={
        <ModuleOverview
          stats={[
            { label: t('builder.statTotal'), value: data?.length ?? 0 },
            { label: t('builder.statPublished'), value: published, tone: 'success' },
            {
              label: t('builder.statDraft'),
              value: (data?.length ?? 0) - published,
            },
          ]}
        />
      }
    >
      {creating && (
        <form
          className="mb-3 flex flex-col gap-2 rounded-lg border border-[rgb(var(--border-line))] bg-surface-1 p-3 sm:flex-row sm:items-end"
          onSubmit={(event) => {
            event.preventDefault();
            if (name.trim()) create.mutate();
          }}
        >
          <div className="flex-1">
            <label htmlFor="builder-name"
                   className="mb-1 block text-label text-text-secondary">
              {t('builder.nameLabel')}
            </label>
            <Input
              id="builder-name"
              size="sm"
              autoFocus
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder={t('builder.namePlaceholder')}
            />
          </div>
          <div className="flex gap-2">
            <Button type="submit" variant="primary" size="sm"
                    loading={create.isPending} disabled={!name.trim()}>
              {t('builder.createAndOpen')}
            </Button>
            <Button type="button" variant="ghost" size="sm"
                    onClick={() => { setCreating(false); setName(''); }}>
              {t('common.cancel')}
            </Button>
          </div>
        </form>
      )}

      {error ? (
        <ErrorState title={t('common.errorTitle')} message={(error as Error).message}
                    onRetry={() => refetch()} />
      ) : isLoading ? (
        <TableSkeleton rows={4} columns={5} />
      ) : items.length === 0 ? (
        <EmptyState
          icon={Hammer}
          title={search ? t('common.noResults') : t('builder.emptyTitle')}
          description={search ? undefined : t('builder.empty')}
          action={
            canCreate && !search ? (
              <Button variant="primary" leadingIcon={<Plus className="h-3.5 w-3.5" />}
                      onClick={() => setCreating(true)}>
                {t('builder.add')}
              </Button>
            ) : null
          }
        />
      ) : (
        <div className="overflow-hidden rounded-lg border border-[rgb(var(--border-line))] bg-surface-1">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[860px] text-left">
              <thead>
                <tr className="border-b border-[rgb(var(--border-line))] text-tiny uppercase tracking-[0.08em] text-text-quaternary">
                  <th scope="col" className="px-4 py-2.5 font-emphasis">{t('builder.columnName')}</th>
                  <th scope="col" className="px-3 py-2.5 font-emphasis">{t('common.status')}</th>
                  <th scope="col" className="px-3 py-2.5 text-right font-emphasis">
                    {t('builder.columnStreams')}
                  </th>
                  <th scope="col" className="whitespace-nowrap px-3 py-2.5 font-emphasis">
                    {t('builder.columnTested')}
                  </th>
                  <th scope="col" className="whitespace-nowrap px-3 py-2.5 font-emphasis">
                    {t('builder.columnUpdated')}
                  </th>
                  <th scope="col" className="px-4 py-2.5 text-right font-emphasis">
                    {t('common.actions')}
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[rgb(var(--border-line))]">
                {items.map((project) => (
                  <tr key={project.id} className="transition-colors hover:bg-surface-2/60">
                    <td className="px-4 py-2.5">
                      <Link href={`/builder/${project.id}`} className="block min-w-0">
                        <span className="block truncate text-caption font-emphasis text-text-primary hover:text-brand">
                          {project.name}
                        </span>
                        <span className="block truncate font-mono text-tiny text-text-quaternary">
                          {project.connector_key}
                        </span>
                      </Link>
                    </td>
                    <td className="px-3 py-2.5">
                      {project.status === 'PUBLISHED' ? (
                        <Badge variant="success" size="xs">
                          {t('builder.statusPublished', {
                            v: String(project.published_version),
                          })}
                        </Badge>
                      ) : (
                        <Badge variant="subtle" size="xs">{t('builder.statusDraft')}</Badge>
                      )}
                    </td>
                    <td className="px-3 py-2.5 text-right text-caption tabular-nums text-text-secondary">
                      {project.stream_count}
                    </td>
                    <td className="whitespace-nowrap px-3 py-2.5 text-caption">
                      {project.last_tested_at ? (
                        <span className="inline-flex items-center gap-1.5">
                          {project.last_test_ok
                            ? <CheckCircle2 className="h-3.5 w-3.5 text-success" />
                            : <CircleDashed className="h-3.5 w-3.5 text-danger" />}
                          <span className="text-text-tertiary">
                            {formatRelative(project.last_tested_at, locale)}
                          </span>
                        </span>
                      ) : (
                        <span className="text-text-quaternary">{t('common.never')}</span>
                      )}
                    </td>
                    <td className="whitespace-nowrap px-3 py-2.5 text-caption text-text-tertiary">
                      {project.updated_at ? formatRelative(project.updated_at, locale) : '—'}
                    </td>
                    <td className="px-4 py-2.5 text-right">
                      <Link href={`/builder/${project.id}`}>
                        <Button size="xs" variant="ghost">{t('builder.open')}</Button>
                      </Link>
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
