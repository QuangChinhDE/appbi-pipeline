'use client';

/**
 * A draggable divider between two panes.
 *
 * Kept from V1 essentially unchanged -- pane sizing is not what the rework is
 * about, and the behaviour was already right: pointer capture so a fast drag
 * does not lose the handle, and keyboard arrows so the layout is reachable
 * without a mouse.
 */

import * as React from 'react';

import { cn } from '@/lib/utils';

interface ResizerProps {
  orientation?: 'vertical' | 'horizontal';
  /** Current size of the pane before the divider, in pixels. */
  value: number;
  onChange: (value: number) => void;
  min?: number;
  max?: number;
  className?: string;
  label?: string;
}

export function Resizer({
  orientation = 'vertical',
  value,
  onChange,
  min = 160,
  max = 900,
  className,
  label,
}: ResizerProps) {
  const dragging = React.useRef(false);
  const origin = React.useRef({ position: 0, value: 0 });

  const clamp = React.useCallback(
    (next: number) => Math.min(Math.max(next, min), max),
    [min, max],
  );

  const onPointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    dragging.current = true;
    origin.current = {
      position: orientation === 'vertical' ? event.clientX : event.clientY,
      value,
    };
    // Capture, so dragging quickly past the handle keeps sending events here
    // rather than to whatever is now under the cursor.
    event.currentTarget.setPointerCapture(event.pointerId);
  };

  const onPointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!dragging.current) return;
    const position = orientation === 'vertical' ? event.clientX : event.clientY;
    onChange(clamp(origin.current.value + (position - origin.current.position)));
  };

  const onPointerUp = (event: React.PointerEvent<HTMLDivElement>) => {
    dragging.current = false;
    event.currentTarget.releasePointerCapture(event.pointerId);
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    const step = event.shiftKey ? 48 : 12;
    const decrease = orientation === 'vertical' ? 'ArrowLeft' : 'ArrowUp';
    const increase = orientation === 'vertical' ? 'ArrowRight' : 'ArrowDown';
    if (event.key === decrease) {
      event.preventDefault();
      onChange(clamp(value - step));
    } else if (event.key === increase) {
      event.preventDefault();
      onChange(clamp(value + step));
    }
  };

  return (
    <div
      role="separator"
      aria-orientation={orientation}
      aria-label={label ?? 'Resize panel'}
      aria-valuenow={value}
      aria-valuemin={min}
      aria-valuemax={max}
      tabIndex={0}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onKeyDown={onKeyDown}
      className={cn(
        'group relative shrink-0 touch-none',
        'focus-visible:outline-none',
        orientation === 'vertical'
          ? 'w-px cursor-col-resize hover:w-px'
          : 'h-px cursor-row-resize',
        'bg-[rgb(var(--border-line))]',
        className,
      )}
    >
      {/* A 1px target is unhittable; this widens the hit area without
          widening the line. */}
      <span
        aria-hidden
        className={cn(
          'absolute z-10 transition-colors',
          orientation === 'vertical'
            ? '-left-1.5 -right-1.5 top-0 bottom-0'
            : '-top-1.5 -bottom-1.5 left-0 right-0',
          'group-hover:bg-brand/30 group-focus-visible:bg-brand/50',
        )}
      />
    </div>
  );
}
