'use client';

/**
 * What the upload is about to become, before it becomes it.
 *
 * The screen is arranged around one question -- is this what you meant -- so
 * the things a person is likely to want to change are editable in place and
 * everything else is shown only to be read. The converted query is behind a
 * disclosure rather than in the table, because the point of the review is the
 * shape of the project; the body is there for the one model somebody wants to
 * check, not for all of them at once.
 *
 * The two rows that matter most are the ones that are not models: what will be
 * skipped, and which warehouse tables the queries turn out to read. Both are
 * common ways an upload is not what somebody assumed.
 */

import * as React from 'react';
import {
  AlertTriangle, ChevronRight, Database, FileWarning, Sparkles,
} from 'lucide-react';

import { Badge } from '@/components/ui/Badge';
import { Input, Select } from '@/components/ui/Input';
import { cn } from '@/lib/utils';
import type { SqlImportAnalysis, SqlImportCandidate } from '@/lib/types';
import { useI18n } from '@/providers/LanguageProvider';

const LAYERS = ['staging', 'intermediate', 'marts'] as const;
const MATERIALIZATIONS = ['view', 'table', 'ephemeral'] as const;

export function SqlImportReview({
  analysis, onChange,
}: {
  analysis: SqlImportAnalysis;
  onChange: (candidates: SqlImportCandidate[]) => void;
}) {
  const { t } = useI18n();
  const [open, setOpen] = React.useState<string | null>(null);

  const update = (key: string, patch: Partial<SqlImportCandidate>) => {
    onChange(analysis.candidates.map((item) =>
      item.key === key ? { ...item, ...patch } : item));
  };

  return (
    <div className="space-y-3">
      {analysis.cycle.length > 0 && (
        <Notice tone="danger" icon={AlertTriangle}>
          {t('tfsql.cycle', { models: analysis.cycle.join(' → ') })}
        </Notice>
      )}

      {analysis.notes.map((note) => (
        <Notice key={note} tone="warning" icon={Sparkles}>{note}</Notice>
      ))}

      {analysis.skipped.length > 0 && (
        <div className="rounded-md border border-warning/30 bg-warning/[0.06] p-3">
          <p className="flex items-center gap-1.5 text-caption font-emphasis text-text-primary">
            <FileWarning className="h-3.5 w-3.5 text-warning" />
            {t('tfsql.skipped', { n: analysis.skipped.length })}
          </p>
          <ul className="mt-1.5 space-y-1">
            {analysis.skipped.map((item) => (
              <li key={`${item.file}-${item.reason}`} className="text-tiny text-text-secondary">
                <span className="font-mono text-text-primary">{item.file}</span>
                {' — '}{item.reason}
              </li>
            ))}
          </ul>
        </div>
      )}

      {analysis.candidates.length === 0 ? (
        <p className="rounded-md bg-surface-2 px-3 py-6 text-center text-caption text-text-tertiary">
          {t('tfsql.nothingToImport')}
        </p>
      ) : (
        <div className="overflow-hidden rounded-lg border border-[rgb(var(--border-line))]">
          <table className="w-full text-left">
            <thead>
              <tr className="border-b border-[rgb(var(--border-line))] bg-surface-2 text-tiny uppercase tracking-[0.08em] text-text-quaternary">
                <th scope="col" className="px-3 py-2 font-emphasis">{t('tfsql.colModel')}</th>
                <th scope="col" className="px-3 py-2 font-emphasis">{t('tfsql.colLayer')}</th>
                <th scope="col" className="px-3 py-2 font-emphasis">{t('tfsql.colStored')}</th>
                <th scope="col" className="px-3 py-2 font-emphasis">{t('tfsql.colReads')}</th>
                <th scope="col" className="w-8 px-2 py-2" />
              </tr>
            </thead>
            <tbody className="divide-y divide-[rgb(var(--border-line))]">
              {analysis.candidates.map((candidate) => (
                <React.Fragment key={candidate.key}>
                  <tr className="align-top">
                    <td className="px-3 py-2">
                      <Input
                        size="sm"
                        value={candidate.name}
                        aria-label={t('tfsql.nameFor', { file: candidate.file_name })}
                        onChange={(event) =>
                          update(candidate.key, { name: event.target.value })}
                        className="font-mono"
                      />
                      <p className="mt-1 truncate text-tiny text-text-quaternary">
                        {candidate.file_name}
                      </p>
                    </td>
                    <td className="px-3 py-2">
                      <Select
                        size="sm"
                        value={candidate.layer}
                        aria-label={t('tfsql.layerFor', { file: candidate.file_name })}
                        onChange={(event) =>
                          update(candidate.key, { layer: event.target.value })}
                      >
                        {LAYERS.map((layer) => (
                          <option key={layer} value={layer}>{layer}</option>
                        ))}
                      </Select>
                    </td>
                    <td className="px-3 py-2">
                      <Select
                        size="sm"
                        value={candidate.materialized}
                        aria-label={t('tfsql.storedFor', { file: candidate.file_name })}
                        onChange={(event) =>
                          update(candidate.key, { materialized: event.target.value })}
                      >
                        {MATERIALIZATIONS.map((item) => (
                          <option key={item} value={item}>{item}</option>
                        ))}
                      </Select>
                    </td>
                    <td className="px-3 py-2">
                      <div className="flex flex-wrap gap-1">
                        {candidate.depends_on.map((name) => (
                          <Badge key={name} variant="brand" size="xs">{name}</Badge>
                        ))}
                        {candidate.sources.map((name) => (
                          <Badge key={name} variant="subtle" size="xs">
                            <Database className="h-2.5 w-2.5" />{name}
                          </Badge>
                        ))}
                        {candidate.depends_on.length === 0
                          && candidate.sources.length === 0 && (
                          <span className="text-tiny text-text-quaternary">—</span>
                        )}
                      </div>
                      {candidate.description && (
                        <p className="mt-1 text-tiny text-text-tertiary">
                          {candidate.description}
                        </p>
                      )}
                    </td>
                    <td className="px-2 py-2">
                      <button
                        type="button"
                        onClick={() =>
                          setOpen(open === candidate.key ? null : candidate.key)}
                        aria-label={t('tfsql.showQuery', { file: candidate.file_name })}
                        className="rounded-sm p-1 text-text-tertiary hover:bg-surface-2"
                      >
                        <ChevronRight className={cn(
                          'h-3.5 w-3.5 transition-transform',
                          open === candidate.key && 'rotate-90',
                        )} />
                      </button>
                    </td>
                  </tr>
                  {open === candidate.key && (
                    <tr className="bg-surface-2/40">
                      <td colSpan={5} className="px-3 py-2">
                        <p className="mb-1 text-tiny text-text-tertiary">
                          {t('tfsql.queryUnchanged')}
                        </p>
                        <pre className="max-h-64 overflow-auto rounded-sm bg-surface-1 p-2 font-mono text-tiny leading-relaxed text-text-secondary">
                          {candidate.preview}
                        </pre>
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {analysis.sources.length > 0 && (
        <p className="text-tiny text-text-tertiary">
          {t('tfsql.sourcesDeclared', { tables: analysis.sources.join(', ') })}
        </p>
      )}
      {!analysis.ai_used && analysis.candidates.length > 0 && (
        <p className="text-tiny text-text-quaternary">{t('tfsql.noAi')}</p>
      )}
    </div>
  );
}

function Notice({
  tone, icon: Icon, children,
}: {
  tone: 'danger' | 'warning';
  icon: React.ElementType;
  children: React.ReactNode;
}) {
  return (
    <p className={cn(
      'flex items-start gap-2 rounded-md p-2.5 text-caption',
      tone === 'danger'
        ? 'bg-danger/[0.06] text-danger'
        : 'bg-warning/[0.06] text-text-secondary',
    )}>
      <Icon className={cn(
        'mt-0.5 h-3.5 w-3.5 shrink-0',
        tone === 'danger' ? 'text-danger' : 'text-warning',
      )} />
      <span className="min-w-0">{children}</span>
    </p>
  );
}
