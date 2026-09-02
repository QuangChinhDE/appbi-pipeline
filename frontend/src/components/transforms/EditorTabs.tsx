'use client';

/**
 * Open file tabs.
 *
 * A dbt change is almost never one file: a model and its `_schema.yml` move
 * together, and a source change touches the YAML and every model that reads it.
 * Single-file editing was the thing that made V1 feel unlike a dbt tool, so the
 * dirty marker, Save All and close-others all live here.
 */

import * as React from 'react';
import { MoreHorizontal, X } from 'lucide-react';

import { Menu } from '@/components/ui/Menu';
import { cn } from '@/lib/utils';

export interface OpenTab {
  path: string;
  dirty: boolean;
}

interface EditorTabsProps {
  tabs: OpenTab[];
  activePath: string | null;
  onSelect: (path: string) => void;
  onClose: (path: string) => void;
  onCloseOthers: (path: string) => void;
  onCloseAll: () => void;
}

export function EditorTabs({
  tabs, activePath, onSelect, onClose, onCloseOthers, onCloseAll,
}: EditorTabsProps) {
  const strip = React.useRef<HTMLDivElement>(null);

  // Keep the active tab visible when it changes from outside -- clicking a
  // Problems row or a lineage node opens a file that may be scrolled away.
  React.useEffect(() => {
    const active = strip.current?.querySelector<HTMLElement>('[data-active="true"]');
    active?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  }, [activePath]);

  if (tabs.length === 0) return null;

  return (
    <div
      ref={strip}
      role="tablist"
      className={cn(
        'flex h-9 items-stretch overflow-x-auto border-b border-[rgb(var(--border-line))]',
        'bg-surface-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden',
      )}
    >
      {tabs.map((tab) => {
        const active = tab.path === activePath;
        const name = tab.path.split('/').pop() ?? tab.path;
        return (
          <div
            key={tab.path}
            data-active={active}
            role="tab"
            aria-selected={active}
            className={cn(
              'group flex shrink-0 items-center gap-1.5 border-r border-[rgb(var(--border-line))]',
              'px-3 text-caption transition-colors',
              active
                ? 'bg-surface-0 text-text-primary'
                : 'text-text-tertiary hover:bg-surface-2 hover:text-text-secondary',
            )}
          >
            <button
              type="button"
              onClick={() => onSelect(tab.path)}
              className="flex items-center gap-1.5 py-1"
              title={tab.path}
            >
              <span className={cn(active && 'font-emphasis')}>{name}</span>
              {tab.dirty && (
                <span
                  className="h-1.5 w-1.5 rounded-full bg-brand"
                  aria-label="Chưa lưu"
                />
              )}
            </button>
            <button
              type="button"
              onClick={(event) => { event.stopPropagation(); onClose(tab.path); }}
              aria-label={`Đóng ${name}`}
              className={cn(
                'rounded-sm p-0.5 transition-opacity',
                'hover:bg-surface-3',
                active ? 'opacity-60 hover:opacity-100' : 'opacity-0 group-hover:opacity-60',
              )}
            >
              <X className="h-3 w-3" />
            </button>
          </div>
        );
      })}

      <div className="ml-auto flex shrink-0 items-center px-2">
        <Menu
          label="Tác vụ tab"
          items={[
            {
              id: 'close-others',
              label: 'Đóng các tab khác',
              disabled: !activePath || tabs.length < 2,
              onSelect: () => activePath && onCloseOthers(activePath),
            },
            { id: 'close-all', label: 'Đóng tất cả', onSelect: onCloseAll },
          ]}
          trigger={
            <span className="rounded-sm p-1 text-text-tertiary hover:bg-surface-2">
              <MoreHorizontal className="h-3.5 w-3.5" />
            </span>
          }
        />
      </div>
    </div>
  );
}
