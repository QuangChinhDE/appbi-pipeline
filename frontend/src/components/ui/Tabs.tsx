'use client';

import * as React from 'react';

import { cn } from '@/lib/utils';

export interface TabItem {
  id: string;
  label: string;
  count?: number;
  disabled?: boolean;
}

export function Tabs({
  items, value, onChange, className,
}: {
  items: TabItem[];
  value: string;
  onChange: (id: string) => void;
  className?: string;
}) {
  return (
    <div
      role="tablist"
      className={cn('flex items-center gap-1 border-b border-[rgb(var(--border-line))]', className)}
    >
      {items.map((item) => {
        const active = item.id === value;
        return (
          <button
            key={item.id}
            role="tab"
            type="button"
            aria-selected={active}
            disabled={item.disabled}
            onClick={() => onChange(item.id)}
            className={cn(
              'relative -mb-px flex items-center gap-1.5 px-3 py-2 text-caption font-emphasis transition-colors',
              'border-b-2 disabled:opacity-40',
              active
                ? 'border-brand text-text-primary'
                : 'border-transparent text-text-tertiary hover:text-text-primary',
            )}
          >
            {item.label}
            {item.count !== undefined && (
              <span
                className={cn(
                  'rounded-full px-1.5 text-tiny',
                  active ? 'bg-brand/10 text-brand' : 'bg-surface-2 text-text-quaternary',
                )}
              >
                {item.count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}

export function SegmentedControl<T extends string>({
  options, value, onChange, size = 'sm',
}: {
  options: { value: T; label: string }[];
  value: T;
  onChange: (value: T) => void;
  size?: 'xs' | 'sm';
}) {
  return (
    <div className="inline-flex items-center rounded-md border border-[rgb(var(--border-strong))] bg-surface-1 p-0.5">
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          onClick={() => onChange(option.value)}
          aria-pressed={value === option.value}
          className={cn(
            'rounded-sm px-2.5 font-emphasis transition-colors',
            size === 'xs' ? 'h-6 text-tiny' : 'h-7 text-caption',
            value === option.value
              ? 'bg-surface-3 text-text-primary'
              : 'text-text-tertiary hover:text-text-primary',
          )}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}
