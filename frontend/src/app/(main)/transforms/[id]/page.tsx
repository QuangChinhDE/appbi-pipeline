'use client';

import * as React from 'react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle, Braces, Check, ChevronDown, ChevronRight, Clock, Code2, Download, Eye, GitFork,
  Loader2, MoreHorizontal, PanelRightClose, PanelRightOpen, Play, Plus, Save, Settings, Sparkles,
  ShieldCheck, Table2, TestTube2,
  Trash2, Workflow,
  Pencil, Wrench, X,
} from 'lucide-react';

import { ApiError, transformApi } from '@/lib/api';
import type { TransformModel } from '@/lib/types';
import { qk } from '@/lib/queryKeys';
import { cn } from '@/lib/utils';
import { useWorkspaceId } from '@/hooks/use-current-user';
import { usePermissions } from '@/hooks/use-permissions';
import { toastError, toastSuccess } from '@/hooks/use-toast';
import { useI18n } from '@/providers/LanguageProvider';
import { DetailHeader } from '@/components/layout/PageLayout';
import { Badge, type BadgeVariant, Kbd } from '@/components/ui/Badge';
import { Button, IconButton } from '@/components/ui/Button';
import { EmptyState, ErrorState, Spinner } from '@/components/ui/Feedback';
import { Checkbox, Input, Label, Select, Textarea } from '@/components/ui/Input';
import { Menu } from '@/components/ui/Menu';
import { ConfirmDialog, Modal } from '@/components/ui/Modal';
import { Tabs } from '@/components/ui/Tabs';
import { LogViewer } from '@/components/integrations/LogViewer';
import { SqlEditor, type SqlEditorHandle } from '@/components/transforms/SqlEditor';
import { Pane, usePaneState } from '@/components/transforms/Collapsible';
import { LineageView } from '@/components/transforms/LineageView';
import { Resizer, usePaneSize } from '@/components/transforms/Resizer';
import { PublishBar, ReleaseHistoryModal } from '@/components/transforms/PublishBar';
import { AiDraftDialog } from '@/components/transforms/AiDraftDialog';
import { GitSyncPanel } from '@/components/transforms/GitSyncPanel';

const ACTIVE = ['QUEUED', 'STARTING', 'RUNNING', 'CANCEL_REQUESTED'];
const healthTone: Record<string, BadgeVariant> = {
  HEALTHY: 'success', WARNING: 'warning', ERROR: 'danger', UNKNOWN: 'neutral',
};

type Draft = TransformModel;
type Operation = import('@/lib/types').TransformOperation;
type TestForm = {
  column_name: string;
  rule: string;
  severity: string;
  values: string;
  target_model: string;
  target_field: string;
};

const EMPTY_TEST_FORM: TestForm = {
  column_name: '', rule: 'NOT_NULL', severity: 'ERROR', values: '',
  target_model: '', target_field: '',
};

/**
 * Starting points for a new model.
 *
 * A blank editor is the hardest place to begin: the author has to remember
 * dbt's Jinja spelling, the layer conventions, and the incremental config all
 * at once. These are the four shapes almost every project actually contains,
 * and they double as a worked example of the syntax.
 */
type ModelTemplate = {
  id: string;
  layer: 'STAGING' | 'CORE' | 'MART';
  materialization: 'VIEW' | 'TABLE' | 'INCREMENTAL';
  sql: (context: { source?: string; table?: string; upstream?: string }) => string;
};

const MODEL_TEMPLATES: ModelTemplate[] = [
  {
    id: 'blank', layer: 'STAGING', materialization: 'VIEW',
    sql: () => 'select\n    *\nfrom\n',
  },
  {
    id: 'staging', layer: 'STAGING', materialization: 'VIEW',
    sql: ({ source = 'src_raw', table = 'my_table' }) => [
      '-- Clean one raw table: rename columns, fix types, drop what you do not need.',
      'select',
      '    id                as ' + table + '_id,',
      '    name              as ' + table + '_name,',
      '    created_at',
      "from {{ source('" + source + "', '" + table + "') }}",
      'where id is not null',
      '',
    ].join('\n'),
  },
  {
    id: 'join', layer: 'CORE', materialization: 'TABLE',
    sql: ({ upstream = 'stg_model' }) => [
      '-- Combine two upstream models into one wider table.',
      'select',
      '    a.*,',
      '    b.name as related_name',
      "from {{ ref('" + upstream + "') }} a",
      "left join {{ ref('" + upstream + "') }} b on b.id = a.related_id",
      '',
    ].join('\n'),
  },
  {
    id: 'aggregate', layer: 'MART', materialization: 'TABLE',
    sql: ({ upstream = 'stg_model' }) => [
      '-- One row per day: the shape a dashboard usually wants.',
      'select',
      '    date(created_at) as day,',
      '    count(*)         as row_count',
      "from {{ ref('" + upstream + "') }}",
      'group by 1',
      'order by 1',
      '',
    ].join('\n'),
  },
  {
    id: 'incremental', layer: 'MART', materialization: 'INCREMENTAL',
    sql: ({ upstream = 'stg_model' }) => [
      '-- Only processes new rows after the first run. Set a Unique key in Config.',
      'select',
      '    *',
      "from {{ ref('" + upstream + "') }}",
      '{% if is_incremental() %}',
      '  where updated_at > (select max(updated_at) from {{ this }})',
      '{% endif %}',
      '',
    ].join('\n'),
  },
];

/** Captures the three parts of a `ref('name')` call so one can be rewritten. */
const REF_PATTERN = /(ref\(\s*['"])([^'"]+)(['"]\s*\))/g;

/** Model names referenced by `{{ ref('x') }}` in a piece of model SQL. */
function refsOf(sql: string): Set<string> {
  const found = new Set<string>();
  const pattern = /ref\(\s*['"]([^'"]+)['"]\s*\)/g;
  let match = pattern.exec(sql);
  while (match) { found.add(match[1]); match = pattern.exec(sql); }
  return found;
}

export default function TransformWorkbenchPage() {
  const params = useParams<{ id: string }>();
  const transformId = params.id;
  const workspaceId = useWorkspaceId();
  const queryClient = useQueryClient();
  const { locale } = useI18n();
  const { can } = usePermissions();
  const canEdit = can('transforms', 'edit');
  const canRun = can('transforms', 'operate');
  const copy = locale === 'vi' ? vi : en;

  const query = useQuery({
    queryKey: qk.transform(workspaceId, transformId),
    queryFn: () => transformApi.detail(transformId),
  });
  const [selectedId, setSelectedId] = React.useState<string | null>(null);
  const [draft, setDraft] = React.useState<Draft | null>(null);
  const [dirty, setDirty] = React.useState(false);
  const [pendingModelId, setPendingModelId] = React.useState<string | null>(null);
  const [newModelOpen, setNewModelOpen] = React.useState(false);
  const [aiOpen, setAiOpen] = React.useState(false);
  const [aiDraft, setAiDraft] = React.useState<import('@/lib/types').DraftedModel | null>(null);
  const [aiError, setAiError] = React.useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = React.useState(false);
  const [generatedOpen, setGeneratedOpen] = React.useState(false);
  const [rightTab, setRightTab] = React.useState('config');
  const [bottomTab, setBottomTab] = React.useState('preview');
  const [activeRunId, setActiveRunId] = React.useState<string | null>(null);
  const [newModel, setNewModel] = React.useState({ name: '', layer: 'STAGING', materialization: 'VIEW' });
  const [newTemplate, setNewTemplate] = React.useState('staging');
  const [testForm, setTestForm] = React.useState<TestForm>(EMPTY_TEST_FORM);
  const editorRef = React.useRef<SqlEditorHandle | null>(null);
  const [conflict, setConflict] = React.useState<{ server: TransformModel } | null>(null);
  const [modelFilter, setModelFilter] = React.useState('');
  const [railTab, setRailTab] = React.useState('models');
  const [renaming, setRenaming] = React.useState(false);
  const [renameValue, setRenameValue] = React.useState('');
  const [pendingDelete, setPendingDelete] = React.useState<TransformModel | null>(null);

  React.useEffect(() => {
    if (!query.data || selectedId) return;
    const first = query.data.models[0];
    if (first) {
      setSelectedId(first.id);
      setDraft(structuredClone(first));
    }
  }, [query.data, selectedId]);

  React.useEffect(() => {
    if (!dirty) return undefined;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);

  // Compiling is the verb non-technical users understand least, and the answer
  // it gives -- "is this SQL valid?" -- is one they want continuously rather
  // than on request. Run it quietly after a save and report the verdict beside
  // the model name, the way Dataform keeps a live compiled pane.
  const [syntax, setSyntax] = React.useState<'UNKNOWN' | 'CHECKING' | 'OK' | 'ERROR'>('UNKNOWN');
  const syntaxRun = React.useRef<string | null>(null);

  // The hardest moment for a newcomer is the empty editor: they have a table in
  // the rail and no idea what to type. This turns that into one click -- create
  // a model that selects from it, and show the rows.
  const exploreInput = useMutation({
    mutationFn: async (asset: import('@/lib/types').DataAsset) => {
      const base = asset.relation_name.replace(/[^A-Za-z0-9_]/g, '_').toLowerCase();
      const taken = new Set((query.data?.models ?? []).map((item) => item.name));
      let name = `stg_${base}`;
      for (let index = 2; taken.has(name); index += 1) name = `stg_${base}_${index}`;
      const created = await transformApi.createModel(transformId, {
        name, layer: 'STAGING', materialization: 'VIEW',
      });
      const sql = [
        `-- ${copy.exploreComment}`,
        'select',
        '    *',
        // No LIMIT here: preview wraps the query in its own row cap, and two
        // limits in one statement is a syntax error.
        `from {{ source('${asset.source_name}', '${asset.relation_name}') }}`,
        '',
      ].join(String.fromCharCode(10));
      return transformApi.updateModel(transformId, created.id, {
        sql, version: created.version,
      });
    },
    onSuccess: async (created) => {
      await queryClient.invalidateQueries({ queryKey: qk.transform(workspaceId, transformId) });
      setSelectedId(created.id);
      setDraft(structuredClone(created));
      setDirty(false);
      run.mutate({ operation: 'PREVIEW', modelId: created.id });
    },
    onError: (error) => toastError(error),
  });

  const checkSyntax = React.useCallback(async (modelId: string) => {
    setSyntax('CHECKING');
    try {
      const created = await transformApi.run(transformId, 'COMPILE', modelId);
      syntaxRun.current = created.id;
    } catch {
      setSyntax('UNKNOWN');
    }
  }, [transformId]);

  const save = useMutation({
    mutationFn: async () => {
      if (!draft) throw new Error('No model selected');
      return transformApi.updateModel(transformId, draft.id, {
        sql: draft.sql, layer: draft.layer, materialization: draft.materialization,
        output_schema: draft.output_schema, relation_name: draft.relation_name,
        description: draft.description, tags: draft.tags, config: draft.config,
        version: draft.version,
      });
    },
    onSuccess: (updated) => {
      queryClient.setQueryData(qk.transform(workspaceId, transformId), (current: typeof query.data) => current ? ({
        ...current, models: current.models.map((item) => item.id === updated.id ? updated : item),
      }) : current);
      setDraft(structuredClone(updated));
      setDirty(false);
      toastSuccess(copy.saved);
      void checkSyntax(updated.id);
    },
    onError: async (error) => {
      // A stale `version` makes the server reject the save. Without refreshing
      // it here every later save is rejected too, and the only way out is a
      // reload that throws away the SQL the user just wrote.
      if (error instanceof ApiError && error.status === 409) {
        const fresh = await transformApi.detail(transformId).catch(() => null);
        const server = fresh?.models.find((item) => item.id === draft?.id);
        if (fresh) queryClient.setQueryData(qk.transform(workspaceId, transformId), fresh);
        if (server) { setConflict({ server }); return; }
      }
      toastError(error);
    },
  });

  const run = useMutation({
    mutationFn: async ({ operation, modelId, fullRefresh, source }: {
      operation: Operation; modelId?: string; fullRefresh?: boolean;
      source?: 'DRAFT' | 'RELEASE';
    }) => {
      // VALIDATE probes the warehouse, not the SQL, so it must not force an
      // unsaved model to be written just to check a connection.
      // A released run executes frozen code, so there is nothing to save first.
      if (dirty && operation !== 'VALIDATE' && source !== 'RELEASE') {
        await save.mutateAsync();
      }
      // Clear the previous run's outcome first: until the new run id arrives,
      // the panel would otherwise keep showing the last run's SUCCEEDED badge,
      // which reads as if the run that just started had already passed.
      setActiveRunId(null);
      return transformApi.run(transformId, operation, modelId, { fullRefresh, source });
    },
    onSuccess: (created) => {
      setActiveRunId(created.id);
      setBottomTab(
        created.operation === 'COMPILE' ? 'compiled'
          : created.operation === 'PREVIEW' ? 'preview' : 'logs',
      );
      queryClient.invalidateQueries({ queryKey: qk.runs(workspaceId) });
    },
    onError: (error) => toastError(error),
  });

  const execution = useQuery({
    queryKey: qk.transformRun(workspaceId, activeRunId ?? ''),
    queryFn: () => transformApi.execution(activeRunId!),
    enabled: Boolean(activeRunId),
    // Keep polling until a terminal status arrives. Gating on `data` alone
    // stops the poll whenever the first response has not landed yet, which
    // leaves the panel showing QUEUED for a run that has already finished.
    refetchInterval: (state) => {
      const status = state.state.data?.status;
      if (!status) return 1500;
      return ACTIVE.includes(status) ? 1500 : false;
    },
    staleTime: 0,
  });

  React.useEffect(() => {
    if (!execution.data || ACTIVE.includes(execution.data.status)) return;
    if (execution.data.id === syntaxRun.current) {
      setSyntax(execution.data.status === 'SUCCEEDED' ? 'OK' : 'ERROR');
    }
    queryClient.invalidateQueries({ queryKey: qk.transform(workspaceId, transformId) });
    queryClient.invalidateQueries({ queryKey: qk.runs(workspaceId) });
  }, [execution.data, queryClient, transformId, workspaceId]);

  React.useEffect(() => {
    const shortcut = (event: KeyboardEvent) => {
      if (!(event.ctrlKey || event.metaKey)) return;
      if (event.key.toLowerCase() === 's') {
        event.preventDefault();
        if (dirty && canEdit) save.mutate();
      }
      if (event.key === 'Enter' && draft && canRun) {
        event.preventDefault();
        run.mutate({ operation: 'PREVIEW', modelId: draft.id });
      }
    };
    window.addEventListener('keydown', shortcut);
    return () => window.removeEventListener('keydown', shortcut);
  }, [canEdit, canRun, dirty, draft, run, save]);

  const aiDrafting = useMutation({
    mutationFn: ({ assetId, intent }: { assetId: string; intent: string }) =>
      transformApi.draftModel(transformId, { asset_id: assetId, intent }),
    onMutate: () => { setAiError(null); setAiDraft(null); },
    onSuccess: (result) => setAiDraft(result),
    onError: (error) => setAiError(
      error instanceof ApiError ? error.message : String(error),
    ),
  });

  /**
   * Accept a draft: create the model, write its SQL, attach its tests.
   *
   * The tests come along because they were checked against the real output
   * schema during drafting; leaving them for the user to retype is asking them
   * to redo work that has already been verified.
   */
  const acceptDraft = useMutation({
    mutationFn: async (drafted: import('@/lib/types').DraftedModel) => {
      const taken = new Set((query.data?.models ?? []).map((item) => item.name));
      let name = drafted.name;
      for (let suffix = 2; taken.has(name); suffix += 1) name = `${drafted.name}_${suffix}`;
      const created = await transformApi.createModel(transformId, {
        name, layer: drafted.layer, materialization: drafted.materialization,
      });
      const saved = await transformApi.updateModel(transformId, created.id, {
        sql: drafted.sql, version: created.version,
        description: drafted.summary || undefined,
      });
      for (const test of drafted.tests) {
        try {
          await transformApi.addTest(transformId, created.id, {
            column_name: test.column_name, rule: test.rule, severity: 'ERROR',
          });
        } catch {
          // A rejected test must not cost the user the model they just accepted.
        }
      }
      return saved;
    },
    onSuccess: async (created) => {
      await queryClient.invalidateQueries({ queryKey: qk.transform(workspaceId, transformId) });
      setSelectedId(created.id); setDraft(structuredClone(created)); setDirty(false);
      setAiOpen(false); setAiDraft(null);
      toastSuccess(copy.aiAccepted);
    },
    onError: (error) => toastError(error),
  });

  const createModel = useMutation({
    mutationFn: async () => {
      const created = await transformApi.createModel(transformId, newModel);
      const template = MODEL_TEMPLATES.find((item) => item.id === newTemplate);
      if (!template || template.id === 'blank') return created;
      const firstInput = (query.data?.inputs ?? [])[0];
      const seeded = template.sql({
        source: firstInput?.source_name ?? undefined,
        table: firstInput?.relation_name ?? undefined,
        upstream: (query.data?.models ?? [])[0]?.name,
      });
      return transformApi.updateModel(transformId, created.id, {
        sql: seeded, version: created.version,
      });
    },
    onSuccess: async (created) => {
      await queryClient.invalidateQueries({ queryKey: qk.transform(workspaceId, transformId) });
      setSelectedId(created.id); setDraft(structuredClone(created)); setDirty(false);
      setNewModelOpen(false); setNewModel({ name: '', layer: 'STAGING', materialization: 'VIEW' });
      setNewTemplate('staging');
    },
    onError: (error) => toastError(error),
  });
  const removeModel = useMutation({
    mutationFn: (modelId: string) => transformApi.removeModel(transformId, modelId),
    onSuccess: async (_result, modelId) => {
      // Land on the neighbour rather than the top of the rail: in a grouped
      // list, jumping from a Mart model back to the first Staging one loses
      // the user's place entirely.
      const previous = query.data?.models ?? [];
      const index = previous.findIndex((item) => item.id === modelId);
      setDirty(false);
      setSelectedId(null);
      setDraft(null);
      await queryClient.invalidateQueries({ queryKey: qk.transform(workspaceId, transformId) });
      const remaining = previous.filter((item) => item.id !== modelId);
      const next = remaining[Math.min(Math.max(index, 0), remaining.length - 1)];
      if (next) { setSelectedId(next.id); setDraft(structuredClone(next)); }
    },
    onError: (error) => toastError(error),
  });
  // Tests are stored server-side, but the editor holds unsaved SQL. Replacing
  // the whole draft after a test change would throw that SQL away, so only the
  // test list is merged back in and `dirty` is left exactly as it was.
  const syncTestsIntoDraft = React.useCallback(async () => {
    const refreshed = await transformApi.detail(transformId);
    queryClient.setQueryData(qk.transform(workspaceId, transformId), refreshed);
    setDraft((current) => {
      if (!current) return current;
      const model = refreshed.models.find((item) => item.id === current.id);
      return model ? { ...current, tests: model.tests } : current;
    });
  }, [queryClient, transformId, workspaceId]);

  const addTest = useMutation({
    mutationFn: () => transformApi.addTest(transformId, draft!.id, {
      column_name: testForm.column_name || null, rule: testForm.rule, severity: testForm.severity,
      config: testForm.rule === 'ACCEPTED_VALUES'
        ? { values: testForm.values.split(',').map((value) => value.trim()).filter(Boolean) }
        : testForm.rule === 'RELATIONSHIPS'
          ? { to: testForm.target_model, field: testForm.target_field }
          : {},
    }),
    onSuccess: async () => {
      await syncTestsIntoDraft();
      setTestForm(EMPTY_TEST_FORM);
    },
    onError: (error) => toastError(error),
  });
  const removeTest = useMutation({
    mutationFn: (testId: string) => transformApi.removeTest(transformId, draft!.id, testId),
    onSuccess: () => syncTestsIntoDraft(),
    onError: (error) => toastError(error),
  });

  // Only mark a line when the failure belongs to the model on screen -- a
  // marker pointing at an unrelated model's line number is worse than none.
  const errorLine = React.useMemo(() => {
    const location = execution.data?.error?.location;
    if (!location?.line || !draft) return null;
    if (location.name && location.name !== draft.name) return null;
    return location.line;
  }, [execution.data, draft]);

  // Put the caret on the offending line as soon as a run reports one, rather
  // than making the user read a number and scroll there.
  React.useEffect(() => {
    if (errorLine) editorRef.current?.jumpToLine(errorLine);
  }, [errorLine]);

  // `run.isPending` only covers the POST; the run itself lives on in polling,
  // so without this the user can fire a second build over the first.
  const running = run.isPending
    || Boolean(execution.data && ACTIVE.includes(execution.data.status));

  const cancel = useMutation({
    mutationFn: () => transformApi.cancel(activeRunId!),
    onError: (error) => toastError(error),
  });

  // Everything the editor can complete: sibling models for `ref`, source
  // aliases for `source`, and the columns of this Transform's inputs.
  const completionSets = React.useMemo(() => ({
    refs: (query.data?.models ?? []).map((item) => item.name),
    sources: [...new Set((query.data?.inputs ?? [])
      .map((item) => item.source_name).filter(Boolean) as string[])],
  }), [query.data]);

  const inputColumns = React.useMemo(() => [...new Set(
    (query.data?.inputs ?? []).flatMap((item) => (item.columns ?? [])
      .map((column) => column.name).filter(Boolean) as string[]),
  )], [query.data]);

  // Remembered per pane so somebody who works mostly in SQL can fold the rest
  // away once rather than on every visit.
  const [inputsOpen, toggleInputs] = usePaneState('inputs', true);
  const [sideOpen, toggleSide] = usePaneState('config', true);
  const [railWidth, setRailWidth] = usePaneSize('rail', 224, 176, 420);
  const [sideWidth, setSideWidth] = usePaneSize('side', 260, 200, 520);
  const [editorRatio, setEditorRatio] = usePaneSize('editorRatio', 60, 25, 85);
  const splitRef = React.useRef<HTMLElement | null>(null);
  const [modelsOpen, toggleModels] = usePaneState('models', true);
  const [historyOpen, setHistoryOpen] = React.useState(false);
  const [diffWanted, setDiffWanted] = React.useState(false);
  const [inspectId, setInspectId] = React.useState<string | null>(null);
  const releaseModels = useQuery({
    queryKey: qk.transformRelease(workspaceId, transformId, inspectId ?? ''),
    queryFn: () => transformApi.releaseModels(transformId, inspectId!),
    enabled: Boolean(inspectId),
  });
  const diff = useQuery({
    queryKey: qk.transformDiff(workspaceId, transformId),
    queryFn: () => transformApi.diff(transformId),
    enabled: diffWanted,
  });
  const releases = useQuery({
    queryKey: qk.transformReleases(workspaceId, transformId),
    queryFn: () => transformApi.releases(transformId),
    enabled: historyOpen,
  });

  const rename = useMutation({
    mutationFn: (name: string) => transformApi.update(transformId, {
      name: name.trim(), version: query.data?.version,
    }),
    onSuccess: async () => {
      setRenaming(false);
      await queryClient.invalidateQueries({ queryKey: qk.transform(workspaceId, transformId) });
      queryClient.invalidateQueries({ queryKey: qk.transforms(workspaceId) });
    },
    onError: (error) => { setRenaming(false); toastError(error); },
  });

  const publish = useMutation({
    mutationFn: (notes: string) =>
      transformApi.publish(transformId, { notes: notes || null, activate: true }),
    onSuccess: async (created) => {
      await queryClient.invalidateQueries({ queryKey: qk.transform(workspaceId, transformId) });
      queryClient.invalidateQueries({ queryKey: qk.transformReleases(workspaceId, transformId) });
      queryClient.invalidateQueries({ queryKey: qk.transformDiff(workspaceId, transformId) });
      toastSuccess(copy.published.replace('{n}', String(created.release_number)));
    },
    onError: (error) => toastError(error),
  });

  const restoreDraft = useMutation({
    mutationFn: (releaseId: string) => transformApi.restoreRelease(transformId, releaseId),
    onSuccess: async (detail) => {
      await queryClient.invalidateQueries({ queryKey: qk.transform(workspaceId, transformId) });
      queryClient.invalidateQueries({ queryKey: qk.transformDiff(workspaceId, transformId) });
      const current = detail.models.find((item) => item.id === selectedId);
      if (current) { setDraft(structuredClone(current)); setDirty(false); }
      setInspectId(null);
      setHistoryOpen(false);
      toastSuccess(copy.restoredToDraft);
    },
    onError: (error) => toastError(error),
  });

  const restore = useMutation({
    mutationFn: (releaseId: string) => transformApi.activateRelease(transformId, releaseId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: qk.transform(workspaceId, transformId) });
      queryClient.invalidateQueries({ queryKey: qk.transformReleases(workspaceId, transformId) });
      setHistoryOpen(false);
      toastSuccess(copy.restored);
    },
    onError: (error) => toastError(error),
  });

  const visibleModels = React.useMemo(() => {
    const needle = modelFilter.trim().toLowerCase();
    const models = query.data?.models ?? [];
    return needle ? models.filter((model) => model.name.toLowerCase().includes(needle)) : models;
  }, [query.data, modelFilter]);

  const visibleInputs = React.useMemo(() => {
    const needle = modelFilter.trim().toLowerCase();
    const inputs = query.data?.inputs ?? [];
    const matched = needle
      ? inputs.filter((item) => `${item.schema_name}.${item.relation_name}`
        .toLowerCase().includes(needle)
        || (item.pipeline_name ?? '').toLowerCase().includes(needle))
      : inputs;
    const grouped = new Map<string, typeof matched>();
    for (const item of matched) {
      const key = item.pipeline_name ?? copy.warehouseRelation;
      grouped.set(key, [...(grouped.get(key) ?? []), item]);
    }
    return [...grouped.entries()];
  }, [query.data, modelFilter, copy.warehouseRelation]);

  // Last run's per-node outcome, keyed by model name, so the rail can show
  // where the project actually stands rather than only what is selected.
  const nodeByModel = React.useMemo(() => {
    const map = new Map<string, import('@/lib/types').TransformRunNode>();
    for (const node of execution.data?.nodes ?? []) {
      if (node.resource_type === 'MODEL') map.set(node.name, node);
    }
    return map;
  }, [execution.data]);

  /** Models whose SQL still references this one by `ref()`. */
  const dependents = React.useCallback((name: string) => (query.data?.models ?? [])
    .filter((model) => model.name !== name && refsOf(model.sql).has(name))
    .map((model) => model.name), [query.data]);

  /** Point every `ref('from')` in this model at `to`. */
  const fixRef = React.useCallback((from: string, to: string) => {
    setDraft((current) => {
      if (!current) return current;
      const next = current.sql.replace(
        REF_PATTERN,
        (whole, open: string, name: string, close: string) =>
          (name === from ? open + to + close : whole),
      );
      return { ...current, sql: next };
    });
    setDirty(true);
  }, []);

  const jumpToLine = React.useCallback((line: number) => {
    editorRef.current?.jumpToLine(line);
  }, []);

  const duplicateModel = useMutation({
    mutationFn: async (model: TransformModel) => {
      const created = await transformApi.createModel(transformId, {
        name: `${model.name}_copy`, layer: model.layer,
        materialization: model.materialization,
      });
      return transformApi.updateModel(transformId, created.id, {
        sql: model.sql, description: model.description, tags: model.tags,
        config: model.config, version: created.version,
      });
    },
    onSuccess: async (created) => {
      await queryClient.invalidateQueries({ queryKey: qk.transform(workspaceId, transformId) });
      setSelectedId(created.id); setDraft(structuredClone(created)); setDirty(false);
    },
    onError: (error) => toastError(error),
  });

  const lineage = useQuery({
    queryKey: qk.transformLineage(workspaceId, transformId),
    queryFn: () => transformApi.lineage(transformId),
  });
  const generatedProject = useQuery({
    queryKey: ['workspace', workspaceId, 'transform', transformId, 'project'],
    queryFn: () => transformApi.project(transformId), enabled: generatedOpen,
  });

  const patchDraft = (changes: Partial<Draft>) => {
    setDraft((current) => current ? { ...current, ...changes } : current);
    setDirty(true);
  };

  // Typing `{{ source('src_raw','deals') }}` by hand is the single easiest way
  // to break a model: one wrong quote and dbt reports a missing relation. The
  // aliases come from the server, so a picked reference is always spelled right.
  const insertSnippet = React.useCallback((snippet: string) => {
    editorRef.current?.insert(snippet);
  }, []);

  const referenceItems = React.useMemo(() => {
    const inputs = (query.data?.inputs ?? [])
      .filter((asset) => asset.source_name)
      .map((asset) => ({
        id: `source:${asset.id}`,
        label: `source · ${asset.schema_name}.${asset.relation_name}`,
        onSelect: () => insertSnippet(
          `{{ source('${asset.source_name}', '${asset.relation_name}') }}`,
        ),
      }));
    const models = (query.data?.models ?? [])
      .filter((model) => model.id !== selectedId)
      .map((model) => ({
        id: `ref:${model.id}`,
        label: `ref · ${model.name}`,
        onSelect: () => insertSnippet(`{{ ref('${model.name}') }}`),
      }));
    return [...inputs, ...models];
  }, [query.data, insertSnippet, selectedId]);
  const chooseModel = (modelId: string) => {
    if (modelId === selectedId) return;
    if (dirty) { setPendingModelId(modelId); return; }
    const model = query.data?.models.find((item) => item.id === modelId);
    if (model) { setSelectedId(model.id); setDraft(structuredClone(model)); }
  };
  const finishSwitch = (modelId: string) => {
    const model = query.data?.models.find((item) => item.id === modelId);
    if (model) { setSelectedId(model.id); setDraft(structuredClone(model)); setDirty(false); }
    setPendingModelId(null);
  };

  if (query.isLoading) return <Spinner label={copy.loading} />;
  if (query.error) return <div className="p-6"><ErrorState title={copy.loadError} message={(query.error as Error).message} onRetry={() => query.refetch()} /></div>;
  if (!query.data) return null;
  const transform = query.data;

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
      {/* Subtitle carries the warehouse only. Output schema and engine
          version are settings, not identity, and putting them in the title
          block hands every visitor two pieces of jargon on arrival. */}
      <DetailHeader
        backHref="/transforms" backLabel="Transform"
        title={<span className="flex items-center gap-1.5">
          {renaming ? (
            <Input
              autoFocus size="sm" className="w-64" value={renameValue}
              onChange={(event) => setRenameValue(event.target.value)}
              onBlur={() => rename.mutate(renameValue)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') rename.mutate(renameValue);
                if (event.key === 'Escape') setRenaming(false);
              }}
            />
          ) : (
            <>
              {transform.name}
              {canEdit && (
                <IconButton size="xs" variant="ghost"
                  aria-label={copy.rename} title={copy.rename}
                  onClick={() => { setRenameValue(transform.name); setRenaming(true); }}>
                  <Pencil className="h-3.5 w-3.5" />
                </IconButton>
              )}
            </>
          )}
        </span>}
        icon={<Workflow className="h-5 w-5 text-brand" />}
        subtitle={<span className="text-caption text-text-tertiary" title={`${transform.default_schema} · dbt Core ${transform.dbt_core_version}`}>{transform.destination.name}</span>}
        badges={<>
          <Badge variant={healthTone[transform.health_status] ?? 'neutral'} size="xs" dot
            title={transform.health_message ?? undefined}>
            {transform.health_status}
          </Badge>
          {transform.execution_trigger === 'AFTER_UPSTREAM' && !transform.upstream_ready && (
            <Badge variant="warning" size="xs">{copy.waitingUpstream}</Badge>
          )}
        </>}
        actions={<div className="flex shrink-0 items-center gap-1.5">
          {/* Lineage, the generated project and a connection check are things
              somebody reaches for occasionally. Ranked beside Run they compete
              with it for attention and make the header read as a wall. */}
          <Menu label={copy.tools} items={[
            { id: 'validate', label: copy.validate,
              onSelect: () => run.mutate({ operation: 'VALIDATE' }) },
            { id: 'project', label: locale === 'vi' ? 'Project được sinh' : 'Generated project',
              onSelect: () => setGeneratedOpen(true) },
            { id: 'settings', label: copy.settings, onSelect: () => setSettingsOpen(true) },
          ]} trigger={<span title={copy.tools} aria-label={copy.tools}
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-text-secondary hover:bg-surface-2">
            <Wrench className="h-4 w-4" />
          </span>} />
          <Button size="sm" variant="secondary" leadingIcon={<Save className="h-4 w-4" />} loading={save.isPending} disabled={!dirty || !canEdit} onClick={() => save.mutate()}>{copy.save}</Button>
          <Button size="sm" variant="primary" leadingIcon={<Play className="h-4 w-4" />}
            loading={run.isPending && run.variables?.operation === 'BUILD'}
            disabled={!canRun || running}
            title={copy.runDraftHelp.replace('{schema}', `${transform.default_schema}_draft`)}
            onClick={() => run.mutate({ operation: 'BUILD' })}>{copy.runTransform}</Button>
          {/* Running the published version is a different act from testing the
              draft -- same verb, different code -- so it gets its own entry
              rather than a mode toggle somebody can leave in the wrong state. */}
          {transform.active_release && (
            <Menu label={copy.moreRunOptions} items={[
              { id: 'live', label: copy.runLive.replace(
                  '{n}', String(transform.active_release.release_number)),
                onSelect: () => run.mutate({ operation: 'BUILD', source: 'RELEASE' }) },
            ]} trigger={<span title={copy.moreRunOptions}
              className="flex h-8 w-6 items-center justify-center rounded-md border border-[rgb(var(--border-strong))] bg-surface-1 text-text-secondary hover:border-brand hover:text-brand">⋯</span>} />
          )}
        </div>}
      />

      <PublishBar
        transform={transform} copy={copy} canEdit={canEdit}
        publishing={publish.isPending}
        onPublish={(notes) => publish.mutate(notes)}
        onOpenHistory={() => setHistoryOpen(true)}
        diff={diff.data?.changes} diffLoading={diff.isLoading}
        onRequestDiff={() => setDiffWanted(true)}
      />

      {/* The app sidebar already takes 240px, so on a 1366px laptop the old
          230/290 rails left about 600px for SQL. These are narrower and the
          three-column layout starts at lg rather than xl. */}
      {/* Column widths are the user's to set, so the template is computed
          rather than a class. Below md the panes stack and the seams are
          hidden, because dragging a 1px target on a phone is not a feature. */}
      <div
        className="relative grid min-h-0 flex-1 grid-cols-1 border-t border-[rgb(var(--border-line))] bg-surface-1 md:grid-cols-[var(--rail)_4px_minmax(0,1fr)] lg:grid-cols-[var(--rail)_4px_minmax(0,1fr)_var(--seam)_var(--side)]"
        style={{
          '--rail': `${railWidth}px`,
          '--side': sideOpen ? `${sideWidth}px` : '0px',
          '--seam': sideOpen ? '4px' : '0px',
        } as React.CSSProperties}
      >
        {/* Its own stacking context, above the editor: the SQL textarea is
            absolutely positioned with z-auto and comes later in the DOM, so a
            row menu opening over it would otherwise be painted underneath. */}
        <aside className="relative z-20 flex min-h-0 flex-col border-r border-[rgb(var(--border-line))] bg-surface-1">
          {/* Two tabs rather than two stacked sections: sources and models
              answer different questions and are never read together, so
              stacking them spends twice the height for no gain. */}
          <div className="shrink-0 space-y-1.5 p-2 pb-1">
            {/* Creating a model used to be reachable only from the empty state,
                so the second model was harder to make than the first. */}
            {canEdit && (
              <div className="flex gap-1.5">
                <Button size="xs" variant="secondary" className="flex-1"
                  leadingIcon={<Plus className="h-3.5 w-3.5" />}
                  onClick={() => setNewModelOpen(true)}>{copy.newModel}</Button>
                <Button size="xs" variant="secondary" className="flex-1"
                  leadingIcon={<Sparkles className="h-3.5 w-3.5" />}
                  title={copy.ai.description}
                  onClick={() => { setAiDraft(null); setAiError(null); setAiOpen(true); }}>
                  {copy.aiButton}
                </Button>
              </div>
            )}
            <Input size="sm" value={modelFilter} aria-label={copy.filterAll}
              placeholder={copy.filterAll}
              onChange={(event) => setModelFilter(event.target.value)} />
          </div>
          <Tabs className="shrink-0" value={railTab} onChange={setRailTab} items={[
            { id: 'models', label: copy.models },
            { id: 'sources', label: copy.inputs },
          ]} />
          <div className="min-h-0 flex-1 overflow-y-auto px-1 py-2">
          {railTab === 'models' && (<>
            {(['STAGING', 'CORE', 'MART'] as const).map((layer) => {
              const models = visibleModels.filter((model) => model.layer === layer);
              if (!models.length) return null;
              return (
                <div key={layer} className="mb-2">
                  <p className="px-2 py-1 text-[10px] font-emphasis uppercase text-text-quaternary">
                    {copy.layerLabel[layer]}
                  </p>
                  {models.map((model) => {
                    // Dataform marks every action with the outcome of the last
                    // run; without it the rail says nothing about whether the
                    // project is healthy.
                    const node = nodeByModel.get(model.name);
                    const failed = node && !['SUCCESS', 'PASS', 'SKIPPED'].includes(node.status);
                    const failedTests = model.tests.filter((test) => test.last_status === 'FAILED').length;
                    return (
                      <div key={model.id}
                        className={cn('group flex h-8 w-full items-center gap-1.5 pl-2 pr-1',
                          selectedId === model.id ? 'bg-brand/[0.08]' : 'hover:bg-surface-2')}>
                        <button type="button" onClick={() => chooseModel(model.id)}
                          title={`${model.name} · ${model.materialization.toLowerCase()}`}
                          className={cn('flex min-w-0 flex-1 items-center gap-2 text-left text-caption',
                            selectedId === model.id ? 'text-brand' : 'text-text-secondary')}>
                          <span className={cn('h-1.5 w-1.5 shrink-0 rounded-full',
                            failed || failedTests ? 'bg-danger'
                              : node ? 'bg-success' : 'bg-[rgb(var(--border-strong))]')} />
                          <span className="min-w-0 flex-1 truncate">{model.name}</span>
                          {selectedId === model.id && dirty
                            && <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-warning" title={copy.unsaved} />}
                        </button>
                        {canEdit && (
                          <span className="opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
                            <Menu label={copy.modelActions} align="end" items={[
                              { id: 'run', label: copy.runModel,
                                onSelect: () => { chooseModel(model.id); run.mutate({ operation: 'RUN_MODEL', modelId: model.id }); } },
                              { id: 'preview', label: copy.preview,
                                onSelect: () => { chooseModel(model.id); run.mutate({ operation: 'PREVIEW', modelId: model.id }); } },
                              { id: 'duplicate', label: copy.duplicateModel,
                                onSelect: () => duplicateModel.mutate(model) },
                              { id: 'delete', label: copy.deleteModel, destructive: true,
                                onSelect: () => setPendingDelete(model) },
                            ]} trigger={<span title={copy.modelActions}
                              className="flex h-5 w-5 items-center justify-center rounded text-text-quaternary hover:bg-surface-3 hover:text-text-primary">⋯</span>} />
                          </span>
                        )}
                      </div>
                    );
                  })}
                </div>
              );
            })}
            {visibleModels.length === 0 && (
              <p className="px-2 py-3 text-tiny text-text-quaternary">{copy.noModelMatch}</p>
            )}
          </>)}
          {railTab === 'sources' && (<>
            {visibleInputs.map(([pipeline, assets]) => (
              <div key={pipeline} className="mb-2">
                <p className="px-2 py-1 text-[10px] font-emphasis uppercase text-text-quaternary">
                  {pipeline}
                </p>
                {assets.map((asset) => (
                  <div key={asset.id} className="group relative flex items-center pr-1 hover:bg-surface-2">
                    <div className="min-w-0 flex-1 px-2 py-1.5">
                      <p className="flex items-center gap-1 truncate font-mono text-tiny text-text-secondary">
                        <span className="min-w-0 flex-1 truncate">{asset.relation_name}</span>
                        {/* "Stale" only means something when the Transform is
                            waiting on upstream data; after a manual build every
                            input is older than the build, which is normal. */}
                        {asset.freshness_state === 'STALE'
                          && transform.execution_trigger === 'AFTER_UPSTREAM' && (
                          <Clock className="h-3 w-3 shrink-0 text-warning" aria-label={copy.stale} />
                        )}
                        {asset.freshness_state === 'UNRESOLVED' && (
                          <AlertTriangle className="h-3 w-3 shrink-0 text-danger" aria-label={copy.unresolved} />
                        )}
                      </p>
                      <p className="truncate text-[10px] text-text-quaternary">
                        {asset.columns.length} {copy.columnsShort}
                      </p>
                    </div>
                    {canEdit && asset.source_name && (
                      <span className="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
                        <IconButton size="xs" variant="ghost"
                          aria-label={copy.exploreInput} title={copy.exploreInput}
                          loading={exploreInput.isPending}
                          onClick={() => exploreInput.mutate(asset)}>
                          <Eye className="h-3.5 w-3.5" />
                        </IconButton>
                        {/* Inserting the reference is a different intent from
                            previewing the table, so it gets its own control. */}
                        <IconButton size="xs" variant="ghost"
                          aria-label={copy.insertReference} title={copy.insertReference}
                          onClick={() => insertSnippet(
                            `{{ source('${asset.source_name}', '${asset.relation_name}') }}`,
                          )}>
                          <Plus className="h-3.5 w-3.5" />
                        </IconButton>
                      </span>
                    )}
                  </div>
                ))}
              </div>
            ))}
            {visibleInputs.length === 0 && (
              <p className="px-2 py-3 text-tiny text-text-quaternary">{copy.noModelMatch}</p>
            )}
          </>)}
          </div>
        </aside>

        {/* Seam between the rail and the editor. */}
        <Resizer
          orientation="vertical" ariaLabel={copy.resizeRail}
          value={railWidth} onResize={setRailWidth}
        />

        <main ref={splitRef} className="flex min-h-[620px] min-w-0 flex-col overflow-hidden bg-surface-0 md:min-h-0">
          {draft ? <>
            <div className="flex h-11 shrink-0 items-center gap-2 border-b border-[rgb(var(--border-line))] bg-surface-1 px-3">
              <span className="min-w-0 flex-1 truncate text-caption font-strong text-text-primary">{draft.name}</span>
              {/* The "is my SQL valid?" answer, given continuously rather than
                  only when somebody thinks to ask for it. */}
              {syntax === 'CHECKING' && (
                <Loader2 className="h-3 w-3 shrink-0 animate-spin text-text-quaternary" />
              )}
              {syntax === 'OK' && !dirty && (
                <span title={copy.syntaxOk}
                  className="flex shrink-0 items-center gap-1 text-tiny text-success">
                  <Check className="h-3 w-3" />{copy.syntaxOk}
                </span>
              )}
              {syntax === 'ERROR' && !dirty && (
                <span title={copy.syntaxError}
                  className="flex shrink-0 items-center gap-1 text-tiny text-danger">
                  <AlertTriangle className="h-3 w-3" />{copy.syntaxError}
                </span>
              )}
              {dirty && <Badge variant="warning" size="xs">{copy.unsaved}</Badge>}
              {canEdit && referenceItems.length > 0 && (
                <Menu
                  label={copy.insertReference}
                  items={referenceItems}
                  trigger={<span title={copy.insertReference}
                    className="flex h-7 shrink-0 items-center gap-1 whitespace-nowrap rounded-md border border-[rgb(var(--border-strong))] bg-surface-1 px-2 text-tiny text-text-secondary hover:border-brand hover:text-brand">
                    <Braces className="h-3.5 w-3.5" />ref
                  </span>}
                />
              )}
              {/* Preview answers "is this SQL right?", which is the question
                  being asked almost every time. The rest -- compiling, testing,
                  writing the table -- are steps you take once you already
                  believe the SQL, so they sit one click away instead of
                  competing for the same glance. */}
              {/* One control, not two: the caret reads as "this action and its
                  variants" where a separate overflow reads as a mystery menu. */}
              <span className="flex shrink-0 items-center">
              <Button size="xs" variant="primary" className="rounded-r-none"
                leadingIcon={<Eye className="h-3.5 w-3.5" />}
                disabled={!canRun || running}
                loading={run.isPending && run.variables?.operation === 'PREVIEW'}
                title={copy.previewHelp}
                onClick={() => run.mutate({ operation: 'PREVIEW', modelId: draft.id })}>
                {copy.preview}
              </Button>
              <Menu label={copy.modelActions} items={[
                { id: 'test', label: copy.testModel, description: copy.testHelp,
                  onSelect: () => run.mutate({ operation: 'TEST', modelId: draft.id }) },
                { id: 'run', label: copy.runModel, description: copy.runModelHelp,
                  onSelect: () => run.mutate({ operation: 'RUN_MODEL', modelId: draft.id }) },
                { id: 'upstream', label: copy.runUpstream, description: copy.runUpstreamHelp,
                  onSelect: () => run.mutate({ operation: 'RUN_UPSTREAM', modelId: draft.id }) },
                { id: 'refresh', label: copy.fullRefreshModel, description: copy.fullRefreshHelp,
                  onSelect: () => run.mutate({ operation: 'RUN_MODEL', modelId: draft.id, fullRefresh: true }) },
              ]} trigger={<span title={copy.moreActions} aria-label={copy.moreActions}
                className="flex h-7 w-6 shrink-0 items-center justify-center rounded-r-md border border-l-0 border-brand bg-brand text-white hover:bg-brand-hover">
                <ChevronDown className="h-3.5 w-3.5" /></span>} />
              </span>
              {/* Model settings are a once-per-model errand; reserving a column
                  for them costs the editor width on every screen. */}
              <IconButton size="xs" variant="ghost"
                aria-label={sideOpen ? copy.hidePanel : copy.showPanel}
                title={sideOpen ? copy.hidePanel : copy.showPanel}
                onClick={toggleSide}>
                {sideOpen
                  ? <PanelRightClose className="h-3.5 w-3.5" />
                  : <PanelRightOpen className="h-3.5 w-3.5" />}
              </IconButton>
            </div>
            {/* The editor owns the remaining height; the surface token keeps it
                on the app's palette in both themes rather than a fixed hex. */}
            <div className="min-h-[140px] min-w-0 overflow-hidden bg-surface-2"
              style={{ flex: `${editorRatio} 1 0%` }}>
            <SqlEditor
              ref={editorRef} value={draft.sql}
              onChange={(sql) => patchDraft({ sql })}
              onSave={() => { if (dirty && canEdit) save.mutate(); }}
              onRun={() => { if (canRun) run.mutate({ operation: 'PREVIEW', modelId: draft.id }); }}
              readOnly={!canEdit}
              completions={completionSets}
              columns={inputColumns}
            />
            </div>
            {/* Editor against results. The ratio is a percentage so the split
                survives a window resize rather than pinning one pane to pixels. */}
            <Resizer
              orientation="horizontal" ariaLabel={copy.resizeEditor}
              value={editorRatio} step={3} scaleFrom={splitRef}
              onResize={setEditorRatio}
            />
            <OutputPanel
              ratio={100 - editorRatio}
              tab={bottomTab} setTab={setBottomTab} execution={execution.data}
              loading={execution.isFetching && Boolean(activeRunId)} runId={activeRunId}
              modelName={draft.name} copy={copy}
              onCancel={() => cancel.mutate()} cancelling={cancel.isPending}
              onJump={jumpToLine}
              canEdit={canEdit}
              lineage={lineage.data} lineageLoading={lineage.isLoading}
              lineageExpanded={editorRatio <= 26}
              onExpandLineage={() => setEditorRatio(editorRatio <= 26 ? 60 : 25)}
              knownModels={(query.data?.models ?? []).map((item) => item.name)}
              onFixRef={fixRef}
            />
          </> : <div className="flex flex-1 items-center justify-center p-6"><EmptyState icon={Code2} title={copy.noModel} action={canEdit ? <Button size="sm" variant="primary" onClick={() => setNewModelOpen(true)}>{copy.newModel}</Button> : undefined} /></div>}
        </main>

        {sideOpen && (
          <Resizer
            orientation="vertical" ariaLabel={copy.resizeSide} invert
            value={sideWidth} onResize={setSideWidth}
          />
        )}
        {sideOpen && (
        <aside className="flex min-h-[320px] flex-col border-t border-[rgb(var(--border-line))] bg-surface-1 md:col-span-2 lg:col-span-1 lg:min-h-0 lg:border-l lg:border-t-0">
          {/* Its own title and close control: the panel arrives on request, so
              it has to say what it is and how to dismiss it. */}
          <div className="flex h-9 shrink-0 items-center gap-2 border-b border-[rgb(var(--border-line))] px-3">
            <span className="text-caption font-emphasis text-text-primary">{copy.modelSettings}</span>
            <IconButton size="xs" variant="ghost" className="ml-auto"
              aria-label={copy.hidePanel} title={copy.hidePanel} onClick={toggleSide}>
              <X className="h-3.5 w-3.5" />
            </IconButton>
          </div>
          <Tabs value={rightTab} onChange={setRightTab} items={[{ id: 'config', label: copy.config }, { id: 'tests', label: copy.tests, count: draft?.tests.length ?? 0 }]} />
          <div className="min-h-0 flex-1 overflow-y-auto p-3">
            {draft && rightTab === 'config' && <ConfigPanel draft={draft} patchDraft={patchDraft} copy={copy} canEdit={canEdit} onDelete={() => setPendingDelete(draft)} deleting={removeModel.isPending} adapter={transform.dbt_adapter_name} />}
            {draft && rightTab === 'tests' && <TestsPanel draft={draft} form={testForm} setForm={setTestForm} add={() => addTest.mutate()} adding={addTest.isPending} remove={(id) => removeTest.mutate(id)} canEdit={canEdit} copy={copy} />}
          </div>
        </aside>
        )}

        {!sideOpen && draft && (
          <Button
            size="sm" variant="primary"
            className="absolute bottom-4 right-4 z-20 shadow-lg"
            leadingIcon={<Settings className="h-4 w-4" />}
            onClick={toggleSide}
          >
            {copy.config}
          </Button>
        )}
      </div>

      <AiDraftDialog
        open={aiOpen} onClose={() => { setAiOpen(false); setAiDraft(null); }}
        inputs={query.data?.inputs ?? []} copy={copy.ai}
        drafting={aiDrafting.isPending} draft={aiDraft} error={aiError}
        onDraft={(assetId, intent) => aiDrafting.mutate({ assetId, intent })}
        onAccept={(drafted) => acceptDraft.mutate(drafted)}
        accepting={acceptDraft.isPending}
      />

      <Modal open={newModelOpen} onClose={() => setNewModelOpen(false)} title={copy.newModel} size="sm" footer={<><Button size="sm" variant="ghost" onClick={() => setNewModelOpen(false)}>{copy.cancel}</Button><Button size="sm" variant="primary" loading={createModel.isPending} disabled={!/^[A-Za-z_][A-Za-z0-9_]*$/.test(newModel.name)} onClick={() => createModel.mutate()}>{copy.create}</Button></>}>
                <div className="space-y-3">
          <div>
            <Label required>{copy.modelName}</Label>
            <Input autoFocus value={newModel.name} placeholder="stg_deal"
              onChange={(event) => setNewModel({ ...newModel, name: event.target.value })} />
          </div>
          <div>
            <Label>{copy.startFrom}</Label>
            {/* Picking a shape also sets the layer and materialisation that
                shape implies, so the two selects below usually need no thought. */}
            <div className="space-y-1.5">
              {MODEL_TEMPLATES.map((template) => (
                <button key={template.id} type="button"
                  onClick={() => { setNewTemplate(template.id);
                    setNewModel((current) => ({ ...current, layer: template.layer,
                      materialization: template.materialization })); }}
                  className={cn(
                    "w-full rounded-md border px-2.5 py-2 text-left transition-colors",
                    newTemplate === template.id
                      ? "border-brand bg-brand/[0.06]"
                      : "border-[rgb(var(--border-line))] hover:border-[rgb(var(--border-strong))]",
                  )}>
                  <span className="block text-caption font-emphasis text-text-primary">
                    {copy.templateName[template.id]}
                  </span>
                  <span className="block text-tiny text-text-tertiary">
                    {copy.templateHint[template.id]}
                  </span>
                </button>
              ))}
            </div>
          </div>
          <details className="rounded-md border border-[rgb(var(--border-line))] px-2.5 py-2">
            <summary className="cursor-pointer text-caption text-text-secondary">
              {copy.advanced}
            </summary>
            <div className="mt-2 space-y-2.5">
              <div>
                <Label>{copy.layer}</Label>
                <Select value={newModel.layer}
                  onChange={(event) => setNewModel({ ...newModel, layer: event.target.value })}>
                  <option value="STAGING">{copy.layerLabel.STAGING}</option>
                  <option value="CORE">{copy.layerLabel.CORE}</option>
                  <option value="MART">{copy.layerLabel.MART}</option>
                </Select>
              </div>
              <div>
                <Label>{copy.materialization}</Label>
                <Select value={newModel.materialization}
                  onChange={(event) => setNewModel({ ...newModel, materialization: event.target.value })}>
                  <option value="VIEW">View</option>
                  <option value="TABLE">Table</option>
                  <option value="INCREMENTAL">Incremental</option>
                </Select>
              </div>
            </div>
          </details>
        </div>
      </Modal>

      <Modal open={Boolean(pendingModelId)} onClose={() => setPendingModelId(null)} title={copy.unsavedTitle} size="sm" footer={<><Button size="sm" variant="ghost" onClick={() => setPendingModelId(null)}>{copy.cancel}</Button><Button size="sm" variant="secondary" onClick={() => pendingModelId && finishSwitch(pendingModelId)}>{copy.discard}</Button><Button size="sm" variant="primary" loading={save.isPending} onClick={async () => { await save.mutateAsync(); if (pendingModelId) finishSwitch(pendingModelId); }}>{copy.save}</Button></>}><p className="text-caption text-text-secondary">{copy.unsavedMessage}</p></Modal>

      <Modal
        open={Boolean(conflict)} onClose={() => setConflict(null)}
        title={copy.conflictTitle} description={copy.conflictMessage} size="xl"
        footer={<>
          <Button size="sm" variant="ghost" onClick={() => setConflict(null)}>{copy.cancel}</Button>
          <Button size="sm" variant="secondary" onClick={() => {
            if (conflict) { setDraft(structuredClone(conflict.server)); setDirty(false); }
            setConflict(null);
          }}>{copy.conflictTakeServer}</Button>
          <Button size="sm" variant="primary" loading={save.isPending} onClick={() => {
            // Keep the user's SQL, adopt the server's version so the retry is
            // accepted rather than rejected forever.
            if (conflict) setDraft((current) => current
              ? { ...current, version: conflict.server.version } : current);
            setConflict(null);
            requestAnimationFrame(() => save.mutate());
          }}>{copy.conflictKeepMine}</Button>
        </>}
      >
        {conflict && (
          <div className="grid gap-3 md:grid-cols-2">
            <div>
              <p className="mb-1 text-tiny font-emphasis uppercase text-text-quaternary">{copy.conflictMine}</p>
              <pre className="max-h-72 overflow-auto rounded-md bg-[#111827] p-3 font-mono text-tiny leading-5 text-[#e5e7eb]">{draft?.sql}</pre>
            </div>
            <div>
              <p className="mb-1 text-tiny font-emphasis uppercase text-text-quaternary">{copy.conflictTheirs}</p>
              <pre className="max-h-72 overflow-auto rounded-md bg-[#111827] p-3 font-mono text-tiny leading-5 text-[#e5e7eb]">{conflict.server.sql}</pre>
            </div>
          </div>
        )}
      </Modal>

      <ConfirmDialog
        open={Boolean(pendingDelete)} onClose={() => setPendingDelete(null)}
        onConfirm={() => { if (pendingDelete) removeModel.mutate(pendingDelete.id); setPendingDelete(null); }}
        title={copy.deleteModelTitle}
        confirmLabel={copy.deleteModel} cancelLabel={copy.cancel}
        destructive loading={removeModel.isPending}
        message={<>
          <p>{copy.deleteModelMessage.replace('{name}', pendingDelete?.name ?? '')}</p>
          {pendingDelete && dependents(pendingDelete.name).length > 0 && (
            <p className="mt-2 text-caption text-warning">
              {copy.deleteModelDependents}{' '}
              <span className="font-mono">{dependents(pendingDelete.name).join(', ')}</span>
            </p>
          )}
        </>}
      />

      <ReleaseHistoryModal
        open={historyOpen} onClose={() => setHistoryOpen(false)}
        releases={releases.data} loading={releases.isLoading} copy={copy}
        canEdit={canEdit} restoring={restore.isPending}
        onRestore={(id) => restore.mutate(id)}
        inspecting={(releases.data ?? []).find((item) => item.id === inspectId) ?? null}
        onInspect={setInspectId}
        models={releaseModels.data?.models} modelsLoading={releaseModels.isLoading}
        onRestoreDraft={(id) => restoreDraft.mutate(id)}
        restoringDraft={restoreDraft.isPending}
      />


      <GeneratedProjectModal open={generatedOpen} onClose={() => setGeneratedOpen(false)} files={generatedProject.data} loading={generatedProject.isLoading} title={locale === 'vi' ? 'Project dbt được sinh' : 'Generated dbt project'} />
      <SettingsModal open={settingsOpen} onClose={() => setSettingsOpen(false)} transform={transform} copy={copy} canEdit={canEdit} />
    </div>
  );
}

function RailSection({ title, action, children }: { title: string; action?: React.ReactNode; children: React.ReactNode }) {
  return <section className="border-b border-[rgb(var(--border-line))] p-2"><div className="mb-1 flex h-7 items-center justify-between px-1"><h2 className="text-[10px] font-emphasis uppercase text-text-quaternary">{title}</h2>{action}</div>{children}</section>;
}

function OutputPanel({ ratio, tab, setTab, execution, loading, runId, modelName, copy, onCancel, cancelling, onJump, knownModels, onFixRef, canEdit, lineage, lineageLoading, lineageExpanded, onExpandLineage }: { ratio: number; tab: string; setTab: (tab: string) => void; execution?: import('@/lib/types').TransformExecution; loading: boolean; runId: string | null; modelName: string; copy: typeof en; onCancel?: () => void; cancelling?: boolean; onJump?: (line: number) => void; knownModels?: string[]; onFixRef?: (from: string, to: string) => void; canEdit?: boolean;
  lineage?: import('@/lib/types').TransformLineage; lineageLoading?: boolean;
  lineageExpanded?: boolean; onExpandLineage?: () => void }) {
  const compiled = execution ? Object.entries(execution.compiled_sql).find(([key]) => key.endsWith(`.${modelName}`))?.[1] : undefined;
  const nodes = execution?.nodes ?? [];
  const models = nodes.filter((node) => node.resource_type === 'MODEL');
  const active = Boolean(execution && ACTIVE.includes(execution.status));
  return <div className="flex min-h-[120px] flex-col overflow-hidden border-t border-[rgb(var(--border-line))] bg-surface-1"
    style={{ flex: `${ratio} 1 0%` }}>
    <div className="flex h-9 items-center border-b border-[rgb(var(--border-line))] px-2">
      <Tabs className="border-0" value={tab} onChange={setTab} items={[
        { id: 'preview', label: copy.preview },
        { id: 'nodes', label: copy.results, count: nodes.length || undefined },
        { id: 'lineage', label: copy.lineage },
        { id: 'compiled', label: copy.compiledSql },
        ...(canEdit ? [{ id: 'logs', label: copy.logs }] : []),
      ]} />
      <div className="ml-auto flex items-center gap-2">
        {loading && <Loader2 className="h-3.5 w-3.5 animate-spin text-brand" />}
        {execution && <Badge size="xs" variant={execution.status === 'SUCCEEDED' ? 'success' : execution.status === 'FAILED' ? 'danger' : 'info'}>{execution.status}</Badge>}
        {/* Without this the only way to stop a long build is to leave the page. */}
        {active && onCancel && <Button size="xs" variant="ghost" loading={cancelling} onClick={onCancel}>{copy.cancelRun}</Button>}
        {runId && <Link className="text-tiny text-brand hover:underline" href={`/runs/${runId}`}>{copy.viewRun}</Link>}
      </div>
    </div>
    <div className="min-h-0 flex-1 overflow-auto p-3">
      {execution?.error && <RunErrorCallout error={execution.error} copy={copy} onJump={onJump} knownModels={knownModels} onFixRef={onFixRef} />}
      {tab === 'preview' && <PreviewTable preview={execution?.preview} empty={copy.noPreview} copy={copy} />}
      {tab === 'nodes' && <NodeResults nodes={nodes} models={models} copy={copy} />}
      {tab === 'lineage' && (
        <LineageView
          data={lineage} loading={Boolean(lineageLoading)} copy={copy}
          selectedName={modelName}
          expanded={Boolean(lineageExpanded)}
          onToggleExpand={onExpandLineage ?? (() => {})}
        />
      )}
      {tab === 'compiled' && <pre className="whitespace-pre-wrap font-mono text-tiny leading-5 text-text-secondary">{compiled ?? copy.noCompiled}</pre>}
      {tab === 'logs' && (runId ? <LogViewer runId={runId} live={active} /> : <p className="text-caption text-text-quaternary">{copy.noLogs}</p>)}
    </div>
  </div>;
}

/** What dbt did, node by node — the equivalent of its "1 of 6 OK" output. */
function NodeResults({ nodes, models, copy }: {
  nodes: import('@/lib/types').TransformRunNode[];
  models: import('@/lib/types').TransformRunNode[];
  copy: typeof en;
}) {
  if (!nodes.length) return <p className="text-caption text-text-quaternary">{copy.noResults}</p>;
  const tone = (status: string) => status === 'SUCCESS' || status === 'PASS' ? 'text-success'
    : status === 'WARN' ? 'text-warning'
      : status === 'SKIPPED' ? 'text-text-quaternary' : 'text-danger';
  return <div>
    <p className="mb-2 text-tiny text-text-tertiary">
      {models.length} {copy.modelsRan} · {nodes.length - models.length} {copy.testsRan}
    </p>
    {/* `w-auto`, not `min-w-full`: a full-width table spreads four short
        columns across 1200px, so the eye has to travel the whole row to pair a
        status with its model. */}
    <table className="w-auto text-left text-tiny">
      <tbody>
        {nodes.map((node) => (
          <tr key={`${node.resource_type}-${node.name}`} className="border-b border-[rgb(var(--border-line))] last:border-0">
            <td className="py-1 pr-4 align-top">
              <span className={cn('font-emphasis', tone(node.status))}>{node.status}</span>
            </td>
            <td className="py-1 pr-4 align-top font-mono text-text-secondary">{node.name}</td>
            <td className="py-1 pr-4 align-top text-text-quaternary">{node.resource_type}</td>
            <td className="py-1 pr-4 align-top text-right tabular-nums text-text-quaternary">
              {node.execution_time != null ? `${node.execution_time.toFixed(1)}s` : ''}
            </td>
            <td className="py-1 align-top text-text-tertiary">{node.message ?? ''}</td>
          </tr>
        ))}
      </tbody>
    </table>
  </div>;
}

/**
 * A dbt failure arrives as a wall of text. What a person needs first is which
 * model broke and where; the traceback stays available but folded away.
 */
function RunErrorCallout({ error, copy, onJump, knownModels, onFixRef }: { error: import('@/lib/types').RunError; copy: typeof en; onJump?: (line: number) => void; knownModels?: string[]; onFixRef?: (from: string, to: string) => void }) {
  const [showTechnical, setShowTechnical] = React.useState(false);
  const location = error.location;
  const where = [
    location?.name,
    location?.line ? `${copy.line} ${location.line}` : null,
  ].filter(Boolean).join(' · ');
  const remediation = error.remediation_action
    ? copy.remediation[error.remediation_action as keyof typeof copy.remediation]
    : undefined;

  // A missing ref is the most common authoring slip, and the fix is usually a
  // model that already exists under a slightly different name. Naming the
  // closest matches turns a dead end into one click.
  const missingRef = (error.location as { missing_ref?: string } | null)?.missing_ref;
  const suggestions = React.useMemo(() => {
    if (!missingRef || !knownModels?.length) return [] as string[];
    const target = missingRef.toLowerCase();
    return knownModels
      .map((name) => {
        const other = name.toLowerCase();
        if (other === target) return { name, score: 100 };
        if (other.includes(target) || target.includes(other)) return { name, score: 60 };
        const parts = target.split(/[^a-z0-9]+/).filter((part) => part.length > 2);
        const shared = parts.filter((part) => other.includes(part)).length;
        return { name, score: shared * 20 };
      })
      .filter((item) => item.score > 0)
      .sort((a, b) => b.score - a.score)
      .slice(0, 3)
      .map((item) => item.name);
  }, [missingRef, knownModels]);
  return (
    <div className="mb-2 rounded-md border border-danger/25 bg-danger/[0.04] p-2.5">
      {where && (
        <p className="mb-1 flex items-center gap-1.5 font-mono text-tiny text-danger">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
          {/* A line number you have to scroll to yourself is only half an
              answer; this puts the caret on it. */}
          {location?.line && onJump
            ? <button type="button" onClick={() => onJump(location.line!)}
                className="underline decoration-dotted underline-offset-2 hover:no-underline">{where}</button>
            : where}
        </p>
      )}
      <p className="whitespace-pre-wrap text-caption text-danger">{error.summary}</p>
      {remediation && (
        <p className="mt-1.5 text-tiny text-text-secondary">{remediation}</p>
      )}
      {missingRef && suggestions.length > 0 && (
        <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
          <span className="text-tiny text-text-tertiary">{copy.didYouMean}</span>
          {suggestions.map((name) => (
            <button key={name} type="button"
              onClick={() => onFixRef?.(missingRef, name)}
              className="rounded border border-[rgb(var(--border-strong))] bg-surface-1 px-1.5 py-0.5 font-mono text-tiny text-brand hover:border-brand">
              {name}
            </button>
          ))}
        </div>
      )}
      {error.technical_message && (
        <>
          <button type="button" onClick={() => setShowTechnical((value) => !value)}
            className="mt-1.5 text-tiny text-text-tertiary underline-offset-2 hover:underline">
            {showTechnical ? copy.hideTechnical : copy.showTechnical}
          </button>
          {showTechnical && (
            <pre className="mt-1.5 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-black/25 p-2 font-mono text-[11px] leading-4 text-text-secondary">
              {error.technical_message}
            </pre>
          )}
        </>
      )}
    </div>
  );
}

function PreviewTable({ preview, empty, copy }: { preview?: Record<string, unknown> | null; empty: string; copy: typeof en }) {
  if (!preview) return <p className="text-caption text-text-quaternary">{empty}</p>;
  const candidate = (preview.data ?? preview.rows ?? preview.show) as unknown;
  const rows = Array.isArray(candidate) ? candidate : [];
  if (!rows.length || typeof rows[0] !== 'object') return <pre className="font-mono text-tiny text-text-secondary">{JSON.stringify(preview, null, 2)}</pre>;
  const columns = Object.keys(rows[0] as Record<string, unknown>);

  // Anything a person can read on screen they will eventually want in a
  // spreadsheet; the rows are already here, so the download costs one function.
  const download = () => {
    const escape = (value: unknown) => {
      const text = String(value ?? '');
      const risky = text.includes(",") || text.includes(String.fromCharCode(34))
        || text.includes(String.fromCharCode(10));
      return risky
        ? String.fromCharCode(34)
          + text.replaceAll(String.fromCharCode(34), String.fromCharCode(34, 34))
          + String.fromCharCode(34)
        : text;
    };
    const csv = [
      columns.join(','),
      ...rows.map((row) => columns
        .map((column) => escape((row as Record<string, unknown>)[column])).join(',')),
    ].join(String.fromCharCode(10));
    const url = URL.createObjectURL(new Blob([`${String.fromCharCode(65279)}${csv}`],
      { type: 'text/csv;charset=utf-8' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = 'preview.csv';
    link.click();
    URL.revokeObjectURL(url);
  };

  return <div>
    <div className="mb-1.5 flex items-center gap-2">
      <span className="text-tiny text-text-tertiary">
        {rows.length} {copy.rowsCounted}
      </span>
      <Button size="xs" variant="ghost" className="ml-auto"
        leadingIcon={<Download className="h-3.5 w-3.5" />} onClick={download}>
        {copy.exportCsv}
      </Button>
    </div>
    <div className="overflow-x-auto"><table className="min-w-full text-left text-tiny"><thead><tr className="border-b border-[rgb(var(--border-line))]">{columns.map((column) => <th key={column} className="px-2 py-1 font-emphasis text-text-tertiary">{column}</th>)}</tr></thead><tbody>{rows.map((row, index) => <tr key={index} className="border-b border-[rgb(var(--border-line))]">{columns.map((column) => <td key={column} className="whitespace-nowrap px-2 py-1 font-mono text-text-secondary">{String((row as Record<string, unknown>)[column] ?? '')}</td>)}</tr>)}</tbody></table></div>
  </div>;
}

/** Each dbt adapter implements its own incremental strategies; offering one the
 *  warehouse does not implement only fails later, on a production build. */
const STRATEGIES: Record<string, string[]> = {
  'dbt-bigquery': ['merge', 'insert_overwrite'],
  'dbt-postgres': ['append', 'merge', 'delete+insert'],
  'dbt-sqlserver': ['append', 'merge', 'delete+insert'],
};

function ConfigPanel({ draft, patchDraft, copy, canEdit, onDelete, deleting, adapter }: { draft: Draft; patchDraft: (changes: Partial<Draft>) => void; copy: typeof en; canEdit: boolean; onDelete: () => void; deleting: boolean; adapter: string }) {
  const strategies = STRATEGIES[adapter] ?? ['merge', 'append'];
  return (
    <div className="space-y-4">
      <Field label={copy.materialization} hint={copy.materializationHint[draft.materialization]}>
        <Select disabled={!canEdit} value={draft.materialization}
          onChange={(event) => patchDraft({ materialization: event.target.value as Draft['materialization'] })}>
          <option value="VIEW">View</option>
          <option value="TABLE">Table</option>
          <option value="INCREMENTAL">Incremental</option>
        </Select>
      </Field>

      {draft.materialization === 'INCREMENTAL' && (
        <>
          <Field label={copy.uniqueKey} hint={copy.uniqueKeyHint}>
            <Input disabled={!canEdit} placeholder="id"
              value={String(draft.config.unique_key ?? '')}
              onChange={(event) => patchDraft({ config: { ...draft.config, unique_key: event.target.value } })} />
          </Field>
          <Field label={copy.strategy} hint={copy.strategyHint}>
            <Select disabled={!canEdit}
              value={String(draft.config.incremental_strategy ?? strategies[0] ?? 'merge')}
              onChange={(event) => patchDraft({ config: { ...draft.config, incremental_strategy: event.target.value } })}>
              {strategies.map((option) => <option key={option} value={option}>{option}</option>)}
            </Select>
          </Field>
          {draft.config.incremental_strategy === 'merge' && !draft.config.unique_key && (
            <p className="-mt-2 text-tiny text-warning">{copy.mergeNeedsKey}</p>
          )}
        </>
      )}

      <Field label={copy.layer} hint={copy.layerHint}>
        <Select disabled={!canEdit} value={draft.layer}
          onChange={(event) => patchDraft({ layer: event.target.value as Draft['layer'] })}>
          <option value="STAGING">{copy.layerLabel.STAGING}</option>
          <option value="CORE">{copy.layerLabel.CORE}</option>
          <option value="MART">{copy.layerLabel.MART}</option>
        </Select>
      </Field>

      <Field label={copy.description} hint={copy.descriptionHint}>
        <Textarea disabled={!canEdit} rows={3} value={draft.description ?? ''}
          onChange={(event) => patchDraft({ description: event.target.value || null })} />
      </Field>

      {/* Naming is a deliberate choice, not a first-run decision: both fields
          already have working defaults, so they only get in the way up front. */}
      <details className="rounded-md border border-[rgb(var(--border-line))] px-2.5 py-2">
        <summary className="cursor-pointer text-caption text-text-secondary">{copy.advanced}</summary>
        <div className="mt-3 space-y-4">
          <Field label={copy.relationName} hint={copy.relationNameHint}>
            <Input disabled={!canEdit} value={draft.relation_name ?? ''} placeholder={draft.name}
              onChange={(event) => patchDraft({ relation_name: event.target.value || null })} />
          </Field>
          <Field label={copy.outputSchema} hint={copy.outputSchemaHint}>
            <Input disabled={!canEdit} value={draft.output_schema ?? ''} placeholder={copy.defaultOutput}
              onChange={(event) => patchDraft({ output_schema: event.target.value || null })} />
          </Field>
        </div>
      </details>

      {canEdit && (
        // Full width and outlined in red at the foot of the panel: deleting a
        // model destroys its SQL and every test on it, so it should not look
        // like the ghost buttons around it.
        <button type="button" onClick={onDelete} disabled={deleting}
          className="mt-2 flex w-full items-center justify-center gap-1.5 rounded-md border border-danger/40 px-3 py-1.5 text-caption text-danger transition-colors hover:bg-danger/[0.06] disabled:opacity-50">
          <Trash2 className="h-3.5 w-3.5" />
          {copy.deleteModel}
        </button>
      )}
    </div>
  );
}

/** A labelled control with the one line of explanation the label cannot carry. */
function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div>
      <Label>{label}</Label>
      {children}
      {hint && <p className="mt-1 text-tiny leading-4 text-text-quaternary">{hint}</p>}
    </div>
  );
}
function TestsPanel({ draft, form, setForm, add, adding, remove, canEdit, copy }: { draft: Draft; form: TestForm; setForm: React.Dispatch<React.SetStateAction<TestForm>>; add: () => void; adding: boolean; remove: (id: string) => void; canEdit: boolean; copy: typeof en }) {
  const relationshipIncomplete = form.rule === 'RELATIONSHIPS'
    && (!form.target_model || !form.target_field);
  return <div>
    <div className="space-y-2">{draft.tests.map((test) => (
      <div key={test.id} className="rounded-md border border-[rgb(var(--border-line))] p-2">
        <div className="flex items-start gap-2">
          <TestTube2 className="mt-0.5 h-3.5 w-3.5 text-text-tertiary" />
          <div className="min-w-0 flex-1">
            {/* Column on top, rule beneath, outcome as a badge -- the shape a
                reader scans, rather than three values joined by slashes. */}
            <p className="truncate text-caption font-emphasis text-text-primary">
              {test.column_name ?? copy.model}
            </p>
            <p className="mt-0.5 text-tiny uppercase tracking-wide text-text-quaternary">
              {test.rule.replaceAll('_', ' ')}
            </p>
            <div className="mt-1 flex items-center gap-1.5">
              <Badge size="xs"
                variant={test.last_status === 'PASSED' ? 'success'
                  : test.last_status === 'FAILED' ? 'danger' : 'neutral'}>
                {test.last_status ?? copy.notRunYet}
              </Badge>
              {test.severity !== 'ERROR' && (
                <span className="text-tiny text-text-quaternary">{test.severity}</span>
              )}
            </div>
          </div>
          {canEdit && <IconButton size="xs" variant="ghost" aria-label={copy.removeTest}
            onClick={() => remove(test.id)}><Trash2 className="h-3 w-3" /></IconButton>}
        </div>
      </div>
    ))}</div>
    {canEdit && <div className="mt-4 space-y-2 border-t border-[rgb(var(--border-line))] pt-3">
      <div><Label>{copy.column}</Label><Input size="sm" value={form.column_name}
        onChange={(event) => setForm({ ...form, column_name: event.target.value })} /></div>
      <div><Label>{copy.rule}</Label><Select size="sm" value={form.rule}
        onChange={(event) => setForm({ ...form, rule: event.target.value })}>
        <option value="NOT_NULL">Not null</option>
        <option value="UNIQUE">Unique</option>
        <option value="ACCEPTED_VALUES">Accepted values</option>
        <option value="RELATIONSHIPS">Relationships</option>
      </Select></div>
      {form.rule === 'ACCEPTED_VALUES' && <div><Label>{copy.values}</Label><Input
        size="sm" value={form.values}
        onChange={(event) => setForm({ ...form, values: event.target.value })}
        placeholder="OPEN, WON, LOST"
      /></div>}
      {form.rule === 'RELATIONSHIPS' && <>
        <div><Label>Target model</Label><Input size="sm" value={form.target_model}
          onChange={(event) => setForm({ ...form, target_model: event.target.value })}
          placeholder="dim_customer" /></div>
        <div><Label>Target field</Label><Input size="sm" value={form.target_field}
          onChange={(event) => setForm({ ...form, target_field: event.target.value })}
          placeholder="customer_id" /></div>
      </>}
      <div><Label>{copy.severity}</Label><Select size="sm" value={form.severity}
        onChange={(event) => setForm({ ...form, severity: event.target.value })}>
        <option value="ERROR">Error</option><option value="WARN">Warning</option>
      </Select></div>
      <Button fullWidth size="sm" variant="secondary" loading={adding}
        disabled={!form.column_name || (form.rule === 'ACCEPTED_VALUES' && !form.values) || relationshipIncomplete}
        onClick={add} leadingIcon={<Plus className="h-3.5 w-3.5" />}>
        {copy.addTest}
      </Button>
    </div>}
  </div>;
}

/**
 * A drawn dependency graph rather than a printed adjacency list.
 *
 * Nodes are placed in columns by how far downstream they are, which is the
 * layout Dataform and dbt docs both use, and edges are real curves — with 20
 * models a list of "a -> b" lines is something a person has to assemble in
 * their head before it means anything.
 */
function GeneratedProjectModal({ open, onClose, files, loading, title }: { open: boolean; onClose: () => void; files?: Record<string, string>; loading: boolean; title: string }) {
  const paths = React.useMemo(() => Object.keys(files ?? {}).sort(), [files]);
  const [selected, setSelected] = React.useState('dbt_project.yml');
  React.useEffect(() => {
    if (paths.length && !paths.includes(selected)) setSelected(paths[0]);
  }, [paths, selected]);
  return <Modal open={open} onClose={onClose} title={title} size="xl">
    {loading ? <Spinner /> : <div className="grid min-h-[460px] grid-cols-[210px_minmax(0,1fr)] overflow-hidden rounded-md border border-[rgb(var(--border-line))]">
      <nav className="overflow-y-auto border-r border-[rgb(var(--border-line))] bg-surface-2 p-1">
        {paths.map((path) => <button key={path} type="button" onClick={() => setSelected(path)}
          className={cn('block h-8 w-full truncate px-2 text-left font-mono text-tiny', selected === path ? 'bg-brand/[0.08] text-brand' : 'text-text-secondary hover:bg-surface-3')}
          title={path}>{path}</button>)}
      </nav>
      <pre className="overflow-auto bg-[#111827] p-4 font-mono text-tiny leading-5 text-[#e5e7eb]">{files?.[selected] ?? ''}</pre>
    </div>}
  </Modal>;
}

function SettingsModal({ open, onClose, transform, copy, canEdit }: {
  open: boolean; onClose: () => void;
  transform: import('@/lib/types').TransformDetail; copy: typeof en; canEdit: boolean;
}) {
  const workspaceId = useWorkspaceId();
  const queryClient = useQueryClient();
  const [schema, setSchema] = React.useState(transform.default_schema);
  const [trigger, setTrigger] = React.useState(transform.execution_trigger);
  const [inputIds, setInputIds] = React.useState<string[]>(
    () => transform.inputs.map((item) => item.id),
  );
  const [intervalSeconds, setIntervalSeconds] = React.useState(
    () => Number(transform.schedule?.interval_seconds ?? 86400),
  );
  const [zone, setZone] = React.useState(
    () => transform.schedule?.timezone ?? 'Asia/Bangkok',
  );
  const [scheduleKind, setScheduleKind] = React.useState<'INTERVAL' | 'DAILY' | 'CRON'>(
    () => {
      const type = transform.schedule?.type;
      return type === 'DAILY' || type === 'CRON' ? type : 'INTERVAL';
    },
  );
  const [timeOfDay, setTimeOfDay] = React.useState(
    () => transform.schedule?.time_of_day ?? '02:00',
  );
  const [cron, setCron] = React.useState(
    () => transform.schedule?.cron_expression ?? '0 2 * * *',
  );
  const [exporting, setExporting] = React.useState(false);

  // Re-seed whenever the drawer is reopened so it never shows a stale draft
  // from a previous session.
  React.useEffect(() => {
    if (!open) return;
    setSchema(transform.default_schema);
    setTrigger(transform.execution_trigger);
    setInputIds(transform.inputs.map((item) => item.id));
  }, [open, transform]);

  const candidates = useQuery({
    queryKey: qk.transformInputs(workspaceId, transform.destination.id),
    queryFn: () => transformApi.inputCandidates(transform.destination.id),
    enabled: open,
  });

  const schemaValid = /^[A-Za-z_][A-Za-z0-9_]*$/.test(schema);
  const changed = schema !== transform.default_schema
    || trigger !== transform.execution_trigger
    || inputIds.length !== transform.inputs.length
    || inputIds.some((id) => !transform.inputs.some((item) => item.id === id));

  const save = useMutation({
    mutationFn: () => transformApi.update(transform.id, {
      default_schema: schema, execution_trigger: trigger,
      schedule: trigger === 'SCHEDULE'
        ? {
          type: scheduleKind, timezone: zone,
          ...(scheduleKind === 'INTERVAL' ? { interval_seconds: intervalSeconds } : {}),
          ...(scheduleKind === 'DAILY' ? { time_of_day: timeOfDay } : {}),
          ...(scheduleKind === 'CRON' ? { cron_expression: cron } : {}),
        }
        : undefined,
      input_asset_ids: inputIds, version: transform.version,
    }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: qk.transform(workspaceId, transform.id) });
      await queryClient.invalidateQueries({ queryKey: qk.transforms(workspaceId) });
      toastSuccess(copy.settingsSaved);
      onClose();
    },
    onError: (error) => toastError(error),
  });

  const saveGit = useMutation({
    mutationFn: (body: Parameters<typeof transformApi.configureGit>[1]) =>
      transformApi.configureGit(transform.id, body),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: qk.transform(workspaceId, transform.id) });
      toastSuccess(copy.git.saved);
    },
    onError: (error) => toastError(error),
  });
  const syncGit = useMutation({
    mutationFn: (force: boolean) => transformApi.syncGit(transform.id, force),
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({ queryKey: qk.transform(workspaceId, transform.id) });
      if (result.status === 'FAILED') toastError(new Error(result.message));
      else toastSuccess(result.message);
    },
    onError: (error) => toastError(error),
  });

  // A plain <a download> would navigate the SPA to a JSON error envelope when
  // the export fails, taking any unsaved model edits with it.
  const exportProject = async () => {
    setExporting(true);
    try {
      const response = await fetch(transformApi.exportUrl(transform.id), {
        credentials: 'include',
      });
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = `${transform.name.replace(/[^A-Za-z0-9_-]/g, '_')}.zip`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    } catch (error) {
      toastError(error);
    } finally {
      setExporting(false);
    }
  };

  return <Modal
    open={open} onClose={onClose} title={copy.settings} size="lg"
    footer={canEdit ? <>
      <Button size="sm" variant="ghost" onClick={onClose}>{copy.cancel}</Button>
      <Button size="sm" variant="primary" loading={save.isPending}
        disabled={!changed || !schemaValid || inputIds.length === 0}
        onClick={() => save.mutate()}>{copy.save}</Button>
    </> : undefined}
  >
    <div className="space-y-4">
      <dl className="grid grid-cols-[150px_1fr] gap-x-4 gap-y-2 text-caption">
        <dt className="text-text-tertiary">{copy.warehouse}</dt>
        <dd className="text-text-primary">{transform.destination.name}</dd>
        <dt className="text-text-tertiary">Runtime</dt>
        <dd className="text-text-primary">dbt Core {transform.dbt_core_version}
          <span className="ml-2 text-tiny text-text-quaternary">
            {transform.dbt_adapter_name} {transform.dbt_adapter_version}
          </span>
        </dd>
      </dl>

      <div>
        <Label required>{copy.defaultOutput}</Label>
        <Input disabled={!canEdit} value={schema} invalid={Boolean(schema) && !schemaValid}
          onChange={(event) => setSchema(event.target.value)} />
        <p className="mt-1 text-tiny text-text-quaternary">{copy.outputSchemaHelp}</p>
      </div>

      <div>
        <Label>{copy.executionTrigger}</Label>
        <Select disabled={!canEdit} value={trigger}
          onChange={(event) => setTrigger(event.target.value as typeof trigger)}>
          <option value="MANUAL">{copy.triggerManual}</option>
          <option value="AFTER_UPSTREAM">{copy.triggerUpstream}</option>
          <option value="SCHEDULE">{copy.triggerSchedule}</option>
        </Select>
        <p className="mt-1 text-tiny text-text-quaternary">
          {trigger === 'AFTER_UPSTREAM' ? copy.triggerUpstreamHelp
            : trigger === 'SCHEDULE' ? copy.triggerScheduleHelp
              : copy.triggerManualHelp}
        </p>
      </div>

      {trigger === 'SCHEDULE' && (
        <div className="rounded-md border border-[rgb(var(--border-line))] p-2.5">
          {/* Naming the subject matters: an unattended run executes the
              published version, not whatever the editor holds. */}
          <p className="mb-2 text-tiny text-text-tertiary">
            {transform.active_release
              ? `${copy.scheduleRuns} ${copy.liveVersion.replace('{n}', String(transform.active_release.release_number))}.`
              : copy.nothingPublishedHint}
          </p>
          <div className="grid gap-2 sm:grid-cols-2">
            <div>
              <Label>{copy.scheduleKind}</Label>
              <Select disabled={!canEdit} value={scheduleKind}
                onChange={(event) => setScheduleKind(event.target.value as typeof scheduleKind)}>
                <option value="INTERVAL">{copy.kindInterval}</option>
                <option value="DAILY">{copy.kindDaily}</option>
                <option value="CRON">{copy.kindCron}</option>
              </Select>
            </div>
            {scheduleKind === 'INTERVAL' && (
              <div>
                <Label>{copy.scheduleEvery}</Label>
                <Select disabled={!canEdit} value={String(intervalSeconds)}
                  onChange={(event) => setIntervalSeconds(Number(event.target.value))}>
                  <option value="1800">{copy.every30m}</option>
                  <option value="3600">{copy.every1h}</option>
                  <option value="21600">{copy.every6h}</option>
                  <option value="86400">{copy.everyDay}</option>
                </Select>
              </div>
            )}
            {scheduleKind === 'DAILY' && (
              <div>
                <Label>{copy.atTime}</Label>
                <Input disabled={!canEdit} value={timeOfDay} placeholder="02:00"
                  onChange={(event) => setTimeOfDay(event.target.value)} />
              </div>
            )}
            {scheduleKind === 'CRON' && (
              <div>
                <Label>{copy.cronExpression}</Label>
                <Input disabled={!canEdit} value={cron} placeholder="0 2 * * *"
                  onChange={(event) => setCron(event.target.value)} />
              </div>
            )}
            <div>
              <Label>{copy.timezone}</Label>
              <Input disabled={!canEdit} value={zone}
                onChange={(event) => setZone(event.target.value)} />
            </div>
          </div>
          {transform.next_run_at && (
            <p className="mt-2 text-tiny text-text-quaternary">
              {copy.nextRun}: {new Date(transform.next_run_at).toLocaleString()}
            </p>
          )}
        </div>
      )}

      <div>
        <Label>{copy.inputs}</Label>
        {candidates.isLoading ? <Spinner /> : (
          <div className="max-h-56 overflow-y-auto rounded-md border border-[rgb(var(--border-line))]">
            {(candidates.data?.assets ?? []).map((asset) => (
              <label key={asset.id}
                className="flex cursor-pointer items-center gap-2 border-b border-[rgb(var(--border-line))] px-3 py-2 last:border-b-0 hover:bg-surface-2">
                <Checkbox
                  checked={inputIds.includes(asset.id)}
                  disabled={!canEdit}
                  aria-label={`${asset.schema_name}.${asset.relation_name}`}
                  onChange={(checked) => setInputIds((current) => checked
                    ? [...current, asset.id]
                    : current.filter((id) => id !== asset.id))}
                />
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-mono text-tiny text-text-primary">
                    {asset.schema_name}.{asset.relation_name}
                  </span>
                  <span className="block truncate text-[10px] text-text-quaternary">
                    {asset.pipeline_name ?? copy.warehouseRelation}
                  </span>
                </span>
              </label>
            ))}
          </div>
        )}
        {inputIds.length === 0 && <p className="mt-1 text-tiny text-danger">{copy.needInput}</p>}
      </div>

      <div className="border-t border-[rgb(var(--border-line))] pt-4">
        <h3 className="text-caption font-emphasis text-text-primary">{copy.git.title}</h3>
        <p className="mb-2 mt-0.5 text-tiny text-text-tertiary">{copy.git.description}</p>
        <GitSyncPanel
          git={transform.git ?? { connected: false }} copy={copy.git} canEdit={canEdit}
          saving={saveGit.isPending} syncing={syncGit.isPending}
          onSave={(body) => saveGit.mutate(body)}
          onSync={(force) => syncGit.mutate(force)} />
      </div>

      <div className="border-t border-[rgb(var(--border-line))] pt-4">
        <Button size="sm" variant="secondary" loading={exporting}
          leadingIcon={<Download className="h-4 w-4" />} onClick={exportProject}>
          {copy.exportProject}
        </Button>
        <div className="mt-3 flex items-center gap-2 text-tiny text-text-quaternary">
          <Kbd>Ctrl</Kbd><Kbd>S</Kbd><span>{copy.save}</span>
          <Kbd>Ctrl</Kbd><Kbd>Enter</Kbd><span>{copy.preview}</span>
        </div>
      </div>
    </div>
  </Modal>;
}

const REMEDIATION_VI = {
  RETRY_RUN: 'Chạy lại lần chạy này.',
  SPLIT_OR_RETRY: 'Lần chạy vượt quá thời gian cho phép. Thử tách nhỏ model hoặc chạy lại.',
  REVIEW_TEST_FAILURES: 'Xem các test không đạt ở tab Tests để biết dòng dữ liệu nào sai.',
  CHECK_DESTINATION_CREDENTIALS: 'Kiểm tra lại thông tin đăng nhập của Destination.',
  CHECK_DESTINATION_CONNECTIVITY: 'Kiểm tra kết nối mạng tới warehouse.',
  FIX_MODEL_SQL: 'Sửa SQL của model rồi Compile lại.',
  VIEW_LOGS: 'Mở tab Logs để xem chi tiết.',
};
const REMEDIATION_EN = {
  RETRY_RUN: 'Retry this run.',
  SPLIT_OR_RETRY: 'The run exceeded its time limit. Split the model or retry.',
  REVIEW_TEST_FAILURES: 'Open the Tests tab to see which rows failed.',
  CHECK_DESTINATION_CREDENTIALS: 'Check this Destination’s credentials.',
  CHECK_DESTINATION_CONNECTIVITY: 'Check network access to the warehouse.',
  FIX_MODEL_SQL: 'Fix the model SQL and compile again.',
  VIEW_LOGS: 'Open the Logs tab for details.',
};

const vi = {
  git: {
    title: 'Đồng bộ với GitHub',
    description: 'Khi repository có commit mới, các bảng ở đây được cập nhật theo.',
    saved: 'Đã lưu thiết lập đồng bộ',
    notConnected: 'Transform này chưa nối với repository nào',
    notConnectedHint: 'Chỉ những Transform được tạo bằng Import từ GitHub mới có mục này.',
    repository: 'Repository', branch: 'Nhánh', branchDefault: 'nhánh mặc định',
    lastSync: 'Lần đồng bộ gần nhất', never: 'Chưa đồng bộ lần nào',
    commit: 'commit', nextSync: 'Lần kiểm tra tới',
    syncNow: 'Đồng bộ ngay',
    reapply: 'Áp lại commit hiện tại',
    reapplyHint: 'Dùng khi bạn vừa cấp thêm quyền đọc bảng nguồn mà repository chưa có commit mới.',
    enable: 'Tự động theo dõi repository',
    enableHint: 'Hệ thống hỏi GitHub xem nhánh đã có commit mới chưa, chỉ tải về khi có.',
    every: 'Kiểm tra mỗi',
    autoPublish: 'Tự động xuất bản sau khi đồng bộ',
    autoPublishHint: 'Bật thì lịch chạy sẽ dùng ngay code mới. Tắt thì bạn xem lại rồi tự bấm Xuất bản.',
    token: 'GitHub access token',
    tokenStored: 'Chưa lưu token nào',
    tokenReplace: 'Đã lưu — nhập để thay token khác',
    tokenHint: 'Token được mã hoá khi lưu và không bao giờ hiển thị lại.',
    save: 'Lưu',
    disconnect: 'Ngắt kết nối',
    managed: 'Bảng do repository quản lý',
    managedHint: 'Chỉ những bảng này bị ghi đè hoặc gỡ khi đồng bộ. Bảng bạn tự viết luôn được giữ.',
    statusLabel: {
      APPLIED: 'Đã áp dụng thay đổi từ Git',
      UNCHANGED: 'Đang khớp với Git',
      FAILED: 'Đồng bộ thất bại',
    } as Record<string, string>,
    intervals: [
      { value: 5, label: '5 phút' }, { value: 15, label: '15 phút' },
      { value: 30, label: '30 phút' }, { value: 60, label: '1 giờ' },
      { value: 360, label: '6 giờ' }, { value: 1440, label: '1 ngày' },
    ],
  },
  aiButton: 'Nhờ AI',
  aiAccepted: 'Đã tạo bảng từ bản nháp của AI',
  ai: {
    title: 'Nhờ AI viết một bảng dữ liệu',
    description: 'Chọn bảng nguồn và mô tả bằng tiếng Việt. AI đọc dữ liệu thật rồi viết SQL, và kho dữ liệu sẽ chạy thử trước khi bạn nhận.',
    sourceTable: 'Bảng nguồn',
    intent: 'Bạn muốn bảng này cho ra dữ liệu gì?',
    intentPlaceholder: 'Ví dụ: làm sạch bảng đơn hàng, đổi tên cột cho dễ hiểu, bỏ dòng không có mã đơn.',
    intentHint: 'Càng nói rõ mục đích dùng để làm gì, kết quả càng sát. Không cần biết SQL.',
    examples: 'Gợi ý',
    draft: 'Viết thử',
    drafting: 'AI đang đọc dữ liệu và viết SQL',
    draftingHint: 'Bước này gồm đọc mẫu dữ liệu thật và nhờ kho dữ liệu chạy thử câu lệnh, nên mất khoảng 15-40 giây.',
    again: 'Viết lại',
    use: 'Dùng bản này',
    cancel: 'Hủy',
    back: 'Quay lại',
    summary: 'Tóm tắt',
    assumptions: 'AI đã phải phỏng đoán',
    assumptionsHint: 'Đây là những chỗ dữ liệu không tự nói rõ. Hãy đọc và xác nhận trước khi dùng.',
    proposedTests: 'Kiểm tra đề xuất',
    noTests: 'Không có kiểm tra nào phù hợp với dữ liệu này.',
    sql: 'SQL',
    validationOk: 'Kho dữ liệu đã chấp nhận câu lệnh này',
    validationRepaired: 'Có lỗi ở bản đầu, AI đã sửa và kho dữ liệu chấp nhận',
    validationFailed: 'Kho dữ liệu từ chối câu lệnh này',
    validationSkipped: 'Chưa chạy thử được trên kho dữ liệu này',
    validationOkHint: 'Đã chạy thử trên chính kho dữ liệu của bạn, không tốn chi phí và không ghi gì.',
    validationRepairedHint: 'Bản dưới đây là bản đã sửa; bản lỗi đã bị bỏ.',
    validationFailedHint: 'Bạn vẫn có thể nhận rồi tự sửa trong trình soạn thảo, hoặc bấm Viết lại.',
    validationSkippedHint: 'Loại kho dữ liệu này chưa hỗ trợ chạy thử. Hãy dùng Xem thử sau khi nhận.',
    confidence: { HIGH: 'Độ tin cậy cao', MEDIUM: 'Độ tin cậy vừa', LOW: 'Độ tin cậy thấp' },
    layerLabel: { STAGING: 'Làm sạch', CORE: 'Tổng hợp', MART: 'Phục vụ báo cáo' },
    ruleLabel: { NOT_NULL: 'Không được rỗng', UNIQUE: 'Không trùng', ACCEPTED_VALUES: 'Giá trị cho phép' },
    exampleIntents: [
      'Làm sạch bảng này: đổi tên cột cho dễ hiểu và sửa kiểu dữ liệu',
      'Đổi các cột thời gian sang dạng ngày giờ đọc được',
      'Bỏ các dòng thiếu mã định danh',
      'Đếm số dòng theo từng ngày',
    ],
  },
  loading: 'Đang tải Transform', loadError: 'Không tải được Transform', lineage: 'Sơ đồ phụ thuộc', settings: 'Cài đặt', save: 'Lưu', saved: 'Đã lưu', runTransform: 'Chạy Transform', inputs: 'Nguồn dữ liệu', models: 'Bảng dữ liệu', newModel: 'Bảng mới', noModel: 'Chưa có bảng dữ liệu nào', unsaved: 'Chưa lưu', visualLater: 'Visual mode sẽ được bổ sung sau khi SQL round-trip ổn định', compile: 'Kiểm tra cú pháp', preview: 'Xem thử', runModel: 'Ghi bảng này', config: 'Cấu hình', tests: 'Kiểm tra', cancel: 'Hủy', create: 'Tạo', modelName: 'Tên bảng', layer: 'Nhóm', materialization: 'Cách tạo dữ liệu', unsavedTitle: 'Bảng chưa được lưu', discard: 'Bỏ thay đổi', unsavedMessage: 'Lưu, bỏ thay đổi, hoặc hủy để quay lại bảng đang sửa.', compiledSql: 'SQL đã dịch', logs: 'Nhật ký', viewRun: 'Xem lần chạy', noPreview: 'Bấm Xem thử để xem dữ liệu.', noCompiled: 'Lưu model để xem SQL đã dịch.', noLogs: 'Chưa có nhật ký.', outputSchema: 'Schema đích', defaultOutput: 'Mặc định của Transform', relationName: 'Tên bảng', description: 'Mô tả', uniqueKey: 'Khóa duy nhất', strategy: 'Cách thêm dữ liệu mới', deleteModel: 'Xóa bảng', model: 'Bảng', removeTest: 'Xóa', column: 'Cột', rule: 'Quy tắc', values: 'Giá trị, cách nhau bằng dấu phẩy', severity: 'Mức độ', addTest: 'Thêm kiểm tra', lineageDescription: 'Sơ đồ cho thấy dữ liệu chảy từ nguồn nào tới bảng nào.', noLineage: 'Chạy Transform một lần để dựng sơ đồ.', warehouse: 'Kho dữ liệu', executionTrigger: 'Điều kiện chạy', exportProject: 'Tải project dbt về',
  validate: 'Kiểm tra kết nối', insertReference: 'Chèn tham chiếu',
  stale: 'Dữ liệu nguồn cũ hơn lần chạy gần nhất', unresolved: 'Chưa xác minh được relation',
  warehouseRelation: 'Relation trong warehouse',
  line: 'dòng', showTechnical: 'Xem chi tiết kỹ thuật', hideTechnical: 'Ẩn chi tiết kỹ thuật',
  remediation: REMEDIATION_VI,
  settingsSaved: 'Đã lưu cài đặt Transform',
  outputSchemaHelp: 'Schema mặc định nơi các model được tạo ra.',
  triggerManual: 'Chạy thủ công', triggerUpstream: 'Chạy sau khi dữ liệu nguồn sẵn sàng',
  triggerManualHelp: 'Chỉ chạy khi bạn bấm Chạy Transform.',
  triggerUpstreamHelp: 'Chỉ chạy khi tất cả input bắt buộc đã được nạp mới.',
  needInput: 'Cần ít nhất một input.',
  waitingUpstream: 'Đang chờ dữ liệu nguồn',
  testModel: 'Chạy kiểm tra', moreRunOptions: 'Tùy chọn chạy khác',
  runUpstream: 'Chạy cả model nguồn phía trên', fullRefreshModel: 'Dựng lại model từ đầu',
  fullRefreshAll: 'Dựng lại toàn bộ từ đầu', cancelRun: 'Dừng',
  results: 'Kết quả', noResults: 'Chạy Compile hoặc Chạy Transform để xem kết quả từng model.',
  modelsRan: 'model', testsRan: 'test',
  mergeNeedsKey: 'Chiến lược merge cần Unique key, nếu không dbt sẽ chỉ thêm dòng mới.',
  conflictTitle: 'Model đã bị thay đổi ở nơi khác',
  conflictMessage: 'Ai đó (hoặc một tab khác) đã lưu model này sau khi bạn mở. Chọn bản muốn giữ.',
  conflictMine: 'Bản của bạn', conflictTheirs: 'Bản trên máy chủ',
  conflictKeepMine: 'Giữ bản của tôi', conflictTakeServer: 'Lấy bản trên máy chủ',
  didYouMean: 'Ý bạn là:',
  draft: 'Bản nháp', live: 'Đang chạy:', liveVersion: 'Phiên bản {n}',
  nothingPublished: 'Chưa xuất bản',
  nothingPublishedHint: 'Chưa xuất bản bản nào — lịch chạy sẽ không có gì để chạy.',
  inSync: 'Khớp với bản nháp', outOfSync: 'Bản nháp có thay đổi chưa xuất bản.',
  publish: 'Xuất bản', publishTitle: 'Xuất bản bản nháp',
  publishHint: 'Đóng băng code hiện tại. Từ giờ lịch chạy sẽ dùng bản này, kể cả khi bạn sửa tiếp.',
  notes: 'Ghi chú', notesPlaceholder: 'Thay đổi gì trong lần này?',
  history: 'Lịch sử', historyTitle: 'Các phiên bản đã xuất bản',
  noReleases: 'Chưa có phiên bản nào.', restore: 'Khôi phục', active: 'Đang chạy',
  published: 'Đã xuất bản Phiên bản {n}', restored: 'Đã chuyển sang phiên bản này', restoredToDraft: 'Đã đưa vào bản nháp',
  scheduleRuns: 'Lịch chạy dùng', columnsShort: 'cột', tools: 'Công cụ',
  moreActions: 'Thao tác khác', syntaxOk: 'SQL hợp lệ', syntaxError: 'SQL có lỗi',
  exploreInput: 'Xem thử bảng này', exploreComment: 'Đọc dữ liệu từ bảng nguồn.',
  hidePanel: 'Đóng', showPanel: 'Cấu hình', modelSettings: 'Thiết lập bảng', rename: 'Đổi tên', notRunYet: 'Chưa chạy', exportCsv: 'Tải CSV', rowsCounted: 'dòng',
  resizeRail: 'Kéo để đổi độ rộng danh sách', resizeSide: 'Kéo để đổi độ rộng bảng cấu hình',
  resizeEditor: 'Kéo để đổi chiều cao vùng soạn thảo',
  zoomIn: 'Phóng to', zoomOut: 'Thu nhỏ', zoomFit: 'Vừa khung',
  expand: 'Mở rộng sơ đồ', collapse: 'Thu lại',
  legendSource: 'Nguồn dữ liệu', legendSelected: 'Đang mở', legendHealthy: 'Bảng đã dựng',
  previewHelp: 'Xem thử 20 dòng kết quả. Không ghi gì vào warehouse.',
  compileHelp: 'Kiểm tra cú pháp, không chạm dữ liệu.',
  testHelp: 'Chạy các test đã đặt cho model này.',
  runModelHelp: 'Ghi model này vào schema thử nghiệm.',
  runUpstreamHelp: 'Chạy cả các model mà nó phụ thuộc.',
  fullRefreshHelp: 'Dựng lại từ đầu thay vì thêm dòng mới.',
  runDraftHelp: 'Chạy code trong trình soạn thảo. Ghi vào schema thử nghiệm {schema}, không đụng bảng thật.',
  runLive: 'Chạy Phiên bản {n} (bản đang chạy)', modelsCounted: 'bảng',
  changes: 'Thay đổi sẽ được xuất bản', noChanges: 'Không có thay đổi nào.',
  changeAdded: 'Thêm', changeRemoved: 'Xóa', changeModified: 'Sửa',
  changeUnchanged: 'Giữ nguyên', inspect: 'Xem', before: 'Trước khi sửa',
  after: 'Sau khi sửa', restoreToDraft: 'Đưa về bản nháp',
  restoreHint: 'Đưa về bản nháp để xem lại rồi tự xuất bản; hoặc chuyển thẳng lịch chạy sang phiên bản này.',
  backToList: 'Quay lại danh sách', noSqlChange: 'Không đổi SQL.',
  triggerSchedule: 'Theo lịch',
  triggerScheduleHelp: 'Tự chạy theo chu kỳ. Luôn chạy phiên bản đã xuất bản.',
  scheduleEvery: 'Chạy mỗi', timezone: 'Múi giờ', nextRun: 'Lần chạy tới',
  every30m: '30 phút', every1h: '1 giờ', every6h: '6 giờ', everyDay: '1 ngày',
  scheduleKind: 'Kiểu lịch', kindInterval: 'Theo chu kỳ', kindDaily: 'Hằng ngày',
  kindCron: 'Biểu thức cron', atTime: 'Vào lúc', cronExpression: 'Cron',
  startFrom: 'Bắt đầu từ', advanced: 'Tùy chọn nâng cao',
  materializationHint: {
    VIEW: 'Không lưu dữ liệu, chạy lại mỗi lần đọc. Nhanh, luôn mới, hợp cho bước làm sạch.',
    TABLE: 'Ghi kết quả thành bảng thật. Đọc nhanh, dựng lại toàn bộ mỗi lần chạy.',
    INCREMENTAL: 'Lần đầu dựng toàn bộ, sau đó chỉ thêm dòng mới. Hợp bảng lớn.',
  } as Record<string, string>,
  uniqueKeyHint: 'Cột nhận diện một dòng. Thiếu nó, dữ liệu cũ sẽ bị nhân đôi.',
  strategyHint: 'Cách dbt ghi dòng mới vào bảng đã có. Danh sách theo warehouse đang dùng.',
  layerHint: 'Chỉ để sắp xếp trong danh sách, không đổi cách chạy.',
  descriptionHint: 'Hiện trong tài liệu dbt sinh ra.',
  relationNameHint: 'Tên bảng trong warehouse. Để trống thì lấy tên model.',
  outputSchemaHint: 'Schema đích. Để trống thì dùng mặc định của Transform.',
  templateName: {
    blank: 'Trống', staging: 'Làm sạch một bảng nguồn',
    join: 'Ghép hai model', aggregate: 'Tổng hợp theo ngày',
    incremental: 'Chỉ xử lý dữ liệu mới',
  } as Record<string, string>,
  templateHint: {
    blank: 'Tự viết từ đầu.',
    staging: 'Đổi tên cột, sửa kiểu dữ liệu. Thường là bước đầu tiên.',
    join: 'Nối hai model đã có thành một bảng rộng hơn.',
    aggregate: 'Mỗi ngày một dòng — dạng dashboard hay dùng.',
    incremental: 'Lần sau chỉ chạy trên dòng mới. Cần đặt Unique key.',
  } as Record<string, string>,
  filterModels: 'Tìm bảng', filterAll: 'Tìm bảng hoặc nguồn...', modelActions: 'Thao tác', duplicateModel: 'Nhân bản',
  noModelMatch: 'Không tìm thấy bảng nào.',
  layerLabel: { STAGING: 'Staging', CORE: 'Core', MART: 'Data Mart' } as Record<string, string>,
  deleteModelTitle: 'Xóa bảng này?',
  deleteModelMessage: 'Model "{name}" và toàn bộ test của nó sẽ bị xóa. Không hoàn tác được.',
  deleteModelDependents: 'Các model đang tham chiếu tới nó sẽ không compile được:',
};
const en = {
  git: {
    title: 'GitHub sync',
    description: 'When the repository gets a new commit, the models here follow it.',
    saved: 'Sync settings saved',
    notConnected: 'This Transform does not follow a repository',
    notConnectedHint: 'Only Transforms created through Import from GitHub have one.',
    repository: 'Repository', branch: 'Branch', branchDefault: 'default branch',
    lastSync: 'Last sync', never: 'Never synced',
    commit: 'commit', nextSync: 'Next check',
    syncNow: 'Sync now',
    reapply: 'Re-apply current commit',
    reapplyHint: 'For when you have just granted access to a source table and the repository has not moved.',
    enable: 'Follow the repository automatically',
    enableHint: 'Asks GitHub whether the branch has moved, and only downloads when it has.',
    every: 'Check every',
    autoPublish: 'Publish automatically after a sync',
    autoPublishHint: 'On, and the schedule runs the new code straight away. Off, and you review it first.',
    token: 'GitHub access token',
    tokenStored: 'No token stored',
    tokenReplace: 'Stored — type to replace it',
    tokenHint: 'The token is encrypted at rest and never shown again.',
    save: 'Save',
    disconnect: 'Disconnect',
    managed: 'Models the repository owns',
    managedHint: 'Only these are overwritten or removed by a sync. Anything you wrote here is kept.',
    statusLabel: {
      APPLIED: 'Applied changes from Git',
      UNCHANGED: 'In step with Git',
      FAILED: 'Sync failed',
    } as Record<string, string>,
    intervals: [
      { value: 5, label: '5 minutes' }, { value: 15, label: '15 minutes' },
      { value: 30, label: '30 minutes' }, { value: 60, label: '1 hour' },
      { value: 360, label: '6 hours' }, { value: 1440, label: '1 day' },
    ],
  },
  aiButton: 'Ask AI',
  aiAccepted: 'Model created from the AI draft',
  ai: {
    title: 'Ask AI to write a model',
    description: 'Pick a source table and say what you want in plain language. The assistant reads the real data, and the warehouse plans the SQL before you accept it.',
    sourceTable: 'Source table',
    intent: 'What should this model return?',
    intentPlaceholder: 'For example: clean up the orders table, rename columns to something readable, drop rows with no order id.',
    intentHint: 'The clearer you are about what it is for, the closer the result. No SQL needed.',
    examples: 'Try one of these',
    draft: 'Draft it',
    drafting: 'Reading your data and writing SQL',
    draftingHint: 'This samples the real table and asks the warehouse to plan the query, so it takes 15-40 seconds.',
    again: 'Draft again',
    use: 'Use this draft',
    cancel: 'Cancel',
    back: 'Back',
    summary: 'Summary',
    assumptions: 'What the assistant had to guess',
    assumptionsHint: 'These are the points the data did not settle on its own. Read them before accepting.',
    proposedTests: 'Proposed tests',
    noTests: 'No test fits this data.',
    sql: 'SQL',
    validationOk: 'The warehouse accepted this SQL',
    validationRepaired: 'The first attempt failed; the assistant fixed it and the warehouse accepted the result',
    validationFailed: 'The warehouse rejected this SQL',
    validationSkipped: 'Could not be planned against this warehouse',
    validationOkHint: 'Planned against your own warehouse at no cost, writing nothing.',
    validationRepairedHint: 'What you see below is the corrected version; the failed one was discarded.',
    validationFailedHint: 'You can still accept it and fix it in the editor, or draft again.',
    validationSkippedHint: 'This warehouse type has no dry run yet. Use Preview after accepting.',
    confidence: { HIGH: 'High confidence', MEDIUM: 'Medium confidence', LOW: 'Low confidence' },
    layerLabel: { STAGING: 'Staging', CORE: 'Core', MART: 'Mart' },
    ruleLabel: { NOT_NULL: 'Not null', UNIQUE: 'Unique', ACCEPTED_VALUES: 'Accepted values' },
    exampleIntents: [
      'Clean this table up: readable column names and correct types',
      'Turn the timestamp columns into readable date-times',
      'Drop rows with no identifier',
      'Count rows per day',
    ],
  },
  loading: 'Loading transform', loadError: 'Could not load transform', lineage: 'Lineage', settings: 'Settings', save: 'Save', saved: 'Model saved', runTransform: 'Run Transform', inputs: 'Inputs', models: 'Models', newModel: 'New model', noModel: 'No models yet', unsaved: 'Unsaved', visualLater: 'Visual mode follows after reliable SQL round-tripping', compile: 'Compile', preview: 'Preview', runModel: 'Run model', config: 'Config', tests: 'Tests', cancel: 'Cancel', create: 'Create', modelName: 'Model name', layer: 'Layer', materialization: 'Materialization', unsavedTitle: 'Unsaved model', discard: 'Discard', unsavedMessage: 'Save, discard, or cancel before switching away from this model.', compiledSql: 'Compiled SQL', logs: 'Logs', viewRun: 'View run', noPreview: 'Run Preview to inspect rows.', noCompiled: 'Run Compile to inspect compiled SQL.', noLogs: 'No logs yet.', outputSchema: 'Output schema', defaultOutput: 'Transform default', relationName: 'Relation name', description: 'Description', uniqueKey: 'Unique key', strategy: 'Incremental strategy', deleteModel: 'Delete model', model: 'Model', removeTest: 'Remove test', column: 'Column', rule: 'Rule', values: 'Comma-separated values', severity: 'Severity', addTest: 'Add test', lineageDescription: 'Lineage uses AppBI asset identities and the dbt manifest.', noLineage: 'Compile or run this Transform to generate lineage.', warehouse: 'Warehouse', executionTrigger: 'Execution trigger', exportProject: 'Export dbt project',
  validate: 'Check connection', insertReference: 'Insert reference',
  stale: 'Source data is older than the last build', unresolved: 'Relation is not verified',
  warehouseRelation: 'Warehouse relation',
  line: 'line', showTechnical: 'Show technical details', hideTechnical: 'Hide technical details',
  remediation: REMEDIATION_EN,
  settingsSaved: 'Transform settings saved',
  outputSchemaHelp: 'Default schema the models are created in.',
  triggerManual: 'Manual', triggerUpstream: 'After upstream data is ready',
  triggerManualHelp: 'Runs only when you press Run Transform.',
  triggerUpstreamHelp: 'Runs once every required input has been freshly loaded.',
  needInput: 'At least one input is required.',
  waitingUpstream: 'Waiting for upstream data',
  testModel: 'Test', moreRunOptions: 'More run options',
  runUpstream: 'Run with upstream models', fullRefreshModel: 'Full refresh this model',
  fullRefreshAll: 'Full refresh everything', cancelRun: 'Stop',
  results: 'Results', noResults: 'Run Compile or Run Transform to see per-model results.',
  modelsRan: 'models', testsRan: 'tests',
  mergeNeedsKey: 'The merge strategy needs a Unique key, or dbt only appends rows.',
  conflictTitle: 'This model changed elsewhere',
  conflictMessage: 'Someone (or another tab) saved this model after you opened it. Choose which version to keep.',
  conflictMine: 'Your version', conflictTheirs: 'Server version',
  conflictKeepMine: 'Keep mine', conflictTakeServer: 'Take server version',
  didYouMean: 'Did you mean:',
  draft: 'Draft', live: 'Live:', liveVersion: 'Version {n}',
  nothingPublished: 'Not published',
  nothingPublishedHint: 'Nothing published yet — a schedule would have nothing to run.',
  inSync: 'Matches your draft', outOfSync: 'Your draft has unpublished changes.',
  publish: 'Publish', publishTitle: 'Publish this draft',
  publishHint: 'Freezes the current code. Schedules run this version from now on, even while you keep editing.',
  notes: 'Notes', notesPlaceholder: 'What changed in this version?',
  history: 'History', historyTitle: 'Published versions',
  noReleases: 'No versions published yet.', restore: 'Restore', active: 'Live',
  published: 'Published Version {n}', restored: 'Switched to this version', restoredToDraft: 'Copied into the draft',
  scheduleRuns: 'Schedule runs', columnsShort: 'columns', tools: 'Tools',
  moreActions: 'More actions', syntaxOk: 'SQL is valid', syntaxError: 'SQL has an error',
  exploreInput: 'Preview this table', exploreComment: 'Reads from the source table.',
  hidePanel: 'Close', showPanel: 'Settings', modelSettings: 'Model settings', rename: 'Rename', notRunYet: 'Not run', exportCsv: 'Export CSV', rowsCounted: 'rows',
  resizeRail: 'Drag to resize the list', resizeSide: 'Drag to resize the settings panel',
  resizeEditor: 'Drag to resize the editor',
  zoomIn: 'Zoom in', zoomOut: 'Zoom out', zoomFit: 'Fit',
  expand: 'Expand graph', collapse: 'Collapse',
  legendSource: 'Source', legendSelected: 'Open now', legendHealthy: 'Built table',
  previewHelp: 'Shows 20 rows of the result. Writes nothing to the warehouse.',
  compileHelp: 'Checks the SQL. Touches no data.',
  testHelp: 'Runs the tests defined on this model.',
  runModelHelp: 'Writes this model into the trial schema.',
  runUpstreamHelp: 'Also runs the models it depends on.',
  fullRefreshHelp: 'Rebuilds from scratch instead of adding new rows.',
  runDraftHelp: 'Runs the code in the editor. Writes to the trial schema {schema}, leaving the real tables alone.',
  runLive: 'Run Version {n} (the live one)', modelsCounted: 'models',
  changes: 'Changes to publish', noChanges: 'No changes.',
  changeAdded: 'Added', changeRemoved: 'Removed', changeModified: 'Changed',
  changeUnchanged: 'Unchanged', inspect: 'View', before: 'Before',
  after: 'After', restoreToDraft: 'Copy into draft',
  restoreHint: 'Copy into the draft to review then publish yourself, or point the schedule straight at this version.',
  backToList: 'Back to list', noSqlChange: 'SQL unchanged.',
  triggerSchedule: 'On a schedule',
  triggerScheduleHelp: 'Runs automatically. Always runs the published version.',
  scheduleEvery: 'Run every', timezone: 'Timezone', nextRun: 'Next run',
  every30m: '30 minutes', every1h: '1 hour', every6h: '6 hours', everyDay: '1 day',
  scheduleKind: 'Schedule type', kindInterval: 'Every N', kindDaily: 'Daily',
  kindCron: 'Cron expression', atTime: 'At', cronExpression: 'Cron',
  startFrom: 'Start from', advanced: 'Advanced options',
  materializationHint: {
    VIEW: 'Stores no data; re-runs on every read. Fast, always current, good for cleanup steps.',
    TABLE: 'Writes a real table. Fast to read, rebuilt in full on every run.',
    INCREMENTAL: 'Builds once, then adds only new rows. Suits large tables.',
  } as Record<string, string>,
  uniqueKeyHint: 'The column that identifies a row. Without it, existing rows get duplicated.',
  strategyHint: 'How dbt writes new rows into an existing table. Options follow your warehouse.',
  layerHint: 'Grouping in the list only; it does not change how the model runs.',
  descriptionHint: 'Appears in the generated dbt documentation.',
  relationNameHint: 'Table name in the warehouse. Defaults to the model name.',
  outputSchemaHint: 'Target schema. Defaults to the Transform default.',
  templateName: {
    blank: 'Blank', staging: 'Clean one source table',
    join: 'Join two models', aggregate: 'Summarise by day',
    incremental: 'Process only new rows',
  } as Record<string, string>,
  templateHint: {
    blank: 'Write it yourself.',
    staging: 'Rename columns and fix types. Usually the first step.',
    join: 'Combine two existing models into a wider table.',
    aggregate: 'One row per day -- the shape a dashboard wants.',
    incremental: 'Later runs touch only new rows. Needs a Unique key.',
  } as Record<string, string>,
  filterModels: 'Filter models', filterAll: 'Find a table or source...', modelActions: 'Actions', duplicateModel: 'Duplicate',
  noModelMatch: 'No model matches.',
  layerLabel: { STAGING: 'Staging', CORE: 'Core', MART: 'Data Mart' } as Record<string, string>,
  deleteModelTitle: 'Delete model?',
  deleteModelMessage: 'Model "{name}" and all of its tests will be deleted. This cannot be undone.',
  deleteModelDependents: 'These models reference it and will stop compiling:',
};
