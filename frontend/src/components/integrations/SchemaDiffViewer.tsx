'use client';

import * as React from 'react';
import { AlertTriangle, MinusCircle, PencilLine, PlusCircle } from 'lucide-react';

import { Badge } from '@/components/ui/Badge';
import { EmptyState } from '@/components/ui/Feedback';
import type { SchemaChange, SchemaDiff } from '@/lib/types';
import { useI18n } from '@/providers/LanguageProvider';

const SEVERITY_VARIANT: Record<string, 'info' | 'warning' | 'danger'> = {
  INFO: 'info', WARNING: 'warning', BREAKING: 'danger',
};

/** Added / Removed / Changed, exactly as section 9.3 step 6 asks. */
export function SchemaDiffViewer({ diff }: { diff: SchemaDiff }) {
  const { t } = useI18n();
  const total = diff.added.length + diff.removed.length + diff.changed.length;
  if (total === 0) {
    return (
      <EmptyState
        title={t('diff.noChange')}
        description={t('diff.noChangeBody')}
        compact
      />
    );
  }

  return (
    <div className="space-y-4">
      {diff.has_breaking && (
        <div className="flex items-start gap-2 rounded-lg border border-danger/25 bg-danger/[0.04] p-3">
          <AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0 text-danger" />
          <p className="text-caption leading-relaxed text-text-secondary">
            {t('diff.breakingWarning')}
          </p>
        </div>
      )}

      <Group title={t('diff.added')} icon={PlusCircle} tone="text-success" changes={diff.added} />
      <Group title={t('diff.removed')} icon={MinusCircle} tone="text-danger" changes={diff.removed} />
      <Group title={t('diff.changed')} icon={PencilLine} tone="text-warning" changes={diff.changed} />
    </div>
  );
}

function Group({
  title, icon: Icon, tone, changes,
}: {
  title: string;
  icon: React.ElementType;
  tone: string;
  changes: SchemaChange[];
}) {
  const { t } = useI18n();
  if (changes.length === 0) return null;
  return (
    <section>
      <h3 className="mb-1.5 flex items-center gap-1.5 text-caption font-strong text-text-primary">
        <Icon className={`h-3.5 w-3.5 ${tone}`} />
        {title}
        <span className="text-text-quaternary">({changes.length})</span>
      </h3>
      <ul className="divide-y divide-[rgb(var(--border-line))] overflow-hidden rounded-lg border border-[rgb(var(--border-line))] bg-surface-1">
        {changes.map((change, index) => {
          const variant = SEVERITY_VARIANT[change.severity] ?? 'info';
          return (
            <li key={`${change.kind}-${change.stream_name}-${change.field_name ?? index}`}
                className="flex items-start justify-between gap-3 px-3 py-2">
              <div className="min-w-0">
                <p className="text-caption text-text-primary">{change.message}</p>
                <p className="mt-0.5 font-mono text-tiny text-text-quaternary">
                  {change.namespace ? `${change.namespace}.` : ''}{change.stream_name}
                  {change.field_name ? `.${change.field_name}` : ''}
                  {change.before && change.after && (
                    <span className="ml-1.5">{change.before} → {change.after}</span>
                  )}
                </p>
              </div>
              <Badge variant={variant} size="xs">
                {t(`severity.${change.severity}`)}
              </Badge>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
