'use client';

import * as React from 'react';
import { EditorState, RangeSet, type Extension } from '@codemirror/state';
import {
  Decoration, EditorView, keymap, lineNumbers, highlightActiveLine,
  highlightActiveLineGutter, highlightSpecialChars, drawSelection,
  rectangularSelection, crosshairCursor, type DecorationSet, type ViewUpdate,
} from '@codemirror/view';
import {
  defaultKeymap, history, historyKeymap, indentWithTab, toggleComment,
} from '@codemirror/commands';
import { searchKeymap, highlightSelectionMatches, openSearchPanel } from '@codemirror/search';
import { sql, StandardSQL } from '@codemirror/lang-sql';
import {
  autocompletion, completionKeymap, closeBrackets, closeBracketsKeymap,
  type CompletionContext, type CompletionResult,
} from '@codemirror/autocomplete';
import {
  bracketMatching, foldGutter, indentOnInput, syntaxHighlighting, HighlightStyle,
} from '@codemirror/language';
import { tags } from '@lezer/highlight';

/**
 * The model editing surface.
 *
 * A plain textarea cannot show a mistyped Jinja tag, cannot find-and-replace a
 * column across a model, and loses its undo history whenever the value is set
 * programmatically -- all of which happen constantly while authoring dbt SQL.
 */
export interface SqlEditorHandle {
  jumpToLine: (line: number) => void;
  insert: (text: string) => void;
  focus: () => void;
}

const palette = HighlightStyle.define([
  { tag: tags.keyword, color: '#6D28D9', fontWeight: '600' },
  { tag: [tags.string, tags.special(tags.string)], color: '#0F766E' },
  { tag: tags.comment, color: '#64748B', fontStyle: 'italic' },
  { tag: [tags.number, tags.bool, tags.null], color: '#A16207' },
  { tag: [tags.function(tags.variableName), tags.standard(tags.variableName)], color: '#1D4ED8' },
  { tag: tags.operator, color: '#475569' },
  { tag: tags.typeName, color: '#0369A1' },
  { tag: tags.propertyName, color: '#1F2937' },
]);


const jinjaMark = Decoration.mark({ class: 'cm-jinja' });

/** dbt's own vocabulary lives in Jinja tags; SQL highlighting alone leaves
 *  `{{ ref('x') }}` looking like ordinary text. */
const jinjaHighlight = EditorView.decorations.compute(['doc'], (state): DecorationSet => {
  const text = state.doc.toString();
  const pattern = /\{\{[^}]*\}\}|\{%[^%]*%\}/g;
  const ranges = [];
  let match = pattern.exec(text);
  while (match) {
    ranges.push(jinjaMark.range(match.index, match.index + match[0].length));
    match = pattern.exec(text);
  }
  return RangeSet.of(ranges, true);
});

const baseTheme = EditorView.theme({
  '&': { height: '100%', fontSize: '13px', color: 'rgb(var(--text-primary))' },
  '.cm-scroller': {
    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
    lineHeight: '1.55',
  },
  '.cm-content': { padding: '10px 0' },
  '.cm-gutters': {
    backgroundColor: 'transparent',
    border: 'none',
    color: 'rgb(var(--text-quaternary))',
  },
  '.cm-activeLineGutter': { backgroundColor: 'transparent' },
  '.cm-activeLine': { backgroundColor: 'rgb(var(--surface-2) / 0.55)' },
  '.cm-jinja': {
    backgroundColor: 'rgb(var(--brand) / 0.12)',
    borderRadius: '3px',
    color: 'rgb(var(--brand))',
  },
  '&.cm-focused': { outline: 'none' },
  '.cm-panels': {
    backgroundColor: 'rgb(var(--surface-2))',
    color: 'rgb(var(--text-primary))',
    borderTop: '1px solid rgb(var(--border-line))',
  },
  '.cm-panel input, .cm-panel button': {
    backgroundColor: 'rgb(var(--surface-1))',
    color: 'rgb(var(--text-primary))',
    border: '1px solid rgb(var(--border-strong))',
    borderRadius: '4px',
    padding: '2px 6px',
  },
  '.cm-tooltip-autocomplete': {
    border: '1px solid rgb(var(--border-line))',
    backgroundColor: 'rgb(var(--surface-1))',
    borderRadius: '6px',
    overflow: 'hidden',
  },
});

export const SqlEditor = React.forwardRef<SqlEditorHandle, {
  value: string;
  onChange: (value: string) => void;
  onSave?: () => void;
  onRun?: () => void;
  readOnly?: boolean;
  /** Names offered while typing inside `ref(...)` and `source(...)`. */
  completions?: { refs: string[]; sources: string[] };
  /** Column names of this model's inputs. */
  columns?: string[];
}>(function SqlEditor(
  { value, onChange, onSave, onRun, readOnly, completions, columns }, ref,
) {
  const host = React.useRef<HTMLDivElement | null>(null);
  const view = React.useRef<EditorView | null>(null);
  const latest = React.useRef({ onChange, onSave, onRun });
  latest.current = { onChange, onSave, onRun };
  const meta = React.useRef({ completions, columns });
  meta.current = { completions, columns };

  React.useEffect(() => {
    if (!host.current) return undefined;

    const dbtCompletions = (context: CompletionContext): CompletionResult | null => {
      const sets = meta.current.completions;
      const cols = meta.current.columns;
      const inRef = context.matchBefore(/ref\(\s*['"][\w-]*/);
      if (inRef) {
        const typed = inRef.text.replace(/.*['"]/, '');
        return {
          from: inRef.to - typed.length,
          options: (sets?.refs ?? []).map((name) => ({
            label: name, type: 'class', detail: 'model',
          })),
          validFor: /^[\w-]*$/,
        };
      }
      const inSource = context.matchBefore(/source\(\s*['"][\w-]*/);
      if (inSource) {
        const typed = inSource.text.replace(/.*['"]/, '');
        return {
          from: inSource.to - typed.length,
          options: (sets?.sources ?? []).map((name) => ({
            label: name, type: 'namespace', detail: 'source',
          })),
          validFor: /^[\w-]*$/,
        };
      }
      const word = context.matchBefore(/[\w.]+/);
      if (!word || (word.from === word.to && !context.explicit)) return null;
      const options = [
        ...(cols ?? []).map((name) => ({ label: name, type: 'property', detail: 'column' })),
        ...(sets?.refs ?? []).map((name) => ({ label: name, type: 'class', detail: 'model' })),
      ];
      if (!options.length) return null;
      return { from: word.from, options, validFor: /^[\w.]*$/ };
    };

    const extensions: Extension[] = [
      lineNumbers(),
      highlightActiveLineGutter(),
      highlightSpecialChars(),
      history(),
      foldGutter(),
      drawSelection(),
      EditorState.allowMultipleSelections.of(true),
      indentOnInput(),
      syntaxHighlighting(palette, { fallback: true }),
      bracketMatching(),
      closeBrackets(),
      autocompletion({ override: [dbtCompletions], activateOnTyping: true }),
      rectangularSelection(),
      crosshairCursor(),
      highlightActiveLine(),
      highlightSelectionMatches(),
      sql({ dialect: StandardSQL, upperCaseKeywords: false }),
      jinjaHighlight,
      baseTheme,
      EditorView.lineWrapping,
      keymap.of([
        { key: 'Mod-s', preventDefault: true, run: () => { latest.current.onSave?.(); return true; } },
        { key: 'Mod-Enter', preventDefault: true, run: () => { latest.current.onRun?.(); return true; } },
        { key: 'Mod-/', run: toggleComment },
        { key: 'Mod-f', run: openSearchPanel },
        ...closeBracketsKeymap,
        ...defaultKeymap,
        ...searchKeymap,
        ...historyKeymap,
        ...completionKeymap,
        indentWithTab,
      ]),
      EditorView.updateListener.of((update: ViewUpdate) => {
        if (update.docChanged) latest.current.onChange(update.state.doc.toString());
      }),
      EditorState.readOnly.of(Boolean(readOnly)),
    ];

    const instance = new EditorView({
      state: EditorState.create({ doc: value, extensions }),
      parent: host.current,
    });
    view.current = instance;
    return () => { instance.destroy(); view.current = null; };
    // The document is synchronised by the effect below; rebuilding the view on
    // every keystroke would discard undo history and the cursor position.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [readOnly]);

  // Adopt an externally changed document -- switching models, resolving a save
  // conflict -- without disturbing what the user is typing.
  React.useEffect(() => {
    const instance = view.current;
    if (!instance) return;
    const current = instance.state.doc.toString();
    if (current === value) return;
    instance.dispatch({ changes: { from: 0, to: current.length, insert: value } });
  }, [value]);

  React.useImperativeHandle(ref, () => ({
    jumpToLine: (line: number) => {
      const instance = view.current;
      if (!instance) return;
      const total = instance.state.doc.lines;
      const target = instance.state.doc.line(Math.min(Math.max(line, 1), total));
      instance.dispatch({
        selection: { anchor: target.from, head: target.to },
        effects: EditorView.scrollIntoView(target.from, { y: 'center' }),
      });
      instance.focus();
    },
    insert: (text: string) => {
      const instance = view.current;
      if (!instance) return;
      const at = instance.state.selection.main;
      instance.dispatch({
        changes: { from: at.from, to: at.to, insert: text },
        selection: { anchor: at.from + text.length },
      });
      instance.focus();
    },
    focus: () => view.current?.focus(),
  }), []);

  return <div ref={host} className="h-full overflow-hidden" />;
});
