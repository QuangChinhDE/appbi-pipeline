'use client';

import { MoreVertical } from 'lucide-react';
import * as React from 'react';

import { cn } from '@/lib/utils';

export interface MenuItem {
  id: string;
  label: string;
  /** One line on what the action does; shown under the label. */
  description?: string;
  icon?: React.ReactNode;
  onSelect: () => void;
  destructive?: boolean;
  disabled?: boolean;
}

/**
 * A row-level action menu.
 *
 * Rows in this product carry three to five actions each, and rendering them as
 * buttons puts a wall of controls beside every row -- the eye then has to scan
 * past the actions to read the data, which is the wrong way round for a table
 * whose job is to be read.
 *
 * Keyboard support is not decoration here: the menu holds the only route to
 * some actions (clearing a stream's data, downloading a log), so a pointer-only
 * menu would make them unreachable. Arrow keys move, Enter selects, Escape
 * closes and returns focus to the trigger, and focus is trapped while open.
 */
export function Menu({
  items, label, align = 'end', trigger,
}: {
  items: MenuItem[];
  /** Accessible name. The trigger is an icon, so it has no text of its own. */
  label: string;
  align?: 'start' | 'end';
  trigger?: React.ReactNode;
}) {
  const [open, setOpen] = React.useState(false);
  const [active, setActive] = React.useState(0);
  const [above, setAbove] = React.useState(false);
  const root = React.useRef<HTMLDivElement>(null);
  const panel = React.useRef<HTMLDivElement>(null);
  const triggerRef = React.useRef<HTMLButtonElement>(null);
  const itemRefs = React.useRef<(HTMLButtonElement | null)[]>([]);

  const enabled = items.filter((item) => !item.disabled);

  const close = React.useCallback((refocus = true) => {
    setOpen(false);
    if (refocus) triggerRef.current?.focus();
  }, []);

  React.useEffect(() => {
    if (!open) return undefined;
    // Pointer-down rather than click: a click listener fires after the button
    // that opened another menu has already toggled, so two menus could be open
    // at once.
    const onPointerDown = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) close(false);
    };
    document.addEventListener('pointerdown', onPointerDown);
    return () => document.removeEventListener('pointerdown', onPointerDown);
  }, [open, close]);

  React.useEffect(() => {
    if (open) itemRefs.current[active]?.focus();
  }, [open, active]);

  // Flip above the trigger when there is no room below.
  //
  // The last row of a table is the common case and the worst one: the menu
  // opened past the bottom of the viewport, so the destructive item at the end
  // of the list -- the one that most needs to be seen before it is clicked --
  // was the part that fell off screen.
  React.useLayoutEffect(() => {
    if (!open) { setAbove(false); return; }
    const trigger = triggerRef.current?.getBoundingClientRect();
    const height = panel.current?.offsetHeight ?? 0;
    if (!trigger || !height) return;
    const roomBelow = window.innerHeight - trigger.bottom;
    setAbove(roomBelow < height + 8 && trigger.top > height + 8);
  }, [open, items.length]);

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (!open) return;
    if (event.key === 'Escape') { event.preventDefault(); close(); return; }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      const step = event.key === 'ArrowDown' ? 1 : -1;
      setActive((current) => (current + step + enabled.length) % enabled.length);
    }
    if (event.key === 'Home') { event.preventDefault(); setActive(0); }
    if (event.key === 'End') { event.preventDefault(); setActive(enabled.length - 1); }
  };

  return (
    <div ref={root} className="relative inline-flex" onKeyDown={onKeyDown}>
      <button
        ref={triggerRef}
        type="button"
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => { setActive(0); setOpen((value) => !value); }}
        className={cn(
          'inline-flex h-7 w-7 items-center justify-center rounded-md text-text-tertiary',
          'transition-colors hover:bg-surface-2 hover:text-text-primary',
          'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1',
          'focus-visible:outline-brand',
          open && 'bg-surface-2 text-text-primary',
        )}
      >
        {trigger ?? <MoreVertical className="h-4 w-4" />}
      </button>

      {open && (
        <div
          ref={panel}
          role="menu"
          aria-label={label}
          className={cn(
            'absolute z-30 min-w-[190px] overflow-hidden rounded-lg',
            'border border-[rgb(var(--border-line))] bg-surface-1 py-1 shadow-lg',
            align === 'end' ? 'right-0' : 'left-0',
            above ? 'bottom-full mb-1' : 'top-full mt-1',
          )}
        >
          {enabled.map((item, index) => (
            <button
              key={item.id}
              ref={(node) => { itemRefs.current[index] = node; }}
              type="button"
              role="menuitem"
              tabIndex={-1}
              onClick={() => { close(); item.onSelect(); }}
              onMouseEnter={() => setActive(index)}
              className={cn(
                'flex w-full items-start gap-2 px-3 py-1.5 text-left text-caption',
                'transition-colors focus:outline-none',
                item.destructive ? 'text-danger' : 'text-text-secondary',
                index === active && (item.destructive
                  ? 'bg-danger/10 text-danger'
                  : 'bg-surface-2 text-text-primary'),
              )}
            >
              {item.icon && <span className="text-text-quaternary">{item.icon}</span>}
              <span className="min-w-0 flex-1">
                <span className="block">{item.label}</span>
                {item.description && (
                  <span className="mt-0.5 block text-tiny leading-4 text-text-quaternary">
                    {item.description}
                  </span>
                )}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
