'use client';

/**
 * Source control, on the same files dbt runs.
 *
 * V1's Git support was one-way and lossy by construction: it read a repository,
 * converted it into product rows, and warned about everything it could not
 * represent. Round-tripping was not a missing feature, it was impossible.
 *
 * Here the file set *is* the state, so a commit is the working revision with a
 * message on it. Three states are kept distinct, because conflating them is what
 * makes a Git UI untrustworthy:
 *
 *   clean      the file matches the last commit
 *   unsaved    edited in the editor, not written to a revision yet
 *   uncommitted  saved here, not in the repository
 */

import * as React from 'react';
import {
  ArrowDownToLine, ArrowUpFromLine, Check, GitBranch, GitCommitVertical,
  RefreshCw, TriangleAlert,
} from 'lucide-react';

import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Modal } from '@/components/ui/Modal';
import type { GitBranch as Branch, GitStatus } from '@/lib/types';
import { cn } from '@/lib/utils';

interface GitPanelProps {
  status: GitStatus | null;
  branches: Branch[];
  loading?: boolean;
  busy?: boolean;
  canEdit: boolean;
  onRefresh: () => void;
  onPull: (discardLocal: boolean) => void;
  onCommit: (message: string, paths: string[] | null) => void;
  onCheckout: (branch: string, discardLocal: boolean) => void;
  onOpenDiff: (path: string) => void;
}

export function GitPanel({
  status, branches, loading, busy, canEdit,
  onRefresh, onPull, onCommit, onCheckout, onOpenDiff,
}: GitPanelProps) {
  const [message, setMessage] = React.useState('');
  const [staged, setStaged] = React.useState<Set<string>>(() => new Set());
  const [branchOpen, setBranchOpen] = React.useState(false);

  // Everything is staged by default; unticking is the deliberate act, which
  // matches how people actually commit from an editor.
  React.useEffect(() => {
    setStaged(new Set(status?.changes.map((item) => item.path) ?? []));
  }, [status?.changes]);

  if (loading && !status) {
    return <p className="p-3 text-caption text-text-tertiary">Đang đọc trạng thái…</p>;
  }
  if (!status) {
    return (
      <p className="p-3 text-caption text-text-tertiary">
        Dự án này không kết nối với repository nào.
      </p>
    );
  }

  const toggle = (path: string) => {
    setStaged((current) => {
      const next = new Set(current);
      if (next.has(path)) next.delete(path); else next.add(path);
      return next;
    });
  };

  const stagedPaths = status.changes
    .map((item) => item.path)
    .filter((path) => staged.has(path));
  const allStaged = stagedPaths.length === status.changes.length;

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-1.5 border-b border-[rgb(var(--border-line))] px-2 py-1.5">
        <button
          type="button"
          onClick={() => setBranchOpen(true)}
          disabled={!canEdit}
          className={cn(
            'flex min-w-0 items-center gap-1.5 rounded-sm px-1.5 py-1 text-caption',
            'text-text-secondary hover:bg-surface-2 disabled:pointer-events-none',
          )}
        >
          <GitBranch className="h-3.5 w-3.5 shrink-0 text-text-tertiary" />
          <span className="truncate font-emphasis">{status.branch}</span>
        </button>
        {status.behind && (
          <Badge variant="warning" size="xs">có commit mới</Badge>
        )}
        <Button
          variant="ghost" size="xs" onClick={onRefresh} loading={busy}
          aria-label="Làm mới" className="ml-auto"
        >
          <RefreshCw className="h-3.5 w-3.5" />
        </Button>
      </div>

      <div className="flex gap-1.5 border-b border-[rgb(var(--border-line))] px-2 py-1.5">
        <Button
          variant="secondary" size="xs" fullWidth
          onClick={() => onPull(false)}
          disabled={!canEdit || busy}
          leadingIcon={<ArrowDownToLine className="h-3 w-3" />}
        >
          Lấy về
        </Button>
        <Button
          variant="secondary" size="xs" fullWidth
          onClick={() => onCommit(message, allStaged ? null : stagedPaths)}
          disabled={!canEdit || busy || stagedPaths.length === 0 || !message.trim()}
          leadingIcon={<ArrowUpFromLine className="h-3 w-3" />}
        >
          Commit &amp; đẩy lên
        </Button>
      </div>

      {status.behind && (
        <div className="flex items-start gap-2 border-b border-[rgb(var(--border-line))] bg-warning/10 px-2 py-1.5">
          <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warning" />
          <p className="text-tiny text-text-secondary">
            Nhánh trên GitHub đã có commit mới. Hãy lấy về trước khi commit, để
            thay đổi của bạn nằm trên chúng.
          </p>
        </div>
      )}

      <div className="min-h-0 flex-1 overflow-auto">
        {status.changes.length === 0 ? (
          <p className="flex items-center justify-center gap-1.5 px-3 py-8 text-caption text-text-tertiary">
            <Check className="h-3.5 w-3.5 text-success" />
            Không có thay đổi nào chưa commit
          </p>
        ) : (
          <>
            <p className="px-2 py-1.5 text-tiny uppercase tracking-wide text-text-quaternary">
              Thay đổi ({status.changes.length})
            </p>
            {status.changes.map((change) => (
              <div
                key={change.path}
                className="group flex items-center gap-1.5 px-2 py-1 hover:bg-surface-2"
              >
                <input
                  type="checkbox"
                  checked={staged.has(change.path)}
                  onChange={() => toggle(change.path)}
                  disabled={!canEdit}
                  className="h-3 w-3 shrink-0 accent-[rgb(var(--brand))]"
                  aria-label={`Chọn ${change.path}`}
                />
                <span className={cn(
                  'w-3 shrink-0 text-center font-mono text-tiny font-emphasis',
                  change.change === 'A' && 'text-success',
                  change.change === 'M' && 'text-warning',
                  change.change === 'D' && 'text-danger',
                )}>
                  {change.change}
                </span>
                <button
                  type="button"
                  onClick={() => onOpenDiff(change.path)}
                  className="min-w-0 flex-1 truncate text-left text-caption text-text-secondary hover:text-text-primary"
                  title={change.path}
                >
                  {change.path}
                </button>
              </div>
            ))}
          </>
        )}
      </div>

      {canEdit && status.changes.length > 0 && (
        <div className="border-t border-[rgb(var(--border-line))] p-2">
          <textarea
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            rows={2}
            placeholder="Nội dung commit"
            className={cn(
              'w-full resize-none rounded-sm border border-[rgb(var(--border-line))]',
              'bg-surface-0 px-2 py-1.5 text-caption text-text-primary',
              'placeholder:text-text-quaternary focus:outline-none focus:ring-1 focus:ring-brand/40',
            )}
          />
          <p className="mt-1 text-tiny text-text-quaternary">
            {stagedPaths.length}/{status.changes.length} tệp được chọn
          </p>
        </div>
      )}

      {status.head_commit_sha && (
        <div className="flex items-center gap-1.5 border-t border-[rgb(var(--border-line))] px-2 py-1.5">
          <GitCommitVertical className="h-3 w-3 shrink-0 text-text-quaternary" />
          <span className="truncate font-mono text-tiny text-text-tertiary">
            {status.head_commit_sha.slice(0, 8)}
          </span>
          {status.last_status === 'FAILED' && status.last_message && (
            <span className="truncate text-tiny text-danger" title={status.last_message}>
              {status.last_message}
            </span>
          )}
        </div>
      )}

      <Modal
        open={branchOpen}
        onClose={() => setBranchOpen(false)}
        title="Chuyển nhánh"
      >
        <div className="space-y-2">
          {status.changes.length > 0 && (
            <p className="rounded-md bg-warning/10 p-2 text-caption text-warning">
              Có {status.changes.length} tệp chưa commit. Chuyển nhánh sẽ bỏ
              những thay đổi đó.
            </p>
          )}
          <ul className="max-h-72 space-y-0.5 overflow-auto">
            {branches.map((branch) => (
              <li key={branch.name}>
                <button
                  type="button"
                  onClick={() => {
                    onCheckout(branch.name, status.changes.length > 0);
                    setBranchOpen(false);
                  }}
                  disabled={branch.current}
                  className={cn(
                    'flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-left text-caption',
                    branch.current
                      ? 'bg-brand/10 text-text-primary'
                      : 'text-text-secondary hover:bg-surface-2',
                  )}
                >
                  <GitBranch className="h-3.5 w-3.5 shrink-0 text-text-tertiary" />
                  <span className="truncate">{branch.name}</span>
                  {branch.protected && (
                    <Badge variant="subtle" size="xs" className="ml-auto">bảo vệ</Badge>
                  )}
                  {branch.current && (
                    <Check className="ml-auto h-3.5 w-3.5 shrink-0 text-brand" />
                  )}
                </button>
              </li>
            ))}
          </ul>
        </div>
      </Modal>
    </div>
  );
}
