'use client';

import * as React from 'react';
import { AlertTriangle, Info, Loader2, Lock, type LucideIcon } from 'lucide-react';

import { cn } from '@/lib/utils';
import { Button } from './Button';
import { useI18n } from '@/providers/LanguageProvider';

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn('skeleton h-4 w-full', className)} aria-hidden />;
}

export function TableSkeleton({ rows = 6, columns = 5 }: { rows?: number; columns?: number }) {
  return (
    <div className="overflow-hidden rounded-lg border border-[rgb(var(--border-line))] bg-surface-1">
      <div className="border-b border-[rgb(var(--border-line))] px-4 py-2.5">
        <Skeleton className="h-3 w-32" />
      </div>
      {Array.from({ length: rows }).map((_, rowIndex) => (
        <div
          key={rowIndex}
          className="flex items-center gap-4 border-b border-[rgb(var(--border-line))] px-4 py-3 last:border-0"
        >
          {Array.from({ length: columns }).map((__, columnIndex) => (
            <Skeleton
              key={columnIndex}
              className={cn('h-3.5', columnIndex === 0 ? 'w-1/4' : 'flex-1')}
            />
          ))}
        </div>
      ))}
    </div>
  );
}

export function CardSkeleton({ count = 4 }: { count?: number }) {
  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
      {Array.from({ length: count }).map((_, index) => (
        <div
          key={index}
          className="rounded-lg border border-[rgb(var(--border-line))] bg-surface-1 p-4"
        >
          <Skeleton className="mb-3 h-4 w-1/2" />
          <Skeleton className="mb-2 h-3 w-full" />
          <Skeleton className="h-3 w-2/3" />
        </div>
      ))}
    </div>
  );
}

export function Spinner({ className, label }: { className?: string; label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-10 text-text-tertiary">
      <Loader2 className={cn('h-5 w-5 animate-spin text-brand', className)} />
      {label && <span className="text-caption">{label}</span>}
    </div>
  );
}

export function EmptyState({
  icon: Icon = Info,
  title,
  description,
  action,
  compact,
}: {
  icon?: LucideIcon;
  title: string;
  description?: React.ReactNode;
  action?: React.ReactNode;
  compact?: boolean;
}) {
  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center rounded-lg border border-dashed',
        'border-[rgb(var(--border-strong))] bg-surface-1 text-center',
        compact ? 'px-4 py-6' : 'px-6 py-10',
      )}
    >
      <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-lg bg-surface-2 text-text-tertiary">
        <Icon className="h-5 w-5" />
      </div>
      <p className="text-small font-emphasis text-text-primary">{title}</p>
      {description && (
        <p className="mt-1 max-w-md text-caption leading-relaxed text-text-tertiary">{description}</p>
      )}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

/**
 * Section-level failure. A partial outage must never blank the whole page
 * (section 33.1), so this renders inline where the data would have been.
 */
/** Is this the server saying "not allowed", rather than "it went wrong"? */
export function isPermissionDenied(error: unknown): boolean {
  return Boolean(error) && (error as { code?: string }).code === 'PERMISSION_DENIED';
}

export function ErrorState({
  title,
  message,
  onRetry,
  traceId,
  compact,
  error,
}: {
  title: string;
  message?: string;
  onRetry?: () => void;
  traceId?: string;
  compact?: boolean;
  /**
   * The error itself, when the caller has it. A refusal is not a failure: the
   * request worked and the answer was no. Framed in red as "could not load"
   * with a Retry button, it reads as a broken product and invites the person to
   * press a button that can only ever refuse again.
   */
  error?: unknown;
}) {
  const { t } = useI18n();
  const denied = isPermissionDenied(error);
  return (
    <div
      className={cn(
        'rounded-lg',
        denied
          ? 'border border-[rgb(var(--border-line))] bg-surface-1'
          : 'border border-danger/25 bg-danger/[0.04]',
        compact ? 'p-3' : 'p-5',
      )}
      role="alert"
    >
      <div className="flex items-start gap-3">
        {denied ? (
          <Lock className="mt-0.5 h-4 w-4 flex-shrink-0 text-text-quaternary" />
        ) : (
          <AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0 text-danger" />
        )}
        <div className="min-w-0 flex-1">
          <p className="text-caption font-strong text-text-primary">{title}</p>
          {message && (
            <p className="mt-1 text-caption leading-relaxed text-text-secondary">{message}</p>
          )}
          {traceId && (
            <p className="mt-1.5 font-mono text-tiny text-text-quaternary">trace: {traceId}</p>
          )}
        </div>
        {onRetry && !denied && (
          <Button size="xs" variant="secondary" onClick={onRetry}>
            {t('common.retry')}
          </Button>
        )}
      </div>
    </div>
  );
}
