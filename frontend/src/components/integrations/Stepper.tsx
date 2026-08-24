'use client';

import * as React from 'react';
import { Check } from 'lucide-react';

import { Button } from '@/components/ui/Button';
import { cn } from '@/lib/utils';
import { useI18n } from '@/providers/LanguageProvider';

export interface Step {
  id: string;
  label: string;
}

export function Stepper({
  steps, current, onStepClick,
}: {
  steps: Step[];
  current: number;
  onStepClick?: (index: number) => void;
}) {
  const { t } = useI18n();
  return (
    <ol className="flex items-center gap-1 overflow-x-auto" aria-label={t('common.steps')}>
      {steps.map((step, index) => {
        const done = index < current;
        const active = index === current;
        const clickable = Boolean(onStepClick) && index <= current;
        return (
          <li key={step.id} className="flex items-center gap-1">
            <button
              type="button"
              disabled={!clickable}
              onClick={() => clickable && onStepClick?.(index)}
              aria-current={active ? 'step' : undefined}
              className={cn(
                'flex items-center gap-2 rounded-md px-2.5 py-1.5 transition-colors',
                clickable && !active && 'hover:bg-surface-2',
                !clickable && 'cursor-default',
              )}
            >
              <span
                className={cn(
                  'flex h-5 w-5 flex-shrink-0 items-center justify-center rounded-full text-tiny font-strong',
                  done && 'bg-success text-white',
                  active && 'bg-brand text-white',
                  !done && !active && 'bg-surface-3 text-text-quaternary',
                )}
              >
                {done ? <Check className="h-3 w-3" /> : index + 1}
              </span>
              <span
                className={cn(
                  'whitespace-nowrap text-caption font-emphasis',
                  active ? 'text-text-primary' : 'text-text-tertiary',
                )}
              >
                {step.label}
              </span>
            </button>
            {index < steps.length - 1 && (
              <span
                className={cn('h-px w-6 flex-shrink-0', done ? 'bg-success/50' : 'bg-[rgb(var(--border-line))]')}
                aria-hidden
              />
            )}
          </li>
        );
      })}
    </ol>
  );
}

/** Sticky footer required for every wizard (section 7.3). */
export function WizardFooter({
  onBack, onNext, backLabel, nextLabel,
  nextDisabled, nextLoading, backDisabled, extra, hint,
}: {
  onBack?: () => void;
  onNext?: () => void;
  backLabel?: string;
  nextLabel?: string;
  nextDisabled?: boolean;
  nextLoading?: boolean;
  backDisabled?: boolean;
  extra?: React.ReactNode;
  /** Shown in place of the Back button on the first step. */
  hint?: React.ReactNode;
}) {
  const { t } = useI18n();
  return (
    // Opaque, not translucent: content scrolling under a see-through bar made
    // half-covered cards look clipped rather than scrolled.
    <div className="sticky bottom-0 z-10 mt-4 flex items-center justify-between gap-3 rounded-lg border border-[rgb(var(--border-line))] bg-surface-1 px-4 py-2.5 shadow-[0_-4px_12px_-8px_rgb(0_0_0/0.25)]">
      <div className="flex items-center gap-2">
        {onBack ? (
          <Button variant="ghost" onClick={onBack} disabled={backDisabled}>
            {backLabel ?? t('common.back')}
          </Button>
        ) : (
          hint && <p className="text-tiny text-text-quaternary">{hint}</p>
        )}
      </div>
      <div className="flex items-center gap-2">
        {extra}
        {onNext && (
          <Button variant="primary" onClick={onNext} disabled={nextDisabled} loading={nextLoading}>
            {nextLabel ?? t('common.continue')}
          </Button>
        )}
      </div>
    </div>
  );
}
