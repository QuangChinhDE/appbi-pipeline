'use client';

import * as React from 'react';

import { cn } from '@/lib/utils';

export type BadgeVariant =
  | 'neutral' | 'subtle' | 'brand' | 'success' | 'warning' | 'danger' | 'info' | 'outline';
type BadgeSize = 'xs' | 'sm' | 'md';

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  variant?: BadgeVariant;
  size?: BadgeSize;
  pill?: boolean;
  dot?: boolean;
}

const sizes: Record<BadgeSize, string> = {
  xs: 'text-tiny px-1.5 h-4 gap-1',
  sm: 'text-tiny px-2 h-5 gap-1',
  md: 'text-label px-2.5 h-6 gap-1.5',
};

const variants: Record<BadgeVariant, string> = {
  neutral: 'bg-surface-2 text-text-secondary border border-[rgb(var(--border-line))]',
  subtle: 'bg-surface-2 text-text-tertiary',
  brand: 'bg-brand/10 text-brand',
  success: 'bg-success/10 text-success',
  warning: 'bg-warning/10 text-warning',
  danger: 'bg-danger/10 text-danger',
  info: 'bg-info/10 text-info',
  outline: 'bg-transparent text-text-secondary border border-[rgb(var(--border-strong))]',
};

const dotColors: Record<BadgeVariant, string> = {
  neutral: 'bg-text-quaternary',
  subtle: 'bg-text-quaternary',
  brand: 'bg-brand',
  success: 'bg-success',
  warning: 'bg-warning',
  danger: 'bg-danger',
  info: 'bg-info',
  outline: 'bg-text-quaternary',
};

export const Badge = React.forwardRef<HTMLSpanElement, BadgeProps>(function Badge(
  { className, variant = 'neutral', size = 'sm', pill = true, dot, children, ...props },
  ref,
) {
  return (
    <span
      ref={ref}
      className={cn(
        'inline-flex items-center font-emphasis whitespace-nowrap',
        pill ? 'rounded-full' : 'rounded-sm',
        sizes[size],
        variants[variant],
        className,
      )}
      {...props}
    >
      {dot && (
        <span className={cn('inline-block h-1.5 w-1.5 rounded-full', dotColors[variant])} aria-hidden />
      )}
      {children}
    </span>
  );
});

export function StatusDot({
  variant = 'neutral',
  pulse,
  className,
  ...props
}: { variant?: BadgeVariant; pulse?: boolean } & React.HTMLAttributes<HTMLSpanElement>) {
  return (
    <span className={cn('relative inline-flex h-2 w-2', className)} {...props}>
      {pulse && (
        <span
          className={cn('absolute inline-flex h-full w-full animate-ping rounded-full opacity-60',
            dotColors[variant])}
          aria-hidden
        />
      )}
      <span className={cn('relative inline-flex h-2 w-2 rounded-full', dotColors[variant])} />
    </span>
  );
}

export function Kbd({ children, className, ...props }: React.HTMLAttributes<HTMLElement>) {
  return (
    <kbd
      className={cn(
        'inline-flex h-5 min-w-[1.25rem] items-center justify-center px-1',
        'rounded-sm text-tiny font-mono font-emphasis',
        'bg-surface-2 text-text-tertiary border border-[rgb(var(--border-line))]',
        className,
      )}
      {...props}
    >
      {children}
    </kbd>
  );
}
