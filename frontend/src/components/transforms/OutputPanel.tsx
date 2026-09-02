'use client';

/**
 * The bottom panel: Preview, Results, Problems, Compiled, Logs.
 *
 * Five answers to five different questions, kept apart on purpose:
 *
 *   Preview   what the query returns          (`dbt show`)
 *   Results   what happened per resource      (`run_results.json`)
 *   Problems  everything currently wrong      (parse + run + tests)
 *   Compiled  the SQL the warehouse receives  (`manifest.json`)
 *   Logs      dbt's own output                (the process)
 *
 * Compile is not warehouse validation and Results is not a log; conflating them
 * is how somebody ends up reading a stack trace to find out that one test
 * failed.
 */

import * as React from 'react';
import {
  AlertTriangle, CheckCircle2, ChevronRight, Clock, Download, MinusCircle,
  ScrollText, XCircle,
} from 'lucide-react';

import { Badge, type BadgeVariant } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import type {
  CompiledCode, InvocationNode, PreviewResult, TransformInvocationDetail,
  TransformLogPage, TransformProblem,
} from '@/lib/types';
import { cn } from '@/lib/utils';

export type OutputTab = 'preview' | 'results' | 'problems' | 'compiled' | 'logs';

const TABS: { id: OutputTab; label: string }[] = [
  { id: 'preview', label: 'Preview' },
  { id: 'results', label: 'Results' },
  { id: 'problems', label: 'Problems' },
  { id: 'compiled', label: 'Compiled' },
  { id: 'logs', label: 'Logs' },
];

function statusVariant(status: string): BadgeVariant {
  const value = status.toLowerCase();
  if (value === 'success' || value === 'pass') return 'success';
  if (value === 'warn') return 'warning';
  if (value === 'skipped') return 'subtle';
  return 'danger';
}

function statusIcon(status: string) {
  const value = status.toLowerCase();
  const style = 'h-3.5 w-3.5 shrink-0';
  if (value === 'success' || value === 'pass') {
    return <CheckCircle2 className={cn(style, 'text-success')} />;
  }
  if (value === 'warn') return <AlertTriangle className={cn(style, 'text-warning')} />;
  if (value === 'skipped') return <MinusCircle className={cn(style, 'text-text-quaternary')} />;
  return <XCircle className={cn(style, 'text-danger')} />;
}

function formatDuration(seconds: number | null): string {
  if (seconds === null || seconds === undefined) return '—';
  if (seconds < 1) return `${Math.round(seconds * 1000)}ms`;
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
}

function formatBytes(bytes: number | null): string {
  if (bytes === null || bytes === undefined) return '—';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1; }
  return `${value.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
}

interface OutputPanelProps {
  tab: OutputTab;
  onTabChange: (tab: OutputTab) => void;
  invocation: TransformInvocationDetail | null;
  problems: TransformProblem[];
  parseStatus: string;
  compiled: CompiledCode | null;
  logs: TransformLogPage | null;
  onOpenProblem: (problem: TransformProblem) => void;
  onOpenResource: (uniqueId: string) => void;
  loading?: boolean;
}

export function OutputPanel({
  tab,
  onTabChange,
  invocation,
  problems,
  parseStatus,
  compiled,
  logs,
  onOpenProblem,
  onOpenResource,
  loading,
}: OutputPanelProps) {
  const failedCount = problems.filter((item) => item.severity === 'error').length;

  return (
    <div className="flex h-full flex-col bg-surface-1">
      <div className="flex h-8 shrink-0 items-center gap-0.5 border-b border-[rgb(var(--border-line))] px-1">
        {TABS.map((item) => (
          <button
            key={item.id}
            type="button"
            onClick={() => onTabChange(item.id)}
            className={cn(
              'flex h-7 items-center gap-1.5 rounded-sm px-2.5 text-caption transition-colors',
              tab === item.id
                ? 'bg-surface-2 text-text-primary font-emphasis'
                : 'text-text-tertiary hover:text-text-secondary',
            )}
          >
            {item.label}
            {item.id === 'problems' && failedCount > 0 && (
              <Badge variant="danger" size="xs">{failedCount}</Badge>
            )}
            {item.id === 'results' && invocation && invocation.nodes.length > 0 && (
              <Badge
                variant={invocation.nodes_failed > 0 ? 'danger' : 'subtle'}
                size="xs"
              >
                {invocation.nodes.length}
              </Badge>
            )}
          </button>
        ))}

        {invocation && (
          <div className="ml-auto flex items-center gap-2 pr-2 text-tiny text-text-tertiary">
            <span className="font-mono">
              dbt {invocation.command}
              {invocation.selector ? ` --select ${invocation.selector}` : ''}
            </span>
            {invocation.duration_seconds !== null && (
              <span className="flex items-center gap-1">
                <Clock className="h-3 w-3" />
                {formatDuration(invocation.duration_seconds)}
              </span>
            )}
          </div>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        {tab === 'preview' && <PreviewTab preview={invocation?.preview ?? null} loading={loading} />}
        {tab === 'results' && (
          <ResultsTab
            invocation={invocation}
            onOpenResource={onOpenResource}
            loading={loading}
          />
        )}
        {tab === 'problems' && (
          <ProblemsTab
            problems={problems}
            parseStatus={parseStatus}
            onOpen={onOpenProblem}
          />
        )}
        {tab === 'compiled' && <CompiledTab compiled={compiled} />}
        {tab === 'logs' && <LogsTab logs={logs} />}
      </div>
    </div>
  );
}

/** `dbt show` returns rows; column order is the query's own, not alphabetical. */
function PreviewTab({ preview, loading }: { preview: PreviewResult | null; loading?: boolean }) {
  const rows = React.useMemo<Record<string, unknown>[]>(() => {
    if (!preview) return [];
    if (Array.isArray(preview.data)) return preview.data as Record<string, unknown>[];
    // `dbt show --output json` nests under the selected node's name.
    const show = (preview as Record<string, unknown>).show;
    if (Array.isArray(show)) return show as Record<string, unknown>[];
    return [];
  }, [preview]);

  const columns = React.useMemo(() => {
    const seen: string[] = [];
    rows.forEach((row) => Object.keys(row).forEach((key) => {
      if (!seen.includes(key)) seen.push(key);
    }));
    return seen;
  }, [rows]);

  const downloadCsv = () => {
    const escape = (value: unknown) => {
      const text = value === null || value === undefined ? '' : String(value);
      return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
    };
    const csv = [
      columns.join(','),
      ...rows.map((row) => columns.map((column) => escape(row[column])).join(',')),
    ].join('\n');
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = 'preview.csv';
    anchor.click();
    URL.revokeObjectURL(url);
  };

  if (loading) return <Empty>Đang chạy preview…</Empty>;
  if (!preview || rows.length === 0) {
    return <Empty>Chưa có preview. Mở một model rồi bấm Preview.</Empty>;
  }

  return (
    <div>
      <div className="flex items-center justify-between border-b border-[rgb(var(--border-line))] px-3 py-1.5">
        <span className="text-tiny text-text-tertiary">
          {rows.length} dòng · {columns.length} cột
        </span>
        <Button variant="ghost" size="xs" onClick={downloadCsv} leadingIcon={<Download className="h-3 w-3" />}>
          CSV
        </Button>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-caption">
          <thead className="sticky top-0 bg-surface-2">
            <tr>
              {columns.map((column) => (
                <th
                  key={column}
                  className="whitespace-nowrap px-3 py-1.5 text-left font-emphasis text-text-secondary"
                >
                  {column}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr
                key={index}
                className="border-t border-[rgb(var(--border-line))] hover:bg-surface-2"
              >
                {columns.map((column) => (
                  <td
                    key={column}
                    className={cn(
                      'whitespace-nowrap px-3 py-1 font-mono text-tiny',
                      row[column] === null || row[column] === undefined
                        ? 'text-text-quaternary italic'
                        : 'text-text-secondary',
                    )}
                  >
                    {row[column] === null || row[column] === undefined
                      ? 'null'
                      : String(row[column])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ResultsTab({
  invocation, onOpenResource, loading,
}: {
  invocation: TransformInvocationDetail | null;
  onOpenResource: (uniqueId: string) => void;
  loading?: boolean;
}) {
  if (loading && !invocation) return <Empty>Đang chạy…</Empty>;
  if (!invocation || invocation.nodes.length === 0) {
    return <Empty>Chưa có kết quả. Hãy chạy Build hoặc Test.</Empty>;
  }

  return (
    <table className="w-full text-caption">
      <thead className="sticky top-0 bg-surface-2">
        <tr className="text-left text-text-secondary">
          <th className="w-8 px-3 py-1.5" />
          <th className="px-2 py-1.5 font-emphasis">Resource</th>
          <th className="px-2 py-1.5 font-emphasis">Loại</th>
          <th className="px-2 py-1.5 font-emphasis">Thời gian</th>
          <th className="px-2 py-1.5 font-emphasis">Dòng</th>
          <th className="px-2 py-1.5 font-emphasis">Dữ liệu</th>
          <th className="px-2 py-1.5 font-emphasis">Relation</th>
        </tr>
      </thead>
      <tbody>
        {invocation.nodes.map((node: InvocationNode) => (
          <React.Fragment key={node.unique_id}>
            <tr
              className="cursor-pointer border-t border-[rgb(var(--border-line))] hover:bg-surface-2"
              onClick={() => onOpenResource(node.unique_id)}
            >
              <td className="px-3 py-1.5">{statusIcon(node.status)}</td>
              <td className="px-2 py-1.5 text-text-primary">{node.name}</td>
              <td className="px-2 py-1.5">
                <Badge variant="subtle" size="xs">{node.resource_type}</Badge>
              </td>
              <td className="px-2 py-1.5 font-mono text-tiny text-text-tertiary">
                {formatDuration(node.execution_time)}
              </td>
              <td className="px-2 py-1.5 font-mono text-tiny text-text-tertiary">
                {node.rows_affected ?? '—'}
              </td>
              <td className="px-2 py-1.5 font-mono text-tiny text-text-tertiary">
                {formatBytes(node.bytes_processed)}
              </td>
              <td className="truncate px-2 py-1.5 font-mono text-tiny text-text-quaternary">
                {node.relation_name ?? '—'}
              </td>
            </tr>
            {node.message && (
              <tr className="bg-surface-2/50">
                <td />
                <td colSpan={6} className="px-2 pb-2">
                  <p className={cn(
                    'whitespace-pre-wrap font-mono text-tiny',
                    statusVariant(node.status) === 'danger' ? 'text-danger' : 'text-text-tertiary',
                  )}>
                    {node.message}
                  </p>
                </td>
              </tr>
            )}
          </React.Fragment>
        ))}
      </tbody>
    </table>
  );
}

function ProblemsTab({
  problems, parseStatus, onOpen,
}: {
  problems: TransformProblem[];
  parseStatus: string;
  onOpen: (problem: TransformProblem) => void;
}) {
  if (problems.length === 0) {
    return (
      <Empty>
        {parseStatus === 'OK'
          ? 'Không có vấn đề nào. Dự án parse sạch.'
          : 'Chưa có thông tin. Lưu một tệp để dbt parse lại.'}
      </Empty>
    );
  }

  return (
    <div className="divide-y divide-[rgb(var(--border-line))]">
      {problems.map((problem, index) => (
        <button
          key={`${problem.unique_id ?? problem.path ?? index}-${index}`}
          type="button"
          onClick={() => onOpen(problem)}
          className="flex w-full items-start gap-2 px-3 py-2 text-left hover:bg-surface-2"
        >
          {problem.severity === 'error'
            ? <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-danger" />
            : <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warning" />}
          <div className="min-w-0 flex-1">
            <p className="whitespace-pre-wrap text-caption text-text-primary">
              {problem.message}
            </p>
            <p className="mt-0.5 flex items-center gap-1.5 text-tiny text-text-tertiary">
              <Badge variant="subtle" size="xs">{problem.source}</Badge>
              {problem.resource_name && <span>{problem.resource_name}</span>}
              {problem.path && (
                <span className="font-mono">
                  {problem.path}{problem.line ? `:${problem.line}` : ''}
                </span>
              )}
            </p>
          </div>
          {problem.path && (
            <ChevronRight className="mt-0.5 h-3.5 w-3.5 shrink-0 text-text-quaternary" />
          )}
        </button>
      ))}
    </div>
  );
}

function CompiledTab({ compiled }: { compiled: CompiledCode | null }) {
  if (!compiled?.compiled_code) {
    return (
      <Empty>
        Chưa có SQL đã biên dịch. Mở một model rồi bấm Compile.
      </Empty>
    );
  }
  return (
    <div>
      <p className="border-b border-[rgb(var(--border-line))] px-3 py-1.5 text-tiny text-text-tertiary">
        Đây là câu SQL dbt gửi tới kho dữ liệu — Jinja đã được thay thế hết.
      </p>
      <pre className="overflow-x-auto p-3 font-mono text-tiny leading-relaxed text-text-secondary">
        {compiled.compiled_code}
      </pre>
    </div>
  );
}

function LogsTab({ logs }: { logs: TransformLogPage | null }) {
  const bottom = React.useRef<HTMLDivElement>(null);
  const [follow, setFollow] = React.useState(true);

  React.useEffect(() => {
    if (follow) bottom.current?.scrollIntoView({ block: 'end' });
  }, [logs, follow]);

  if (!logs || logs.lines.length === 0) {
    return <Empty>Chưa có log. Log xuất hiện ngay khi dbt bắt đầu chạy.</Empty>;
  }

  return (
    <div className="relative">
      <div className="sticky top-0 z-10 flex items-center justify-between border-b border-[rgb(var(--border-line))] bg-surface-1 px-3 py-1.5">
        <span className="flex items-center gap-1.5 text-tiny text-text-tertiary">
          <ScrollText className="h-3 w-3" />
          {logs.total_lines} dòng
        </span>
        <label className="flex items-center gap-1.5 text-tiny text-text-tertiary">
          <input
            type="checkbox"
            checked={follow}
            onChange={(event) => setFollow(event.target.checked)}
            className="h-3 w-3 accent-[rgb(var(--brand))]"
          />
          Theo dõi
        </label>
      </div>
      <pre className="whitespace-pre-wrap p-3 font-mono text-tiny leading-relaxed text-text-secondary">
        {logs.lines.join('\n')}
      </pre>
      <div ref={bottom} />
    </div>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return (
    <p className="px-4 py-8 text-center text-caption text-text-tertiary">{children}</p>
  );
}
