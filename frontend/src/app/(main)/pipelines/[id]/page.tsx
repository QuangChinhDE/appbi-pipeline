'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useParams, useRouter } from 'next/navigation';
import * as React from 'react';

import { ErrorRemediationCard, fromApiError, type RemediationInput }
  from '@/components/integrations/ErrorRemediationCard';
import { SchemaDiffViewer } from '@/components/integrations/SchemaDiffViewer';
import { DetailBody, Card } from '@/components/layout/PageLayout';
import { Button } from '@/components/ui/Button';
import { ErrorState, Spinner } from '@/components/ui/Feedback';
import { ConfirmDialog } from '@/components/ui/Modal';
import { Tabs } from '@/components/ui/Tabs';
import { useWorkspaceId } from '@/hooks/use-current-user';
import { useUrlTab } from '@/hooks/use-url-tab';
import { toastError, toastSuccess } from '@/hooks/use-toast';
import { pipelineApi, runApi } from '@/lib/api';
import { describeSchedule, formatDateTime } from '@/lib/format';
import { qk } from '@/lib/queryKeys';
import type { PipelineStreamView, StreamSelection } from '@/lib/types';
import { useI18n } from '@/providers/LanguageProvider';

import { ConnectionHeader } from './ConnectionHeader';
import { JobHistoryTab, OUTCOMES, type JobOutcomeFilter } from './JobHistoryTab';
import { SchemaTab } from './SchemaTab';
import { SettingsTab } from './SettingsTab';
import { StatusTab } from './StatusTab';
import { StreamDetailModal } from './StreamDetailModal';

const TABS = ['status', 'jobs', 'schema', 'settings'] as const;

export default function PipelineDetailPage() {
  const params = useParams<{ id: string }>();
  const pipelineId = params.id;
  const { t, locale } = useI18n();
  const router = useRouter();
  const queryClient = useQueryClient();
  const workspaceId = useWorkspaceId();

  const { tab, hrefForQuery, queryValue, setQuery } = useUrlTab(TABS, 'status');
  const [failure, setFailure] = React.useState<RemediationInput | null>(null);
  const [confirmDelete, setConfirmDelete] = React.useState(false);

  const { data: pipeline, isLoading, error, refetch } = useQuery({
    queryKey: qk.pipeline(workspaceId, pipelineId),
    queryFn: () => pipelineApi.detail(pipelineId),
    // Poll the product API while a run is active; never the engine.
    refetchInterval: (query) =>
      query.state.data?.health.code === 'RUNNING' ? 4_000 : 30_000,
  });

  // Job history is paged and filtered on the server. `limit` grows rather than
  // an offset cursor advancing, so a run that finishes between pages cannot
  // shift the window and hide a row -- the list is newest-first and shifting is
  // exactly how a paged view silently skips one.
  const requestedOutcome = queryValue('outcome')?.toUpperCase();
  const outcome: JobOutcomeFilter = OUTCOMES.includes(requestedOutcome as JobOutcomeFilter)
    ? requestedOutcome as JobOutcomeFilter
    : 'ALL';
  const [jobLimit, setJobLimit] = React.useState(50);
  React.useEffect(() => { setJobLimit(50); }, [outcome]);

  const runs = useQuery({
    queryKey: qk.runs(workspaceId, {
      pipeline_id: pipelineId, status: outcome, limit: jobLimit,
    }),
    queryFn: () => runApi.list({
      pipeline_id: pipelineId,
      status: outcome === 'ALL' ? undefined : outcome,
      limit: jobLimit,
    }),
    enabled: tab === 'jobs' || tab === 'status',
    placeholderData: (previous) => previous,
    refetchInterval: pipeline?.health.code === 'RUNNING' ? 5_000 : false,
  });

  const diff = useQuery({
    queryKey: qk.schemaDiff(workspaceId, pipelineId),
    queryFn: () => pipelineApi.schemaDiff(pipelineId),
    enabled: tab === 'schema',
  });

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ['workspace', workspaceId] });

  const runNow = useMutation({
    mutationFn: () => pipelineApi.run(pipelineId, crypto.randomUUID()),
    onSuccess: () => { setFailure(null); invalidate(); toastSuccess(t('pipelines.runQueued')); },
    onError: (caught) => { setFailure(fromApiError(caught)); toastError(caught); },
  });

  const toggleEnabled = useMutation({
    mutationFn: () => (pipeline?.status === 'PAUSED'
      ? pipelineApi.enable(pipelineId)
      : pipelineApi.pause(pipelineId)),
    onSuccess: invalidate,
    onError: (caught) => toastError(caught),
  });

  const rediscover = useMutation({
    mutationFn: () => pipelineApi.rediscover(pipelineId),
    onSuccess: () => { invalidate(); toastSuccess(t('pipelines.schemaRefreshed')); },
    onError: (caught) => toastError(caught),
  });

  const saveStreams = useMutation({
    mutationFn: (streams: StreamSelection[]) =>
      pipelineApi.update(pipelineId, { streams, version: pipeline?.version }),
    onSuccess: () => { invalidate(); toastSuccess(t('pipelines.streamsSaved')); },
    onError: (caught) => toastError(caught),
  });

  const remove = useMutation({
    mutationFn: () => pipelineApi.remove(pipelineId),
    onSuccess: () => { invalidate(); router.push('/pipelines'); },
    onError: (caught) => toastError(caught),
  });

  const approveSchema = useMutation({
    mutationFn: (snapshotId: string) => pipelineApi.approveSchema(pipelineId, snapshotId),
    onSuccess: () => { invalidate(); toastSuccess(t('pipelines.schemaApproved')); },
    onError: (caught) => toastError(caught),
  });

  if (isLoading) return <Spinner label={t('common.loading')} />;
  if (error) {
    return (
      <div className="p-6">
        <ErrorState title={t('common.errorTitle')} message={(error as Error).message}
                    onRetry={() => refetch()} />
      </div>
    );
  }
  if (!pipeline) return null;

  const streams = pipeline.streams;
  const openStream = streams.find((stream) => stream.id === queryValue('stream')) ?? null;

  /** Stream edits PATCH the whole list, so a single-stream change is expanded. */
  const patchOneStream = (target: PipelineStreamView, patch: Partial<StreamSelection>) => {
    saveStreams.mutate(streams.map((stream) => {
      const base: StreamSelection = {
        name: stream.name,
        namespace: stream.namespace,
        selected: stream.selected,
        sync_mode: stream.sync_mode as StreamSelection['sync_mode'],
        destination_sync_mode:
          stream.destination_sync_mode as StreamSelection['destination_sync_mode'],
        cursor_fields: stream.cursor_fields,
        primary_key_fields: stream.primary_key_fields,
        selected_fields: stream.selected_fields,
      };
      return stream.id === target.id ? { ...base, ...patch } : base;
    }));
  };

  const hasSchemaChanges = diff.data
    && (diff.data.added.length + diff.data.removed.length + diff.data.changed.length) > 0;

  return (
    <div>
      <ConnectionHeader
        pipeline={pipeline}
        scheduleLabel={describeSchedule(pipeline.schedule, t)}
        onSyncNow={() => runNow.mutate()}
        syncing={runNow.isPending || pipeline.health.code === 'RUNNING'}
        enabled={pipeline.status !== 'PAUSED'}
        onToggleEnabled={() => toggleEnabled.mutate()}
        toggling={toggleEnabled.isPending}
      />

      <div className="border-b border-[rgb(var(--border-line))] bg-surface-1 px-4 sm:px-6 xl:px-8">
        <Tabs
          value={tab}
          items={[
            {
              id: 'status', label: t('pipelines.tab.status'),
              href: hrefForQuery({ tab: 'status', stream: null }),
            },
            {
              id: 'jobs', label: t('pipelines.tab.jobs'),
              href: hrefForQuery({ tab: 'jobs', stream: null }),
            },
            {
              id: 'schema', label: t('pipelines.tab.schema'), count: pipeline.stream_count,
              href: hrefForQuery({ tab: 'schema', stream: null }),
            },
            {
              id: 'settings', label: t('pipelines.tab.settings'),
              href: hrefForQuery({ tab: 'settings', stream: null }),
            },
          ]}
        />
      </div>

      <DetailBody>
        {/* Anything broken comes before the tab body: that is what the reader
            came for, and a tab strip is not a place to hide it. */}
        {failure && (
          <div className="mb-4">
            <ErrorRemediationCard error={{ ...failure, onRetry: () => runNow.mutate() }} />
          </div>
        )}
        {pipeline.status === 'NEEDS_REVIEW' && (
          <div className="mb-4">
            <ErrorRemediationCard
              error={{
                code: 'PIPELINE_NEEDS_REVIEW',
                message: pipeline.needs_review_reason ?? t('pipelines.needsReviewDefault'),
                category: 'SCHEMA',
                affects: pipeline.name,
                action: 'REVIEW_SCHEMA',
                onAction: () => setQuery({ tab: 'schema', stream: null }),
              }}
            />
          </div>
        )}

        {tab === 'status' && (
          <StatusTab
            streams={streams}
            onOpenStream={(stream) => setQuery({ stream: stream.id })}
            onRefreshStream={() => rediscover.mutate()}
            onClearStream={(stream) => patchOneStream(stream, { selected: false })}
            onManageStreams={() => setQuery({ tab: 'schema', stream: null })}
          />
        )}

        {tab === 'jobs' && (
          <JobHistoryTab
            runs={runs.data?.items ?? []}
            total={runs.data?.page.total ?? 0}
            isLoading={runs.isLoading}
            loadingMore={runs.isFetching && !runs.isLoading}
            hasMore={(runs.data?.items.length ?? 0) < (runs.data?.page.total ?? 0)}
            outcome={outcome}
            metrics={pipeline.metrics}
            syncing={runNow.isPending || pipeline.health.code === 'RUNNING'}
            onSyncNow={() => runNow.mutate()}
            onOutcome={(value) => setQuery({
              tab: 'jobs', outcome: value === 'ALL' ? null : value,
            }, { replace: true })}
            onLoadMore={() => setJobLimit((value) => value + 50)}
          />
        )}

        {tab === 'schema' && (
          <div className="space-y-4">
            {hasSchemaChanges && diff.data?.to_snapshot_id && (
              <Card
                title={t('pipelines.schemaChanges')}
                description={pipeline.schema_snapshot_at
                  ? t('pipelines.schemaCurrentAt', {
                      time: formatDateTime(pipeline.schema_snapshot_at, locale) })
                  : undefined}
                action={
                  <Button
                    size="xs"
                    variant="primary"
                    loading={approveSchema.isPending}
                    onClick={() => approveSchema.mutate(diff.data!.to_snapshot_id!)}
                  >
                    {t('pipelines.approveSchema')}
                  </Button>
                }
              >
                <SchemaDiffViewer diff={diff.data} />
              </Card>
            )}
            <SchemaTab
              streams={streams}
              saving={saveStreams.isPending}
              onChange={(next) => saveStreams.mutate(next)}
              onOpenStream={(stream) => setQuery({ stream: stream.id })}
              onRefreshSchema={() => rediscover.mutate()}
              refreshing={rediscover.isPending}
            />
          </div>
        )}

        {tab === 'settings' && (
          <SettingsTab pipeline={pipeline} onDelete={() => setConfirmDelete(true)} />
        )}
      </DetailBody>

      <StreamDetailModal
        stream={openStream}
        open={openStream !== null}
        saving={saveStreams.isPending}
        onClose={() => setQuery({ stream: null }, { replace: true })}
        onChange={(patch) => { if (openStream) patchOneStream(openStream, patch); }}
      />

      <ConfirmDialog
        open={confirmDelete}
        onClose={() => setConfirmDelete(false)}
        onConfirm={() => remove.mutate()}
        loading={remove.isPending}
        destructive
        title={t('pipelines.deleteTitle')}
        confirmLabel={t('common.delete')}
        message={t('pipelines.deleteBody', { name: pipeline.name })}
      />
    </div>
  );
}
