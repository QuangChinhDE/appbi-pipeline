'use client';

import * as React from 'react';
import { Maximize2, Minimize2, Scan, ZoomIn, ZoomOut } from 'lucide-react';

import type { TransformLineage } from '@/lib/types';
import { cn } from '@/lib/utils';
import { EmptyState, Spinner } from '@/components/ui/Feedback';
import { IconButton } from '@/components/ui/Button';

export type LineageCopy = {
  lineage: string;
  lineageDescription: string;
  noLineage: string;
  zoomIn: string;
  zoomOut: string;
  zoomFit: string;
  expand: string;
  collapse: string;
  legendSource: string;
  legendSelected: string;
  legendHealthy: string;
};

const NODE_W = 180;
const NODE_H = 46;
const COL_GAP = 56;
const ROW_GAP = 18;
const PAD = 18;

/**
 * The dependency graph, drawn where the results are.
 *
 * Kept beside Preview and Results rather than behind a dialog: the question it
 * answers -- what feeds this, what breaks if I change it -- is asked while
 * reading the SQL, and a dialog hides the SQL to answer it.
 */
export function LineageView({
  data, loading, copy, selectedName, expanded, onToggleExpand,
}: {
  data?: TransformLineage;
  loading: boolean;
  copy: LineageCopy;
  /** The model open in the editor, highlighted so its place is obvious. */
  selectedName?: string;
  expanded: boolean;
  onToggleExpand: () => void;
}) {
  const [zoom, setZoom] = React.useState(1);
  const [focused, setFocused] = React.useState<string | null>(null);

  const graph = React.useMemo(() => {
    if (!data?.nodes.length) return null;
    const incoming = new Map<string, string[]>();
    const outgoing = new Map<string, string[]>();
    for (const edge of data.edges) {
      incoming.set(edge.to, [...(incoming.get(edge.to) ?? []), edge.from]);
      outgoing.set(edge.from, [...(outgoing.get(edge.from) ?? []), edge.to]);
    }
    // Longest-path layering: a node sits one column right of its deepest parent.
    const depth = new Map<string, number>();
    const resolve = (id: string, seen: Set<string>): number => {
      if (depth.has(id)) return depth.get(id)!;
      if (seen.has(id)) return 0;
      seen.add(id);
      const parents = incoming.get(id) ?? [];
      const value = parents.length
        ? Math.max(...parents.map((parent) => resolve(parent, seen) + 1)) : 0;
      depth.set(id, value);
      return value;
    };
    for (const node of data.nodes) resolve(node.id, new Set());

    const columns: (typeof data.nodes)[] = [];
    for (const node of data.nodes) {
      const level = depth.get(node.id) ?? 0;
      (columns[level] ??= []).push(node);
    }
    const position = new Map<string, { x: number; y: number }>();
    columns.forEach((column, columnIndex) => column.forEach((node, rowIndex) => {
      position.set(node.id, {
        x: PAD + columnIndex * (NODE_W + COL_GAP),
        y: PAD + rowIndex * (NODE_H + ROW_GAP),
      });
    }));
    return {
      position,
      width: PAD * 2 + columns.length * NODE_W + Math.max(columns.length - 1, 0) * COL_GAP,
      height: PAD * 2 + Math.max(...columns.map((c) => c.length), 1) * (NODE_H + ROW_GAP),
      related: (id: string) => new Set([
        id, ...(incoming.get(id) ?? []), ...(outgoing.get(id) ?? []),
      ]),
    };
  }, [data]);

  const highlighted = focused && graph ? graph.related(focused) : null;

  const accent: Record<string, string> = {
    SOURCE: 'border-l-info',
    PIPELINE: 'border-l-info',
    DATA_ASSET: 'border-l-success',
    MODEL: 'border-l-brand',
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex h-7 shrink-0 items-center gap-1 px-1">
        <span className="text-tiny text-text-tertiary">{copy.lineageDescription}</span>
        <div className="ml-auto flex items-center gap-0.5">
          <IconButton size="xs" variant="ghost" aria-label={copy.zoomOut} title={copy.zoomOut}
            onClick={() => setZoom((value) => Math.max(0.5, Number((value - 0.15).toFixed(2))))}>
            <ZoomOut className="h-3.5 w-3.5" />
          </IconButton>
          <IconButton size="xs" variant="ghost" aria-label={copy.zoomIn} title={copy.zoomIn}
            onClick={() => setZoom((value) => Math.min(1.6, Number((value + 0.15).toFixed(2))))}>
            <ZoomIn className="h-3.5 w-3.5" />
          </IconButton>
          <IconButton size="xs" variant="ghost" aria-label={copy.zoomFit} title={copy.zoomFit}
            onClick={() => setZoom(1)}>
            <Scan className="h-3.5 w-3.5" />
          </IconButton>
          <IconButton size="xs" variant="ghost"
            aria-label={expanded ? copy.collapse : copy.expand}
            title={expanded ? copy.collapse : copy.expand}
            onClick={onToggleExpand}>
            {expanded
              ? <Minimize2 className="h-3.5 w-3.5" />
              : <Maximize2 className="h-3.5 w-3.5" />}
          </IconButton>
        </div>
      </div>

      {loading ? <Spinner /> : !graph ? (
        <EmptyState title={copy.noLineage} compact />
      ) : (
        <>
          <div className="min-h-0 flex-1 overflow-auto rounded-md border border-[rgb(var(--border-line))] bg-surface-2">
            <div
              className="relative origin-top-left"
              style={{ width: graph.width, height: graph.height, transform: `scale(${zoom})` }}
            >
              <svg className="absolute inset-0 h-full w-full" aria-hidden>
                <defs>
                  <marker id="lineage-arrow" viewBox="0 0 8 8" refX="7" refY="4"
                    markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                    <path d="M 0 1 L 7 4 L 0 7 z" className="fill-[rgb(var(--border-strong))]" />
                  </marker>
                </defs>
                {data!.edges.map((edge, index) => {
                  const from = graph.position.get(edge.from);
                  const to = graph.position.get(edge.to);
                  if (!from || !to) return null;
                  const x1 = from.x + NODE_W;
                  const y1 = from.y + NODE_H / 2;
                  const x2 = to.x - 7;
                  const y2 = to.y + NODE_H / 2;
                  const mid = (x1 + x2) / 2;
                  const lit = !highlighted
                    || (highlighted.has(edge.from) && highlighted.has(edge.to));
                  return (
                    <path
                      key={`${edge.from}-${edge.to}-${index}`}
                      d={`M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`}
                      fill="none" strokeWidth={1.5}
                      markerEnd="url(#lineage-arrow)"
                      className="stroke-[rgb(var(--border-strong))]"
                      opacity={lit ? 1 : 0.28}
                    />
                  );
                })}
              </svg>

              {data!.nodes.map((node) => {
                const at = graph.position.get(node.id)!;
                const lit = !highlighted || highlighted.has(node.id);
                const isCurrent = Boolean(selectedName) && node.label === selectedName;
                return (
                  <button
                    key={node.id} type="button"
                    onMouseEnter={() => setFocused(node.id)}
                    onMouseLeave={() => setFocused(null)}
                    onFocus={() => setFocused(node.id)}
                    onBlur={() => setFocused(null)}
                    style={{ left: at.x, top: at.y, width: NODE_W, height: NODE_H }}
                    className={cn(
                      'absolute flex flex-col justify-center rounded-md border border-l-2 px-2.5 text-left transition-opacity',
                      accent[node.type] ?? 'border-l-neutral',
                      isCurrent
                        ? 'border-brand bg-brand/[0.07] shadow-focus-brand'
                        : 'border-[rgb(var(--border-line))] bg-surface-1',
                      lit ? 'opacity-100' : 'opacity-35',
                    )}
                  >
                    <span className="truncate text-[10px] uppercase tracking-wide text-text-quaternary">
                      {node.layer ?? node.type.replace('_', ' ')}
                    </span>
                    <span className="truncate text-caption font-emphasis text-text-primary">
                      {node.label}
                    </span>
                    {node.materialization && (
                      <span className="truncate text-[10px] text-text-quaternary">
                        {node.materialization}
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
          </div>

          {/* The colours mean something; saying so costs one line. */}
          <div className="flex shrink-0 items-center gap-4 pt-1.5 text-[10px] text-text-quaternary">
            <span className="flex items-center gap-1.5">
              <span className="h-2.5 w-0.5 rounded-sm bg-info" />{copy.legendSource}
            </span>
            <span className="flex items-center gap-1.5">
              <span className="h-2.5 w-0.5 rounded-sm bg-success" />{copy.legendHealthy}
            </span>
            <span className="flex items-center gap-1.5">
              <span className="h-2.5 w-0.5 rounded-sm bg-brand" />{copy.legendSelected}
            </span>
          </div>
        </>
      )}
    </div>
  );
}
