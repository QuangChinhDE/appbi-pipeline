'use client';

import * as React from 'react';
import Link from 'next/link';
import { ArrowLeft, Search } from 'lucide-react';

import { Input } from '@/components/ui/Input';
import { cn } from '@/lib/utils';
import { useI18n } from '@/providers/LanguageProvider';

/** List page skeleton shared by every module (section 7.3). */
export function PageListLayout({
  title, description, overview, action, searchable = true, searchPlaceholder,
  searchValue, onSearchChange, filters, children,
}: {
  title: string;
  description?: React.ReactNode;
  overview?: React.ReactNode;
  action?: React.ReactNode;
  searchable?: boolean;
  searchPlaceholder?: string;
  searchValue?: string;
  onSearchChange?: (value: string) => void;
  filters?: React.ReactNode;
  children: React.ReactNode;
}) {
  const { t } = useI18n();
  return (
    <div className="flex h-full flex-col px-4 pt-5 sm:px-6 xl:px-8">
      <header className="mb-3.5 shrink-0">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between sm:gap-4">
          <div className="min-w-0">
            <h1 className="text-h3 font-strong text-text-primary">{title}</h1>
            {description && (
              <p className="mt-1 max-w-2xl text-caption text-text-tertiary">{description}</p>
            )}
          </div>
          {action && <div className="flex-shrink-0">{action}</div>}
        </div>
      </header>

      {overview && <div className="mb-3 shrink-0">{overview}</div>}

      {(searchable || filters) && (
        <div className="mb-3 flex shrink-0 flex-col gap-2.5 lg:flex-row lg:items-center">
          {searchable && (
            <div className="w-full lg:max-w-xs">
              <Input
                size="sm"
                value={searchValue ?? ''}
                onChange={(event) => onSearchChange?.(event.target.value)}
                placeholder={searchPlaceholder ?? t('common.search')}
                aria-label={searchPlaceholder ?? t('common.searchLabel')}
                leadingIcon={<Search />}
              />
            </div>
          )}
          {filters && <div className="flex flex-wrap items-center gap-2">{filters}</div>}
        </div>
      )}

      <div className="min-h-0 flex-1 pb-8 [scrollbar-gutter:stable]">{children}</div>
    </div>
  );
}

/** Compact stats strip, same shape as AppBI's ModuleOverview. */
export function ModuleOverview({
  stats,
}: {
  stats: { label: string; value: React.ReactNode; helper?: string; tone?: 'default' | 'danger' | 'warning' | 'success' }[];
}) {
  if (stats.length === 0) return null;
  const tones = {
    default: 'text-text-primary',
    danger: 'text-danger',
    warning: 'text-warning',
    success: 'text-success',
  } as const;
  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-1 rounded-lg border border-[rgb(var(--border-line))] bg-surface-1 px-3 py-1.5">
      {stats.map((stat) => (
        <div key={stat.label} className="flex items-baseline gap-1.5" title={stat.helper}>
          <span className={cn('text-small font-strong', tones[stat.tone ?? 'default'])}>
            {stat.value}
          </span>
          <span className="text-tiny uppercase tracking-[0.08em] text-text-tertiary">
            {stat.label}
          </span>
        </div>
      ))}
    </div>
  );
}

/** Detail page header: back link, title, badges, action group. */
export function DetailHeader({
  backHref, backLabel, icon, title, subtitle, badges, badgesInline = false, actions,
}: {
  backHref: string;
  backLabel: string;
  icon?: React.ReactNode;
  /** Usually the record name; accepts an element so a page can put an
   *  inline control -- a rename pencil -- beside it. */
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  badges?: React.ReactNode;
  /** Keep status beside the title on dense operational screens. */
  badgesInline?: boolean;
  actions?: React.ReactNode;
}) {
  return (
    <div className="border-b border-[rgb(var(--border-line))] bg-surface-1 px-4 pb-4 pt-5 sm:px-6 xl:px-8">
      <Link
        href={backHref}
        className="mb-1 -ml-1 inline-flex items-center gap-1 rounded px-1 py-1.5 text-caption text-text-tertiary transition-colors hover:text-text-primary"
      >
        <ArrowLeft className="h-3.5 w-3.5" />
        {backLabel}
      </Link>
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <div className="flex min-w-0 flex-wrap items-center gap-2.5">
            {icon}
            <h1 className="truncate text-h3 font-strong text-text-primary">{title}</h1>
            {badgesInline && badges && (
              <div className="flex flex-wrap items-center gap-1.5">{badges}</div>
            )}
          </div>
          {(subtitle || (!badgesInline && badges)) && (
            <div className="mt-1.5 flex flex-wrap items-center gap-2">
              {subtitle}
              {!badgesInline && badges}
            </div>
          )}
        </div>
        {actions && <div className="flex flex-shrink-0 flex-wrap items-center gap-2">{actions}</div>}
      </div>
    </div>
  );
}

export function DetailBody({ children }: { children: React.ReactNode }) {
  // A flex column that takes the height left over, so a page that wants to
  // reach the bottom -- a wizard with its buttons at the foot -- can ask for it
  // with flex-1. Content that does not ask keeps its natural height and sits at
  // the top exactly as before.
  return (
    <div className="flex min-h-0 flex-1 flex-col px-4 py-5 sm:px-6 xl:px-8">
      {children}
    </div>
  );
}

export function Card({
  title, description, action, children, className, padded = true,
}: {
  title?: React.ReactNode;
  description?: React.ReactNode;
  action?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  padded?: boolean;
}) {
  return (
    <section
      className={cn(
        'overflow-hidden rounded-lg border border-[rgb(var(--border-line))] bg-surface-1',
        className,
      )}
    >
      {(title || action) && (
        <div className="flex items-start justify-between gap-3 border-b border-[rgb(var(--border-line))] px-4 py-2.5">
          <div className="min-w-0">
            {title && (
              <h2 className="text-caption font-strong text-text-primary">{title}</h2>
            )}
            {description && (
              <p className="mt-0.5 text-tiny text-text-tertiary">{description}</p>
            )}
          </div>
          {action}
        </div>
      )}
      <div className={cn(padded && 'p-4')}>{children}</div>
    </section>
  );
}

export function StatTile({
  label, value, helper, tone = 'default', icon,
}: {
  label: string;
  value: React.ReactNode;
  helper?: React.ReactNode;
  tone?: 'default' | 'danger' | 'warning' | 'success' | 'info';
  icon?: React.ReactNode;
}) {
  const tones = {
    default: 'text-text-primary',
    danger: 'text-danger',
    warning: 'text-warning',
    success: 'text-success',
    info: 'text-info',
  } as const;
  return (
    <div className="flex h-full flex-col rounded-lg border border-[rgb(var(--border-line))] bg-surface-1 px-3.5 py-3">
      <div className="flex items-center justify-between gap-2">
        <p className="text-tiny uppercase tracking-[0.08em] text-text-tertiary">{label}</p>
        {icon && <span className="text-text-quaternary">{icon}</span>}
      </div>
      <p className={cn('mt-1 text-h2 font-emphasis tabular-nums', tones[tone])}>{value}</p>
      {helper && <p className="mt-0.5 text-tiny text-text-quaternary">{helper}</p>}
    </div>
  );
}
