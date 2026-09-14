'use client';

import * as React from 'react';
import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, CheckCircle2, Server } from 'lucide-react';

import { connectorApi, opsApi } from '@/lib/api';
import { qk } from '@/lib/queryKeys';
import { formatDateTime, formatDuration } from '@/lib/format';
import { useWorkspaceId } from '@/hooks/use-current-user';
import { usePermissions } from '@/hooks/use-permissions';
import { useI18n } from '@/providers/LanguageProvider';
import { Badge } from '@/components/ui/Badge';
import { EmptyState, ErrorState, Spinner } from '@/components/ui/Feedback';
import { Card, PageListLayout, StatTile } from '@/components/layout/PageLayout';
import { SettingsTabs } from '@/components/layout/SettingsTabs';

export default function EngineSettingsPage() {
  const { t, locale } = useI18n();
  const workspaceId = useWorkspaceId();
  const { isPlatformAdmin } = usePermissions();

  const engine = useQuery({
    queryKey: qk.engine(workspaceId),
    queryFn: opsApi.engineStatus,
    refetchInterval: 30_000,
  });

  const compatibility = useQuery({
    queryKey: [...qk.engine(workspaceId), 'compatibility'],
    queryFn: connectorApi.compatibility,
    enabled: isPlatformAdmin,
    retry: false,
  });

  if (!isPlatformAdmin) {
    return (
      <PageListLayout title={t('settings.engine')} searchable={false}>
        <SettingsTabs active="engine" />
        <EmptyState
          icon={Server}
          title={t('settings.adminOnly')}
          description={t('settings.adminOnlyBody')}
        />
      </PageListLayout>
    );
  }

  return (
    <PageListLayout
      title={t('settings.engine')}
      searchable={false}
      description={t('settings.engineSubtitle')}
    >
      <SettingsTabs active="engine" />

      {engine.error ? (
        <ErrorState title={t('common.errorTitle')} message={(engine.error as Error).message}
                    onRetry={() => engine.refetch()} />
      ) : engine.isLoading ? (
        <Spinner />
      ) : (
        <div className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <StatTile
              label={t('common.status')}
              value={
                <span className="flex items-center gap-1.5 text-h3">
                  {engine.data?.operational ? (
                    <CheckCircle2 className="h-4 w-4 text-success" />
                  ) : (
                    <AlertTriangle className="h-4 w-4 text-danger" />
                  )}
                  {t(engine.data?.operational ? 'settings.engineOk' : 'settings.engineDown')}
                </span>
              }
              helper={engine.data?.checked_at
                ? t('settings.checkedAt', {
                    time: formatDateTime(engine.data.checked_at, locale) }) : undefined}
            />
            <StatTile label={t('monitoring.running')} value={engine.data?.active_runs ?? 0} />
            <StatTile label={t('monitoring.queued')} value={engine.data?.queued_runs ?? 0} />
            <StatTile
              label={t('monitoring.lag')}
              value={formatDuration(engine.data?.reconciliation_lag_seconds ?? null)}
              helper={t('settings.lagTarget')}
            />
          </div>

          <Card title={t('monitoring.engine')}>
            <dl className="grid gap-x-6 gap-y-2 sm:grid-cols-2">
              <Row label={t('settings.engineType')} value={engine.data?.engine_type ?? '—'} />
              <Row label={t('monitoring.version')} value={engine.data?.version ?? '—'} />
              <Row label={t('monitoring.adapterContract')}
                   value={`v${engine.data?.adapter_contract_version ?? '—'}`} />
              <Row label={t('settings.productVersion')}
                   value={engine.data?.product_version ?? '—'} />
              {engine.data?.detail && (
                <Row label={t('settings.engineDetail')} value={engine.data.detail} />
              )}
              {engine.data?.metrics && Object.entries(engine.data.metrics).map(([key, value]) => (
                <Row key={key} label={key} value={String(value)} />
              ))}
            </dl>
            <p className="mt-3 text-tiny leading-relaxed text-text-quaternary">
              {t('settings.engineNote')}
            </p>
          </Card>

          <Card
            title={t('settings.matrix')}
            description={t('settings.matrixHint')}
            padded={false}
          >
            {compatibility.isLoading ? (
              <Spinner />
            ) : compatibility.error ? (
              <div className="p-4">
                <ErrorState title={t('settings.matrixError')}
                            message={(compatibility.error as Error).message} compact />
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[720px] text-left">
                  <thead>
                    <tr className="border-b border-[rgb(var(--border-line))] text-tiny uppercase tracking-[0.08em] text-text-quaternary">
                      <th scope="col" className="px-4 py-2.5 font-emphasis">
                        {t('settings.colConnector')}
                      </th>
                      <th scope="col" className="px-3 py-2.5 font-emphasis">
                        {t('settings.colImage')}
                      </th>
                      <th scope="col" className="px-3 py-2.5 font-emphasis">
                        {t('settings.colCertification')}
                      </th>
                      <th scope="col" className="px-3 py-2.5 font-emphasis">
                        {t('settings.colSpecSource')}
                      </th>
                      <th scope="col" className="px-4 py-2.5 font-emphasis">
                        {t('settings.colRefreshed')}
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[rgb(var(--border-line))]">
                    {Object.entries(compatibility.data?.connectors ?? {}).map(([key, value]) => (
                      <tr key={key}>
                        <td className="px-4 py-2.5 font-mono text-caption text-text-primary">
                          {key}
                        </td>
                        <td className="px-3 py-2.5 font-mono text-tiny text-text-secondary">
                          {value.image}
                        </td>
                        <td className="px-3 py-2.5">
                          <Badge
                            variant={value.certification === 'SUPPORTED' ? 'success'
                              : value.certification === 'BLOCKED' ? 'danger' : 'warning'}
                            size="xs"
                          >
                            {value.certification}
                          </Badge>
                        </td>
                        <td className="px-3 py-2.5 text-caption text-text-tertiary">
                          {value.spec_source}
                        </td>
                        <td className="px-4 py-2.5 text-caption text-text-tertiary">
                          {value.last_refreshed_at
                            ? formatDateTime(value.last_refreshed_at, locale)
                            : t('common.never')}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </div>
      )}
    </PageListLayout>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-caption text-text-tertiary">{label}</dt>
      <dd className="text-right text-caption text-text-primary">{value}</dd>
    </div>
  );
}
