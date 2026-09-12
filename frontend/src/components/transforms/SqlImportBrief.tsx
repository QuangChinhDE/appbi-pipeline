'use client';

/**
 * The brief, offered where the files are chosen.
 *
 * Most people arriving at this screen are not going to hand-write four dbt
 * models -- they are going to ask an assistant for the SQL. That is worth
 * planning for rather than working around: an assistant told the conventions
 * up front produces files that import cleanly, which costs nothing here and
 * saves the importer guessing at names afterwards.
 *
 * So the brief sits next to the file picker rather than in documentation
 * nobody opens, and the button puts it on the clipboard in one press.
 */

import * as React from 'react';
import { Check, ClipboardCopy, Sparkles } from 'lucide-react';

import { Button } from '@/components/ui/Button';
import { SQL_IMPORT_PROMPT } from '@/lib/sqlImportPrompt';
import { cn } from '@/lib/utils';
import { useI18n } from '@/providers/LanguageProvider';

export function SqlImportBrief({ className }: { className?: string }) {
  const { t, locale } = useI18n();
  const [open, setOpen] = React.useState(false);
  const [copied, setCopied] = React.useState(false);
  const text = SQL_IMPORT_PROMPT[locale] ?? SQL_IMPORT_PROMPT.en;

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2500);
    } catch {
      // Clipboard blocked. The text is on screen either way, which is why the
      // disclosure opens rather than the copy silently doing nothing.
      setOpen(true);
    }
  };

  return (
    <div className={cn(
      'rounded-lg border border-[rgb(var(--border-line))] bg-surface-2/50 p-3',
      className,
    )}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <p className="flex items-center gap-1.5 text-caption font-emphasis text-text-primary">
            <Sparkles className="h-3.5 w-3.5 text-brand" />
            {t('tfsql.briefTitle')}
          </p>
          <p className="mt-0.5 text-tiny leading-relaxed text-text-tertiary">
            {t('tfsql.briefBody')}
          </p>
        </div>
        <div className="flex shrink-0 gap-1.5">
          <Button
            size="xs"
            variant="secondary"
            onClick={copy}
            leadingIcon={copied
              ? <Check className="h-3 w-3 text-success" />
              : <ClipboardCopy className="h-3 w-3" />}
          >
            {copied ? t('common.copied') : t('tfsql.briefCopy')}
          </Button>
          <Button size="xs" variant="ghost" onClick={() => setOpen(!open)}>
            {open ? t('tfsql.briefHide') : t('tfsql.briefShow')}
          </Button>
        </div>
      </div>
      {open && (
        <pre className="mt-2.5 max-h-72 overflow-auto rounded-md bg-surface-1 p-2.5 font-mono text-tiny leading-relaxed text-text-secondary">
          {text}
        </pre>
      )}
    </div>
  );
}
