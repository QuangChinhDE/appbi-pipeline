'use client';

import * as React from 'react';
import { ChevronDown, Eye, EyeOff, Plus, X } from 'lucide-react';

import { Button, IconButton } from '@/components/ui/Button';
import {
  Checkbox, FieldError, FieldHelp, Input, Label, Select, Textarea,
} from '@/components/ui/Input';
import type { JsonSchema } from '@/lib/types';
import { cn } from '@/lib/utils';
import { useI18n } from '@/providers/LanguageProvider';

/**
 * Renders a connector's JSON Schema as a form (section 52).
 *
 * Only what the spec declares is shown: required, secret, enum, default,
 * min/max, pattern, description, advanced and oneOf all map to concrete UI.
 * Anything the renderer cannot express is surfaced as an explicit notice rather
 * than silently dropped, so an unsupported connector is visible, not broken.
 */

export type FormValues = Record<string, unknown>;

interface FieldProps {
  name: string;
  schema: JsonSchema;
  value: unknown;
  onChange: (value: unknown) => void;
  required: boolean;
  error?: string;
  /** Existing secrets come back masked; the field starts read-only. */
  secretConfigured?: boolean;
  path: string;
}

/**
 * A oneOf branch identifies itself either with `const` or with a single-value
 * `enum` — real connector specs use both spellings.
 */
function constValueOf(schema: JsonSchema): unknown {
  if (schema.const !== undefined) return schema.const;
  if (Array.isArray(schema.enum) && schema.enum.length === 1) return schema.enum[0];
  return undefined;
}

/** Connector descriptions ship raw HTML; render it as plain text. */
function plainText(value?: string): string | undefined {
  if (!value) return undefined;
  return value
    .replace(/<br\s*\/?>/gi, ' ')
    .replace(/<[^>]+>/g, '')
    .replace(/&nbsp;/g, ' ')
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/\s+/g, ' ')
    .trim();
}

function placeholderFor(schema: JsonSchema): string | undefined {
  const examples = schema.examples;
  if (Array.isArray(examples) && examples.length > 0) return String(examples[0]);
  return undefined;
}

function typeOf(schema: JsonSchema): string {
  const raw = schema.type;
  if (Array.isArray(raw)) return raw.find((t) => t !== 'null') ?? 'string';
  return raw ?? (schema.oneOf ? 'oneOf' : 'string');
}

function SecretField({ name, schema, value, onChange, required, error, secretConfigured, path }: FieldProps) {
  const { t } = useI18n();
  const [reveal, setReveal] = React.useState(false);
  const [editing, setEditing] = React.useState(!secretConfigured);

  if (secretConfigured && !editing) {
    return (
      <div>
        <Label htmlFor={path} required={required}>{schema.title ?? name}</Label>
        <div className="flex items-center gap-2">
          <Input id={path} value="••••••••" readOnly className="font-mono" />
          <Button size="sm" variant="secondary" onClick={() => { setEditing(true); onChange(''); }}>
            {t('common.change')}
          </Button>
        </div>
        <FieldHelp>{t('actor.secretStored')}</FieldHelp>
      </div>
    );
  }

  return (
    <div>
      <Label htmlFor={path} required={required}>{schema.title ?? name}</Label>
      <Input
        id={path}
        type={reveal ? 'text' : 'password'}
        autoComplete="new-password"
        value={(value as string) ?? ''}
        invalid={Boolean(error)}
        onChange={(event) => onChange(event.target.value)}
        trailingIcon={
          <button
            type="button"
            aria-label={reveal ? t('common.hide') : t('common.show')}
            onClick={() => setReveal((v) => !v)}
            className="pointer-events-auto text-text-tertiary hover:text-text-primary"
          >
            {reveal ? <EyeOff /> : <Eye />}
          </button>
        }
      />
      <FieldError>{error}</FieldError>
      <FieldHelp>{plainText(schema.description)}</FieldHelp>
    </div>
  );
}

function ArrayField({ name, schema, value, onChange, required, path }: FieldProps) {
  const { t } = useI18n();
  const items = Array.isArray(value) ? (value as string[]) : [];
  const update = (next: string[]) => onChange(next);
  return (
    <div>
      <Label htmlFor={`${path}-0`} required={required}>{schema.title ?? name}</Label>
      <div className="space-y-1.5">
        {items.map((item, index) => (
          <div key={index} className="flex items-center gap-1.5">
            <Input
              id={`${path}-${index}`}
              aria-label={`${schema.title ?? name} ${index + 1}`}
              value={item}
              onChange={(event) => {
                const next = [...items];
                next[index] = event.target.value;
                update(next);
              }}
            />
            <IconButton
              aria-label={t('common.removeRow')}
              size="sm"
              variant="ghost"
              onClick={() => update(items.filter((_, i) => i !== index))}
            >
              <X className="h-3.5 w-3.5" />
            </IconButton>
          </div>
        ))}
        <Button
          size="xs"
          variant="subtle"
          leadingIcon={<Plus className="h-3 w-3" />}
          onClick={() => update([...items, ''])}
        >
          {t('common.addValue')}
        </Button>
      </div>
      <FieldHelp>{plainText(schema.description)}</FieldHelp>
    </div>
  );
}

/** `oneOf` becomes a picker plus the chosen branch's own fields. */
function OneOfField({ name, schema, value, onChange, required, path, secretsConfigured }: FieldProps & {
  secretsConfigured?: Record<string, boolean>;
}) {
  const { t } = useI18n();
  const branches = schema.oneOf ?? [];
  const current = (value ?? schema.default ?? {}) as Record<string, unknown>;

  const discriminator = React.useMemo(() => {
    for (const branch of branches) {
      for (const [key, prop] of Object.entries(branch.properties ?? {})) {
        if (constValueOf(prop) !== undefined) return key;
      }
    }
    return null;
  }, [branches]);

  const activeIndex = React.useMemo(() => {
    if (!discriminator) return 0;
    const found = branches.findIndex(
      (branch) => constValueOf(branch.properties?.[discriminator] ?? {}) === current[discriminator],
    );
    return found >= 0 ? found : 0;
  }, [branches, current, discriminator]);

  const branch = branches[activeIndex];
  const extraProps = Object.entries(branch?.properties ?? {}).filter(
    ([key]) => key !== discriminator,
  );

  return (
    <div className="rounded-md border border-[rgb(var(--border-line))] bg-surface-2/50 p-3">
      <Label htmlFor={path} required={required}>{schema.title ?? name}</Label>
      <Select
        id={path}
        value={String(activeIndex)}
        onChange={(event) => {
          const next = branches[Number(event.target.value)];
          const seed: Record<string, unknown> = {};
          for (const [key, prop] of Object.entries(next?.properties ?? {})) {
            const fixed = constValueOf(prop);
            if (fixed !== undefined) seed[key] = fixed;
            else if (prop.default !== undefined) seed[key] = prop.default;
          }
          onChange(seed);
        }}
      >
        {branches.map((option, index) => (
          <option key={index} value={index}>
            {option.title ?? t('wizard.optionN', { n: index + 1 })}
          </option>
        ))}
      </Select>
      <FieldHelp>{plainText(schema.description)}</FieldHelp>

      {extraProps.length > 0 && (
        <div className="mt-3 space-y-3 border-t border-[rgb(var(--border-line))] pt-3">
          {extraProps.map(([key, prop]) => (
            <SchemaField
              key={key}
              name={key}
              schema={prop}
              value={current[key]}
              required={(branch?.required ?? []).includes(key)}
              path={`${path}.${key}`}
              secretsConfigured={secretsConfigured}
              onChange={(next) => onChange({ ...current, [key]: next })}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export function SchemaField(props: FieldProps & { secretsConfigured?: Record<string, boolean> }) {
  const { t } = useI18n();
  const { name, schema, value, onChange, required, error, path, secretsConfigured } = props;

  if (schema.airbyte_secret) {
    return <SecretField {...props} secretConfigured={secretsConfigured?.[name]} />;
  }
  if (schema.oneOf?.length) {
    return <OneOfField {...props} />;
  }

  const kind = typeOf(schema);
  const id = path;
  const deprecatedNote = schema.deprecated ? t('wizard.deprecatedField') : undefined;

  if (kind === 'boolean') {
    return (
      <div>
        <Checkbox
          checked={Boolean(value ?? schema.default ?? false)}
          onChange={onChange}
          label={schema.title ?? name}
        />
        <FieldHelp>{deprecatedNote ?? plainText(schema.description)}</FieldHelp>
      </div>
    );
  }

  if (Array.isArray(schema.enum) && schema.enum.length > 0) {
    return (
      <div>
        <Label htmlFor={id} required={required}>{schema.title ?? name}</Label>
        <Select
          id={id}
          value={String(value ?? schema.default ?? '')}
          invalid={Boolean(error)}
          onChange={(event) => onChange(event.target.value)}
        >
          <option value="">{t('common.selectPlaceholder')}</option>
          {schema.enum.map((option) => (
            <option key={String(option)} value={String(option)}>{String(option)}</option>
          ))}
        </Select>
        <FieldError>{error}</FieldError>
        <FieldHelp>{deprecatedNote ?? plainText(schema.description)}</FieldHelp>
      </div>
    );
  }

  if (kind === 'array') {
    return <ArrayField {...props} />;
  }

  if (kind === 'integer' || kind === 'number') {
    return (
      <div>
        <Label htmlFor={id} required={required}>{schema.title ?? name}</Label>
        <Input
          id={id}
          type="number"
          placeholder={placeholderFor(schema)}
          min={schema.minimum}
          max={schema.maximum}
          value={value === undefined || value === null ? '' : String(value)}
          invalid={Boolean(error)}
          onChange={(event) => {
            const raw = event.target.value;
            onChange(raw === '' ? null : Number(raw));
          }}
        />
        <FieldError>{error}</FieldError>
        <FieldHelp>{deprecatedNote ?? plainText(schema.description)}</FieldHelp>
      </div>
    );
  }

  if (kind === 'object' && schema.properties) {
    const nested = (value ?? {}) as Record<string, unknown>;
    return (
      <div className="rounded-md border border-[rgb(var(--border-line))] bg-surface-2/50 p-3">
        <p className="mb-2 text-caption font-emphasis text-text-secondary">
          {schema.title ?? name}
        </p>
        <div className="space-y-3">
          {Object.entries(schema.properties).map(([key, prop]) => (
            <SchemaField
              key={key}
              name={key}
              schema={prop}
              value={nested[key]}
              required={(schema.required ?? []).includes(key)}
              path={`${path}.${key}`}
              secretsConfigured={secretsConfigured}
              onChange={(next) => onChange({ ...nested, [key]: next })}
            />
          ))}
        </div>
        <FieldHelp>{plainText(schema.description)}</FieldHelp>
      </div>
    );
  }

  const multiline = (schema.description ?? '').length > 140 || schema.format === 'textarea';
  return (
    <div>
      <Label htmlFor={id} required={required}>{schema.title ?? name}</Label>
      {multiline ? (
        <Textarea
          id={id}
          value={(value as string) ?? ''}
          invalid={Boolean(error)}
          onChange={(event) => onChange(event.target.value)}
        />
      ) : (
        <Input
          id={id}
          value={(value as string) ?? ''}
          invalid={Boolean(error)}
          placeholder={placeholderFor(schema)}
          pattern={schema.pattern}
          onChange={(event) => onChange(event.target.value)}
        />
      )}
      <FieldError>{error}</FieldError>
      <FieldHelp>{deprecatedNote ?? plainText(schema.description)}</FieldHelp>
    </div>
  );
}

function orderOf(schema: JsonSchema): number {
  return typeof schema.order === 'number' ? schema.order : 999;
}

export function applyDefaults(spec: JsonSchema, current: FormValues = {}): FormValues {
  const out: FormValues = { ...current };
  const required = new Set(spec.required ?? []);
  for (const [key, prop] of Object.entries(spec.properties ?? {})) {
    if (out[key] !== undefined) continue;
    if (prop.default !== undefined) {
      out[key] = prop.default;
      continue;
    }
    // Many connector specs give no default but do give an example. For a
    // required, non-secret scalar that example is the conventional value
    // (port 5432, schema "public"), so prefilling it saves a lookup.
    const example = Array.isArray(prop.examples) ? prop.examples[0] : undefined;
    if (required.has(key) && example !== undefined && !prop.airbyte_secret) {
      out[key] = prop.type === 'integer' || prop.type === 'number'
        ? Number(example)
        : example;
    }
  }
  return out;
}

export function validateAgainstSpec(
  spec: JsonSchema,
  values: FormValues,
  t: (key: string, vars?: Record<string, string | number>) => string,
): Record<string, string> {
  const errors: Record<string, string> = {};
  for (const key of spec.required ?? []) {
    const value = values[key];
    const empty =
      value === undefined || value === null || value === '' ||
      (Array.isArray(value) && value.length === 0);
    if (empty) {
      errors[key] = `${spec.properties?.[key]?.title ?? key} ${t('common.required')}`;
    }
  }
  for (const [key, prop] of Object.entries(spec.properties ?? {})) {
    const value = values[key];
    if (value === undefined || value === null || value === '') continue;
    if ((prop.type === 'integer' || prop.type === 'number') && typeof value === 'number') {
      if (prop.minimum !== undefined && value < prop.minimum) {
        errors[key] = t('wizard.minValue', { n: prop.minimum });
      }
      if (prop.maximum !== undefined && value > prop.maximum) {
        errors[key] = t('wizard.maxValue', { n: prop.maximum });
      }
    }
    if (prop.pattern && typeof value === 'string' && !new RegExp(prop.pattern).test(value)) {
      errors[key] = t('wizard.badFormat');
    }
  }
  return errors;
}

export function DynamicConnectorForm({
  spec, values, onChange, errors, secretsConfigured,
}: {
  spec: JsonSchema;
  values: FormValues;
  onChange: (values: FormValues) => void;
  errors?: Record<string, string>;
  secretsConfigured?: Record<string, boolean>;
}) {
  const { t } = useI18n();
  const [showAdvanced, setShowAdvanced] = React.useState(false);

  const entries = Object.entries(spec.properties ?? {});
  const basic = entries.filter(([, prop]) => !prop.airbyte_advanced).sort(
    (a, b) => orderOf(a[1]) - orderOf(b[1]),
  );
  const advanced = entries.filter(([, prop]) => prop.airbyte_advanced).sort(
    (a, b) => orderOf(a[1]) - orderOf(b[1]),
  );

  const unsupported = entries.filter(
    ([, prop]) => prop.oneOf === undefined && prop.type === undefined && prop.enum === undefined,
  );

  const setField = (key: string, value: unknown) => onChange({ ...values, [key]: value });

  return (
    <div className="space-y-4">
      {unsupported.length > 0 && (
        <div className="rounded-md border border-warning/30 bg-warning/5 p-3 text-caption text-text-secondary">
          {t('wizard.unsupportedFields', {
            fields: unsupported.map(([key]) => key).join(', '),
          })}
        </div>
      )}

      {basic.map(([key, prop]) => (
        <SchemaField
          key={key}
          name={key}
          schema={prop}
          value={values[key]}
          required={(spec.required ?? []).includes(key)}
          error={errors?.[key]}
          path={`cfg-${key}`}
          secretsConfigured={secretsConfigured}
          onChange={(next) => setField(key, next)}
        />
      ))}

      {advanced.length > 0 && (
        <div className="rounded-md border border-[rgb(var(--border-line))]">
          <button
            type="button"
            onClick={() => setShowAdvanced((v) => !v)}
            aria-expanded={showAdvanced}
            className="flex w-full items-center justify-between px-3 py-2 text-caption font-emphasis text-text-secondary hover:bg-surface-2"
          >
            <span>{t('common.advanced', { n: advanced.length })}</span>
            <ChevronDown className={cn('h-3.5 w-3.5 transition-transform', showAdvanced && 'rotate-180')} />
          </button>
          {showAdvanced && (
            <div className="space-y-3 border-t border-[rgb(var(--border-line))] p-3">
              {advanced.map(([key, prop]) => (
                <SchemaField
                  key={key}
                  name={key}
                  schema={prop}
                  value={values[key]}
                  required={(spec.required ?? []).includes(key)}
                  error={errors?.[key]}
                  path={`cfg-${key}`}
                  secretsConfigured={secretsConfigured}
                  onChange={(next) => setField(key, next)}
                />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/** Split a submitted form into non-secret config vs credentials. */
export function splitSecrets(
  spec: JsonSchema, values: FormValues,
): { configuration: FormValues; credentials: FormValues } {
  const configuration: FormValues = {};
  const credentials: FormValues = {};
  for (const [key, value] of Object.entries(values)) {
    if (spec.properties?.[key]?.airbyte_secret) credentials[key] = value;
    else configuration[key] = value;
  }
  return { configuration, credentials };
}
