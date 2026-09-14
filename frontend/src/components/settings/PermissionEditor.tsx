'use client';

/**
 * One person's permissions, module by module.
 *
 * The screen this replaces offered a single dropdown with six role names in
 * it. Picking one was the only permission decision an administrator could
 * make, so "let Minh acknowledge alerts without letting him rename pipelines"
 * had no answer short of editing Python and redeploying.
 *
 * Two controls per row, and the split is the point. The **level** is the
 * shorthand -- five rungs, offered only where they differ -- and covers the
 * decision almost every time. The **actions** underneath are the truth: a
 * level writes them, and changing one by hand leaves the level reading
 * "custom", which is honest rather than tidy.
 *
 * Nothing here is a list this file maintains. Modules, their actions, which
 * levels they offer and what each level grants all arrive from the API, so the
 * editor cannot come to offer a module the backend has never heard of -- or,
 * worse, omit one the backend gates on, which no administrator could then ever
 * grant.
 */

import * as React from 'react';
import { ChevronDown, RotateCcw } from 'lucide-react';

import { cn } from '@/lib/utils';
import { useI18n } from '@/providers/LanguageProvider';
import { Badge } from '@/components/ui/Badge';
import { Select } from '@/components/ui/Input';
import type { PermissionCatalog, PermissionMap } from '@/lib/types';

/** The three worth a second look: reading rows, re-reading history, and
 *  replacing the secret a connection authenticates with. */
const SENSITIVE = new Set(['view_data', 'reset', 'manage_credentials']);

function sameSet(left: string[] = [], right: string[] = []): boolean {
  if (left.length !== right.length) return false;
  const held = new Set(left);
  return right.every((item) => held.has(item));
}

/** Which level names this exact set, or `custom` when none does. */
function levelOf(
  module: PermissionCatalog['modules'][number], actions: string[],
): string {
  return module.levels.find(
    (level) => sameSet(module.level_actions[level] ?? [], actions),
  ) ?? 'custom';
}

export function PermissionEditor({
  catalog, value, preset, onChange, disabled,
}: {
  catalog: PermissionCatalog;
  value: PermissionMap;
  /** What the chosen role grants, so a row can say it has departed and offer
   *  the way back. Without this, the only way to undo one experiment is to
   *  remember what the preset said. */
  preset?: PermissionMap;
  onChange: (next: PermissionMap) => void;
  disabled?: boolean;
}) {
  const { t, tf } = useI18n();
  const [open, setOpen] = React.useState<string | null>(null);

  const setModule = (module: string, actions: string[]) => {
    onChange({ ...value, [module]: [...actions].sort() });
  };

  return (
    <div className="overflow-hidden rounded-lg border border-[rgb(var(--border-line))]">
      <div className="hidden border-b border-[rgb(var(--border-line))] bg-surface-2 px-3 py-2 text-tiny uppercase tracking-[0.08em] text-text-quaternary sm:grid sm:grid-cols-[minmax(0,1fr)_11rem_auto] sm:gap-3">
        <span className="font-emphasis">{t('perm.colModule')}</span>
        <span className="font-emphasis">{t('perm.colLevel')}</span>
        <span />
      </div>

      <div className="divide-y divide-[rgb(var(--border-line))]">
        {catalog.modules.map((module) => {
          const held = value[module.module] ?? [];
          const level = levelOf(module, held);
          const departed = preset
            ? !sameSet(preset[module.module] ?? [], held)
            : false;
          const expanded = open === module.module;

          return (
            <div key={module.module} className="px-3 py-2.5">
              <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_11rem_auto] sm:items-center sm:gap-3">
                <div className="min-w-0">
                  <p className="text-caption font-emphasis text-text-primary">
                    {tf([`module.${module.module}`], module.module)}
                  </p>
                  {/* The sensitive powers named on the row, because they are
                      what somebody scanning this page is looking for. */}
                  <div className="mt-0.5 flex flex-wrap items-center gap-1">
                    {held.filter((a) => SENSITIVE.has(a)).map((action) => (
                      <Badge key={action} variant="subtle" size="xs">
                        {tf([`perm.flag.${action}`], action)}
                      </Badge>
                    ))}
                    {departed && (
                      <span className="text-tiny text-text-quaternary">
                        {t('perm.departedFromPreset')}
                      </span>
                    )}
                  </div>
                </div>

                <Select
                  size="sm"
                  className="w-44"
                  value={level}
                  disabled={disabled}
                  aria-label={t('perm.levelFor', {
                    module: tf([`module.${module.module}`], module.module),
                  })}
                  onChange={(event) => {
                    const chosen = event.target.value;
                    if (chosen === 'custom') return;
                    setModule(module.module, module.level_actions[chosen] ?? []);
                  }}
                >
                  {module.levels.map((option) => (
                    <option key={option} value={option}>
                      {tf([`perm.level.${option}`], option)}
                    </option>
                  ))}
                  {/* Only ever present when the actions match no level. Picking
                      it does nothing -- it is a readout, not a choice. */}
                  {level === 'custom' && (
                    <option value="custom">{t('perm.level.custom')}</option>
                  )}
                </Select>

                <div className="flex items-center gap-1">
                  {departed && !disabled && preset && (
                    <button
                      type="button"
                      className="inline-flex items-center gap-1 rounded px-1.5 py-1 text-tiny text-text-tertiary transition-colors hover:text-text-primary"
                      onClick={() => setModule(module.module, preset[module.module] ?? [])}
                    >
                      <RotateCcw className="h-3 w-3" />
                      {t('perm.resetToPreset')}
                    </button>
                  )}
                  <button
                    type="button"
                    aria-expanded={expanded}
                    className="inline-flex items-center gap-1 rounded px-1.5 py-1 text-tiny text-text-tertiary transition-colors hover:text-text-primary"
                    onClick={() => setOpen(expanded ? null : module.module)}
                  >
                    {t('perm.advanced')}
                    <ChevronDown
                      className={cn('h-3 w-3 transition-transform', expanded && 'rotate-180')}
                    />
                  </button>
                </div>
              </div>

              {expanded && (
                <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1.5 rounded-md bg-surface-2 px-3 py-2">
                  {module.actions.map((action) => (
                    <label
                      key={action}
                      className={cn(
                        'flex items-center gap-1.5 text-caption text-text-secondary',
                        disabled ? 'cursor-not-allowed opacity-60' : 'cursor-pointer',
                      )}
                    >
                      <input
                        type="checkbox"
                        checked={held.includes(action)}
                        disabled={disabled}
                        onChange={(event) => {
                          const next = new Set(held);
                          if (event.target.checked) {
                            next.add(action);
                            // Nothing can be done to a thing that cannot be
                            // seen. The API would accept the pair and the
                            // product would refuse every request made with it.
                            next.add('view');
                          } else {
                            next.delete(action);
                            // Removing sight removes everything that depended
                            // on it, rather than leaving a row that claims
                            // powers it cannot use.
                            if (action === 'view') next.clear();
                          }
                          setModule(module.module, [...next]);
                        }}
                      />
                      <span className={cn(SENSITIVE.has(action) && 'font-emphasis')}>
                        {tf([`perm.action.${action}`], action)}
                      </span>
                    </label>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
