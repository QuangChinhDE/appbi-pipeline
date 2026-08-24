'use client';

import * as React from 'react';
import Link from 'next/link';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Database, Plus, RefreshCw, Warehouse } from 'lucide-react';

import { destinationApi, sourceApi } from '@/lib/api';
import { qk } from '@/lib/queryKeys';
import { formatRelative } from '@/lib/format';
import { useWorkspaceId } from '@/hooks/use-current-user';
import { usePermissions } from '@/hooks/use-permissions';
import { toastError, toastSuccess } from '@/hooks/use-toast';
import { useI18n } from '@/providers/LanguageProvider';
import { Button } from '@/components/ui/Button';
import { Select } from '@/components/ui/Input';
import { EmptyState, ErrorState, TableSkeleton } from '@/components/ui/Feedback';
import { ModuleOverview, PageListLayout } from '@/components/layout/PageLayout';
import { HealthBadge } from './Badges';
import { ConnectorIcon } from './ConnectorIcon';

export type ActorKind = 'source' | 'destination';

export function ActorListPage({ kind }: { kind: ActorKind }) {
  const { t, locale } = useI18n();
  const workspaceId = useWorkspaceId();
  const queryClient = useQueryClient();
  const { can } = usePermissions();

  const isSource = kind === 'source';
  const api = isSource ? sourceApi : destinationApi;
  const module = isSource ? 'sources' : 'destinations';
  const basePath = isSource ? '/sources' : '/destinations';

  const [search, setSearch] = React.useState('');
  const [debounced, setDebounced] = React.useState('');
  const [health, setHealth] = React.useState('');
  const [connectorKey, setConnectorKey] = React.useState('');
  const [usage, setUsage] = React.useState('');

  // Server-side search: debounce so typing does not hammer the API (section 39.1).
  React.useEffect(() => {
    const timer = setTimeout(() => setDebounced(search), 300);
    return () => clearTimeout(timer);
  }, [search]);

  const filters = { q: debounced || undefined, health: health || undefined,
    connector_key: connectorKey || undefined, usage: usage || undefined };

  const listKey = isSource ? qk.sources(workspaceId, filters) : qk.destinations(workspaceId, filters);
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: listKey,
    queryFn: () => api.list(filters),
  });

  const connectors = useQuery({
    queryKey: qk.connectors(workspaceId, { type: isSource ? 'SOURCE' : 'DESTINATION' }),
    queryFn: () => import('@/lib/api').then((m) =>
      m.connectorApi.list({ type: isSource ? 'SOURCE' : 'DESTINATION' })),
  });

  const [testingId, setTestingId] = React.useState<string | null>(null);
  const testConnection = useMutation({
    mutationFn: (id: string) => api.test(id),
    onMutate: (id) => setTestingId(id),
    onSettled: () => setTestingId(null),
    onSuccess: (result) => {
      if (result.succeeded) toastSuccess(t('sources.testSuccess'));
      else toastError(new Error(result.message ?? t('sources.testFailed')));
      queryClient.invalidateQueries({ queryKey: ['workspace', workspaceId] });
    },
    onError: (caught) => toastError(caught),
  });

  const items = data?.items ?? [];
  const summary = data?.summary ?? {};

  return (
    <PageListLayout
      title={isSource ? t('sources.title') : t('destinations.title')}
      description={isSource ? t('sources.subtitle') : t('destinations.subtitle')}
      searchValue={search}
      onSearchChange={setSearch}
      searchPlaceholder={t('common.search')}
      action={
        can(module, 'create') ? (
          <Link href={`${basePath}/new`}>
            <Button variant="primary" leadingIcon={<Plus className="h-3.5 w-3.5" />}>
              {isSource ? t('sources.add') : t('destinations.add')}
            </Button>
          </Link>
        ) : null
      }
      overview={
        <ModuleOverview
          stats={[
            { label: t('actor.statTotal'), value: summary.total ?? 0 },
            { label: t('actor.statHealthy'), value: summary.healthy ?? 0, tone: 'success' },
            { label: t('actor.statError'), value: summary.error ?? 0,
              tone: (summary.error ?? 0) > 0 ? 'danger' : 'default' },
            { label: t('actor.statNotTested'), value: summary.not_tested ?? 0 },
          ]}
        />
      }
      filters={
        <>
          <Select size="sm" value={health} onChange={(e) => setHealth(e.target.value)}
                  className="w-40" aria-label={t('actor.filterHealth', { value: '' })}>
            <option value="">{t('actor.filterHealth', { value: t('common.all') })}</option>
            <option value="HEALTHY">{t('health.HEALTHY')}</option>
            <option value="WARNING">{t('health.WARNING')}</option>
            <option value="ERROR">{t('health.ERROR')}</option>
            <option value="UNKNOWN">{t('health.UNKNOWN')}</option>
          </Select>
          <Select size="sm" value={connectorKey} onChange={(e) => setConnectorKey(e.target.value)}
                  className="w-48" aria-label={t('actor.filterConnector', { value: '' })}>
            <option value="">{t('actor.filterConnector', { value: t('common.all') })}</option>
            {(connectors.data ?? []).map((connector) => (
              <option key={connector.connector_key} value={connector.connector_key}>
                {connector.display_name}
              </option>
            ))}
          </Select>
          <Select size="sm" value={usage} onChange={(e) => setUsage(e.target.value)}
                  className="w-40" aria-label={t('actor.filterUsage', { value: '' })}>
            <option value="">{t('actor.filterUsage', { value: t('common.all') })}</option>
            <option value="used">{t('actor.usageUsed')}</option>
            <option value="unused">{t('actor.usageUnused')}</option>
          </Select>
        </>
      }
    >
      {error ? (
        <ErrorState title={t('common.errorTitle')} message={(error as Error).message}
                    onRetry={() => refetch()} />
      ) : isLoading ? (
        <TableSkeleton rows={5} columns={6} />
      ) : items.length === 0 ? (
        <EmptyState
          icon={isSource ? Database : Warehouse}
          title={debounced || health || connectorKey
            ? t('common.noResults')
            : t(isSource ? 'sources.emptyTitle' : 'destinations.emptyTitle')}
          description={debounced || health || connectorKey ? undefined : (isSource ? t('sources.empty') : t('destinations.empty'))}
          action={
            can(module, 'create') && !debounced ? (
              <Link href={`${basePath}/new`}>
                <Button variant="primary" leadingIcon={<Plus className="h-3.5 w-3.5" />}>
                  {isSource ? t('sources.add') : t('destinations.add')}
                </Button>
              </Link>
            ) : null
          }
        />
      ) : (
        <div className="overflow-hidden rounded-lg border border-[rgb(var(--border-line))] bg-surface-1">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[880px] text-left">
              <thead>
                <tr className="border-b border-[rgb(var(--border-line))] text-tiny uppercase tracking-[0.08em] text-text-quaternary">
                  <th scope="col" className="px-4 py-2.5 font-emphasis">{t('common.name')}</th>
                  <th scope="col" className="px-3 py-2.5 font-emphasis">{t('common.type')}</th>
                  <th scope="col" className="px-3 py-2.5 font-emphasis">{t('actor.health')}</th>
                  <th scope="col" className="px-3 py-2.5 font-emphasis">{t('sources.usedBy')}</th>
                  <th scope="col" className="px-3 py-2.5 font-emphasis">{t('sources.lastChecked')}</th>
                  <th scope="col" className="px-3 py-2.5 font-emphasis">{t('common.owner')}</th>
                  <th scope="col" className="px-3 py-2.5 font-emphasis">{t('common.updated')}</th>
                  <th
                    scope="col"
                    className="sticky right-0 bg-surface-1 px-4 py-2.5 text-right font-emphasis"
                  >
                    {t('common.actions')}
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[rgb(var(--border-line))]">
                {items.map((actor) => (
                  <tr key={actor.id} className="transition-colors hover:bg-surface-2/60">
                    <td className="px-4 py-2.5">
                      <Link
                        href={`${basePath}/${actor.id}`}
                        className="flex min-w-0 items-center gap-2"
                      >
                        <ConnectorIcon icon={actor.connector_icon} connectorKey={actor.connector_key} size="sm" />
                        <span className="min-w-0">
                          <span className="block truncate text-caption font-emphasis text-text-primary hover:text-brand">
                            {actor.name}
                          </span>
                          {actor.status !== 'ACTIVE' && (
                            <span className="text-tiny text-text-quaternary">{actor.status}</span>
                          )}
                        </span>
                      </Link>
                    </td>
                    <td className="px-3 py-2.5 text-caption text-text-secondary">
                      {actor.connector_display_name ?? actor.connector_key}
                    </td>
                    <td className="px-3 py-2.5"><HealthBadge health={actor.health} size="xs" /></td>
                    <td className="px-3 py-2.5 text-caption tabular-nums text-text-secondary">
                      {actor.pipeline_count}
                    </td>
                    <td className="whitespace-nowrap px-3 py-2.5 text-caption text-text-tertiary">
                      {actor.last_test_at ? formatRelative(actor.last_test_at, locale) : t('common.never')}
                    </td>
                    <td className="px-3 py-2.5 text-caption text-text-tertiary">
                      {actor.owner?.full_name ?? '—'}
                    </td>
                    <td className="whitespace-nowrap px-3 py-2.5 text-caption text-text-tertiary">
                      {formatRelative(actor.updated_at, locale)}
                    </td>
                    <td className="sticky right-0 bg-surface-1 px-4 py-2.5">
                      <div className="flex items-center justify-end gap-1.5">
                        {actor.available_actions.includes('TEST') && (
                          <Button
                            size="xs"
                            variant="ghost"
                            loading={testingId === actor.id}
                            onClick={() => testConnection.mutate(actor.id)}
                            leadingIcon={<RefreshCw className="h-3 w-3" />}
                          >
                            {testingId === actor.id ? t('sources.testing') : t('sources.testConnection')}
                          </Button>
                        )}
                        <Link href={`${basePath}/${actor.id}`}>
                          <Button size="xs" variant="ghost">{t('common.detail')}</Button>
                        </Link>
                      </div>
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
