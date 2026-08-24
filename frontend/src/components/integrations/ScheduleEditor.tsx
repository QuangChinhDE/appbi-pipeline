'use client';

import * as React from 'react';
import { useQuery } from '@tanstack/react-query';
import { CalendarClock, Info } from 'lucide-react';

import { ApiError, pipelineApi } from '@/lib/api';
import { formatDateTime } from '@/lib/format';
import { useI18n } from '@/providers/LanguageProvider';
import { Input, Label, Select } from '@/components/ui/Input';
import type { ScheduleConfig } from '@/lib/types';
import { cn } from '@/lib/utils';

const INTERVAL_OPTIONS = [
  { seconds: 900, key: 'schedule.every15m' },
  { seconds: 1800, key: 'schedule.every30m' },
  { seconds: 3600, key: 'schedule.every1h' },
  { seconds: 10800, key: 'schedule.every3h' },
  { seconds: 21600, key: 'schedule.every6h' },
  { seconds: 43200, key: 'schedule.every12h' },
  { seconds: 86400, key: 'schedule.every24h' },
];

const TIMEZONES = [
  'Asia/Bangkok', 'Asia/Ho_Chi_Minh', 'Asia/Singapore', 'Asia/Tokyo',
  'Europe/London', 'Europe/Berlin', 'America/New_York', 'UTC',
];

/**
 * Product-owned schedule editor. Cron is available but gated behind an explicit
 * toggle, and every mode shows the next three fire times before you commit
 * (section 17.2).
 */
export function ScheduleEditor({
  value, onChange, allowCron = true,
}: {
  value: ScheduleConfig;
  onChange: (next: ScheduleConfig) => void;
  allowCron?: boolean;
}) {
  const { t, locale } = useI18n();

  const preview = useQuery({
    queryKey: ['schedule-preview', value],
    queryFn: () => pipelineApi.previewSchedule(value),
    enabled: value.type !== 'MANUAL',
    retry: false,
  });

  const set = (patch: Partial<ScheduleConfig>) => onChange({ ...value, ...patch });

  const types: { id: ScheduleConfig['type']; label: string; hint: string }[] = [
    { id: 'MANUAL', label: t('schedule.typeManual'), hint: t('schedule.typeManualHint') },
    { id: 'INTERVAL', label: t('schedule.typeInterval'), hint: t('schedule.typeIntervalHint') },
    { id: 'DAILY', label: t('schedule.typeDaily'), hint: t('schedule.typeDailyHint') },
    ...(allowCron
      ? [{ id: 'CRON' as const, label: t('schedule.typeCron'), hint: t('schedule.typeCronHint') }]
      : []),
  ];

  return (
    <div className="space-y-4">
      <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
        {types.map((type) => (
          <button
            key={type.id}
            type="button"
            aria-pressed={value.type === type.id}
            onClick={() => set({
              type: type.id,
              interval_seconds: type.id === 'INTERVAL' ? (value.interval_seconds ?? 3600) : null,
              time_of_day: type.id === 'DAILY' ? (value.time_of_day ?? '02:00') : null,
              cron_expression: type.id === 'CRON' ? (value.cron_expression ?? '0 2 * * *') : null,
            })}
            className={cn(
              'rounded-lg border p-3 text-left transition-colors',
              value.type === type.id
                ? 'border-brand bg-brand-soft/60'
                : 'border-[rgb(var(--border-line))] bg-surface-1 hover:bg-surface-2',
            )}
          >
            <span className="block text-caption font-strong text-text-primary">{type.label}</span>
            <span className="mt-0.5 block text-tiny leading-relaxed text-text-tertiary">
              {type.hint}
            </span>
          </button>
        ))}
      </div>

      {value.type !== 'MANUAL' && (
        <div className="grid gap-3 sm:grid-cols-2">
          {value.type === 'INTERVAL' && (
            <div>
              <Label htmlFor="sched-interval" required>{t('schedule.intervalLabel')}</Label>
              <Select
                id="sched-interval"
                value={String(value.interval_seconds ?? 3600)}
                onChange={(event) => set({ interval_seconds: Number(event.target.value) })}
              >
                {INTERVAL_OPTIONS.map((option) => (
                  <option key={option.seconds} value={option.seconds}>{t(option.key)}</option>
                ))}
              </Select>
            </div>
          )}

          {value.type === 'DAILY' && (
            <div>
              <Label htmlFor="sched-time" required>{t('schedule.timeLabel')}</Label>
              <Input
                id="sched-time"
                type="time"
                value={value.time_of_day ?? '02:00'}
                onChange={(event) => set({ time_of_day: event.target.value })}
              />
            </div>
          )}

          {value.type === 'CRON' && (
            <div className="sm:col-span-2">
              <Label htmlFor="sched-cron" required hint={t('schedule.cronHint')}>
                {t('schedule.cronLabel')}
              </Label>
              <Input
                id="sched-cron"
                className="font-mono"
                value={value.cron_expression ?? ''}
                placeholder="0 2 * * *"
                onChange={(event) => set({ cron_expression: event.target.value })}
              />
            </div>
          )}

          <div>
            <Label htmlFor="sched-tz" required>{t('schedule.timezoneLabel')}</Label>
            <Select
              id="sched-tz"
              value={value.timezone}
              onChange={(event) => set({ timezone: event.target.value })}
            >
              {TIMEZONES.map((zone) => <option key={zone} value={zone}>{zone}</option>)}
            </Select>
          </div>
        </div>
      )}

      {value.type !== 'MANUAL' && (
        <div className="rounded-lg border border-[rgb(var(--border-line))] bg-surface-2/50 p-3">
          <p className="flex items-center gap-1.5 text-caption font-emphasis text-text-secondary">
            <CalendarClock className="h-3.5 w-3.5" />
            {t('schedule.nextRuns')}
          </p>
          {preview.isError ? (
            <p className="mt-1.5 text-caption text-danger">
              {preview.error instanceof ApiError
                ? preview.error.message
                : t('schedule.invalid')}
            </p>
          ) : preview.isLoading ? (
            <p className="mt-1.5 text-caption text-text-quaternary">{t('common.loading')}</p>
          ) : (
            <>
              <p className="mt-0.5 text-tiny text-text-tertiary">{preview.data?.description}</p>
              <ul className="mt-1.5 space-y-0.5">
                {(preview.data?.next_runs ?? []).map((iso) => (
                  <li key={iso} className="font-mono text-tiny text-text-secondary">
                    {formatDateTime(iso, locale)}
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}

      {value.type !== 'MANUAL' && (
        <p className="flex items-start gap-1.5 text-tiny leading-relaxed text-text-quaternary">
          <Info className="mt-0.5 h-3 w-3 flex-shrink-0" />
          {t('schedule.overlapNote')}
        </p>
      )}
    </div>
  );
}
