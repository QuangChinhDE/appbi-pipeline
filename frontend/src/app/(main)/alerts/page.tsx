'use client';

import * as React from 'react';
import Link from 'next/link';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Bell, BellOff, CheckCheck } from 'lucide-react';

import { opsApi } from '@/lib/api';
import { qk } from '@/lib/queryKeys';
import { formatRelative } from '@/lib/format';
import { useWorkspaceId } from '@/hooks/use-current-user';
import { usePermissions } from '@/hooks/use-permissions';
import { useUrlTab } from '@/hooks/use-url-tab';
import { toastError, toastSuccess } from '@/hooks/use-toast';
import { useI18n } from '@/providers/LanguageProvider';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Toggle } from '@/components/ui/Input';
import { EmptyState, ErrorState, TableSkeleton, isPermissionDenied } from '@/components/ui/Feedback';
import { Tabs } from '@/components/ui/Tabs';
import { Card, ModuleOverview, PageListLayout } from '@/components/layout/PageLayout';

const SEVERITY_VARIANT: Record<string, 'info' | 'warning' | 'danger'> = {
  INFO: 'info', WARNING: 'warning', ERROR: 'danger', CRITICAL: 'danger',
};
const ALERT_TABS = ['notifications', 'rules'] as const;

export default function AlertsPage() {
  const { t, tf, locale } = useI18n();
  const eventLabel = (event: string) => tf([`alerts.event.${event}`], event);
  const workspaceId = useWorkspaceId();
  const queryClient = useQueryClient();
  const { can } = usePermissions();
  const { tab, hrefForTab } = useUrlTab(ALERT_TABS, 'notifications');

  const notifications = useQuery({
    queryKey: qk.notifications(workspaceId, {}),
    queryFn: () => opsApi.notifications({ limit: 100 }),
    refetchInterval: 30_000,
  });

  const rules = useQuery({
    queryKey: qk.alertRules(workspaceId),
    queryFn: opsApi.alertRules,
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['workspace', workspaceId] });

  const acknowledge = useMutation({
    mutationFn: (id?: string) => opsApi.acknowledge(id),
    onSuccess: (result) => { toastSuccess(result.message ?? t('alerts.updated')); invalidate(); },
    onError: (caught) => toastError(caught),
  });

  const toggleRule = useMutation({
    mutationFn: ({ id, enabled, rule }: { id: string; enabled: boolean; rule: any }) =>
      opsApi.updateRule(id, { ...rule, enabled }),
    onSuccess: () => { toastSuccess(t('alerts.ruleUpdated')); invalidate(); },
    onError: (caught) => toastError(caught),
  });

  const unread = (notifications.data ?? []).filter((n) => n.status === 'NEW');

  return (
    <PageListLayout
      title={t('alerts.title')}
      description={t('alerts.subtitle')}
      searchable={false}
      action={
        tab === 'notifications' && unread.length > 0 && can('alerts', 'operate') ? (
          <Button
            variant="secondary"
            loading={acknowledge.isPending}
            onClick={() => acknowledge.mutate(undefined)}
            leadingIcon={<CheckCheck className="h-3.5 w-3.5" />}
          >
            {t('alerts.acknowledgeAll')}
          </Button>
        ) : null
      }
      overview={
        <ModuleOverview stats={[
          { label: t('alerts.summary.unread'), value: unread.length, tone: unread.length ? 'warning' : 'default' },
          { label: t('alerts.summary.total'), value: notifications.data?.length ?? 0 },
          {
            label: t('alerts.summary.enabledRules'),
            value: (rules.data ?? []).filter((rule) => rule.enabled).length,
          },
        ]} />
      }
    >
      <Tabs
        className="mb-4"
        value={tab}
        items={[
          {
            id: 'notifications', label: t('alerts.notifications'), count: unread.length,
            href: hrefForTab('notifications'),
          },
          {
            id: 'rules', label: t('alerts.rules'), count: rules.data?.length,
            href: hrefForTab('rules'),
          },
        ]}
      />

      {tab === 'notifications' && (
        notifications.error ? (
          <ErrorState title={isPermissionDenied(notifications.error)
                        ? t('common.noAccessTitle') : t('common.errorTitle')}
                      message={(notifications.error as Error).message}
                      error={notifications.error}
                      onRetry={() => notifications.refetch()} />
        ) : notifications.isLoading ? (
          <TableSkeleton rows={5} columns={4} />
        ) : (notifications.data ?? []).length === 0 ? (
          <EmptyState icon={BellOff} title={t('alerts.empty')}
                      description={t('alerts.emptyBody')} />
        ) : (
          <ul className="space-y-2">
            {(notifications.data ?? []).map((notification) => (
              <li
                key={notification.id}
                className={
                  notification.status === 'NEW'
                    ? 'rounded-lg border border-[rgb(var(--border-strong))] bg-surface-1 p-3.5'
                    : 'rounded-lg border border-[rgb(var(--border-line))] bg-surface-1/60 p-3.5'
                }
              >
                <div className="flex items-start gap-3">
                  <Bell className={
                    notification.status === 'NEW'
                      ? 'mt-0.5 h-4 w-4 flex-shrink-0 text-brand'
                      : 'mt-0.5 h-4 w-4 flex-shrink-0 text-text-quaternary'
                  } />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-caption font-strong text-text-primary">
                        {notification.title}
                      </span>
                      <Badge variant={SEVERITY_VARIANT[notification.severity] ?? 'info'} size="xs">
                        {eventLabel(notification.event_type)}
                      </Badge>
                      {notification.occurrence_count > 1 && (
                        <Badge variant="subtle" size="xs">
                          ×{notification.occurrence_count}
                        </Badge>
                      )}
                      {notification.status !== 'NEW' && (
                        <Badge variant="neutral" size="xs">{t('alerts.seen')}</Badge>
                      )}
                    </div>
                    {notification.body && (
                      <p className="mt-0.5 text-caption leading-relaxed text-text-secondary">
                        {notification.body}
                      </p>
                    )}
                    <div className="mt-1.5 flex flex-wrap items-center gap-3">
                      <span className="text-tiny text-text-quaternary">
                        {formatRelative(notification.created_at, locale)}
                      </span>
                      {notification.run_id && (
                        <Link href={`/runs/${notification.run_id}?tab=summary`}
                              className="text-tiny text-brand hover:underline">
                          {t('alerts.viewRun')}
                        </Link>
                      )}
                      {notification.resource_type === 'PIPELINE' && notification.resource_id && (
                        <Link href={`/pipelines/${notification.resource_id}?tab=status`}
                              className="text-tiny text-brand hover:underline">
                          {t('alerts.openPipeline')}
                        </Link>
                      )}
                      {notification.resource_type === 'SOURCE' && notification.resource_id && (
                        <Link href={`/sources/${notification.resource_id}?tab=overview`}
                              className="text-tiny text-brand hover:underline">
                          {t('alerts.openSource')}
                        </Link>
                      )}
                      {notification.remediation_action && (
                        <span className="text-tiny text-text-tertiary">
                          {t('alerts.hint', {
                            action: t(`action.${notification.remediation_action}`) })}
                        </span>
                      )}
                    </div>
                  </div>
                  {notification.status === 'NEW' && can('alerts', 'operate') && (
                    <Button size="xs" variant="ghost"
                            onClick={() => acknowledge.mutate(notification.id)}>
                      {t('alerts.acknowledge')}
                    </Button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )
      )}

      {tab === 'rules' && (
        rules.isLoading ? (
          <TableSkeleton rows={4} columns={4} />
        ) : (
          <Card padded={false}>
            {(rules.data ?? []).length === 0 ? (
              <div className="p-4">
                <EmptyState
                  icon={BellOff}
                  title={t('alerts.noRules')}
                  description={t('alerts.noRulesBody')}
                  compact
                />
              </div>
            ) : (
              <ul className="divide-y divide-[rgb(var(--border-line))]">
              {(rules.data ?? []).map((rule) => (
                <li key={rule.id} className="flex items-center justify-between gap-4 px-4 py-3">
                  <div className="min-w-0">
                    <p className="text-caption font-emphasis text-text-primary">{rule.name}</p>
                    <p className="mt-0.5 text-tiny text-text-tertiary">
                      {eventLabel(rule.event_type)}
                      {' · '}{t('alerts.threshold')}: {rule.threshold}
                      {' · '}{t('alerts.cooldown')}:{' '}
                      {t('alerts.minutes', { n: Math.round(rule.cooldown_seconds / 60) })}
                      {' · '}{t('alerts.channel')}: {rule.channel}
                    </p>
                  </div>
                  <Toggle
                    checked={rule.enabled}
                    disabled={!can('alerts', 'edit') || toggleRule.isPending}
                    onChange={(enabled) =>
                      toggleRule.mutate({ id: rule.id, enabled, rule })}
                    label={rule.enabled ? t('common.on') : t('common.off')}
                  />
                </li>
              ))}
              </ul>
            )}
          </Card>
        )
      )}
    </PageListLayout>
  );
}
