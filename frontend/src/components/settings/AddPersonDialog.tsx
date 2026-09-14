'use client';

/**
 * Somebody joins, and lands everywhere they need to, in one pass.
 *
 * Onboarding is one decision — "Minh is on the marketing team, he needs these
 * three workspaces" — and it used to be spelled as one invitation per
 * workspace, made from inside each workspace in turn. Three screens is how the
 * third one gets forgotten.
 *
 * The second field is the one that matters: **same access as** somebody who
 * already does the job. That is the sentence an administrator actually says,
 * and typing the equivalent out by hand is where a wrong role comes from.
 */

import * as React from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { organizationApi } from '@/lib/api';
import { toastError, toastSuccess } from '@/hooks/use-toast';
import { useI18n } from '@/providers/LanguageProvider';
import { Button } from '@/components/ui/Button';
import { Input, Label, Select } from '@/components/ui/Input';
import { Modal } from '@/components/ui/Modal';
import type { PersonAcrossWorkspaces, WorkspaceSummary } from '@/lib/types';

const ROLE_IDS = ['OWNER', 'DATA_ADMIN', 'CONNECTOR_DEV', 'OPERATOR', 'ANALYST', 'AUDITOR'];
const ORG_ROLE_IDS = ['ORG_OWNER', 'ORG_ADMIN', 'ORG_MEMBER'];

export function AddPersonDialog({
  open, onClose, workspaces, people, googleAvailable,
}: {
  open: boolean;
  onClose: () => void;
  workspaces: WorkspaceSummary[];
  /** For "same access as" — the list is already on the page behind this. */
  people: PersonAcrossWorkspaces[];
  googleAvailable: boolean;
}) {
  const { t } = useI18n();
  const queryClient = useQueryClient();

  const [form, setForm] = React.useState({
    email: '', full_name: '', password: '', org_role: 'ORG_MEMBER',
  });
  const [likeUser, setLikeUser] = React.useState('');
  const [seats, setSeats] = React.useState<Record<string, string>>({});

  const reset = () => {
    setForm({ email: '', full_name: '', password: '', org_role: 'ORG_MEMBER' });
    setLikeUser('');
    setSeats({});
  };

  const create = useMutation({
    mutationFn: () => organizationApi.addPerson({
      email: form.email.trim(),
      full_name: form.full_name.trim(),
      password: form.password.trim() || undefined,
      org_role: form.org_role,
      like_user_id: likeUser || undefined,
      seats: Object.entries(seats).map(([workspace_id, role]) => ({ workspace_id, role })),
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['org-people'] });
      queryClient.invalidateQueries({ queryKey: ['org-overview'] });
      queryClient.invalidateQueries({ queryKey: ['org-workspaces'] });
      onClose();
      reset();
      toastSuccess(t('admin.personAdded'));
    },
    onError: (caught) => toastError(caught),
  });

  // Copying somebody's access and picking seats by hand are two answers to the
  // same question, so choosing one puts the other away rather than leaving both
  // on screen for the API to reconcile.
  const copying = Boolean(likeUser);
  const model = people.find((p) => p.user_id === likeUser);

  return (
    <Modal
      open={open}
      onClose={() => { onClose(); reset(); }}
      size="xl"
      title={t('admin.addPerson')}
      description={t('admin.addPersonBody')}
      footer={
        <>
          <Button variant="ghost" size="sm" onClick={() => { onClose(); reset(); }}>
            {t('common.cancel')}
          </Button>
          <Button
            variant="primary"
            size="sm"
            loading={create.isPending}
            disabled={!form.email.trim() || !form.full_name.trim()}
            onClick={() => create.mutate()}
          >
            {t('admin.addPerson')}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="grid gap-3 md:grid-cols-2 md:items-start md:gap-x-8">
          <div>
            <Label htmlFor="ap-email" required>{t('login.email')}</Label>
            <Input id="ap-email" type="email" value={form.email}
                   onChange={(e) => setForm({ ...form, email: e.target.value })} />
          </div>
          <div>
            <Label htmlFor="ap-name" required>{t('settings.fullName')}</Label>
            <Input id="ap-name" value={form.full_name}
                   onChange={(e) => setForm({ ...form, full_name: e.target.value })} />
          </div>
          <div>
            <Label htmlFor="ap-orgrole">{t('admin.colOrgRole')}</Label>
            <Select id="ap-orgrole" value={form.org_role}
                    onChange={(e) => setForm({ ...form, org_role: e.target.value })}>
              {ORG_ROLE_IDS.map((id) => (
                <option key={id} value={id}>{t(`orgRole.${id}`)}</option>
              ))}
            </Select>
          </div>
          <div>
            <Label htmlFor="ap-pw" required={!googleAvailable}
                   hint={googleAvailable
                     ? t('settings.invitePasswordOptional')
                     : t('settings.invitePasswordHint')}>
              {t('settings.invitePassword')}
            </Label>
            <Input id="ap-pw" type="password" value={form.password}
                   onChange={(e) => setForm({ ...form, password: e.target.value })} />
          </div>
        </div>

        <div className="rounded-lg border border-[rgb(var(--border-line))] bg-surface-2 p-3">
          <Label htmlFor="ap-like">{t('admin.sameAccessAs')}</Label>
          <Select
            id="ap-like"
            className="w-full sm:w-72"
            value={likeUser}
            onChange={(event) => {
              setLikeUser(event.target.value);
              if (event.target.value) setSeats({});
            }}
          >
            <option value="">{t('admin.pickWorkspacesByHand')}</option>
            {people.filter((person) => person.seats.length > 0).map((person) => (
              <option key={person.user_id} value={person.user_id}>
                {person.full_name} — {t('admin.inCount', { n: person.seats.length })}
              </option>
            ))}
          </Select>
          <p className="mt-1 text-tiny leading-relaxed text-text-quaternary">
            {t('admin.sameAccessHint')}
          </p>
          {copying && model && (
            <ul className="mt-2 space-y-0.5">
              {model.seats.map((seat) => {
                const workspace = workspaces.find((w) => w.id === seat.workspace_id);
                return (
                  <li key={seat.workspace_id} className="text-caption text-text-secondary">
                    {workspace?.name ?? seat.workspace_id}
                    {' · '}
                    <span className="text-text-tertiary">{t(`role.${seat.role}`)}</span>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        {!copying && (
          <div>
            <p className="mb-1.5 text-caption font-emphasis text-text-secondary">
              {t('admin.whichWorkspaces')}
            </p>
            <div className="overflow-hidden rounded-lg border border-[rgb(var(--border-line))]">
              <ul className="divide-y divide-[rgb(var(--border-line))]">
                {workspaces.map((workspace) => {
                  const granted = workspace.id in seats;
                  return (
                    <li key={workspace.id}
                        className="flex flex-wrap items-center gap-x-3 gap-y-2 px-3 py-2">
                      <label className="flex min-w-0 flex-1 basis-48 cursor-pointer items-center gap-2">
                        <input
                          type="checkbox"
                          checked={granted}
                          onChange={(event) => {
                            const next = { ...seats };
                            if (event.target.checked) next[workspace.id] = 'ANALYST';
                            else delete next[workspace.id];
                            setSeats(next);
                          }}
                        />
                        <span className="min-w-0">
                          <span className="block truncate text-caption text-text-primary">
                            {workspace.name}
                          </span>
                          <span className="text-tiny font-mono text-text-quaternary">
                            {workspace.slug}
                          </span>
                        </span>
                      </label>
                      <Select
                        size="sm"
                        className="w-44"
                        value={seats[workspace.id] ?? ''}
                        disabled={!granted}
                        aria-label={t('admin.seatFor', {
                          name: form.full_name || t('admin.addPerson'),
                          workspace: workspace.name,
                        })}
                        onChange={(event) =>
                          setSeats({ ...seats, [workspace.id]: event.target.value })}
                      >
                        {!granted && <option value="">{t('admin.noSeat')}</option>}
                        {ROLE_IDS.map((id) => (
                          <option key={id} value={id}>{t(`role.${id}`)}</option>
                        ))}
                      </Select>
                    </li>
                  );
                })}
              </ul>
            </div>
          </div>
        )}
      </div>
    </Modal>
  );
}
