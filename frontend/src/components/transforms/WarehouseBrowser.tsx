'use client';

import * as React from 'react';
import { useQuery } from '@tanstack/react-query';
import { Check, Database, Table2 } from 'lucide-react';

import { transformApi } from '@/lib/api';
import { qk } from '@/lib/queryKeys';
import { cn } from '@/lib/utils';
import { useWorkspaceId } from '@/hooks/use-current-user';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Checkbox, Input, Label, Select } from '@/components/ui/Input';
import { EmptyState, ErrorState, Spinner } from '@/components/ui/Feedback';

export type WarehouseBrowserCopy = {
  project: string;
  dataset: string;
  chooseDataset: string;
  filter: string;
  noTables: string;
  noMatch: string;
  loadFailed: string;
  alreadyAdded: string;
  fromPipeline: string;
  addSelected: string;
  selectedCount: string;
  nothingSelected: string;
};

/**
 * Choose input tables by looking at the warehouse, an account at a time.
 *
 * Three levels because BigQuery has three, and a real service account can see
 * several projects: the account decides what is visible, not the Destination.
 * That matters because a Destination's credential exists so a Pipeline can
 * write, while a Transform often has to read a project the Pipeline never
 * touches -- and widening the ingestion account to allow that is a change to
 * production ingestion made for the sake of a report.
 *
 * Pipelines appear as a label rather than a separate list. A table either
 * exists in the warehouse or it does not; whether AppBI keeps it fresh is a
 * property of that table, not a different way of finding it.
 */
export function WarehouseBrowser({
  connectionId, copy, chosen, onAdd, adding, disabled,
}: {
  /** The connection chosen a step earlier; it decides what is visible here. */
  connectionId: string;
  copy: WarehouseBrowserCopy;
  /** Asset ids already in this Transform's basket. */
  chosen: string[];
  /** Register and select the chosen relations. */
  onAdd: (relations: {
    catalog_name: string | null; schema_name: string; relation_name: string;
  }[]) => void;
  adding?: boolean;
  disabled?: boolean;
}) {
  const workspaceId = useWorkspaceId();
  const [catalog, setCatalog] = React.useState('');
  const [schema, setSchema] = React.useState('');
  const [filter, setFilter] = React.useState('');
  const [picked, setPicked] = React.useState<Set<string>>(new Set());

  const scope = connectionId;

  // A different account sees a different warehouse, so a project and dataset
  // chosen under the old one are not answers to the new one.
  React.useEffect(() => { setCatalog(''); setSchema(''); setPicked(new Set()); }, [scope]);

  const catalogs = useQuery({
    queryKey: qk.transformWarehouse(workspaceId, connectionId, 'catalogs'),
    queryFn: () => transformApi.browseWarehouse(connectionId),
    enabled: Boolean(connectionId),
  });

  // The account's home project is the sensible default; choosing it saves a
  // click that has only one right answer.
  React.useEffect(() => {
    const list = catalogs.data?.catalogs ?? [];
    if (!catalog && list.length) setCatalog(catalogs.data?.catalog_name ?? list[0]);
  }, [catalog, catalogs.data]);

  const schemas = useQuery({
    queryKey: qk.transformWarehouse(workspaceId, connectionId, catalog),
    queryFn: () => transformApi.browseWarehouse(connectionId, { catalog }),
    enabled: Boolean(connectionId && catalog),
  });

  const tables = useQuery({
    queryKey: qk.transformWarehouse(workspaceId, connectionId, `${catalog}|${schema}`),
    queryFn: () => transformApi.browseWarehouse(connectionId, { catalog, schema }),
    enabled: Boolean(connectionId && catalog && schema),
  });

  const needle = filter.trim().toLowerCase();
  const visible = (tables.data?.relations ?? []).filter(
    (item) => !needle || item.relation_name.toLowerCase().includes(needle),
  );

  const toggle = (key: string) => setPicked((current) => {
    const next = new Set(current);
    if (next.has(key)) next.delete(key); else next.add(key);
    return next;
  });

  return (
    <div className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-3">
        <div>
          <Label>{copy.project}</Label>
          {catalogs.isLoading ? (
            <div className="flex h-9 items-center"><Spinner /></div>
          ) : catalogs.error ? (
            <p className="text-caption text-danger">{(catalogs.error as Error).message}</p>
          ) : (
            <Select value={catalog}
              onChange={(event) => {
                setCatalog(event.target.value); setSchema(''); setPicked(new Set());
              }}>
              {(catalogs.data?.catalogs ?? []).map((item) => (
                <option key={item} value={item}>{item}</option>
              ))}
            </Select>
          )}
        </div>
        <div>
          <Label required>{copy.dataset}</Label>
          <Select value={schema} disabled={!catalog || schemas.isLoading}
            onChange={(event) => { setSchema(event.target.value); setPicked(new Set()); }}>
            <option value="">{schemas.isLoading ? '…' : copy.chooseDataset}</option>
            {(schemas.data?.schemas ?? []).map((item) => (
              <option key={item} value={item}>{item}</option>
            ))}
          </Select>
        </div>
        <div>
          <Label>{copy.filter}</Label>
          <Input size="sm" value={filter} disabled={!schema}
            onChange={(event) => setFilter(event.target.value)} />
        </div>
      </div>

      {schema ? (
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
            <>
              {/* Bounded height: a production dataset holds hundreds of tables,
                  and a list that pushes the wizard's own buttons off screen is
                  worse than no list. */}
              <ul className="max-h-72 divide-y divide-[rgb(var(--border-line))] overflow-y-auto">
                {visible.map((item) => {
                  const key = `${item.schema_name}.${item.relation_name}`;
                  // Already in the basket -- not merely registered somewhere.
                  // A table another Transform happens to read is still a table
                  // this one may want, and marking it done locked it out.
                  const added = Boolean(item.asset_id && chosen.includes(item.asset_id));
                  return (
                    <li key={key}
                      className={cn('flex items-center gap-2 px-3 py-2',
                        added ? 'opacity-70' : 'hover:bg-surface-2')}>
                      <Checkbox checked={added || picked.has(key)} disabled={added || disabled}
                        aria-label={item.relation_name}
                        onChange={() => toggle(key)} />
                      <span className="min-w-0 flex-1 truncate font-mono text-caption text-text-secondary">
                        {item.relation_name}
                      </span>
                      {item.pipeline_name && (
                        <span className="shrink-0 truncate text-tiny text-text-tertiary"
                          title={item.pipeline_name}>
                          {copy.fromPipeline} {item.pipeline_name}
                        </span>
                      )}
                      {item.relation_type === 'VIEW' && (
                        <Badge variant="neutral" size="xs">view</Badge>
                      )}
                      {added && (
                        <span className="flex shrink-0 items-center gap-1 text-tiny text-success">
                          <Check className="h-3.5 w-3.5" />{copy.alreadyAdded}
                        </span>
                      )}
                    </li>
                  );
                })}
              </ul>
              <div className="flex items-center justify-between gap-2 border-t border-[rgb(var(--border-line))] px-3 py-2">
                <span className="text-tiny text-text-tertiary">
                  {picked.size
                    ? copy.selectedCount.replace('{n}', String(picked.size))
                    : copy.nothingSelected}
                </span>
                <Button size="xs" variant="primary" loading={adding}
                  disabled={picked.size === 0 || disabled}
                  onClick={() => {
                    onAdd(visible
                      .filter((item) => picked.has(
                        `${item.schema_name}.${item.relation_name}`,
                      ))
                      .map((item) => ({
                        catalog_name: item.catalog_name ?? catalog ?? null,
                        schema_name: item.schema_name,
                        relation_name: item.relation_name,
                      })));
                    setPicked(new Set());
                  }}>
                  {copy.addSelected}
                </Button>
              </div>
            </>
          )}
        </div>
      ) : (
        <div className="rounded-lg border border-dashed border-[rgb(var(--border-line))] px-4 py-6 text-center">
          <Database className="mx-auto h-5 w-5 text-text-quaternary" />
          <p className="mt-1.5 text-caption text-text-tertiary">{copy.chooseDataset}</p>
        </div>
      )}
    </div>
  );
}
