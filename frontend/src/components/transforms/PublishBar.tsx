'use client';

/**
 * The draft-versus-live strip, and the publish dialog behind it.
 *
 * The concept is kept from V1; what it compares has changed. V1 diffed model
 * rows. This compares the working revision's content hash against the live
 * release's -- so "Draft matches Live" means the *project* matches, including
 * every file and config the product has no form for.
 *
 * The dialog leads with downstream impact rather than a file list. A one-line
 * change to a staging model can rebuild forty marts, and a list of changed
 * files does not say so.
 */

import * as React from 'react';
import {
  AlertTriangle, ArrowRight, CheckCircle2, CircleDot, GitCommitVertical, Loader2,
} from 'lucide-react';

import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Modal } from '@/components/ui/Modal';
import type { PublishPlan, TransformRelease } from '@/lib/types';
import { cn } from '@/lib/utils';

interface PublishBarProps {
  hasUnpublishedChanges: boolean;
  activeRelease: TransformRelease | null;
  releases: TransformRelease[];
  plan: PublishPlan | null;
  planLoading?: boolean;
  publishing?: boolean;
  canOperate: boolean;
  onPublish: (notes: string, activate: boolean) => void;
  onOpenPlan: () => void;
  onActivate: (releaseId: string) => void;
  onViewRelease?: (release: TransformRelease) => void;
}

export function PublishBar({
  hasUnpublishedChanges,
  activeRelease,
  releases,
  plan,
  planLoading,
  publishing,
  canOperate,
  onPublish,
  onOpenPlan,
  onActivate,
  onViewRelease,
}: PublishBarProps) {
  const [dialogOpen, setDialogOpen] = React.useState(false);
  const [notes, setNotes] = React.useState('');
  const [activate, setActivate] = React.useState(true);

  // A release still being checked is the state people misread most: the code is
  // frozen, but it is not what production runs yet.
  const verifying = releases.find((item) => item.status === 'VERIFYING');
  const failed = releases.find(
    (item) => item.status === 'FAILED' && item.release_number > (activeRelease?.release_number ?? 0),
  );

  const open = () => { onOpenPlan(); setDialogOpen(true); };

  return (
    <>
      <div className="flex h-9 items-center gap-2 border-b border-[rgb(var(--border-line))] bg-surface-1 px-3">
        {verifying ? (
          <>
            <Loader2 className="h-3.5 w-3.5 animate-spin text-brand" />
            <span className="text-caption text-text-secondary">
              Đang kiểm tra bản {verifying.release_number} trước khi cho chạy thật
            </span>
          </>
        ) : failed ? (
          <>
            <AlertTriangle className="h-3.5 w-3.5 text-danger" />
            <span className="text-caption text-text-secondary">
              Bản {failed.release_number} không build được
            </span>
            <span className="truncate text-tiny text-text-tertiary">
              {failed.verification_error}
            </span>
          </>
        ) : hasUnpublishedChanges ? (
          <>
            <CircleDot className="h-3.5 w-3.5 text-warning" />
            <span className="text-caption text-text-secondary">
              Bản nháp có thay đổi chưa xuất bản
            </span>
          </>
        ) : activeRelease ? (
          <>
            <CheckCircle2 className="h-3.5 w-3.5 text-success" />
            <span className="text-caption text-text-secondary">
              Bản nháp trùng với bản {activeRelease.release_number} đang chạy
            </span>
          </>
        ) : (
          <>
            <AlertTriangle className="h-3.5 w-3.5 text-warning" />
            <span className="text-caption text-text-secondary">
              Chưa xuất bản lần nào — lịch chạy tự động chưa có gì để chạy
            </span>
          </>
        )}

        <div className="ml-auto flex items-center gap-2">
          {activeRelease && (
            <button
              type="button"
              onClick={() => onViewRelease?.(activeRelease)}
              className="flex items-center gap-1 text-tiny text-text-tertiary hover:text-text-primary"
            >
              <GitCommitVertical className="h-3 w-3" />
              Đang chạy: bản {activeRelease.release_number}
            </button>
          )}
          {canOperate && (
            <Button
              variant={hasUnpublishedChanges ? 'primary' : 'secondary'}
              size="xs"
              onClick={open}
              disabled={!hasUnpublishedChanges || publishing || Boolean(verifying)}
            >
              Xuất bản
            </Button>
          )}
        </div>
      </div>

      <Modal
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
        title="Xuất bản phiên bản này"
        size="lg"
      >
        <div className="space-y-4">
          <p className="text-caption text-text-secondary">
            Xuất bản sẽ đóng băng toàn bộ tệp của dự án ở trạng thái hiện tại.
            Sau đó AppBI build thử đúng bản đó; chỉ khi build thành công nó mới
            được đưa vào chạy thật.
          </p>

          {planLoading ? (
            <p className="text-caption text-text-tertiary">Đang so sánh…</p>
          ) : plan ? (
            <>
              {plan.affected_resources.length > 0 && (
                <Section
                  title={`Resource thay đổi (${plan.affected_resources.length})`}
                >
                  <div className="flex flex-wrap gap-1">
                    {plan.affected_resources.slice(0, 30).map((item) => (
                      <Badge key={item.unique_id} variant="brand" size="xs">
                        {item.name}
                      </Badge>
                    ))}
                  </div>
                </Section>
              )}

              {plan.downstream_resources.length > 0 && (
                <Section
                  title={`Sẽ build lại theo (${plan.downstream_resources.length})`}
                  hint="Những resource này không đổi code, nhưng phụ thuộc vào phần đã đổi."
                >
                  <div className="flex flex-wrap gap-1">
                    {plan.downstream_resources.slice(0, 30).map((item) => (
                      <Badge key={item.unique_id} variant="subtle" size="xs">
                        {item.name}
                      </Badge>
                    ))}
                    {plan.downstream_resources.length > 30 && (
                      <span className="text-tiny text-text-quaternary">
                        và {plan.downstream_resources.length - 30} nữa
                      </span>
                    )}
                  </div>
                </Section>
              )}

              <Section title={`Tệp thay đổi (${plan.files.length})`}>
                <ul className="max-h-48 space-y-0.5 overflow-auto">
                  {plan.files.map((file) => (
                    <li key={file.path} className="flex items-center gap-2 font-mono text-tiny">
                      <span className={cn(
                        'w-3 shrink-0 text-center font-emphasis',
                        file.change === 'A' && 'text-success',
                        file.change === 'M' && 'text-warning',
                        file.change === 'D' && 'text-danger',
                      )}>
                        {file.change}
                      </span>
                      <span className="truncate text-text-secondary">{file.path}</span>
                    </li>
                  ))}
                </ul>
              </Section>

              <div className="flex items-center gap-2 rounded-md bg-surface-2 px-2 py-1.5 font-mono text-tiny text-text-tertiary">
                <span>{plan.live_hash?.slice(0, 12) ?? 'chưa có'}</span>
                <ArrowRight className="h-3 w-3" />
                <span className="text-text-primary">{plan.draft_hash.slice(0, 12)}</span>
              </div>
            </>
          ) : null}

          <label className="block">
            <span className="mb-1 block text-caption text-text-secondary">
              Ghi chú (tuỳ chọn)
            </span>
            <textarea
              value={notes}
              onChange={(event) => setNotes(event.target.value)}
              rows={2}
              placeholder="Có gì thay đổi trong bản này?"
              className={cn(
                'w-full rounded-md border border-[rgb(var(--border-line))] bg-surface-0',
                'px-2 py-1.5 text-caption text-text-primary',
                'placeholder:text-text-quaternary focus:outline-none focus:ring-1 focus:ring-brand/40',
              )}
            />
          </label>

          <label className="flex items-start gap-2">
            <input
              type="checkbox"
              checked={activate}
              onChange={(event) => setActivate(event.target.checked)}
              className="mt-0.5 h-3.5 w-3.5 accent-[rgb(var(--brand))]"
            />
            <span className="text-caption text-text-secondary">
              Đưa vào chạy thật ngay khi kiểm tra xong
              <span className="block text-tiny text-text-tertiary">
                Bỏ chọn nếu muốn tự bấm sau khi xem kết quả kiểm tra.
              </span>
            </span>
          </label>

          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setDialogOpen(false)}>
              Huỷ
            </Button>
            <Button
              variant="primary"
              loading={publishing}
              onClick={() => { onPublish(notes, activate); setDialogOpen(false); setNotes(''); }}
            >
              Xuất bản
            </Button>
          </div>
        </div>
      </Modal>
    </>
  );
}

function Section({
  title, hint, children,
}: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="mb-1 text-caption font-emphasis text-text-primary">{title}</p>
      {hint && <p className="mb-1.5 text-tiny text-text-tertiary">{hint}</p>}
      {children}
    </div>
  );
}
