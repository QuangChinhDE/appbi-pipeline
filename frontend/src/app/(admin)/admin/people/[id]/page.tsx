'use client';

/**
 * One person: everywhere they can go, and the two actions that change it.
 *
 * The grid is for reading — forty people at a glance. This is for acting on
 * one of them, which is a different job and was previously spread across every
 * workspace they belonged to. The two verbs here are the ones an administrator
 * actually performs: make this person's access match somebody else's, and take
 * it all away.
 */

import * as React from 'react';
import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, Copy, SlidersHorizontal, UserMinus } from 'lucide-react';

import { organizationApi } from '@/lib/api';
import { useCurrentUser, useWorkspaceSwitch } from '@/hooks/use-current-user';
import { toastError, toastSuccess } from '@/hooks/use-toast';
import { useI18n } from '@/providers/LanguageProvider';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Label, Select } from '@/components/ui/Input';
import { ConfirmDialog, Modal } from '@/components/ui/Modal';
import { CardSkeleton, EmptyState, ErrorState } from '@/components/ui/Feedback';
import { Card } from '@/components/layout/PageLayout';
import { PermissionEditor } from '@/components/settings/PermissionEditor';
import type { AccessChange, PermissionMap } from '@/lib/types';

const ROLE_IDS = ['OWNER', 'DATA_ADMIN', 'CONNECTOR_DEV', 'OPERATOR', 'ANALYST', 'AUDITOR'];

export default function AdminPersonPage() {
  const { t, tf } = useI18n();
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const queryClient = useQueryClient();
  const { data: me } = useCurrentUser();
  const switchWorkspace = useWorkspaceSwitch();

  const userId = params.id;
  const isMe = userId === me?.id;

  const person = useQuery({
    queryKey: ['org-person', userId],
    queryFn: () => organizationApi.person(userId),
  });
  const everybody = useQuery({
    queryKey: ['org-people'],
    queryFn: organizationApi.people,
  });
  // Modules, actions and presets. Served rather than compiled in, so the
  // editor cannot come to offer something the API will refuse.
  const catalog = useQuery({
    queryKey: ['permission-catalog'],
    queryFn: organizationApi.permissionCatalog,
    staleTime: 10 * 60 * 1000,
  });
  // The resolved permission map lives on the workspace's member rows, not on
  // the seat, so the editor opens with what the gate will actually use. Up
  // here with the other hooks: below the early returns it ran in a different
  // order on the render that returns early, which React reads as a different
  // component.
  const workspaceIds = (everybody.data?.workspaces ?? []).map((w) => w.id);
  const memberLists = useQuery({
    queryKey: ['org-member-maps', workspaceIds.join(',')],
    enabled: workspaceIds.length > 0,
    queryFn: async () => {
      const pairs = await Promise.all(workspaceIds.map(async (id) => [
        id, await organizationApi.workspaceMembers(id),
      ] as const));
      return Object.fromEntries(pairs);
    },
  });

  const [copyFrom, setCopyFrom] = React.useState('');
  const [copyOpen, setCopyOpen] = React.useState(false);
  const [offboarding, setOffboarding] = React.useState(false);
  // What the last action actually did. Shown rather than assumed, because
  // "removed" and "removed from the two I remembered" look the same otherwise.
  const [report, setReport] = React.useState<AccessChange[] | null>(null);
  // Which workspace's permission map is open, and what it currently says.
  // Tuning used to mean switching the session into that workspace and
  // finding its members screen; the console edits it in place now.
  const [tuning, setTuning] = React.useState<{
    workspaceId: string; membershipId: string; name: string; role: string;
  } | null>(null);
  const [draft, setDraft] = React.useState<PermissionMap>({});

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['org-person', userId] });
    queryClient.invalidateQueries({ queryKey: ['org-people'] });
    queryClient.invalidateQueries({ queryKey: ['org-overview'] });
    queryClient.invalidateQueries({ queryKey: ['org-workspaces'] });
  };

  const setSeat = useMutation({
    mutationFn: async (change: { workspaceId: string; memberId: string | null; role: string }) => {
      if (!change.role) {
        if (change.memberId) {
          await organizationApi.removeWorkspaceMember(change.workspaceId, change.memberId);
        }
        return;
      }
      if (change.memberId) {
        await organizationApi.updateWorkspaceMember(
          change.workspaceId, change.memberId, { role: change.role },
        );
        return;
      }
      await organizationApi.addWorkspaceMember(change.workspaceId, {
        email: person.data!.email,
        full_name: person.data!.full_name,
        role: change.role,
      });
    },
    onSuccess: () => { invalidate(); toastSuccess(t('admin.seatSaved')); },
    onError: (caught) => { invalidate(); toastError(caught); },
  });

  const copy = useMutation({
    mutationFn: () => organizationApi.copyAccess(userId, { from_user_id: copyFrom, mode: 'match' }),
    onSuccess: (result) => {
      invalidate();
      setCopyOpen(false);
      setCopyFrom('');
      setReport(result.changes);
      toastSuccess(t('admin.accessCopied', { n: result.changes.length }));
    },
    onError: (caught) => toastError(caught),
  });

  const savePermissions = useMutation({
    mutationFn: () => organizationApi.updateWorkspaceMember(
      tuning!.workspaceId, tuning!.membershipId, { permissions: draft },
    ),
    onSuccess: () => {
      invalidate();
      setTuning(null);
      toastSuccess(t('settings.permissionsUpdated'));
    },
    onError: (caught) => toastError(caught),
  });

  const offboard = useMutation({
    mutationFn: () => organizationApi.offboard(userId),
    onSuccess: (result) => {
      invalidate();
      setOffboarding(false);
      toastSuccess(result.account_deactivated
        ? t('admin.offboardedAndLocked', { n: result.changes.length })
        : t('admin.offboarded', { n: result.changes.length }));
      router.push('/admin/people');
    },
    onError: (caught) => { setOffboarding(false); toastError(caught); },
  });

  if (person.error) {
    return (
      <ErrorState
        title={t('common.errorTitle')}
        message={(person.error as Error).message}
        onRetry={() => person.refetch()}
      />
    );
  }
  if (person.isLoading || !person.data) return <CardSkeleton count={3} />;

  const data = person.data;
  const workspaces = everybody.data?.workspaces ?? [];
  const members = memberLists.data ?? {};
  const models = (everybody.data?.people ?? [])
    .filter((candidate) => candidate.user_id !== userId && candidate.seats.length > 0);

  return (
    <div className="space-y-4">
      <Link
        href="/admin/people"
        className="-ml-1 inline-flex items-center gap-1 rounded px-1 py-1 text-caption text-text-tertiary transition-colors hover:text-text-primary"
      >
        <ArrowLeft className="h-3.5 w-3.5" />
        {t('admin.people')}
      </Link>

      <header className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <h1 className="flex flex-wrap items-center gap-2 text-h3 font-strong text-text-primary">
            {data.full_name}
            {isMe && <Badge variant="subtle" size="sm">{t('settings.you')}</Badge>}
            {!data.is_active && (
              <Badge variant="warning" size="sm">{t('admin.disabled')}</Badge>
            )}
          </h1>
          <p className="mt-1 flex flex-wrap items-center gap-x-3 text-caption text-text-tertiary">
            <span>{data.email}</span>
            <span>
              {data.org_role
                ? tf([`orgRole.${data.org_role}`], data.org_role)
                : t('admin.notInOrg')}
            </span>
            {data.auth_provider !== 'password' && <Badge variant="neutral" size="xs">Google</Badge>}
          </p>
        </div>

        {!isMe && (
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="secondary" onClick={() => setCopyOpen(true)}
                    leadingIcon={<Copy className="h-3.5 w-3.5" />}>
              {t('admin.copyAccess')}
            </Button>
            <Button variant="danger" onClick={() => setOffboarding(true)}
                    leadingIcon={<UserMinus className="h-3.5 w-3.5" />}>
              {t('admin.offboard')}
            </Button>
          </div>
        )}
      </header>

      {report && (
        <Card title={t('admin.whatChanged')}>
          <ul className="space-y-1">
            {report.length === 0 ? (
              <li className="text-caption text-text-tertiary">{t('admin.nothingChanged')}</li>
            ) : report.map((change) => (
              <li key={change.workspace_id + change.action}
                  className="flex flex-wrap items-center gap-2 text-caption">
                <Badge
                  size="xs"
                  variant={change.action === 'removed' ? 'danger'
                    : change.action === 'granted' ? 'success' : 'brand'}
                >
                  {t(`admin.change.${change.action}`)}
                </Badge>
                <span className="text-text-primary">{change.workspace_name}</span>
                {change.role && (
                  <span className="text-text-tertiary">{t(`role.${change.role}`)}</span>
                )}
              </li>
            ))}
          </ul>
        </Card>
      )}

      <Card title={t('admin.seatsTitle')} description={t('admin.seatsHint')} padded={false}>
        {workspaces.length === 0 ? (
          <EmptyState title={t('admin.noWorkspaces')} compact />
        ) : (
          <ul className="divide-y divide-[rgb(var(--border-line))]">
            {workspaces.map((workspace) => {
              const seat = data.seats.find((s) => s.workspace_id === workspace.id);
              return (
                <li key={workspace.id}
                    className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-2.5">
                  <span className="min-w-0 flex-1 basis-48">
                    <Link
                      href={`/admin/workspaces/${workspace.id}`}
                      className="block truncate text-caption font-emphasis text-text-primary hover:text-brand"
                    >
                      {workspace.name}
                    </Link>
                    <span className="text-tiny font-mono text-text-quaternary">
                      {workspace.slug}
                    </span>
                  </span>

                  <Select
                    size="sm"
                    className="w-44"
                    value={seat?.role ?? ''}
                    aria-label={t('admin.seatFor', {
                      name: data.full_name, workspace: workspace.name,
                    })}
                    onChange={(event) => setSeat.mutate({
                      workspaceId: workspace.id,
                      memberId: seat?.membership_id ?? null,
                      role: event.target.value,
                    })}
                  >
                    <option value="">{t('admin.noSeat')}</option>
                    {ROLE_IDS.map((id) => (
                      <option key={id} value={id}>{t(`role.${id}`)}</option>
                    ))}
                  </Select>

                  {/* Tuning used to mean switching the session into that
                      workspace and finding its members screen — two context
                      changes to adjust one checkbox. It happens here now. */}
                  {seat && catalog.data && (
                    <Button
                      size="xs"
                      variant="ghost"
                      leadingIcon={<SlidersHorizontal className="h-3 w-3" />}
                      onClick={() => {
                        setTuning({
                          workspaceId: workspace.id,
                          membershipId: seat.membership_id,
                          name: workspace.name,
                          role: seat.role,
                        });
                        const member = (members[workspace.id] ?? []).find(
                          (m) => m.id === seat.membership_id,
                        );
                        setDraft(member?.permissions ?? {});
                      }}
                    >
                      {seat.customised
                        ? t('admin.tuneEdited')
                        : t('settings.editPermissions')}
                    </Button>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </Card>

      <Modal
        open={copyOpen}
        onClose={() => setCopyOpen(false)}
        title={t('admin.copyAccess')}
        description={t('admin.copyAccessBody', { name: data.full_name })}
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => setCopyOpen(false)}>
              {t('common.cancel')}
            </Button>
            <Button variant="primary" size="sm" loading={copy.isPending}
                    disabled={!copyFrom} onClick={() => copy.mutate()}>
              {t('admin.copyAccess')}
            </Button>
          </>
        }
      >
        <div className="space-y-2">
          <Label htmlFor="copy-from" required>{t('admin.copyFrom')}</Label>
          <Select id="copy-from" value={copyFrom}
                  onChange={(event) => setCopyFrom(event.target.value)}>
            <option value="">{t('admin.pickAPerson')}</option>
            {models.map((candidate) => (
              <option key={candidate.user_id} value={candidate.user_id}>
                {candidate.full_name} — {t('admin.inCount', { n: candidate.seats.length })}
              </option>
            ))}
          </Select>
          <p className="text-tiny leading-relaxed text-text-quaternary">
            {t('admin.copyAccessWarning')}
          </p>
        </div>
      </Modal>

      <Modal
        open={Boolean(tuning)}
        onClose={() => setTuning(null)}
        size="xl"
        title={t('admin.tuneIn', { workspace: tuning?.name ?? '' })}
        description={t('admin.tuneBody', { name: data.full_name })}
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => setTuning(null)}>
              {t('common.cancel')}
            </Button>
            <Button variant="primary" size="sm" loading={savePermissions.isPending}
                    onClick={() => savePermissions.mutate()}>
              {t('common.save')}
            </Button>
          </>
        }
      >
        {tuning && catalog.data && (
          <PermissionEditor
            catalog={catalog.data}
            value={draft}
            preset={catalog.data.presets[tuning.role]}
            onChange={setDraft}
          />
        )}
      </Modal>

      <ConfirmDialog
        open={offboarding}
        onClose={() => setOffboarding(false)}
        onConfirm={() => offboard.mutate()}
        loading={offboard.isPending}
        destructive
        title={t('admin.offboardTitle', { name: data.full_name })}
        confirmLabel={t('admin.offboard')}
        message={t('admin.offboardBody', {
          name: data.full_name, n: data.seats.length,
        })}
      />
    </div>
  );
}
