'use client';

import * as React from 'react';
import { AlertTriangle, ChevronRight, Info, Search } from 'lucide-react';

import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Checkbox, Input, Select } from '@/components/ui/Input';
import { EmptyState } from '@/components/ui/Feedback';
import type { StreamCapability, StreamSelection } from '@/lib/types';
import { cn } from '@/lib/utils';
import { useI18n } from '@/providers/LanguageProvider';
import { useSyncModeLabel } from './Badges';

export function streamKey(namespace: string | null, name: string): string {
  return `${namespace ?? ''}::${name}`;
}

/**
 * Stream picker + per-stream sync configuration (section 14.2 steps 2-3).
 *
 * Options are capability-driven: a mode the connector or catalog does not
 * support is never offered, and the reason is shown instead.
 */
export function StreamSelector({
  streams, selections, onChange, destinationModes,
}: {
  streams: StreamCapability[];
  selections: Record<string, StreamSelection>;
  onChange: (next: Record<string, StreamSelection>) => void;
  destinationModes: string[];
}) {
  const { t } = useI18n();
  const syncModeLabel = useSyncModeLabel();
  const [query, setQuery] = React.useState('');
  const [onlySelected, setOnlySelected] = React.useState(false);
  const [expanded, setExpanded] = React.useState<string | null>(null);

  const visible = streams.filter((stream) => {
    const key = streamKey(stream.namespace, stream.name);
    if (onlySelected && !selections[key]?.selected) return false;
    if (!query) return true;
    const needle = query.toLowerCase();
    return (
      stream.name.toLowerCase().includes(needle) ||
      (stream.namespace ?? '').toLowerCase().includes(needle)
    );
  });

  const selectedCount = Object.values(selections).filter((s) => s.selected).length;

  const setStream = (key: string, patch: Partial<StreamSelection>) => {
    const current = selections[key];
    if (!current) return;
    onChange({ ...selections, [key]: { ...current, ...patch } });
  };

  const toggleAll = (checked: boolean) => {
    const next = { ...selections };
    for (const stream of visible) {
      const key = streamKey(stream.namespace, stream.name);
      if (next[key]) next[key] = { ...next[key], selected: checked };
    }
    onChange(next);
  };

  const allVisibleSelected =
    visible.length > 0 &&
    visible.every((s) => selections[streamKey(s.namespace, s.name)]?.selected);

  return (
    <div className="space-y-3">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex flex-1 items-center gap-2">
          <div className="max-w-xs flex-1">
            <Input
              size="sm"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder={t('stream.search')}
              aria-label={t('stream.search')}
              leadingIcon={<Search />}
            />
          </div>
          <Checkbox
            checked={onlySelected}
            onChange={setOnlySelected}
            label={t('stream.onlySelected')}
          />
        </div>
        <div className="flex items-center gap-2">
          <span className="text-caption text-text-tertiary">
            {t('stream.selectedOf', { n: selectedCount, total: streams.length })}
          </span>
          <Button size="xs" variant="subtle" onClick={() => toggleAll(!allVisibleSelected)}>
            {allVisibleSelected ? t('stream.deselectAll') : t('stream.selectAll')}
          </Button>
        </div>
      </div>

      {visible.length === 0 ? (
        <EmptyState title={t('stream.noMatch')} compact />
      ) : (
        <div className="overflow-hidden rounded-lg border border-[rgb(var(--border-line))] bg-surface-1">
          {/* Virtualisation is unnecessary below a few hundred streams; the list
              is windowed by the search box above for very large catalogs. */}
          <div className="max-h-[520px] overflow-y-auto">
            <table className="w-full min-w-[820px] text-left">
              <thead className="sticky top-0 z-10 bg-surface-1">
                <tr className="border-b border-[rgb(var(--border-line))] text-tiny uppercase tracking-[0.08em] text-text-quaternary">
                  <th scope="col" className="w-10 px-3 py-2" />
                  <th scope="col" className="px-2 py-2 font-emphasis">{t('stream.colName')}</th>
                  <th scope="col" className="px-2 py-2 font-emphasis">{t('stream.colRead')}</th>
                  <th scope="col" className="px-2 py-2 font-emphasis">{t('stream.colCursor')}</th>
                  <th scope="col" className="px-2 py-2 font-emphasis">{t('stream.colWrite')}</th>
                  <th scope="col" className="px-2 py-2 font-emphasis">{t('stream.colPk')}</th>
                  <th scope="col" className="w-8 px-2 py-2" />
                </tr>
              </thead>
              <tbody className="divide-y divide-[rgb(var(--border-line))]">
                {visible.map((stream) => {
                  const key = streamKey(stream.namespace, stream.name);
                  const selection = selections[key];
                  if (!selection) return null;
                  const supportsIncremental = stream.supported_sync_modes.includes('incremental');
                  const isIncremental = selection.sync_mode === 'incremental';
                  const needsCursor = isIncremental && !stream.source_defined_cursor;
                  const needsPk = selection.destination_sync_mode === 'append_dedup';
                  const open = expanded === key;

                  return (
                    <React.Fragment key={key}>
                      <tr className={cn(
                        'align-top transition-colors',
                        selection.selected ? 'bg-brand-soft/25' : 'hover:bg-surface-2/60',
                      )}>
                        <td className="px-3 py-2">
                          <Checkbox
                            checked={selection.selected}
                            onChange={(checked) => setStream(key, { selected: checked })}
                          />
                        </td>
                        <td className="px-2 py-2">
                          <span className="block text-caption font-emphasis text-text-primary">
                            {stream.name}
                          </span>
                          <span className="text-tiny text-text-quaternary">
                            {stream.namespace ? `${stream.namespace} · ` : ''}
                            {t('stream.fieldCount', { n: stream.fields.length })}
                          </span>
                          {stream.unsupported_reason && (
                            <span className="mt-0.5 flex items-center gap-1 text-tiny text-warning">
                              <Info className="h-3 w-3" />
                              {stream.unsupported_reason}
                            </span>
                          )}
                        </td>
                        <td className="px-2 py-2">
                          <Select
                            size="sm"
                            className="w-36"
                            disabled={!selection.selected}
                            value={selection.sync_mode}
                            aria-label={t('stream.readLabel', { name: stream.name })}
                            onChange={(event) => {
                              const mode = event.target.value as StreamSelection['sync_mode'];
                              setStream(key, {
                                sync_mode: mode,
                                cursor_fields: mode === 'incremental'
                                  ? (selection.cursor_fields.length
                                      ? selection.cursor_fields
                                      : stream.default_cursor_field)
                                  : [],
                              });
                            }}
                          >
                            <option value="full_refresh">{syncModeLabel('full_refresh')}</option>
                            {supportsIncremental && (
                              <option value="incremental">{syncModeLabel('incremental')}</option>
                            )}
                          </Select>
                        </td>
                        <td className="px-2 py-2">
                          {!needsCursor ? (
                            <span className="text-tiny text-text-quaternary">
                              {/* A cursor only means something for incremental. */}
                              {isIncremental && stream.source_defined_cursor
                                ? t('stream.connectorDecides')
                                : '—'}
                            </span>
                          ) : (
                            <Select
                              size="sm"
                              className="w-36"
                              disabled={!selection.selected}
                              invalid={selection.cursor_fields.length === 0}
                              value={selection.cursor_fields[0] ?? ''}
                              aria-label={t('stream.cursorLabel', { name: stream.name })}
                              onChange={(event) =>
                                setStream(key, {
                                  cursor_fields: event.target.value ? [event.target.value] : [],
                                })}
                            >
                              <option value="">{t('common.selectPlaceholder')}</option>
                              {stream.fields.map((field) => (
                                <option key={field.name} value={field.name}>{field.name}</option>
                              ))}
                            </Select>
                          )}
                        </td>
                        <td className="px-2 py-2">
                          <Select
                            size="sm"
                            className="w-40"
                            disabled={!selection.selected}
                            value={selection.destination_sync_mode}
                            aria-label={t('stream.writeLabel', { name: stream.name })}
                            onChange={(event) => {
                              const mode = event.target
                                .value as StreamSelection['destination_sync_mode'];
                              setStream(key, {
                                destination_sync_mode: mode,
                                primary_key_fields: mode === 'append_dedup'
                                  ? (selection.primary_key_fields.length
                                      ? selection.primary_key_fields
                                      : stream.source_defined_primary_key)
                                  : [],
                              });
                            }}
                          >
                            {destinationModes.map((mode) => (
                              <option key={mode} value={mode}>{syncModeLabel(mode)}</option>
                            ))}
                          </Select>
                        </td>
                        <td className="px-2 py-2">
                          {!needsPk ? (
                            <span className="text-tiny text-text-quaternary">—</span>
                          ) : selection.primary_key_fields.length > 0 ? (
                            <Badge variant="subtle" size="xs" pill={false}>
                              {selection.primary_key_fields.flat().join(', ')}
                            </Badge>
                          ) : (
                            <Select
                              size="sm"
                              className="w-32"
                              invalid
                              value=""
                              aria-label={t('stream.pkLabel', { name: stream.name })}
                              onChange={(event) =>
                                setStream(key, {
                                  primary_key_fields: event.target.value
                                    ? [[event.target.value]] : [],
                                })}
                            >
                              <option value="">{t('common.selectPlaceholder')}</option>
                              {stream.fields.map((field) => (
                                <option key={field.name} value={field.name}>{field.name}</option>
                              ))}
                            </Select>
                          )}
                        </td>
                        <td className="px-2 py-2">
                          <button
                            type="button"
                            aria-label={t('stream.viewFields', { name: stream.name })}
                            aria-expanded={open}
                            onClick={() => setExpanded(open ? null : key)}
                            className="rounded p-1 text-text-quaternary hover:text-text-primary"
                          >
                            <ChevronRight className={cn('h-3.5 w-3.5 transition-transform',
                              open && 'rotate-90')} />
                          </button>
                        </td>
                      </tr>
                      {open && (
                        <tr className="bg-surface-2/40">
                          <td />
                          <td colSpan={6} className="px-2 pb-3">
                            <div className="flex flex-wrap gap-1.5">
                              {stream.fields.map((field) => (
                                <span
                                  key={field.name}
                                  className="rounded-sm border border-[rgb(var(--border-line))] bg-surface-1 px-1.5 py-0.5 text-tiny"
                                >
                                  <span className="text-text-secondary">{field.name}</span>
                                  <span className="ml-1 text-text-quaternary">{field.type}</span>
                                </span>
                              ))}
                            </div>
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {selectedCount === 0 && (
        <p className="flex items-center gap-1.5 text-caption text-warning">
          <AlertTriangle className="h-3.5 w-3.5" />
          {t('stream.noneSelected')}
        </p>
      )}
    </div>
  );
}

/** Seed one selection per catalog stream, using the connector's own hints. */
export function buildInitialSelections(
  streams: StreamCapability[], destinationModes: string[],
): Record<string, StreamSelection> {
  const preferOverwrite = destinationModes.includes('overwrite');
  const out: Record<string, StreamSelection> = {};
  for (const stream of streams) {
    out[streamKey(stream.namespace, stream.name)] = {
      name: stream.name,
      namespace: stream.namespace,
      selected: false,
      sync_mode: 'full_refresh',
      destination_sync_mode: preferOverwrite ? 'overwrite' : (destinationModes[0] as never),
      cursor_fields: [],
      primary_key_fields: [],
    };
  }
  return out;
}

/** Every problem the backend would reject, surfaced before the user submits. */
export function validateSelections(
  streams: StreamCapability[],
  selections: Record<string, StreamSelection>,
  t: (key: string, vars?: Record<string, string | number>) => string,
): string[] {
  const problems: string[] = [];
  const chosen = Object.entries(selections).filter(([, s]) => s.selected);
  if (chosen.length === 0) return [t('stream.noneSelected')];

  for (const [key, selection] of chosen) {
    const stream = streams.find(
      (s) => streamKey(s.namespace, s.name) === key,
    );
    if (!stream) continue;
    if (selection.sync_mode === 'incremental'
        && !stream.source_defined_cursor
        && selection.cursor_fields.length === 0) {
      problems.push(t('stream.needCursor', { name: stream.name }));
    }
    if (selection.destination_sync_mode === 'append_dedup'
        && selection.primary_key_fields.length === 0) {
      problems.push(t('stream.needPk', { name: stream.name }));
    }
  }
  return problems;
}
