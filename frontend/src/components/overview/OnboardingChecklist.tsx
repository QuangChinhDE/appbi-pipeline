'use client';

/**
 * The four steps, for a workspace that has none of them yet.
 *
 * It lived inside the overview page and moved out when that page became Data
 * Health — because an empty workspace has no health to report. A green tick
 * over nothing would be the most misleading thing the screen could say, so the
 * checklist takes the whole page until there has been one successful run.
 */

import * as React from 'react';
import Link from 'next/link';
import { Activity, ArrowRight, CheckCircle2, Circle } from 'lucide-react';

import { useWorkspacePath } from '@/hooks/use-workspace-path';
import { useI18n } from '@/providers/LanguageProvider';
import { Button } from '@/components/ui/Button';

export function OnboardingChecklist({ state }: { state: Record<string, boolean> }) {
  const { t } = useI18n();
  const ws = useWorkspacePath();
  const steps = [
    { key: 'has_source', label: t('overview.onboarding.source'), href: '/sources/new?journey=1' },
    { key: 'has_destination', label: t('overview.onboarding.destination'), href: '/destinations/new' },
    { key: 'has_pipeline', label: t('overview.onboarding.pipeline'), href: '/pipelines/new' },
    { key: 'has_successful_run', label: t('overview.onboarding.run'), href: '/pipelines' },
  ];
  const nextStep = steps.find((step) => !state[step.key]);

  return (
    <div className="rounded-lg border border-brand/25 bg-brand-soft/60 p-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="flex items-center gap-1.5 text-caption font-strong text-text-primary">
            <Activity className="h-3.5 w-3.5 text-brand" />
            {t('overview.onboarding.title')}
          </p>
          <ol className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1.5">
            {steps.map((step) => {
              const done = Boolean(state[step.key]);
              return (
                <li key={step.key} className="flex items-center gap-1.5">
                  {done ? (
                    <CheckCircle2 className="h-3.5 w-3.5 text-success" />
                  ) : (
                    <Circle className="h-3.5 w-3.5 text-text-quaternary" />
                  )}
                  <span
                    className={done
                      ? 'text-caption text-text-tertiary line-through'
                      : 'text-caption text-text-secondary'}
                  >
                    {step.label}
                  </span>
                </li>
              );
            })}
          </ol>
        </div>
        {nextStep && (
          <Link href={ws(nextStep.href)} className="flex-shrink-0">
            <Button variant="primary" size="sm" trailingIcon={<ArrowRight className="h-3.5 w-3.5" />}>
              {nextStep.label}
            </Button>
          </Link>
        )}
      </div>
    </div>
  );
}
