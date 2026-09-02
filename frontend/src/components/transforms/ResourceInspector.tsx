'use client';

/**
 * The Config drawer for one resource.
 *
 * Two rules shape this whole component:
 *
 * 1. Everything shown is read from the parsed dbt resource, never from a
 *    product row. If dbt did not report it, it is not here.
 *
 * 2. Configs the product has no form for are still *shown*, marked read-only.
 *    A form that cannot faithfully rewrite a value must not offer to -- silently
 *    dropping a `contract` or a package option on save is the exact data loss
 *    the rework forbids. So the structured section covers what round-trips
 *    safely, and everything else is displayed as it is, with a link to the file
 *    where it can be edited as code.
 */

import * as React from 'react';
import {
  Braces, Columns3, Database, ExternalLink, FileCode, Info, Tag, TestTube, X,
} from 'lucide-react';

import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import type { ResourceDetail } from '@/lib/types';
import { cn } from '@/lib/utils';

/**
 * Config keys the Config tab renders as fields.
 *
 * Deliberately short. Every key here has to be one this product can write back
 * into a YAML file without disturbing anything else; anything more ambitious
 * belongs in the raw view until it can be done safely.
 */
const STRUCTURED_KEYS = [
  'materialized', 'schema', 'alias', 'database', 'tags', 'enabled',
  'unique_key', 'incremental_strategy', 'on_schema_change', 'full_refresh',
  'partition_by', 'cluster_by', 'labels', 'require_partition_filter',
  'partition_expiration_days', 'group', 'access', 'docs', 'persist_docs',
  'severity', 'error_if', 'warn_if', 'store_failures', 'where', 'limit',
  'meta', 'grants', 'contract', 'batch_size', 'begin', 'lookback',
  'event_time', 'concurrent_batches', 'snapshot_meta_column_names',
  'target_schema', 'target_database', 'strategy', 'updated_at', 'check_cols',
  'invalidate_hard_deletes', 'dbt_valid_to_current', 'hard_deletes',
  'post-hook', 'pre-hook', 'sql_header', 'quoting', 'column_types',
  'delimiter', 'quote_columns', 'file_format', 'location_root',
];

type InspectorTab = 'overview' | 'config' | 'columns' | 'tests' | 'docs';

interface ResourceInspectorProps {
  resource: ResourceDetail | null;
  loading?: boolean;
  onClose: () => void;
  onOpenFile: (path: string, line?: number) => void;
  onSelectResource: (uniqueId: string) => void;
  /** The Pipeline that loads this source, when AppBI loads it. */
  pipelineName?: string | null;
}

export function ResourceInspector({
  resource, loading, onClose, onOpenFile, onSelectResource, pipelineName,
}: ResourceInspectorProps) {
  const [tab, setTab] = React.useState<InspectorTab>('overview');

  React.useEffect(() => { setTab('overview'); }, [resource?.unique_id]);

  if (loading) {
    return (
      <Drawer onClose={onClose} title="Đang đọc…">
        <p className="p-4 text-caption text-text-tertiary">Đang đọc resource…</p>
      </Drawer>
    );
  }
  if (!resource) return null;

  const structured = Object.entries(resource.config)
    .filter(([key]) => STRUCTURED_KEYS.includes(key))
    .filter(([, value]) => value !== null && value !== undefined && value !== '');
  const unknown = Object.entries(resource.config)
    .filter(([key]) => !STRUCTURED_KEYS.includes(key));

  const tabs: { id: InspectorTab; label: string; count?: number }[] = [
    { id: 'overview', label: 'Overview' },
    { id: 'config', label: 'Config', count: Object.keys(resource.config).length },
    { id: 'columns', label: 'Columns', count: resource.columns.length },
    { id: 'tests', label: 'Tests', count: resource.tests.length },
    { id: 'docs', label: 'Docs' },
  ];

  return (
    <Drawer onClose={onClose} title={resource.name} subtitle={resource.resource_type}>
      <div className="flex shrink-0 gap-0.5 border-b border-[rgb(var(--border-line))] px-2">
        {tabs.map((item) => (
          <button
            key={item.id}
            type="button"
            onClick={() => setTab(item.id)}
            className={cn(
              'flex items-center gap-1 border-b-2 px-2 py-1.5 text-caption transition-colors',
              tab === item.id
                ? 'border-brand text-text-primary font-emphasis'
                : 'border-transparent text-text-tertiary hover:text-text-secondary',
            )}
          >
            {item.label}
            {item.count !== undefined && item.count > 0 && (
              <span className="text-tiny text-text-quaternary">{item.count}</span>
            )}
          </button>
        ))}
      </div>

      <div className="min-h-0 flex-1 overflow-auto p-3">
        {tab === 'overview' && (
          <div className="space-y-3">
            <Field label="Unique ID" mono>{resource.unique_id}</Field>
            {resource.path && (
              <Field label="Path">
                <button
                  type="button"
                  onClick={() => onOpenFile(resource.path!)}
                  className="flex items-center gap-1 font-mono text-caption text-brand hover:underline"
                >
                  <FileCode className="h-3 w-3" />
                  {resource.path}
                </button>
              </Field>
            )}
            {resource.patch_path && resource.patch_path !== resource.path && (
              <Field label="YAML">
                <button
                  type="button"
                  onClick={() => onOpenFile(resource.patch_path!)}
                  className="flex items-center gap-1 font-mono text-caption text-brand hover:underline"
                >
                  <FileCode className="h-3 w-3" />
                  {resource.patch_path}
                </button>
              </Field>
            )}
            {resource.materialized && (
              <Field label="Materialization">
                <div>
                  <Badge variant="brand" size="sm">{resource.materialized}</Badge>
                  <p className="mt-1 text-tiny text-text-tertiary">
                    Cách dbt tạo resource này trong kho dữ liệu.
                  </p>
                </div>
              </Field>
            )}
            {resource.relation_name && (
              <Field label="Relation" mono>{resource.relation_name}</Field>
            )}
            {resource.package_name && (
              <Field label="Package">{resource.package_name}</Field>
            )}
            {resource.group && <Field label="Group">{resource.group}</Field>}
            {resource.tags.length > 0 && (
              <Field label="Tags">
                <div className="flex flex-wrap gap-1">
                  {resource.tags.map((tag) => (
                    <Badge key={tag} variant="subtle" size="xs">
                      <Tag className="h-2.5 w-2.5" />{tag}
                    </Badge>
                  ))}
                </div>
              </Field>
            )}
            {!resource.enabled && (
              <div className="rounded-md bg-warning/10 p-2 text-caption text-warning">
                Resource này đang bị tắt (<code>enabled: false</code>), nên dbt
                không build nó.
              </div>
            )}

            {resource.freshness && (
              <Field label="Freshness">
                <div>
                  <Badge
                    variant={
                      resource.freshness.status === 'PASS' ? 'success'
                        : resource.freshness.status === 'WARN' ? 'warning' : 'danger'
                    }
                    size="sm"
                  >
                    {resource.freshness.status}
                  </Badge>
                  {resource.freshness.age_seconds !== null && (
                    <span className="ml-2 text-tiny text-text-tertiary">
                      dữ liệu mới nhất cách đây{' '}
                      {Math.round(resource.freshness.age_seconds / 60)} phút
                    </span>
                  )}
                  {resource.freshness.message && (
                    <p className="mt-1 text-tiny text-danger">{resource.freshness.message}</p>
                  )}
                </div>
              </Field>
            )}

            {/* AppBI's own contribution. dbt cannot know this, and it is the
                integration value the blueprint asks for -- without changing what
                the source means to dbt. */}
            {pipelineName && (
              <Field label="Nguồn dữ liệu">
                <div className="flex items-center gap-1.5 rounded-md bg-success/10 px-2 py-1.5">
                  <Database className="h-3.5 w-3.5 text-success" />
                  <span className="text-caption text-text-secondary">
                    Do Pipeline <strong className="font-emphasis">{pipelineName}</strong> nạp
                  </span>
                </div>
              </Field>
            )}

            {resource.warehouse && (
              <Field label="Trong kho dữ liệu">
                <dl className="space-y-0.5 text-caption">
                  {resource.warehouse.type && (
                    <Pair label="Kiểu" value={resource.warehouse.type} />
                  )}
                  {Object.entries(resource.warehouse.stats).map(([key, stat]) => (
                    <Pair key={key} label={stat.label} value={String(stat.value)} />
                  ))}
                </dl>
              </Field>
            )}

            {resource.last_result && (
              <Field label="Lần chạy gần nhất">
                <div className="flex items-center gap-2">
                  <Badge
                    variant={
                      ['success', 'pass'].includes(resource.last_result.status.toLowerCase())
                        ? 'success' : 'danger'
                    }
                    size="sm"
                  >
                    {resource.last_result.status}
                  </Badge>
                  {resource.last_result.execution_time !== null && (
                    <span className="text-tiny text-text-tertiary">
                      {resource.last_result.execution_time.toFixed(1)}s
                    </span>
                  )}
                  {resource.last_result.rows_affected !== null && (
                    <span className="text-tiny text-text-tertiary">
                      {resource.last_result.rows_affected} dòng
                    </span>
                  )}
                </div>
              </Field>
            )}

            {(resource.parents.length > 0 || resource.children.length > 0) && (
              <div className="grid grid-cols-2 gap-3">
                <Field label={`Phụ thuộc (${resource.parents.length})`}>
                  <RelatedList ids={resource.parents} onSelect={onSelectResource} />
                </Field>
                <Field label={`Được dùng bởi (${resource.children.length})`}>
                  <RelatedList ids={resource.children} onSelect={onSelectResource} />
                </Field>
              </div>
            )}
          </div>
        )}

        {tab === 'config' && (
          <div className="space-y-3">
            {structured.length > 0 && (
              <div>
                <p className="mb-1.5 text-tiny uppercase tracking-wide text-text-quaternary">
                  Cấu hình
                </p>
                <dl className="space-y-1">
                  {structured.map(([key, value]) => (
                    <Pair key={key} label={key} value={renderValue(value)} mono />
                  ))}
                </dl>
              </div>
            )}

            {unknown.length > 0 && (
              <div>
                <p className="mb-1.5 flex items-center gap-1 text-tiny uppercase tracking-wide text-text-quaternary">
                  <Braces className="h-3 w-3" />
                  Cấu hình khác
                </p>
                {/* Displayed, not hidden. This is the round-trip promise made
                    visible: AppBI does not understand these keys, and it also
                    does not touch them. */}
                <p className="mb-1.5 text-tiny text-text-tertiary">
                  Giao diện chưa có form cho những mục này, nhưng chúng vẫn được
                  giữ nguyên. Sửa trực tiếp trong tệp nếu cần.
                </p>
                <dl className="space-y-1 rounded-md bg-surface-2 p-2">
                  {unknown.map(([key, value]) => (
                    <Pair key={key} label={key} value={renderValue(value)} mono />
                  ))}
                </dl>
              </div>
            )}

            {resource.path && (
              <Button
                variant="secondary" size="sm" fullWidth
                onClick={() => onOpenFile(resource.path!)}
                leadingIcon={<ExternalLink className="h-3.5 w-3.5" />}
              >
                Sửa trong tệp
              </Button>
            )}
          </div>
        )}

        {tab === 'columns' && (
          resource.columns.length === 0 ? (
            <p className="text-caption text-text-tertiary">
              Chưa có cột nào. Kiểu dữ liệu thật xuất hiện sau khi chạy
              <span className="font-mono"> docs generate</span>.
            </p>
          ) : (
            <table className="w-full text-caption">
              <thead>
                <tr className="text-left text-text-tertiary">
                  <th className="pb-1 font-emphasis">Cột</th>
                  <th className="pb-1 font-emphasis">Kiểu</th>
                  <th className="pb-1 font-emphasis">Mô tả</th>
                </tr>
              </thead>
              <tbody>
                {resource.columns.map((column) => (
                  <tr key={column.name} className="border-t border-[rgb(var(--border-line))]">
                    <td className="py-1 font-mono text-tiny text-text-primary">
                      {column.name}
                      {/* Drift, shown rather than smoothed over: the YAML
                          documents a column the warehouse no longer has. */}
                      {!column.in_warehouse && column.documented && (
                        <Badge variant="warning" size="xs" className="ml-1">
                          không còn
                        </Badge>
                      )}
                      {column.in_warehouse && !column.documented && (
                        <Badge variant="subtle" size="xs" className="ml-1">
                          chưa mô tả
                        </Badge>
                      )}
                    </td>
                    <td className="py-1 font-mono text-tiny text-text-tertiary">
                      {column.data_type ?? '—'}
                    </td>
                    <td className="py-1 text-tiny text-text-secondary">
                      {column.description ?? '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )
        )}

        {tab === 'tests' && (
          resource.tests.length === 0 ? (
            <p className="text-caption text-text-tertiary">
              Chưa có test nào cho resource này.
            </p>
          ) : (
            <ul className="space-y-1">
              {resource.tests.map((test) => (
                <li
                  key={test.unique_id}
                  className="flex items-center gap-1.5 rounded-sm bg-surface-2 px-2 py-1.5"
                >
                  <TestTube className="h-3.5 w-3.5 shrink-0 text-warning" />
                  <span className="truncate text-caption text-text-secondary">
                    {test.name}
                  </span>
                  {test.path && (
                    <button
                      type="button"
                      onClick={() => onOpenFile(test.path!)}
                      className="ml-auto shrink-0 text-tiny text-brand hover:underline"
                    >
                      mở
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )
        )}

        {tab === 'docs' && (
          <div className="space-y-2">
            {resource.description ? (
              <p className="whitespace-pre-wrap text-caption text-text-secondary">
                {resource.description}
              </p>
            ) : (
              <p className="flex items-start gap-1.5 text-caption text-text-tertiary">
                <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                Chưa có mô tả. Thêm <code className="font-mono">description</code> vào
                tệp YAML của resource này.
              </p>
            )}
            {resource.patch_path && (
              <Button
                variant="secondary" size="sm"
                onClick={() => onOpenFile(resource.patch_path!)}
                leadingIcon={<Columns3 className="h-3.5 w-3.5" />}
              >
                Sửa tài liệu
              </Button>
            )}
          </div>
        )}
      </div>
    </Drawer>
  );
}

function renderValue(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

function RelatedList({
  ids, onSelect,
}: { ids: string[]; onSelect: (uniqueId: string) => void }) {
  if (ids.length === 0) {
    return <span className="text-tiny text-text-quaternary">Không có</span>;
  }
  return (
    <ul className="space-y-0.5">
      {ids.slice(0, 12).map((id) => (
        <li key={id}>
          <button
            type="button"
            onClick={() => onSelect(id)}
            className="truncate text-left text-caption text-brand hover:underline"
            title={id}
          >
            {id.split('.').pop()}
          </button>
        </li>
      ))}
      {ids.length > 12 && (
        <li className="text-tiny text-text-quaternary">và {ids.length - 12} nữa</li>
      )}
    </ul>
  );
}

function Field({
  label, children, mono,
}: { label: string; children: React.ReactNode; mono?: boolean }) {
  return (
    <div>
      <p className="mb-0.5 text-tiny uppercase tracking-wide text-text-quaternary">{label}</p>
      <div className={cn('text-caption text-text-secondary', mono && 'font-mono break-all')}>
        {children}
      </div>
    </div>
  );
}

function Pair({
  label, value, mono,
}: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex gap-2">
      <dt className="w-40 shrink-0 truncate text-tiny text-text-tertiary" title={label}>
        {label}
      </dt>
      <dd className={cn('min-w-0 flex-1 break-all text-tiny text-text-secondary', mono && 'font-mono')}>
        {value}
      </dd>
    </div>
  );
}

function Drawer({
  title, subtitle, onClose, children,
}: {
  title: string;
  subtitle?: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  return (
    <aside
      className="flex h-full w-full flex-col border-l border-[rgb(var(--border-line))] bg-surface-1"
      aria-label={`Chi tiết ${title}`}
    >
      <div className="flex shrink-0 items-start gap-2 border-b border-[rgb(var(--border-line))] px-3 py-2">
        <div className="min-w-0 flex-1">
          <p className="truncate text-small font-emphasis text-text-primary">{title}</p>
          {subtitle && <p className="text-tiny text-text-tertiary">{subtitle}</p>}
        </div>
        <Button variant="ghost" size="xs" onClick={onClose} aria-label="Đóng">
          <X className="h-3.5 w-3.5" />
        </Button>
      </div>
      {children}
    </aside>
  );
}
