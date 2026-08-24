'use client';

import * as React from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ShieldCheck, UserPlus } from 'lucide-react';

import { workspaceApi } from '@/lib/api';
import { qk } from '@/lib/queryKeys';
import { formatDateTime } from '@/lib/format';
import { useCurrentUser, useWorkspaceId } from '@/hooks/use-current-user';
import { usePermissions } from '@/hooks/use-permissions';
import { toastError, toastSuccess } from '@/hooks/use-toast';
import { useI18n } from '@/providers/LanguageProvider';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Input, Label, Select } from '@/components/ui/Input';
import { ConfirmDialog, Modal } from '@/components/ui/Modal';
import { ErrorState, TableSkeleton } from '@/components/ui/Feedback';
import { Card, PageListLayout } from '@/components/layout/PageLayout';
import { SettingsTabs } from '@/components/layout/SettingsTabs';

const ROLE_IDS = ['OWNER', 'DATA_ADMIN', 'OPERATOR', 'ANALYST', 'AUDITOR'];

export default function AccessSettingsPage() {
  const { t, tf, locale } = useI18n();
  const roles = ROLE_IDS.map((id) => ({
    id, label: t(`role.${id}`), hint: t(`role.${id}.hint`),
  }));
  const workspaceId = useWorkspaceId();
  const queryClient = useQueryClient();
  const { can, permissions } = usePermissions();
  const { data: me } = useCurrentUser();

  const [inviteOpen, setInviteOpen] = React.useState(false);
  const [removing, setRemoving] = React.useState<{ id: string; name: string } | null>(null);
  const [invite, setInvite] = React.useState({
    email: '', full_name: '', role: 'ANALYST', password: '',
  });

  const members = useQuery({
    queryKey: qk.members(workspaceId),
    queryFn: workspaceApi.members,
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: qk.members(workspaceId) });

  const addMember = useMutation({
    mutationFn: () => workspaceApi.invite(invite),
    onSuccess: () => {
      invalidate();
      setInviteOpen(false);
      setInvite({ email: '', full_name: '', role: 'ANALYST', password: '' });
      toastSuccess(t('settings.memberAdded'));
    },
    onError: (caught) => toastError(caught),
  });

  const changeRole = useMutation({
    mutationFn: ({ id, role }: { id: string; role: string }) => workspaceApi.updateRole(id, role),
    onSuccess: () => { invalidate(); toastSuccess(t('settings.roleUpdated')); },
    onError: (caught) => toastError(caught),
  });

  const removeMember = useMutation({
    mutationFn: (id: string) => workspaceApi.removeMember(id),
    onSuccess: () => { invalidate(); setRemoving(null); toastSuccess(t('settings.memberRemoved')); },
    onError: (caught) => { setRemoving(null); toastError(caught); },
  });

  return (
    <PageListLayout
      title={t('settings.access')}
      searchable={false}
      description={t('settings.accessSubtitle')}
      action={
        can('members', 'create') ? (
          <Button variant="primary" onClick={() => setInviteOpen(true)}
                  leadingIcon={<UserPlus className="h-3.5 w-3.5" />}>
            {t('settings.addMember')}
          </Button>
        ) : null
      }
    >
      <SettingsTabs active="access" />

      <div className="space-y-4">
        {members.error ? (
          <ErrorState title={t('common.errorTitle')} message={(members.error as Error).message}
                      onRetry={() => members.refetch()} />
        ) : members.isLoading ? (
          <TableSkeleton rows={4} columns={4} />
        ) : (
          <Card title={t('settings.members', { n: members.data?.length ?? 0 })} padded={false}>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[640px] text-left">
                <thead>
                  <tr className="border-b border-[rgb(var(--border-line))] text-tiny uppercase tracking-[0.08em] text-text-quaternary">
                    <th scope="col" className="px-4 py-2.5 font-emphasis">
                      {t('settings.colUser')}
                    </th>
                    <th scope="col" className="px-3 py-2.5 font-emphasis">
                      {t('settings.colRole')}
                    </th>
                    <th scope="col" className="px-3 py-2.5 font-emphasis">
                      {t('settings.colJoined')}
                    </th>
                    <th scope="col" className="px-4 py-2.5 text-right font-emphasis">
                      {t('common.actions')}
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[rgb(var(--border-line))]">
                  {(members.data ?? []).map((member) => (
                    <tr key={member.id}>
                      <td className="px-4 py-2.5">
                        <span className="block text-caption font-emphasis text-text-primary">
                          {member.full_name}
                          {member.user_id === me?.id && (
                            <Badge variant="subtle" size="xs" className="ml-1.5">
                              {t('settings.you')}
                            </Badge>
                          )}
                        </span>
                        <span className="text-tiny text-text-quaternary">{member.email}</span>
                      </td>
                      <td className="px-3 py-2.5">
                        {can('members', 'edit') && member.user_id !== me?.id ? (
                          <Select
                            size="sm"
                            className="w-40"
                            value={member.role}
                            aria-label={t('settings.roleLabelFor', { name: member.full_name })}
                            onChange={(event) =>
                              changeRole.mutate({ id: member.id, role: event.target.value })}
                          >
                            {roles.map((role) => (
                              <option key={role.id} value={role.id}>{role.label}</option>
                            ))}
                          </Select>
                        ) : (
                          <Badge variant="neutral" size="sm">{member.role}</Badge>
                        )}
                      </td>
                      <td className="px-3 py-2.5 text-caption text-text-tertiary">
                        {formatDateTime(member.created_at, locale)}
                      </td>
                      <td className="px-4 py-2.5 text-right">
                        {can('members', 'delete') && member.user_id !== me?.id && (
                          <Button
                            size="xs"
                            variant="ghost"
                            onClick={() => setRemoving({ id: member.id, name: member.full_name })}
                          >
                            {t('settings.removeMember')}
                          </Button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        )}

        <Card
          title={t('settings.currentPermissions')}
          description={t('settings.currentPermissionsHint', { role: me?.role ?? '—' })}
        >
          <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
            {Object.entries(permissions ?? {}).map(([module, actions]) => (
              <div key={module}
                   className="rounded-md border border-[rgb(var(--border-line))] px-3 py-2">
                <p className="text-caption font-emphasis text-text-primary">
                  {tf([`module.${module}`], module)}
                </p>
                <div className="mt-1 flex flex-wrap gap-1">
                  {actions.length === 0 ? (
                    <span className="text-tiny text-text-quaternary">
                      {t('settings.noPermission')}
                    </span>
                  ) : (
                    actions.map((action) => (
                      <Badge key={action} variant="subtle" size="xs" pill={false}>{action}</Badge>
                    ))
                  )}
                </div>
              </div>
            ))}
          </div>
        </Card>

        <Card title={t('settings.roleDescriptions')}>
          <ul className="space-y-2">
            {roles.map((role) => (
              <li key={role.id} className="flex items-start gap-2">
                <ShieldCheck className="mt-0.5 h-3.5 w-3.5 flex-shrink-0 text-text-quaternary" />
                <span>
                  <span className="text-caption font-emphasis text-text-primary">{role.label}</span>
                  <span className="ml-1.5 text-caption text-text-tertiary">{role.hint}</span>
                </span>
              </li>
            ))}
          </ul>
        </Card>
      </div>

      <Modal
        open={inviteOpen}
        onClose={() => setInviteOpen(false)}
        title={t('settings.addMember')}
        description={t('settings.inviteBody')}
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => setInviteOpen(false)}>
              {t('common.cancel')}
            </Button>
            <Button variant="primary" size="sm" loading={addMember.isPending}
                    onClick={() => addMember.mutate()}>
              {t('settings.addMember')}
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <div>
            <Label htmlFor="inv-email" required>{t('login.email')}</Label>
            <Input id="inv-email" type="email" value={invite.email}
                   onChange={(event) => setInvite({ ...invite, email: event.target.value })} />
          </div>
          <div>
            <Label htmlFor="inv-name" required>{t('settings.fullName')}</Label>
            <Input id="inv-name" value={invite.full_name}
                   onChange={(event) => setInvite({ ...invite, full_name: event.target.value })} />
          </div>
          <div>
            <Label htmlFor="inv-role" required>{t('settings.inviteRole')}</Label>
            <Select id="inv-role" value={invite.role}
                    onChange={(event) => setInvite({ ...invite, role: event.target.value })}>
              {roles.map((role) => (
                <option key={role.id} value={role.id}>{role.label} — {role.hint}</option>
              ))}
            </Select>
          </div>
          <div>
            <Label htmlFor="inv-pw" required hint={t('settings.invitePasswordHint')}>
              {t('settings.invitePassword')}
            </Label>
            <Input id="inv-pw" type="password" value={invite.password}
                   onChange={(event) => setInvite({ ...invite, password: event.target.value })} />
          </div>
        </div>
      </Modal>

      <ConfirmDialog
        open={Boolean(removing)}
        onClose={() => setRemoving(null)}
        onConfirm={() => removing && removeMember.mutate(removing.id)}
        loading={removeMember.isPending}
        destructive
        title={t('settings.removeTitle')}
        confirmLabel={t('settings.removeMember')}
        message={t('settings.removeBody', { name: removing?.name ?? '' })}
      />
    </PageListLayout>
  );
}
