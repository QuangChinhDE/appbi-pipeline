'use client';

import * as React from 'react';
import { Check, Database, Table2 } from 'lucide-react';

import { transformApi } from '@/lib/api';
import { qk } from '@/lib/queryKeys';
import { useQuery } from '@tanstack/react-query';
import { useWorkspaceId } from '@/hooks/use-current-user';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Input, Label, Select } from '@/components/ui/Input';
import { EmptyState, ErrorState, Spinner } from '@/components/ui/Feedback';

export type WarehouseBrowserCopy = {
  dataset: string;
  chooseDataset: string;
  filter: string;
  noTables: string;
  noMatch: string;
  loadFailed: string;
  alreadyAdded: string;
  add: string;
  adding: string;
  selected: string;
  hint: string;
};

/**
 * Pick input tables by looking at the warehouse, not by remembering its layout.
 *
 * A Transform input does not have to come from a Pipeline -- a dataset loaded
 * by any other means is an equally valid source, and the API has always
 * accepted one. What was missing was any way to find it: the only path was a
 * form asking for a dataset and table name typed from memory, which is a
 * feature that exists without being usable.
 */
export function WarehouseBrowser({
  destinationId, copy, onAdd, adding, disabled,
}: {
  destinationId: string;
  copy: WarehouseBrowserCopy;
  /** Called with the chosen relation; the caller registers and attaches it. */
  onAdd: (relation: { schema_name: string; relation_name: string; catalog_name: string | null }) => void;
  adding?: string | null;
  disabled?: boolean;
}) {
  const workspaceId = useWorkspaceId();
  const [schema, setSchema] = React.useState('');
  const [filter, setFilter] = React.useState('');

  const datasets = useQuery({
    queryKey: qk.transformWarehouse(workspaceId, destinationId),
    queryFn: () => transformApi.browseWarehouse(destinationId),
    enabled: Boolean(destinationId),
  });

  const tables = useQuery({
    queryKey: qk.transformWarehouse(workspaceId, destinationId, schema),
    queryFn: () => transformApi.browseWarehouse(destinationId, schema),
    enabled: Boolean(destinationId && schema),
  });

  const catalog = datasets.data?.catalog_name ?? null;
  const needle = filter.trim().toLowerCase();
  const visible = (tables.data?.relations ?? []).filter(
    (item) => !needle || item.relation_name.toLowerCase().includes(needle),
  );

  return (
    <div className="space-y-3">
      <p className="text-caption text-text-tertiary">{copy.hint}</p>
      <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <div>
          <Label required>{copy.dataset}</Label>
          {datasets.isLoading ? (
            <div className="flex h-9 items-center"><Spinner /></div>
          ) : datasets.error ? (
            <p className="text-caption text-danger">{(datasets.error as Error).message}</p>
          ) : (
            <Select value={schema} onChange={(event) => { setSchema(event.target.value); setFilter(''); }}>
              <option value="">{copy.chooseDataset}</option>
              {(datasets.data?.schemas ?? []).map((item) => (
                <option key={item} value={item}>{item}</option>
              ))}
            </Select>
          )}
        </div>
        <div>
          <Label>{copy.filter}</Label>
          <Input size="sm" value={filter} placeholder={copy.filter} disabled={!schema}
            onChange={(event) => setFilter(event.target.value)} />
        </div>
      </div>

      {schema && (
        <div className="overflow-hidden rounded-lg border border-[rgb(var(--border-line))] bg-surface-1">
          {tables.isLoading ? (
            <div className="flex items-center justify-center py-8"><Spinner /></div>
          ) : tables.error ? (
            <div className="p-4">
              <ErrorState title={copy.loadFailed}
                message={(tables.error as Error).message}
                onRetry={() => tables.refetch()} />
            </div>
          ) : visible.length === 0 ? (
            <div className="p-4">
              <EmptyState icon={Table2} title={needle ? copy.noMatch : copy.noTables} />
            </div>
          ) : (
            // Bounded height on purpose: a production dataset can hold hundreds
            // of tables, and a list that pushes the wizard's own buttons off
            // screen is worse than no list.
            <ul className="max-h-72 divide-y divide-[rgb(var(--border-line))] overflow-y-auto">
              {visible.map((item) => {
                const key = `${item.schema_name}.${item.relation_name}`;
                return (
                  <li key={key} className="flex items-center gap-2 px-3 py-2 hover:bg-surface-2">
                    <Table2 className="h-3.5 w-3.5 shrink-0 text-text-quaternary" />
                    <span className="min-w-0 flex-1 truncate font-mono text-caption text-text-secondary">
                      {item.relation_name}
                    </span>
                    {item.relation_type === 'VIEW' && (
                      <Badge variant="neutral" size="xs">view</Badge>
                    )}
                    {item.asset_id ? (
                      <span className="flex shrink-0 items-center gap-1 text-tiny text-success">
                        <Check className="h-3.5 w-3.5" />{copy.alreadyAdded}
                      </span>
                    ) : (
                      <Button size="xs" variant="secondary" disabled={disabled}
                        loading={adding === key}
                        onClick={() => onAdd({
                          schema_name: item.schema_name,
                          relation_name: item.relation_name,
                          catalog_name: catalog,
                        })}>
                        {copy.add}
                      </Button>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
      {!schema && !datasets.isLoading && (
        <div className="rounded-lg border border-dashed border-[rgb(var(--border-line))] px-4 py-6 text-center">
          <Database className="mx-auto h-5 w-5 text-text-quaternary" />
          <p className="mt-1.5 text-caption text-text-tertiary">{copy.chooseDataset}</p>
        </div>
      )}
    </div>
  );
}
