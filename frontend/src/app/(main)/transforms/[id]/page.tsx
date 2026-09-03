'use client';

/**
 * The Transform workbench: a dbt Studio, presented by AppBI.
 *
 * The layout follows the blueprint's §18: an explorer on the left with PROJECT
 * and RESOURCES views, a multi-file editor in the middle, an output panel below
 * it, an inspector drawer on the right, and a command bar at the bottom.
 *
 * The state model matters more than the layout. Three things are kept strictly
 * apart, because conflating any two of them is how a data tool becomes
 * untrustworthy:
 *
 *   buffers          what the editor holds (may be unsaved)
 *   working revision what was last saved  (what a parse describes)
 *   active release   what production runs (what a schedule executes)
 *
 * Every write carries the revision it was based on, so two people editing one
 * project get a conflict dialog rather than a silent overwrite.
 */

import * as React from 'react';
import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ArrowLeft, ChevronDown, FileCode, GitBranch, Hammer, Loader2,
  Play, Save, Settings2, TriangleAlert,
} from 'lucide-react';

import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { ErrorState, Spinner } from '@/components/ui/Feedback';
import { Menu } from '@/components/ui/Menu';
import { Modal } from '@/components/ui/Modal';
import { Input } from '@/components/ui/Input';
import { CommandBar, type ParsedCommand } from '@/components/transforms/CommandBar';
import { DbtFileEditor } from '@/components/transforms/DbtFileEditor';
import { EditorTabs, type OpenTab } from '@/components/transforms/EditorTabs';
import { GitPanel } from '@/components/transforms/GitPanel';
import { LineageView } from '@/components/transforms/LineageView';
import { OutputPanel, type OutputTab } from '@/components/transforms/OutputPanel';
import { ProjectFileTree } from '@/components/transforms/ProjectFileTree';
import { PublishBar } from '@/components/transforms/PublishBar';
import { ResourceInspector } from '@/components/transforms/ResourceInspector';
import {
  EMPTY_FILTERS, ResourceTree, type ResourceFilters,
} from '@/components/transforms/ResourceTree';
import { Resizer } from '@/components/transforms/Resizer';
import { useWorkspaceId } from '@/hooks/use-current-user';
import { toastError, toastSuccess } from '@/hooks/use-toast';
import { ApiError, transformApi } from '@/lib/api';
import { qk } from '@/lib/queryKeys';
import type {
  DbtCommand, FileTemplate, ResourceSummary, TransformProblem,
} from '@/lib/types';
import { cn } from '@/lib/utils';

type ExplorerView = 'project' | 'resources' | 'git';
type CentreView = 'editor' | 'lineage';

/** A run in one of these states is still moving, so keep polling it. */
const ACTIVE_STATUSES = ['QUEUED', 'STARTING', 'RUNNING', 'CANCEL_REQUESTED'];

export default function TransformWorkbenchPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id;
  const router = useRouter();
  const workspaceId = useWorkspaceId();
  const queryClient = useQueryClient();

  // ── layout ──────────────────────────────────────────────────────────────
  const [explorerWidth, setExplorerWidth] = React.useState(248);
  const [outputHeight, setOutputHeight] = React.useState(220);
  const [explorerView, setExplorerView] = React.useState<ExplorerView>('project');
  const [centreView, setCentreView] = React.useState<CentreView>('editor');
  const [outputTab, setOutputTab] = React.useState<OutputTab>('problems');
  const [inspectorId, setInspectorId] = React.useState<string | null>(null);

  // ── editor state ────────────────────────────────────────────────────────
  const [tabs, setTabs] = React.useState<OpenTab[]>([]);
  const [activePath, setActivePath] = React.useState<string | null>(null);
  const [buffers, setBuffers] = React.useState<Record<string, string>>({});
  const [baseline, setBaseline] = React.useState<Record<string, string>>({});
  const [errorLine, setErrorLine] = React.useState<number | null>(null);
  const [history, setHistory] = React.useState<string[]>([]);
  const [invocationId, setInvocationId] = React.useState<string | null>(null);
  const [filters, setFilters] = React.useState<ResourceFilters>(EMPTY_FILTERS);
  const [lineageFocus, setLineageFocus] = React.useState<string | null>(null);
  const [lineageFull, setLineageFull] = React.useState(false);
  const [newFileOpen, setNewFileOpen] = React.useState(false);
  const [newFileParent, setNewFileParent] = React.useState('');
  const [conflict, setConflict] = React.useState<{ server: string; mine: string } | null>(null);

  const invalidate = React.useCallback(
    (key?: readonly unknown[]) =>
      queryClient.invalidateQueries({ queryKey: key ?? ['workspace', workspaceId] }),
    [queryClient, workspaceId],
  );

  // ── project ─────────────────────────────────────────────────────────────
  const project = useQuery({
    queryKey: qk.transform(workspaceId, projectId),
    queryFn: () => transformApi.detail(projectId),
    refetchInterval: 20_000,
  });
  const detail = project.data;
  const canEdit = detail?.permissions.can_edit ?? false;
  const canOperate = detail?.permissions.can_operate ?? false;
  const revisionId = detail?.working_revision?.id ?? null;

  const files = useQuery({
    queryKey: qk.transformFiles(workspaceId, projectId),
    queryFn: () => transformApi.files(projectId),
    enabled: Boolean(detail),
  });

  const problems = useQuery({
    queryKey: qk.transformProblems(workspaceId, projectId),
    queryFn: () => transformApi.problems(projectId),
    enabled: Boolean(detail),
  });

  const completions = useQuery({
    queryKey: qk.transformCompletions(workspaceId, projectId),
    queryFn: () => transformApi.completions(projectId),
    enabled: Boolean(detail) && detail?.parse_status === 'OK',
  });

  const resourceFilters = React.useMemo(() => ({
    search: filters.search || undefined,
    resource_type: filters.resourceTypes.length ? filters.resourceTypes : undefined,
    tag: filters.tag ?? undefined,
    package: filters.packageName ?? undefined,
    materialized: filters.materialized ?? undefined,
    limit: 500,
  }), [filters]);

  const resources = useQuery({
    queryKey: qk.transformResources(workspaceId, projectId, resourceFilters),
    queryFn: () => transformApi.resources(projectId, resourceFilters),
    enabled: Boolean(detail),
  });

  const facets = useQuery({
    queryKey: qk.transformFacets(workspaceId, projectId),
    queryFn: () => transformApi.resourceFacets(projectId),
    enabled: Boolean(detail),
  });

  const lineageOptions = React.useMemo(() => ({
    focus: lineageFull ? undefined : (lineageFocus ?? undefined),
    full: lineageFull,
    max_nodes: lineageFull ? 800 : 200,
  }), [lineageFocus, lineageFull]);

  const lineage = useQuery({
    queryKey: qk.transformLineage(workspaceId, projectId, lineageOptions),
    queryFn: () => transformApi.lineage(projectId, lineageOptions),
    enabled: Boolean(detail) && centreView === 'lineage',
  });

  const inspector = useQuery({
    queryKey: qk.transformResource(workspaceId, projectId, inspectorId ?? ''),
    queryFn: () => transformApi.resourceDetail(projectId, inspectorId!),
    enabled: Boolean(inspectorId),
  });

  const releases = useQuery({
    queryKey: qk.transformReleases(workspaceId, projectId),
    queryFn: () => transformApi.releases(projectId),
    enabled: Boolean(detail),
  });

  const publishPlan = useQuery({
    queryKey: qk.transformPublishPlan(workspaceId, projectId),
    queryFn: () => transformApi.publishPlan(projectId),
    enabled: false,
  });

  const gitStatus = useQuery({
    queryKey: qk.transformGitStatus(workspaceId, projectId),
    queryFn: () => transformApi.gitStatus(projectId, true),
    enabled: Boolean(detail) && detail?.mode === 'GIT',
    refetchInterval: explorerView === 'git' ? 30_000 : false,
  });

  const gitBranches = useQuery({
    queryKey: qk.transformGitBranches(workspaceId, projectId),
    queryFn: () => transformApi.gitBranches(projectId),
    enabled: Boolean(detail) && detail?.mode === 'GIT' && explorerView === 'git',
  });

  const templates = useQuery({
    queryKey: qk.transformTemplates(workspaceId, projectId),
    queryFn: () => transformApi.fileTemplates(projectId),
    enabled: newFileOpen,
  });

  // The most recent run for this project, so opening the page shows the last
  // build's results rather than an empty panel.  Without this `invocationId`
  // only ever gets set by an action taken in this page session, and a reload
  // threw away everything the project had already done.
  const lastInvocation = useQuery({
    queryKey: qk.transformInvocations(workspaceId, projectId, { limit: 1 }),
    queryFn: () => transformApi.invocations(projectId, { limit: 1 }),
  });

  React.useEffect(() => {
    const latest = lastInvocation.data?.items?.[0]?.id;
    if (latest) setInvocationId((current) => current ?? latest);
  }, [lastInvocation.data]);

  // The run being watched. Polled only while it is still moving -- a finished
  // run is immutable, so continuing to poll it is pure waste.
  const invocation = useQuery({
    queryKey: qk.transformInvocation(workspaceId, invocationId ?? ''),
    queryFn: () => transformApi.invocation(invocationId!),
    enabled: Boolean(invocationId),
    refetchInterval: (query) =>
      ACTIVE_STATUSES.includes(query.state.data?.status ?? '') ? 1_500 : false,
  });

  const logs = useQuery({
    queryKey: qk.transformInvocationLogs(workspaceId, invocationId ?? ''),
    queryFn: () => transformApi.logs(invocationId!, 0, 2000),
    enabled: Boolean(invocationId) && outputTab === 'logs',
    refetchInterval: () =>
      ACTIVE_STATUSES.includes(invocation.data?.status ?? '') ? 2_000 : false,
  });

  const activeResource = React.useMemo(() => {
    if (!activePath) return null;
    return (resources.data?.items ?? []).find(
      (item) => item.path === activePath,
    ) ?? null;
  }, [activePath, resources.data]);

  const compiled = useQuery({
    queryKey: qk.transformCompiled(workspaceId, projectId, activeResource?.unique_id ?? ''),
    queryFn: () => transformApi.compiled(projectId, activeResource!.unique_id),
    enabled: Boolean(activeResource) && outputTab === 'compiled',
    retry: false,
  });

  // ── file operations ─────────────────────────────────────────────────────

  const openFile = React.useCallback(async (path: string, line?: number) => {
    setCentreView('editor');
    setActivePath(path);
    setErrorLine(line ?? null);
    setTabs((current) =>
      current.some((tab) => tab.path === path)
        ? current
        : [...current, { path, dirty: false }],
    );
    if (buffers[path] !== undefined) return;
    try {
      const content = await transformApi.fileContent(projectId, path);
      setBuffers((current) => ({ ...current, [path]: content.content }));
      setBaseline((current) => ({ ...current, [path]: content.content }));
    } catch (error) {
      toastError(error);
      setTabs((current) => current.filter((tab) => tab.path !== path));
    }
  }, [buffers, projectId]);

  const dirtyPaths = React.useMemo(
    () => new Set(tabs.filter((tab) => tab.dirty).map((tab) => tab.path)),
    [tabs],
  );

  const setBuffer = (path: string, value: string) => {
    setBuffers((current) => ({ ...current, [path]: value }));
    setTabs((current) => current.map((tab) =>
      tab.path === path ? { ...tab, dirty: value !== baseline[path] } : tab,
    ));
  };

  const afterSave = (saved: string[], newRevision: string, parseId: string | null) => {
    setBaseline((current) => {
      const next = { ...current };
      saved.forEach((path) => { next[path] = buffers[path] ?? ''; });
      return next;
    });
    setTabs((current) => current.map((tab) =>
      saved.includes(tab.path) ? { ...tab, dirty: false } : tab,
    ));
    if (parseId) setInvocationId(parseId);
    invalidate(qk.transform(workspaceId, projectId));
    invalidate(qk.transformFiles(workspaceId, projectId));
    invalidate(qk.transformProblems(workspaceId, projectId));
  };

  const save = useMutation({
    mutationFn: async (path: string) =>
      transformApi.saveFile(projectId, {
        path,
        content: buffers[path] ?? '',
        // The revision the editor was looking at. A mismatch is a 409 carrying
        // both versions, never a silent overwrite of somebody else's work.
        expected_revision_id: revisionId,
      }),
    onSuccess: (result) => {
      afterSave(result.saved_paths, result.revision_id, result.parse_invocation_id);
      toastSuccess('Đã lưu.');
    },
    onError: (error, path) => {
      if (error instanceof ApiError && error.code === 'TRANSFORM_REVISION_STALE') {
        void handleConflict(path);
        return;
      }
      toastError(error);
    },
  });

  const saveAll = useMutation({
    mutationFn: async () => {
      const changed = tabs.filter((tab) => tab.dirty);
      if (changed.length === 0) return null;
      return transformApi.saveBatch(projectId, {
        changes: changed.map((tab) => ({ path: tab.path, content: buffers[tab.path] ?? '' })),
        expected_revision_id: revisionId,
      });
    },
    onSuccess: (result) => {
      if (!result) return;
      afterSave(result.saved_paths, result.revision_id, result.parse_invocation_id);
      toastSuccess(`Đã lưu ${result.saved_paths.length} tệp.`);
    },
    onError: (error) => toastError(error),
  });

  /** Somebody else saved while this buffer was open. Show both, decide nothing. */
  const handleConflict = async (path: string) => {
    try {
      const server = await transformApi.fileContent(projectId, path);
      setConflict({ server: server.content, mine: buffers[path] ?? '' });
    } catch (error) {
      toastError(error);
    }
  };

  const createFile = useMutation({
    mutationFn: (input: { path: string; template?: string }) =>
      transformApi.createFile(projectId, {
        path: input.path,
        template: input.template,
        expected_revision_id: revisionId,
      }),
    onSuccess: (result) => {
      invalidate(qk.transformFiles(workspaceId, projectId));
      invalidate(qk.transform(workspaceId, projectId));
      if (result.parse_invocation_id) setInvocationId(result.parse_invocation_id);
      const path = result.saved_paths[0];
      if (path) void openFile(path);
      setNewFileOpen(false);
      toastSuccess('Đã tạo tệp.');
    },
    onError: (error) => toastError(error),
  });

  const deleteFile = useMutation({
    mutationFn: (path: string) =>
      transformApi.deleteFiles(projectId, {
        paths: [path], expected_revision_id: revisionId,
      }),
    onSuccess: (result, path) => {
      setTabs((current) => current.filter((tab) => tab.path !== path));
      if (activePath === path) setActivePath(null);
      invalidate(qk.transformFiles(workspaceId, projectId));
      if (result.parse_invocation_id) setInvocationId(result.parse_invocation_id);
      toastSuccess('Đã xoá tệp.');
    },
    onError: (error) => toastError(error),
  });

  const moveFile = useMutation({
    mutationFn: (input: { from: string; to: string }) =>
      transformApi.moveFile(projectId, {
        from_path: input.from, to_path: input.to, expected_revision_id: revisionId,
      }),
    onSuccess: (result, input) => {
      setTabs((current) => current.map((tab) =>
        tab.path === input.from ? { ...tab, path: input.to } : tab,
      ));
      setBuffers((current) => {
        const next = { ...current };
        next[input.to] = next[input.from];
        delete next[input.from];
        return next;
      });
      setBaseline((current) => {
        const next = { ...current };
        next[input.to] = next[input.from];
        delete next[input.from];
        return next;
      });
      if (activePath === input.from) setActivePath(input.to);
      invalidate(qk.transformFiles(workspaceId, projectId));
      if (result.parse_invocation_id) setInvocationId(result.parse_invocation_id);
    },
    onError: (error) => toastError(error),
  });

  // ── running dbt ─────────────────────────────────────────────────────────

  const run = useMutation({
    mutationFn: (input: {
      command: DbtCommand; selector?: string | null; exclude?: string | null;
      fullRefresh?: boolean; source?: 'DRAFT' | 'RELEASE';
    }) => transformApi.run(projectId, {
      command: input.command,
      selector: input.selector ?? null,
      exclude: input.exclude ?? null,
      full_refresh: input.fullRefresh ?? false,
      source: input.source ?? 'DRAFT',
    }),
    onSuccess: (created, input) => {
      setInvocationId(created.id);
      setOutputTab(
        input.command === 'show' ? 'preview'
          : input.command === 'compile' ? 'compiled'
            : 'results',
      );
      const line = `dbt ${input.command}${input.selector ? ` --select ${input.selector}` : ''}`;
      setHistory((current) => [line, ...current.filter((item) => item !== line)].slice(0, 20));
      invalidate(qk.transform(workspaceId, projectId));
    },
    onError: (error) => toastError(error),
  });

  const cancel = useMutation({
    mutationFn: () => transformApi.cancel(invocationId!),
    onSuccess: () => invalidate(qk.transformInvocation(workspaceId, invocationId ?? '')),
    onError: (error) => toastError(error),
  });

  /** Save anything unsaved, then run -- Preview must reflect what is on screen. */
  const saveThenRun = async (input: Parameters<typeof run.mutate>[0]) => {
    if (tabs.some((tab) => tab.dirty)) {
      await saveAll.mutateAsync();
    }
    run.mutate(input);
  };

  const publish = useMutation({
    mutationFn: (input: { notes: string; activate: boolean }) =>
      transformApi.publish(projectId, { notes: input.notes, activate: input.activate }),
    onSuccess: (result) => {
      setInvocationId(result.verification_invocation_id);
      setOutputTab('results');
      invalidate();
      toastSuccess(
        `Đã xuất bản bản ${result.release.release_number}. Đang build thử để xác nhận.`,
      );
    },
    onError: (error) => toastError(error),
  });

  const gitPull = useMutation({
    mutationFn: (discardLocal: boolean) =>
      transformApi.gitPull(projectId, { discard_local: discardLocal }),
    onSuccess: (result) => {
      invalidate();
      setBuffers({});
      setBaseline({});
      setTabs([]);
      setActivePath(null);
      toastSuccess(
        result.changed ? `Đã lấy về ${result.files_changed} tệp thay đổi.` : 'Không có gì mới.',
      );
    },
    onError: (error) => toastError(error),
  });

  const gitCommit = useMutation({
    mutationFn: (input: { message: string; paths: string[] | null }) =>
      transformApi.gitCommit(projectId, input),
    onSuccess: (result) => {
      invalidate();
      toastSuccess(`Đã commit ${result.files_committed} tệp lên ${result.branch}.`);
    },
    onError: (error) => toastError(error),
  });

  const gitCheckout = useMutation({
    mutationFn: (input: { branch: string; discardLocal: boolean }) =>
      transformApi.gitCheckout(projectId, {
        branch: input.branch, discard_local: input.discardLocal,
      }),
    onSuccess: (result) => {
      invalidate();
      setBuffers({}); setBaseline({}); setTabs([]); setActivePath(null);
      toastSuccess(`Đã chuyển sang nhánh ${result.branch}.`);
    },
    onError: (error) => toastError(error),
  });

  // ── keyboard ────────────────────────────────────────────────────────────
  React.useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const meta = event.metaKey || event.ctrlKey;
      if (!meta) return;
      if (event.key === 's') {
        event.preventDefault();
        if (event.shiftKey) saveAll.mutate();
        else if (activePath) save.mutate(activePath);
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [activePath, save, saveAll]);

  // Warn before losing unsaved buffers to a page close.
  React.useEffect(() => {
    const anyDirty = tabs.some((tab) => tab.dirty);
    if (!anyDirty) return undefined;
    const onBeforeUnload = (event: BeforeUnloadEvent) => { event.preventDefault(); };
    window.addEventListener('beforeunload', onBeforeUnload);
    return () => window.removeEventListener('beforeunload', onBeforeUnload);
  }, [tabs]);

  if (project.isLoading) {
    return <div className="flex h-full items-center justify-center"><Spinner label="Đang mở dự án…" /></div>;
  }
  if (project.error || !detail) {
    return (
      <div className="p-6">
        <ErrorState
          title="Không mở được dự án"
          message={(project.error as Error | null)?.message}
          onRetry={() => project.refetch()}
        />
      </div>
    );
  }

  const activeRelease = (releases.data ?? []).find((item) => item.is_active) ?? null;
  const dirtyCount = tabs.filter((tab) => tab.dirty).length;
  const running = ACTIVE_STATUSES.includes(invocation.data?.status ?? '');
  const environment = detail.environments.find(
    (item) => item.id === detail.default_environment_id,
  ) ?? detail.environments[0] ?? null;

  const selectorFor = (resource: ResourceSummary | null) => resource?.name ?? null;

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* header */}
      <header className="flex h-11 shrink-0 items-center gap-2 border-b border-[rgb(var(--border-line))] px-3">
        <Link
          href="/transforms"
          className="shrink-0 rounded-sm p-1 text-text-tertiary hover:bg-surface-2 hover:text-text-primary"
          aria-label="Về danh sách"
        >
          <ArrowLeft className="h-4 w-4" />
        </Link>
        <div className="flex min-w-0 items-center gap-2">
          <span className="truncate text-small font-emphasis text-text-primary">
            {detail.name}
          </span>
          {environment && (
            <Badge variant={environment.protected ? 'warning' : 'subtle'} size="xs">
              {environment.name}
            </Badge>
          )}
          {detail.git && (
            <span className="flex shrink-0 items-center gap-1 text-tiny text-text-tertiary">
              <GitBranch className="h-3 w-3" />
              {detail.git.branch}
              {dirtyCount > 0 && <span className="text-brand">*</span>}
            </span>
          )}
          <ParseBadge status={detail.parse_status} error={detail.parse_error} />
        </div>

        <div className="ml-auto flex shrink-0 items-center gap-1.5">
          {canEdit && (
            <Button
              variant="ghost" size="xs"
              onClick={() => saveAll.mutate()}
              disabled={dirtyCount === 0}
              loading={saveAll.isPending}
              leadingIcon={<Save className="h-3.5 w-3.5" />}
            >
              Lưu tất cả{dirtyCount > 0 ? ` (${dirtyCount})` : ''}
            </Button>
          )}
          <Button
            variant="secondary" size="xs"
            disabled={!activeResource || running}
            onClick={() => saveThenRun({
              command: 'show', selector: selectorFor(activeResource),
            })}
            leadingIcon={<Play className="h-3.5 w-3.5" />}
          >
            Preview
          </Button>
          <Menu
            label="Build"
            items={[
              {
                id: 'build-this', label: 'Build resource này',
                description: 'dbt build --select <tên>',
                disabled: !activeResource,
                onSelect: () => saveThenRun({
                  command: 'build', selector: selectorFor(activeResource),
                }),
              },
              {
                id: 'build-parents', label: 'Build cùng nguồn phía trên',
                description: 'dbt build --select +<tên>',
                disabled: !activeResource,
                onSelect: () => saveThenRun({
                  command: 'build', selector: `+${selectorFor(activeResource)}`,
                }),
              },
              {
                id: 'build-children', label: 'Build cùng phần phía dưới',
                description: 'dbt build --select <tên>+',
                disabled: !activeResource,
                onSelect: () => saveThenRun({
                  command: 'build', selector: `${selectorFor(activeResource)}+`,
                }),
              },
              {
                id: 'run-only', label: 'Chỉ chạy, không test',
                description: 'dbt run --select <tên>',
                disabled: !activeResource,
                onSelect: () => saveThenRun({
                  command: 'run', selector: selectorFor(activeResource),
                }),
              },
              {
                id: 'test-only', label: 'Chỉ chạy test',
                description: 'dbt test --select <tên>',
                disabled: !activeResource,
                onSelect: () => saveThenRun({
                  command: 'test', selector: selectorFor(activeResource),
                }),
              },
              {
                id: 'full-refresh', label: 'Build lại từ đầu',
                description: 'Xây lại cả model incremental',
                disabled: !activeResource,
                onSelect: () => saveThenRun({
                  command: 'build', selector: selectorFor(activeResource), fullRefresh: true,
                }),
              },
              {
                id: 'build-all', label: 'Build toàn bộ dự án',
                description: 'dbt build',
                onSelect: () => saveThenRun({ command: 'build' }),
              },
            ]}
            trigger={
              <span className={cn(
                'inline-flex h-7 items-center gap-1 rounded-md bg-brand px-2.5',
                'text-label font-emphasis text-text-inverse hover:bg-brand-hover',
              )}>
                <Hammer className="h-3.5 w-3.5" />
                Build
                <ChevronDown className="h-3 w-3" />
              </span>
            }
          />
          <Menu
            label="Tác vụ dự án"
            items={[
              {
                id: 'lineage', label: centreView === 'lineage' ? 'Xem trình soạn thảo' : 'Xem sơ đồ phụ thuộc',
                onSelect: () => setCentreView(centreView === 'lineage' ? 'editor' : 'lineage'),
              },
              {
                id: 'docs', label: 'Tài liệu dự án',
                onSelect: () => router.push(`/transforms/${projectId}/docs`),
              },
              {
                id: 'runs', label: 'Lịch sử chạy',
                onSelect: () => router.push(`/transforms/${projectId}/runs`),
              },
              {
                id: 'freshness', label: 'Kiểm tra độ mới của source',
                description: 'dbt source freshness',
                onSelect: () => run.mutate({ command: 'source-freshness' }),
              },
              {
                id: 'deps', label: 'Cài package',
                description: 'dbt deps',
                onSelect: () => run.mutate({ command: 'deps' }),
              },
              {
                id: 'docs-generate', label: 'Sinh catalog',
                description: 'dbt docs generate — lấy kiểu dữ liệu thật từ kho',
                onSelect: () => run.mutate({ command: 'docs-generate' }),
              },
              {
                id: 'export', label: 'Tải dự án về',
                onSelect: () => window.open(transformApi.exportUrl(projectId), '_blank'),
              },
            ]}
            trigger={
              <span className="rounded-sm p-1.5 text-text-tertiary hover:bg-surface-2">
                <Settings2 className="h-4 w-4" />
              </span>
            }
          />
        </div>
      </header>

      <PublishBar
        hasUnpublishedChanges={detail.has_unpublished_changes}
        activeRelease={activeRelease}
        releases={releases.data ?? []}
        plan={publishPlan.data ?? null}
        planLoading={publishPlan.isFetching}
        publishing={publish.isPending}
        canOperate={canOperate}
        onOpenPlan={() => publishPlan.refetch()}
        onPublish={(notes, activate) => publish.mutate({ notes, activate })}
        onActivate={(releaseId) =>
          transformApi.activateRelease(projectId, releaseId)
            .then(() => { invalidate(); toastSuccess('Đã đưa vào chạy thật.'); })
            .catch(toastError)}
      />

      {/* body */}
      <div className="flex min-h-0 flex-1">
        {/* explorer */}
        <div style={{ width: explorerWidth }} className="flex shrink-0 flex-col bg-surface-1">
          <div className="flex h-8 shrink-0 items-center gap-0.5 border-b border-[rgb(var(--border-line))] px-1">
            {([
              ['project', 'Project'],
              ['resources', 'Resources'],
              ...(detail.mode === 'GIT' ? [['git', 'Git'] as const] : []),
            ] as [ExplorerView, string][]).map(([id, label]) => (
              <button
                key={id}
                type="button"
                onClick={() => setExplorerView(id)}
                className={cn(
                  'h-6 rounded-sm px-2 text-tiny uppercase tracking-wide transition-colors',
                  explorerView === id
                    ? 'bg-surface-2 text-text-primary font-emphasis'
                    : 'text-text-tertiary hover:text-text-secondary',
                )}
              >
                {label}
                {id === 'git' && (gitStatus.data?.changes.length ?? 0) > 0 && (
                  <span className="ml-1 text-brand">{gitStatus.data?.changes.length}</span>
                )}
              </button>
            ))}
          </div>

          <div className="min-h-0 flex-1">
            {explorerView === 'project' && (
              <ProjectFileTree
                tree={files.data?.tree ?? []}
                activePath={activePath}
                dirtyPaths={dirtyPaths}
                gitChanges={
                  gitStatus.data
                    ? new Map(gitStatus.data.changes.map((item) => [item.path, item.change]))
                    : undefined
                }
                onOpen={openFile}
                onCreate={(parent) => { setNewFileParent(parent); setNewFileOpen(true); }}
                onRename={(path) => {
                  const next = window.prompt('Tên mới', path);
                  if (next && next !== path) moveFile.mutate({ from: path, to: next });
                }}
                onDuplicate={(path) => {
                  const next = window.prompt('Tên bản sao', path.replace(/(\.[^.]+)$/, '_copy$1'));
                  if (next) {
                    transformApi.fileContent(projectId, path)
                      .then((content) => transformApi.createFile(projectId, {
                        path: next, content: content.content,
                        expected_revision_id: revisionId,
                      }))
                      .then(() => { invalidate(qk.transformFiles(workspaceId, projectId)); })
                      .catch(toastError);
                  }
                }}
                onDelete={(path) => {
                  if (window.confirm(`Xoá ${path}?`)) deleteFile.mutate(path);
                }}
                canEdit={canEdit}
              />
            )}
            {explorerView === 'resources' && (
              <ResourceTree
                resources={resources.data?.items ?? []}
                counts={resources.data?.counts ?? {}}
                total={resources.data?.total ?? 0}
                facets={facets.data}
                filters={filters}
                onFiltersChange={setFilters}
                activeUniqueId={inspectorId ?? activeResource?.unique_id ?? null}
                onSelect={(resource) => {
                  setInspectorId(resource.unique_id);
                  if (resource.path) void openFile(resource.path);
                }}
                loading={resources.isLoading}
                truncated={(resources.data?.total ?? 0) > (resources.data?.items.length ?? 0)}
              />
            )}
            {explorerView === 'git' && (
              <GitPanel
                status={gitStatus.data ?? null}
                branches={gitBranches.data ?? []}
                loading={gitStatus.isLoading}
                busy={gitPull.isPending || gitCommit.isPending || gitCheckout.isPending}
                canEdit={canEdit}
                onRefresh={() => gitStatus.refetch()}
                onPull={(discard) => gitPull.mutate(discard)}
                onCommit={(message, paths) => gitCommit.mutate({ message, paths })}
                onCheckout={(branch, discard) =>
                  gitCheckout.mutate({ branch, discardLocal: discard })}
                onOpenDiff={(path) => void openFile(path)}
              />
            )}
          </div>
        </div>

        <Resizer
          value={explorerWidth}
          onChange={setExplorerWidth}
          min={180}
          max={480}
          label="Thay đổi bề rộng cột trái"
        />

        {/* centre */}
        <div className="flex min-w-0 flex-1 flex-col">
          {centreView === 'lineage' ? (
            <LineageView
              graph={lineage.data ?? {
                nodes: [], edges: [], truncated: false, total_nodes: 0, scope: 'DRAFT',
              }}
              focusId={lineageFocus}
              loading={lineage.isLoading}
              showingFull={lineageFull}
              onShowFull={() => setLineageFull((value) => !value)}
              onSelect={(node) => {
                setLineageFocus(node.unique_id);
                setInspectorId(node.unique_id);
              }}
              onOpenFile={(path) => void openFile(path)}
            />
          ) : (
            <>
              <EditorTabs
                tabs={tabs}
                activePath={activePath}
                onSelect={(path) => { setActivePath(path); setErrorLine(null); }}
                onClose={(path) => {
                  const tab = tabs.find((item) => item.path === path);
                  if (tab?.dirty && !window.confirm(`${path} chưa lưu. Đóng và bỏ thay đổi?`)) {
                    return;
                  }
                  setTabs((current) => current.filter((item) => item.path !== path));
                  if (activePath === path) {
                    const remaining = tabs.filter((item) => item.path !== path);
                    setActivePath(remaining[remaining.length - 1]?.path ?? null);
                  }
                }}
                onCloseOthers={(path) => {
                  setTabs((current) => current.filter((item) => item.path === path));
                  setActivePath(path);
                }}
                onCloseAll={() => {
                  if (dirtyCount > 0 && !window.confirm('Có tệp chưa lưu. Đóng tất cả?')) return;
                  setTabs([]); setActivePath(null);
                }}
              />

              <div className="min-h-0 flex-1">
                {activePath ? (
                  <DbtFileEditor
                    key={activePath}
                    path={activePath}
                    value={buffers[activePath] ?? ''}
                    onChange={(value) => setBuffer(activePath, value)}
                    completions={completions.data}
                    readOnly={!canEdit}
                    errorLine={errorLine}
                    onSave={() => save.mutate(activePath)}
                    onSaveAll={() => saveAll.mutate()}
                    onPreview={() => saveThenRun({
                      command: 'show', selector: selectorFor(activeResource),
                    })}
                  />
                ) : (
                  <WelcomePane
                    projectName={detail.dbt_project_name}
                    parseStatus={detail.parse_status}
                    parseError={detail.parse_error}
                    onOpenProblems={() => setOutputTab('problems')}
                  />
                )}
              </div>
            </>
          )}

          <Resizer
            orientation="horizontal"
            value={outputHeight}
            onChange={setOutputHeight}
            min={100}
            max={560}
            label="Thay đổi chiều cao khung kết quả"
          />

          <div style={{ height: outputHeight }} className="shrink-0">
            <OutputPanel
              tab={outputTab}
              onTabChange={setOutputTab}
              invocation={invocation.data ?? null}
              problems={problems.data?.problems ?? []}
              parseStatus={detail.parse_status}
              compiled={compiled.data ?? null}
              logs={logs.data ?? null}
              loading={running}
              onOpenProblem={(problem: TransformProblem) => {
                if (problem.path) void openFile(problem.path, problem.line ?? undefined);
              }}
              onOpenResource={(uniqueId) => setInspectorId(uniqueId)}
            />
          </div>

          <CommandBar
            onRun={(command: ParsedCommand) => saveThenRun({
              command: command.command,
              selector: command.selector,
              exclude: command.exclude,
              fullRefresh: command.fullRefresh,
            })}
            running={running}
            onCancel={() => cancel.mutate()}
            resources={resources.data?.items ?? []}
            history={history}
            environmentName={environment?.name ?? null}
            environmentProtected={environment?.protected ?? false}
            disabled={!canEdit}
          />
        </div>

        {/* inspector */}
        {inspectorId && (
          <div className="w-80 shrink-0">
            <ResourceInspector
              resource={inspector.data ?? null}
              loading={inspector.isLoading}
              onClose={() => setInspectorId(null)}
              onOpenFile={(path, line) => void openFile(path, line)}
              onSelectResource={setInspectorId}
            />
          </div>
        )}
      </div>

      <NewFileDialog
        open={newFileOpen}
        parent={newFileParent}
        templates={templates.data ?? []}
        pending={createFile.isPending}
        onClose={() => setNewFileOpen(false)}
        onCreate={(path, template) => createFile.mutate({ path, template })}
      />

      <Modal
        open={Boolean(conflict)}
        onClose={() => setConflict(null)}
        title="Tệp đã bị người khác thay đổi"
        size="lg"
      >
        <div className="space-y-3">
          <p className="text-caption text-text-secondary">
            Trong lúc bạn đang sửa, có người khác đã lưu tệp này. Hãy so sánh
            rồi chọn cách xử lý — AppBI không tự quyết định thay bạn.
          </p>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <p className="mb-1 text-tiny uppercase text-text-quaternary">Bản của bạn</p>
              <pre className="max-h-64 overflow-auto rounded-md bg-surface-2 p-2 font-mono text-tiny text-text-secondary">
                {conflict?.mine}
              </pre>
            </div>
            <div>
              <p className="mb-1 text-tiny uppercase text-text-quaternary">Bản trên máy chủ</p>
              <pre className="max-h-64 overflow-auto rounded-md bg-surface-2 p-2 font-mono text-tiny text-text-secondary">
                {conflict?.server}
              </pre>
            </div>
          </div>
          <div className="flex justify-end gap-2">
            <Button
              variant="secondary"
              onClick={() => {
                if (activePath && conflict) {
                  setBuffers((current) => ({ ...current, [activePath]: conflict.server }));
                  setBaseline((current) => ({ ...current, [activePath]: conflict.server }));
                  setTabs((current) => current.map((tab) =>
                    tab.path === activePath ? { ...tab, dirty: false } : tab,
                  ));
                }
                setConflict(null);
                invalidate(qk.transform(workspaceId, projectId));
              }}
            >
              Lấy bản trên máy chủ
            </Button>
            <Button
              variant="primary"
              onClick={async () => {
                setConflict(null);
                await project.refetch();
                if (activePath) save.mutate(activePath);
              }}
            >
              Giữ bản của tôi và ghi đè
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}

function ParseBadge({ status, error }: { status: string; error: string | null }) {
  if (status === 'OK') {
    return <Badge variant="success" size="xs">Parse ✓</Badge>;
  }
  if (status === 'ERROR') {
    return (
      <Badge variant="danger" size="xs" title={error ?? undefined}>
        <TriangleAlert className="h-2.5 w-2.5" /> Dự án có lỗi
      </Badge>
    );
  }
  if (status === 'PENDING') {
    return (
      <Badge variant="subtle" size="xs">
        <Loader2 className="h-2.5 w-2.5 animate-spin" /> Đang đọc
      </Badge>
    );
  }
  return <Badge variant="subtle" size="xs">Chưa đọc</Badge>;
}

function WelcomePane({
  projectName, parseStatus, parseError, onOpenProblems,
}: {
  projectName: string | null;
  parseStatus: string;
  parseError: string | null;
  onOpenProblems: () => void;
}) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center">
      <FileCode className="h-8 w-8 text-text-quaternary" />
      <div>
        <p className="text-small text-text-secondary">
          {projectName ? (
            <>Dự án dbt <span className="font-mono">{projectName}</span></>
          ) : 'Dự án dbt'}
        </p>
        <p className="mt-1 text-caption text-text-tertiary">
          Chọn một tệp ở cột trái để bắt đầu.
        </p>
      </div>
      {parseStatus === 'ERROR' && parseError && (
        <button
          type="button"
          onClick={onOpenProblems}
          className="max-w-md rounded-md bg-danger/10 px-3 py-2 text-left text-caption text-danger hover:bg-danger/15"
        >
          <span className="flex items-center gap-1.5 font-emphasis">
            <TriangleAlert className="h-3.5 w-3.5" /> dbt chưa đọc được dự án
          </span>
          <span className="mt-1 block whitespace-pre-wrap text-tiny">{parseError}</span>
        </button>
      )}
    </div>
  );
}

function NewFileDialog({
  open, parent, templates, pending, onClose, onCreate,
}: {
  open: boolean;
  parent: string;
  templates: FileTemplate[];
  pending: boolean;
  onClose: () => void;
  onCreate: (path: string, template?: string) => void;
}) {
  const [path, setPath] = React.useState('');
  const [template, setTemplate] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (open) { setPath(parent ? `${parent}/` : ''); setTemplate(null); }
  }, [open, parent]);

  return (
    <Modal open={open} onClose={onClose} title="Tệp mới">
      <div className="space-y-3">
        <label className="block">
          <span className="mb-1 block text-caption text-text-secondary">Đường dẫn</span>
          <Input
            value={path}
            onChange={(event) => setPath(event.target.value)}
            placeholder="models/staging/stg_orders.sql"
            className="font-mono"
            autoFocus
          />
        </label>

        <div>
          <p className="mb-1.5 text-caption text-text-secondary">Bắt đầu từ mẫu</p>
          <div className="grid grid-cols-2 gap-1.5">
            {templates.map((item) => (
              <button
                key={item.key}
                type="button"
                onClick={() => {
                  setTemplate(item.key);
                  // Only fill the path if the person has not typed their own.
                  if (!path || path === (parent ? `${parent}/` : '')) setPath(item.path);
                }}
                className={cn(
                  'rounded-md border px-2 py-1.5 text-left text-caption transition-colors',
                  template === item.key
                    ? 'border-brand bg-brand/5 text-text-primary'
                    : 'border-[rgb(var(--border-line))] text-text-secondary hover:bg-surface-2',
                )}
              >
                {item.label}
              </button>
            ))}
          </div>
        </div>

        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>Huỷ</Button>
          <Button
            variant="primary"
            loading={pending}
            disabled={!path.trim()}
            onClick={() => onCreate(path.trim(), template ?? undefined)}
          >
            Tạo
          </Button>
        </div>
      </div>
    </Modal>
  );
}
