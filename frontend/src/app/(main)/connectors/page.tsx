'use client';

import * as React from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Boxes, ExternalLink, RefreshCw } from 'lucide-react';

import { connectorApi } from '@/lib/api';
import { qk } from '@/lib/queryKeys';
import { supportLevelKey } from '@/lib/format';
import { useWorkspaceId } from '@/hooks/use-current-user';
import { usePermissions } from '@/hooks/use-permissions';
import { toastError, toastSuccess } from '@/hooks/use-toast';
import { useI18n } from '@/providers/LanguageProvider';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Select } from '@/components/ui/Input';
import { EmptyState, ErrorState, TableSkeleton } from '@/components/ui/Feedback';
import { ModuleOverview, PageListLayout } from '@/components/layout/PageLayout';
import { CertificationBadge } from '@/components/integrations/Badges';
import { ConnectorIcon } from '@/components/integrations/ConnectorIcon';

/** How many connector cards to render before asking for more. */
const PAGE = 24;

export default function ConnectorsPage() {
  const { t } = useI18n();
  const workspaceId = useWorkspaceId();
  const queryClient = useQueryClient();
  const { isPlatformAdmin } = usePermissions();

  const [search, setSearch] = React.useState('');
  const [type, setType] = React.useState('');
  // The catalogue is the full upstream registry; 650+ detail cards at once is a
  // wall of text, so it is paged and the count is always stated.
  const [limit, setLimit] = React.useState(PAGE);

  React.useEffect(() => setLimit(PAGE), [search, type]);

  const filters = { q: search || undefined, type: type || undefined };
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: qk.connectors(workspaceId, filters),
    queryFn: () => connectorApi.list(filters),
  });

  const refresh = useMutation({
    mutationFn: (connectorKey?: string) => connectorApi.refresh(connectorKey),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['workspace', workspaceId] });
      const changed = Object.entries(result.result).filter(([, value]) => value === 'changed');
      toastSuccess(
        t('connectors.refreshed'),
        changed.length
          ? t('connectors.refreshedChanged', { n: changed.length })
          : t('connectors.refreshedNone'),
      );
    },
    onError: (caught) => toastError(caught),
  });

  const items = data ?? [];
  const visible = items.slice(0, limit);

  return (
    <PageListLayout
      title={t('connectors.title')}
      description={t('connectors.subtitle')}
      searchValue={search}
      onSearchChange={setSearch}
      searchPlaceholder={t('connectors.search')}
      action={
        isPlatformAdmin ? (
          <Button
            variant="secondary"
            loading={refresh.isPending}
            onClick={() => refresh.mutate(undefined)}
            leadingIcon={<RefreshCw className="h-3.5 w-3.5" />}
          >
            {t('connectors.refresh')}
          </Button>
        ) : null
      }
      overview={
        <ModuleOverview
          stats={[
            { label: t('connectors.statTotal'), value: items.length },
            { label: t('connectors.statCertified'),
              value: items.filter((c) => c.certification === 'SUPPORTED').length,
              tone: 'success' },
            { label: t('connectors.statBeta'),
              value: items.filter((c) => c.certification === 'BETA').length },
            { label: t('connectors.statUpdates'),
              value: items.filter((c) => c.update_available).length },
          ]}
        />
      }
      filters={
        <Select size="sm" className="w-44" value={type}
                aria-label={t('connectors.filterType', { value: '' })}
                onChange={(event) => setType(event.target.value)}>
          <option value="">{t('connectors.filterType', { value: t('common.all') })}</option>
          <option value="SOURCE">{t('connectors.typeSource')}</option>
          <option value="DESTINATION">{t('connectors.typeDestination')}</option>
        </Select>
      }
    >
      {error ? (
        <ErrorState title={t('common.errorTitle')} message={(error as Error).message}
                    onRetry={() => refetch()} />
      ) : isLoading ? (
        <TableSkeleton rows={8} columns={7} />
      ) : items.length === 0 ? (
        <EmptyState icon={Boxes} title={t('common.noResults')} />
      ) : (
        /* A catalogue of 650+ entries is a reference list, not a gallery. Cards
           this dense left ragged gaps between rows and buried the few connectors
           we certify; a table keeps every row the same height and scannable. */
        <div className="overflow-hidden rounded-lg border border-[rgb(var(--border-line))] bg-surface-1">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[900px] text-left">
              <thead>
                <tr className="border-b border-[rgb(var(--border-line))] text-tiny uppercase tracking-[0.08em] text-text-quaternary">
                  <th scope="col" className="px-4 py-2.5 font-emphasis">Connector</th>
                  <th scope="col" className="px-3 py-2.5 font-emphasis">{t('connectors.columnType')}</th>
                  <th scope="col" className="px-3 py-2.5 font-emphasis">{t('connectors.group')}</th>
                  <th scope="col" className="whitespace-nowrap px-3 py-2.5 font-emphasis">
                    {t('connectors.pinnedVersion')}
                  </th>
                  <th scope="col" className="whitespace-nowrap px-3 py-2.5 font-emphasis">
                    {t('connectors.support')}
                  </th>
                  <th scope="col" className="px-3 py-2.5 text-right font-emphasis">
                    {t('connectors.usedBy')}
                  </th>
                  <th scope="col" className="px-4 py-2.5 text-right font-emphasis">
                    {t('common.actions')}
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[rgb(var(--border-line))]">
                {visible.map((connector) => (
                  <tr key={connector.connector_key} className="transition-colors hover:bg-surface-2/60">
                    <td className="px-4 py-2">
                      <div className="flex items-center gap-2.5">
                        <ConnectorIcon
                          icon={connector.icon}
                          connectorKey={connector.connector_key}
                          size="md"
                        />
                        <div className="min-w-0">
                          <div className="flex items-center gap-1.5">
                            <span className="truncate text-caption font-emphasis text-text-primary">
                              {connector.display_name}
                            </span>
                            {connector.certification === 'SUPPORTED' && (
                              <CertificationBadge certification={connector.certification} />
                            )}
                          </div>
                          <div className="flex items-center gap-1.5">
                            <span className="truncate font-mono text-tiny text-text-quaternary">
                              {connector.connector_key}
                            </span>
                            {connector.supports_cdc && (
                              <Badge variant="subtle" size="xs" pill={false}>CDC</Badge>
                            )}
                            {connector.supports_oauth && (
                              <Badge variant="subtle" size="xs" pill={false}>OAuth</Badge>
                            )}
                          </div>
                        </div>
                      </div>
                    </td>
                    <td className="px-3 py-2 text-caption text-text-secondary">
                      {t(connector.connector_type === 'SOURCE'
                        ? 'connectors.typeSource' : 'connectors.typeDestination')}
                    </td>
                    <td className="px-3 py-2 text-caption text-text-secondary">
                      {connector.category}
                    </td>
                    <td className="whitespace-nowrap px-3 py-2">
                      <span className="font-mono text-tiny text-text-secondary">
                        {connector.version}
                      </span>
                      {connector.update_available && (
                        <Badge variant="info" size="xs" className="ml-1.5">
                          {t('connectors.newVersion', { version: connector.latest_version ?? '' })}
                        </Badge>
                      )}
                      <span className="block text-tiny text-text-quaternary">
                        {connector.release_stage}
                      </span>
                    </td>
                    <td className="whitespace-nowrap px-3 py-2 text-caption text-text-secondary">
                      {t(supportLevelKey(connector.support_level))}
                      <span className="block text-tiny text-text-quaternary">
                        {connector.image_pulled
                          ? t('connectors.imagePulled') : t('connectors.imageLazy')}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-right text-caption tabular-nums text-text-secondary">
                      {connector.usage_count}
                    </td>
                    <td className="px-4 py-2">
                      <div className="flex items-center justify-end gap-1.5">
                        {connector.documentation_url && (
                          <a
                            href={connector.documentation_url}
                            target="_blank"
                            rel="noreferrer noopener"
                            aria-label={`${t('connectors.openDocs')} - ${connector.display_name}`}
                            className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-tiny font-emphasis text-brand hover:bg-brand/10"
                          >
                            {t('connectors.docsShort')}
                            <ExternalLink className="h-3 w-3" />
                          </a>
                        )}
                        {isPlatformAdmin && (
                          <Button
                            size="xs"
                            variant="ghost"
                            loading={refresh.isPending
                              && refresh.variables === connector.connector_key}
                            onClick={() => refresh.mutate(connector.connector_key)}
                          >
                            {t('connectors.readSpecShort')}
                          </Button>
                        )}
                      </div>
                      {connector.disabled_reason && (
                        <p className="mt-0.5 text-right text-tiny text-danger">
                          {connector.disabled_reason}
                        </p>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {!isLoading && !error && visible.length > 0 && (
        <div className="mt-3 flex items-center justify-between gap-3">
          <p className="text-tiny text-text-quaternary">
            {t('connectors.showing', {
              shown: String(visible.length), total: String(items.length),
            })}
          </p>
          {visible.length < items.length && (
            <Button size="xs" variant="secondary" onClick={() => setLimit((n) => n + 48)}>
              {t('wizard.showMoreConnectors')}
            </Button>
          )}
        </div>
      )}
    </PageListLayout>
  );
}

