'use client';

import * as React from 'react';
import { ChevronDown } from 'lucide-react';

import { cn } from '@/lib/utils';

const sizeMap = { sm: 'h-8 text-caption', md: 'h-9 text-caption', lg: 'h-10 text-small' } as const;

const fieldBase =
  'w-full rounded-md bg-surface-1 text-text-primary placeholder:text-text-quaternary ' +
  'border transition-[border-color,box-shadow] duration-150 outline-none ' +
  'disabled:cursor-not-allowed disabled:opacity-60 disabled:bg-surface-2';

const fieldState = (invalid?: boolean) =>
  invalid
    ? 'border-danger/60 focus:shadow-[0_0_0_3px_rgb(220_38_38/0.15)]'
    : 'border-[rgb(var(--border-strong))] focus:border-brand focus:shadow-focus-brand';

export interface InputProps extends Omit<React.InputHTMLAttributes<HTMLInputElement>, 'size'> {
  leadingIcon?: React.ReactNode;
  trailingIcon?: React.ReactNode;
  size?: keyof typeof sizeMap;
  invalid?: boolean;
}

export const Input = React.forwardRef<HTMLInputElement, InputProps>(function Input(
  { className, leadingIcon, trailingIcon, size = 'md', invalid, ...props },
  ref,
) {
  const input = (
    <input
      ref={ref}
      aria-invalid={invalid || undefined}
      className={cn(
        fieldBase, fieldState(invalid), sizeMap[size],
        leadingIcon ? 'pl-9' : 'pl-3',
        trailingIcon ? 'pr-9' : 'pr-3',
        className,
      )}
      {...props}
    />
  );
  if (!leadingIcon && !trailingIcon) return input;
  return (
    <div className="relative flex items-center">
      {leadingIcon && (
        <span className="pointer-events-none absolute left-3 text-text-tertiary [&_svg]:h-4 [&_svg]:w-4">
          {leadingIcon}
        </span>
      )}
      {input}
      {trailingIcon && (
        <span className="absolute right-3 text-text-tertiary [&_svg]:h-4 [&_svg]:w-4">
          {trailingIcon}
        </span>
      )}
    </div>
  );
});

export interface TextareaProps extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {
  invalid?: boolean;
}

export const Textarea = React.forwardRef<HTMLTextAreaElement, TextareaProps>(function Textarea(
  { className, invalid, rows = 3, ...props },
  ref,
) {
  return (
    <textarea
      ref={ref}
      rows={rows}
      aria-invalid={invalid || undefined}
      className={cn(fieldBase, fieldState(invalid),
        'resize-y px-3 py-2 text-caption leading-relaxed', className)}
      {...props}
    />
  );
});

export interface SelectProps extends Omit<React.SelectHTMLAttributes<HTMLSelectElement>, 'size'> {
  size?: keyof typeof sizeMap;
  invalid?: boolean;
}

export const Select = React.forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { className, size = 'md', invalid, children, ...props },
  ref,
) {
  return (
    <div className="relative flex items-center">
      <select
        ref={ref}
        aria-invalid={invalid || undefined}
        className={cn(fieldBase, fieldState(invalid), sizeMap[size],
          'appearance-none pl-3 pr-8', className)}
        {...props}
      >
        {children}
      </select>
      <ChevronDown className="pointer-events-none absolute right-2.5 h-3.5 w-3.5 text-text-tertiary" />
    </div>
  );
});

export function Label({
  children, required, hint, htmlFor, className,
}: {
  children: React.ReactNode;
  required?: boolean;
  hint?: string;
  htmlFor?: string;
  className?: string;
}) {
  return (
    <label
      htmlFor={htmlFor}
      className={cn('mb-1 flex items-baseline gap-1.5 text-caption font-emphasis text-text-secondary',
        className)}
    >
      <span>{children}</span>
      {required && <span className="text-danger" aria-hidden>*</span>}
      {hint && <span className="text-tiny font-normal text-text-quaternary">{hint}</span>}
    </label>
  );
}

export function FieldError({ children }: { children?: React.ReactNode }) {
  if (!children) return null;
  return <p className="mt-1 text-tiny text-danger">{children}</p>;
}

export function FieldHelp({ children }: { children?: React.ReactNode }) {
  if (!children) return null;
  return <p className="mt-1 text-tiny leading-relaxed text-text-quaternary">{children}</p>;
}

export function Toggle({
  checked, onChange, label, description, disabled, hideLabel,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  /** Always required. With `hideLabel` it becomes the accessible name. */
  label: string;
  description?: string;
  disabled?: boolean;
  /**
   * Visually hide the text, for a toggle in a table row that already
   * names what is being switched. It is still announced -- a column of
   * unlabelled switches is unusable with a screen reader, and dropping
   * the label entirely is how that happens.
   */
  hideLabel?: boolean;
}) {
  return (
    <label className={cn('flex items-start gap-3', disabled && 'opacity-60')}>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={label}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={cn(
          'relative mt-0.5 h-5 w-9 flex-shrink-0 overflow-hidden rounded-full transition-colors',
          checked ? 'bg-brand' : 'bg-surface-3',
        )}
      >
        <span
          className={cn(
            'absolute top-0.5 h-4 w-4 rounded-full bg-white shadow-linear-sm transition-transform',
            checked ? 'translate-x-4' : 'translate-x-0.5',
          )}
        />
      </button>
      {!hideLabel && (
        <span className="min-w-0">
          <span className="block text-caption font-emphasis text-text-primary">{label}</span>
          {description && (
            <span className="block text-tiny leading-relaxed text-text-tertiary">{description}</span>
          )}
        </span>
      )}
    </label>
  );
}

export function Checkbox({
  checked, indeterminate, onChange, label, disabled, className,
  'aria-label': ariaLabel,
}: {
  checked: boolean;
  indeterminate?: boolean;
  onChange: (value: boolean) => void;
  label?: React.ReactNode;
  disabled?: boolean;
  className?: string;
  'aria-label'?: string;
}) {
  const ref = React.useRef<HTMLInputElement>(null);
  React.useEffect(() => {
    if (ref.current) ref.current.indeterminate = Boolean(indeterminate) && !checked;
  }, [indeterminate, checked]);

  return (
    <label className={cn('inline-flex items-center gap-2', disabled && 'opacity-50', className)}>
      <input
        ref={ref}
        type="checkbox"
        checked={checked}
        disabled={disabled}
        aria-label={typeof label === 'string' ? label : ariaLabel}
        onChange={(event) => onChange(event.target.checked)}
        className="h-4 w-4 cursor-pointer rounded-sm border-[rgb(var(--border-strong))] accent-brand"
      />
      {label && <span className="text-caption text-text-secondary">{label}</span>}
    </label>
  );
}
