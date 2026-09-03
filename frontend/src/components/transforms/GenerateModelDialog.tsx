'use client';

/**
 * "Tạo model từ bảng" -- the form that writes somebody's first dbt model.
 *
 * The hard part of a dbt project is not running it, it is writing the first
 * file: a staging model is a `select` over a `source()`, the source has to be
 * declared in YAML before `ref` resolves, and the tests live somewhere else
 * again. Someone who knows their own data should not have to learn that filing
 * system before they can say "this table, these columns".
 *
 * So this asks only what a person knows -- which table, which columns, what to
 * call them, which are unique, which are never empty -- and the server writes
 * ordinary dbt into the conventional places. Nothing is hidden afterwards: the
 * generated `.sql` opens in the same editor as every other file, and the next
 * edit is a normal edit.
 */

import * as React from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { Database, Loader2, Table2 } from 'lucide-react';

import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Modal } from '@/components/ui/Modal';
import { transformApi } from '@/lib/api';
import type { GenerateColumn, WarehouseColumn } from '@/lib/types';
import { cn } from '@/lib/utils';

interface GenerateModelDialogProps {
  open: boolean;
  onClose: () => void;
  projectId: string;
  connectionId: string;
  /** Where the project says its raw data lives, so the schema starts correct. */
  defaultSchema?: string | null;
  revisionId?: string | null;
  onGenerated: (result: { savedPaths: string[]; parseId: string | null }) => void;
}

/** `customers` -> `stg_customers`, the dbt convention for a staging model. */
function suggestModelName(table: string): string {
  const slug = table.toLowerCase().replace(/[^a-z0-9_]+/g, '_').replace(/^_+|_+$/g, '');
  return slug.startsWith('stg_') ? slug : `stg_${slug}`;
}

export function GenerateModelDialog({
  open, onClose, projectId, connectionId, defaultSchema, revisionId, onGenerated,
}: GenerateModelDialogProps) {
  const [schema, setSchema] = React.useState<string | null>(defaultSchema ?? null);
  const [table, setTable] = React.useState<string | null>(null);
  const [modelName, setModelName] = React.useState('');
  const [materialized, setMaterialized] = React.useState<'view' | 'table'>('view');
  const [columns, setColumns] = React.useState<GenerateColumn[]>([]);
  const [error, setError] = React.useState<string | null>(null);

  // Reopening should not show the previous run's answers.
  React.useEffect(() => {
    if (!open) return;
    setSchema(defaultSchema ?? null);
    setTable(null);
    setModelName('');
    setMaterialized('view');
    setColumns([]);
    setError(null);
  }, [open, defaultSchema]);

  const schemas = useQuery({
    queryKey: ['warehouse', connectionId],
    queryFn: () => transformApi.browseWarehouse(connectionId),
    enabled: open,
  });

  const tables = useQuery({
    queryKey: ['warehouse', connectionId, schema],
    queryFn: () => transformApi.browseWarehouse(connectionId, { schema: schema! }),
    enabled: open && Boolean(schema),
  });

  const columnQuery = useQuery({
    queryKey: ['warehouse', connectionId, schema, table],
    queryFn: () => transformApi.browseWarehouseColumns(connectionId, {
      schema: schema!, table: table!,
    }),
    enabled: open && Boolean(schema && table),
  });

  // Everything selected, nothing renamed: the common case is "all of it", and
  // unticking what you do not want is faster than ticking what you do.
  React.useEffect(() => {
    const fetched = columnQuery.data?.columns;
    if (!fetched) return;
    setColumns(fetched.map((column: WarehouseColumn) => ({
      name: column.name,
      alias: '',
      selected: true,
      unique: false,
      // The warehouse already knows which columns cannot be null. Proposing a
      // test that matches the constraint costs nothing and catches drift later.
      not_null: !column.nullable,
    })));
  }, [columnQuery.data]);

  const generate = useMutation({
    mutationFn: () => transformApi.generateModel(projectId, {
      source_name: schema!,
      schema_name: schema!,
      table_name: table!,
      model_name: modelName,
      materialized,
      columns,
      expected_revision_id: revisionId ?? null,
    }),
  });

  const chosen = columns.filter((column) => column.selected);
  const ready = Boolean(schema && table && modelName && chosen.length > 0);

  const setColumn = (name: string, patch: Partial<GenerateColumn>) => {
    setColumns((current) => current.map(
      (column) => (column.name === name ? { ...column, ...patch } : column),
    ));
  };

  const allSelected = columns.length > 0 && columns.every((column) => column.selected);

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Tạo model từ bảng"
      description="Chọn một bảng trong kho dữ liệu. AppBI viết ra tệp dbt tương ứng, và bạn sửa lại bất cứ lúc nào."
      size="lg"
      footer={(
        <div className="flex items-center justify-between gap-3">
          <p className="min-w-0 truncate text-tiny text-text-tertiary">
            {ready
              ? `Sẽ tạo models/staging/${modelName}.sql với ${chosen.length} cột`
              : 'Chọn bảng và ít nhất một cột'}
          </p>
          <div className="flex shrink-0 gap-2">
            <Button variant="secondary" onClick={onClose}>Huỷ</Button>
            <Button
              disabled={!ready || generate.isPending}
              onClick={() => {
                setError(null);
                generate.mutate(undefined, {
                  onSuccess: (result) => {
                    onGenerated({
                      savedPaths: result.saved_paths,
                      parseId: result.parse_invocation_id ?? null,
                    });
                    onClose();
                  },
                  onError: (cause: unknown) => setError(
                    cause instanceof Error ? cause.message : 'Không tạo được model.',
                  ),
                });
              }}
            >
              {generate.isPending ? 'Đang tạo…' : 'Tạo model'}
            </Button>
          </div>
        </div>
      )}
    >
      <div className="space-y-4">
        {error && (
          <p className="rounded-sm bg-danger/10 px-3 py-2 text-caption text-danger">
            {error}
          </p>
        )}

        <div className="grid grid-cols-2 gap-3">
          <Picker
            label="Schema"
            icon={<Database className="h-3.5 w-3.5" />}
            loading={schemas.isLoading}
            value={schema}
            options={schemas.data?.schemas ?? []}
            onChange={(next) => { setSchema(next); setTable(null); setColumns([]); }}
          />
          <Picker
            label="Bảng"
            icon={<Table2 className="h-3.5 w-3.5" />}
            loading={tables.isFetching}
            value={table}
            options={(tables.data?.relations ?? []).map((item) => item.name)}
            disabled={!schema}
            onChange={(next) => {
              setTable(next);
              setModelName(suggestModelName(next));
            }}
          />
        </div>

        <div className="grid grid-cols-2 gap-3">
          <label className="block">
            <span className="mb-1 block text-tiny uppercase tracking-wide text-text-quaternary">
              Tên model
            </span>
            <Input
              value={modelName}
              onChange={(event) => setModelName(event.target.value)}
              placeholder="stg_customers"
              className="font-mono"
            />
          </label>
          <div>
            <span className="mb-1 block text-tiny uppercase tracking-wide text-text-quaternary">
              Cách lưu kết quả
            </span>
            <div className="flex gap-1.5">
              {(['view', 'table'] as const).map((option) => (
                <button
                  key={option}
                  type="button"
                  onClick={() => setMaterialized(option)}
                  className={cn(
                    'flex-1 rounded-sm border px-2 py-1.5 text-caption transition-colors',
                    materialized === option
                      ? 'border-brand bg-brand/10 text-text-primary'
                      : 'border-[rgb(var(--border-line))] text-text-secondary hover:bg-surface-2',
                  )}
                >
                  {option === 'view' ? 'View (luôn mới)' : 'Table (đọc nhanh)'}
                </button>
              ))}
            </div>
          </div>
        </div>

        <div>
          <div className="mb-1 flex items-baseline justify-between">
            <span className="text-tiny uppercase tracking-wide text-text-quaternary">
              Cột {columns.length > 0 && `(${chosen.length}/${columns.length})`}
            </span>
            {columns.length > 0 && (
              <button
                type="button"
                className="text-tiny text-brand hover:underline"
                onClick={() => setColumns(
                  columns.map((column) => ({ ...column, selected: !allSelected })),
                )}
              >
                {allSelected ? 'Bỏ chọn tất cả' : 'Chọn tất cả'}
              </button>
            )}
          </div>

          <div className="max-h-64 overflow-auto rounded-sm border border-[rgb(var(--border-line))]">
            {columnQuery.isFetching ? (
              <p className="flex items-center justify-center gap-2 py-8 text-caption text-text-tertiary">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                Đang đọc cột…
              </p>
            ) : columns.length === 0 ? (
              <p className="py-8 text-center text-caption text-text-tertiary">
                Chọn một bảng để xem các cột.
              </p>
            ) : (
              <table className="w-full text-caption">
                <thead className="sticky top-0 bg-surface-2 text-tiny text-text-tertiary">
                  <tr>
                    <th className="w-8 px-2 py-1.5" />
                    <th className="px-2 py-1.5 text-left font-normal">Cột</th>
                    <th className="px-2 py-1.5 text-left font-normal">Đổi tên thành</th>
                    <th className="w-16 px-2 py-1.5 font-normal" title="Giá trị không trùng nhau">
                      unique
                    </th>
                    <th className="w-16 px-2 py-1.5 font-normal" title="Giá trị không được rỗng">
                      not_null
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {columns.map((column) => (
                    <tr
                      key={column.name}
                      className={cn(
                        'border-t border-[rgb(var(--border-line))]',
                        !column.selected && 'opacity-45',
                      )}
                    >
                      <td className="px-2 py-1">
                        <input
                          type="checkbox"
                          checked={column.selected}
                          onChange={(event) => setColumn(column.name, {
                            selected: event.target.checked,
                          })}
                          aria-label={`Chọn cột ${column.name}`}
                        />
                      </td>
                      <td className="px-2 py-1 font-mono text-text-secondary">
                        {column.name}
                      </td>
                      <td className="px-2 py-1">
                        <input
                          value={column.alias ?? ''}
                          disabled={!column.selected}
                          onChange={(event) => setColumn(column.name, {
                            alias: event.target.value,
                          })}
                          placeholder="giữ nguyên"
                          className={cn(
                            'h-6 w-full rounded-sm bg-surface-2 px-1.5 font-mono text-caption',
                            'text-text-primary placeholder:text-text-quaternary',
                            'focus:outline-none focus:ring-1 focus:ring-brand/40',
                          )}
                        />
                      </td>
                      {(['unique', 'not_null'] as const).map((flag) => (
                        <td key={flag} className="px-2 py-1 text-center">
                          <input
                            type="checkbox"
                            checked={column[flag]}
                            disabled={!column.selected}
                            onChange={(event) => setColumn(column.name, {
                              [flag]: event.target.checked,
                            })}
                            aria-label={`${flag} cho ${column.name}`}
                          />
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      </div>
    </Modal>
  );
}

function Picker({
  label, icon, value, options, onChange, loading, disabled,
}: {
  label: string;
  icon: React.ReactNode;
  value: string | null;
  options: string[];
  onChange: (next: string) => void;
  loading?: boolean;
  disabled?: boolean;
}) {
  return (
    <label className="block">
      <span className="mb-1 flex items-center gap-1 text-tiny uppercase tracking-wide text-text-quaternary">
        {icon}
        {label}
      </span>
      <select
        value={value ?? ''}
        disabled={disabled || loading}
        onChange={(event) => onChange(event.target.value)}
        className={cn(
          'h-8 w-full rounded-sm border border-[rgb(var(--border-line))] bg-surface-1 px-2',
          'text-caption text-text-primary disabled:opacity-50',
          'focus:outline-none focus:ring-1 focus:ring-brand/40',
        )}
      >
        <option value="">{loading ? 'Đang tải…' : `Chọn ${label.toLowerCase()}`}</option>
        {options.map((option) => (
          <option key={option} value={option}>{option}</option>
        ))}
      </select>
    </label>
  );
}
