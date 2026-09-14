'use client';

/**
 * Everybody, and everywhere they can reach.
 *
 * A workspace's member list answers "who is in here". The organisation's
 * member list answers "who is in the organisation". Neither answers "where can
 * this person go" — which is the question actually asked when somebody changes
 * team, or leaves, and answering it meant opening every workspace in turn.
 *
 * So the grid is the page: one row per person, one column per workspace, and
 * the cell is the seat. Changing a cell grants, changes or revokes that seat
 * without the session ever leaving the console.
 */

import * as React from 'react';
import Link from 'next/link';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ShieldCheck, UserPlus } from 'lucide-react';

import { authApi, organizationApi } from '@/lib/api';
import { cn } from '@/lib/utils';
import { useCurrentUser } from '@/hooks/use-current-user';
import { toastError, toastSuccess } from '@/hooks/use-toast';
import { useI18n } from '@/providers/LanguageProvider';
import { Badge } from '@/components/ui/Badge';
import { Input, Select } from '@/components/ui/Input';
import { EmptyState, ErrorState, TableSkeleton } from '@/components/ui/Feedback';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/layout/PageLayout';
import { AddPersonDialog } from '@/components/settings/AddPersonDialog';
import type { WorkspaceSeat } from '@/lib/types';

const ROLE_IDS = ['OWNER', 'DATA_ADMIN', 'CONNECTOR_DEV', 'OPERATOR', 'ANALYST', 'AUDITOR'];
const ORG_ROLE_IDS = ['ORG_OWNER', 'ORG_ADMIN', 'ORG_MEMBER'];

/** The value a cell shows when somebody holds no seat in that workspace. */
const NO_SEAT = '';

export default function AdminPeoplePage() {
  const { t, tf } = useI18n();
  const queryClient = useQueryClient();
  const { data: me } = useCurrentUser();
  const [filter, setFilter] = React.useState('');
  const [adding, setAdding] = React.useState(false);

  // Whether an invited account can sign in without a password at all.
  const authConfig = useQuery({
    queryKey: ['auth-config'],
    queryFn: authApi.config,
    staleTime: Infinity,
    retry: false,
  });

  const people = useQuery({
    queryKey: ['org-people'],
    queryFn: organizationApi.people,
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['org-people'] });
    queryClient.invalidateQueries({ queryKey: ['org-overview'] });
    queryClient.invalidateQueries({ queryKey: ['org-workspaces'] });
  };

  const setSeat = useMutation({
    mutationFn: async (change: {
      workspaceId: string; userId: string; email: string; fullName: string;
      memberId: string | null; role: string;
    }) => {
      if (change.role === NO_SEAT) {
        if (!change.memberId) return;
        await organizationApi.removeWorkspaceMember(change.workspaceId, change.memberId);
        return;
      }
      if (change.memberId) {
        await organizationApi.updateWorkspaceMember(
          change.workspaceId, change.memberId, { role: change.role },
        );
        return;
      }
      // No seat yet. The account already exists -- everybody on this page does
      // -- so no password is sent, and the API adds them with the credential
      // they already use.
      await organizationApi.addWorkspaceMember(change.workspaceId, {
        email: change.email, full_name: change.fullName, role: change.role,
      });
    },
    onSuccess: () => { invalidate(); toastSuccess(t('admin.seatSaved')); },
    onError: (caught) => { invalidate(); toastError(caught); },
  });

  const setOrgRole = useMutation({
    mutationFn: ({ id, role }: { id: string; role: string }) =>
      organizationApi.updateRole(id, role),
    onSuccess: () => { invalidate(); toastSuccess(t('settings.roleUpdated')); },
    onError: (caught) => toastError(caught),
  });

  if (people.error) {
    return (
      <ErrorState
        title={t('common.errorTitle')}
        message={(people.error as Error).message}
        onRetry={() => people.refetch()}
      />
    );
  }

  const workspaces = people.data?.workspaces ?? [];
  const needle = filter.trim().toLowerCase();
  const rows = (people.data?.people ?? []).filter((person) =>
    !needle
    || person.full_name.toLowerCase().includes(needle)
    || person.email.toLowerCase().includes(needle));

  // A seat carries the membership id the endpoints address, so a cell edit
  // needs no extra lookup. Indexed once here rather than searched on every
  // cell render -- a grid of forty people by eight workspaces renders 320 of
  // them.
  const seatIndex = new Map<string, WorkspaceSeat>();
  for (const person of people.data?.people ?? []) {
    for (const seat of person.seats) {
      seatIndex.set(`${person.user_id}:${seat.workspace_id}`, seat);
    }
  }

  return (
    <div className="space-y-4">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <h1 className="text-h3 font-strong text-text-primary">{t('admin.peopleTitle')}</h1>
          <p className="mt-1 max-w-2xl text-caption text-text-tertiary">
            {t('admin.peopleSubtitle')}
          </p>
        </div>
        {/* Onboarding starts with a person, not with a workspace. It used to
            start with picking a workspace, which is the wrong end of the
            sentence an administrator is actually saying. */}
        <Button variant="primary" onClick={() => setAdding(true)}
                leadingIcon={<UserPlus className="h-3.5 w-3.5" />}>
          {t('admin.addPerson')}
        </Button>
      </header>

      <div className="w-full lg:max-w-xs">
        <Input
          size="sm"
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
          placeholder={t('admin.findPerson')}
          aria-label={t('admin.findPerson')}
        />
      </div>

      <Card padded={false}>
        {people.isLoading ? (
          <TableSkeleton rows={5} columns={4} />
        ) : rows.length === 0 ? (
          <EmptyState title={t('admin.nobodyFound')} compact />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left">
              <thead>
                <tr className="border-b border-[rgb(var(--border-line))] bg-surface-2 text-tiny uppercase tracking-[0.08em] text-text-quaternary">
                  <th scope="col" className="min-w-[220px] px-4 py-2.5 font-emphasis">
                    {t('settings.colUser')}
                  </th>
                  <th scope="col" className="w-44 px-3 py-2.5 font-emphasis">
                    {t('admin.colOrgRole')}
                  </th>
                  {workspaces.map((workspace) => (
                    <th key={workspace.id} scope="col"
                        className="w-48 px-3 py-2.5 font-emphasis">
                      <Link
                        href={`/admin/workspaces/${workspace.id}`}
                        className="block truncate hover:text-brand"
                        title={workspace.name}
                      >
                        {workspace.name}
                      </Link>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-[rgb(var(--border-line))]">
                {rows.map((person) => {
                  const isMe = person.user_id === me?.id;
                  return (
                    <tr key={person.user_id} className="align-top">
                      <td className="px-4 py-2.5">
                        <span className="flex flex-wrap items-center gap-1.5">
                          <Link
                            href={`/admin/people/${person.user_id}`}
                            className="text-caption font-emphasis text-text-primary hover:text-brand"
                          >
                            {person.full_name}
                          </Link>
                          {isMe && (
                            <Badge variant="subtle" size="xs">{t('settings.you')}</Badge>
                          )}
                          {!person.is_active && (
                            <Badge variant="warning" size="xs">{t('admin.disabled')}</Badge>
                          )}
                          {person.auth_provider !== 'password' && (
                            <Badge variant="neutral" size="xs">Google</Badge>
                          )}
                        </span>
                        <span className="block text-tiny text-text-quaternary">
                          {person.email}
                        </span>
                      </td>

                      <td className="px-3 py-2.5">
                        {person.org_role === null ? (
                          <span className="text-tiny text-text-quaternary">
                            {t('admin.notInOrg')}
                          </span>
                        ) : isMe ? (
                          // Demoting yourself out of the console you are
                          // standing in is a door that locks behind you.
                          <Badge variant="neutral" size="sm">
                            {tf([`orgRole.${person.org_role}`], person.org_role)}
                          </Badge>
                        ) : (
                          <Select
                            size="sm"
                            className="w-40"
                            value={person.org_role}
                            aria-label={t('admin.orgRoleFor', { name: person.full_name })}
                            onChange={(event) => setOrgRole.mutate({
                              id: person.org_membership_id!, role: event.target.value,
                            })}
                          >
                            {ORG_ROLE_IDS.map((id) => (
                              <option key={id} value={id}>{tf([`orgRole.${id}`], id)}</option>
                            ))}
                          </Select>
                        )}
                      </td>

                      {workspaces.map((workspace) => {
                        const seat = seatIndex.get(`${person.user_id}:${workspace.id}`);
                        return (
                          <td key={workspace.id} className="px-3 py-2.5">
                            <Select
                              size="sm"
                              className="w-44"
                              value={seat?.role ?? NO_SEAT}
                              aria-label={t('admin.seatFor', {
                                name: person.full_name, workspace: workspace.name,
                              })}
                              onChange={(event) => setSeat.mutate({
                                workspaceId: workspace.id,
                                userId: person.user_id,
                                email: person.email,
                                fullName: person.full_name,
                                memberId: seat?.membership_id ?? null,
                                role: event.target.value,
                              })}
                            >
                              <option value={NO_SEAT}>{t('admin.noSeat')}</option>
                              {ROLE_IDS.map((id) => (
                                <option key={id} value={id}>{t(`role.${id}`)}</option>
                              ))}
                            </Select>
                            {seat?.customised && (
                              <p className="mt-0.5 text-tiny text-text-quaternary">
                                {t('settings.roleEdited')}
                              </p>
                            )}
                          </td>
                        );
                      })}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <p className="flex items-start gap-2 text-tiny leading-relaxed text-text-quaternary">
        <ShieldCheck className="mt-0.5 h-3.5 w-3.5 flex-shrink-0" />
        <span className={cn('max-w-3xl')}>{t('admin.peopleFootnote')}</span>
      </p>

      <AddPersonDialog
        open={adding}
        onClose={() => setAdding(false)}
        workspaces={workspaces}
        people={people.data?.people ?? []}
        googleAvailable={Boolean(authConfig.data?.google)}
      />
    </div>
  );
}
