'use client';

import * as React from 'react';
import { CircleCheck, GitBranch, History, Table2, TriangleAlert, Upload } from 'lucide-react';

import type {
  TransformDetail, TransformDiffEntry, TransformRelease, TransformReleaseModel,
} from '@/lib/types';
import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Modal } from '@/components/ui/Modal';
import { Input, Label, Textarea } from '@/components/ui/Input';
import { EmptyState, Spinner } from '@/components/ui/Feedback';

export type PublishCopy = {
  draft: string;
  live: string;
  liveVersion: string;
  nothingPublished: string;
  nothingPublishedHint: string;
  inSync: string;
  outOfSync: string;
  publish: string;
  publishTitle: string;
  publishHint: string;
  notes: string;
  notesPlaceholder: string;
  history: string;
  historyTitle: string;
  noReleases: string;
  restore: string;
  active: string;
  modelsCounted: string;
  cancel: string;
  scheduleRuns: string;
  changes: string;
  noChanges: string;
  changeAdded: string;
  changeRemoved: string;
  changeModified: string;
  changeUnchanged: string;
  inspect: string;
  before: string;
  after: string;
  restoreToDraft: string;
  restoreHint: string;
  backToList: string;
  noSqlChange: string;
  verifying: string;
  verifyingHint: string;
  rejected: string;
  statusLabel: Record<string, string>;
};

/**
 * The one line that answers "will tonight's run include what I just typed?".
 *
 * A schedule executes a published snapshot, so the draft in the editor and the
 * code that actually runs can differ. Leaving that implicit is how people ship
 * half-finished models by accident, so it gets a permanent strip rather than a
 * detail buried in settings.
 */
export function PublishBar({
  transform, copy, canEdit, publishing, onPublish, onOpenHistory,
  diff, diffLoading, onRequestDiff, latestRelease,
}: {
  transform: TransformDetail;
  copy: PublishCopy;
  canEdit: boolean;
  publishing: boolean;
  onPublish: (notes: string) => void;
  onOpenHistory: () => void;
  /** The newest release, live or not -- a publish under verification is
   *  neither active nor absent, and the strip has to say which. */
  latestRelease?: TransformRelease;
  diff?: TransformDiffEntry[];
  diffLoading?: boolean;
  onRequestDiff: () => void;
}) {
  const [open, setOpen] = React.useState(false);
  const [notes, setNotes] = React.useState('');
  const release = transform.active_release;
  const diverged = transform.draft_has_changes;
  // A snapshot still being compiled, and one whose compile said no. Neither is
  // the live release, and neither is "nothing published" either.
  const verifying = latestRelease?.status === 'VERIFYING' ? latestRelease : undefined;
  const rejected = latestRelease?.status === 'FAILED' ? latestRelease : undefined;

  return (
    <>
      <div className={cn(
        'flex h-9 shrink-0 items-center gap-2 border-b px-3 text-caption',
        rejected
          ? 'border-danger/25 bg-danger/[0.06]'
          : verifying
            ? 'border-info/25 bg-info/[0.06]'
            : diverged
              ? 'border-warning/25 bg-warning/[0.06]'
              : 'border-[rgb(var(--border-line))] bg-surface-1',
      )}>
        {verifying ? (
          <>
            <Spinner />
            <span className="text-text-secondary">
              {copy.verifying.replace('{n}', String(verifying.release_number))}
            </span>
            <span className="min-w-0 truncate text-text-tertiary">
              · {copy.verifyingHint}
            </span>
          </>
        ) : rejected ? (
          <>
            <TriangleAlert className="h-3.5 w-3.5 shrink-0 text-danger" />
            <span className="text-danger">
              {copy.rejected.replace('{n}', String(rejected.release_number))}
            </span>
            <span className="min-w-0 truncate text-text-tertiary"
              title={rejected.verify_error ?? undefined}>
              · {rejected.verify_error}
            </span>
          </>
        ) : release ? (
          <>
            <GitBranch className="h-3.5 w-3.5 shrink-0 text-text-quaternary" />
            <span className="text-text-secondary">
              {copy.live}
              {' '}
              <span className="font-emphasis text-text-primary">
                {copy.liveVersion.replace('{n}', String(release.release_number))}
              </span>
            </span>
            {diverged ? (
              <span className="min-w-0 truncate text-text-tertiary">· {copy.outOfSync}</span>
            ) : (
              <span className="flex items-center gap-1 text-success">
                <CircleCheck className="h-3.5 w-3.5" />
                {copy.inSync}
              </span>
            )}
          </>
        ) : (
          <>
            <Badge size="xs" variant="warning">{copy.draft}</Badge>
            <span className="min-w-0 truncate text-text-tertiary">
              {copy.nothingPublishedHint}
            </span>
          </>
        )}
        <div className="ml-auto flex shrink-0 items-center gap-1.5">
          <Button size="xs" variant="ghost" leadingIcon={<History className="h-3.5 w-3.5" />}
            onClick={onOpenHistory}>
            {copy.history}
          </Button>
          {canEdit && (
            <Button
              size="xs" variant={diverged || !release ? 'primary' : 'secondary'}
              leadingIcon={<Upload className="h-3.5 w-3.5" />}
              onClick={() => { setNotes(''); setOpen(true); onRequestDiff(); }}
            >
              {copy.publish}
            </Button>
          )}
        </div>
      </div>

      <Modal
        open={open} onClose={() => setOpen(false)}
        title={copy.publishTitle} description={copy.publishHint} size="sm"
        footer={<>
          <Button size="sm" variant="ghost" onClick={() => setOpen(false)}>{copy.cancel}</Button>
          <Button size="sm" variant="primary" loading={publishing}
            onClick={() => { onPublish(notes); setOpen(false); }}>
            {copy.publish}
          </Button>
        </>}
      >
        <div className="space-y-3">
          {/* What is about to change, before it changes. Publishing without
              seeing this is how somebody ships an experiment by accident. */}
          <div>
            <Label>{copy.changes}</Label>
            {diffLoading ? <Spinner /> : !diff?.length ? (
              <p className="text-tiny text-text-quaternary">{copy.noChanges}</p>
            ) : (
              <ul className="max-h-40 space-y-1 overflow-auto rounded-md border border-[rgb(var(--border-line))] p-2">
                {diff.map((entry) => (
                  <li key={entry.name} className="flex items-center gap-2 text-tiny">
                    <span className={cn(
                      'w-16 shrink-0 font-emphasis uppercase',
                      entry.change === 'ADDED' ? 'text-success'
                        : entry.change === 'REMOVED' ? 'text-danger' : 'text-warning',
                    )}>
                      {entry.change === 'ADDED' ? copy.changeAdded
                        : entry.change === 'REMOVED' ? copy.changeRemoved
                          : copy.changeModified}
                    </span>
                    <Table2 className="h-3 w-3 shrink-0 text-text-quaternary" />
                    <span className="min-w-0 truncate font-mono text-text-secondary">
                      {entry.name}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div>
            <Label>{copy.notes}</Label>
            <Textarea
              rows={3} value={notes} placeholder={copy.notesPlaceholder}
              onChange={(event) => setNotes(event.target.value)}
            />
          </div>
        </div>
      </Modal>
    </>
  );
}

/**
 * Version history, and the two ways back.
 *
 * Reading comes before either: a version is only worth restoring once you can
 * see what it contains, so opening a row shows the SQL it froze beside the SQL
 * it replaced.
 */
export function ReleaseHistoryModal({
  open, onClose, releases, loading, copy, canEdit, onRestore, restoring,
  models, modelsLoading, onInspect, inspecting, onRestoreDraft, restoringDraft,
}: {
  open: boolean;
  onClose: () => void;
  releases?: TransformRelease[];
  loading: boolean;
  copy: PublishCopy;
  canEdit: boolean;
  onRestore: (releaseId: string) => void;
  restoring: boolean;
  /** The opened release's models, once fetched. */
  models?: TransformReleaseModel[];
  modelsLoading?: boolean;
  onInspect: (releaseId: string | null) => void;
  /** Which release is open, or null for the list. */
  inspecting: TransformRelease | null;
  onRestoreDraft: (releaseId: string) => void;
  restoringDraft: boolean;
}) {
  const [openModel, setOpenModel] = React.useState<string | null>(null);

  React.useEffect(() => { setOpenModel(null); }, [inspecting?.id]);

  return (
    <Modal
      open={open}
      onClose={() => { onInspect(null); onClose(); }}
      title={inspecting
        ? copy.liveVersion.replace('{n}', String(inspecting.release_number))
        : copy.historyTitle}
      size="xl"
      footer={inspecting ? <>
        <Button size="sm" variant="ghost" onClick={() => onInspect(null)}>
          {copy.backToList}
        </Button>
        {canEdit && (
          <Button size="sm" variant="secondary" loading={restoringDraft}
            onClick={() => onRestoreDraft(inspecting.id)}>
            {copy.restoreToDraft}
          </Button>
        )}
        {/* Copying the SQL back into the draft is always allowed -- that is how
            you recover from a version that failed. Making it live is not: the
            server refuses it, and offering the button anyway only produces an
            error the user could not have predicted. */}
        {canEdit && !inspecting.is_active && (
          <Button size="sm" variant="primary" loading={restoring}
            disabled={inspecting.status !== 'READY'}
            title={inspecting.status !== 'READY'
              ? (inspecting.verify_error ?? copy.statusLabel[inspecting.status]) : undefined}
            onClick={() => onRestore(inspecting.id)}>
            {copy.restore}
          </Button>
        )}
      </> : undefined}
    >
      {inspecting ? (
        <div className="space-y-3">
          <p className="text-tiny text-text-tertiary">{copy.restoreHint}</p>
          {modelsLoading ? <Spinner /> : (
            <div className="divide-y divide-[rgb(var(--border-line))]">
              {(models ?? []).map((model) => {
                const expanded = openModel === model.name;
                return (
                  <div key={model.name}>
                    <button
                      type="button"
                      onClick={() => setOpenModel(expanded ? null : model.name)}
                      className="flex w-full items-center gap-2 py-2 text-left"
                    >
                      <span className={cn(
                        'w-20 shrink-0 text-tiny font-emphasis uppercase',
                        model.change === 'ADDED' ? 'text-success'
                          : model.change === 'REMOVED' ? 'text-danger'
                            : model.change === 'MODIFIED' ? 'text-warning'
                              : 'text-text-quaternary',
                      )}>
                        {model.change === 'ADDED' ? copy.changeAdded
                          : model.change === 'REMOVED' ? copy.changeRemoved
                            : model.change === 'MODIFIED' ? copy.changeModified
                              : copy.changeUnchanged}
                      </span>
                      <Table2 className="h-3.5 w-3.5 shrink-0 text-text-quaternary" />
                      <span className="min-w-0 flex-1 truncate font-mono text-caption text-text-secondary">
                        {model.name}
                      </span>
                      <span className="shrink-0 text-tiny text-brand">{copy.inspect}</span>
                    </button>
                    {expanded && (
                      <div className={cn(
                        'grid gap-3 pb-3',
                        model.change === 'MODIFIED' ? 'md:grid-cols-2' : 'grid-cols-1',
                      )}>
                        {model.change === 'MODIFIED' && (
                          <div>
                            <p className="mb-1 text-tiny font-emphasis uppercase text-text-quaternary">
                              {copy.before}
                            </p>
                            <pre className="max-h-64 overflow-auto rounded-md bg-surface-2 p-2.5 font-mono text-tiny leading-5 text-text-secondary">
                              {model.previous_sql}
                            </pre>
                          </div>
                        )}
                        <div>
                          <p className="mb-1 text-tiny font-emphasis uppercase text-text-quaternary">
                            {model.change === 'REMOVED' ? copy.before : copy.after}
                          </p>
                          <pre className="max-h-64 overflow-auto rounded-md bg-surface-2 p-2.5 font-mono text-tiny leading-5 text-text-secondary">
                            {model.sql ?? model.previous_sql}
                          </pre>
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      ) : loading ? <Spinner /> : !releases?.length ? (
        <EmptyState title={copy.noReleases} compact />
      ) : (
        <div className="divide-y divide-[rgb(var(--border-line))]">
          {releases.map((release) => (
            <div key={release.id} className="flex items-center gap-3 py-2.5">
              <span className="w-24 shrink-0 text-caption font-emphasis text-text-primary">
                {copy.liveVersion.replace('{n}', String(release.release_number))}
              </span>
              <div className="min-w-0 flex-1">
                <p className="truncate text-caption text-text-secondary">
                  {release.notes || '—'}
                </p>
                <p className="text-tiny text-text-quaternary">
                  {new Date(release.created_at).toLocaleString()}
                  {' · '}
                  {release.model_count} {copy.modelsCounted}
                </p>
              </div>
              {release.is_active && <Badge size="xs" variant="success">{copy.active}</Badge>}
              {/* A version that never compiled is in this list because it was
                  published, not because it can be gone back to. Saying which
                  is the difference between a rollback target and a dead end. */}
              {!release.is_active && release.status !== 'READY' && (
                <Badge size="xs" variant={release.status === 'FAILED' ? 'danger' : 'info'}
                  title={release.verify_error ?? undefined}>
                  {copy.statusLabel[release.status] ?? release.status}
                </Badge>
              )}
              <Button size="xs" variant="ghost" onClick={() => onInspect(release.id)}>
                {copy.inspect}
              </Button>
            </div>
          ))}
        </div>
      )}
    </Modal>
  );
}
