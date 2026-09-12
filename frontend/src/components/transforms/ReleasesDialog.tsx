'use client';

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, CheckCircle2, Loader2, RotateCcw, Upload } from 'lucide-react';
import * as React from 'react';

import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { EmptyState } from '@/components/ui/Feedback';
import { ConfirmDialog, Modal } from '@/components/ui/Modal';
import { toastError, toastSuccess } from '@/hooks/use-toast';
import { transformApi } from '@/lib/api';
import { formatDateTime } from '@/lib/format';
import type { TransformRelease } from '@/lib/types';
import { useI18n } from '@/providers/LanguageProvider';

/**
 * The published versions of a project, and what can be done with them.
 *
 * Everything here already existed and none of it was reachable. The backend
 * lists releases, activates one, and restores its files into the editor; the
 * API client had all three; `PublishBar` declared an `onActivate` prop that
 * nothing ever called and an `onViewRelease` the page never passed. So the
 * button reading "Đang chạy: bản 1" did nothing at all, and a release that
 * broke production could only be fixed forward -- there was no way to put
 * yesterday's version back.
 *
 * Two different actions, deliberately worded apart, because confusing them is
 * how somebody edits for an hour and wonders why production has not changed:
 *
 *   activate  changes what production runs. Nothing in the editor moves.
 *   restore   changes what is in the editor. Production keeps running what it
 *             was running until somebody publishes.
 */
export function ReleasesDialog({
  projectId, releases, open, onClose, canOperate, canEdit,
}: {
  projectId: string;
  releases: TransformRelease[];
  open: boolean;
  onClose: () => void;
  canOperate: boolean;
  canEdit: boolean;
}) {
  const { locale } = useI18n();
  const queryClient = useQueryClient();
  const [confirmRestore, setConfirmRestore] = React.useState<TransformRelease | null>(null);

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['workspace'] });

  const activate = useMutation({
    mutationFn: (releaseId: string) => transformApi.activateRelease(projectId, releaseId),
    onSuccess: () => {
      refresh();
      toastSuccess('Đã đổi bản đang chạy thật');
    },
    onError: toastError,
  });

  const restore = useMutation({
    mutationFn: (releaseId: string) => transformApi.restoreRelease(projectId, releaseId),
    onSuccess: () => {
      refresh();
      setConfirmRestore(null);
      toastSuccess('Đã đưa bản đó vào trình soạn thảo',
        'Bản đang chạy thật chưa đổi. Xuất bản nếu muốn nó chạy.');
      onClose();
    },
    onError: (caught) => { setConfirmRestore(null); toastError(caught); },
  });

  const ordered = [...releases].sort((a, b) => b.release_number - a.release_number);

  return (
    <>
      <Modal open={open} onClose={onClose} title="Các bản đã xuất bản">
        {ordered.length === 0 ? (
          <EmptyState
            title="Chưa xuất bản lần nào"
            description="Xuất bản một bản để lịch chạy tự động có thứ để chạy."
          />
        ) : (
          <div className="space-y-2">
            {ordered.map((release) => (
              <div
                key={release.id}
                className="rounded-md border border-[rgb(var(--border-line))] px-3 py-2.5"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-caption font-strong text-text-primary">
                    Bản {release.release_number}
                  </span>
                  {release.is_active && (
                    <Badge variant="success" size="xs">
                      <CheckCircle2 className="mr-1 h-3 w-3" />Đang chạy thật
                    </Badge>
                  )}
                  {release.status === 'VERIFYING' && (
                    <Badge variant="neutral" size="xs">
                      <Loader2 className="mr-1 h-3 w-3 animate-spin" />Đang kiểm tra
                    </Badge>
                  )}
                  {release.status === 'FAILED' && (
                    <Badge variant="danger" size="xs">
                      <AlertTriangle className="mr-1 h-3 w-3" />Build hỏng
                    </Badge>
                  )}
                  <span className="ml-auto text-tiny text-text-tertiary">
                    {release.activated_at
                      ? `Chạy thật từ ${formatDateTime(release.activated_at, locale)}`
                      : release.verified_at
                        ? `Kiểm tra xong ${formatDateTime(release.verified_at, locale)}`
                        : ''}
                  </span>
                </div>

                <p className="mt-1 text-tiny text-text-tertiary">
                  {release.file_count} tệp
                  {release.revision_number ? ` · bản nháp ${release.revision_number}` : ''}
                  {release.dbt_version ? ` · dbt ${release.dbt_version}` : ''}
                  {release.environment_name ? ` · ${release.environment_name}` : ''}
                </p>

                {/* The reason a release failed is the only thing that makes the
                    badge actionable, and it was already being fetched. */}
                {release.verification_error && (
                  <p className="mt-1 text-tiny text-danger">{release.verification_error}</p>
                )}

                <div className="mt-2 flex flex-wrap gap-2">
                  {/* `READY` is a version that built successfully and is not
                      live; `RETIRED` is one that was live and was replaced --
                      which is exactly what rolling back means. The backend
                      accepts both and refuses everything else. */}
                  {canOperate && !release.is_active
                    && (release.status === 'READY' || release.status === 'RETIRED') && (
                    <Button
                      size="xs" variant="secondary"
                      loading={activate.isPending}
                      onClick={() => activate.mutate(release.id)}
                      leadingIcon={<Upload className="h-3 w-3" />}
                    >
                      Cho bản này chạy thật
                    </Button>
                  )}
                  {canEdit && (
                    <Button
                      size="xs" variant="ghost"
                      onClick={() => setConfirmRestore(release)}
                      leadingIcon={<RotateCcw className="h-3 w-3" />}
                    >
                      Mở lại trong trình soạn thảo
                    </Button>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </Modal>

      {/* Restoring overwrites the draft, which may hold work nobody has
          published. Cheap to confirm, expensive to undo. */}
      <ConfirmDialog
        open={confirmRestore !== null}
        onClose={() => setConfirmRestore(null)}
        onConfirm={() => confirmRestore && restore.mutate(confirmRestore.id)}
        title={`Mở lại bản ${confirmRestore?.release_number ?? ''} trong trình soạn thảo?`}
        message={
          'Toàn bộ tệp trong trình soạn thảo sẽ bị thay bằng tệp của bản đó. '
          + 'Thay đổi chưa xuất bản sẽ mất. Bản đang chạy thật không đổi — '
          + 'muốn nó đổi thì xuất bản lại sau khi xem xong.'
        }
        confirmLabel="Mở lại"
        loading={restore.isPending}
      />
    </>
  );
}
