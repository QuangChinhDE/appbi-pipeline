/** Display formatting. Storage is always UTC; rendering follows the locale. */

export type Locale = 'vi' | 'en';

export function formatDateTime(value: string | null | undefined, locale: Locale = 'vi'): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return new Intl.DateTimeFormat(locale === 'vi' ? 'vi-VN' : 'en-GB', {
    day: '2-digit', month: '2-digit', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  }).format(date);
}

export function formatTime(value: string | null | undefined, locale: Locale = 'vi'): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return new Intl.DateTimeFormat(locale === 'vi' ? 'vi-VN' : 'en-GB', {
    hour: '2-digit', minute: '2-digit',
  }).format(date);
}

const RELATIVE_UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ['year', 31536000], ['month', 2592000], ['day', 86400],
  ['hour', 3600], ['minute', 60], ['second', 1],
];

export function formatRelative(value: string | null | undefined, locale: Locale = 'vi'): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  const deltaSeconds = (date.getTime() - Date.now()) / 1000;
  const formatter = new Intl.RelativeTimeFormat(locale === 'vi' ? 'vi' : 'en', { numeric: 'auto' });
  for (const [unit, seconds] of RELATIVE_UNITS) {
    if (Math.abs(deltaSeconds) >= seconds || unit === 'second') {
      return formatter.format(Math.round(deltaSeconds / seconds), unit);
    }
  }
  return '—';
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return '—';
  if (seconds < 1) return '<1s';
  const total = Math.round(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  if (hours > 0) return `${hours}h ${minutes}m`;
  if (minutes > 0) return `${minutes}m ${secs}s`;
  return `${secs}s`;
}

export function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—';
  return new Intl.NumberFormat('en-US').format(value);
}

export function formatBytes(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—';
  if (value === 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const exponent = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  return `${(value / 1024 ** exponent).toFixed(exponent === 0 ? 0 : 1)} ${units[exponent]}`;
}

export function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—';
  return `${value.toFixed(1)}%`;
}

export function describeSchedule(
  schedule: { type: string; interval_seconds?: number | null; time_of_day?: string | null; cron_expression?: string | null },
  t: (key: string, vars?: Record<string, string | number>) => string,
): string {
  switch (schedule.type) {
    case 'MANUAL':
      return t('schedule.manual');
    case 'INTERVAL': {
      const seconds = schedule.interval_seconds ?? 0;
      if (seconds % 86400 === 0) return t('schedule.everyDays', { n: seconds / 86400 });
      if (seconds % 3600 === 0) return t('schedule.everyHours', { n: seconds / 3600 });
      return t('schedule.everyMinutes', { n: Math.max(1, Math.round(seconds / 60)) });
    }
    case 'DAILY':
      return t('schedule.dailyAt', { time: schedule.time_of_day ?? '02:00' });
    case 'CRON':
      return `${t('schedule.typeCron')} · ${schedule.cron_expression ?? ''}`;
    default:
      return schedule.type;
  }
}


/**
 * Which support badge a connector earns. Kept in one place because "community"
 * is wrong for a connector the workspace built itself, and that mistake is easy
 * to reintroduce at each call site.
 */
export function supportLevelKey(level: string): string {
  if (level === 'certified') return 'connectors.supportCertified';
  if (level === 'workspace') return 'connectors.supportWorkspace';
  return 'connectors.supportCommunity';
}
