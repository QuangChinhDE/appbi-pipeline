'use client';

import * as React from 'react';
import { useQuery } from '@tanstack/react-query';
import { ScrollText } from 'lucide-react';

import { opsApi } from '@/lib/api';
import { qk } from '@/lib/queryKeys';
import { formatDateTime } from '@/lib/format';
import { useWorkspaceId } from '@/hooks/use-current-user';
import { useI18n } from '@/providers/LanguageProvider';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Input, Select } from '@/components/ui/Input';
import { EmptyState, ErrorState, TableSkeleton, isPermissionDenied } from '@/components/ui/Feedback';
import { PageListLayout } from '@/components/layout/PageLayout';

const RESOURCE_TYPES = ['SOURCE', 'DESTINATION', 'PIPELINE', 'RUN', 'MEMBER',
  'CONNECTOR', 'WORKSPACE', 'ALERT_RULE', 'USER'];

export default function AuditPage() {
  const { t, locale } = useI18n();
  const workspaceId = useWorkspaceId();

  const [action, setAction] = React.useState('');
  const [resourceType, setResourceType] = React.useState('');
  const [page, setPage] = React.useState(0);
  const limit = 50;

  const filters = {
    action: action || undefined,
    resource_type: resourceType || undefined,
    limit,
    offset: page * limit,
  };

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: qk.audit(workspaceId, filters),
    queryFn: () => opsApi.audit(filters),
  });

  const items = data?.items ?? [];
  const total = data?.page.total ?? 0;
  const [expanded, setExpanded] = React.useState<string | null>(null);

  return (
    <PageListLayout
      title={t('audit.title')}
      description={t('audit.subtitle')}
      searchable={false}
      filters={
        <>
          <div className="w-56">
            <Input
              size="sm"
              value={action}
              placeholder={t('audit.filterAction')}
              aria-label={t('audit.filterAction')}
              onChange={(event) => { setAction(event.target.value); setPage(0); }}
            />
          </div>
          <Select size="sm" className="w-48" value={resourceType}
                  aria-label={t('audit.filterResource', { value: '' })}
                  onChange={(event) => { setResourceType(event.target.value); setPage(0); }}>
            <option value="">{t('audit.filterResource', { value: t('common.all') })}</option>
            {RESOURCE_TYPES.map((value) => (
              <option key={value} value={value}>{value}</option>
            ))}
          </Select>
        </>
      }
    >
      {error ? (
        <ErrorState title={isPermissionDenied(error)
                      ? t('common.noAccessTitle') : t('common.errorTitle')}
                    message={(error as Error).message} error={error}
                    onRetry={() => refetch()} />
      ) : isLoading ? (
        <TableSkeleton rows={8} columns={5} />
      ) : items.length === 0 ? (
        <EmptyState icon={ScrollText} title={t('audit.empty')} />
      ) : (
        <>
          <div className="overflow-hidden rounded-lg border border-[rgb(var(--border-line))] bg-surface-1">
            <div className="overflow-x-auto">
              <table className="w-full min-w-[900px] text-left">
                <thead>
                  <tr className="border-b border-[rgb(var(--border-line))] text-tiny uppercase tracking-[0.08em] text-text-quaternary">
                    <th scope="col" className="px-4 py-2.5 font-emphasis">{t('audit.colTime')}</th>
                    <th scope="col" className="px-3 py-2.5 font-emphasis">{t('audit.colAction')}</th>
                    <th scope="col" className="px-3 py-2.5 font-emphasis">{t('audit.colActor')}</th>
                    <th scope="col" className="px-3 py-2.5 font-emphasis">{t('audit.colResource')}</th>
                    <th scope="col" className="px-3 py-2.5 font-emphasis">{t('audit.colResult')}</th>
                    <th scope="col" className="px-4 py-2.5 font-emphasis">{t('audit.colTrace')}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[rgb(var(--border-line))]">
                  {items.map((event) => (
                    <React.Fragment key={event.id}>
                      <tr
                        className="cursor-pointer transition-colors hover:bg-surface-2/60"
                        onClick={() => setExpanded(expanded === event.id ? null : event.id)}
                      >
                        <td className="whitespace-nowrap px-4 py-2.5 text-caption text-text-tertiary">
                          {formatDateTime(event.created_at, locale)}
                        </td>
                        <td className="px-3 py-2.5">
                          <span className="font-mono text-caption text-text-primary">
                            {event.action}
                          </span>
                        </td>
                        <td className="px-3 py-2.5 text-caption text-text-secondary">
                          {event.actor_label ?? event.actor_type}
                          {event.actor_type !== 'USER' && (
                            <Badge variant="subtle" size="xs" className="ml-1.5">
                              {event.actor_type}
                            </Badge>
                          )}
                        </td>
                        <td className="px-3 py-2.5 text-caption text-text-secondary">
                          {event.resource_name ?? event.resource_type ?? '—'}
                          {event.resource_type && (
                            <span className="ml-1.5 text-tiny text-text-quaternary">
                              {event.resource_type}
                            </span>
                          )}
                        </td>
                        <td className="px-3 py-2.5">
                          <Badge variant={event.result === 'SUCCESS' ? 'success' : 'danger'}
                                 size="xs">
                            {event.result}
                          </Badge>
                        </td>
                        <td className="px-4 py-2.5 font-mono text-tiny text-text-quaternary">
                          {event.trace_id?.slice(0, 14) ?? '—'}
                        </td>
                      </tr>
                      {expanded === event.id && (event.before_summary || event.after_summary) && (
                        <tr className="bg-surface-2/40">
                          <td colSpan={6} className="px-4 py-3">
                            <div className="grid gap-3 sm:grid-cols-2">
                              <SummaryBlock title={t('audit.before')}
                                            payload={event.before_summary} />
                              <SummaryBlock title={t('audit.after')}
                                            payload={event.after_summary} />
                            </div>
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {total > limit && (
            <div className="mt-3 flex items-center justify-between">
              <span className="text-caption text-text-tertiary">
                {t('common.showing', { n: items.length, total })}
              </span>
              <div className="flex gap-2">
                <Button size="sm" variant="secondary" disabled={page === 0}
                        onClick={() => setPage((p) => Math.max(0, p - 1))}>
                  {t('common.prevPage')}
                </Button>
                <Button size="sm" variant="secondary" disabled={!data?.page.has_more}
                        onClick={() => setPage((p) => p + 1)}>
                  {t('common.nextPage')}
                </Button>
              </div>
            </div>
          )}
        </>
      )}
    </PageListLayout>
  );
}

function SummaryBlock({
  title, payload,
}: {
  title: string;
  payload: Record<string, unknown> | null;
}) {
  if (!payload) return null;
  return (
    <div>
      <p className="mb-1 text-tiny uppercase tracking-[0.08em] text-text-quaternary">{title}</p>
      <pre className="overflow-x-auto rounded-md bg-surface-1 p-2 font-mono text-tiny leading-relaxed text-text-secondary">
        {JSON.stringify(payload, null, 2)}
      </pre>
    </div>
  );
}
