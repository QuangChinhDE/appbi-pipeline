'use client';

/**
 * The editor, for every kind of file a dbt project contains.
 *
 * V1 had a SQL editor because a Transform only ever held SQL. A dbt project is
 * SQL, YAML, Markdown, CSV, JSON and sometimes Python, and an editor that can
 * only open one of them cannot open the project -- so language selection is by
 * file extension here rather than assumed.
 *
 * dbt-aware completion is the other half. `ref`, `source`, `macro` and the model
 * names they take come from the last parse, not from a regex over open files, so
 * a model contributed by a package is offered too.
 */

import * as React from 'react';
import {
  autocompletion, closeBrackets, closeBracketsKeymap,
  completionKeymap, type CompletionContext, type CompletionResult,
} from '@codemirror/autocomplete';
import {
  defaultKeymap, history, historyKeymap, indentWithTab,
} from '@codemirror/commands';
import { sql, PostgreSQL } from '@codemirror/lang-sql';
import {
  HighlightStyle, StreamLanguage, bracketMatching, foldGutter, foldKeymap,
  indentOnInput, syntaxHighlighting,
} from '@codemirror/language';
import { highlightSelectionMatches, search, searchKeymap } from '@codemirror/search';
import { Compartment, EditorState, type Extension } from '@codemirror/state';
import {
  EditorView, ViewUpdate, drawSelection, dropCursor, highlightActiveLine,
  highlightActiveLineGutter, keymap, lineNumbers, rectangularSelection,
} from '@codemirror/view';
import { tags } from '@lezer/highlight';

import type { Completions } from '@/lib/types';
import { cn } from '@/lib/utils';

export type EditorLanguage = 'sql' | 'yaml' | 'markdown' | 'json' | 'csv' | 'python' | 'text';

export function languageForPath(path: string): EditorLanguage {
  const lower = path.toLowerCase();
  if (lower.endsWith('.sql')) return 'sql';
  if (lower.endsWith('.yml') || lower.endsWith('.yaml')) return 'yaml';
  if (lower.endsWith('.md')) return 'markdown';
  if (lower.endsWith('.json')) return 'json';
  if (lower.endsWith('.csv') || lower.endsWith('.tsv')) return 'csv';
  if (lower.endsWith('.py')) return 'python';
  return 'text';
}

/**
 * A small YAML mode.
 *
 * `@codemirror/lang-yaml` is not among this project's dependencies and adding a
 * package to colour one file type is a poor trade. YAML's surface syntax is
 * simple enough to tokenise line by line: keys, values, comments, anchors and
 * list markers cover everything in a dbt schema file.
 */
const yamlMode = StreamLanguage.define<{ inValue: boolean }>({
  name: 'yaml',
  startState: () => ({ inValue: false }),
  token(stream, state) {
    if (stream.sol()) state.inValue = false;
    if (stream.eatSpace()) return null;
    if (stream.peek() === '#') { stream.skipToEnd(); return 'comment'; }
    if (!state.inValue) {
      if (stream.match(/^-\s+/)) return 'punctuation';
      if (stream.match(/^[\w.$-]+(?=\s*:)/)) return 'propertyName';
      if (stream.match(/^:\s*/)) { state.inValue = true; return 'punctuation'; }
    }
    if (stream.match(/^&[\w-]+|^\*[\w-]+/)) return 'labelName';
    if (stream.match(/^(true|false|null|yes|no|on|off)\b/i)) return 'bool';
    if (stream.match(/^-?\d+(\.\d+)?\b/)) return 'number';
    if (stream.match(/^"(?:[^"\\]|\\.)*"|^'(?:[^'\\]|\\.)*'/)) return 'string';
    if (stream.match(/^\{\{[^}]*\}\}/)) return 'macroName';
    if (stream.match(/^[|>][-+]?/)) return 'operator';
    stream.next();
    return null;
  },
  languageData: { commentTokens: { line: '#' } },
});

/** Markdown, CSV and plain text share a deliberately quiet mode. */
const plainMode = StreamLanguage.define({
  name: 'text',
  token(stream) {
    if (stream.match(/^#+\s.*/)) return 'heading';
    if (stream.match(/^`[^`]*`/)) return 'monospace';
    stream.next();
    return null;
  },
});

const highlight = HighlightStyle.define([
  { tag: tags.keyword, color: 'rgb(var(--brand))', fontWeight: '500' },
  { tag: tags.string, color: 'rgb(var(--success))' },
  { tag: tags.number, color: 'rgb(var(--info))' },
  { tag: tags.bool, color: 'rgb(var(--info))' },
  { tag: tags.comment, color: 'rgb(var(--text-quaternary))', fontStyle: 'italic' },
  { tag: tags.propertyName, color: 'rgb(var(--brand))' },
  { tag: tags.labelName, color: 'rgb(var(--warning))' },
  { tag: tags.operator, color: 'rgb(var(--text-tertiary))' },
  { tag: tags.punctuation, color: 'rgb(var(--text-tertiary))' },
  { tag: tags.heading, color: 'rgb(var(--text-primary))', fontWeight: '600' },
  { tag: tags.macroName, color: 'rgb(var(--warning))' },
  { tag: tags.function(tags.variableName), color: 'rgb(var(--warning))' },
]);

const theme = EditorView.theme({
  '&': { height: '100%', fontSize: '13px', backgroundColor: 'transparent' },
  '.cm-scroller': {
    fontFamily: 'var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace)',
    lineHeight: '1.6',
  },
  '.cm-content': { padding: '10px 0', caretColor: 'rgb(var(--brand))' },
  '.cm-gutters': {
    backgroundColor: 'transparent',
    borderRight: '1px solid rgb(var(--border-line))',
    color: 'rgb(var(--text-quaternary))',
    fontSize: '11px',
  },
  '.cm-activeLine': { backgroundColor: 'rgb(var(--surface-2) / 0.5)' },
  '.cm-activeLineGutter': { backgroundColor: 'transparent', color: 'rgb(var(--text-tertiary))' },
  '.cm-selectionBackground, ::selection': { backgroundColor: 'rgb(var(--brand) / 0.2)' },
  '&.cm-focused .cm-selectionBackground': { backgroundColor: 'rgb(var(--brand) / 0.25)' },
  '.cm-matchingBracket': {
    backgroundColor: 'rgb(var(--brand) / 0.2)', outline: '1px solid rgb(var(--brand) / 0.4)',
  },
  '.cm-tooltip': {
    backgroundColor: 'rgb(var(--surface-1))',
    border: '1px solid rgb(var(--border-line))',
    borderRadius: '6px',
    boxShadow: '0 8px 24px rgb(0 0 0 / 0.12)',
  },
  '.cm-tooltip-autocomplete ul li[aria-selected]': {
    backgroundColor: 'rgb(var(--brand) / 0.12)', color: 'rgb(var(--text-primary))',
  },
  '.cm-searchMatch': { backgroundColor: 'rgb(var(--warning) / 0.25)' },
  '.cm-searchMatch-selected': { backgroundColor: 'rgb(var(--warning) / 0.45)' },
  // Where an error was reported. Drives click-a-problem-jump-to-the-line.
  '.cm-errorLine': {
    backgroundColor: 'rgb(var(--danger) / 0.1)',
    boxShadow: 'inset 3px 0 0 rgb(var(--danger))',
  },
}, { dark: false });

/** Jinja constructs, offered in every SQL file. */
const JINJA_SNIPPETS: { label: string; detail: string; apply: string }[] = [
  { label: 'ref', detail: 'Reference another model', apply: "{{ ref('') }}" },
  { label: 'source', detail: 'Reference a source table', apply: "{{ source('', '') }}" },
  { label: 'config', detail: 'Configure this model', apply: "{{ config(materialized='table') }}" },
  { label: 'var', detail: 'Project variable', apply: "{{ var('') }}" },
  { label: 'this', detail: 'This model as a relation', apply: '{{ this }}' },
  { label: 'target', detail: 'The current target', apply: '{{ target.name }}' },
  {
    label: 'is_incremental',
    detail: 'True on an incremental build',
    apply: '{% if is_incremental() %}\n  \n{% endif %}',
  },
  { label: 'if', detail: 'Jinja conditional', apply: '{% if %}\n\n{% endif %}' },
  { label: 'for', detail: 'Jinja loop', apply: '{% for item in items %}\n\n{% endfor %}' },
  { label: 'set', detail: 'Assign a Jinja variable', apply: "{% set name = '' %}" },
];

/** YAML keys a dbt schema file accepts, offered at the start of a line. */
const YAML_KEYS = [
  'version', 'models', 'sources', 'seeds', 'snapshots', 'exposures', 'metrics',
  'groups', 'macros', 'analyses', 'unit_tests', 'saved_queries', 'semantic_models',
  'name', 'description', 'columns', 'config', 'tests', 'data_tests', 'meta',
  'tags', 'docs', 'identifier', 'schema', 'database', 'loaded_at_field',
  'freshness', 'warn_after', 'error_after', 'count', 'period', 'tables',
  'materialized', 'unique_key', 'incremental_strategy', 'on_schema_change',
  'partition_by', 'cluster_by', 'enabled', 'alias', 'group', 'access',
  'contract', 'constraints', 'data_type', 'quote', 'severity', 'where',
  'owner', 'depends_on', 'arguments', 'persist_docs',
];

function dbtCompletions(
  language: EditorLanguage,
  completions: Completions | undefined,
) {
  return (context: CompletionContext): CompletionResult | null => {
    if (!completions) return null;

    if (language === 'sql') {
      // Inside `ref('…')` -- offer models, seeds and snapshots.
      const inRef = context.matchBefore(/ref\(\s*['"][\w.]*/);
      if (inRef) {
        const quote = inRef.text.includes('"') ? '"' : "'";
        return {
          from: inRef.from + inRef.text.lastIndexOf(quote) + 1,
          options: completions.refs.map((item) => ({
            label: item.label, type: 'variable', detail: item.detail ?? undefined,
          })),
        };
      }
      // Inside `source('…')` -- offer the two-part name.
      const inSource = context.matchBefore(/source\(\s*['"][\w.]*/);
      if (inSource) {
        const quote = inSource.text.includes('"') ? '"' : "'";
        return {
          from: inSource.from + inSource.text.lastIndexOf(quote) + 1,
          options: completions.sources.map((item) => ({
            label: item.label.split('.')[0],
            type: 'variable',
            detail: item.detail ?? item.label,
            apply: item.label.replace('.', `${quote}, ${quote}`),
          })),
        };
      }
      // Inside `{{ … }}` or `{% … %}` -- Jinja and macros.
      const inJinja = context.matchBefore(/\{[{%]\s*[\w.]*/);
      if (inJinja) {
        const word = context.matchBefore(/[\w.]*/);
        return {
          from: word?.from ?? context.pos,
          options: [
            ...JINJA_SNIPPETS.map((item) => ({
              label: item.label, type: 'keyword', detail: item.detail,
            })),
            ...completions.macros.map((item) => ({
              label: item.label, type: 'function',
              detail: item.detail ? `macro · ${item.detail}` : 'macro',
            })),
          ],
        };
      }
      // Bare word: offer column names from every model, plus the Jinja openers.
      const word = context.matchBefore(/[\w]{2,}/);
      if (!word || word.from === word.to) return null;
      const columns = new Set<string>();
      Object.values(completions.columns).forEach((names) =>
        names.forEach((name) => columns.add(name)));
      return {
        from: word.from,
        options: [
          ...Array.from(columns).map((name) => ({
            label: name, type: 'property', detail: 'column',
          })),
          ...completions.refs.map((item) => ({
            label: item.label, type: 'variable', detail: 'model',
            apply: `{{ ref('${item.label}') }}`,
          })),
        ],
      };
    }

    if (language === 'yaml') {
      // After `- ` in a test list -- offer every test in the manifest, not the
      // four built-ins: a package's tests are as available as dbt's own.
      const inTest = context.matchBefore(/(data_)?tests:\s*\n(\s*-\s*[\w.]*)/);
      const afterDash = context.matchBefore(/-\s+[\w.]*/);
      if (inTest || afterDash) {
        const word = context.matchBefore(/[\w.]*/);
        return {
          from: word?.from ?? context.pos,
          options: completions.tests.map((item) => ({
            label: item.label, type: 'function', detail: item.detail ?? 'test',
          })),
        };
      }
      const word = context.matchBefore(/^\s*[\w_]*/);
      if (!word || word.from === word.to) return null;
      return {
        from: word.from + (word.text.length - word.text.trimStart().length),
        options: YAML_KEYS.map((key) => ({
          label: key, type: 'property', apply: `${key}: `,
        })),
      };
    }

    return null;
  };
}

function languageExtension(language: EditorLanguage): Extension {
  switch (language) {
    case 'sql':
      return sql({ dialect: PostgreSQL, upperCaseKeywords: false });
    case 'yaml':
      return yamlMode;
    default:
      return plainMode;
  }
}

interface DbtFileEditorProps {
  value: string;
  onChange: (value: string) => void;
  path: string;
  completions?: Completions;
  readOnly?: boolean;
  /** 1-based line to highlight and scroll to; from a Problems row. */
  errorLine?: number | null;
  onSave?: () => void;
  onSaveAll?: () => void;
  onPreview?: () => void;
  className?: string;
}

export function DbtFileEditor({
  value,
  onChange,
  path,
  completions,
  readOnly = false,
  errorLine,
  onSave,
  onSaveAll,
  onPreview,
  className,
}: DbtFileEditorProps) {
  const host = React.useRef<HTMLDivElement>(null);
  const view = React.useRef<EditorView | null>(null);
  const language = React.useMemo(() => languageForPath(path), [path]);

  // Compartments, so language and completion sources can be reconfigured when
  // the open tab changes without tearing down the editor -- which would lose
  // undo history and scroll position on every tab switch.
  const languageSlot = React.useRef(new Compartment());
  const completionSlot = React.useRef(new Compartment());
  const readOnlySlot = React.useRef(new Compartment());

  // Handlers in a ref: the keymap is built once, and rebuilding it on every
  // render would drop keystrokes mid-edit.
  const handlers = React.useRef({ onSave, onSaveAll, onPreview, onChange });
  handlers.current = { onSave, onSaveAll, onPreview, onChange };

  React.useEffect(() => {
    if (!host.current) return;

    const state = EditorState.create({
      doc: value,
      extensions: [
        lineNumbers(),
        highlightActiveLineGutter(),
        highlightActiveLine(),
        history(),
        foldGutter(),
        drawSelection(),
        dropCursor(),
        rectangularSelection(),
        indentOnInput(),
        bracketMatching(),
        closeBrackets(),
        search({ top: true }),
        highlightSelectionMatches(),
        syntaxHighlighting(highlight),
        theme,
        EditorState.allowMultipleSelections.of(true),
        languageSlot.current.of(languageExtension(language)),
        completionSlot.current.of(
          autocompletion({
            override: [dbtCompletions(language, completions)],
            activateOnTyping: true,
            closeOnBlur: true,
          }),
        ),
        readOnlySlot.current.of(EditorState.readOnly.of(readOnly)),
        keymap.of([
          {
            key: 'Mod-s',
            preventDefault: true,
            run: () => { handlers.current.onSave?.(); return true; },
          },
          {
            key: 'Mod-Shift-s',
            preventDefault: true,
            run: () => { handlers.current.onSaveAll?.(); return true; },
          },
          {
            key: 'Mod-Enter',
            preventDefault: true,
            run: () => { handlers.current.onPreview?.(); return true; },
          },
          ...closeBracketsKeymap,
          ...defaultKeymap,
          ...searchKeymap,
          ...historyKeymap,
          ...foldKeymap,
          ...completionKeymap,
          indentWithTab,
        ]),
        EditorView.lineWrapping,
        EditorView.updateListener.of((update: ViewUpdate) => {
          if (update.docChanged) {
            handlers.current.onChange(update.state.doc.toString());
          }
        }),
      ],
    });

    view.current = new EditorView({ state, parent: host.current });
    return () => { view.current?.destroy(); view.current = null; };
    // Built once per mounted editor. Everything that varies is reconfigured
    // through a compartment below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Language and completions follow the open file.
  React.useEffect(() => {
    view.current?.dispatch({
      effects: [
        languageSlot.current.reconfigure(languageExtension(language)),
        completionSlot.current.reconfigure(
          autocompletion({
            override: [dbtCompletions(language, completions)],
            activateOnTyping: true,
            closeOnBlur: true,
          }),
        ),
      ],
    });
  }, [language, completions]);

  React.useEffect(() => {
    view.current?.dispatch({
      effects: readOnlySlot.current.reconfigure(EditorState.readOnly.of(readOnly)),
    });
  }, [readOnly]);

  // External value changes -- a tab switch, a Git pull, a release restore.
  // Compared before dispatching so a keystroke does not cause a self-inflicted
  // document replacement and lose the cursor.
  React.useEffect(() => {
    const editor = view.current;
    if (!editor) return;
    const current = editor.state.doc.toString();
    if (current === value) return;
    editor.dispatch({
      changes: { from: 0, to: current.length, insert: value },
    });
  }, [value]);

  // Jump to a reported error line.
  React.useEffect(() => {
    const editor = view.current;
    if (!editor || !errorLine) return;
    const total = editor.state.doc.lines;
    const line = editor.state.doc.line(Math.min(Math.max(errorLine, 1), total));
    editor.dispatch({
      selection: { anchor: line.from },
      effects: EditorView.scrollIntoView(line.from, { y: 'center' }),
    });
    editor.focus();
  }, [errorLine]);

  return (
    <div
      ref={host}
      className={cn('h-full w-full overflow-hidden bg-surface-0', className)}
      data-language={language}
    />
  );
}
