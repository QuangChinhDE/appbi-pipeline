'use client';

import * as React from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Save } from 'lucide-react';

import { workspaceApi } from '@/lib/api';
import { qk } from '@/lib/queryKeys';
import { useWorkspaceId } from '@/hooks/use-current-user';
import { usePermissions } from '@/hooks/use-permissions';
import { toastError, toastSuccess } from '@/hooks/use-toast';
import { useI18n } from '@/providers/LanguageProvider';
import { Button } from '@/components/ui/Button';
import { Input, Label, Select, Toggle } from '@/components/ui/Input';
import { ErrorState, Spinner } from '@/components/ui/Feedback';
import { Card, PageListLayout } from '@/components/layout/PageLayout';
import { SettingsTabs } from '@/components/layout/SettingsTabs';

const TIMEZONES = [
  'Asia/Bangkok', 'Asia/Ho_Chi_Minh', 'Asia/Singapore', 'Asia/Tokyo',
  'Europe/London', 'Europe/Berlin', 'America/New_York', 'UTC',
];

export default function WorkspaceSettingsPage() {
  const { t } = useI18n();
  const workspaceId = useWorkspaceId();
  const queryClient = useQueryClient();
  const { can } = usePermissions();
  const editable = can('settings', 'edit');

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: qk.settings(workspaceId),
    queryFn: workspaceApi.settings,
  });

  const [form, setForm] = React.useState<{
    name: string; timezone: string;
    allow_save_without_test: boolean; auto_accept_additive_schema: boolean;
  } | null>(null);

  React.useEffect(() => {
    if (data && form === null) {
      setForm({
        name: data.name,
        timezone: data.timezone,
        allow_save_without_test: data.allow_save_without_test,
        auto_accept_additive_schema: data.auto_accept_additive_schema,
      });
    }
  }, [data, form]);

  const save = useMutation({
    mutationFn: () => workspaceApi.updateSettings(form ?? {}),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['workspace', workspaceId] });
      queryClient.invalidateQueries({ queryKey: qk.me() });
      toastSuccess(t('settings.saved'));
    },
    onError: (caught) => toastError(caught),
  });

  return (
    <PageListLayout title={t('settings.workspace')} searchable={false}
                    description={t('settings.workspaceSubtitle')}>
      <SettingsTabs active="workspace" />

      {error ? (
        <ErrorState title={t('common.errorTitle')} message={(error as Error).message}
                    onRetry={() => refetch()} />
      ) : isLoading || !form ? (
        <Spinner />
      ) : (
        <div className="max-w-2xl space-y-4">
          <Card title={t('settings.workspaceInfo')}>
            <div className="space-y-3">
              <div>
                <Label htmlFor="ws-name" required>{t('settings.workspaceName')}</Label>
                <Input
                  id="ws-name"
                  value={form.name}
                  disabled={!editable}
                  onChange={(event) => setForm({ ...form, name: event.target.value })}
                />
              </div>
              <div>
                <Label htmlFor="ws-slug">{t('settings.workspaceSlug')}</Label>
                <Input id="ws-slug" value={data?.slug ?? ''} readOnly className="font-mono" />
              </div>
              <div>
                <Label htmlFor="ws-tz" required>{t('settings.defaultTimezone')}</Label>
                <Select
                  id="ws-tz"
                  value={form.timezone}
                  disabled={!editable}
                  onChange={(event) => setForm({ ...form, timezone: event.target.value })}
                >
                  {TIMEZONES.map((zone) => <option key={zone} value={zone}>{zone}</option>)}
                </Select>
              </div>
            </div>
          </Card>

          <Card title={t('settings.policies')}>
            <div className="space-y-4">
              <Toggle
                checked={form.allow_save_without_test}
                disabled={!editable}
                onChange={(value) => setForm({ ...form, allow_save_without_test: value })}
                label={t('settings.allowUntested')}
                description={t('settings.allowUntestedHint')}
              />
              <Toggle
                checked={form.auto_accept_additive_schema}
                disabled={!editable}
                onChange={(value) => setForm({ ...form, auto_accept_additive_schema: value })}
                label={t('settings.autoAccept')}
                description={t('settings.autoAcceptHint')}
              />
            </div>
          </Card>

          <Card title={t('settings.limits')} description={t('settings.limitsHint')}>
            <dl className="space-y-2.5">
              <div className="flex items-baseline justify-between gap-3">
                <dt className="text-caption text-text-tertiary">{t('settings.minInterval')}</dt>
                <dd className="text-caption text-text-primary">
                  {t('alerts.minutes', {
                    n: Math.round((data?.min_schedule_interval_seconds ?? 0) / 60) })}
                </dd>
              </div>
              <div className="flex items-baseline justify-between gap-3">
                <dt className="text-caption text-text-tertiary">
                  {t('settings.maxConcurrent')}
                </dt>
                <dd className="text-caption text-text-primary">
                  {data?.max_concurrent_runs_per_workspace}
                </dd>
              </div>
            </dl>
          </Card>

          {editable && (
            <Button
              variant="primary"
              loading={save.isPending}
              onClick={() => save.mutate()}
              leadingIcon={<Save className="h-3.5 w-3.5" />}
            >
              {t('common.save')}
            </Button>
          )}
        </div>
      )}
    </PageListLayout>
  );
}
