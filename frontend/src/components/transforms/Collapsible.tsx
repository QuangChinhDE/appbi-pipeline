'use client';

import * as React from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';

import { cn } from '@/lib/utils';

/**
 * Remembered per pane, per browser.
 *
 * Which panes somebody wants open depends on what they are doing — reading a
 * failure, writing SQL, checking a schedule — and that does not change from one
 * visit to the next. Re-collapsing everything on every load would make the
 * setting worthless.
 */
export function usePaneState(key: string, initial: boolean) {
  const storageKey = `appbi.transform.pane.${key}`;
  const [open, setOpen] = React.useState(initial);

  React.useEffect(() => {
    try {
      const stored = window.localStorage.getItem(storageKey);
      if (stored !== null) setOpen(stored === '1');
    } catch {
      // Private windows and blocked site data both throw; the default stands.
    }
  }, [storageKey]);

  const toggle = React.useCallback(() => {
    setOpen((current) => {
      const next = !current;
      try {
        window.localStorage.setItem(storageKey, next ? '1' : '0');
      } catch {
        // Not being able to remember the choice is not a reason to refuse it.
      }
      return next;
    });
  }, [storageKey]);

  return [open, toggle] as const;
}

/** A titled section the user can fold away, with room for a summary and actions. */
export function Pane({
  title, count, summary, action, open, onToggle, children, className,
}: {
  title: string;
  /** Shown beside the title; keeps the number visible while folded. */
  count?: number;
  /** One line describing the contents, readable while folded. */
  summary?: React.ReactNode;
  action?: React.ReactNode;
  open: boolean;
  onToggle: () => void;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={cn('border-b border-[rgb(var(--border-line))]', className)}>
      <div className="flex h-8 items-center gap-1 pl-1 pr-2">
        <button
          type="button" onClick={onToggle} aria-expanded={open}
          className="flex min-w-0 flex-1 items-center gap-1 text-left"
        >
          {open
            ? <ChevronDown className="h-3.5 w-3.5 shrink-0 text-text-quaternary" />
            : <ChevronRight className="h-3.5 w-3.5 shrink-0 text-text-quaternary" />}
          <span className="text-[10px] font-emphasis uppercase tracking-wide text-text-quaternary">
            {title}
          </span>
          {count !== undefined && count > 0 && (
            <span className="text-[10px] text-text-quaternary">({count})</span>
          )}
          {!open && summary && (
            <span className="ml-1 min-w-0 flex-1 truncate text-tiny text-text-quaternary">
              {summary}
            </span>
          )}
        </button>
        {action}
      </div>
      {open && <div className="px-2 pb-2">{children}</div>}
    </section>
  );
}
