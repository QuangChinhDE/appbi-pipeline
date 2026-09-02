'use client';

/**
 * Choose a warehouse key, or enter a new one.
 *
 * Two steps, in this order: a kind of system, then a connection to it. The
 * order matters -- somebody creating their first project may have no Destination
 * yet, and a picker that starts from "which Destination" has nothing to show
 * them.
 *
 * A key that is named and listed is a key that gets reused, which is why an
 * existing connection is the first thing offered rather than an empty form.
 */

import * as React from 'react';
import { Check, CircleAlert, KeyRound, Loader2, Plus, RefreshCw } from 'lucide-react';
import { toast } from 'sonner';

import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { ApiError, transformApi } from '@/lib/api';
import type { TransformSystem, WarehouseConnection } from '@/lib/types';
import { cn } from '@/lib/utils';

interface ConnectionPickerProps {
  systems: TransformSystem[];
  connections: WarehouseConnection[];
  value: string | null;
  onChange: (connectionId: string) => void;
  onCreated: () => void;
  disabled?: boolean;
}

export function ConnectionPicker({
  systems, connections, value, onChange, onCreated, disabled,
}: ConnectionPickerProps) {
  const [system, setSystem] = React.useState<string | null>(
    systems.length === 1 ? systems[0].connector_key : null,
  );
  const [creating, setCreating] = React.useState(false);

  const available = React.useMemo(
    () => connections.filter((item) => !system || item.connector_key === system),
    [connections, system],
  );

  return (
    <div className="space-y-3">
      <div>
        <p className="mb-1.5 text-caption font-emphasis text-text-primary">
          Kho dữ liệu
        </p>
        <div className="flex flex-wrap gap-1.5">
          {systems.map((item) => (
            <button
              key={item.connector_key}
              type="button"
              disabled={disabled}
              onClick={() => { setSystem(item.connector_key); setCreating(false); }}
              className={cn(
                'rounded-md border px-3 py-1.5 text-caption transition-colors',
                system === item.connector_key
                  ? 'border-brand bg-brand/10 text-text-primary font-emphasis'
                  : 'border-[rgb(var(--border-line))] text-text-secondary hover:bg-surface-2',
              )}
            >
              {item.label}
            </button>
          ))}
        </div>
      </div>

      {system && !creating && (
        <div>
          <div className="mb-1.5 flex items-center justify-between">
            <p className="text-caption font-emphasis text-text-primary">Kết nối</p>
            <Button
              variant="ghost" size="xs" disabled={disabled}
              onClick={() => setCreating(true)}
              leadingIcon={<Plus className="h-3 w-3" />}
            >
              Kết nối mới
            </Button>
          </div>

          {available.length === 0 ? (
            <p className="rounded-md bg-surface-2 px-3 py-3 text-caption text-text-tertiary">
              Chưa có kết nối nào tới hệ thống này. Hãy tạo một kết nối mới.
            </p>
          ) : (
            <ul className="space-y-1">
              {available.map((connection) => (
                <li key={connection.id}>
                  <button
                    type="button"
                    disabled={disabled}
                    onClick={() => onChange(connection.id)}
                    className={cn(
                      'flex w-full items-center gap-2 rounded-md border px-3 py-2 text-left',
                      value === connection.id
                        ? 'border-brand bg-brand/5'
                        : 'border-[rgb(var(--border-line))] hover:bg-surface-2',
                    )}
                  >
                    <KeyRound className="h-4 w-4 shrink-0 text-text-tertiary" />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-caption font-emphasis text-text-primary">
                        {connection.name}
                      </p>
                      {connection.account && (
                        <p className="truncate text-tiny text-text-tertiary">
                          {connection.account}
                        </p>
                      )}
                    </div>
                    {connection.is_default && (
                      <Badge variant="subtle" size="xs">có sẵn</Badge>
                    )}
                    {connection.verification_status === 'FAILED' && (
                      <CircleAlert
                        className="h-3.5 w-3.5 shrink-0 text-danger"
                        aria-label="Kết nối không dùng được"
                      />
                    )}
                    {value === connection.id && (
                      <Check className="h-4 w-4 shrink-0 text-brand" />
                    )}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {system && creating && (
        <NewConnectionForm
          system={systems.find((item) => item.connector_key === system)!}
          onCancel={() => setCreating(false)}
          onCreated={(connection) => {
            setCreating(false);
            onCreated();
            onChange(connection.id);
          }}
        />
      )}
    </div>
  );
}

function NewConnectionForm({
  system, onCancel, onCreated,
}: {
  system: TransformSystem;
  onCancel: () => void;
  onCreated: (connection: WarehouseConnection) => void;
}) {
  const [method, setMethod] = React.useState(system.auth_methods[0]);
  const [saving, setSaving] = React.useState(false);
  const [fields, setFields] = React.useState<Record<string, string>>({});

  const set = (key: string) => (event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
    setFields((current) => ({ ...current, [key]: event.target.value }));

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setSaving(true);
    try {
      // Checked against the warehouse before it is kept: a connection nobody
      // can use is worse in a list than absent from one, because it looks like
      // a working choice.
      const created = await transformApi.createConnection({
        connector_key: system.connector_key,
        name: fields.name ?? '',
        auth_method: method,
        project_id: fields.project_id,
        dataset_location: fields.dataset_location,
        credentials_json: fields.credentials_json,
        host: fields.host,
        port: fields.port ? Number(fields.port) : undefined,
        database: fields.database,
        username: fields.username,
        password: fields.password,
        ssl_mode: fields.ssl_mode,
      });
      toast.success(`Đã kiểm tra và lưu kết nối “${created.name}”.`);
      onCreated(created);
    } catch (error) {
      toast.error(
        error instanceof ApiError ? error.message : 'Không lưu được kết nối.',
      );
    } finally {
      setSaving(false);
    }
  };

  const isBigQuery = system.connector_key === 'destination-bigquery';

  return (
    <form
      onSubmit={submit}
      className="space-y-2.5 rounded-md border border-[rgb(var(--border-line))] p-3"
    >
      <p className="text-caption font-emphasis text-text-primary">
        Kết nối {system.label} mới
      </p>

      {system.auth_methods.length > 1 && (
        <div className="flex gap-1.5">
          {system.auth_methods.map((item) => (
            <button
              key={item}
              type="button"
              onClick={() => setMethod(item)}
              className={cn(
                'rounded-sm px-2 py-1 text-tiny transition-colors',
                method === item
                  ? 'bg-brand text-text-inverse'
                  : 'bg-surface-2 text-text-secondary hover:bg-surface-3',
              )}
            >
              {item === 'service_account' ? 'Khoá dịch vụ'
                : item === 'oauth' ? 'Đăng nhập Google' : 'Mật khẩu'}
            </button>
          ))}
        </div>
      )}

      <Field label="Tên gợi nhớ">
        <Input
          value={fields.name ?? ''} onChange={set('name')}
          placeholder="Kho phân tích" required size="sm"
        />
      </Field>

      {isBigQuery ? (
        <>
          <Field label="Project ID">
            <Input
              value={fields.project_id ?? ''} onChange={set('project_id')}
              placeholder="my-gcp-project" size="sm"
            />
          </Field>
          <Field label="Vị trí dataset" hint="Ví dụ: asia-southeast1">
            <Input
              value={fields.dataset_location ?? ''} onChange={set('dataset_location')}
              placeholder="US" size="sm"
            />
          </Field>
          {method === 'service_account' && (
            <Field label="Service account JSON">
              <textarea
                value={fields.credentials_json ?? ''}
                onChange={set('credentials_json')}
                rows={4}
                required
                spellCheck={false}
                placeholder='{"type": "service_account", …}'
                className={cn(
                  'w-full rounded-md border border-[rgb(var(--border-line))] bg-surface-0',
                  'px-2 py-1.5 font-mono text-tiny text-text-primary',
                  'placeholder:text-text-quaternary focus:outline-none focus:ring-1 focus:ring-brand/40',
                )}
              />
            </Field>
          )}
        </>
      ) : (
        <>
          <div className="grid grid-cols-3 gap-2">
            <div className="col-span-2">
              <Field label="Host">
                <Input value={fields.host ?? ''} onChange={set('host')} size="sm" required />
              </Field>
            </div>
            <Field label="Port">
              <Input
                value={fields.port ?? ''} onChange={set('port')}
                placeholder="5432" size="sm" inputMode="numeric"
              />
            </Field>
          </div>
          <Field label="Database">
            <Input value={fields.database ?? ''} onChange={set('database')} size="sm" required />
          </Field>
          <div className="grid grid-cols-2 gap-2">
            <Field label="Tài khoản">
              <Input value={fields.username ?? ''} onChange={set('username')} size="sm" required />
            </Field>
            <Field label="Mật khẩu">
              <Input
                type="password" value={fields.password ?? ''} onChange={set('password')}
                size="sm" required
              />
            </Field>
          </div>
        </>
      )}

      <div className="flex justify-end gap-2 pt-1">
        <Button variant="ghost" size="sm" type="button" onClick={onCancel}>
          Huỷ
        </Button>
        <Button
          variant="primary" size="sm" type="submit" loading={saving}
          leadingIcon={saving
            ? <Loader2 className="h-3 w-3 animate-spin" />
            : <RefreshCw className="h-3 w-3" />}
        >
          Kiểm tra &amp; lưu
        </Button>
      </div>
    </form>
  );
}

function Field({
  label, hint, children,
}: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-caption text-text-secondary">{label}</span>
      {children}
      {hint && <span className="mt-0.5 block text-tiny text-text-quaternary">{hint}</span>}
    </label>
  );
}
