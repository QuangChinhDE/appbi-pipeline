'use client';

import * as React from 'react';
import { Ban, Search } from 'lucide-react';

import { Input } from '@/components/ui/Input';
import { EmptyState } from '@/components/ui/Feedback';
import type { Connector } from '@/lib/types';
import { supportLevelKey } from '@/lib/format';
import { cn } from '@/lib/utils';
import { useI18n } from '@/providers/LanguageProvider';
import { CertificationBadge } from './Badges';
import { ConnectorIcon } from './ConnectorIcon';

/**
 * The catalogue is the full upstream registry — hundreds of connectors — so the
 * grid is capped and the count is stated. Rendering every card at once buries
 * the ones we certify and turns the step into a directory dump.
 */
const INITIAL_LIMIT = 24;
const STEP = 48;

export function ConnectorPicker({
  connectors, value, onChange,
}: {
  connectors: Connector[];
  value: string | null;
  onChange: (connectorKey: string) => void;
}) {
  const { t } = useI18n();
  const [query, setQuery] = React.useState('');
  const [category, setCategory] = React.useState<string>('');
  const [limit, setLimit] = React.useState(INITIAL_LIMIT);

  // Narrowing the list restarts it from the top; otherwise the user is left
  // scrolled into the middle of a result set they have not seen.
  React.useEffect(() => setLimit(INITIAL_LIMIT), [query, category]);

  const categories = React.useMemo(
    () => Array.from(new Set(connectors.map((c) => c.category))).sort(),
    [connectors],
  );

  const filtered = React.useMemo(() => {
    const needle = query.trim().toLowerCase();
    return connectors.filter((connector) => {
      if (category && connector.category !== category) return false;
      if (!needle) return true;
      return (
        connector.display_name.toLowerCase().includes(needle) ||
        connector.connector_key.toLowerCase().includes(needle) ||
        connector.category.toLowerCase().includes(needle)
      );
    });
  }, [connectors, category, query]);

  const visible = filtered.slice(0, limit);
  const selectedOffList = value && !visible.some((c) => c.connector_key === value)
    ? connectors.find((c) => c.connector_key === value)
    : undefined;

  return (
    <div className="space-y-3">
      <div className="flex flex-col gap-2 lg:flex-row lg:items-center lg:justify-between">
        <div className="lg:w-72">
          <Input
            size="sm"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t('wizard.searchConnector')}
            aria-label={t('wizard.searchConnector')}
            leadingIcon={<Search />}
          />
        </div>
        <div className="flex flex-wrap gap-1.5">
          <CategoryChip label={t('common.all')} active={!category} onClick={() => setCategory('')} />
          {categories.map((name) => (
            <CategoryChip
              key={name}
              label={name}
              active={category === name}
              onClick={() => setCategory(name)}
            />
          ))}
        </div>
      </div>

      {filtered.length === 0 ? (
        <EmptyState title={t('wizard.noConnectorMatch')} compact />
      ) : (
        <>
          {/* The chosen connector stays on screen even when a later search
              excludes it, so the step cannot silently lose the selection. */}
          {selectedOffList && (
            <ConnectorCard connector={selectedOffList} selected onChange={onChange} t={t} />
          )}

          <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
            {visible.map((connector) => (
              <ConnectorCard
                key={connector.connector_key}
                connector={connector}
                selected={value === connector.connector_key}
                onChange={onChange}
                t={t}
              />
            ))}
          </div>

          <div className="flex items-center justify-between gap-3">
            <p className="text-tiny text-text-quaternary">
              {t('wizard.connectorCount', {
                shown: String(visible.length),
                total: String(filtered.length),
              })}
            </p>
            {visible.length < filtered.length && (
              <button
                type="button"
                onClick={() => setLimit((current) => current + STEP)}
                className="rounded-md px-2 py-1 text-tiny font-emphasis text-brand hover:bg-brand/10"
              >
                {t('wizard.showMoreConnectors')}
              </button>
            )}
          </div>
        </>
      )}
    </div>
  );
}

function ConnectorCard({
  connector, selected, onChange, t,
}: {
  connector: Connector;
  selected: boolean;
  onChange: (connectorKey: string) => void;
  t: (key: string, vars?: Record<string, string>) => string;
}) {
  const blocked = !connector.selectable;
  // Most of the catalogue ships without hand-written copy, so the secondary line
  // states what is actually known rather than leaving a blank gap.
  const subtitle = blocked
    ? (connector.disabled_reason ?? t('wizard.connectorBlocked'))
    : connector.description
      || `${connector.category} · ${t(supportLevelKey(connector.support_level))}`;

  return (
    <button
      type="button"
      disabled={blocked}
      onClick={() => onChange(connector.connector_key)}
      aria-pressed={selected}
      className={cn(
        'flex h-full items-start gap-2.5 rounded-lg border p-2.5 text-left transition-colors',
        selected
          ? 'border-brand bg-brand-soft/60 shadow-focus-brand'
          : 'border-[rgb(var(--border-line))] bg-surface-1 hover:border-[rgb(var(--border-strong))] hover:bg-surface-2',
        blocked
          && 'cursor-not-allowed opacity-55 hover:border-[rgb(var(--border-line))] hover:bg-surface-1',
      )}
    >
      <ConnectorIcon icon={connector.icon} connectorKey={connector.connector_key} size="md" />
      <span className="min-w-0 flex-1">
        <span className="flex items-start gap-1.5">
          {/* Wraps rather than truncates: "Sample Data…" tells the user less
              than the two lines it would have taken to say it. */}
          <span className="line-clamp-2 flex-1 text-caption font-strong leading-snug text-text-primary">
            {connector.display_name}
          </span>
          {connector.certification === 'SUPPORTED' && (
            <CertificationBadge certification={connector.certification} />
          )}
        </span>
        <span className="mt-1 block line-clamp-2 text-tiny leading-relaxed text-text-tertiary">
          {subtitle}
        </span>
        {/* The pinned version used to sit here. Nobody picks a connector by
            its version, and a line that is always present costs a row of
            height on every tile in the grid. */}
        {(connector.supports_cdc || blocked) && (
          <span className="mt-1 flex flex-wrap items-center gap-1.5 text-tiny text-text-quaternary">
            {connector.supports_cdc && <span>CDC</span>}
            {blocked && <Ban className="h-3 w-3 text-danger" />}
          </span>
        )}
      </span>
    </button>
  );
}

function CategoryChip({
  label, active, onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        'rounded-full border px-2.5 py-1 text-tiny font-emphasis transition-colors',
        active
          ? 'border-brand bg-brand/10 text-brand'
          : 'border-[rgb(var(--border-line))] text-text-tertiary hover:text-text-primary',
      )}
    >
      {label}
    </button>
  );
}
