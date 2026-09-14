'use client';

import * as React from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ShieldCheck, SlidersHorizontal, UserPlus } from 'lucide-react';

import { authApi, workspaceApi } from '@/lib/api';
import { qk } from '@/lib/queryKeys';
import { formatDateTime } from '@/lib/format';
import { cn } from '@/lib/utils';
import { useCurrentUser, useWorkspaceId } from '@/hooks/use-current-user';
import { summarisePermissions, usePermissions } from '@/hooks/use-permissions';
import { toastError, toastSuccess } from '@/hooks/use-toast';
import { useI18n } from '@/providers/LanguageProvider';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Input, Label, Select } from '@/components/ui/Input';
import { ConfirmDialog, Modal } from '@/components/ui/Modal';
import { ErrorState, TableSkeleton } from '@/components/ui/Feedback';
import { Card, PageListLayout } from '@/components/layout/PageLayout';
import { PermissionEditor } from '@/components/settings/PermissionEditor';
import type { Member, PermissionMap } from '@/lib/types';
import { SettingsTabs } from '@/components/layout/SettingsTabs';

// Order runs from most to least authority so the picker reads as a ladder.
// PLATFORM_ADMIN is missing on purpose: it is an account property rather than a
// membership, so offering it here would be a control that silently does nothing.
const ROLE_IDS = [
  'OWNER', 'DATA_ADMIN', 'CONNECTOR_DEV', 'OPERATOR', 'ANALYST', 'AUDITOR',
];

export default function AccessSettingsPage() {
  const { t, tf, locale } = useI18n();
  const roles = ROLE_IDS.map((id) => ({
    id, label: t(`role.${id}`), hint: t(`role.${id}.hint`),
  }));
  const workspaceId = useWorkspaceId();
  const queryClient = useQueryClient();
  const { can, permissions, levels } = usePermissions();
  const { data: me } = useCurrentUser();

  const [inviteOpen, setInviteOpen] = React.useState(false);
  const [removing, setRemoving] = React.useState<{ id: string; name: string } | null>(null);
  const [invite, setInvite] = React.useState({
    email: '', full_name: '', role: 'ANALYST', password: '',
  });

  // OPERATOR and ANALYST hold no `members` permission at all, so this request
  // can only ever 403 for them. Firing it anyway put a red "could not load"
  // banner over a page that was working, with a Retry button that could never
  // succeed. The rest of the page -- what your own role may do -- is exactly
  // what those two came here to read.
  const canViewMembers = can('members', 'view');
  const members = useQuery({
    queryKey: qk.members(workspaceId),
    queryFn: workspaceApi.members,
    enabled: canViewMembers,
  });

  // Served rather than compiled in, so the editor offers exactly the modules
  // and actions the API will accept. Only fetched for somebody who can open
  // the editor -- it sits behind the same permission.
  // Whether an invited account can sign in without a password at all. The
  // API refuses to create one that could not, so the form has to know.
  const authConfig = useQuery({
    queryKey: ['auth-config'],
    queryFn: authApi.config,
    staleTime: Infinity,
    retry: false,
  });
  const googleAvailable = Boolean(authConfig.data?.google);

  const catalog = useQuery({
    queryKey: ['permission-catalog', workspaceId],
    queryFn: workspaceApi.permissionCatalog,
    enabled: canViewMembers,
    staleTime: 10 * 60 * 1000,
  });

  const [editing, setEditing] = React.useState<Member | null>(null);
  const [draft, setDraft] = React.useState<PermissionMap>({});
  const [draftRole, setDraftRole] = React.useState('ANALYST');

  const openEditor = (member: Member) => {
    setEditing(member);
    setDraft(member.permissions);
    setDraftRole(member.role);
  };

  const invalidate = () => queryClient.invalidateQueries({ queryKey: qk.members(workspaceId) });

  const addMember = useMutation({
    // An empty box means "no password", which is a real answer once Google
    // sign-in exists -- not an empty string to hash.
    mutationFn: () => workspaceApi.invite({
      ...invite, password: invite.password.trim() || undefined,
    }),
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

  const savePermissions = useMutation({
    mutationFn: () => workspaceApi.updatePermissions(editing!.id, draft),
    onSuccess: () => {
      invalidate();
      setEditing(null);
      toastSuccess(t('settings.permissionsUpdated'));
    },
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
        {/* Side by side once there is room for both. The members table has four
            columns; given a 1600px row it does not fill them, it just spreads
            them out. Pairing it with the permissions card uses the window
            without stretching either one past the width of its content. */}
        <div className={cn('grid items-start gap-4',
          canViewMembers && '2xl:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)]')}>
        {!canViewMembers ? null : members.error ? (
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
                        {/* A role that no longer describes what this person
                            holds must say so, or the picker reads as the whole
                            answer when it is only where the answer started. */}
                        {member.customised && (
                          <p className="mt-0.5 text-tiny text-text-quaternary">
                            {t('settings.roleEdited')}
                          </p>
                        )}
                      </td>
                      <td className="px-3 py-2.5 text-caption text-text-tertiary">
                        {formatDateTime(member.created_at, locale)}
                      </td>
                      <td className="px-4 py-2.5 text-right">
                        {can('members', 'edit') && catalog.data && (
                          <Button
                            size="xs"
                            variant="ghost"
                            leadingIcon={<SlidersHorizontal className="h-3 w-3" />}
                            onClick={() => openEditor(member)}
                          >
                            {t('settings.editPermissions')}
                          </Button>
                        )}
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
          {/* Ten short rows, one per module. Down a single column they put the
              module name and its level a screen apart on a wide monitor, so
              the list wraps into columns as the room appears. */}
          <ul className="grid gap-x-10 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-2">
            {Object.entries(permissions ?? {}).map(([module, actions]) => {
              const { level, flags } = summarisePermissions(actions, levels?.[module]);
              return (
                <li key={module}
                    className="flex flex-wrap items-center gap-x-2 gap-y-1 border-b border-[rgb(var(--border-line))] py-2 last:border-b-0">
                  <span className="flex-1 basis-32 text-caption font-emphasis text-text-primary">
                    {tf([`module.${module}`], module)}
                  </span>
                  <span className={level === 'none'
                    ? 'text-caption text-text-quaternary'
                    : 'text-caption text-text-tertiary'}>
                    {t(`perm.level.${level}`)}
                  </span>
                  {flags.map((flag) => (
                    <Badge key={flag} variant="subtle" size="xs">
                      {t(`perm.flag.${flag}`)}
                    </Badge>
                  ))}
                </li>
              );
            })}
          </ul>
        </Card>
        </div>

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
            <Label
              htmlFor="inv-pw"
              required={!googleAvailable}
              hint={googleAvailable
                ? t('settings.invitePasswordOptional')
                : t('settings.invitePasswordHint')}
            >
              {t('settings.invitePassword')}
            </Label>
            <Input id="inv-pw" type="password" value={invite.password}
                   onChange={(event) => setInvite({ ...invite, password: event.target.value })} />
            {googleAvailable && !invite.password.trim() && (
              <p className="mt-1 text-tiny leading-relaxed text-text-quaternary">
                {t('settings.inviteGoogleOnly')}
              </p>
            )}
          </div>
        </div>
      </Modal>

      <Modal
        open={Boolean(editing)}
        onClose={() => setEditing(null)}
        size="xl"
        title={t('settings.editPermissionsFor', { name: editing?.full_name ?? '' })}
        description={t('settings.editPermissionsBody')}
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => setEditing(null)}>
              {t('common.cancel')}
            </Button>
            <Button variant="primary" size="sm" loading={savePermissions.isPending}
                    onClick={() => savePermissions.mutate()}>
              {t('common.save')}
            </Button>
          </>
        }
      >
        {editing && catalog.data && (
          <div className="space-y-3">
            {/* Picking a preset fills the grid below rather than saving. The
                administrator sees what the role means before committing to it,
                which is the thing a role picker on its own never showed. */}
            <div className="flex flex-wrap items-end gap-3">
              <div className="w-52">
                <Label htmlFor="perm-preset">{t('settings.startFromRole')}</Label>
                <Select
                  id="perm-preset"
                  size="sm"
                  value={draftRole}
                  onChange={(event) => {
                    const chosen = event.target.value;
                    setDraftRole(chosen);
                    const spec = catalog.data?.presets[chosen];
                    if (spec) setDraft(spec);
                  }}
                >
                  {roles.map((role) => (
                    <option key={role.id} value={role.id}>{role.label}</option>
                  ))}
                </Select>
              </div>
              <p className="flex-1 text-tiny leading-relaxed text-text-quaternary">
                {t('settings.presetHint')}
              </p>
            </div>

            <PermissionEditor
              catalog={catalog.data}
              value={draft}
              preset={catalog.data.presets[draftRole]}
              onChange={setDraft}
            />
          </div>
        )}
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
