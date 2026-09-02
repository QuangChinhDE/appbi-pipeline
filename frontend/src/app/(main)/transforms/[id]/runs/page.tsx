'use client';

/**
 * Run history for one project.
 *
 * Every row names the exact code it executed: which revision, which release,
 * which environment, which command and selector. "The run of the current code"
 * is precisely the ambiguity that made a production failure impossible to
 * reproduce, so it does not exist here.
 */

import * as React from 'react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ArrowLeft, CheckCircle2, ChevronDown, ChevronRight, Clock, Loader2,
  MinusCircle, RotateCcw, XCircle,
} from 'lucide-react';

import { Badge, type BadgeVariant } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { EmptyState, ErrorState, TableSkeleton } from '@/components/ui/Feedback';
import { Select } from '@/components/ui/Input';
import { useWorkspaceId } from '@/hooks/use-current-user';
import { toastError, toastSuccess } from '@/hooks/use-toast';
import { transformApi } from '@/lib/api';
import { formatRelative } from '@/lib/format';
import { qk } from '@/lib/queryKeys';
import type { TransformInvocation } from '@/lib/types';
import { cn } from '@/lib/utils';

const ACTIVE = ['QUEUED', 'STARTING', 'RUNNING', 'CANCEL_REQUESTED'];

function statusVariant(status: string): BadgeVariant {
  if (status === 'SUCCEEDED') return 'success';
  if (ACTIVE.includes(status)) return 'info';
  if (status === 'CANCELLED') return 'subtle';
  return 'danger';
}

function statusLabel(status: string): string {
  switch (status) {
    case 'SUCCEEDED': return 'Thành công';
    case 'FAILED': return 'Thất bại';
    case 'FAILED_TO_START': return 'Không khởi động được';
    case 'CANCELLED': return 'Đã huỷ';
    case 'TIMED_OUT': return 'Quá giờ';
    case 'QUEUED': return 'Đang chờ';
    case 'STARTING': return 'Đang khởi động';
    case 'RUNNING': return 'Đang chạy';
    case 'CANCEL_REQUESTED': return 'Đang huỷ';
    default: return status;
  }
}

export default function TransformRunsPage() {
  const { id: projectId } = useParams<{ id: string }>();
  const workspaceId = useWorkspaceId();
  const queryClient = useQueryClient();

  const [command, setCommand] = React.useState('');
  const [status, setStatus] = React.useState('');
  const [environmentId, setEnvironmentId] = React.useState('');
  const [expanded, setExpanded] = React.useState<string | null>(null);

  const project = useQuery({
    queryKey: qk.transform(workspaceId, projectId),
    queryFn: () => transformApi.detail(projectId),
  });

  const filters = React.useMemo(() => ({
    command: command ? [command] : undefined,
    status: status ? [status] : undefined,
    environment_id: environmentId || undefined,
    limit: 50,
  }), [command, status, environmentId]);

  const runs = useQuery({
    queryKey: qk.transformInvocations(workspaceId, projectId, filters),
    queryFn: () => transformApi.invocations(projectId, filters),
    // Only while something is moving; a finished run is immutable.
    refetchInterval: (query) =>
      (query.state.data?.items ?? []).some((item) => ACTIVE.includes(item.status))
        ? 4_000 : false,
  });

  const retry = useMutation({
    mutationFn: (invocationId: string) => transformApi.retry(invocationId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['workspace', workspaceId] });
      toastSuccess('Đã xếp hàng chạy lại đúng phiên bản đó.');
    },
    onError: (error) => toastError(error),
  });

  const items = runs.data?.items ?? [];

  return (
    <div className="flex h-full flex-col px-4 pt-5 sm:px-6">
      <header className="mb-3 shrink-0">
        <Link
          href={`/transforms/${projectId}`}
          className="mb-2 inline-flex items-center gap-1 text-caption text-text-tertiary hover:text-text-primary"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          {project.data?.name ?? 'Transform'}
        </Link>
        <h1 className="text-h3 font-strong text-text-primary">Lịch sử chạy</h1>
        <p className="mt-1 text-caption text-text-tertiary">
          Mỗi lần chạy đều ghi rõ nó đã thực thi đúng phiên bản nào, nên có thể
          chạy lại y hệt.
        </p>
      </header>

      <div className="mb-3 flex shrink-0 flex-wrap gap-2">
        <Select value={command} onChange={(event) => setCommand(event.target.value)}>
          <option value="">Mọi lệnh</option>
          {['build', 'run', 'test', 'compile', 'parse', 'show', 'seed', 'snapshot',
            'source-freshness', 'docs-generate', 'deps'].map((item) => (
            <option key={item} value={item}>dbt {item}</option>
          ))}
        </Select>
        <Select value={status} onChange={(event) => setStatus(event.target.value)}>
          <option value="">Mọi trạng thái</option>
          {['SUCCEEDED', 'FAILED', 'CANCELLED', 'TIMED_OUT', 'RUNNING', 'QUEUED'].map((item) => (
            <option key={item} value={item}>{statusLabel(item)}</option>
          ))}
        </Select>
        {(project.data?.environments ?? []).length > 1 && (
          <Select
            value={environmentId}
            onChange={(event) => setEnvironmentId(event.target.value)}
          >
            <option value="">Mọi môi trường</option>
            {(project.data?.environments ?? []).map((item) => (
              <option key={item.id} value={item.id}>{item.name}</option>
            ))}
          </Select>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-auto pb-6">
        {runs.isLoading ? (
          <TableSkeleton rows={6} columns={5} />
        ) : runs.error ? (
          <ErrorState
            title="Không tải được lịch sử chạy"
            message={(runs.error as Error).message}
            onRetry={() => runs.refetch()}
          />
        ) : items.length === 0 ? (
          <EmptyState
            icon={Clock}
            title="Chưa có lần chạy nào"
            description="Chạy Build hoặc Preview trong trình soạn thảo để bắt đầu."
          />
        ) : (
          <ul className="space-y-1">
            {items.map((run) => (
              <RunRow
                key={run.id}
                run={run}
                expanded={expanded === run.id}
                onToggle={() => setExpanded(expanded === run.id ? null : run.id)}
                onRetry={() => retry.mutate(run.id)}
                retrying={retry.isPending}
              />
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function RunRow({
  run, expanded, onToggle, onRetry, retrying,
}: {
  run: TransformInvocation;
  expanded: boolean;
  onToggle: () => void;
  onRetry: () => void;
  retrying: boolean;
}) {
  const workspaceId = useWorkspaceId();
  const detail = useQuery({
    queryKey: qk.transformInvocation(workspaceId, run.id),
    queryFn: () => transformApi.invocation(run.id),
    enabled: expanded,
  });

  return (
    <li className="rounded-lg border border-[rgb(var(--border-line))] bg-surface-1">
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-center gap-3 px-3 py-2.5 text-left hover:bg-surface-2"
      >
        {expanded
          ? <ChevronDown className="h-3.5 w-3.5 shrink-0 text-text-quaternary" />
          : <ChevronRight className="h-3.5 w-3.5 shrink-0 text-text-quaternary" />}

        <Badge variant={statusVariant(run.status)} size="sm" className="shrink-0">
          {ACTIVE.includes(run.status) && <Loader2 className="h-2.5 w-2.5 animate-spin" />}
          {statusLabel(run.status)}
        </Badge>

        <div className="min-w-0 flex-1">
          <p className="truncate font-mono text-caption text-text-primary">
            dbt {run.command}
            {run.selector ? ` --select ${run.selector}` : ''}
            {run.exclude ? ` --exclude ${run.exclude}` : ''}
          </p>
          <p className="mt-0.5 flex flex-wrap items-center gap-x-2.5 text-tiny text-text-tertiary">
            {run.environment_name && <span>{run.environment_name}</span>}
            {/* The identity of the code that ran. Without this a production
                failure cannot be reproduced. */}
            {run.release_number !== null ? (
              <span>bản đã xuất bản {run.release_number}</span>
            ) : (
              <span>phiên bản nháp {run.revision_number}</span>
            )}
            <span>{run.trigger_type === 'SCHEDULE' ? 'theo lịch' : 'thủ công'}</span>
            {run.created_at && <span>{formatRelative(run.created_at)}</span>}
          </p>
        </div>

        {run.nodes_total > 0 && (
          <div className="hidden shrink-0 items-center gap-2 text-tiny sm:flex">
            <span className="flex items-center gap-1 text-success">
              <CheckCircle2 className="h-3 w-3" />{run.nodes_succeeded}
            </span>
            {run.nodes_failed > 0 && (
              <span className="flex items-center gap-1 text-danger">
                <XCircle className="h-3 w-3" />{run.nodes_failed}
              </span>
            )}
            {run.nodes_skipped > 0 && (
              <span className="flex items-center gap-1 text-text-quaternary">
                <MinusCircle className="h-3 w-3" />{run.nodes_skipped}
              </span>
            )}
          </div>
        )}

        {run.duration_seconds !== null && (
          <span className="hidden w-16 shrink-0 text-right font-mono text-tiny text-text-tertiary md:block">
            {run.duration_seconds < 60
              ? `${run.duration_seconds.toFixed(1)}s`
              : `${Math.floor(run.duration_seconds / 60)}m`}
          </span>
        )}
      </button>

      {expanded && (
        <div className="border-t border-[rgb(var(--border-line))] px-3 py-2.5">
          {run.error_summary && (
            <p className="mb-2 whitespace-pre-wrap rounded-md bg-danger/10 p-2 text-caption text-danger">
              {run.error_summary}
            </p>
          )}

          <dl className="mb-2 grid grid-cols-2 gap-x-4 gap-y-0.5 text-tiny sm:grid-cols-4">
            <Pair label="Phiên bản" value={`#${run.revision_number ?? '—'}`} />
            <Pair label="Bản xuất bản" value={run.release_number ? `#${run.release_number}` : '—'} />
            <Pair label="Môi trường" value={run.environment_name ?? '—'} />
            <Pair label="dbt run id" value={run.dbt_invocation_id?.slice(0, 8) ?? '—'} />
          </dl>

          {detail.isLoading ? (
            <p className="text-caption text-text-tertiary">Đang đọc chi tiết…</p>
          ) : (detail.data?.nodes.length ?? 0) > 0 ? (
            <div className="max-h-64 overflow-auto rounded-md border border-[rgb(var(--border-line))]">
              <table className="w-full text-caption">
                <thead className="sticky top-0 bg-surface-2 text-left text-text-secondary">
                  <tr>
                    <th className="px-2 py-1 font-emphasis">Resource</th>
                    <th className="px-2 py-1 font-emphasis">Trạng thái</th>
                    <th className="px-2 py-1 font-emphasis">Thời gian</th>
                    <th className="px-2 py-1 font-emphasis">Thông báo</th>
                  </tr>
                </thead>
                <tbody>
                  {detail.data!.nodes.map((node) => (
                    <tr key={node.unique_id} className="border-t border-[rgb(var(--border-line))]">
                      <td className="px-2 py-1 text-text-primary">{node.name}</td>
                      <td className="px-2 py-1">
                        <Badge variant={statusVariant(
                          node.status.toLowerCase() === 'success'
                            || node.status.toLowerCase() === 'pass'
                            ? 'SUCCEEDED' : 'FAILED',
                        )} size="xs">
                          {node.status}
                        </Badge>
                      </td>
                      <td className="px-2 py-1 font-mono text-tiny text-text-tertiary">
                        {node.execution_time?.toFixed(1) ?? '—'}s
                      </td>
                      <td className="truncate px-2 py-1 text-tiny text-text-tertiary">
                        {node.message ?? '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}

          {run.actions.can_retry && (
            <Button
              variant="secondary" size="xs" className="mt-2"
              onClick={onRetry} loading={retrying}
              leadingIcon={<RotateCcw className="h-3 w-3" />}
            >
              Chạy lại đúng phiên bản này
            </Button>
          )}
        </div>
      )}
    </li>
  );
}

function Pair({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex gap-1.5">
      <dt className="text-text-quaternary">{label}</dt>
      <dd className="truncate font-mono text-text-secondary">{value}</dd>
    </div>
  );
}
