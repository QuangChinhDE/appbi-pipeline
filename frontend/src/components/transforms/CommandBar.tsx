'use client';

/**
 * The command bar.
 *
 * It reads like a dbt command line, and that is the point -- somebody who knows
 * dbt should be able to type what they already know. But nothing here is sent
 * as a string: the bar parses what was typed into a command name plus a
 * selector, and the API takes those as separate typed fields.
 *
 * The selector itself is *not* interpreted. dbt's node selection has graph
 * operators, set unions, `tag:`/`path:`/`config:` methods and state comparison;
 * re-implementing any of it here would produce a different set from the one dbt
 * actually runs. So it is passed through verbatim and dbt decides.
 */

import * as React from 'react';
import { ChevronUp, History, Play, Terminal } from 'lucide-react';

import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import type { DbtCommand, ResourceSummary } from '@/lib/types';
import { cn } from '@/lib/utils';

/** Commands offered by name, with a one-line description of what dbt does. */
const COMMANDS: { command: DbtCommand; hint: string; writes: boolean }[] = [
  { command: 'build', hint: 'Chạy model rồi test, theo đúng thứ tự phụ thuộc', writes: true },
  { command: 'run', hint: 'Chỉ chạy model, không test', writes: true },
  { command: 'test', hint: 'Chỉ chạy test', writes: true },
  { command: 'compile', hint: 'Dịch Jinja thành SQL, không chạm kho dữ liệu', writes: false },
  { command: 'show', hint: 'Xem thử kết quả của một model', writes: false },
  { command: 'seed', hint: 'Nạp các tệp CSV trong seeds/', writes: true },
  { command: 'snapshot', hint: 'Chạy snapshot', writes: true },
  { command: 'source-freshness', hint: 'Kiểm tra độ mới của source', writes: false },
  { command: 'docs-generate', hint: 'Sinh catalog và tài liệu', writes: false },
  { command: 'parse', hint: 'Đọc lại dự án', writes: false },
  { command: 'deps', hint: 'Cài package trong packages.yml', writes: false },
  { command: 'ls', hint: 'Liệt kê resource khớp selector', writes: false },
];

const COMMAND_NAMES = COMMANDS.map((item) => item.command);

export interface ParsedCommand {
  command: DbtCommand;
  selector: string | null;
  exclude: string | null;
  fullRefresh: boolean;
}

/**
 * Turn typed text into a structured command.
 *
 * Tolerant of the `dbt ` prefix and of `-s`/`--select`, because those are what
 * somebody with dbt muscle memory types. Anything it cannot place becomes part
 * of the selector rather than being silently dropped.
 */
export function parseCommandLine(input: string): ParsedCommand | null {
  const tokens = input.trim().split(/\s+/).filter(Boolean);
  if (tokens.length === 0) return null;
  if (tokens[0] === 'dbt') tokens.shift();
  if (tokens.length === 0) return null;

  // `source freshness` is two words on the command line, one command here.
  let command = tokens.shift() as string;
  if (command === 'source' && tokens[0] === 'freshness') {
    tokens.shift();
    command = 'source-freshness';
  }
  if (command === 'docs' && tokens[0] === 'generate') {
    tokens.shift();
    command = 'docs-generate';
  }
  if (!COMMAND_NAMES.includes(command as DbtCommand)) return null;

  const selectors: string[] = [];
  const excludes: string[] = [];
  let fullRefresh = false;
  let mode: 'select' | 'exclude' = 'select';

  tokens.forEach((token) => {
    if (token === '--select' || token === '-s' || token === '--models' || token === '-m') {
      mode = 'select';
    } else if (token === '--exclude') {
      mode = 'exclude';
    } else if (token === '--full-refresh') {
      fullRefresh = true;
    } else if (token.startsWith('-')) {
      // An unrecognised flag: ignored rather than passed on, because the API
      // takes typed fields and would reject it anyway.
    } else if (mode === 'select') {
      selectors.push(token);
    } else {
      excludes.push(token);
    }
  });

  return {
    command: command as DbtCommand,
    selector: selectors.length > 0 ? selectors.join(' ') : null,
    exclude: excludes.length > 0 ? excludes.join(' ') : null,
    fullRefresh,
  };
}

interface CommandBarProps {
  onRun: (command: ParsedCommand) => void;
  running: boolean;
  onCancel?: () => void;
  resources: ResourceSummary[];
  history: string[];
  environmentName: string | null;
  environmentProtected: boolean;
  disabled?: boolean;
}

export function CommandBar({
  onRun, running, onCancel, resources, history, environmentName,
  environmentProtected, disabled,
}: CommandBarProps) {
  const [value, setValue] = React.useState('');
  const [open, setOpen] = React.useState(false);
  const inputRef = React.useRef<HTMLInputElement>(null);

  const parsed = React.useMemo(() => parseCommandLine(value), [value]);

  // Suggestions follow what has been typed so far: a command when the line is
  // empty, resource names once a selector is being written.
  const suggestions = React.useMemo(() => {
    const trimmed = value.trim();
    const tokens = trimmed.split(/\s+/).filter(Boolean);
    const head = tokens[0] === 'dbt' ? tokens.slice(1) : tokens;

    if (head.length <= 1) {
      const prefix = (head[0] ?? '').toLowerCase();
      return COMMANDS
        .filter((item) => item.command.startsWith(prefix))
        .map((item) => ({
          label: `dbt ${item.command}`,
          detail: item.hint,
          apply: `dbt ${item.command} `,
          badge: item.writes ? 'ghi' : 'đọc',
        }));
    }

    const last = head[head.length - 1] ?? '';
    if (last.startsWith('-')) return [];
    // Strip dbt's graph operators before matching, so `+fct_or` still finds
    // `fct_orders` and the operator is preserved on the way back in.
    const bare = last.replace(/^[+@]+/, '').replace(/\+$/, '').toLowerCase();
    if (bare.length < 1) return [];
    const prefix = last.slice(0, last.length - bare.length - (last.endsWith('+') ? 1 : 0));

    return resources
      .filter((item) => item.name.toLowerCase().includes(bare))
      .slice(0, 8)
      .map((item) => ({
        label: item.name,
        detail: `${item.resource_type}${item.materialized ? ` · ${item.materialized}` : ''}`,
        apply: `${head.slice(0, -1).map((token) => token).join(' ')} ${prefix}${item.name} `,
        badge: item.resource_type,
      }))
      .map((item) => ({ ...item, apply: `dbt ${item.apply.trim()} ` }));
  }, [value, resources]);

  const run = () => {
    if (!parsed || running || disabled) return;
    onRun(parsed);
    setOpen(false);
  };

  return (
    <div className="relative border-t border-[rgb(var(--border-line))] bg-surface-1">
      {open && (suggestions.length > 0 || history.length > 0) && (
        <div className="absolute bottom-full left-0 right-0 z-30 max-h-72 overflow-auto border-t border-[rgb(var(--border-line))] bg-surface-1 shadow-lg">
          {suggestions.length > 0 ? (
            suggestions.map((item) => (
              <button
                key={item.label}
                type="button"
                onMouseDown={(event) => {
                  event.preventDefault();
                  setValue(item.apply);
                  inputRef.current?.focus();
                }}
                className="flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-surface-2"
              >
                <Terminal className="h-3.5 w-3.5 shrink-0 text-text-quaternary" />
                <span className="font-mono text-caption text-text-primary">{item.label}</span>
                <span className="truncate text-tiny text-text-tertiary">{item.detail}</span>
                {item.badge && (
                  <Badge variant="subtle" size="xs" className="ml-auto shrink-0">
                    {item.badge}
                  </Badge>
                )}
              </button>
            ))
          ) : (
            <>
              <p className="flex items-center gap-1.5 px-3 py-1.5 text-tiny uppercase tracking-wide text-text-quaternary">
                <History className="h-3 w-3" /> Gần đây
              </p>
              {history.slice(0, 8).map((item, index) => (
                <button
                  key={`${item}-${index}`}
                  type="button"
                  onMouseDown={(event) => {
                    event.preventDefault();
                    setValue(item);
                    inputRef.current?.focus();
                  }}
                  className="block w-full px-3 py-1.5 text-left font-mono text-caption text-text-secondary hover:bg-surface-2"
                >
                  {item}
                </button>
              ))}
            </>
          )}
        </div>
      )}

      <div className="flex h-10 items-center gap-2 px-3">
        <Terminal className="h-3.5 w-3.5 shrink-0 text-text-quaternary" />
        <input
          ref={inputRef}
          value={value}
          onChange={(event) => { setValue(event.target.value); setOpen(true); }}
          onFocus={() => setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 120)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') { event.preventDefault(); run(); }
            if (event.key === 'Escape') setOpen(false);
            if (event.key === 'ArrowUp' && !value && history[0]) setValue(history[0]);
          }}
          placeholder="dbt build --select +fct_orders"
          disabled={disabled}
          spellCheck={false}
          className={cn(
            'h-7 min-w-0 flex-1 bg-transparent font-mono text-caption',
            'text-text-primary placeholder:text-text-quaternary focus:outline-none',
          )}
        />

        {environmentName && (
          <Badge
            variant={environmentProtected ? 'warning' : 'subtle'}
            size="xs"
            className="shrink-0"
            title={environmentProtected
              ? 'Môi trường production — lệnh ghi cần quyền vận hành'
              : undefined}
          >
            {environmentName}
          </Badge>
        )}

        {parsed && !running && (
          <span className="hidden shrink-0 text-tiny text-text-quaternary md:inline">
            {parsed.command}
            {parsed.selector ? ` · ${parsed.selector}` : ''}
          </span>
        )}

        {running ? (
          <Button variant="secondary" size="xs" onClick={onCancel}>
            Dừng
          </Button>
        ) : (
          <Button
            variant="primary" size="xs"
            onClick={run}
            disabled={!parsed || disabled}
            leadingIcon={<Play className="h-3 w-3" />}
          >
            Chạy
          </Button>
        )}
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          className="shrink-0 rounded-sm p-1 text-text-tertiary hover:bg-surface-2"
          aria-label="Lịch sử lệnh"
        >
          <ChevronUp className={cn('h-3.5 w-3.5 transition-transform', open && 'rotate-180')} />
        </button>
      </div>
    </div>
  );
}
