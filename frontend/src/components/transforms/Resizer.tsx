'use client';

import * as React from 'react';

import { cn } from '@/lib/utils';

/**
 * A pane size the user chose, remembered per pane.
 *
 * How much room the SQL deserves against its results is not a property of the
 * app -- it depends on whether somebody is writing a long query or reading a
 * wide table, and that changes hour to hour. Fixed proportions force everyone
 * into one compromise, so the size is theirs to set and ours to remember.
 */
export function usePaneSize(key: string, initial: number, min: number, max: number) {
  const storageKey = `appbi.transform.size.${key}`;
  const [size, setSize] = React.useState(initial);

  React.useEffect(() => {
    try {
      const stored = window.localStorage.getItem(storageKey);
      if (stored === null) return;
      const value = Number(stored);
      if (Number.isFinite(value)) setSize(Math.min(Math.max(value, min), max));
    } catch {
      // Private windows and blocked site data both throw; the default stands.
    }
  }, [storageKey, min, max]);

  const commit = React.useCallback((value: number) => {
    const clamped = Math.min(Math.max(value, min), max);
    setSize(clamped);
    try {
      window.localStorage.setItem(storageKey, String(Math.round(clamped)));
    } catch {
      // Not being able to remember the choice is no reason to refuse it.
    }
  }, [storageKey, min, max]);

  return [size, commit] as const;
}

/**
 * The draggable seam between two panes.
 *
 * Kept as a real button so it is reachable by keyboard: arrow keys nudge, which
 * is the only way somebody not using a mouse can rebalance the panes at all.
 */
export function Resizer({
  orientation, value, onResize, ariaLabel, step = 24, invert = false, scaleFrom,
}: {
  /** `vertical` = a vertical seam between left/right panes, dragged on X. */
  orientation: 'vertical' | 'horizontal';
  /** The current size, so a drag can be applied to where it started. */
  value: number;
  /** Called with the new size. */
  onResize: (next: number) => void;
  ariaLabel: string;
  step?: number;
  /** True when dragging toward the seam should shrink rather than grow. */
  invert?: boolean;
  /**
   * Set when `value` is a percentage rather than pixels: the drag distance is
   * divided by this element's size to convert one into the other.
   */
  scaleFrom?: React.RefObject<HTMLElement | null>;
}) {
  const dragging = React.useRef(false);
  const origin = React.useRef(0);
  const start = React.useRef(value);
  const latest = React.useRef({ onResize, invert, scaleFrom });
  latest.current = { onResize, invert, scaleFrom };

  React.useEffect(() => {
    const move = (event: PointerEvent) => {
      if (!dragging.current) return;
      event.preventDefault();
      const position = orientation === 'vertical' ? event.clientX : event.clientY;
      let delta = position - origin.current;
      const host = latest.current.scaleFrom?.current;
      if (host) {
        const span = orientation === 'vertical'
          ? host.getBoundingClientRect().width
          : host.getBoundingClientRect().height;
        if (span > 0) delta = delta * 100 / span;
      }
      latest.current.onResize(start.current + (latest.current.invert ? -delta : delta));
    };
    const stop = () => {
      if (!dragging.current) return;
      dragging.current = false;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', stop);
    window.addEventListener('pointercancel', stop);
    return () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', stop);
      window.removeEventListener('pointercancel', stop);
    };
  }, [orientation]);

  return (
    <button
      type="button"
      aria-label={ariaLabel}
      title={ariaLabel}
      onPointerDown={(event) => {
        dragging.current = true;
        origin.current = orientation === 'vertical' ? event.clientX : event.clientY;
        start.current = value;
        // Without these the pointer turns into a text caret mid-drag and the
        // page selects whatever it passes over.
        document.body.style.cursor = orientation === 'vertical' ? 'col-resize' : 'row-resize';
        document.body.style.userSelect = 'none';
      }}
      onKeyDown={(event) => {
        const back = event.key === 'ArrowLeft' || event.key === 'ArrowUp';
        const forward = event.key === 'ArrowRight' || event.key === 'ArrowDown';
        if (!back && !forward) return;
        event.preventDefault();
        const direction = back ? -step : step;
        onResize(value + (invert ? -direction : direction));
      }}
      className={cn(
        'group relative shrink-0 bg-transparent transition-colors',
        // A 1px seam is impossible to grab, so the hit area is wider than the
        // line people actually see.
        orientation === 'vertical'
          ? 'w-1 cursor-col-resize hover:bg-brand/30'
          : 'h-1 cursor-row-resize hover:bg-brand/30',
        'focus-visible:bg-brand/50 focus-visible:outline-none',
      )}
    >
      <span
        aria-hidden
        className={cn(
          'absolute',
          orientation === 'vertical'
            ? '-left-1 -right-1 inset-y-0'
            : '-top-1 -bottom-1 inset-x-0',
        )}
      />
    </button>
  );
}
