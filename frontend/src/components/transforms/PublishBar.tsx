'use client';

import * as React from 'react';
import { CircleCheck, GitBranch, History, Upload } from 'lucide-react';

import type { TransformDetail, TransformDiffEntry, TransformRelease } from '@/lib/types';
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
  diff, diffLoading, onRequestDiff,
}: {
  transform: TransformDetail;
  copy: PublishCopy;
  canEdit: boolean;
  publishing: boolean;
  onPublish: (notes: string) => void;
  onOpenHistory: () => void;
  diff?: TransformDiffEntry[];
  diffLoading?: boolean;
  onRequestDiff: () => void;
}) {
  const [open, setOpen] = React.useState(false);
  const [notes, setNotes] = React.useState('');
  const release = transform.active_release;
  const diverged = transform.draft_has_changes;

  return (
    <>
      <div className={cn(
        'flex h-9 shrink-0 items-center gap-2 border-b px-3 text-caption',
        diverged
          ? 'border-warning/25 bg-warning/[0.06]'
          : 'border-[rgb(var(--border-line))] bg-surface-1',
      )}>
        {release ? (
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

/** Version history, and the one-click way back to an earlier one. */
export function ReleaseHistoryModal({
  open, onClose, releases, loading, copy, canEdit, onRestore, restoring,
}: {
  open: boolean;
  onClose: () => void;
  releases?: TransformRelease[];
  loading: boolean;
  copy: PublishCopy;
  canEdit: boolean;
  onRestore: (releaseId: string) => void;
  restoring: boolean;
}) {
  return (
    <Modal open={open} onClose={onClose} title={copy.historyTitle} size="lg">
      {loading ? <Spinner /> : !releases?.length ? (
        <EmptyState title={copy.noReleases} compact />
      ) : (
        <div className="divide-y divide-[rgb(var(--border-line))]">
          {releases.map((release) => (
            <div key={release.id} className="flex items-center gap-3 py-2.5">
              <span className="w-20 shrink-0 text-caption font-emphasis text-text-primary">
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
              {release.is_active ? (
                <Badge size="xs" variant="success">{copy.active}</Badge>
              ) : canEdit && (
                <Button size="xs" variant="ghost" loading={restoring}
                  onClick={() => onRestore(release.id)}>
                  {copy.restore}
                </Button>
              )}
            </div>
          ))}
        </div>
      )}
    </Modal>
  );
}
