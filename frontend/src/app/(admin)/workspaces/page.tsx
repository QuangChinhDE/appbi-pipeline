'use client';

/**
 * The chooser: what you can reach, and what needs you.
 *
 * The first question anybody has is the same one, and no workspace can answer
 * it from inside itself — a workspace is a wall. Somebody with one workspace
 * sees one and walks in; somebody with eight sees which of them is failing.
 *
 * It lives at `/workspaces` rather than `/admin`, because choosing where to
 * work is not an administrative act -- an analyst with one workspace lands
 * here too. Creating and removing one happen beside the list they change, for
 * whoever may.
 *
 * Numbers are per workspace and per reader. A count the reader holds no
 * permission to know arrives as null and is simply absent — showing zero would
 * be a different claim, and a false one.
 */

import * as React from 'react';
import Link from 'next/link';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, ArrowRight, CheckCircle2, PlayCircle, Plus, Users } from 'lucide-react';

import { organizationApi } from '@/lib/api';
import { formatRelative } from '@/lib/format';
import { usePermissions } from '@/hooks/use-permissions';
import { toastError, toastSuccess } from '@/hooks/use-toast';
import { useI18n } from '@/providers/LanguageProvider';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Input, Label } from '@/components/ui/Input';
import { ConfirmDialog, Modal } from '@/components/ui/Modal';
import { CardSkeleton, EmptyState, ErrorState } from '@/components/ui/Feedback';
import { Card, StatTile } from '@/components/layout/PageLayout';

/** Lower-case, digits and dashes — what the API accepts, applied while typing
 *  so the rejection never has to happen. */
function slugify(raw: string): string {
  return raw
    .normalize('NFD').replace(/[̀-ͯ]/g, '')
    .replace(/đ/g, 'd').replace(/Đ/g, 'd')
    .toLowerCase().trim()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 40);
}

export default function WorkspaceChooserPage() {
  const { t, locale } = useI18n();
  const queryClient = useQueryClient();
  const { canOrg } = usePermissions();

  const [creating, setCreating] = React.useState(false);
  const [draft, setDraft] = React.useState({ name: '', slug: '' });
  const [slugTouched, setSlugTouched] = React.useState(false);
  const [dropping, setDropping] = React.useState<{ id: string; name: string } | null>(null);

  const overview = useQuery({
    queryKey: ['org-overview'],
    queryFn: organizationApi.overview,
    refetchInterval: 60_000,
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['org-overview'] });
    queryClient.invalidateQueries({ queryKey: ['me'] });
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

  if (overview.error) {
    return (
      <ErrorState
        title={t('common.errorTitle')}
        message={(overview.error as Error).message}
        onRetry={() => overview.refetch()}
      />
    );
  }

  const data = overview.data;
  const administersMembers = Boolean(data?.administers_members);
  // Failing first, then busiest: the order somebody scanning this reads in.
  const rows = [...(data?.workspaces ?? [])].sort((a, b) =>
    ((b.failing_count ?? 0) - (a.failing_count ?? 0))
    || ((b.pipeline_count ?? 0) - (a.pipeline_count ?? 0)));
  const single = rows.length === 1;

  return (
    <div className="space-y-4">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <h1 className="text-h3 font-strong text-text-primary">
            {single ? t('admin.homeTitleOne') : t('admin.overviewTitle')}
          </h1>
          <p className="mt-1 max-w-2xl text-caption text-text-tertiary">
            {single ? t('admin.homeSubtitleOne') : t('admin.overviewSubtitle')}
          </p>
        </div>
        {canOrg('create') && (
          <Button variant="primary" onClick={() => setCreating(true)}
                  leadingIcon={<Plus className="h-3.5 w-3.5" />}>
            {t('org.addWorkspace')}
          </Button>
        )}
      </header>

      {overview.isLoading ? (
        <CardSkeleton count={4} />
      ) : (
        <>
          {/* Totals are an organisation's question. Somebody with one
              workspace is not served by a tile saying "1 workspace". */}
          {!single && (
            <div className="grid auto-rows-fr gap-3 sm:grid-cols-2 xl:grid-cols-4">
              <StatTile label={t('admin.kpiWorkspaces')} value={data?.total_workspaces ?? 0} />
              <StatTile label={t('admin.kpiPipelines')} value={data?.total_pipelines ?? 0} />
              <StatTile
                label={t('admin.kpiFailing')}
                value={data?.total_failing ?? 0}
                tone={(data?.total_failing ?? 0) > 0 ? 'danger' : 'default'}
                icon={<AlertTriangle className="h-3.5 w-3.5" />}
              />
              <StatTile
                label={t('admin.kpiPeople')}
                value={data?.administers_organization ? (data?.total_people ?? 0) : '—'}
              />
            </div>
          )}

          <Card title={t('admin.workspaceHealth')} padded={false}>
            {rows.length === 0 ? (
              <EmptyState title={t('admin.noWorkspacesYours')} compact />
            ) : (
              <ul className="divide-y divide-[rgb(var(--border-line))]">
                {rows.map((workspace) => (
                  <li key={workspace.id}
                      className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3">
                    <span className="min-w-0 flex-1 basis-52">
                      <span className="flex flex-wrap items-center gap-2">
                        {/* The name walks in. It used to open a console page
                            about the workspace, which is a thing to read where
                            people wanted a thing to enter. */}
                        <Link
                          href={`/workspaces/${workspace.id}/overview`}
                          className="truncate text-small font-emphasis text-text-primary hover:text-brand"
                        >
                          {workspace.name}
                        </Link>
                        {workspace.failing_count != null && workspace.failing_count > 0 ? (
                          <Badge variant="danger" size="xs">
                            <AlertTriangle className="h-2.5 w-2.5" />
                            {t('admin.failingCount', { n: workspace.failing_count })}
                          </Badge>
                        ) : workspace.pipeline_count ? (
                          <Badge variant="success" size="xs">
                            <CheckCircle2 className="h-2.5 w-2.5" />
                            {t('admin.allHealthy')}
                          </Badge>
                        ) : null}
                        {workspace.running_count != null && workspace.running_count > 0 && (
                          <Badge variant="brand" size="xs">
                            <PlayCircle className="h-2.5 w-2.5" />
                            {t('admin.runningCount', { n: workspace.running_count })}
                          </Badge>
                        )}
                        {workspace.status !== 'ACTIVE' && (
                          <Badge variant="warning" size="xs">{workspace.status}</Badge>
                        )}
                        {workspace.via_organization && (
                          <Badge variant="neutral" size="xs">{t('org.viaOrganization')}</Badge>
                        )}
                      </span>
                      <span className="mt-0.5 flex flex-wrap items-center gap-x-3 text-tiny text-text-quaternary">
                        <span className="font-mono">{workspace.slug}</span>
                        {workspace.member_count != null && (
                          <span>{t('org.seatCount', { n: workspace.member_count })}</span>
                        )}
                        {workspace.pipeline_count != null && (
                          <span>{t('admin.pipelineCount', { n: workspace.pipeline_count })}</span>
                        )}
                        {workspace.last_run_at
                          ? <span>{t('admin.lastRun', {
                              when: formatRelative(workspace.last_run_at, locale),
                            })}</span>
                          : workspace.pipeline_count != null && (
                            <span>{t('admin.neverRun')}</span>
                          )}
                        {/* An empty workspace is not a healthy one; it is one
                            nobody has been let into yet. */}
                        {workspace.member_count === 0 && (
                          <span className="text-warning">{t('admin.nobodyInside')}</span>
                        )}
                      </span>
                    </span>

                    {administersMembers && (
                      <Link href="/admin/people">
                        <Button size="xs" variant="ghost"
                                leadingIcon={<Users className="h-3 w-3" />}>
                          {t('org.manageSeats')}
                        </Button>
                      </Link>
                    )}
                    <Link href={`/workspaces/${workspace.id}/overview`}>
                      <Button size="xs" variant="secondary">
                        {t('admin.openWorkspace')}
                        <ArrowRight className="ml-1 h-3 w-3" />
                      </Button>
                    </Link>
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

          {administersMembers && !single && (
            <p className="text-tiny text-text-quaternary">
              {t('admin.overviewFootnote')}{' '}
              <Link href="/admin/people" className="text-brand hover:underline">
                {t('admin.people')}
              </Link>
            </p>
          )}
        </>
      )}

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
                    disabled={!draft.name.trim() || !draft.slug.trim()}
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
                // stops — typing a name should not rewrite a slug chosen on
                // purpose.
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
