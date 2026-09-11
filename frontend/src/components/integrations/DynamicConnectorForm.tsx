'use client';

import * as React from 'react';
import { ChevronDown, Eye, EyeOff, X } from 'lucide-react';

import { Button } from '@/components/ui/Button';
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

/**
 * The path this field's value lives at in the configuration, as the API names
 * it: `credentials.service_account_info`. `path` carries a `cfg-` prefix for
 * DOM ids, which is stripped here rather than changing what ids look like.
 */
function configPath(path: string | undefined): string {
  return (path ?? '').replace(/^cfg-/, '');
}

function isSecretStored(
  props: FieldProps & { secretsConfigured?: Record<string, boolean> },
): boolean {
  const { name, path, secretsConfigured } = props;
  if (!secretsConfigured) return false;
  return Boolean(secretsConfigured[configPath(path)] ?? secretsConfigured[name]);
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
        {/* This branch drew no error at all, so a refusal that named this
            field was invisible and the form looked broken for no reason. */}
        <FieldError>{error}</FieldError>
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

/**
 * A list of short values, entered as chips.
 *
 * This was a column of text boxes with an "add" button, which is fine for two
 * or three long strings and wrong for a list of ids: adding the fourth service
 * desk meant a click, then a field, then a click. Typing them separated by
 * commas into one box is worse still -- a stray space or a full-width comma
 * pasted from a spreadsheet goes unnoticed until a sync quietly reads nothing.
 *
 * So: type, press Enter, it becomes a chip you can see and remove on its own.
 * Pasting a comma-separated list splits it, which is what somebody arriving
 * from a spreadsheet will do first.
 */
function ArrayField({ name, schema, value, onChange, required, path, error }: FieldProps) {
  const { t } = useI18n();
  const items = Array.isArray(value) ? (value as string[]) : [];
  const [draft, setDraft] = React.useState('');

  const commit = (raw: string) => {
    // Split on comma and whitespace, so a paste from a spreadsheet column and
    // a paste from a comma-separated list both arrive as separate chips.
    const parts = raw.split(/[,;\s]+/).map((part) => part.trim()).filter(Boolean);
    if (parts.length === 0) return;
    // Duplicates are silently ignored rather than refused: adding an id twice
    // is a slip, not a decision, and a validation error for it would be noise.
    const next = [...items];
    for (const part of parts) if (!next.includes(part)) next.push(part);
    onChange(next);
    setDraft('');
  };

  const remove = (index: number) => onChange(items.filter((_, i) => i !== index));

  return (
    <div>
      <Label htmlFor={path} required={required}>{schema.title ?? name}</Label>
      <div
        className={cn(
          'flex min-h-9 flex-wrap items-center gap-1.5 rounded-md border bg-surface-1 px-2 py-1.5',
          error
            ? 'border-[rgb(var(--danger))]'
            : 'border-[rgb(var(--border-strong))] focus-within:border-brand',
        )}
        // Clicking the empty space beside the chips should put the cursor in
        // the box, which is what the whole control looks like it is.
        onClick={() => document.getElementById(path ?? '')?.focus()}
      >
        {items.map((item, index) => (
          <span
            key={`${item}-${index}`}
            className="inline-flex items-center gap-1 rounded bg-surface-3 py-0.5 pl-2 pr-1 text-caption text-text-primary"
          >
            <span className="font-mono">{item}</span>
            <button
              type="button"
              aria-label={t('common.removeValue', { value: item })}
              className="text-text-tertiary hover:text-text-primary"
              onClick={(event) => { event.stopPropagation(); remove(index); }}
            >
              <X className="h-3 w-3" />
            </button>
          </span>
        ))}
        <input
          id={path}
          aria-label={schema.title ?? name}
          value={draft}
          placeholder={items.length === 0 ? t('common.chipPlaceholder') : ''}
          className="min-w-[8rem] flex-1 bg-transparent text-caption text-text-primary outline-none placeholder:text-text-tertiary"
          onChange={(event) => {
            // A pasted list is committed immediately rather than sitting in
            // the box looking like one value.
            if (/[,;]/.test(event.target.value)) commit(event.target.value);
            else setDraft(event.target.value);
          }}
          onKeyDown={(event) => {
            if (event.key === 'Enter' || event.key === 'Tab') {
              if (draft.trim()) { event.preventDefault(); commit(draft); }
            } else if (event.key === 'Backspace' && draft === '' && items.length > 0) {
              remove(items.length - 1);
            }
          }}
          // Typed but not committed is still meant. Losing it on blur is the
          // classic way a chip editor eats the last value somebody entered.
          onBlur={() => commit(draft)}
        />
      </div>
      <FieldError>{error}</FieldError>
      <FieldHelp>{plainText(schema.description)}</FieldHelp>
    </div>
  );
}

/** `oneOf` becomes a picker plus the chosen branch's own fields. */
function OneOfField({ name, schema, value, onChange, required, path, secretsConfigured }: FieldProps & {
  secretsConfigured?: Record<string, boolean>;
}) {
  const { t } = useI18n();
  // Memoised because both are `??` expressions: a fresh array/object every
  // render would change the dependencies of the two useMemos below on every
  // render, which makes them memos in name only.
  const branches = React.useMemo(() => schema.oneOf ?? [], [schema.oneOf]);
  const current = React.useMemo(
    () => (value ?? schema.default ?? {}) as Record<string, unknown>,
    [value, schema.default],
  );

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
    // Looked up by dotted path, not by leaf name. Google Sheets keeps every
    // secret inside `credentials`, so `service_account_info` matched nothing
    // and drew an empty box next to a database that held the key.
    return <SecretField {...props} secretConfigured={isSecretStored(props)} />;
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

  // What kind of box this is depends on the field, not on how much prose
  // happens to describe it.
  //
  // This used to be `description.length > 140`, so writing a careful
  // explanation of a field silently turned it into a four-line textarea. The
  // Base `domain` field — one short hostname — rendered as a paragraph box
  // because its help text explained why the wrong choice looks like an expired
  // token. The better the documentation, the worse the form got.
  const multiline = schema.format === 'textarea'
    || schema.multiline === true
    // A genuinely long value: a private key, a JSON service account, a query.
    || (typeof value === 'string' && value.length > 120);
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
    // A checkbox always shows a definite state, so the payload must carry one
    // too. Omitting an unticked boolean lets the connector pick its own
    // default -- dbt-postgres assumes SSL, which then fails against a server
    // that has it switched off, while the form still reads "disabled".
    if (prop.type === 'boolean') {
      out[key] = false;
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
  secretsConfigured?: Record<string, boolean>,
): Record<string, string> {
  const errors: Record<string, string> = {};
  for (const key of spec.required ?? []) {
    const value = values[key];
    // A stored secret is not in `values` and never will be -- the server does
    // not send credentials back. Counting it as missing made the settings tab
    // of any saved source unsaveable: pressing Save answered "1 field(s) still
    // need a value", the masked token box showed no error because it renders
    // without one, and the only way through was to re-enter a token that was
    // already correct.
    const stored = Boolean(secretsConfigured?.[key]);
    const empty =
      !stored && (
        value === undefined || value === null || value === '' ||
        (Array.isArray(value) && value.length === 0));
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

/**
 * Resolve a connector spec into one language.
 *
 * Connectors this product defines carry both: `title` and `description` hold
 * the English, `title_vi` and `description_vi` the Vietnamese. An Airbyte
 * image carries only the English pair, so it passes through untouched --
 * which is the intended behaviour, since translating a connector's own
 * documentation is not something this product should invent.
 *
 * Done once, on the way in, rather than at each of the eighteen places a
 * label or a help line is read. Missing the eighteenth is exactly how a form
 * ends up half translated.
 */
export function localizeSpec(spec: JsonSchema, locale: string): JsonSchema {
  if (locale === 'en') return spec;

  const walk = (node: unknown): unknown => {
    if (Array.isArray(node)) return node.map(walk);
    if (!node || typeof node !== 'object') return node;

    const source = node as Record<string, unknown>;
    const out: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(source)) {
      // The localised variants are consumed here, not passed on: leaving them
      // in would put `title_vi` beside `title` in anything that enumerates
      // the schema's own keys.
      if (key.endsWith(`_${locale}`)) continue;
      out[key] = walk(value);
    }
    for (const field of ['title', 'description', 'examples'] as const) {
      const localised = source[`${field}_${locale}`];
      if (localised !== undefined) out[field] = localised;
    }
    return out;
  };

  return walk(spec) as JsonSchema;
}

export function DynamicConnectorForm({
  spec: rawSpec, values, onChange, errors, secretsConfigured,
}: {
  spec: JsonSchema;
  values: FormValues;
  onChange: (values: FormValues) => void;
  errors?: Record<string, string>;
  secretsConfigured?: Record<string, boolean>;
}) {
  const { t, locale } = useI18n();
  const [showAdvanced, setShowAdvanced] = React.useState(false);
  const spec = React.useMemo(() => localizeSpec(rawSpec, locale), [rawSpec, locale]);

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
