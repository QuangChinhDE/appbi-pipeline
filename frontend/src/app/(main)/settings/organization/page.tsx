'use client';

import * as React from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Building2, Plus, UserPlus } from 'lucide-react';

import { organizationApi } from '@/lib/api';
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

// Three roles, ordered most to least authority. Kept short on purpose: the
// organisation layer answers one question -- who may open which workspaces --
// and a longer list would invite people to model their org chart here instead
// of using workspace roles, which is where per-area authority belongs.
const ORG_ROLE_IDS = ['ORG_OWNER', 'ORG_ADMIN', 'ORG_MEMBER'];

export default function OrganizationSettingsPage() {
  const { t, locale } = useI18n();
  const queryClient = useQueryClient();
  const { canOrg } = usePermissions();
  const { data: me } = useCurrentUser();
  const workspaceId = useWorkspaceId();
  const roles = ORG_ROLE_IDS.map((id) => ({
    id, label: t(`orgRole.${id}`), hint: t(`orgRole.${id}.hint`),
  }));

  const [inviteOpen, setInviteOpen] = React.useState(false);
  const [workspaceOpen, setWorkspaceOpen] = React.useState(false);
  const [removing, setRemoving] = React.useState<{ id: string; name: string } | null>(null);
  const [droppingWorkspace, setDroppingWorkspace] =
    React.useState<{ id: string; name: string } | null>(null);
  const [invite, setInvite] = React.useState({
    email: '', full_name: '', role: 'ORG_MEMBER', password: '',
  });
  const [workspace, setWorkspace] = React.useState({ name: '', slug: '' });

  const organization = useQuery({ queryKey: qk.organization(workspaceId), queryFn: organizationApi.get });
  const workspaces = useQuery({
    queryKey: qk.organizationWorkspaces(workspaceId),
    queryFn: organizationApi.workspaces,
  });
  const members = useQuery({
    queryKey: qk.organizationMembers(workspaceId),
    queryFn: organizationApi.members,
  });

  const invalidate = (key: readonly unknown[]) =>
    queryClient.invalidateQueries({ queryKey: key });

  const addMember = useMutation({
    mutationFn: () => organizationApi.invite(invite),
    onSuccess: () => {
      invalidate(qk.organizationMembers(workspaceId));
      setInviteOpen(false);
      setInvite({ email: '', full_name: '', role: 'ORG_MEMBER', password: '' });
      toastSuccess(t('org.memberAdded'));
    },
    onError: (caught) => toastError(caught),
  });

  const changeRole = useMutation({
    mutationFn: ({ id, role }: { id: string; role: string }) =>
      organizationApi.updateRole(id, role),
    onSuccess: () => { invalidate(qk.organizationMembers(workspaceId)); toastSuccess(t('org.roleUpdated')); },
    onError: (caught) => toastError(caught),
  });

  const removeMember = useMutation({
    mutationFn: (id: string) => organizationApi.removeMember(id),
    onSuccess: () => {
      invalidate(qk.organizationMembers(workspaceId));
      setRemoving(null);
      toastSuccess(t('org.memberRemoved'));
    },
    onError: (caught) => { setRemoving(null); toastError(caught); },
  });

  // Refused while the workspace still holds pipelines, sources, destinations or
  // dbt projects: each of those has its own teardown, and cascading them away
  // would leave live Airbyte connections and orphaned credentials behind. The
  // server names what is blocking; surfacing its message verbatim is more use
  // than a generic failure toast.
  const dropWorkspace = useMutation({
    mutationFn: (id: string) => organizationApi.deleteWorkspace(id),
    onSuccess: () => {
      invalidate(qk.organizationWorkspaces(workspaceId));
      invalidate(qk.me());
      setDroppingWorkspace(null);
      toastSuccess(t('org.workspaceDeleted'));
    },
    onError: (caught) => { setDroppingWorkspace(null); toastError(caught); },
  });

  const createWorkspace = useMutation({
    mutationFn: () => organizationApi.createWorkspace(workspace),
    onSuccess: () => {
      invalidate(qk.organizationWorkspaces(workspaceId));
      invalidate(qk.me());
      setWorkspaceOpen(false);
      setWorkspace({ name: '', slug: '' });
      toastSuccess(t('org.workspaceCreated'));
    },
    onError: (caught) => toastError(caught),
  });

  const canAdmin = canOrg('admin');

  return (
    <PageListLayout
      title={t('settings.organization')}
      searchable={false}
      description={t('settings.organizationSubtitle')}
      action={
        canAdmin ? (
          <Button variant="primary" onClick={() => setInviteOpen(true)}
                  leadingIcon={<UserPlus className="h-3.5 w-3.5" />}>
            {t('org.addMember')}
          </Button>
        ) : null
      }
    >
      <SettingsTabs active="organization" />

      <div className="space-y-4">
        {organization.error ? (
          <ErrorState title={t('common.errorTitle')}
                      message={(organization.error as Error).message}
                      onRetry={() => organization.refetch()} />
        ) : (
          <Card title={t('org.identity')}>
            <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
              <span className="flex items-center gap-2">
                <Building2 className="h-4 w-4 text-text-quaternary" />
                <span className="text-caption font-emphasis text-text-primary">
                  {organization.data?.name ?? '—'}
                </span>
              </span>
              <span className="text-caption text-text-tertiary">
                {t('org.slug')}: <code className="font-mono">{organization.data?.slug ?? '—'}</code>
              </span>
              <span className="text-caption text-text-tertiary">
                {t('org.yourRole')}:{' '}
                <Badge variant="neutral" size="sm">
                  {organization.data?.role ? t(`orgRole.${organization.data.role}`) : '—'}
                </Badge>
              </span>
            </div>
          </Card>
        )}

        <Card
          title={t('org.workspaces', { n: workspaces.data?.length ?? 0 })}
          description={t('org.workspacesHint')}
          padded={false}
          action={
            canOrg('create') ? (
              <Button size="xs" variant="ghost" onClick={() => setWorkspaceOpen(true)}
                      leadingIcon={<Plus className="h-3 w-3" />}>
                {t('org.addWorkspace')}
              </Button>
            ) : null
          }
        >
          {workspaces.isLoading ? (
            <TableSkeleton rows={3} columns={3} />
          ) : (
            <ul className="divide-y divide-[rgb(var(--border-line))]">
              {(workspaces.data ?? []).map((item) => (
                <li key={item.id}
                    className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2.5">
                  <span className="flex-1 basis-40">
                    <span className="block text-caption font-emphasis text-text-primary">
                      {item.name}
                    </span>
                    <span className="text-tiny font-mono text-text-quaternary">{item.slug}</span>
                  </span>
                  {item.role ? (
                    <Badge variant="subtle" size="sm">{t(`role.${item.role}`)}</Badge>
                  ) : (
                    <span className="text-tiny text-text-quaternary">{t('org.noAccess')}</span>
                  )}
                  {item.via_organization && (
                    <Badge variant="neutral" size="xs">{t('org.viaOrganization')}</Badge>
                  )}
                  {canOrg('delete') && (
                    <Button
                      size="xs"
                      variant="ghost"
                      onClick={() => setDroppingWorkspace({ id: item.id, name: item.name })}
                    >
                      {t('common.delete')}
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Card>

        {members.error ? (
          <ErrorState title={t('common.errorTitle')} message={(members.error as Error).message}
                      onRetry={() => members.refetch()} />
        ) : members.isLoading ? (
          <TableSkeleton rows={3} columns={4} />
        ) : (
          <Card title={t('org.members', { n: members.data?.length ?? 0 })} padded={false}>
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
                        {canAdmin && member.user_id !== me?.id ? (
                          <Select
                            size="sm"
                            className="w-44"
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
                          <Badge variant="neutral" size="sm">{t(`orgRole.${member.role}`)}</Badge>
                        )}
                      </td>
                      <td className="px-3 py-2.5 text-caption text-text-tertiary">
                        {formatDateTime(member.created_at, locale)}
                      </td>
                      <td className="px-4 py-2.5 text-right">
                        {canAdmin && member.user_id !== me?.id && (
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

        <Card title={t('org.roleDescriptions')}>
          <ul className="space-y-2">
            {roles.map((role) => (
              <li key={role.id} className="flex items-start gap-2">
                <Building2 className="mt-0.5 h-3.5 w-3.5 flex-shrink-0 text-text-quaternary" />
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
        title={t('org.addMember')}
        description={t('org.inviteBody')}
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => setInviteOpen(false)}>
              {t('common.cancel')}
            </Button>
            <Button variant="primary" size="sm" loading={addMember.isPending}
                    onClick={() => addMember.mutate()}>
              {t('org.addMember')}
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <div>
            <Label htmlFor="org-email" required>{t('login.email')}</Label>
            <Input id="org-email" type="email" value={invite.email}
                   onChange={(event) => setInvite({ ...invite, email: event.target.value })} />
          </div>
          <div>
            <Label htmlFor="org-name" required>{t('settings.fullName')}</Label>
            <Input id="org-name" value={invite.full_name}
                   onChange={(event) => setInvite({ ...invite, full_name: event.target.value })} />
          </div>
          <div>
            <Label htmlFor="org-role" required>{t('org.inviteRole')}</Label>
            <Select id="org-role" value={invite.role}
                    onChange={(event) => setInvite({ ...invite, role: event.target.value })}>
              {roles.map((role) => (
                <option key={role.id} value={role.id}>{role.label} — {role.hint}</option>
              ))}
            </Select>
          </div>
          <div>
            <Label htmlFor="org-pw" required hint={t('settings.invitePasswordHint')}>
              {t('settings.invitePassword')}
            </Label>
            <Input id="org-pw" type="password" value={invite.password}
                   onChange={(event) => setInvite({ ...invite, password: event.target.value })} />
          </div>
        </div>
      </Modal>

      <Modal
        open={workspaceOpen}
        onClose={() => setWorkspaceOpen(false)}
        title={t('org.addWorkspace')}
        description={t('org.addWorkspaceBody')}
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => setWorkspaceOpen(false)}>
              {t('common.cancel')}
            </Button>
            <Button variant="primary" size="sm" loading={createWorkspace.isPending}
                    onClick={() => createWorkspace.mutate()}>
              {t('org.addWorkspace')}
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <div>
            <Label htmlFor="ws-name" required>{t('org.workspaceName')}</Label>
            <Input
              id="ws-name"
              value={workspace.name}
              onChange={(event) => {
                const name = event.target.value;
                // Suggest a slug rather than making people invent one; they can
                // still overwrite it, and the backend is what enforces the shape.
                setWorkspace((current) => ({
                  name,
                  slug: current.slug === slugify(current.name) ? slugify(name) : current.slug,
                }));
              }}
            />
          </div>
          <div>
            <Label htmlFor="ws-slug" required hint={t('org.slugHint')}>{t('org.slug')}</Label>
            <Input id="ws-slug" value={workspace.slug}
                   onChange={(event) => setWorkspace({ ...workspace, slug: event.target.value })} />
          </div>
        </div>
      </Modal>

      <ConfirmDialog
        open={Boolean(droppingWorkspace)}
        onClose={() => setDroppingWorkspace(null)}
        onConfirm={() => droppingWorkspace && dropWorkspace.mutate(droppingWorkspace.id)}
        loading={dropWorkspace.isPending}
        destructive
        title={t('org.deleteWorkspaceTitle')}
        confirmLabel={t('common.delete')}
        message={t('org.deleteWorkspaceBody', { name: droppingWorkspace?.name ?? '' })}
      />

      <ConfirmDialog
        open={Boolean(removing)}
        onClose={() => setRemoving(null)}
        onConfirm={() => removing && removeMember.mutate(removing.id)}
        loading={removeMember.isPending}
        destructive
        title={t('org.removeTitle')}
        confirmLabel={t('settings.removeMember')}
        message={t('org.removeBody', { name: removing?.name ?? '' })}
      />
    </PageListLayout>
  );
}

/** Lowercase, hyphenated, no leading or trailing hyphen -- the shape the API
 * accepts, so the suggested value is never one the server would reject. */
function slugify(value: string): string {
  return value
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .replace(/[đĐ]/g, 'd')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 100);
}
