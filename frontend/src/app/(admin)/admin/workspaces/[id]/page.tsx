'use client';

/**
 * One workspace, seen from outside it.
 *
 * Its members used to live in a modal over a list, which is fine for a quick
 * change and wrong as a destination: a modal cannot hold the health of the
 * thing beside the people in it, and a list whose rows only open modals is a
 * list that goes nowhere.
 */

import * as React from 'react';
import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, ArrowLeft, ArrowRight, CheckCircle2, Trash2, UserPlus } from 'lucide-react';

import { organizationApi } from '@/lib/api';
import { formatRelative } from '@/lib/format';
import { useWorkspaceSwitch } from '@/hooks/use-current-user';
import { toastError, toastSuccess } from '@/hooks/use-toast';
import { useI18n } from '@/providers/LanguageProvider';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Input, Label, Select } from '@/components/ui/Input';
import { CardSkeleton, EmptyState, ErrorState } from '@/components/ui/Feedback';
import { Card, StatTile } from '@/components/layout/PageLayout';

const ROLE_IDS = ['OWNER', 'DATA_ADMIN', 'CONNECTOR_DEV', 'OPERATOR', 'ANALYST', 'AUDITOR'];

export default function AdminWorkspacePage() {
  const { t, locale } = useI18n();
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const queryClient = useQueryClient();
  const switchWorkspace = useWorkspaceSwitch();
  const workspaceId = params.id;

  const [adding, setAdding] = React.useState(false);
  const [draft, setDraft] = React.useState({
    email: '', full_name: '', role: 'ANALYST', password: '',
  });

  const overview = useQuery({
    queryKey: ['org-overview'],
    queryFn: organizationApi.overview,
  });
  const members = useQuery({
    queryKey: ['workspace-members', workspaceId],
    queryFn: () => organizationApi.workspaceMembers(workspaceId),
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['workspace-members', workspaceId] });
    queryClient.invalidateQueries({ queryKey: ['org-overview'] });
    queryClient.invalidateQueries({ queryKey: ['org-people'] });
    queryClient.invalidateQueries({ queryKey: ['org-workspaces'] });
  };

  const add = useMutation({
    mutationFn: () => organizationApi.addWorkspaceMember(workspaceId, {
      ...draft, password: draft.password.trim() || undefined,
    }),
    onSuccess: () => {
      invalidate();
      setAdding(false);
      setDraft({ email: '', full_name: '', role: 'ANALYST', password: '' });
      toastSuccess(t('org.seatGranted'));
    },
    onError: (caught) => toastError(caught),
  });

  const changeRole = useMutation({
    mutationFn: ({ id, role }: { id: string; role: string }) =>
      organizationApi.updateWorkspaceMember(workspaceId, id, { role }),
    onSuccess: () => { invalidate(); toastSuccess(t('settings.roleUpdated')); },
    onError: (caught) => toastError(caught),
  });

  const drop = useMutation({
    mutationFn: (id: string) => organizationApi.removeWorkspaceMember(workspaceId, id),
    onSuccess: () => { invalidate(); toastSuccess(t('org.seatRemoved')); },
    onError: (caught) => toastError(caught),
  });

  if (overview.error) {
    return (
      <ErrorState
        title={t('common.errorTitle')}
        message={(overview.error as Error).message}
        onRetry={() => overview.refetch()}
      />
    );
  }
  if (overview.isLoading) return <CardSkeleton count={3} />;

  const workspace = overview.data?.workspaces.find((w) => w.id === workspaceId);
  if (!workspace) {
    return <ErrorState title={t('admin.workspaceGone')} message={t('admin.workspaceGoneBody')} />;
  }

  return (
    <div className="space-y-4">
      <Link
        href="/admin/workspaces"
        className="-ml-1 inline-flex items-center gap-1 rounded px-1 py-1 text-caption text-text-tertiary transition-colors hover:text-text-primary"
      >
        <ArrowLeft className="h-3.5 w-3.5" />
        {t('admin.workspaces')}
      </Link>

      <header className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <h1 className="flex flex-wrap items-center gap-2 text-h3 font-strong text-text-primary">
            {workspace.name}
            {workspace.failing_count > 0 ? (
              <Badge variant="danger" size="sm">
                <AlertTriangle className="h-2.5 w-2.5" />
                {t('admin.failingCount', { n: workspace.failing_count })}
              </Badge>
            ) : workspace.pipeline_count > 0 ? (
              <Badge variant="success" size="sm">
                <CheckCircle2 className="h-2.5 w-2.5" />
                {t('admin.allHealthy')}
              </Badge>
            ) : null}
          </h1>
          <p className="mt-1 font-mono text-caption text-text-quaternary">{workspace.slug}</p>
        </div>
        <Button
          variant="primary"
          onClick={async () => { await switchWorkspace(workspaceId); router.push('/overview'); }}
        >
          {t('admin.openWorkspace')}
          <ArrowRight className="ml-1 h-3.5 w-3.5" />
        </Button>
      </header>

      <div className="grid auto-rows-fr gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <StatTile label={t('admin.kpiPipelines')} value={workspace.pipeline_count} />
        <StatTile
          label={t('admin.kpiFailing')}
          value={workspace.failing_count}
          tone={workspace.failing_count > 0 ? 'danger' : 'default'}
        />
        <StatTile label={t('org.seatCount', { n: workspace.member_count })}
                  value={workspace.member_count} />
        <StatTile
          label={t('admin.lastRunLabel')}
          value={workspace.last_run_at
            ? formatRelative(workspace.last_run_at, locale)
            : t('admin.neverRun')}
        />
      </div>

      <Card
        title={t('org.seatsIn', { name: workspace.name })}
        description={t('org.seatsHint')}
        padded={false}
        action={
          !adding ? (
            <Button size="xs" variant="secondary" onClick={() => setAdding(true)}
                    leadingIcon={<UserPlus className="h-3 w-3" />}>
              {t('org.grantSeat')}
            </Button>
          ) : null
        }
      >
        {adding && (
          <div className="space-y-3 border-b border-[rgb(var(--border-line))] bg-surface-2 p-3">
            <div className="grid gap-3 md:grid-cols-2 md:items-start md:gap-x-6">
              <div>
                <Label htmlFor="ws-seat-email" required>{t('login.email')}</Label>
                <Input id="ws-seat-email" type="email" value={draft.email}
                       onChange={(e) => setDraft({ ...draft, email: e.target.value })} />
              </div>
              <div>
                <Label htmlFor="ws-seat-name" required>{t('settings.fullName')}</Label>
                <Input id="ws-seat-name" value={draft.full_name}
                       onChange={(e) => setDraft({ ...draft, full_name: e.target.value })} />
              </div>
              <div>
                <Label htmlFor="ws-seat-role">{t('settings.colRole')}</Label>
                <Select id="ws-seat-role" value={draft.role}
                        onChange={(e) => setDraft({ ...draft, role: e.target.value })}>
                  {ROLE_IDS.map((id) => (
                    <option key={id} value={id}>{t(`role.${id}`)}</option>
                  ))}
                </Select>
              </div>
              <div>
                <Label htmlFor="ws-seat-pw" hint={t('org.seatPasswordHint')}>
                  {t('settings.invitePassword')}
                </Label>
                <Input id="ws-seat-pw" type="password" value={draft.password}
                       onChange={(e) => setDraft({ ...draft, password: e.target.value })} />
              </div>
            </div>
            <p className="text-tiny leading-relaxed text-text-quaternary">
              {t('org.seatExistingHint')}
            </p>
            <div className="flex items-center gap-2">
              <Button size="sm" variant="primary" loading={add.isPending}
                      onClick={() => add.mutate()}>
                {t('org.grantSeat')}
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setAdding(false)}>
                {t('common.cancel')}
              </Button>
            </div>
          </div>
        )}

        {members.isLoading ? (
          <div className="p-4"><CardSkeleton count={2} /></div>
        ) : (members.data ?? []).length === 0 ? (
          <EmptyState title={t('org.noSeats')} compact />
        ) : (
          <ul className="divide-y divide-[rgb(var(--border-line))]">
            {(members.data ?? []).map((member) => (
              <li key={member.id}
                  className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-2.5">
                <span className="min-w-0 flex-1 basis-48">
                  <Link
                    href={`/admin/people/${member.user_id}`}
                    className="block truncate text-caption font-emphasis text-text-primary hover:text-brand"
                  >
                    {member.full_name}
                  </Link>
                  <span className="text-tiny text-text-quaternary">{member.email}</span>
                </span>
                <Select
                  size="sm"
                  className="w-44"
                  value={member.role}
                  aria-label={t('settings.roleLabelFor', { name: member.full_name })}
                  onChange={(event) =>
                    changeRole.mutate({ id: member.id, role: event.target.value })}
                >
                  {ROLE_IDS.map((id) => (
                    <option key={id} value={id}>{t(`role.${id}`)}</option>
                  ))}
                </Select>
                {member.customised && (
                  <span className="text-tiny text-text-quaternary">
                    {t('settings.roleEdited')}
                  </span>
                )}
                <Button
                  size="xs"
                  variant="ghost"
                  aria-label={t('org.removeSeatFor', { name: member.full_name })}
                  onClick={() => drop.mutate(member.id)}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
