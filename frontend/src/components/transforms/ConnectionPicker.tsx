'use client';

import * as React from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { KeyRound, Plus, Trash2 } from 'lucide-react';

import { transformApi } from '@/lib/api';
import { qk } from '@/lib/queryKeys';
import { cn } from '@/lib/utils';
import { useWorkspaceId } from '@/hooks/use-current-user';
import type { WarehouseConnection } from '@/lib/types';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Input, Label, Select, Textarea } from '@/components/ui/Input';
import { EmptyState, ErrorState, Spinner } from '@/components/ui/Feedback';

export type ConnectionPickerCopy = {
  systemTitle: string;
  systemHelp: string;
  connectionTitle: string;
  connectionHelp: string;
  defaultKey: string;
  noAccount: string;
  projects: string;
  none: string;
  addKey: string;
  newKeyTitle: string;
  keyName: string;
  keyNamePlaceholder: string;
  authMethod: string;
  authLabel: Record<string, string>;
  project: string;
  location: string;
  credentials: string;
  credentialsHint: string;
  host: string;
  port: string;
  database: string;
  username: string;
  password: string;
  oauthHint: string;
  oauthStart: string;
  oauthDone: string;
  save: string;
  cancel: string;
  remove: string;
  loadFailed: string;
};

/**
 * Which system, then which connection to it.
 *
 * Two questions in the order a person asks them. The system comes from the
 * engine lock -- a warehouse nobody has certified an adapter for cannot be
 * offered -- and it decides everything below: which connections are relevant,
 * and what a new one needs. BigQuery takes a service account or a Google
 * sign-in; a database takes a host and a password.
 */
export function ConnectionPicker({
  copy, value, onChange, disabled,
}: {
  copy: ConnectionPickerCopy;
  value: string | null;
  onChange: (connectionId: string | null) => void;
  disabled?: boolean;
}) {
  const workspaceId = useWorkspaceId();
  const queryClient = useQueryClient();
  const [system, setSystem] = React.useState('');
  const [adding, setAdding] = React.useState(false);

  const systems = useQuery({
    queryKey: qk.transformSystems(workspaceId), queryFn: transformApi.systems,
  });
  const connections = useQuery({
    queryKey: qk.transformConnections(workspaceId), queryFn: transformApi.connections,
  });

  // One certified system is not a choice worth making somebody click through.
  React.useEffect(() => {
    const list = systems.data ?? [];
    if (!system && list.length) setSystem(list[0].connector_key);
  }, [system, systems.data]);

  const chosenSystem = (systems.data ?? []).find((item) => item.connector_key === system);
  const rows = (connections.data ?? []).filter((item) => item.connector_key === system);

  const remove = useMutation({
    mutationFn: (id: string) => transformApi.removeConnection(id),
    onSuccess: async (_result, id) => {
      if (value === id) onChange(null);
      await queryClient.invalidateQueries({
        queryKey: qk.transformConnections(workspaceId),
      });
    },
  });

  return (
    <div className="space-y-5">
      <section>
        <h2 className="text-small font-strong text-text-primary">{copy.systemTitle}</h2>
        <p className="mt-1 text-caption text-text-tertiary">{copy.systemHelp}</p>
        {systems.isLoading ? (
          <div className="mt-3 flex justify-center py-4"><Spinner /></div>
        ) : (
          <div className="mt-3 flex flex-wrap gap-2">
            {(systems.data ?? []).map((item) => (
              <button key={item.connector_key} type="button" disabled={disabled}
                onClick={() => { setSystem(item.connector_key); onChange(null); setAdding(false); }}
                className={cn(
                  'rounded-lg border px-4 py-2 text-caption transition-colors',
                  system === item.connector_key
                    ? 'border-brand bg-brand/[0.06]'
                    : 'border-[rgb(var(--border-line))] hover:border-[rgb(var(--border-strong))]',
                )}>
                <span className="block text-caption font-emphasis text-text-primary">
                  {item.label}
                </span>
              </button>
            ))}
          </div>
        )}
      </section>

      <section>
        <h2 className="text-small font-strong text-text-primary">{copy.connectionTitle}</h2>
        <p className="mt-1 text-caption text-text-tertiary">{copy.connectionHelp}</p>

        {connections.isLoading ? (
          <div className="mt-3 flex justify-center py-6"><Spinner /></div>
        ) : connections.error ? (
          <div className="mt-3">
            <ErrorState title={copy.loadFailed} message={(connections.error as Error).message}
              onRetry={() => connections.refetch()} />
          </div>
        ) : rows.length === 0 ? (
          <div className="mt-3"><EmptyState title={copy.none} compact /></div>
        ) : (
          <div className="mt-3 divide-y divide-[rgb(var(--border-line))] overflow-hidden rounded-lg border border-[rgb(var(--border-line))] bg-surface-1">
            {rows.map((row) => (
              <div key={row.id}
                className={cn('flex items-center gap-3 px-4 py-3 transition-colors',
                  value === row.id ? 'bg-brand/[0.06]' : 'hover:bg-surface-2')}>
                <button type="button" disabled={disabled} onClick={() => onChange(row.id)}
                  className="flex min-w-0 flex-1 items-center gap-3 text-left">
                  <span className={cn(
                    'flex h-4 w-4 shrink-0 items-center justify-center rounded-full border',
                    value === row.id ? 'border-brand' : 'border-[rgb(var(--border-strong))]',
                  )}>
                    {value === row.id && <span className="h-2 w-2 rounded-full bg-brand" />}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="flex flex-wrap items-center gap-2">
                      <span className="text-caption font-emphasis text-text-primary">
                        {row.name}
                      </span>
                      {row.is_default && (
                        <Badge variant="subtle" size="xs">{copy.defaultKey}</Badge>
                      )}
                      {!row.is_default && (
                        <Badge variant="neutral" size="xs">
                          {copy.authLabel[row.auth_method] ?? row.auth_method}
                        </Badge>
                      )}
                    </span>
                    <span className="mt-0.5 block truncate font-mono text-tiny text-text-tertiary">
                      {row.account ?? copy.noAccount}
                      {row.catalogs.length > 1
                        ? ` · ${row.catalogs.length} ${copy.projects}` : ''}
                    </span>
                  </span>
                </button>
                {!row.is_default && !disabled && (
                  <Button size="xs" variant="ghost"
                    loading={remove.isPending && remove.variables === row.id}
                    aria-label={copy.remove} title={copy.remove}
                    onClick={() => remove.mutate(row.id)}>
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                )}
              </div>
            ))}
          </div>
        )}

        {remove.error ? (
          <p className="mt-2 text-caption text-danger">{(remove.error as Error).message}</p>
        ) : null}

        {!adding ? (
          <Button size="sm" variant="secondary" className="mt-3"
            disabled={disabled || !chosenSystem}
            leadingIcon={<Plus className="h-4 w-4" />}
            onClick={() => setAdding(true)}>{copy.addKey}</Button>
        ) : chosenSystem ? (
          <NewConnectionForm
            copy={copy} system={chosenSystem}
            onCancel={() => setAdding(false)}
            onCreated={(row) => { setAdding(false); onChange(row.id); }} />
        ) : null}
      </section>
    </div>
  );
}

function NewConnectionForm({
  copy, system, onCancel, onCreated,
}: {
  copy: ConnectionPickerCopy;
  system: import('@/lib/types').TransformSystem;
  onCancel: () => void;
  onCreated: (row: WarehouseConnection) => void;
}) {
  const workspaceId = useWorkspaceId();
  const queryClient = useQueryClient();
  const [method, setMethod] = React.useState(system.auth_methods[0] ?? 'service_account');
  const [name, setName] = React.useState('');
  const [form, setForm] = React.useState({
    project_id: '', dataset_location: '', credentials_json: '',
    host: '', port: '5432', database: '', username: '', password: '',
  });
  const [grant, setGrant] = React.useState<{ id: string; account: string } | null>(null);

  const set = (key: keyof typeof form) => (
    event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>,
  ) => setForm((current) => ({ ...current, [key]: event.target.value }));

  // The consent window writes the grant handle back onto this page's URL, which
  // is the only thing about the sign-in that ever touches the browser.
  React.useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const id = params.get('oauth_grant');
    if (!id) return;
    transformApi.oauthGrant(id)
      .then((row) => setGrant({ id: row.id, account: row.account_label }))
      .catch(() => undefined);
    params.delete('oauth_grant'); params.delete('connector');
    const rest = params.toString();
    window.history.replaceState({}, '', window.location.pathname + (rest ? `?${rest}` : ''));
  }, []);

  const startOauth = useMutation({
    mutationFn: () => transformApi.startOauth(system.connector_key),
    onSuccess: (result) => { window.location.href = result.authorize_url; },
  });

  const create = useMutation({
    mutationFn: () => transformApi.createConnection({
      connector_key: system.connector_key,
      name: name.trim(),
      auth_method: method,
      ...(system.connector_key === 'destination-bigquery'
        ? {
          project_id: form.project_id.trim() || undefined,
          dataset_location: form.dataset_location.trim() || undefined,
          ...(method === 'oauth'
            ? { oauth_grant_id: grant?.id }
            : { credentials_json: form.credentials_json.trim() }),
        }
        : {
          host: form.host.trim(),
          port: Number(form.port) || undefined,
          database: form.database.trim(),
          username: form.username.trim(),
          password: form.password,
        }),
    }),
    onSuccess: async (row) => {
      await queryClient.invalidateQueries({
        queryKey: qk.transformConnections(workspaceId),
      });
      onCreated(row);
    },
  });

  const isBigQuery = system.connector_key === 'destination-bigquery';
  const ready = Boolean(name.trim()) && (
    isBigQuery
      ? (method === 'oauth'
        ? Boolean(grant && form.project_id.trim())
        : form.credentials_json.trim().length > 20)
      : Boolean(form.host.trim() && form.database.trim() && form.username.trim())
  );

  return (
    <div className="mt-3 rounded-lg border border-[rgb(var(--border-line))] bg-surface-1 p-4">
      <h3 className="text-caption font-emphasis text-text-primary">{copy.newKeyTitle}</h3>

      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <div>
          <Label required>{copy.keyName}</Label>
          <Input autoFocus value={name} placeholder={copy.keyNamePlaceholder}
            onChange={(event) => setName(event.target.value)} />
        </div>
        {system.auth_methods.length > 1 && (
          <div>
            <Label>{copy.authMethod}</Label>
            <Select value={method} onChange={(event) => setMethod(event.target.value)}>
              {system.auth_methods.map((item) => (
                <option key={item} value={item}>{copy.authLabel[item] ?? item}</option>
              ))}
            </Select>
          </div>
        )}
      </div>

      {isBigQuery ? (
        <div className="mt-3 space-y-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <Label required={method === 'oauth'}>{copy.project}</Label>
              <Input value={form.project_id} placeholder="my-gcp-project"
                onChange={set('project_id')} />
            </div>
            <div>
              <Label>{copy.location}</Label>
              <Input value={form.dataset_location} placeholder="US"
                onChange={set('dataset_location')} />
            </div>
          </div>
          {method === 'oauth' ? (
            <div className="rounded-md border border-[rgb(var(--border-line))] px-3 py-2.5">
              <p className="text-caption text-text-secondary">{copy.oauthHint}</p>
              {grant ? (
                <p className="mt-1.5 font-mono text-tiny text-success">
                  {copy.oauthDone} {grant.account}
                </p>
              ) : (
                <Button size="xs" variant="secondary" className="mt-2"
                  loading={startOauth.isPending}
                  onClick={() => startOauth.mutate()}>{copy.oauthStart}</Button>
              )}
              {startOauth.error ? (
                <p className="mt-1.5 text-caption text-danger">
                  {(startOauth.error as Error).message}
                </p>
              ) : null}
            </div>
          ) : (
            <div>
              <Label required>{copy.credentials}</Label>
              <Textarea rows={5} value={form.credentials_json} spellCheck={false}
                placeholder={'{ "type": "service_account", ... }'}
                onChange={set('credentials_json')} />
              <p className="mt-1 text-tiny text-text-tertiary">{copy.credentialsHint}</p>
            </div>
          )}
        </div>
      ) : (
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <div>
            <Label required>{copy.host}</Label>
            <Input value={form.host} placeholder="10.0.0.4" onChange={set('host')} />
          </div>
          <div>
            <Label>{copy.port}</Label>
            <Input value={form.port} onChange={set('port')} />
          </div>
          <div>
            <Label required>{copy.database}</Label>
            <Input value={form.database} onChange={set('database')} />
          </div>
          <div>
            <Label required>{copy.username}</Label>
            <Input value={form.username} onChange={set('username')} />
          </div>
          <div className="sm:col-span-2">
            <Label required>{copy.password}</Label>
            <Input type="password" value={form.password} autoComplete="off"
              onChange={set('password')} />
            <p className="mt-1 text-tiny text-text-tertiary">{copy.credentialsHint}</p>
          </div>
        </div>
      )}

      {create.error ? (
        <p className="mt-2 text-caption text-danger">{(create.error as Error).message}</p>
      ) : null}
      <div className="mt-3 flex justify-end gap-2">
        <Button size="sm" variant="ghost" onClick={onCancel}>{copy.cancel}</Button>
        <Button size="sm" variant="primary" loading={create.isPending} disabled={!ready}
          leadingIcon={<KeyRound className="h-4 w-4" />}
          onClick={() => create.mutate()}>{copy.save}</Button>
      </div>
    </div>
  );
}
