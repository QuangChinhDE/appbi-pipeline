'use client';

import { ArrowLeft, ArrowRight, Clock, RefreshCw } from 'lucide-react';
import Link from 'next/link';
import * as React from 'react';

import { ConnectorIcon } from '@/components/integrations/ConnectorIcon';
import type { ActorRef, PipelineDetail } from '@/lib/types';
import { cn } from '@/lib/utils';
import { useI18n } from '@/providers/LanguageProvider';

/**
 * Identity, route, schedule and the two controls people actually reach for.
 *
 * The route is the point of the header. A connection is only ever "this source
 * into that destination", and putting the two actors on the title line — as
 * links, so they are a route out as well as a description — answers "what is
 * this" before the reader has to look anywhere else.
 */
export function ConnectionHeader({
  pipeline, scheduleLabel, onSyncNow, syncing, enabled, onToggleEnabled, toggling,
}: {
  pipeline: PipelineDetail;
  scheduleLabel: string;
  onSyncNow: () => void;
  syncing: boolean;
  enabled: boolean;
  onToggleEnabled: () => void;
  toggling: boolean;
}) {
  const { t } = useI18n();
  return (
    <div className="border-b border-[rgb(var(--border-line))] bg-surface-1 px-4 pt-4 sm:px-6 xl:px-8">
      <Link
        href="/pipelines"
        className="-ml-1 mb-1 inline-flex items-center gap-1 rounded px-1 py-1 text-caption text-text-tertiary transition-colors hover:text-text-primary"
      >
        <ArrowLeft className="h-3.5 w-3.5" />
        {t('pipelines.title')}
      </Link>

      <div className="flex flex-col gap-3 pb-3 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <h1 className="truncate text-h3 font-strong text-text-primary">{pipeline.name}</h1>
          <div className="mt-1 flex min-w-0 flex-wrap items-center gap-1.5">
            <ActorChip actor={pipeline.source} href={`/sources/${pipeline.source.id}`} />
            <ArrowRight className="h-3.5 w-3.5 flex-shrink-0 text-text-quaternary" aria-hidden />
            <ActorChip actor={pipeline.destination} href={`/destinations/${pipeline.destination.id}`} />
          </div>
        </div>

        <div className="flex flex-shrink-0 flex-wrap items-center gap-3">
          <span className="inline-flex items-center gap-1.5 text-caption text-text-tertiary">
            <Clock className="h-3.5 w-3.5" aria-hidden />
            {scheduleLabel}
          </span>

          <button
            type="button"
            onClick={onSyncNow}
            disabled={syncing}
            className={cn(
              'inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-caption font-emphasis',
              'text-brand transition-colors hover:bg-surface-2 disabled:opacity-50',
              'focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand',
            )}
          >
            <RefreshCw className={cn('h-3.5 w-3.5', syncing && 'animate-spin')} aria-hidden />
            {t('pipelines.syncNow')}
          </button>

          <EnabledSwitch
            enabled={enabled}
            disabled={toggling}
            onToggle={onToggleEnabled}
            enabledLabel={t('pipelines.enabled')}
            disabledLabel={t('pipelines.disabled')}
          />
        </div>
      </div>
    </div>
  );
}

function ActorChip({ actor, href }: { actor: ActorRef; href: string }) {
  return (
    <Link
      href={href}
      className={cn(
        'inline-flex min-w-0 items-center gap-1.5 rounded-md border border-[rgb(var(--border-line))]',
        'bg-surface-2 px-1.5 py-1 transition-colors hover:border-[rgb(var(--border-strong))]',
      )}
    >
      <ConnectorIcon icon={actor.icon} connectorKey={actor.connector_key} size="xs" />
      <span className="truncate text-caption text-text-secondary">{actor.name}</span>
      {actor.connector_key?.startsWith('source-base-') && (
        <span className="rounded bg-surface-3 px-1 text-[10px] uppercase tracking-wide text-text-tertiary">
          custom
        </span>
      )}
    </Link>
  );
}

/**
 * A switch, not a pair of Pause/Resume buttons.
 *
 * "Is this connection on" is a state, and a switch shows the state at a glance
 * while a button shows only the action available — the reader has to invert it
 * ("it says Pause, so it must be running") to learn anything.
 */
function EnabledSwitch({
  enabled, disabled, onToggle, enabledLabel, disabledLabel,
}: {
  enabled: boolean;
  disabled: boolean;
  onToggle: () => void;
  enabledLabel: string;
  disabledLabel: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={enabled}
      disabled={disabled}
      onClick={onToggle}
      className={cn(
        'inline-flex items-center gap-2 rounded-full py-1 pl-3 pr-1 transition-colors',
        'text-tiny font-strong uppercase tracking-wide disabled:opacity-60',
        'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2',
        'focus-visible:outline-brand',
        enabled ? 'bg-brand text-text-inverse' : 'bg-surface-3 text-text-tertiary',
      )}
    >
      {enabled ? enabledLabel : disabledLabel}
      <span
        aria-hidden
        className={cn(
          'h-4 w-4 rounded-full transition-colors',
          enabled ? 'bg-white' : 'bg-text-quaternary',
        )}
      />
    </button>
  );
}
