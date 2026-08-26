'use client';

import { ChevronDown } from 'lucide-react';
import * as React from 'react';

import { cn } from '@/lib/utils';

/**
 * A section that starts collapsed.
 *
 * Used for the parts of a settings page that most people never touch and a few
 * people need badly: namespace format, stream prefix, the engine's replication
 * cursor. Putting them on the page unconditionally makes the page look like a
 * form with twenty required decisions; hiding them behind a separate screen
 * makes them undiscoverable.
 *
 * The content is unmounted while closed, not hidden with CSS. The replication
 * cursor panel fetches from the engine when it opens, and a display:none panel
 * would fetch on every page load for a panel nobody opened.
 */
export function Disclosure({
  label, description, defaultOpen = false, children, className, onOpen,
}: {
  label: React.ReactNode;
  description?: React.ReactNode;
  defaultOpen?: boolean;
  children: React.ReactNode;
  className?: string;
  /** Fired the first time it opens, for panels that load on demand. */
  onOpen?: () => void;
}) {
  const [open, setOpen] = React.useState(defaultOpen);
  const opened = React.useRef(defaultOpen);
  const id = React.useId();

  const toggle = () => {
    const next = !open;
    setOpen(next);
    if (next && !opened.current) { opened.current = true; onOpen?.(); }
  };

  React.useEffect(() => {
    if (defaultOpen && !opened.current) { opened.current = true; onOpen?.(); }
  }, [defaultOpen, onOpen]);

  return (
    <div className={className}>
      <button
        type="button"
        onClick={toggle}
        aria-expanded={open}
        aria-controls={id}
        className={cn(
          'inline-flex items-center gap-1.5 rounded py-1 text-caption font-strong',
          'text-brand transition-colors hover:text-text-primary',
          'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2',
          'focus-visible:outline-brand',
        )}
      >
        {label}
        <ChevronDown className={cn('h-3.5 w-3.5 transition-transform', open && 'rotate-180')} />
      </button>
      {description && (
        <p className="mt-0.5 text-tiny text-text-tertiary">{description}</p>
      )}
      {open && <div id={id} className="mt-3">{children}</div>}
    </div>
  );
}
