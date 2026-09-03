'use client';

/**
 * The PROJECT view: the real file tree of a real dbt project.
 *
 * Not a curated list of the resources AppBI understands. A dbt engineer opening
 * this should see `dbt_project.yml`, `models/`, `macros/`, `packages.yml` and
 * whatever else the project contains, in the places they expect them -- because
 * that is what the project is.
 */

import * as React from 'react';
import {
  ChevronDown, ChevronRight, Eye, EyeOff, File, FileCode, FileJson,
  FileSpreadsheet, FileText, FolderClosed, FolderOpen, MoreHorizontal,
  Plus, Search, Wand2,
} from 'lucide-react';

import { Button } from '@/components/ui/Button';
import { Menu } from '@/components/ui/Menu';
import type { FileNode } from '@/lib/types';
import { cn } from '@/lib/utils';

/** Folders a dbt project always shows first, in dbt's own conventional order. */
const ORDER = [
  'models', 'macros', 'tests', 'seeds', 'snapshots', 'analyses',
];

function iconFor(node: FileNode, open: boolean) {
  if (node.type === 'directory') {
    return open
      ? <FolderOpen className="h-3.5 w-3.5 text-brand" />
      : <FolderClosed className="h-3.5 w-3.5 text-text-quaternary" />;
  }
  const name = node.name.toLowerCase();
  if (name.endsWith('.sql')) return <FileCode className="h-3.5 w-3.5 text-brand" />;
  if (name.endsWith('.yml') || name.endsWith('.yaml')) {
    return <FileText className="h-3.5 w-3.5 text-warning" />;
  }
  if (name.endsWith('.csv') || name.endsWith('.tsv')) {
    return <FileSpreadsheet className="h-3.5 w-3.5 text-success" />;
  }
  if (name.endsWith('.json')) return <FileJson className="h-3.5 w-3.5 text-info" />;
  if (name.endsWith('.md')) return <FileText className="h-3.5 w-3.5 text-text-tertiary" />;
  return <File className="h-3.5 w-3.5 text-text-quaternary" />;
}

function sortNodes(nodes: FileNode[]): FileNode[] {
  return [...nodes].sort((left, right) => {
    if (left.type !== right.type) return left.type === 'directory' ? -1 : 1;
    const leftRank = ORDER.indexOf(left.name);
    const rightRank = ORDER.indexOf(right.name);
    if (leftRank !== -1 || rightRank !== -1) {
      return (leftRank === -1 ? 99 : leftRank) - (rightRank === -1 ? 99 : rightRank);
    }
    return left.name.localeCompare(right.name);
  });
}

/** Paths of every file whose name matches, plus their ancestor folders. */
function filterTree(nodes: FileNode[], needle: string): FileNode[] {
  if (!needle) return nodes;
  const lower = needle.toLowerCase();
  const walk = (items: FileNode[]): FileNode[] =>
    items.flatMap((node) => {
      if (node.type === 'file') {
        return node.path.toLowerCase().includes(lower) ? [node] : [];
      }
      const children = walk(node.children ?? []);
      if (children.length === 0 && !node.name.toLowerCase().includes(lower)) return [];
      return [{ ...node, children }];
    });
  return walk(nodes);
}

/**
 * Files a dbt project needs on disk but nobody edits by hand.
 *
 * `.gitkeep` exists only so Git tracks an empty directory; `.gitignore`,
 * `packages.yml` and `README.md` are scaffolding a person opens once, if ever.
 * Hiding them is a view filter and nothing more -- the files stay in the
 * project, in Git, and in every export.
 */
const TREE_VIEW_KEY = 'appbi.transform.tree-view';

const NOISE_FILES = new Set(['.gitkeep', '.gitignore', 'packages.yml', 'README.md']);

/** True when the whole subtree is scaffolding: empty folders, markers, no real files. */
function isNoise(node: FileNode): boolean {
  if (node.type === 'file') return NOISE_FILES.has(node.name);
  const children = node.children ?? [];
  return children.length === 0 || children.every(isNoise);
}

/**
 * The tree with scaffolding removed.
 *
 * A folder survives only if something inside it survives, so `macros/` holding
 * one `.gitkeep` disappears while `macros/` holding a real macro stays.
 */
function simplifyTree(nodes: FileNode[]): FileNode[] {
  return nodes.flatMap((node) => {
    if (node.type === 'file') return NOISE_FILES.has(node.name) ? [] : [node];
    const children = simplifyTree(node.children ?? []);
    if (children.length === 0) return [];
    return [{ ...node, children }];
  });
}

/** How many nodes the simple view is hiding, for the toggle's label. */
function countHidden(nodes: FileNode[]): number {
  return nodes.reduce((total, node) => {
    if (node.type === 'file') return total + (NOISE_FILES.has(node.name) ? 1 : 0);
    const children = node.children ?? [];
    if (isNoise(node)) return total + 1 + children.length;
    return total + countHidden(children);
  }, 0);
}

interface ProjectFileTreeProps {
  tree: FileNode[];
  activePath: string | null;
  /** Paths with unsaved edits, so the dot appears on the tree as well as the tab. */
  dirtyPaths: Set<string>;
  /** Git working-tree status per path, when the project is Git-backed. */
  gitChanges?: Map<string, 'A' | 'M' | 'D'>;
  onOpen: (path: string) => void;
  onCreate: (parentPath: string) => void;
  onRename: (path: string) => void;
  onDelete: (path: string) => void;
  onDuplicate: (path: string) => void;
  /** Opens the generator. Absent when the viewer cannot edit. */
  onGenerate?: () => void;
  canEdit: boolean;
}

export function ProjectFileTree({
  tree,
  activePath,
  dirtyPaths,
  gitChanges,
  onOpen,
  onCreate,
  onRename,
  onDelete,
  onDuplicate,
  onGenerate,
  canEdit,
}: ProjectFileTreeProps) {
  const [query, setQuery] = React.useState('');
  const [collapsed, setCollapsed] = React.useState<Set<string>>(() => new Set());
  // Simple by default: a first-time user should see their models, not five
  // empty folders and a `.gitkeep`. The choice is remembered per browser --
  // somebody who switched to the full tree meant it.
  const [simple, setSimple] = React.useState(true);

  React.useEffect(() => {
    try {
      const stored = window.localStorage.getItem(TREE_VIEW_KEY);
      if (stored) setSimple(stored === 'simple');
    } catch { /* private mode, or site data blocked */ }
  }, []);

  const chooseView = (next: boolean) => {
    setSimple(next);
    try {
      window.localStorage.setItem(TREE_VIEW_KEY, next ? 'simple' : 'full');
    } catch { /* not worth failing the click over */ }
  };

  const hidden = React.useMemo(() => countHidden(tree), [tree]);
  const scoped = React.useMemo(
    () => (simple ? simplifyTree(tree) : tree),
    [tree, simple],
  );
  const visible = React.useMemo(() => filterTree(scoped, query), [scoped, query]);

  // A search result the user cannot see is not a result: while filtering,
  // every folder is treated as open regardless of what was collapsed before.
  const isOpen = (path: string) => (query ? true : !collapsed.has(path));

  const toggle = (path: string) => {
    setCollapsed((current) => {
      const next = new Set(current);
      if (next.has(path)) next.delete(path); else next.add(path);
      return next;
    });
  };

  const renderNodes = (nodes: FileNode[], depth: number): React.ReactNode =>
    sortNodes(nodes).map((node) => {
      const open = isOpen(node.path);
      const active = node.path === activePath;
      const dirty = dirtyPaths.has(node.path);
      const git = gitChanges?.get(node.path);

      return (
        <div key={node.path}>
          <div
            className={cn(
              'group flex h-7 items-center gap-1.5 rounded-sm pr-1 text-caption',
              'cursor-pointer select-none',
              active
                ? 'bg-brand/10 text-text-primary'
                : 'text-text-secondary hover:bg-surface-2',
            )}
            style={{ paddingLeft: `${depth * 12 + 6}px` }}
            onClick={() => (node.type === 'directory' ? toggle(node.path) : onOpen(node.path))}
            role="treeitem"
            aria-selected={active}
            aria-expanded={node.type === 'directory' ? open : undefined}
            tabIndex={0}
            onKeyDown={(event) => {
              if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                if (node.type === 'directory') toggle(node.path); else onOpen(node.path);
              }
            }}
          >
            {node.type === 'directory' ? (
              open
                ? <ChevronDown className="h-3 w-3 shrink-0 text-text-quaternary" />
                : <ChevronRight className="h-3 w-3 shrink-0 text-text-quaternary" />
            ) : (
              <span className="w-3 shrink-0" />
            )}
            {iconFor(node, open)}
            <span className={cn('truncate', active && 'font-emphasis')}>{node.name}</span>

            {/* Unsaved beats committed: a file can be both, and the one that
                needs action now is the unsaved edit. */}
            {dirty ? (
              <span
                className="ml-auto h-1.5 w-1.5 shrink-0 rounded-full bg-brand"
                title="Chưa lưu"
              />
            ) : git ? (
              <span
                className={cn(
                  'ml-auto shrink-0 font-mono text-tiny',
                  git === 'A' && 'text-success',
                  git === 'M' && 'text-warning',
                  git === 'D' && 'text-danger',
                )}
                title={
                  git === 'A' ? 'Thêm mới' : git === 'M' ? 'Đã sửa' : 'Đã xoá'
                }
              >
                {git}
              </span>
            ) : null}

            {canEdit && (
              <Menu
                align="start"
                label={`Tác vụ cho ${node.name}`}
                items={[
                  ...(node.type === 'directory'
                    ? [{
                        id: 'new', label: 'Tệp mới…',
                        onSelect: () => onCreate(node.path),
                      }]
                    : []),
                  { id: 'rename', label: 'Đổi tên…', onSelect: () => onRename(node.path) },
                  ...(node.type === 'file'
                    ? [{
                        id: 'duplicate', label: 'Nhân bản',
                        onSelect: () => onDuplicate(node.path),
                      }]
                    : []),
                  {
                    id: 'delete', label: 'Xoá', destructive: true,
                    onSelect: () => onDelete(node.path),
                  },
                ]}
                trigger={
                  <span
                    className={cn(
                      'shrink-0 rounded-sm p-0.5 opacity-0 transition-opacity',
                      'hover:bg-surface-3 group-hover:opacity-100 focus-within:opacity-100',
                      dirty || git ? 'ml-1' : 'ml-auto',
                    )}
                  >
                    <MoreHorizontal className="h-3.5 w-3.5 text-text-tertiary" />
                  </span>
                }
              />
            )}
          </div>
          {node.type === 'directory' && open && node.children
            ? renderNodes(node.children, depth + 1)
            : null}
        </div>
      );
    });

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-1.5 border-b border-[rgb(var(--border-line))] px-2 py-1.5">
        <div className="relative flex-1">
          <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-text-quaternary" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Tìm tệp"
            className={cn(
              'h-7 w-full rounded-sm bg-surface-2 pl-7 pr-2 text-caption',
              'text-text-primary placeholder:text-text-quaternary',
              'focus:outline-none focus:ring-1 focus:ring-brand/40',
            )}
          />
        </div>
        {canEdit && onGenerate && (
          <Button
            variant="ghost"
            size="xs"
            onClick={onGenerate}
            aria-label="Tạo model từ bảng"
            title="Tạo model từ bảng trong kho dữ liệu"
          >
            <Wand2 className="h-3.5 w-3.5" />
          </Button>
        )}
        {canEdit && (
          <Button
            variant="ghost"
            size="xs"
            onClick={() => onCreate('')}
            aria-label="Tệp mới"
            title="Tệp mới"
          >
            <Plus className="h-3.5 w-3.5" />
          </Button>
        )}
      </div>

      <div className="flex-1 overflow-auto py-1" role="tree" aria-label="Tệp dự án">
        {visible.length === 0 ? (
          <p className="px-3 py-6 text-center text-caption text-text-tertiary">
            {query ? 'Không có tệp nào khớp.' : 'Dự án chưa có tệp nào.'}
          </p>
        ) : (
          renderNodes(visible, 0)
        )}
      </div>

      {/* Only worth a row when there is actually something hidden, and never
          while searching -- a filtered tree already explains itself. */}
      {hidden > 0 && !query && (
        <button
          type="button"
          onClick={() => chooseView(!simple)}
          className={cn(
            'flex w-full shrink-0 items-center justify-center gap-1.5 border-t px-2 py-1.5',
            'border-[rgb(var(--border-line))] text-tiny text-text-tertiary',
            'transition-colors hover:bg-surface-2 hover:text-text-secondary',
          )}
        >
          {simple ? (
            <>
              <Eye className="h-3 w-3" />
              Hiện {hidden} tệp cấu hình
            </>
          ) : (
            <>
              <EyeOff className="h-3 w-3" />
              Ẩn tệp cấu hình
            </>
          )}
        </button>
      )}
    </div>
  );
}
