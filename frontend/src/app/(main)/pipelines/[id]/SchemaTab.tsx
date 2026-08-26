'use client';

import { RefreshCw, Search } from 'lucide-react';
import * as React from 'react';

import { Card, ModuleOverview } from '@/components/layout/PageLayout';
import { Button } from '@/components/ui/Button';
import { EmptyState } from '@/components/ui/Feedback';
import { Input, Select, Toggle } from '@/components/ui/Input';
import { formatNumber } from '@/lib/format';
import type { PipelineStreamView, StreamSelection } from '@/lib/types';
import { useI18n } from '@/providers/LanguageProvider';

/**
 * The four sync modes, as one choice instead of two.
 *
 * The API models `sync_mode` and `destination_sync_mode` separately, which is
 * correct — they are separate things to the engine. To a person they are not:
 * only four of the six combinations mean anything, and asking someone to pick
 * two dropdowns that must agree is asking them to hold the engine's model in
 * their head. One control, four options, mapped back on save.
 */
export const SYNC_MODES = [
  { id: 'incremental|append_dedup', sync: 'incremental', dest: 'append_dedup' },
  { id: 'full_refresh|overwrite', sync: 'full_refresh', dest: 'overwrite' },
  { id: 'incremental|append', sync: 'incremental', dest: 'append' },
  { id: 'full_refresh|append', sync: 'full_refresh', dest: 'append' },
] as const;

export function syncModeId(stream: { sync_mode: string; destination_sync_mode: string }) {
  return `${stream.sync_mode}|${stream.destination_sync_mode}`;
}

export function SchemaTab({
  streams, saving, onChange, onOpenStream, onRefreshSchema, refreshing,
}: {
  streams: PipelineStreamView[];
  saving: boolean;
  onChange: (next: StreamSelection[]) => void;
  onOpenStream: (stream: PipelineStreamView) => void;
  onRefreshSchema: () => void;
  refreshing: boolean;
}) {
  const { t } = useI18n();
  const [query, setQuery] = React.useState('');
  const [hideDisabled, setHideDisabled] = React.useState(false);

  const term = query.trim().toLowerCase();
  const shown = streams.filter((stream) => {
    if (hideDisabled && !stream.selected) return false;
    return !term || stream.name.toLowerCase().includes(term);
  });
  const selected = streams.filter((stream) => stream.selected);
  const incremental = selected.filter((stream) => stream.sync_mode === 'incremental').length;
  const fieldCount = selected.reduce((total, stream) => total + stream.field_count, 0);

  /** Every change rewrites the whole selection: PATCH replaces the list. */
  const emit = (id: string, patch: Partial<StreamSelection>) => {
    onChange(streams.map((stream) => {
      const base: StreamSelection = {
        name: stream.name,
        namespace: stream.namespace,
        selected: stream.selected,
        sync_mode: stream.sync_mode as StreamSelection['sync_mode'],
        destination_sync_mode:
          stream.destination_sync_mode as StreamSelection['destination_sync_mode'],
        cursor_fields: stream.cursor_fields,
        primary_key_fields: stream.primary_key_fields,
        selected_fields: stream.selected_fields,
      };
      return stream.id === id ? { ...base, ...patch } : base;
    }));
  };

  return (
    <div className="space-y-3">
      <ModuleOverview stats={[
        { label: t('pipelines.schema.selected'), value: formatNumber(selected.length), tone: 'success' },
        { label: t('pipelines.schema.disabled'), value: formatNumber(streams.length - selected.length) },
        { label: t('pipelines.schema.incremental'), value: formatNumber(incremental) },
        { label: t('pipelines.schema.fieldsTotal'), value: formatNumber(fieldCount) },
      ]} />
      <Card
      title={t('pipelines.schema.selectStreams')}
      padded={false}
      action={
        <Button
          size="xs"
          variant="secondary"
          loading={refreshing}
          onClick={onRefreshSchema}
          leadingIcon={<RefreshCw className="h-3 w-3" />}
        >
          {t('pipelines.schema.refreshSource')}
        </Button>
      }
    >
      <div className="flex flex-col gap-2.5 border-b border-[rgb(var(--border-line))] px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="w-full sm:max-w-md">
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t('pipelines.schema.searchStream')}
            aria-label={t('pipelines.schema.searchStream')}
            leadingIcon={<Search className="h-3.5 w-3.5" />}
          />
        </div>
        <Toggle
          checked={hideDisabled}
          onChange={setHideDisabled}
          label={t('pipelines.schema.hideDisabled')}
        />
      </div>

      {shown.length === 0 ? (
        <div className="p-6">
          <EmptyState
            title={t('pipelines.schema.noMatch')}
            action={
              <Button
                size="sm"
                variant="secondary"
                onClick={() => { setQuery(''); setHideDisabled(false); }}
              >
                {t('pipelines.schema.resetFilters')}
              </Button>
            }
          />
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[920px] text-left">
            <thead>
              <tr className="border-b border-[rgb(var(--border-line))] text-tiny uppercase tracking-[0.08em] text-text-tertiary">
                <th scope="col" className="px-4 py-2 font-normal">{t('pipelines.schema.sync')}</th>
                <th scope="col" className="px-3 py-2 font-normal">{t('pipelines.schema.namespace')}</th>
                <th scope="col" className="px-3 py-2 font-normal">{t('pipelines.schema.stream')}</th>
                <th scope="col" className="px-3 py-2 font-normal">{t('pipelines.schema.syncMode')}</th>
                <th scope="col" className="px-3 py-2 text-right font-normal">{t('pipelines.schema.fields')}</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((stream) => (
                <tr
                  key={stream.id}
                  className="border-b border-[rgb(var(--border-line))] last:border-0 hover:bg-surface-2/40"
                >
                  <td className="px-4 py-2.5">
                    <Toggle
                      checked={stream.selected}
                      disabled={saving}
                      onChange={(selected) => emit(stream.id, { selected })}
                      label={t('pipelines.schema.syncStream', { name: stream.name })}
                      hideLabel
                    />
                  </td>
                  <td className="px-3 py-2.5 text-caption text-text-tertiary">
                    {stream.namespace ?? (
                      <span className="text-text-quaternary">
                        {t('pipelines.schema.destinationDefined')}
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2.5">
                    <button
                      type="button"
                      onClick={() => onOpenStream(stream)}
                      className="max-w-[280px] truncate text-caption text-text-primary hover:text-brand hover:underline"
                    >
                      {stream.name}
                    </button>
                  </td>
                  <td className="px-3 py-2.5">
                    <div className="flex items-center gap-2">
                      <div className="w-[268px]">
                        <Select
                          value={syncModeId(stream)}
                          disabled={saving || !stream.selected}
                          aria-label={t('pipelines.schema.syncModeFor', { name: stream.name })}
                          onChange={(event) => {
                            const mode = SYNC_MODES.find((m) => m.id === event.target.value);
                            if (!mode) return;
                            emit(stream.id, {
                              sync_mode: mode.sync as StreamSelection['sync_mode'],
                              destination_sync_mode:
                                mode.dest as StreamSelection['destination_sync_mode'],
                            });
                          }}
                        >
                          {SYNC_MODES.map((mode) => (
                            <option key={mode.id} value={mode.id}>
                              {t(`pipelines.syncMode.${mode.id.replace('|', '_')}`)}
                            </option>
                          ))}
                        </Select>
                      </div>
                      {stream.cursor_fields.length > 0 && (
                        <span className="whitespace-nowrap text-tiny text-text-tertiary">
                          {t('pipelines.schema.cursorField')}{' '}
                          <span className="font-strong text-text-secondary">
                            {stream.cursor_fields.join(', ')}
                          </span>
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="px-3 py-2.5 text-right text-caption tabular-nums text-text-tertiary">
                    {stream.selected_fields === null
                      ? t('pipelines.schema.allFields')
                      : `${formatNumber(stream.selected_fields.length)}/${formatNumber(stream.field_count)}`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      </Card>
    </div>
  );
}
