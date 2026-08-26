'use client';

/**
 * The documentation panel beside the configuration form.
 *
 * Airbyte puts setup instructions next to the fields they describe, and it is
 * the right shape: a connector form is a list of names only meaningful to
 * someone who already knows the system. Without this, "Access token" means
 * "go and find out somewhere else, then come back and remember what you read".
 *
 * Three things go in it, in the order somebody needs them:
 *
 *   1. how to get the credential — the step that sends people away
 *   2. what this connector will read — so the choice can be checked before
 *      spending a sync on it
 *   3. the field reference — every field, its default, and why it is there
 *
 * Content comes from the connector's own spec and stream list, so a connector
 * that gains a field gains a row here. Nothing is written twice.
 */

import * as React from 'react';
import { BookOpen, ExternalLink, KeyRound, Layers } from 'lucide-react';

import type { ConnectorDetail, JsonSchema } from '@/lib/types';
import { useI18n } from '@/providers/LanguageProvider';

/** Connector descriptions ship raw HTML and markdown; show neither. */
function plain(text: string | null | undefined): string {
  if (!text) return '';
  return text
    .replace(/<[^>]+>/g, '')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .trim();
}

function Section({
  icon, title, children,
}: { icon: React.ReactNode; title: string; children: React.ReactNode }) {
  return (
    <section className="border-b border-[rgb(var(--border-line))] px-4 py-3.5 last:border-b-0">
      <h3 className="mb-2 flex items-center gap-1.5 text-caption font-emphasis text-text-primary">
        <span className="text-text-tertiary">{icon}</span>
        {title}
      </h3>
      {children}
    </section>
  );
}

export function ConnectorDocs({ connector }: { connector: ConnectorDetail }) {
  const { t } = useI18n();

  const properties = React.useMemo(() => {
    const entries = Object.entries(
      (connector.spec_schema?.properties ?? {}) as Record<string, JsonSchema>,
    );
    // The same order the form uses, so the reader's eye can move sideways
    // between a field and its explanation.
    return entries.sort(
      (a, b) => (a[1].order ?? 999) - (b[1].order ?? 999),
    );
  }, [connector.spec_schema]);

  const required = new Set<string>(
    (connector.spec_schema?.required as string[] | undefined) ?? [],
  );

  // Base connectors carry their own credential instructions; anything else
  // falls back to its documentation link.
  const isBase = connector.connector_key.startsWith('source-base-');

  return (
    <aside
      // Scrolls inside itself on a wide screen. HRM has 25 streams, so the
      // panel is taller than the viewport, and without a ceiling the tail of it
      // disappears behind the wizard's sticky footer with no way to reach it.
      className={
        'overflow-hidden rounded-lg border border-[rgb(var(--border-line))] '
        + 'bg-surface-1 lg:flex lg:max-h-[calc(100vh-9rem)] lg:flex-col'
      }
      aria-label={t('docs.title')}
    >
      <header className="shrink-0 border-b border-[rgb(var(--border-line))] bg-surface-2/50 px-4 py-2.5">
        <p className="flex items-center gap-1.5 text-caption font-emphasis text-text-primary">
          <BookOpen className="h-3.5 w-3.5 text-text-tertiary" />
          {t('docs.title')}
        </p>
        <p className="mt-0.5 text-tiny text-text-tertiary">
          {plain(connector.description) || connector.display_name}
        </p>
      </header>

      <div className="lg:min-h-0 lg:flex-1 lg:overflow-y-auto">
      {isBase && (
        <Section icon={<KeyRound className="h-3.5 w-3.5" />} title={t('docs.credential')}>
          <ol className="ml-4 list-decimal space-y-1 text-tiny text-text-secondary">
            <li>{t('docs.base.step1')}</li>
            <li>{t('docs.base.step2')}</li>
            <li>{t('docs.base.step3')}</li>
          </ol>
          <p className="mt-2 rounded-md bg-surface-2 px-2.5 py-2 text-tiny text-text-tertiary">
            {t('docs.base.domainNote')}
          </p>
        </Section>
      )}

      {/* Which tables this produces.
          This section used to repeat every field's description, which the form
          already shows directly under the input it belongs to -- the same
          paragraph twice on one screen, once where it helps and once where it
          is noise. What the form genuinely cannot answer is "what do I get out
          of this", and that is the question people ask before spending a sync
          finding out. */}
      {connector.stream_names?.length ? (
        <Section icon={<Layers className="h-3.5 w-3.5" />} title={t('docs.reads')}>
          <p className="mb-2 text-tiny text-text-secondary">
            {t('docs.streamCount', { n: String(connector.stream_names.length) })}
          </p>
          <ul className="flex flex-wrap gap-1">
            {connector.stream_names.map((name) => (
              <li
                key={name}
                className="rounded bg-surface-2 px-1.5 py-0.5 font-mono text-tiny text-text-secondary"
              >
                {name}
              </li>
            ))}
          </ul>
        </Section>
      ) : null}

      {/* Required fields, named but not re-explained: a checklist of what has
          to be filled in, not a second copy of the help text. */}
      {properties.some(([key]) => required.has(key)) && (
        <Section icon={<BookOpen className="h-3.5 w-3.5" />} title={t('docs.fields')}>
          <ul className="space-y-1">
            {properties.filter(([key]) => required.has(key)).map(([key, schema]) => (
              <li key={key} className="flex items-baseline gap-1.5 text-tiny">
                <span className="text-danger">*</span>
                <span className="text-text-secondary">{schema.title ?? key}</span>
              </li>
            ))}
          </ul>
        </Section>
      )}

      {connector.documentation_url && (
        <Section icon={<ExternalLink className="h-3.5 w-3.5" />} title={t('docs.apiReference')}>
          <a
            href={connector.documentation_url}
            target="_blank"
            rel="noreferrer noopener"
            className="inline-flex items-center gap-1 text-tiny text-brand hover:underline"
          >
            {t('docs.openApiDocs')}
            <ExternalLink className="h-3 w-3" />
          </a>
        </Section>
      )}
      </div>
    </aside>
  );
}
