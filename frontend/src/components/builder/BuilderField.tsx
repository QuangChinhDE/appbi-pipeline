'use client';

import { User } from 'lucide-react';
import * as React from 'react';

import { Input } from '@/components/ui/Input';
import { Menu } from '@/components/ui/Menu';
import { useI18n } from '@/providers/LanguageProvider';
import type { BuilderUserInput } from '@/lib/types';
import { cn } from '@/lib/utils';

/** Label, control, hint. One definition, used by every builder form. */
export function Field({
  label, htmlFor, required, hint, error, children,
}: {
  label: string;
  htmlFor: string;
  required?: boolean;
  hint?: string;
  error?: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label htmlFor={htmlFor} className="mb-1 block text-label text-text-secondary">
        {label}
        {required && <span className="ml-0.5 text-danger">*</span>}
      </label>
      {children}
      {error ? (
        <p className="mt-1 text-tiny text-danger" role="alert">{error}</p>
      ) : hint ? (
        <p className="mt-1 text-tiny text-text-quaternary">{hint}</p>
      ) : null}
    </div>
  );
}

/** The template a picked user input becomes. */
export const configReference = (key: string) => `{{ config['${key}'] }}`;

/**
 * A text field that can take a value from the connector's user inputs.
 *
 * The person icon is the affordance Airbyte's builder uses, and it is doing
 * real work: every one of these fields is a Jinja template, and the only way to
 * reach a user input from one is to type `{{ config['key'] }}` exactly. Getting
 * a quote or a brace wrong produces a connector that authenticates against the
 * literal string, which Base answers with a refusal that reads like an expired
 * token. Picking from a list cannot be misspelled.
 *
 * Inserted at the caret rather than replacing the value, because these fields
 * are usually part of a larger string -- `Bearer {{ config['token'] }}`, or a
 * path with an id in the middle.
 */
export function JinjaInput({
  id, value, onChange, userInputs, onCreateInput, disabled, placeholder, type, ariaLabel,
  ariaInvalid,
}: {
  id: string;
  value: string;
  onChange: (next: string) => void;
  userInputs: BuilderUserInput[];
  /** Offered as "create one" when the input you want does not exist yet. */
  onCreateInput?: () => void;
  disabled?: boolean;
  placeholder?: string;
  type?: string;
  ariaLabel?: string;
  ariaInvalid?: boolean;
}) {
  const { t } = useI18n();
  const ref = React.useRef<HTMLInputElement>(null);

  const insert = (key: string) => {
    const snippet = configReference(key);
    const element = ref.current;
    // Fall back to appending when the field has never been focused: a caret of
    // null would otherwise splice at position 0 and hide what was already there.
    const start = element?.selectionStart ?? value.length;
    const end = element?.selectionEnd ?? value.length;
    const next = value.slice(0, start) + snippet + value.slice(end);
    onChange(next);
    requestAnimationFrame(() => {
      element?.focus();
      const caret = start + snippet.length;
      element?.setSelectionRange(caret, caret);
    });
  };

  const items = [
    ...userInputs.map((input) => ({
      id: input.key,
      label: input.title ? `${input.title} — config['${input.key}']` : `config['${input.key}']`,
      onSelect: () => insert(input.key),
    })),
    ...(onCreateInput ? [{
      id: '__new__',
      label: userInputs.length ? t('builder.pickInputNew') : t('builder.pickInputEmpty'),
      onSelect: onCreateInput,
    }] : []),
  ];

  return (
    <div className="relative">
      <Input
        ref={ref}
        id={id}
        size="sm"
        type={type}
        aria-label={ariaLabel}
        aria-invalid={ariaInvalid}
        value={value}
        disabled={disabled}
        placeholder={placeholder}
        className="pr-8"
        onChange={(event) => onChange(event.target.value)}
      />
      {!disabled && items.length > 0 && (
        <div className="absolute inset-y-0 right-1 flex items-center">
          <Menu
            label={t('builder.pickInput')}
            items={items}
            trigger={(
              <span
                className={cn(
                  'flex h-6 w-6 items-center justify-center rounded',
                  'text-text-quaternary hover:text-brand hover:bg-brand/10',
                )}
                title={t('builder.pickInput')}
              >
                <User className="h-3.5 w-3.5" />
              </span>
            )}
          />
        </div>
      )}
    </div>
  );
}
