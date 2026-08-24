'use client';

import * as React from 'react';
import { AlertOctagon, Check, ChevronDown, Copy } from 'lucide-react';

import { Button } from '@/components/ui/Button';
import { ApiError } from '@/lib/api';
import { useI18n } from '@/providers/LanguageProvider';
import { cn } from '@/lib/utils';

/**
 * The four-part error card required by section 7.3:
 * what happened / what it affects / what to do next / technical details,
 * plus a copyable trace id for support (section 83).
 */

export interface RemediationInput {
  code?: string | null;
  message: string;
  category?: string | null;
  affects?: string | null;
  action?: string | null;
  technicalMessage?: string | null;
  traceId?: string | null;
  onAction?: () => void;
  onRetry?: () => void;
}

export function fromApiError(error: unknown, affects?: string): RemediationInput {
  if (error instanceof ApiError) {
    return {
      code: error.code,
      message: error.message,
      category: error.category,
      affects,
      action: error.remediation?.action,
      technicalMessage: error.technicalMessage,
      traceId: error.traceId,
    };
  }
  return {
    // The catalog is not reachable from this pure helper; the card itself
    // renders a translated fallback when the message is empty.
    message: error instanceof Error ? error.message : '',
    affects,
  };
}

export function ErrorRemediationCard({
  error, className, compact,
}: {
  error: RemediationInput;
  className?: string;
  compact?: boolean;
}) {
  const { t } = useI18n();
  const [open, setOpen] = React.useState(false);
  const [copied, setCopied] = React.useState(false);

  const copyContext = async () => {
    const payload = [
      `code: ${error.code ?? '-'}`,
      `category: ${error.category ?? '-'}`,
      `trace_id: ${error.traceId ?? '-'}`,
      `time: ${new Date().toISOString()}`,
      error.technicalMessage ? `technical: ${error.technicalMessage}` : '',
    ].filter(Boolean).join('\n');
    try {
      await navigator.clipboard.writeText(payload);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      /* clipboard unavailable — the text stays visible on screen anyway */
    }
  };

  const actionLabel = error.action ? t(`action.${error.action}`) : null;

  return (
    <div
      role="alert"
      className={cn(
        'rounded-lg border border-danger/25 bg-danger/[0.04]',
        compact ? 'p-3' : 'p-4',
        className,
      )}
    >
      <div className="flex items-start gap-3">
        <AlertOctagon className="mt-0.5 h-4 w-4 flex-shrink-0 text-danger" aria-hidden />
        <div className="min-w-0 flex-1 space-y-2">
          <div>
            <p className="text-tiny uppercase tracking-[0.08em] text-text-quaternary">
              {t('error.whatHappened')}
            </p>
            <p className="text-caption font-emphasis leading-relaxed text-text-primary">
              {error.message || t('common.unknownError')}
            </p>
          </div>

          {error.affects && (
            <div>
              <p className="text-tiny uppercase tracking-[0.08em] text-text-quaternary">
                {t('error.whatItAffects')}
              </p>
              <p className="text-caption text-text-secondary">{error.affects}</p>
            </div>
          )}

          {(actionLabel || error.onRetry) && (
            <div>
              <p className="text-tiny uppercase tracking-[0.08em] text-text-quaternary">
                {t('error.whatToDo')}
              </p>
              <div className="mt-1 flex flex-wrap items-center gap-2">
                {actionLabel && error.onAction && (
                  <Button size="xs" variant="primary" onClick={error.onAction}>
                    {actionLabel}
                  </Button>
                )}
                {actionLabel && !error.onAction && (
                  <span className="text-caption text-text-secondary">{actionLabel}</span>
                )}
                {error.onRetry && (
                  <Button size="xs" variant="secondary" onClick={error.onRetry}>
                    {t('common.retry')}
                  </Button>
                )}
              </div>
            </div>
          )}

          {(error.technicalMessage || error.traceId || error.code) && (
            <div className="pt-1">
              <button
                type="button"
                onClick={() => setOpen((v) => !v)}
                aria-expanded={open}
                className="-mx-1 inline-flex items-center gap-1 rounded px-1 py-1.5 text-tiny font-emphasis text-text-tertiary hover:text-text-primary"
              >
                <ChevronDown className={cn('h-3 w-3 transition-transform', open && 'rotate-180')} />
                {t('common.technicalDetails')}
              </button>
              {open && (
                <div className="mt-2 space-y-2 rounded-md bg-surface-2 p-2.5">
                  {error.code && (
                    <p className="font-mono text-tiny text-text-secondary">code: {error.code}</p>
                  )}
                  {error.technicalMessage && (
                    <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words font-mono text-tiny leading-relaxed text-text-tertiary">
                      {error.technicalMessage}
                    </pre>
                  )}
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-mono text-tiny text-text-quaternary">
                      {t('error.traceId')}: {error.traceId ?? '-'}
                    </span>
                    <Button
                      size="xs"
                      variant="ghost"
                      onClick={copyContext}
                      leadingIcon={copied
                        ? <Check className="h-3 w-3" />
                        : <Copy className="h-3 w-3" />}
                    >
                      {copied ? t('common.copied') : t('common.copy')}
                    </Button>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
