'use client';

/**
 * The RESOURCES view: what dbt found, grouped by resource type.
 *
 * The file tree answers "where is this written down". This answers "what
 * exists" -- and they are genuinely different questions, because a `.yml` file
 * can define forty tests and a model's config can live in three places.
 *
 * Every row here comes from the manifest. The type counts, the filters and the
 * groupings are all dbt's own vocabulary, so nobody has to learn what AppBI
 * decided to call a snapshot.
 */

import * as React from 'react';
import {
  Boxes, ChevronDown, ChevronRight, CircleSlash, Database, Eye, EyeOff, FileCode,
  FlaskConical, GitBranch, Layers, Package, Search, Sheet, Sparkles, TestTube,
  Workflow, X,
} from 'lucide-react';

import { Badge } from '@/components/ui/Badge';
import type { ResourceFacets, ResourceSummary } from '@/lib/types';
import { cn } from '@/lib/utils';

/** dbt's resource types, in the order a person thinks about them. */
const TYPE_ORDER = [
  'model', 'source', 'seed', 'snapshot', 'test', 'unit_test', 'macro',
  'analysis', 'exposure', 'metric', 'semantic_model', 'saved_query', 'group',
  'operation',
];

const TYPE_LABELS: Record<string, string> = {
  model: 'Models',
  source: 'Sources',
  seed: 'Seeds',
  snapshot: 'Snapshots',
  test: 'Tests',
  unit_test: 'Unit tests',
  macro: 'Macros',
  analysis: 'Analyses',
  exposure: 'Exposures',
  metric: 'Metrics',
  semantic_model: 'Semantic models',
  saved_query: 'Saved queries',
  group: 'Groups',
  operation: 'Operations',
};

function typeIcon(type: string) {
  const style = 'h-3.5 w-3.5 shrink-0';
  switch (type) {
    case 'model': return <FileCode className={cn(style, 'text-brand')} />;
    case 'source': return <Database className={cn(style, 'text-info')} />;
    case 'seed': return <Sheet className={cn(style, 'text-success')} />;
    case 'snapshot': return <GitBranch className={cn(style, 'text-warning')} />;
    case 'test':
    case 'unit_test': return <TestTube className={cn(style, 'text-warning')} />;
    case 'macro': return <Sparkles className={cn(style, 'text-text-tertiary')} />;
    case 'analysis': return <FlaskConical className={cn(style, 'text-text-tertiary')} />;
    case 'exposure': return <Workflow className={cn(style, 'text-info')} />;
    case 'metric': return <Layers className={cn(style, 'text-info')} />;
    case 'group': return <Package className={cn(style, 'text-text-tertiary')} />;
    default: return <Boxes className={cn(style, 'text-text-quaternary')} />;
  }
}

export interface ResourceFilters {
  search: string;
  resourceTypes: string[];
  tag: string | null;
  packageName: string | null;
  materialized: string | null;
  /**
   * Show what installed packages contribute, not just this project.
   *
   * Off by default because a parsed manifest carries every macro dbt itself
   * ships -- 477 rows against the 50 a bare project owns -- and none of them
   * can be opened: their path points inside the installed package, which is
   * not part of the project's file set, so the editor gets a 404. Someone
   * debugging a package macro turns this on deliberately.
   */
  includePackages: boolean;
}

export const EMPTY_FILTERS: ResourceFilters = {
  search: '', resourceTypes: [], tag: null, packageName: null,
  materialized: null, includePackages: false,
};

interface ResourceTreeProps {
  resources: ResourceSummary[];
  counts: Record<string, number>;
  total: number;
  facets?: ResourceFacets;
  filters: ResourceFilters;
  onFiltersChange: (filters: ResourceFilters) => void;
  activeUniqueId: string | null;
  onSelect: (resource: ResourceSummary) => void;
  loading?: boolean;
  /** True when the backend truncated the page, so the count is not the total. */
  truncated?: boolean;
}

export function ResourceTree({
  resources,
  counts,
  total,
  facets,
  filters,
  onFiltersChange,
  activeUniqueId,
  onSelect,
  loading,
  truncated,
}: ResourceTreeProps) {
  const [collapsed, setCollapsed] = React.useState<Set<string>>(() => new Set());
  const [showFilters, setShowFilters] = React.useState(false);

  const grouped = React.useMemo(() => {
    const groups = new Map<string, ResourceSummary[]>();
    resources.forEach((item) => {
      const list = groups.get(item.resource_type) ?? [];
      list.push(item);
      groups.set(item.resource_type, list);
    });
    return [...groups.entries()].sort(([left], [right]) => {
      const leftRank = TYPE_ORDER.indexOf(left);
      const rightRank = TYPE_ORDER.indexOf(right);
      return (leftRank === -1 ? 99 : leftRank) - (rightRank === -1 ? 99 : rightRank);
    });
  }, [resources]);

  const toggle = (type: string) => {
    setCollapsed((current) => {
      const next = new Set(current);
      if (next.has(type)) next.delete(type); else next.add(type);
      return next;
    });
  };

  const activeFilterCount =
    filters.resourceTypes.length
    + (filters.tag ? 1 : 0)
    + (filters.packageName ? 1 : 0)
    + (filters.materialized ? 1 : 0);

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-[rgb(var(--border-line))] px-2 py-1.5">
        <div className="flex items-center gap-1.5">
          <div className="relative flex-1">
            <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-text-quaternary" />
            <input
              value={filters.search}
              onChange={(event) =>
                onFiltersChange({ ...filters, search: event.target.value })}
              placeholder="Tìm resource"
              className={cn(
                'h-7 w-full rounded-sm bg-surface-2 pl-7 pr-2 text-caption',
                'text-text-primary placeholder:text-text-quaternary',
                'focus:outline-none focus:ring-1 focus:ring-brand/40',
              )}
            />
          </div>
          <button
            type="button"
            onClick={() => setShowFilters((open) => !open)}
            className={cn(
              'flex h-7 items-center gap-1 rounded-sm px-2 text-caption transition-colors',
              activeFilterCount > 0
                ? 'bg-brand/10 text-brand'
                : 'text-text-tertiary hover:bg-surface-2',
            )}
          >
            Lọc
            {activeFilterCount > 0 && (
              <Badge variant="brand" size="xs">{activeFilterCount}</Badge>
            )}
          </button>
        </div>

        {showFilters && facets && (
          <div className="mt-2 space-y-2">
            <FacetRow
              label="Loại"
              options={facets.resource_types}
              selected={filters.resourceTypes}
              onToggle={(value) => {
                const next = filters.resourceTypes.includes(value)
                  ? filters.resourceTypes.filter((item) => item !== value)
                  : [...filters.resourceTypes, value];
                onFiltersChange({ ...filters, resourceTypes: next });
              }}
              format={(value) => TYPE_LABELS[value] ?? value}
            />
            {facets.tags.length > 0 && (
              <FacetRow
                label="Tag"
                options={facets.tags}
                selected={filters.tag ? [filters.tag] : []}
                onToggle={(value) =>
                  onFiltersChange({
                    ...filters, tag: filters.tag === value ? null : value,
                  })}
              />
            )}
            {facets.materializations.length > 0 && (
              <FacetRow
                label="Materialization"
                options={facets.materializations}
                selected={filters.materialized ? [filters.materialized] : []}
                onToggle={(value) =>
                  onFiltersChange({
                    ...filters,
                    materialized: filters.materialized === value ? null : value,
                  })}
              />
            )}
            {facets.packages.length > 0 && (
              <FacetRow
                label="Package"
                options={facets.packages}
                selected={filters.packageName ? [filters.packageName] : []}
                onToggle={(value) =>
                  onFiltersChange({
                    ...filters,
                    packageName: filters.packageName === value ? null : value,
                  })}
              />
            )}
            {activeFilterCount > 0 && (
              <button
                type="button"
                onClick={() => onFiltersChange({ ...EMPTY_FILTERS, search: filters.search })}
                className="flex items-center gap-1 text-tiny text-text-tertiary hover:text-text-primary"
              >
                <X className="h-3 w-3" /> Bỏ hết bộ lọc
              </button>
            )}
          </div>
        )}
      </div>

      <div className="flex-1 overflow-auto py-1">
        {loading && resources.length === 0 ? (
          <p className="px-3 py-6 text-center text-caption text-text-tertiary">Đang đọc…</p>
        ) : grouped.length === 0 ? (
          <p className="px-3 py-6 text-center text-caption text-text-tertiary">
            {filters.search || activeFilterCount
              ? 'Không có resource nào khớp.'
              : 'Chưa có resource nào. Hãy parse dự án.'}
          </p>
        ) : (
          grouped.map(([type, items]) => {
            const open = !collapsed.has(type);
            return (
              <div key={type}>
                <button
                  type="button"
                  onClick={() => toggle(type)}
                  className={cn(
                    'flex h-7 w-full items-center gap-1.5 px-2 text-caption',
                    'text-text-secondary hover:bg-surface-2',
                  )}
                >
                  {open
                    ? <ChevronDown className="h-3 w-3 text-text-quaternary" />
                    : <ChevronRight className="h-3 w-3 text-text-quaternary" />}
                  {typeIcon(type)}
                  <span className="font-emphasis">{TYPE_LABELS[type] ?? type}</span>
                  <span className="ml-auto text-tiny text-text-quaternary">
                    {counts[type] ?? items.length}
                  </span>
                </button>
                {open && items.map((item) => (
                  <button
                    key={item.unique_id}
                    type="button"
                    onClick={() => onSelect(item)}
                    className={cn(
                      'flex h-7 w-full items-center gap-1.5 pl-8 pr-2 text-caption',
                      item.unique_id === activeUniqueId
                        ? 'bg-brand/10 text-text-primary'
                        : 'text-text-secondary hover:bg-surface-2',
                      !item.enabled && 'opacity-60',
                    )}
                    title={item.path ?? item.unique_id}
                  >
                    <span className="truncate">{item.name}</span>
                    {/* A resource somebody disabled is still in the project and
                        still the thing they are looking for. */}
                    {!item.enabled && (
                      <CircleSlash
                        className="h-3 w-3 shrink-0 text-text-quaternary"
                        aria-label="Đang tắt"
                      />
                    )}
                    {item.materialized && item.resource_type === 'model' && (
                      <span className="ml-auto shrink-0 text-tiny text-text-quaternary">
                        {item.materialized}
                      </span>
                    )}
                  </button>
                ))}
              </div>
            );
          })
        )}

        {truncated && (
          <p className="px-3 py-2 text-tiny text-text-tertiary">
            Đang hiển thị {resources.length} trong {total}. Hãy lọc để thu hẹp.
          </p>
        )}
      </div>

      {/* Mirrors the PROJECT tree's "show config files" row: the same promise,
          that nothing is gone, only out of the way. Hidden while a package is
          explicitly selected, because then the choice is already made. */}
      {!filters.packageName && (
        <button
          type="button"
          onClick={() => onFiltersChange({
            ...filters, includePackages: !filters.includePackages,
          })}
          className={cn(
            'flex w-full shrink-0 items-center justify-center gap-1.5 border-t px-2 py-1.5',
            'border-[rgb(var(--border-line))] text-tiny text-text-tertiary',
            'transition-colors hover:bg-surface-2 hover:text-text-secondary',
          )}
        >
          {filters.includePackages ? (
            <>
              <EyeOff className="h-3 w-3" />
              Chỉ hiện resource của dự án
            </>
          ) : (
            <>
              <Eye className="h-3 w-3" />
              Hiện cả resource từ package dbt
            </>
          )}
        </button>
      )}
    </div>
  );
}

function FacetRow({
  label, options, selected, onToggle, format,
}: {
  label: string;
  options: string[];
  selected: string[];
  onToggle: (value: string) => void;
  format?: (value: string) => string;
}) {
  return (
    <div>
      <p className="mb-1 text-tiny uppercase tracking-wide text-text-quaternary">{label}</p>
      <div className="flex flex-wrap gap-1">
        {options.slice(0, 24).map((value) => (
          <button
            key={value}
            type="button"
            onClick={() => onToggle(value)}
            className={cn(
              'rounded-sm px-1.5 py-0.5 text-tiny transition-colors',
              selected.includes(value)
                ? 'bg-brand text-text-inverse'
                : 'bg-surface-2 text-text-secondary hover:bg-surface-3',
            )}
          >
            {format ? format(value) : value}
          </button>
        ))}
      </div>
    </div>
  );
}
