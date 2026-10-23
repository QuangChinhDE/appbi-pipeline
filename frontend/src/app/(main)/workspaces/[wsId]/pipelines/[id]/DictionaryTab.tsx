'use client';

import { Download, KeyRound, Pencil, Search, Sparkles } from 'lucide-react';
import * as React from 'react';

import { Card, ModuleOverview } from '@/components/layout/PageLayout';
import { Badge, type BadgeVariant } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { EmptyState, Spinner } from '@/components/ui/Feedback';
import { Input, Select, Textarea, Toggle } from '@/components/ui/Input';
import { formatNumber } from '@/lib/format';
import type { DictionaryField, DictionaryOrigin, DictionaryView } from '@/lib/types';
import { useI18n } from '@/providers/LanguageProvider';

const ORIGIN_BADGE: Record<Exclude<DictionaryOrigin, null>, BadgeVariant> = {
  USER: 'brand',
  CONNECTOR: 'success',
  AI: 'info',
};

/**
 * The data dictionary: every field of every stream and what it means.
 *
 * Structure (path, type) is the schema's and is read-only here. Only the
 * meaning is editable, and an edit is this source's own -- it never changes
 * what another workspace reads for the same connector.
 */
export function DictionaryTab({
  data, isLoading, canEdit, saving, onSave, exportUrl,
}: {
  data: DictionaryView | undefined;
  isLoading: boolean;
  canEdit: boolean;
  saving: boolean;
  onSave: (streamId: string, path: string, description: string) => Promise<unknown>;
  exportUrl: (format: 'csv' | 'xlsx') => string;
}) {
  const { t, locale } = useI18n();
  const [query, setQuery] = React.useState('');
  const [streamFilter, setStreamFilter] = React.useState('selected');
  const [undefinedOnly, setUndefinedOnly] = React.useState(false);
  const [editing, setEditing] = React.useState<{ stream: string; path: string } | null>(null);
  const [draft, setDraft] = React.useState('');

  if (isLoading || !data) return <Spinner label={t('common.loading')} />;

  const meaning = (field: DictionaryField) =>
    (locale === 'vi' ? field.description_vi : field.description) ?? field.description;

  const term = query.trim().toLowerCase();
  const streams = data.streams
    .filter((stream) => (streamFilter === 'all'
      ? true
      : streamFilter === 'selected' ? stream.selected : stream.stream_id === streamFilter))
    .map((stream) => ({
      ...stream,
      fields: stream.fields.filter((field) => {
        if (undefinedOnly && field.origin) return false;
        if (!term) return true;
        return field.path.toLowerCase().includes(term)
          || stream.name.toLowerCase().includes(term)
          || (meaning(field) ?? '').toLowerCase().includes(term);
      }),
    }))
    .filter((stream) => stream.fields.length > 0);

  const all = data.streams.filter((stream) => stream.selected).flatMap((s) => s.fields);
  const defined = all.filter((field) => field.origin).length;

  const startEdit = (streamId: string, field: DictionaryField) => {
    setEditing({ stream: streamId, path: field.path });
    // Start from what is shown, so correcting an AI definition is an edit
    // rather than a rewrite from nothing.
    setDraft(meaning(field) ?? '');
  };
  /** The page toasts a failure; the editor stays open so nothing typed is lost. */
  const commit = async (streamId: string, path: string, text: string) => {
    try {
      await onSave(streamId, path, text);
      setEditing(null);
    } catch {
      /* reported by the page */
    }
  };
  const save = () => (editing ? commit(editing.stream, editing.path, draft) : undefined);

  return (
    <div className="space-y-3">
      <ModuleOverview stats={[
        { label: t('pipelines.dictionary.streams'),
          value: formatNumber(data.streams.filter((s) => s.selected).length) },
        { label: t('pipelines.dictionary.fields'), value: formatNumber(all.length) },
        { label: t('pipelines.dictionary.defined'), value: formatNumber(defined), tone: 'success' },
        { label: t('pipelines.dictionary.undefined'), value: formatNumber(all.length - defined) },
      ]} />

      <Card
        title={t('pipelines.dictionary.title')}
        description={data.ai_enabled
          ? t('pipelines.dictionary.aiOn') : t('pipelines.dictionary.aiOff')}
        padded={false}
        action={
          <div className="flex items-center gap-2">
            <Button size="xs" variant="secondary" leadingIcon={<Download className="h-3 w-3" />}
                    onClick={() => window.open(exportUrl('csv'), '_blank')}>
              CSV
            </Button>
            <Button size="xs" variant="secondary" leadingIcon={<Download className="h-3 w-3" />}
                    onClick={() => window.open(exportUrl('xlsx'), '_blank')}>
              Excel
            </Button>
          </div>
        }
      >
        <div className="flex flex-col gap-2.5 border-b border-[rgb(var(--border-line))] px-4 py-3 sm:flex-row sm:items-center">
          <div className="w-full sm:max-w-sm">
            <Input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder={t('pipelines.dictionary.search')}
              aria-label={t('pipelines.dictionary.search')}
              leadingIcon={<Search className="h-3.5 w-3.5" />}
            />
          </div>
          <Select
            value={streamFilter}
            onChange={(event) => setStreamFilter(event.target.value)}
            aria-label={t('pipelines.dictionary.streamFilter')}
            className="sm:w-[240px]"
          >
            <option value="selected">{t('pipelines.dictionary.selectedStreams')}</option>
            <option value="all">{t('pipelines.dictionary.allStreams')}</option>
            {data.streams.map((stream) => (
              <option key={stream.stream_id} value={stream.stream_id}>{stream.name}</option>
            ))}
          </Select>
          <Toggle checked={undefinedOnly} onChange={setUndefinedOnly}
                  label={t('pipelines.dictionary.undefinedOnly')} />
        </div>

        {streams.length === 0 ? (
          <div className="p-6"><EmptyState title={t('pipelines.dictionary.noMatch')} /></div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[960px] text-left">
              <thead>
                <tr className="border-b border-[rgb(var(--border-line))] text-tiny uppercase tracking-[0.08em] text-text-tertiary">
                  <th scope="col" className="w-[30%] px-4 py-2 font-normal">{t('pipelines.dictionary.field')}</th>
                  <th scope="col" className="w-[150px] px-3 py-2 font-normal">{t('pipelines.dictionary.type')}</th>
                  <th scope="col" className="px-3 py-2 font-normal">{t('pipelines.dictionary.definition')}</th>
                  <th scope="col" className="w-[110px] px-3 py-2 font-normal">{t('pipelines.dictionary.origin')}</th>
                </tr>
              </thead>
              {streams.map((stream) => (
                <tbody key={stream.stream_id}>
                  <tr className="border-b border-[rgb(var(--border-line))] bg-surface-2/50">
                    <td colSpan={4} className="px-4 py-2 text-caption font-strong text-text-primary">
                      {stream.name}
                      {!stream.selected && (
                        <span className="ml-2 text-tiny font-normal text-text-quaternary">
                          {t('pipelines.dictionary.notSynced')}
                        </span>
                      )}
                    </td>
                  </tr>
                  {stream.fields.map((field) => {
                    const isEditing = editing?.stream === stream.stream_id
                      && editing.path === field.path;
                    const text = meaning(field);
                    return (
                      <tr key={field.path}
                          className="border-b border-[rgb(var(--border-line))] align-top last:border-0 hover:bg-surface-2/30">
                        <td className="px-4 py-1.5">
                          <span className="flex items-center gap-1.5 text-caption text-text-secondary"
                                style={{ paddingLeft: `${field.depth * 18}px` }}
                                title={field.path}>
                            {field.depth > 0 && (
                              <span className="select-none text-text-quaternary" aria-hidden>└</span>
                            )}
                            <span className="truncate">{field.name}</span>
                            {field.is_primary_key && (
                              <KeyRound className="h-3 w-3 text-brand"
                                        aria-label={t('pipelines.stream.isPrimaryKey')} />
                            )}
                            {field.is_cursor && (
                              <Badge size="xs" variant="subtle">{t('pipelines.stream.cursorField')}</Badge>
                            )}
                          </span>
                        </td>
                        <td className="px-3 py-1.5 text-caption text-text-tertiary">
                          {field.type}
                          {field.nullable && <span className="ml-1 text-text-quaternary">?</span>}
                        </td>
                        <td className="px-3 py-1.5 text-caption">
                          {isEditing ? (
                            <div className="space-y-1.5">
                              <Textarea
                                autoFocus
                                rows={2}
                                value={draft}
                                maxLength={2000}
                                onChange={(event) => setDraft(event.target.value)}
                                aria-label={t('pipelines.dictionary.definition')}
                              />
                              <div className="flex gap-1.5">
                                <Button size="xs" variant="primary" loading={saving} onClick={save}>
                                  {t('common.save')}
                                </Button>
                                <Button size="xs" variant="secondary" disabled={saving}
                                        onClick={() => setEditing(null)}>
                                  {t('common.cancel')}
                                </Button>
                                {field.origin === 'USER' && (
                                  <Button size="xs" variant="ghost" disabled={saving}
                                          onClick={() => commit(stream.stream_id, field.path, '')}>
                                    {t('pipelines.dictionary.revert')}
                                  </Button>
                                )}
                              </div>
                            </div>
                          ) : (
                            <div className="group flex items-start gap-1.5">
                              <span className={text ? 'text-text-secondary' : 'italic text-text-quaternary'}>
                                {text || t('pipelines.dictionary.none')}
                              </span>
                              {canEdit && (
                                <button
                                  type="button"
                                  onClick={() => startEdit(stream.stream_id, field)}
                                  className="opacity-0 transition-opacity group-hover:opacity-100 focus:opacity-100"
                                  aria-label={t('pipelines.dictionary.edit', { field: field.path })}
                                >
                                  <Pencil className="h-3 w-3 text-text-tertiary hover:text-brand" />
                                </button>
                              )}
                            </div>
                          )}
                        </td>
                        <td className="px-3 py-1.5">
                          {field.origin && (
                            <Badge size="xs" variant={ORIGIN_BADGE[field.origin]}>
                              {field.origin === 'AI' && <Sparkles className="h-2.5 w-2.5" />}
                              {t(`pipelines.dictionary.origin.${field.origin}`)}
                            </Badge>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              ))}
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
