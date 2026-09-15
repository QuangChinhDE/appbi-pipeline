'use client';

/**
 * The pieces the Data Health page is built from.
 *
 * Kept out of the page so the page reads as a layout rather than a wall, and
 * because two of these — the issue card and the sparkline — carry enough
 * judgement to be worth naming.
 */

import * as React from 'react';
import Link from 'next/link';
import {
  AlertTriangle, ArrowRight, CheckCircle2, ChevronDown, Clock, Info, TrendingDown,
} from 'lucide-react';

import { cn } from '@/lib/utils';
import { useI18n } from '@/providers/LanguageProvider';
import { useWorkspacePath } from '@/hooks/use-workspace-path';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import type {
  CauseShare, FreshnessRow, HealthIssue, HealthMetric, ReliabilityDay,
} from '@/lib/types';

const SEVERITY_TONE = {
  CRITICAL: 'border-danger/40 bg-danger/5',
  WARNING: 'border-warning/40 bg-warning/5',
  INFO: 'border-[rgb(var(--border-line))] bg-surface-2',
} as const;

const SEVERITY_ICON = {
  CRITICAL: AlertTriangle,
  WARNING: AlertTriangle,
  INFO: Info,
} as const;

/** Seconds as something a person says out loud: `2h 14m`, `35m`, `4d`. */
export function spanOf(seconds: number, t: (k: string, v?: Record<string, string | number>) => string): string {
  const abs = Math.max(0, Math.round(seconds));
  const d = Math.floor(abs / 86400);
  const h = Math.floor((abs % 86400) / 3600);
  const m = Math.floor((abs % 3600) / 60);
  if (d > 0) return t('health.span.dh', { d, h });
  if (h > 0) return t('health.span.hm', { h, m });
  return t('health.span.m', { m: Math.max(1, m) });
}

/**
 * One problem, with everything needed to act on it.
 *
 * The four lines are deliberate and in this order: what happened, why, what it
 * costs, what it rests on. An engineer reads down from the top and an analyst
 * starts at the impact — the same card serves both, which is why there is one
 * card and not a dashboard per role.
 */
export function IssueCard({ issue }: { issue: HealthIssue }) {
  const { t, tf } = useI18n();
  const ws = useWorkspacePath();
  const [open, setOpen] = React.useState(false);

  /**
   * The server states durations in seconds, which is the right way to send a
   * fact and the wrong way to say one. Any `seconds` variable becomes a `span`
   * -- "1h 30m" -- before it is substituted into a sentence.
   */
  const spoken = React.useCallback((vars: Record<string, unknown>) => {
    const out: Record<string, unknown> = { ...vars };
    for (const [name, value] of Object.entries(vars)) {
      if (typeof value === 'number' && /seconds?$/i.test(name)) {
        out[name.replace(/seconds?$/i, 'span')] = spanOf(value, t);
      }
    }
    return out as Record<string, string | number>;
  }, [t]);
  const Icon = SEVERITY_ICON[issue.severity] ?? Info;
  const hidden = issue.affected_total - issue.affected.length;

  return (
    <div className={cn('rounded-lg border p-3', SEVERITY_TONE[issue.severity])}>
      <div className="flex flex-wrap items-start gap-x-3 gap-y-2">
        <Icon
          className={cn(
            'mt-0.5 h-4 w-4 flex-shrink-0',
            issue.severity === 'CRITICAL' ? 'text-danger'
              : issue.severity === 'WARNING' ? 'text-warning' : 'text-text-quaternary',
          )}
          aria-hidden
        />
        <div className="min-w-0 flex-1 basis-64">
          <p className="text-caption font-emphasis text-text-primary">
            {tf([issue.title_code], issue.title_code, issue.title_vars)}
          </p>

          {issue.cause_code && (
            <p className="mt-1 text-tiny leading-relaxed text-text-secondary">
              {tf([issue.cause_code], '', spoken(issue.cause_vars))}
            </p>
          )}
          {issue.impact_code && (
            <p className="mt-0.5 text-tiny leading-relaxed text-text-secondary">
              {tf([issue.impact_code], '', spoken(issue.impact_vars))}
            </p>
          )}

          {issue.affected.length > 0 && (
            <div className="mt-1.5 flex flex-wrap items-center gap-1">
              {issue.affected.map((item) => (
                item.href ? (
                  <Link key={item.id ?? item.name} href={ws(item.href)}>
                    <Badge variant="subtle" size="xs">{item.name}</Badge>
                  </Link>
                ) : (
                  <Badge key={item.name} variant="subtle" size="xs">{item.name}</Badge>
                )
              ))}
              {hidden > 0 && (
                <span className="text-tiny text-text-quaternary">
                  {t('health.andMore', { n: hidden })}
                </span>
              )}
            </div>
          )}
        </div>

        <div className="flex flex-shrink-0 flex-wrap items-center gap-1.5">
          {issue.action_href && (
            <Link href={ws(issue.action_href)}>
              <Button size="xs" variant={issue.severity === 'CRITICAL' ? 'primary' : 'secondary'}>
                {issue.action_code
                  ? tf([`action.${issue.action_code}`], t('health.investigate'))
                  : t('health.investigate')}
                <ArrowRight className="ml-1 h-3 w-3" />
              </Button>
            </Link>
          )}
          {issue.evidence_code && (
            <button
              type="button"
              aria-expanded={open}
              onClick={() => setOpen((v) => !v)}
              className="inline-flex items-center gap-1 rounded px-1.5 py-1 text-tiny text-text-tertiary hover:text-text-primary"
            >
              {t('health.evidence')}
              <ChevronDown className={cn('h-3 w-3 transition-transform', open && 'rotate-180')} />
            </button>
          )}
        </div>
      </div>

      {open && issue.evidence_code && (
        <p className="mt-2 border-t border-[rgb(var(--border-line))] pt-2 text-tiny leading-relaxed text-text-tertiary">
          {tf([issue.evidence_code], '', spoken(issue.evidence_vars))}
          {issue.occurrence_count > 1 && (
            <> · {t('health.occurrences', { n: issue.occurrence_count })}</>
          )}
        </p>
      )}
    </div>
  );
}

/** One of the four numbers, with the direction it moved. */
export function MetricTile({ metric }: { metric: HealthMetric }) {
  const { t } = useI18n();
  const tone = {
    good: 'text-success', warn: 'text-warning', bad: 'text-danger',
    neutral: 'text-text-primary',
  }[metric.tone];

  const shown = metric.value === null
    ? '—'
    : metric.unit === 'percent' ? `${metric.value}%`
      : metric.unit === 'records' ? compact(metric.value)
        : String(metric.value);

  return (
    <div className="flex h-full flex-col rounded-lg border border-[rgb(var(--border-line))] bg-surface-1 px-3.5 py-3">
      <p className="text-tiny uppercase tracking-[0.08em] text-text-tertiary">
        {t(`health.metric.${metric.key}`)}
      </p>
      <p className={cn('mt-1 text-h2 font-emphasis tabular-nums', tone)}>{shown}</p>
      {/* A delta is only shown when there is one. Below a tenth of a point the
          movement is noise, and "0% vs before" reads as a measurement when it
          is really the absence of one. The server sends a delta only for
          metrics where up is the good direction, which is why the colour can
          come from the sign. */}
      {metric.delta !== null && Math.abs(metric.delta) >= 0.1 && (
        <p className={cn(
          'mt-0.5 text-tiny tabular-nums',
          metric.delta > 0 ? 'text-success' : 'text-danger',
        )}>
          {metric.delta > 0 ? '↑' : '↓'}
          {' '}{Math.abs(metric.delta)}{metric.unit === 'percent' ? ' pt' : '%'}
          {' '}{t('health.vsBefore')}
        </p>
      )}
    </div>
  );
}

function compact(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return String(Math.round(value));
}

/**
 * Seven days of outcomes as a shape.
 *
 * A single "95% this week" cannot say whether that was one bad afternoon or a
 * slow decline, and those call for different responses.
 */
export function ReliabilityChart({ days }: { days: ReliabilityDay[] }) {
  const { t } = useI18n();
  const rates = days.map((day) => {
    const total = day.succeeded + day.failed;
    return total ? day.succeeded / total : null;
  });
  const known = rates.filter((r): r is number => r !== null);
  /**
   * Where the axis starts, as a decision rather than a calculation.
   *
   * Zoom in only when there is nothing to see. A fleet that ran between 97 and
   * 100 all week is flat on a 0-100 axis, and the variation is the reason
   * anyone opened the chart -- so that week is drawn 90-100.
   *
   * The moment any day falls below 90 the axis goes back to zero and the shape
   * is the honest one. The earlier version took the minimum of the data
   * itself, which pinned the worst day of the week to no height at all: a drop
   * to 69% drew as a five-pixel stub, the smallest mark on a chart whose
   * entire job is to show when things turned.
   */
  const floor = known.length && Math.min(...known) < 0.9 ? 0 : 0.9;
  const PLOT = 76;

  return (
    <div className="flex items-end gap-1" role="img"
         aria-label={t('health.reliabilityChart')}>
      {days.map((day, index) => {
        const rate = rates[index];
        const total = day.succeeded + day.failed;
        const height = rate === null
          ? 3
          : Math.max(5, Math.round(((rate - floor) / (1 - floor)) * PLOT));
        return (
          <div key={day.date} className="flex flex-1 flex-col items-center gap-1">
            <span className="text-[10px] tabular-nums text-text-quaternary">
              {total ? `${Math.round(rate! * 100)}%` : ''}
            </span>
            <div
              className={cn(
                'w-full rounded-t',
                rate === null ? 'bg-surface-3'
                  : day.failed === 0 ? 'bg-success/70'
                    : rate >= 0.9 ? 'bg-warning/70' : 'bg-danger/70',
              )}
              style={{ height: `${height}px` }}
              title={`${day.date}: ${day.succeeded}/${total}`}
            />
            <span className="text-[10px] text-text-quaternary">{day.date.slice(5)}</span>
          </div>
        );
      })}
    </div>
  );
}

/** What the week's failures had in common. */
export function CauseBars({ causes }: { causes: CauseShare[] }) {
  const { t, tf } = useI18n();
  if (causes.length === 0) {
    return <p className="text-tiny text-text-quaternary">{t('health.noFailures')}</p>;
  }
  return (
    <ul className="space-y-1.5">
      {causes.map((cause) => (
        <li key={cause.cause} className="flex items-center gap-2">
          <span className="w-32 flex-shrink-0 truncate text-tiny text-text-secondary">
            {tf([`error.category.${cause.cause}`], cause.cause)}
          </span>
          <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-surface-3">
            <span className="block h-full rounded-full bg-brand"
                  style={{ width: `${Math.round(cause.share * 100)}%` }} />
          </span>
          <span className="w-10 flex-shrink-0 text-right text-tiny tabular-nums text-text-tertiary">
            {Math.round(cause.share * 100)}%
          </span>
        </li>
      ))}
    </ul>
  );
}

/** Which data is late, worst first. */
export function FreshnessList({ rows }: { rows: FreshnessRow[] }) {
  const { t } = useI18n();
  const ws = useWorkspacePath();
  const scheduled = rows.filter((row) => row.state === 'late' || row.state === 'on_time');

  if (scheduled.length === 0) {
    return <p className="text-tiny text-text-quaternary">{t('health.noSchedules')}</p>;
  }
  return (
    <ul className="space-y-1.5">
      {scheduled.slice(0, 6).map((row) => (
        <li key={row.pipeline_id} className="flex items-center gap-2">
          <span className={cn(
            'h-1.5 w-1.5 flex-shrink-0 rounded-full',
            row.state === 'late' ? 'bg-danger' : 'bg-success',
          )} />
          <Link
            href={ws(`/pipelines/${row.pipeline_id}?tab=status`)}
            className="min-w-0 flex-1 truncate text-caption text-text-secondary hover:text-text-primary"
          >
            {row.name}
          </Link>
          <span className={cn(
            'flex-shrink-0 text-tiny tabular-nums',
            row.state === 'late' ? 'text-danger' : 'text-text-quaternary',
          )}>
            {row.state === 'late' && row.late_seconds
              ? `+${spanOf(row.late_seconds, t)}`
              : t('health.onTime')}
          </span>
        </li>
      ))}
    </ul>
  );
}

/** A run that succeeded and still went wrong. */
export function AnomalyList({
  rows, labelKey, icon,
}: {
  rows: { pipeline_id: string; name: string; change: number; run_id: string | null }[];
  labelKey: string;
  icon?: React.ReactNode;
}) {
  const { t } = useI18n();
  const ws = useWorkspacePath();
  if (rows.length === 0) {
    return <p className="text-tiny text-text-quaternary">{t(`${labelKey}.none`)}</p>;
  }
  return (
    <ul className="space-y-1.5">
      {rows.map((row) => (
        <li key={row.pipeline_id} className="flex items-center gap-2">
          {icon ?? <TrendingDown className="h-3 w-3 flex-shrink-0 text-warning" />}
          <Link
            href={ws(row.run_id ? `/runs/${row.run_id}` : `/pipelines/${row.pipeline_id}?tab=jobs`)}
            className="min-w-0 flex-1 truncate text-caption text-text-secondary hover:text-text-primary"
          >
            {row.name}
          </Link>
          <span className="flex-shrink-0 text-tiny tabular-nums text-warning">
            {row.change > 0 ? '+' : ''}{Math.round(row.change * 100)}%
          </span>
        </li>
      ))}
    </ul>
  );
}

export { AlertTriangle, CheckCircle2, Clock };
