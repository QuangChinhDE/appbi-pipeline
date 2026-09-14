'use client';

/**
 * Who is in one workspace, managed from outside it.
 *
 * Giving somebody a seat in a second workspace used to mean switching into
 * that workspace, opening its members list, and inviting them there. With
 * eight workspaces that is eight round trips to answer "who has access to
 * what", and no screen anywhere could show the answer side by side.
 *
 * Everything here addresses the workspace by id, so the session never moves.
 */

import * as React from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Trash2, UserPlus } from 'lucide-react';

import { organizationApi } from '@/lib/api';
import { toastError, toastSuccess } from '@/hooks/use-toast';
import { useI18n } from '@/providers/LanguageProvider';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Input, Label, Select } from '@/components/ui/Input';
import { EmptyState, TableSkeleton } from '@/components/ui/Feedback';
import { Modal } from '@/components/ui/Modal';
import type { WorkspaceSummary } from '@/lib/types';

// Most to least authority, so the picker reads as a ladder. PLATFORM_ADMIN is
// an account property rather than a seat, so it is not on offer.
const ROLE_IDS = ['OWNER', 'DATA_ADMIN', 'CONNECTOR_DEV', 'OPERATOR', 'ANALYST', 'AUDITOR'];

export function WorkspaceMembersDialog({
  workspace, open, onClose, onChanged,
}: {
  workspace: WorkspaceSummary | null;
  open: boolean;
  onClose: () => void;
  /** So the row behind this dialog can refresh its member count. */
  onChanged: () => void;
}) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const workspaceId = workspace?.id ?? '';

  const [adding, setAdding] = React.useState(false);
  const [draft, setDraft] = React.useState({
    email: '', full_name: '', role: 'ANALYST', password: '',
  });

  const members = useQuery({
    queryKey: ['workspace-members', workspaceId],
    queryFn: () => organizationApi.workspaceMembers(workspaceId),
    enabled: open && Boolean(workspaceId),
  });

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['workspace-members', workspaceId] });
    onChanged();
  };

  const add = useMutation({
    // An empty password means "this person already has an account, or signs in
    // with Google" -- the API decides which, and refuses only when neither can
    // be true.
    mutationFn: () => organizationApi.addWorkspaceMember(workspaceId, {
      ...draft, password: draft.password.trim() || undefined,
    }),
    onSuccess: () => {
      refresh();
      setAdding(false);
      setDraft({ email: '', full_name: '', role: 'ANALYST', password: '' });
      toastSuccess(t('org.seatGranted'));
    },
    onError: (caught) => toastError(caught),
  });

  const changeRole = useMutation({
    mutationFn: ({ id, role }: { id: string; role: string }) =>
      organizationApi.updateWorkspaceMember(workspaceId, id, { role }),
    onSuccess: () => { refresh(); toastSuccess(t('settings.roleUpdated')); },
    onError: (caught) => toastError(caught),
  });

  const drop = useMutation({
    mutationFn: (id: string) => organizationApi.removeWorkspaceMember(workspaceId, id),
    onSuccess: () => { refresh(); toastSuccess(t('org.seatRemoved')); },
    onError: (caught) => toastError(caught),
  });

  return (
    <Modal
      open={open}
      onClose={onClose}
      size="xl"
      title={t('org.seatsIn', { name: workspace?.name ?? '' })}
      description={t('org.seatsHint')}
      footer={
        <Button variant="ghost" size="sm" onClick={onClose}>{t('common.close')}</Button>
      }
    >
      <div className="space-y-3">
        <div className="flex items-center justify-between gap-3">
          <p className="text-caption text-text-tertiary">
            {t('org.seatCount', { n: members.data?.length ?? 0 })}
          </p>
          {!adding && (
            <Button size="xs" variant="secondary" onClick={() => setAdding(true)}
                    leadingIcon={<UserPlus className="h-3 w-3" />}>
              {t('org.grantSeat')}
            </Button>
          )}
        </div>

        {adding && (
          <div className="space-y-3 rounded-lg border border-[rgb(var(--border-line))] bg-surface-2 p-3">
            <div className="grid gap-3 md:grid-cols-2 md:items-start md:gap-x-6">
              <div>
                <Label htmlFor="seat-email" required>{t('login.email')}</Label>
                <Input id="seat-email" type="email" value={draft.email}
                       onChange={(e) => setDraft({ ...draft, email: e.target.value })} />
              </div>
              <div>
                <Label htmlFor="seat-name" required>{t('settings.fullName')}</Label>
                <Input id="seat-name" value={draft.full_name}
                       onChange={(e) => setDraft({ ...draft, full_name: e.target.value })} />
              </div>
              <div>
                <Label htmlFor="seat-role">{t('settings.colRole')}</Label>
                <Select id="seat-role" value={draft.role}
                        onChange={(e) => setDraft({ ...draft, role: e.target.value })}>
                  {ROLE_IDS.map((id) => (
                    <option key={id} value={id}>{t(`role.${id}`)}</option>
                  ))}
                </Select>
              </div>
              <div>
                <Label htmlFor="seat-pw" hint={t('org.seatPasswordHint')}>
                  {t('settings.invitePassword')}
                </Label>
                <Input id="seat-pw" type="password" value={draft.password}
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
          <TableSkeleton rows={3} columns={3} />
        ) : (members.data ?? []).length === 0 ? (
          <EmptyState title={t('org.noSeats')} compact />
        ) : (
          <div className="overflow-hidden rounded-lg border border-[rgb(var(--border-line))]">
            <div className="overflow-x-auto">
              <table className="w-full min-w-[560px] text-left">
                <thead>
                  <tr className="border-b border-[rgb(var(--border-line))] bg-surface-2 text-tiny uppercase tracking-[0.08em] text-text-quaternary">
                    <th scope="col" className="px-3 py-2 font-emphasis">
                      {t('settings.colUser')}
                    </th>
                    <th scope="col" className="w-48 px-3 py-2 font-emphasis">
                      {t('settings.colRole')}
                    </th>
                    <th scope="col" className="w-10 px-2 py-2" />
                  </tr>
                </thead>
                <tbody className="divide-y divide-[rgb(var(--border-line))]">
                  {(members.data ?? []).map((member) => (
                    <tr key={member.id} className="align-middle">
                      <td className="px-3 py-2">
                        <span className="block text-caption font-emphasis text-text-primary">
                          {member.full_name}
                        </span>
                        <span className="text-tiny text-text-quaternary">{member.email}</span>
                      </td>
                      <td className="px-3 py-2">
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
                        {/* Fine-tuning a map belongs on the workspace's own
                            members screen, where the whole editor lives. This
                            says so rather than offering half of it. */}
                        {member.customised && (
                          <p className="mt-0.5 text-tiny text-text-quaternary">
                            {t('settings.roleEdited')}
                          </p>
                        )}
                      </td>
                      <td className="px-2 py-2 text-right">
                        <Button
                          size="xs"
                          variant="ghost"
                          aria-label={t('org.removeSeatFor', { name: member.full_name })}
                          onClick={() => drop.mutate(member.id)}
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {workspace?.member_count === 0 && (
          <Badge variant="warning" size="xs">{t('org.emptyWorkspace')}</Badge>
        )}
      </div>
    </Modal>
  );
}
