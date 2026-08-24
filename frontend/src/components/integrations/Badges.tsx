'use client';

import * as React from 'react';
import {
  AlertTriangle, Ban, CheckCircle2, CircleDashed, Clock, Loader2, PauseCircle, XCircle,
} from 'lucide-react';

import { Badge, type BadgeVariant } from '@/components/ui/Badge';
import type { HealthBlock } from '@/lib/types';
import { cn } from '@/lib/utils';
import { useI18n } from '@/providers/LanguageProvider';

/**
 * Status is never colour-only (section 38): every badge pairs its colour with an
 * icon and a translated label. Labels come from the catalog, with the server's
 * own wording as the last resort so a value the catalog has not learned yet
 * still reads as words.
 */

const HEALTH_STYLE: Record<string, { variant: BadgeVariant; Icon: React.ElementType }> = {
  HEALTHY: { variant: 'success', Icon: CheckCircle2 },
  RUNNING: { variant: 'info', Icon: Loader2 },
  WARNING: { variant: 'warning', Icon: AlertTriangle },
  ERROR: { variant: 'danger', Icon: XCircle },
  UNKNOWN: { variant: 'neutral', Icon: CircleDashed },
};

export function HealthBadge({
  health, size = 'sm',
}: {
  health: HealthBlock;
  size?: 'xs' | 'sm' | 'md';
}) {
  const { tf } = useI18n();
  const style = HEALTH_STYLE[health.level] ?? HEALTH_STYLE.UNKNOWN;
  const spinning = health.code === 'RUNNING';
  // The code is the specific state (NEVER_RUN, PAUSED, ...); the level is the
  // coarse bucket. Try the specific one first.
  const label = tf(
    [`health.${health.code}`, `health.${health.level}`],
    health.label,
  );
  return (
    <Badge variant={style.variant} size={size} title={health.message ?? undefined}>
      <style.Icon className={cn('h-3 w-3', spinning && 'animate-spin')} aria-hidden />
      {label}
    </Badge>
  );
}

const RUN_STYLE: Record<string, { variant: BadgeVariant; Icon: React.ElementType }> = {
  QUEUED: { variant: 'neutral', Icon: Clock },
  STARTING: { variant: 'info', Icon: Loader2 },
  RUNNING: { variant: 'info', Icon: Loader2 },
  SUCCEEDED: { variant: 'success', Icon: CheckCircle2 },
  FAILED: { variant: 'danger', Icon: XCircle },
  FAILED_TO_START: { variant: 'danger', Icon: XCircle },
  CANCEL_REQUESTED: { variant: 'warning', Icon: Loader2 },
  CANCELLED: { variant: 'neutral', Icon: Ban },
  TIMED_OUT: { variant: 'warning', Icon: AlertTriangle },
};

export function RunStatusBadge({
  status, size = 'sm',
}: {
  status: string;
  size?: 'xs' | 'sm' | 'md';
}) {
  const { tf } = useI18n();
  const style = RUN_STYLE[status] ?? { variant: 'neutral' as BadgeVariant, Icon: CircleDashed };
  const spinning = ['STARTING', 'RUNNING', 'CANCEL_REQUESTED'].includes(status);
  return (
    <Badge variant={style.variant} size={size}>
      <style.Icon className={cn('h-3 w-3', spinning && 'animate-spin')} aria-hidden />
      {tf([`run.${status}`], status)}
    </Badge>
  );
}

const PIPELINE_STATUS: Record<string, { variant: BadgeVariant; Icon: React.ElementType }> = {
  ACTIVE: { variant: 'success', Icon: CheckCircle2 },
  PAUSED: { variant: 'neutral', Icon: PauseCircle },
  NEEDS_REVIEW: { variant: 'warning', Icon: AlertTriangle },
  DELETE_PENDING: { variant: 'danger', Icon: Ban },
  DELETED: { variant: 'neutral', Icon: Ban },
};

export function PipelineStatusBadge({ status }: { status: string }) {
  const { tf } = useI18n();
  const style = PIPELINE_STATUS[status] ?? PIPELINE_STATUS.ACTIVE;
  return (
    <Badge variant={style.variant} size="sm">
      <style.Icon className="h-3 w-3" aria-hidden />
      {tf([`pipelineStatus.${status}`], status)}
    </Badge>
  );
}

const CERTIFICATION_VARIANT: Record<string, BadgeVariant> = {
  SUPPORTED: 'success',
  BETA: 'warning',
  HIDDEN: 'neutral',
  BLOCKED: 'danger',
};

export function CertificationBadge({ certification }: { certification: string }) {
  const { tf } = useI18n();
  return (
    <Badge variant={CERTIFICATION_VARIANT[certification] ?? 'warning'} size="xs">
      {tf([`certification.${certification}`], certification)}
    </Badge>
  );
}

export function TriggerBadge({ trigger }: { trigger: string }) {
  const { tf } = useI18n();
  return <Badge variant="subtle" size="xs">{tf([`trigger.${trigger}`], trigger)}</Badge>;
}

export function SyncModeBadge({ mode, dim }: { mode: string; dim?: boolean }) {
  const { tf } = useI18n();
  return (
    <Badge variant={dim ? 'subtle' : 'brand'} size="xs" pill={false}>
      {tf([`syncMode.${mode}`], mode)}
    </Badge>
  );
}

/** For places that need the plain string (option labels, review tables). */
export function useSyncModeLabel(): (mode: string) => string {
  const { tf } = useI18n();
  return React.useCallback((mode: string) => tf([`syncMode.${mode}`], mode), [tf]);
}
