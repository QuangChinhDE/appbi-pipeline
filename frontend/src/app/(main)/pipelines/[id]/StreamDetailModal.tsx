'use client';

import { ArrowRight, KeyRound, X } from 'lucide-react';
import * as React from 'react';

import { Select, Toggle } from '@/components/ui/Input';
import { formatNumber } from '@/lib/format';
import type { PipelineStreamView, StreamSelection } from '@/lib/types';
import { cn } from '@/lib/utils';
import { useI18n } from '@/providers/LanguageProvider';

import { SYNC_MODES, syncModeId } from './SchemaTab';

/**
 * What this stream sends, field by field, and what it becomes on the far side.
 *
 * The question it answers is "will the column I need actually be there", and
 * that is not answerable from the stream list: `account_export` shows as one
 * object row there while producing twenty-two destination columns. Both sides
 * are shown together because the interesting cases are the ones where they
 * differ — a renamed field, a nested path flattened into a column name.
 */
export function StreamDetailModal({
  stream, open, saving, onClose, onChange,
}: {
  stream: PipelineStreamView | null;
  open: boolean;
  saving: boolean;
  onClose: () => void;
  onChange: (patch: Partial<StreamSelection>) => void;
}) {
  const { t } = useI18n();
  const closeRef = React.useRef<HTMLButtonElement>(null);

  React.useEffect(() => {
    if (!open) return undefined;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    closeRef.current?.focus();
    return () => document.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open || !stream) return null;

  const primaryKeys = new Set(stream.primary_key_fields.map((parts) => parts.join('.')));
  const cursors = new Set(stream.cursor_fields);

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-black/60 p-0 sm:p-6">
      <div
        role="dialog"
        aria-modal="true"
        aria-label={t('pipelines.stream.title', { name: stream.name })}
        className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-none border border-[rgb(var(--border-line))] bg-surface-1 sm:rounded-xl"
      >
        {/* Header: the two decisions that belong to the whole stream. */}
        <div className="flex flex-shrink-0 flex-wrap items-center gap-4 border-b border-[rgb(var(--border-line))] px-4 py-3">
          <Toggle
            checked={stream.selected}
            disabled={saving}
            onChange={(selected) => onChange({ selected })}
            label={t('pipelines.stream.syncStream')}
          />
          <div className="flex min-w-0 flex-1 items-center justify-center gap-3">
            <span className="truncate text-caption text-text-tertiary">
              {t('pipelines.stream.name')}{' '}
              <span className="font-strong text-text-primary">{stream.name}</span>
            </span>
            <div className="w-[268px]">
              <Select
                value={syncModeId(stream)}
                disabled={saving || !stream.selected}
                aria-label={t('pipelines.schema.syncMode')}
                onChange={(event) => {
                  const mode = SYNC_MODES.find((m) => m.id === event.target.value);
                  if (!mode) return;
                  onChange({
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
          </div>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            aria-label={t('common.close')}
            className="rounded-md p-1.5 text-text-tertiary transition-colors hover:bg-surface-2 hover:text-text-primary focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Pane titles stay put while the field list scrolls: with 200 fields,
            a header that scrolls away leaves the columns unlabelled. */}
        <div className="flex flex-shrink-0 items-center gap-2 border-b border-[rgb(var(--border-line))] px-4 py-2.5">
          <span className="flex-1 text-caption font-strong text-text-primary">
            {t('pipelines.stream.sourcePane')}
          </span>
          <ArrowRight className="h-3.5 w-3.5 text-text-quaternary" aria-hidden />
          <span className="flex-1 text-caption font-strong text-text-primary">
            {t('pipelines.stream.destPane')}
          </span>
        </div>

        <div className="min-h-0 flex-1 overflow-auto">
          <table className="w-full min-w-[860px] text-left">
            <thead className="sticky top-0 z-10 bg-surface-1">
              <tr className="border-b border-[rgb(var(--border-line))] text-tiny uppercase tracking-[0.08em] text-text-tertiary">
                <th scope="col" className="px-4 py-2 font-normal">{t('pipelines.stream.fieldName')}</th>
                <th scope="col" className="w-[140px] px-3 py-2 font-normal">{t('pipelines.stream.dataType')}</th>
                <th scope="col" className="w-[110px] px-3 py-2 font-normal">{t('pipelines.stream.cursorField')}</th>
                <th scope="col" className="w-[110px] px-3 py-2 font-normal">{t('pipelines.stream.primaryKey')}</th>
                <th scope="col" className="w-8 px-2 py-2" />
                <th scope="col" className="px-3 py-2 font-normal">{t('pipelines.stream.fieldName')}</th>
              </tr>
            </thead>
            <tbody>
              {stream.fields.map((field) => (
                <tr
                  key={field.path}
                  className="border-b border-[rgb(var(--border-line))] last:border-0 hover:bg-surface-2/30"
                >
                  <td className="px-4 py-1.5">
                    <span
                      className="flex items-center gap-1.5 text-caption text-text-secondary"
                      style={{ paddingLeft: `${field.depth * 18}px` }}
                    >
                      {field.depth > 0 && (
                        <span className="select-none text-text-quaternary" aria-hidden>└</span>
                      )}
                      <span className="truncate">{field.name}</span>
                    </span>
                  </td>
                  {/* Nullability rides with the type. Beside the name it
                      repeated on nearly every row of a Base stream and became
                      noise the eye had to step over to read the names. */}
                  <td className="px-3 py-1.5 text-caption text-text-tertiary">
                    {field.type}
                    {field.nullable && (
                      <span className="ml-1 text-text-quaternary">?</span>
                    )}
                  </td>
                  <td className="px-3 py-1.5">
                    <FieldMark on={cursors.has(field.path)} label={t('pipelines.stream.isCursor')} />
                  </td>
                  <td className="px-3 py-1.5">
                    <FieldMark
                      on={primaryKeys.has(field.path)}
                      label={t('pipelines.stream.isPrimaryKey')}
                      icon={<KeyRound className="h-3 w-3" />}
                    />
                  </td>
                  <td className="px-2 py-1.5 text-center">
                    <ArrowRight className="mx-auto h-3 w-3 text-text-quaternary" aria-hidden />
                  </td>
                  <td className="px-3 py-1.5 text-caption text-text-secondary">
                    <span className="truncate">{field.path}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="flex-shrink-0 border-t border-[rgb(var(--border-line))] px-4 py-2 text-tiny text-text-tertiary">
          {t('pipelines.stream.fieldSummary', {
            n: formatNumber(stream.fields.length),
            top: formatNumber(stream.field_count),
          })}
        </div>
      </div>
    </div>
  );
}

function FieldMark({
  on, label, icon,
}: {
  on: boolean;
  label: string;
  icon?: React.ReactNode;
}) {
  if (!on) {
    return (
      <span
        aria-hidden
        className="block h-3 w-3 rounded-full border border-[rgb(var(--border-line))]"
      />
    );
  }
  return (
    <span
      title={label}
      className={cn(
        'inline-flex items-center gap-1 rounded-full bg-brand/15 px-1.5 py-0.5',
        'text-tiny font-strong text-brand',
      )}
    >
      {icon ?? <span className="h-1.5 w-1.5 rounded-full bg-brand" aria-hidden />}
      <span className="sr-only">{label}</span>
    </span>
  );
}
