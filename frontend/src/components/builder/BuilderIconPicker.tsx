'use client';

import { Check } from 'lucide-react';

import type { BuilderIconKey } from '@/lib/types';
import { cn } from '@/lib/utils';
import {
  BUILDER_ICON_OPTIONS, ConnectorIcon,
} from '@/components/integrations/ConnectorIcon';

export function BuilderIconPicker({
  value, onChange, disabled, label,
}: {
  value: BuilderIconKey;
  onChange: (value: BuilderIconKey) => void;
  disabled?: boolean;
  label: string;
}) {
  return (
    <fieldset disabled={disabled}>
      <legend className="mb-2 text-label text-text-secondary">{label}</legend>
      <div className="grid grid-cols-5 gap-1.5 sm:grid-cols-10">
        {BUILDER_ICON_OPTIONS.map((option) => {
          const selected = value === option.key;
          return (
            <button
              key={option.key}
              type="button"
              title={option.label}
              aria-label={option.label}
              aria-pressed={selected}
              onClick={() => onChange(option.key)}
              className={cn(
                'relative flex h-10 items-center justify-center rounded-md border transition-colors',
                selected
                  ? 'border-brand bg-brand/5 shadow-linear-sm'
                  : 'border-[rgb(var(--border-line))] bg-surface-1 hover:border-[rgb(var(--border-strong))] hover:bg-surface-2',
              )}
            >
              <ConnectorIcon icon={option.key} size="md" />
              {selected && (
                <span className="absolute -right-1 -top-1 flex h-4 w-4 items-center justify-center rounded-full bg-brand text-white">
                  <Check className="h-2.5 w-2.5" aria-hidden />
                </span>
              )}
            </button>
          );
        })}
      </div>
    </fieldset>
  );
}
