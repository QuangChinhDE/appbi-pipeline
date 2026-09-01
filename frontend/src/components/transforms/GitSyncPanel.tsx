'use client';

import * as React from 'react';
import {
  AlertTriangle, CheckCircle2, Github, RefreshCw,
} from 'lucide-react';

import type { GitSyncState } from '@/lib/types';
import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Checkbox, Input, Label, Select } from '@/components/ui/Input';

export type GitSyncCopy = {
  title: string;
  description: string;
  notConnected: string;
  notConnectedHint: string;
  repository: string;
  branch: string;
  branchDefault: string;
  lastSync: string;
  never: string;
  commit: string;
  nextSync: string;
  syncNow: string;
  reapply: string;
  reapplyHint: string;
  enable: string;
  enableHint: string;
  every: string;
  autoPublish: string;
  autoPublishHint: string;
  token: string;
  tokenStored: string;
  tokenReplace: string;
  tokenHint: string;
  save: string;
  disconnect: string;
  managed: string;
  managedHint: string;
  statusLabel: Record<string, string>;
  intervals: { value: number; label: string }[];
};

/**
 * The repository this Transform follows, and how closely.
 *
 * Written for the one question a reader actually has -- is what runs tonight
 * the same as what is in Git? -- so the last sync, the commit behind it and
 * the next check lead, and the settings that produce them come after.
 */
export function GitSyncPanel({
  git, copy, canEdit, saving, syncing, onSave, onSync,
}: {
  git: GitSyncState;
  copy: GitSyncCopy;
  canEdit: boolean;
  saving: boolean;
  syncing: boolean;
  onSave: (body: {
    enabled?: boolean; interval_minutes?: number; auto_publish?: boolean;
    token?: string; repo_url?: string;
  }) => void;
  onSync: (force: boolean) => void;
}) {
  const [enabled, setEnabled] = React.useState(Boolean(git.enabled));
  const [interval, setInterval] = React.useState(git.interval_minutes ?? 30);
  const [autoPublish, setAutoPublish] = React.useState(Boolean(git.auto_publish));
  const [token, setToken] = React.useState('');

  React.useEffect(() => {
    setEnabled(Boolean(git.enabled));
    setInterval(git.interval_minutes ?? 30);
    setAutoPublish(Boolean(git.auto_publish));
  }, [git.enabled, git.interval_minutes, git.auto_publish]);

  if (!git.connected) {
    return (
      <div className="rounded-lg border border-dashed border-[rgb(var(--border-line))] px-4 py-6 text-center">
        <Github className="mx-auto h-5 w-5 text-text-quaternary" />
        <p className="mt-1.5 text-caption font-emphasis text-text-secondary">
          {copy.notConnected}
        </p>
        <p className="mt-0.5 text-tiny text-text-tertiary">{copy.notConnectedHint}</p>
      </div>
    );
  }

  const failed = git.last_status === 'FAILED';
  const dirty = enabled !== Boolean(git.enabled)
    || interval !== (git.interval_minutes ?? 30)
    || autoPublish !== Boolean(git.auto_publish)
    || token.trim().length > 0;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Github className="h-4 w-4 shrink-0 text-text-tertiary" />
        <a href={git.repo_url ?? '#'} target="_blank" rel="noreferrer"
          className="min-w-0 truncate font-mono text-caption text-brand hover:underline">
          {(git.repo_url ?? '').replace('https://github.com/', '')}
        </a>
        <Badge variant="neutral" size="xs">{git.ref || copy.branchDefault}</Badge>
        {git.subdirectory ? (
          <Badge variant="subtle" size="xs">/{git.subdirectory}</Badge>
        ) : null}
      </div>

      {/* The answer to "is this in step with Git?", before any setting. */}
      <div className={cn(
        'flex items-start gap-2 rounded-md border px-3 py-2',
        failed
          ? 'border-danger/30 bg-danger/[0.06]'
          : 'border-[rgb(var(--border-line))] bg-surface-2/50',
      )}>
        {failed
          ? <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-danger" />
          : <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-success" />}
        <div className="min-w-0 flex-1">
          <p className={cn('text-caption',
            failed ? 'font-emphasis text-danger' : 'text-text-primary')}>
            {git.last_status
              ? (copy.statusLabel[git.last_status] ?? git.last_status)
              : copy.never}
            {git.last_message ? ` — ${git.last_message}` : ''}
          </p>
          <p className="mt-0.5 flex flex-wrap gap-x-3 text-tiny text-text-tertiary">
            <span>{copy.lastSync}: {formatWhen(git.last_synced_at) || copy.never}</span>
            {git.last_commit && (
              <span className="font-mono">{copy.commit} {git.last_commit.slice(0, 7)}</span>
            )}
            {enabled && git.next_sync_at && (
              <span>{copy.nextSync}: {formatWhen(git.next_sync_at)}</span>
            )}
          </p>
        </div>
      </div>

      {canEdit && (
        <div className="flex flex-wrap gap-2">
          <Button size="xs" variant="secondary" loading={syncing}
            leadingIcon={<RefreshCw className="h-3.5 w-3.5" />}
            onClick={() => onSync(false)}>{copy.syncNow}</Button>
          <Button size="xs" variant="ghost" disabled={syncing}
            title={copy.reapplyHint}
            onClick={() => onSync(true)}>{copy.reapply}</Button>
        </div>
      )}

      {canEdit && (
        <div className="space-y-2.5 border-t border-[rgb(var(--border-line))] pt-3">
          <div>
            <Checkbox checked={enabled} onChange={setEnabled} label={copy.enable} />
            <p className="ml-6 text-tiny text-text-tertiary">{copy.enableHint}</p>
          </div>
          {enabled && (
            <>
              <div className="max-w-[220px]">
                <Label>{copy.every}</Label>
                <Select value={String(interval)}
                  onChange={(event) => setInterval(Number(event.target.value))}>
                  {copy.intervals.map((item) => (
                    <option key={item.value} value={item.value}>{item.label}</option>
                  ))}
                </Select>
              </div>
              <div>
                <Checkbox checked={autoPublish} onChange={setAutoPublish}
                  label={copy.autoPublish} />
                <p className="ml-6 text-tiny text-text-tertiary">{copy.autoPublishHint}</p>
              </div>
            </>
          )}
          <div>
            <Label>{copy.token}</Label>
            <Input type="password" value={token} autoComplete="off"
              placeholder={git.has_token ? copy.tokenReplace : copy.tokenStored}
              onChange={(event) => setToken(event.target.value)} />
            <p className="mt-1 text-tiny text-text-tertiary">{copy.tokenHint}</p>
          </div>
          <div className="flex justify-end">
            <Button size="xs" variant="primary" loading={saving} disabled={!dirty}
              onClick={() => {
                onSave({
                  enabled, interval_minutes: interval, auto_publish: autoPublish,
                  ...(token.trim() ? { token: token.trim() } : {}),
                });
                setToken('');
              }}>{copy.save}</Button>
          </div>
        </div>
      )}

      {(git.managed?.length ?? 0) > 0 && (
        <div className="border-t border-[rgb(var(--border-line))] pt-3">
          <p className="text-tiny font-emphasis uppercase tracking-wide text-text-tertiary">
            {copy.managed}
          </p>
          <p className="mt-0.5 text-tiny text-text-tertiary">{copy.managedHint}</p>
          <p className="mt-1 font-mono text-tiny text-text-secondary">
            {(git.managed ?? []).join(', ')}
          </p>
        </div>
      )}
    </div>
  );
}

function formatWhen(value?: string | null): string {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleString();
}
