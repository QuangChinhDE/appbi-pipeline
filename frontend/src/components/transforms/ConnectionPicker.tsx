'use client';

import * as React from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { KeyRound, Plus, Trash2 } from 'lucide-react';

import { transformApi } from '@/lib/api';
import { qk } from '@/lib/queryKeys';
import { cn } from '@/lib/utils';
import { useWorkspaceId } from '@/hooks/use-current-user';
import type { ChosenWarehouse, WarehouseConnection } from '@/lib/types';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Input, Label, Select, Textarea } from '@/components/ui/Input';
import { ErrorState, Spinner } from '@/components/ui/Feedback';

export type ConnectionPickerCopy = {
  title: string;
  help: string;
  defaultKey: string;
  noAccount: string;
  projects: string;
  addKey: string;
  newKeyTitle: string;
  keyName: string;
  keyNamePlaceholder: string;
  keyWarehouse: string;
  credentials: string;
  credentialsHint: string;
  save: string;
  cancel: string;
  remove: string;
  loadFailed: string;
};

/**
 * Which key this Transform reads with -- picked from a list, not typed again.
 *
 * A credential used to be stored anonymously per Transform: no name, not
 * listed, not reusable. So there was never an existing key to choose and every
 * Transform meant finding a service account JSON again. dbt Cloud and Dataform
 * both do the opposite -- a named connection you select -- and so does this.
 *
 * Each Destination contributes the key it already uses, which is the common
 * case and needs nothing entered. Saved keys follow, for data that lives
 * somewhere the Pipeline never touches.
 */
export function ConnectionPicker({
  copy, value, onChange, disabled,
}: {
  copy: ConnectionPickerCopy;
  value: ChosenWarehouse | null;
  onChange: (value: ChosenWarehouse | null) => void;
  disabled?: boolean;
}) {
  const workspaceId = useWorkspaceId();
  const queryClient = useQueryClient();
  const [adding, setAdding] = React.useState(false);

  const list = useQuery({
    queryKey: qk.transformConnections(workspaceId),
    queryFn: transformApi.connections,
  });

  const remove = useMutation({
    mutationFn: (id: string) => transformApi.removeConnection(id),
    onSuccess: async (_result, id) => {
      if (value?.connection_id === id) onChange(null);
      await queryClient.invalidateQueries({
        queryKey: qk.transformConnections(workspaceId),
      });
    },
  });

  const rows = list.data ?? [];
  const selected = (row: WarehouseConnection) =>
    value?.connection_id === row.id && value?.destination_id === row.destination_id;

  return (
    <section>
      <h2 className="text-small font-strong text-text-primary">{copy.title}</h2>
      <p className="mt-1 text-caption text-text-tertiary">{copy.help}</p>

      {list.isLoading ? (
        <div className="mt-3 flex justify-center py-6"><Spinner /></div>
      ) : list.error ? (
        <div className="mt-3">
          <ErrorState title={copy.loadFailed} message={(list.error as Error).message}
            onRetry={() => list.refetch()} />
        </div>
      ) : (
        <div className="mt-3 divide-y divide-[rgb(var(--border-line))] overflow-hidden rounded-lg border border-[rgb(var(--border-line))] bg-surface-1">
          {rows.map((row) => (
            <div key={`${row.destination_id}:${row.id ?? 'default'}`}
              className={cn('flex items-center gap-3 px-4 py-3 transition-colors',
                selected(row) ? 'bg-brand/[0.06]' : 'hover:bg-surface-2')}>
              <button type="button" disabled={disabled}
                onClick={() => onChange({
                  destination_id: row.destination_id, connection_id: row.id,
                  name: row.name, account: row.account,
                })}
                className="flex min-w-0 flex-1 items-center gap-3 text-left">
                <span className={cn(
                  'flex h-4 w-4 shrink-0 items-center justify-center rounded-full border',
                  selected(row)
                    ? 'border-brand'
                    : 'border-[rgb(var(--border-strong))]',
                )}>
                  {selected(row) && <span className="h-2 w-2 rounded-full bg-brand" />}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="flex flex-wrap items-center gap-2">
                    <span className="text-caption font-emphasis text-text-primary">
                      {row.name}
                    </span>
                    {row.is_default
                      ? <Badge variant="subtle" size="xs">{copy.defaultKey}</Badge>
                      : <Badge variant="neutral" size="xs">{row.destination_name}</Badge>}
                  </span>
                  <span className="mt-0.5 block truncate font-mono text-tiny text-text-tertiary">
                    {row.account ?? copy.noAccount}
                    {row.catalogs.length > 1
                      ? ` · ${row.catalogs.length} ${copy.projects}`
                      : ''}
                  </span>
                </span>
              </button>
              {row.id && !disabled && (
                <Button size="xs" variant="ghost"
                  loading={remove.isPending && remove.variables === row.id}
                  aria-label={copy.remove} title={copy.remove}
                  onClick={() => remove.mutate(row.id!)}>
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
        <Button size="sm" variant="secondary" className="mt-3" disabled={disabled}
          leadingIcon={<Plus className="h-4 w-4" />}
          onClick={() => setAdding(true)}>{copy.addKey}</Button>
      ) : (
        <NewKeyForm
          copy={copy} destinations={rows.filter((row) => row.is_default)}
          onCancel={() => setAdding(false)}
          onCreated={(row) => {
            setAdding(false);
            onChange({
              destination_id: row.destination_id, connection_id: row.id,
              name: row.name, account: row.account,
            });
          }} />
      )}
    </section>
  );
}

function NewKeyForm({
  copy, destinations, onCancel, onCreated,
}: {
  copy: ConnectionPickerCopy;
  destinations: WarehouseConnection[];
  onCancel: () => void;
  onCreated: (row: WarehouseConnection) => void;
}) {
  const workspaceId = useWorkspaceId();
  const queryClient = useQueryClient();
  const [destinationId, setDestinationId] = React.useState(
    destinations[0]?.destination_id ?? '',
  );
  const [name, setName] = React.useState('');
  const [credentials, setCredentials] = React.useState('');

  const create = useMutation({
    mutationFn: () => transformApi.createConnection({
      destination_id: destinationId,
      name: name.trim(),
      credentials_json: credentials.trim(),
    }),
    onSuccess: async (row) => {
      await queryClient.invalidateQueries({
        queryKey: qk.transformConnections(workspaceId),
      });
      onCreated(row);
    },
  });

  return (
    <div className="mt-3 rounded-lg border border-[rgb(var(--border-line))] bg-surface-1 p-4">
      <h3 className="text-caption font-emphasis text-text-primary">{copy.newKeyTitle}</h3>
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <div>
          <Label required>{copy.keyName}</Label>
          <Input autoFocus value={name} placeholder={copy.keyNamePlaceholder}
            onChange={(event) => setName(event.target.value)} />
        </div>
        <div>
          <Label required>{copy.keyWarehouse}</Label>
          <Select value={destinationId}
            onChange={(event) => setDestinationId(event.target.value)}>
            {destinations.map((row) => (
              <option key={row.destination_id} value={row.destination_id}>
                {row.destination_name}
              </option>
            ))}
          </Select>
        </div>
      </div>
      <div className="mt-3">
        <Label required>{copy.credentials}</Label>
        <Textarea rows={5} value={credentials} spellCheck={false}
          placeholder={'{ "type": "service_account", ... }'}
          onChange={(event) => setCredentials(event.target.value)} />
        <p className="mt-1 text-tiny text-text-tertiary">{copy.credentialsHint}</p>
      </div>
      {create.error ? (
        <p className="mt-2 text-caption text-danger">{(create.error as Error).message}</p>
      ) : null}
      <div className="mt-3 flex justify-end gap-2">
        <Button size="sm" variant="ghost" onClick={onCancel}>{copy.cancel}</Button>
        <Button size="sm" variant="primary" loading={create.isPending}
          leadingIcon={<KeyRound className="h-4 w-4" />}
          disabled={!name.trim() || credentials.trim().length < 20 || !destinationId}
          onClick={() => create.mutate()}>{copy.save}</Button>
      </div>
    </div>
  );
}
