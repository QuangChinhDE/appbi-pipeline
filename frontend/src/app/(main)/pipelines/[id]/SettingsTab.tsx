'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Copy, Save, Trash2 } from 'lucide-react';
import * as React from 'react';

import { Card } from '@/components/layout/PageLayout';
import { HealthBadge } from '@/components/integrations/Badges';
import { ScheduleEditor } from '@/components/integrations/ScheduleEditor';
import { Button } from '@/components/ui/Button';
import { usePermissions } from '@/hooks/use-permissions';
import { Disclosure } from '@/components/ui/Disclosure';
import { Spinner } from '@/components/ui/Feedback';
import { ConfirmDialog } from '@/components/ui/Modal';
import { Input, Select } from '@/components/ui/Input';
import { toastError, toastSuccess } from '@/hooks/use-toast';
import { useWorkspaceId } from '@/hooks/use-current-user';
import { pipelineApi } from '@/lib/api';
import { describeSchedule, formatDateTime } from '@/lib/format';
import { qk } from '@/lib/queryKeys';
import type { PipelineDetail, ScheduleConfig } from '@/lib/types';
import { useI18n } from '@/providers/LanguageProvider';

/**
 * One row per decision: what it is on the left, the control on the right.
 *
 * The previous settings page was a definition list of values you could read
 * but not change, plus a schedule editor. Everything a person came here to
 * change — the name, where the tables land, what they are called — was
 * elsewhere or nowhere.
 */
function Row({
  label, description, children,
}: {
  label: string;
  description?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-2 py-3.5 sm:flex-row sm:items-start sm:gap-6">
      <div className="sm:w-[46%] sm:flex-shrink-0">
        <p className="text-caption font-strong text-text-primary">{label}</p>
        {description && (
          <p className="mt-0.5 text-tiny leading-relaxed text-text-tertiary">{description}</p>
        )}
      </div>
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}

export function SettingsTab({
  pipeline, onDelete,
}: {
  pipeline: PipelineDetail;
  onDelete: () => void;
}) {
  const { t, locale } = useI18n();
  const workspaceId = useWorkspaceId();
  const queryClient = useQueryClient();

  const [name, setName] = React.useState(pipeline.name);
  const [schedule, setSchedule] = React.useState<ScheduleConfig>(pipeline.schedule);
  const [namespace, setNamespace] = React.useState(pipeline.namespace_format ?? '');
  const [prefix, setPrefix] = React.useState(pipeline.stream_prefix ?? '');
  const [overlap, setOverlap] = React.useState(pipeline.overlap_policy);

  // The form is seeded from the server and the server is polled, so a save
  // elsewhere would otherwise silently overwrite what is being typed here.
  // Re-seed only when the version moves, i.e. when the record really changed.
  const version = pipeline.version;
  React.useEffect(() => {
    setName(pipeline.name);
    setSchedule(pipeline.schedule);
    setNamespace(pipeline.namespace_format ?? '');
    setPrefix(pipeline.stream_prefix ?? '');
    setOverlap(pipeline.overlap_policy);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [version]);

  const dirty =
    name !== pipeline.name
    || namespace !== (pipeline.namespace_format ?? '')
    || prefix !== (pipeline.stream_prefix ?? '')
    || overlap !== pipeline.overlap_policy
    || JSON.stringify(schedule) !== JSON.stringify(pipeline.schedule);

  const save = useMutation({
    mutationFn: () => pipelineApi.update(pipeline.id, {
      name,
      schedule,
      namespace_format: namespace || null,
      stream_prefix: prefix || null,
      overlap_policy: overlap,
      version: pipeline.version,
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['workspace', workspaceId] });
      toastSuccess(t('pipelines.settingsSaved'));
    },
    onError: (caught) => toastError(caught),
  });

  const reset = () => {
    setName(pipeline.name);
    setSchedule(pipeline.schedule);
    setNamespace(pipeline.namespace_format ?? '');
    setPrefix(pipeline.stream_prefix ?? '');
    setOverlap(pipeline.overlap_policy);
  };

  return (
    <div className="space-y-4">
      <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(280px,1fr)]">
        <Card title={t('pipelines.settings.title')}>
        <div className="divide-y divide-[rgb(var(--border-line))]">
          <Row
            label={t('pipelines.settings.name')}
            description={t('pipelines.settings.nameHelp')}
          >
            <Input value={name} onChange={(event) => setName(event.target.value)} />
          </Row>

          <Row
            label={t('pipelines.settings.schedule')}
            description={t('pipelines.settings.scheduleHelp')}
          >
            <ScheduleEditor value={schedule} onChange={setSchedule} />
          </Row>

          <div className="py-3.5">
            <Disclosure label={t('pipelines.settings.advanced')}>
              <div className="divide-y divide-[rgb(var(--border-line))]">
                <Row
                  label={t('pipelines.settings.namespace')}
                  description={t('pipelines.settings.namespaceHelp')}
                >
                  <Input
                    value={namespace}
                    placeholder={t('pipelines.settings.namespacePlaceholder')}
                    onChange={(event) => setNamespace(event.target.value)}
                  />
                </Row>
                <Row
                  label={t('pipelines.settings.prefix')}
                  description={t('pipelines.settings.prefixHelp')}
                >
                  <Input
                    value={prefix}
                    placeholder={t('pipelines.settings.prefixPlaceholder')}
                    onChange={(event) => setPrefix(event.target.value)}
                  />
                </Row>
                <Row
                  label={t('pipelines.settings.overlap')}
                  description={t('pipelines.settings.overlapHelp')}
                >
                  <Select
                    value={overlap}
                    onChange={(event) => setOverlap(event.target.value)}
                  >
                    <option value="SKIP_IF_RUNNING">{t('pipelines.overlapSkip')}</option>
                    <option value="QUEUE">{t('pipelines.overlapQueue')}</option>
                  </Select>
                </Row>
              </div>
            </Disclosure>
          </div>
        </div>

        <div className="mt-4 flex items-center justify-end gap-2 border-t border-[rgb(var(--border-line))] pt-4">
          <Button variant="ghost" disabled={!dirty} onClick={reset}>
            {t('common.cancel')}
          </Button>
          <Button
            variant="primary"
            disabled={!dirty}
            loading={save.isPending}
            onClick={() => save.mutate()}
            leadingIcon={<Save className="h-3.5 w-3.5" />}
          >
            {t('common.save')}
          </Button>
        </div>
        </Card>

        <div className="space-y-4">
          <Card title={t('pipelines.settings.summary')}>
            <dl className="space-y-3">
              <SummaryRow
                label={t('common.status')}
                value={<HealthBadge health={pipeline.health} size="xs" />}
              />
              <SummaryRow label={t('pipelines.reviewSource')} value={pipeline.source.name} />
              <SummaryRow
                label={t('pipelines.reviewDestination')}
                value={pipeline.destination.name}
              />
              <SummaryRow
                label={t('pipelines.settings.streams')}
                value={String(pipeline.streams.filter((stream) => stream.selected).length)}
              />
              <SummaryRow
                label={t('pipelines.settings.currentSchedule')}
                value={describeSchedule(pipeline.schedule, t)}
              />
            </dl>
          </Card>

          <Card title={t('pipelines.settings.dangerZone')}>
            <p className="text-caption font-strong text-text-primary">
              {t('pipelines.settings.delete')}
            </p>
            <p className="mt-1 text-tiny leading-relaxed text-text-tertiary">
              {t('pipelines.settings.deleteHelp')}
            </p>
            <Button
              className="mt-3"
              variant="danger"
              onClick={onDelete}
              leadingIcon={<Trash2 className="h-3.5 w-3.5" />}
            >
              {t('pipelines.settings.deleteAction')}
            </Button>
          </Card>
        </div>
      </div>

      <ConnectionStatePanel
        pipelineId={pipeline.id}
        locale={locale}
        running={pipeline.health.code === 'RUNNING'}
      />
    </div>
  );
}

function SummaryRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4">
      <dt className="text-caption text-text-tertiary">{label}</dt>
      <dd className="max-w-[60%] text-right text-caption font-emphasis text-text-primary">
        {value}
      </dd>
    </div>
  );
}

/**
 * The cursor the next incremental sync resumes from — readable and editable.
 *
 * Collapsed and fetched on open. It answers one question — "why did this sync
 * return nothing" — and answering it costs a call to the engine, which is
 * exactly the thing that may be unwell when someone comes looking.
 *
 * Editing is the point, not a bonus. The reason to open this panel is usually
 * that the cursor is *wrong*: a source that back-dated records, a botched
 * backfill, a stream that has to be re-read from a known mark. Read-only, the
 * only remedy left is a full refresh of everything.
 *
 * So it is a real editor, guarded rather than withheld: the JSON is validated
 * before the button enables, saving asks first, and the server refuses while a
 * run is active — that run commits its own copy at the end and would overwrite
 * the edit with no error anywhere.
 */
function ConnectionStatePanel({
  pipelineId, locale, running,
}: {
  pipelineId: string;
  locale: string;
  running: boolean;
}) {
  const { t } = useI18n();
  const { can } = usePermissions();
  // Rewinding the cursor re-delivers history into the warehouse, so it sits
  // behind `reset` rather than `operate`. Without this the panel offered a Save
  // button to every role and answered 403 when it was pressed.
  const canReset = can('pipelines', 'reset');
  const queryClient = useQueryClient();
  const [enabled, setEnabled] = React.useState(false);
  const [draft, setDraft] = React.useState<string | null>(null);
  const [confirming, setConfirming] = React.useState(false);

  const state = useQuery({
    queryKey: qk.pipelineState(pipelineId),
    queryFn: () => pipelineApi.state(pipelineId),
    enabled,
    staleTime: 30_000,
  });

  const server = JSON.stringify(state.data?.state ?? [], null, 2);
  const body = draft ?? server;

  /** Parse once, and use the same result for validity and for sending. */
  const parsed = React.useMemo<{ ok: true; value: Record<string, unknown>[] }
  | { ok: false; message: string }>(() => {
    try {
      const value = JSON.parse(body);
      if (!Array.isArray(value)) return { ok: false, message: t('pipelines.settings.stateNotArray') };
      if (value.some((entry) => entry === null || typeof entry !== 'object' || Array.isArray(entry))) {
        return { ok: false, message: t('pipelines.settings.stateNotObjects') };
      }
      return { ok: true, value: value as Record<string, unknown>[] };
    } catch (caught) {
      return { ok: false, message: (caught as Error).message };
    }
  }, [body, t]);

  const dirty = draft !== null && draft !== server;

  const save = useMutation({
    mutationFn: () => pipelineApi.setState(
      pipelineId, parsed.ok ? parsed.value : [],
    ),
    onSuccess: (result) => {
      // The endpoint answers with what the engine *holds*, not what we sent.
      // Airbyte drops keys it does not recognise inside a stream entry, so a
      // save can succeed while quietly discarding part of the edit; saying
      // "saved" and showing different text would leave the reader to notice
      // that on their own.
      const sent = JSON.stringify(parsed.ok ? parsed.value : []);
      const kept = JSON.stringify(result.state);
      setDraft(null);
      setConfirming(false);
      void queryClient.invalidateQueries({ queryKey: qk.pipelineState(pipelineId) });
      if (sent !== kept) toastError(new Error(t('pipelines.settings.stateNormalised')));
      else toastSuccess(t('pipelines.settings.stateSaved'));
    },
    onError: (caught) => { setConfirming(false); toastError(caught); },
  });

  return (
    <Card>
      <Disclosure
        label={t('pipelines.settings.connectionState')}
        description={t('pipelines.settings.connectionStateHelp')}
        onOpen={() => setEnabled(true)}
      >
        {state.isLoading ? (
          <Spinner label={t('common.loading')} />
        ) : state.data?.unavailable_reason ? (
          <p className="text-caption text-warning">{state.data.unavailable_reason}</p>
        ) : state.data && !state.data.supported ? (
          <p className="text-caption text-text-tertiary">
            {t('pipelines.settings.stateUnsupported')}
          </p>
        ) : (
          <div className="space-y-2">
            <div className="relative">
              <Button
                size="xs"
                variant="ghost"
                className="absolute right-2 top-2 z-10"
                onClick={() => {
                  void navigator.clipboard?.writeText(body).then(
                    () => toastSuccess(t('pipelines.settings.stateCopied')),
                    () => undefined,
                  );
                }}
                leadingIcon={<Copy className="h-3 w-3" />}
              >
                {t('common.copy')}
              </Button>
              <textarea
                value={body}
                spellCheck={false}
                readOnly={!canReset}
                aria-label={t('pipelines.settings.connectionState')}
                onChange={(event) => setDraft(event.target.value)}
                rows={Math.min(20, Math.max(6, body.split(String.fromCharCode(10)).length))}
                className="w-full resize-y rounded-lg border border-[rgb(var(--border-line))] bg-surface-0 p-3 pr-20 font-mono text-tiny leading-relaxed text-text-secondary focus:border-brand focus:outline-none"
              />
            </div>

            {state.data?.state.length === 0 && !dirty && (
              <p className="text-tiny text-text-tertiary">
                {t('pipelines.settings.stateEmpty')}
              </p>
            )}
            {!parsed.ok && (
              <p className="text-tiny text-danger">
                {t('pipelines.settings.stateInvalid', { error: parsed.message })}
              </p>
            )}
            {running && (
              <p className="text-tiny text-warning">
                {t('pipelines.settings.stateRunning')}
              </p>
            )}

            <div className="flex items-center gap-2">
              {canReset && (
                <>
                  <Button
                    size="sm"
                    variant="danger"
                    disabled={!dirty || !parsed.ok || running}
                    loading={save.isPending}
                    onClick={() => setConfirming(true)}
                    leadingIcon={<Save className="h-3.5 w-3.5" />}
                  >
                    {t('pipelines.settings.stateSave')}
                  </Button>
                  <Button size="sm" variant="ghost" disabled={!dirty}
                          onClick={() => setDraft(null)}>
                    {t('common.cancel')}
                  </Button>
                </>
              )}
              {state.data?.fetched_at && !dirty && (
                <span className="text-tiny text-text-quaternary">
                  {t('pipelines.settings.stateFetched', {
                    time: formatDateTime(state.data.fetched_at, locale as never),
                  })}
                </span>
              )}
            </div>
          </div>
        )}
        <p className="mt-2 text-tiny text-warning">
          {t('pipelines.settings.stateWarning')}
        </p>
      </Disclosure>

      <ConfirmDialog
        open={confirming}
        onClose={() => setConfirming(false)}
        onConfirm={() => save.mutate()}
        loading={save.isPending}
        destructive
        title={t('pipelines.settings.stateConfirmTitle')}
        confirmLabel={t('pipelines.settings.stateSave')}
        message={t('pipelines.settings.stateConfirmBody')}
      />
    </Card>
  );
}
