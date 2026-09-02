'use client';

/**
 * The dependency graph, drawn from dbt's own parent/child maps.
 *
 * Not from SQL. dbt has already resolved every `ref` and `source` in order to
 * build in the right order -- including the ones inside a macro, behind a var,
 * or contributed by a package -- and a second answer computed here would be a
 * different graph from the one that actually runs.
 *
 * Layout is by longest-path depth rather than by declared layer. A dbt project
 * has no STAGING/CORE/MART enum, and a graph laid out by folder name would put
 * a model in the wrong column the moment somebody organised their folders
 * differently.
 */

import * as React from 'react';
import { Maximize2, Minimize2, Target, ZoomIn, ZoomOut } from 'lucide-react';

import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import type { LineageNode, TransformLineage } from '@/lib/types';
import { cn } from '@/lib/utils';

const NODE_WIDTH = 168;
const NODE_HEIGHT = 46;
const COLUMN_GAP = 96;
const ROW_GAP = 18;

interface Placed extends LineageNode {
  x: number;
  y: number;
}

/**
 * Depth by longest path from a root.
 *
 * Longest, not shortest: a model that reads both a source and a mart belongs to
 * the right of the mart, or its edge would point backwards and the graph would
 * read as a cycle.
 */
function layout(graph: TransformLineage): { nodes: Placed[]; width: number; height: number } {
  const parents = new Map<string, string[]>();
  const children = new Map<string, string[]>();
  graph.edges.forEach(({ parent, child }) => {
    parents.set(child, [...(parents.get(child) ?? []), parent]);
    children.set(parent, [...(children.get(parent) ?? []), child]);
  });

  const depth = new Map<string, number>();
  const visiting = new Set<string>();

  const resolve = (id: string): number => {
    const known = depth.get(id);
    if (known !== undefined) return known;
    // Guard against a cycle in a stale index. dbt rejects cyclic projects, but
    // an index written before a fix should not hang the browser.
    if (visiting.has(id)) return 0;
    visiting.add(id);
    const own = parents.get(id) ?? [];
    const value = own.length === 0 ? 0 : Math.max(...own.map(resolve)) + 1;
    visiting.delete(id);
    depth.set(id, value);
    return value;
  };

  graph.nodes.forEach((node) => resolve(node.unique_id));

  const columns = new Map<number, LineageNode[]>();
  graph.nodes.forEach((node) => {
    const level = depth.get(node.unique_id) ?? 0;
    columns.set(level, [...(columns.get(level) ?? []), node]);
  });

  const placed: Placed[] = [];
  let height = 0;
  [...columns.entries()]
    .sort(([left], [right]) => left - right)
    .forEach(([level, nodes]) => {
      const sorted = [...nodes].sort((left, right) => left.name.localeCompare(right.name));
      const columnHeight = sorted.length * (NODE_HEIGHT + ROW_GAP);
      height = Math.max(height, columnHeight);
      sorted.forEach((node, index) => {
        placed.push({
          ...node,
          x: level * (NODE_WIDTH + COLUMN_GAP),
          y: index * (NODE_HEIGHT + ROW_GAP),
        });
      });
    });

  const width = (Math.max(...[...columns.keys()], 0) + 1) * (NODE_WIDTH + COLUMN_GAP);
  return { nodes: placed, width: width + 40, height: height + 40 };
}

function nodeColor(type: string): { fill: string; stroke: string } {
  switch (type) {
    case 'source': return { fill: 'rgb(var(--info) / 0.08)', stroke: 'rgb(var(--info) / 0.5)' };
    case 'seed': return { fill: 'rgb(var(--success) / 0.08)', stroke: 'rgb(var(--success) / 0.5)' };
    case 'snapshot': return { fill: 'rgb(var(--warning) / 0.08)', stroke: 'rgb(var(--warning) / 0.5)' };
    case 'exposure': return { fill: 'rgb(var(--brand) / 0.06)', stroke: 'rgb(var(--brand) / 0.4)' };
    default: return { fill: 'rgb(var(--surface-1))', stroke: 'rgb(var(--border-strong))' };
  }
}

interface LineageViewProps {
  graph: TransformLineage;
  focusId: string | null;
  onSelect: (node: LineageNode) => void;
  onOpenFile?: (path: string) => void;
  onShowFull: () => void;
  showingFull: boolean;
  loading?: boolean;
  className?: string;
}

export function LineageView({
  graph, focusId, onSelect, onOpenFile, onShowFull, showingFull, loading, className,
}: LineageViewProps) {
  const [zoom, setZoom] = React.useState(1);
  const { nodes, width, height } = React.useMemo(() => layout(graph), [graph]);
  const positions = React.useMemo(
    () => new Map(nodes.map((node) => [node.unique_id, node])),
    [nodes],
  );

  if (loading) {
    return <Centered className={className}>Đang dựng sơ đồ…</Centered>;
  }
  if (nodes.length === 0) {
    return (
      <Centered className={className}>
        Chưa có sơ đồ. Dự án cần parse thành công trước.
      </Centered>
    );
  }

  return (
    <div className={cn('relative flex h-full flex-col', className)}>
      <div className="flex items-center gap-1.5 border-b border-[rgb(var(--border-line))] px-2 py-1.5">
        <Badge variant={graph.scope === 'RELEASE' ? 'brand' : 'subtle'} size="xs">
          {graph.scope === 'RELEASE' ? 'Bản đang chạy' : 'Bản nháp'}
        </Badge>
        <span className="text-tiny text-text-tertiary">
          {nodes.length}
          {graph.truncated ? ` / ${graph.total_nodes}` : ''} resource
        </span>
        <div className="ml-auto flex items-center gap-1">
          <Button
            variant="ghost" size="xs"
            onClick={() => setZoom((value) => Math.max(0.4, value - 0.15))}
            aria-label="Thu nhỏ"
          >
            <ZoomOut className="h-3.5 w-3.5" />
          </Button>
          <span className="w-9 text-center text-tiny text-text-tertiary">
            {Math.round(zoom * 100)}%
          </span>
          <Button
            variant="ghost" size="xs"
            onClick={() => setZoom((value) => Math.min(2, value + 0.15))}
            aria-label="Phóng to"
          >
            <ZoomIn className="h-3.5 w-3.5" />
          </Button>
          <Button
            variant="ghost" size="xs" onClick={onShowFull}
            leadingIcon={showingFull
              ? <Minimize2 className="h-3.5 w-3.5" />
              : <Maximize2 className="h-3.5 w-3.5" />}
          >
            {showingFull ? 'Thu về lân cận' : 'Toàn bộ sơ đồ'}
          </Button>
        </div>
      </div>

      <div className="flex-1 overflow-auto p-5">
        <svg
          width={width * zoom}
          height={height * zoom}
          viewBox={`0 0 ${width} ${height}`}
          className="overflow-visible"
        >
          <defs>
            <marker
              id="lineage-arrow" viewBox="0 0 10 10" refX="9" refY="5"
              markerWidth="6" markerHeight="6" orient="auto-start-reverse"
            >
              <path d="M 0 0 L 10 5 L 0 10 z" fill="rgb(var(--border-strong))" />
            </marker>
          </defs>

          {graph.edges.map(({ parent, child }) => {
            const from = positions.get(parent);
            const to = positions.get(child);
            if (!from || !to) return null;
            const x1 = from.x + NODE_WIDTH;
            const y1 = from.y + NODE_HEIGHT / 2;
            const x2 = to.x;
            const y2 = to.y + NODE_HEIGHT / 2;
            const midpoint = (x1 + x2) / 2;
            const touchesFocus = parent === focusId || child === focusId;
            return (
              <path
                key={`${parent}->${child}`}
                d={`M ${x1} ${y1} C ${midpoint} ${y1}, ${midpoint} ${y2}, ${x2} ${y2}`}
                fill="none"
                stroke={touchesFocus ? 'rgb(var(--brand))' : 'rgb(var(--border-strong))'}
                strokeWidth={touchesFocus ? 1.6 : 1}
                markerEnd="url(#lineage-arrow)"
                opacity={focusId && !touchesFocus ? 0.35 : 1}
              />
            );
          })}

          {nodes.map((node) => {
            const colors = nodeColor(node.resource_type);
            const isFocus = node.unique_id === focusId;
            return (
              <g
                key={node.unique_id}
                transform={`translate(${node.x}, ${node.y})`}
                className="cursor-pointer"
                onClick={() => onSelect(node)}
                onDoubleClick={() => node.path && onOpenFile?.(node.path)}
                role="button"
                tabIndex={0}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') onSelect(node);
                }}
              >
                <rect
                  width={NODE_WIDTH}
                  height={NODE_HEIGHT}
                  rx={6}
                  fill={colors.fill}
                  stroke={isFocus ? 'rgb(var(--brand))' : colors.stroke}
                  strokeWidth={isFocus ? 2 : 1}
                  opacity={node.enabled ? 1 : 0.5}
                />
                <text
                  x={10} y={19}
                  className="fill-[rgb(var(--text-primary))] text-[11px] font-medium"
                >
                  {node.name.length > 22 ? `${node.name.slice(0, 21)}…` : node.name}
                </text>
                <text
                  x={10} y={34}
                  className="fill-[rgb(var(--text-quaternary))] text-[9px]"
                >
                  {node.resource_type}
                  {node.materialized ? ` · ${node.materialized}` : ''}
                </text>
                {/* AppBI's own contribution: this source is loaded by a
                    Pipeline, which dbt has no way of knowing. */}
                {node.produced_by_pipeline_id && (
                  <circle
                    cx={NODE_WIDTH - 10} cy={12} r={3}
                    fill="rgb(var(--success))"
                  >
                    <title>Do Pipeline của AppBI nạp</title>
                  </circle>
                )}
              </g>
            );
          })}
        </svg>
      </div>

      {graph.truncated && !showingFull && (
        <div className="flex items-center gap-2 border-t border-[rgb(var(--border-line))] px-3 py-1.5">
          <Target className="h-3 w-3 text-text-quaternary" />
          <span className="text-tiny text-text-tertiary">
            Đang hiển thị vùng lân cận. Sơ đồ đầy đủ có {graph.total_nodes} resource.
          </span>
        </div>
      )}
    </div>
  );
}

function Centered({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <div className={cn('flex h-full items-center justify-center', className)}>
      <p className="text-caption text-text-tertiary">{children}</p>
    </div>
  );
}
