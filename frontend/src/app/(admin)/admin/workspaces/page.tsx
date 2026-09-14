'use client';

/**
 * Every workspace the organisation holds: make one, fill one, enter one.
 *
 * This lived as a card on a settings tab inside a workspace. Three things it
 * could not do from there, which is why it is a page now: say how many people
 * are in each one, put somebody into one you are not currently in, and walk
 * into one without going hunting for the switcher.
 */

import * as React from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowRight, Plus, Users } from 'lucide-react';

import { organizationApi } from '@/lib/api';
import { useWorkspaceSwitch } from '@/hooks/use-current-user';
import { usePermissions } from '@/hooks/use-permissions';
import { toastError, toastSuccess } from '@/hooks/use-toast';
import { useI18n } from '@/providers/LanguageProvider';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Input, Label } from '@/components/ui/Input';
import { ConfirmDialog, Modal } from '@/components/ui/Modal';
import { EmptyState, ErrorState, TableSkeleton } from '@/components/ui/Feedback';
import { Card } from '@/components/layout/PageLayout';
import { WorkspaceMembersDialog } from '@/components/settings/WorkspaceMembersDialog';
import type { WorkspaceSummary } from '@/lib/types';

/** Lower-case, digits and dashes — what the API will accept, applied while
 *  typing so the rejection never has to happen. */
function slugify(raw: string): string {
  return raw
    .normalize('NFD').replace(/[̀-ͯ]/g, '')
    .replace(/đ/g, 'd').replace(/Đ/g, 'd')
    .toLowerCase().trim()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 40);
}

export default function AdminWorkspacesPage() {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const { canOrg } = usePermissions();
  const switchWorkspace = useWorkspaceSwitch();

  const [creating, setCreating] = React.useState(false);
  const [draft, setDraft] = React.useState({ name: '', slug: '' });
  const [slugTouched, setSlugTouched] = React.useState(false);
  const [seatsFor, setSeatsFor] = React.useState<WorkspaceSummary | null>(null);
  const [dropping, setDropping] = React.useState<{ id: string; name: string } | null>(null);

  const workspaces = useQuery({
    queryKey: ['org-workspaces'],
    queryFn: organizationApi.workspaces,
  });
  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['org-workspaces'] });
    queryClient.invalidateQueries({ queryKey: ['org-overview'] });
  };

  const create = useMutation({
    mutationFn: () => organizationApi.createWorkspace(draft),
    onSuccess: () => {
      invalidate();
      setCreating(false);
      setDraft({ name: '', slug: '' });
      setSlugTouched(false);
      toastSuccess(t('org.workspaceCreated'));
    },
    onError: (caught) => toastError(caught),
  });

  const drop = useMutation({
    mutationFn: (id: string) => organizationApi.deleteWorkspace(id),
    onSuccess: () => { invalidate(); setDropping(null); toastSuccess(t('org.workspaceDeleted')); },
    onError: (caught) => { setDropping(null); toastError(caught); },
  });

  if (workspaces.error) {
    return (
      <ErrorState
        title={t('common.errorTitle')}
        message={(workspaces.error as Error).message}
        onRetry={() => workspaces.refetch()}
      />
    );
  }

  return (
    <div className="space-y-4">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <h1 className="text-h3 font-strong text-text-primary">{t('admin.workspacesTitle')}</h1>
          <p className="mt-1 max-w-2xl text-caption text-text-tertiary">
            {t('admin.workspacesSubtitle')}
          </p>
        </div>
        {canOrg('create') && (
          <Button variant="primary" onClick={() => setCreating(true)}
                  leadingIcon={<Plus className="h-3.5 w-3.5" />}>
            {t('org.addWorkspace')}
          </Button>
        )}
      </header>

      <Card padded={false}>
        {workspaces.isLoading ? (
          <TableSkeleton rows={3} columns={3} />
        ) : (workspaces.data ?? []).length === 0 ? (
          <EmptyState title={t('admin.noWorkspaces')} compact />
        ) : (
          <ul className="divide-y divide-[rgb(var(--border-line))]">
            {(workspaces.data ?? []).map((workspace) => (
              <li key={workspace.id}
                  className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3">
                <span className="min-w-0 flex-1 basis-56">
                  <span className="flex flex-wrap items-center gap-2">
                    <span className="truncate text-small font-emphasis text-text-primary">
                      {workspace.name}
                    </span>
                    {workspace.status !== 'ACTIVE' && (
                      <Badge variant="warning" size="xs">{workspace.status}</Badge>
                    )}
                    {workspace.via_organization && (
                      <Badge variant="neutral" size="xs">{t('org.viaOrganization')}</Badge>
                    )}
                  </span>
                  <span className="mt-0.5 flex flex-wrap items-center gap-x-3 text-tiny text-text-quaternary">
                    <span className="font-mono">{workspace.slug}</span>
                    {typeof workspace.member_count === 'number' && (
                      <span>{t('org.seatCount', { n: workspace.member_count })}</span>
                    )}
                    {workspace.member_count === 0 && (
                      <span className="text-warning">{t('admin.nobodyInside')}</span>
                    )}
                  </span>
                </span>

                {canOrg('admin') && (
                  <Button size="xs" variant="ghost"
                          leadingIcon={<Users className="h-3 w-3" />}
                          onClick={() => setSeatsFor(workspace)}>
                    {t('org.manageSeats')}
                  </Button>
                )}
                <Button size="xs" variant="secondary"
                        onClick={() => switchWorkspace(workspace.id)}>
                  {t('admin.openWorkspace')}
                  <ArrowRight className="ml-1 h-3 w-3" />
                </Button>
                {canOrg('delete') && (
                  <Button size="xs" variant="ghost"
                          onClick={() => setDropping({ id: workspace.id, name: workspace.name })}>
                    {t('common.delete')}
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Modal
        open={creating}
        onClose={() => setCreating(false)}
        title={t('org.addWorkspace')}
        description={t('org.addWorkspaceBody')}
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => setCreating(false)}>
              {t('common.cancel')}
            </Button>
            <Button variant="primary" size="sm" loading={create.isPending}
                    onClick={() => create.mutate()}>
              {t('org.addWorkspace')}
            </Button>
          </>
        }
      >
        <div className="grid gap-3 md:grid-cols-2 md:items-start md:gap-x-6">
          <div>
            <Label htmlFor="ws-name" required>{t('org.workspaceName')}</Label>
            <Input
              id="ws-name"
              value={draft.name}
              onChange={(event) => {
                const name = event.target.value;
                // The slug follows the name until somebody edits it, and then
                // it stops -- typing a name should not silently rewrite a slug
                // that was chosen on purpose.
                setDraft({ name, slug: slugTouched ? draft.slug : slugify(name) });
              }}
            />
          </div>
          <div>
            <Label htmlFor="ws-slug" required hint={t('org.workspaceSlugHint')}>
              {t('org.workspaceSlug')}
            </Label>
            <Input
              id="ws-slug"
              value={draft.slug}
              className="font-mono"
              onChange={(event) => {
                setSlugTouched(true);
                setDraft({ ...draft, slug: slugify(event.target.value) });
              }}
            />
          </div>
        </div>
      </Modal>

      <WorkspaceMembersDialog
        workspace={seatsFor}
        open={Boolean(seatsFor)}
        onClose={() => setSeatsFor(null)}
        onChanged={invalidate}
      />

      <ConfirmDialog
        open={Boolean(dropping)}
        onClose={() => setDropping(null)}
        onConfirm={() => dropping && drop.mutate(dropping.id)}
        loading={drop.isPending}
        destructive
        title={t('org.deleteWorkspaceTitle')}
        confirmLabel={t('common.delete')}
        message={t('org.deleteWorkspaceBody', { name: dropping?.name ?? '' })}
      />
    </div>
  );
}
