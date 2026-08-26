'use client';

import * as React from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, ArrowLeft, ArrowRight, Database, Lock } from 'lucide-react';

import { destinationApi, pipelineApi, sourceApi } from '@/lib/api';
import { qk } from '@/lib/queryKeys';
import { describeSchedule, formatDateTime } from '@/lib/format';
import { useWorkspaceId } from '@/hooks/use-current-user';
import { toastSuccess } from '@/hooks/use-toast';
import { useI18n } from '@/providers/LanguageProvider';
import { Button } from '@/components/ui/Button';
import { Checkbox, Input, Label, Select, Textarea } from '@/components/ui/Input';
import { EmptyState, Spinner } from '@/components/ui/Feedback';
import { usePermissions } from '@/hooks/use-permissions';
import { Card } from '@/components/layout/PageLayout';
import { ConnectorIcon } from '@/components/integrations/ConnectorIcon';
import {
  ErrorRemediationCard, fromApiError, type RemediationInput,
} from '@/components/integrations/ErrorRemediationCard';
import { ScheduleEditor } from '@/components/integrations/ScheduleEditor';
import { Stepper, WizardFooter } from '@/components/integrations/Stepper';
import {
  StreamSelector, buildInitialSelections, streamKey, validateSelections,
} from '@/components/integrations/StreamSelector';
import { useSyncModeLabel } from '@/components/integrations/Badges';
import type { ScheduleConfig, StreamSelection } from '@/lib/types';

export default function NewPipelinePage() {
  const { t, locale } = useI18n();
  const router = useRouter();
  const queryClient = useQueryClient();
  const workspaceId = useWorkspaceId();
  const { can } = usePermissions();
  const syncModeLabel = useSyncModeLabel();
  // A read-only role must not be walked through a wizard that will 403 on save.
  const canCreate = can('pipelines', 'create');

  const [step, setStep] = React.useState(0);
  const [name, setName] = React.useState('');
  const [description, setDescription] = React.useState('');
  // Prefilled from the journey that got here. Arriving from
  // `/destinations/new?source=<id>` the user has just created both halves;
  // asking them to pick the two things they made thirty seconds ago from two
  // dropdowns is the seam that made the flow feel like three separate tools.
  const searchParams = useSearchParams();
  const [sourceId, setSourceId] = React.useState(searchParams.get('source') ?? '');
  const [destinationId, setDestinationId] =
    React.useState(searchParams.get('destination') ?? '');
  const [selections, setSelections] = React.useState<Record<string, StreamSelection>>({});
  const [schedule, setSchedule] = React.useState<ScheduleConfig>({
    type: 'DAILY', time_of_day: '02:00', timezone: 'Asia/Bangkok',
  });
  const [runFirst, setRunFirst] = React.useState(true);
  const [streamPrefix, setStreamPrefix] = React.useState('');
  const [namespaceFormat, setNamespaceFormat] = React.useState('');
  const [overlapPolicy, setOverlapPolicy] = React.useState<'SKIP_IF_RUNNING' | 'QUEUE'>(
    'SKIP_IF_RUNNING',
  );
  const [failure, setFailure] = React.useState<RemediationInput | null>(null);
  const [problems, setProblems] = React.useState<string[]>([]);

  const sources = useQuery({
    queryKey: qk.sources(workspaceId, { status: 'ACTIVE' }),
    queryFn: () => sourceApi.list({ status: 'ACTIVE', limit: 200 }),
  });
  const destinations = useQuery({
    queryKey: qk.destinations(workspaceId, { status: 'ACTIVE' }),
    queryFn: () => destinationApi.list({ status: 'ACTIVE', limit: 200 }),
  });

  const destinationDetail = useQuery({
    queryKey: qk.destination(workspaceId, destinationId),
    queryFn: () => destinationApi.detail(destinationId),
    enabled: Boolean(destinationId),
  });
  const destinationConnector = useQuery({
    queryKey: qk.connector(workspaceId, destinationDetail.data?.connector_key ?? ''),
    queryFn: () => import('@/lib/api').then((m) =>
      m.connectorApi.detail(destinationDetail.data!.connector_key)),
    enabled: Boolean(destinationDetail.data?.connector_key),
  });
  const destinationModes =
    destinationConnector.data?.supported_destination_sync_modes?.length
      ? destinationConnector.data.supported_destination_sync_modes
      : ['overwrite', 'append'];

  // Discovery is triggered explicitly when entering step 2 so the user is never
  // surprised by a slow connector call.
  const discover = useMutation({
    mutationFn: () => sourceApi.discover(sourceId),
    onSuccess: (snapshot) => {
      setSelections(buildInitialSelections(snapshot.streams, destinationModes));
      setFailure(null);
    },
    onError: (caught) => setFailure(fromApiError(caught)),
  });

  const create = useMutation({
    mutationFn: () => {
      const chosen = Object.values(selections).filter((s) => s.selected);
      return pipelineApi.create({
        name: name.trim(),
        description: description || null,
        source_id: sourceId,
        destination_id: destinationId,
        schema_snapshot_id: discover.data?.id,
        streams: chosen,
        schedule,
        overlap_policy: overlapPolicy,
        namespace_format: namespaceFormat.trim() || null,
        stream_prefix: streamPrefix.trim() || null,
        run_first_sync: runFirst,
      });
    },
    onSuccess: (pipeline) => {
      queryClient.invalidateQueries({ queryKey: ['workspace', workspaceId] });
      toastSuccess(t('pipelines.created'), pipeline.name);
      router.push(`/pipelines/${pipeline.id}`);
    },
    onError: (caught) => setFailure(fromApiError(caught, name)),
  });

  const steps = [
    { id: 'basics', label: t('wizard.step.basics') },
    { id: 'data', label: t('wizard.step.data') },
    { id: 'schedule', label: t('wizard.step.schedule') },
    { id: 'review', label: t('wizard.step.review') },
  ];

  const streams = discover.data?.streams ?? [];
  const selectedList = Object.values(selections).filter((s) => s.selected);

  const goNext = () => {
    setFailure(null);
    if (step === 0) {
      if (!name.trim() || !sourceId || !destinationId) {
        setProblems([t('pipelines.fillBasics')]);
        return;
      }
      setProblems([]);
      setStep(1);
      if (!discover.data) discover.mutate();
      return;
    }
    if (step === 1) {
      const found = validateSelections(streams, selections, t);
      setProblems(found);
      if (found.length > 0) return;
      setStep(2);
      return;
    }
    if (step === 2) {
      setProblems([]);
      setStep(3);
      return;
    }
    create.mutate();
  };

  const sourceRow = sources.data?.items.find((s) => s.id === sourceId);
  const destinationRow = destinations.data?.items.find((d) => d.id === destinationId);

  const noActors =
    !sources.isLoading && !destinations.isLoading &&
    ((sources.data?.items.length ?? 0) === 0 || (destinations.data?.items.length ?? 0) === 0);

  return (
    <div className="px-4 py-6 sm:px-6 xl:px-8">
      <Link
        href="/pipelines"
        className="-ml-1 mb-2 inline-flex items-center gap-1 rounded-md px-1 py-1.5 text-caption text-text-tertiary transition-colors hover:text-text-primary"
      >
        <ArrowLeft className="h-3.5 w-3.5" />
        {t('pipelines.title')}
      </Link>

      <h1 className="text-h3 font-strong text-text-primary">{t('pipelines.add')}</h1>
      <p className="mt-1 text-caption text-text-tertiary">{t('pipelines.addSubtitle')}</p>

      {canCreate && (
        <div className="my-5">
          <Stepper steps={steps} current={step} onStepClick={(index) =>
            index < step && setStep(index)} />
        </div>
      )}

      {!canCreate ? (
        <div className="max-w-2xl">
          <EmptyState
            icon={Lock}
            title={t('wizard.noPermissionTitle')}
            description={t('wizard.noPermissionBody')}
            action={
              <Link href="/pipelines">
                <Button variant="secondary">{t('common.back')}</Button>
              </Link>
            }
          />
        </div>
      ) : noActors ? (
        <EmptyState
          icon={Database}
          title={t('pipelines.needSourceAndDestination')}
          description={t('pipelines.needSourceAndDestinationBody')}
          action={
            <div className="flex gap-2">
              <Link href="/sources/new"><Button variant="primary">{t('sources.add')}</Button></Link>
              <Link href="/destinations/new"><Button variant="secondary">{t('destinations.add')}</Button></Link>
            </div>
          }
        />
      ) : (
        <div className="max-w-5xl space-y-4">
          {failure && (
            <ErrorRemediationCard
              error={{ ...failure, onRetry: step === 1 ? () => discover.mutate() : undefined }}
            />
          )}
          {problems.length > 0 && (
            <div className="rounded-lg border border-warning/30 bg-warning/5 p-3">
              <p className="flex items-center gap-1.5 text-caption font-emphasis text-text-primary">
                <AlertTriangle className="h-3.5 w-3.5 text-warning" />
                {t('pipelines.fixBeforeContinue')}
              </p>
              <ul className="mt-1 list-inside list-disc text-caption text-text-secondary">
                {problems.map((problem) => <li key={problem}>{problem}</li>)}
              </ul>
            </div>
          )}

          {step === 0 && (
            <Card title={t('wizard.step.basics')}>
              <div className="space-y-4">
                <div>
                  <Label htmlFor="pl-name" required>{t('pipelines.nameLabel')}</Label>
                  <Input
                    id="pl-name"
                    value={name}
                    placeholder="Orders to Warehouse"
                    onChange={(event) => setName(event.target.value)}
                  />
                </div>
                <div className="grid gap-4 sm:grid-cols-2">
                  <div>
                    <Label htmlFor="pl-source" required>{t('pipelines.sourceLabel')}</Label>
                    <Select
                      id="pl-source"
                      value={sourceId}
                      onChange={(event) => {
                        setSourceId(event.target.value);
                        setSelections({});
                        discover.reset();
                      }}
                    >
                      <option value="">{t('pipelines.chooseSource')}</option>
                      {(sources.data?.items ?? []).map((source) => (
                        <option key={source.id} value={source.id}>
                          {source.name} ({source.connector_display_name})
                        </option>
                      ))}
                    </Select>
                  </div>
                  <div>
                    <Label htmlFor="pl-destination" required>
                      {t('pipelines.destinationLabel')}
                    </Label>
                    <Select
                      id="pl-destination"
                      value={destinationId}
                      onChange={(event) => setDestinationId(event.target.value)}
                    >
                      <option value="">{t('pipelines.chooseDestination')}</option>
                      {(destinations.data?.items ?? []).map((destination) => (
                        <option key={destination.id} value={destination.id}>
                          {destination.name} ({destination.connector_display_name})
                        </option>
                      ))}
                    </Select>
                  </div>
                </div>
                <div>
                  <Label htmlFor="pl-desc" hint={t('common.optional')}>{t('common.description')}</Label>
                  <Textarea id="pl-desc" rows={2} value={description}
                            onChange={(event) => setDescription(event.target.value)} />
                </div>
              </div>
            </Card>
          )}

          {step === 1 && (
            discover.isPending ? (
              <Card>
                <Spinner label={t('pipelines.discovering')} />
              </Card>
            ) : streams.length === 0 ? (
              <EmptyState
                title={t('pipelines.discoverFailedTitle')}
                description={t('pipelines.discoverFailedBody')}
                action={<Button variant="primary" onClick={() => discover.mutate()}>
                  {t('pipelines.discoverRetry')}
                </Button>}
              />
            ) : (
              <Card
                title={t('pipelines.selectData', { n: streams.length })}
                description={t('pipelines.snapshotAt', {
                  time: formatDateTime(discover.data?.discovered_at, locale) })}
                action={
                  <Button size="xs" variant="ghost" onClick={() => discover.mutate()}>
                    {t('common.refresh')}
                  </Button>
                }
              >
                <StreamSelector
                  streams={streams}
                  selections={selections}
                  onChange={setSelections}
                  destinationModes={destinationModes}
                />
              </Card>
            )
          )}

          {step === 2 && (
            <div className="space-y-4">
              <Card title={t('pipelines.schedule')}>
                <ScheduleEditor value={schedule} onChange={setSchedule} />
              </Card>
              <Card title={t('pipelines.destinationOptions')}>
                <div className="grid gap-4 sm:grid-cols-2">
                  <div>
                    <Label htmlFor="pl-prefix" hint={t('common.optional')}>
                      {t('pipelines.prefix')}
                    </Label>
                    <Input
                      id="pl-prefix"
                      value={streamPrefix}
                      placeholder="base_"
                      onChange={(event) => setStreamPrefix(event.target.value)}
                    />
                  </div>
                  <div>
                    <Label htmlFor="pl-namespace" hint={t('common.optional')}>
                      {t('pipelines.namespaceFormat')}
                    </Label>
                    <Input
                      id="pl-namespace"
                      value={namespaceFormat}
                      placeholder="${SOURCE_NAMESPACE}"
                      onChange={(event) => setNamespaceFormat(event.target.value)}
                    />
                  </div>
                  <div className="sm:col-span-2">
                    <Label htmlFor="pl-overlap">{t('pipelines.overlapLabel')}</Label>
                    <Select
                      id="pl-overlap"
                      value={overlapPolicy}
                      onChange={(event) => setOverlapPolicy(
                        event.target.value as 'SKIP_IF_RUNNING' | 'QUEUE',
                      )}
                    >
                      <option value="SKIP_IF_RUNNING">{t('pipelines.overlapSkip')}</option>
                      <option value="QUEUE">{t('pipelines.overlapQueue')}</option>
                    </Select>
                  </div>
                </div>
              </Card>
            </div>
          )}

          {step === 3 && (
            <div className="space-y-4">
              <Card title={t('pipelines.reviewTitle')}>
                <div className="grid gap-4 sm:grid-cols-2">
                  <div className="flex items-center gap-2.5">
                    <ConnectorIcon icon={sourceRow?.connector_icon} connectorKey={sourceRow?.connector_key} size="md" />
                    <div className="min-w-0">
                      <p className="text-tiny uppercase tracking-[0.08em] text-text-quaternary">
                        {t('pipelines.reviewSource')}
                      </p>
                      <p className="truncate text-caption font-emphasis text-text-primary">
                        {sourceRow?.name}
                      </p>
                    </div>
                    <ArrowRight className="mx-1 h-4 w-4 flex-shrink-0 text-text-quaternary" />
                    <ConnectorIcon icon={destinationRow?.connector_icon} connectorKey={destinationRow?.connector_key} size="md" />
                    <div className="min-w-0">
                      <p className="text-tiny uppercase tracking-[0.08em] text-text-quaternary">
                        {t('pipelines.reviewDestination')}
                      </p>
                      <p className="truncate text-caption font-emphasis text-text-primary">
                        {destinationRow?.name}
                      </p>
                    </div>
                  </div>
                  <dl className="space-y-1.5">
                    <div className="flex justify-between gap-3">
                      <dt className="text-caption text-text-tertiary">
                        {t('pipelines.reviewStreams')}
                      </dt>
                      <dd className="text-caption text-text-primary">{selectedList.length}</dd>
                    </div>
                    <div className="flex justify-between gap-3">
                      <dt className="text-caption text-text-tertiary">
                        {t('pipelines.reviewSchedule')}
                      </dt>
                      <dd className="text-caption text-text-primary">
                        {describeSchedule(schedule, t)}
                      </dd>
                    </div>
                    <div className="flex justify-between gap-3">
                      <dt className="text-caption text-text-tertiary">
                        {t('pipelines.reviewTimezone')}
                      </dt>
                      <dd className="text-caption text-text-primary">{schedule.timezone}</dd>
                    </div>
                    <div className="flex justify-between gap-3">
                      <dt className="text-caption text-text-tertiary">
                        {t('pipelines.prefix')}
                      </dt>
                      <dd className="text-caption text-text-primary">{streamPrefix || '—'}</dd>
                    </div>
                  </dl>
                </div>
              </Card>

              <Card title={t('pipelines.reviewData')} padded={false}>
                <div className="max-h-64 overflow-y-auto">
                  <table className="w-full text-left">
                    <thead className="sticky top-0 bg-surface-1">
                      <tr className="border-b border-[rgb(var(--border-line))] text-tiny uppercase tracking-[0.08em] text-text-quaternary">
                        <th scope="col" className="px-4 py-2 font-emphasis">
                          {t('stream.colName')}
                        </th>
                        <th scope="col" className="px-3 py-2 font-emphasis">
                          {t('stream.colRead')}
                        </th>
                        <th scope="col" className="px-3 py-2 font-emphasis">
                          {t('stream.colWrite')}
                        </th>
                        <th scope="col" className="px-3 py-2 font-emphasis">
                          {t('stream.colCursor')} / {t('stream.colPk')}
                        </th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-[rgb(var(--border-line))]">
                      {selectedList.map((selection) => (
                        <tr key={streamKey(selection.namespace, selection.name)}>
                          <td className="px-4 py-2 text-caption text-text-primary">
                            {selection.namespace ? `${selection.namespace}.` : ''}{selection.name}
                          </td>
                          <td className="px-3 py-2 text-caption text-text-secondary">
                            {syncModeLabel(selection.sync_mode)}
                          </td>
                          <td className="px-3 py-2 text-caption text-text-secondary">
                            {syncModeLabel(selection.destination_sync_mode)}
                          </td>
                          <td className="px-3 py-2 text-tiny text-text-tertiary">
                            {[selection.cursor_fields.join(', '),
                              selection.primary_key_fields.flat().join(', ')]
                              .filter(Boolean).join(' / ') || '—'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>

              <Card>
                <Checkbox
                  checked={runFirst}
                  onChange={setRunFirst}
                  label={t('pipelines.runFirst')}
                />
                <p className="mt-1 pl-6 text-tiny text-text-quaternary">
                  {t('pipelines.runFirstHint')}
                </p>
              </Card>
            </div>
          )}

          <WizardFooter
            onBack={step > 0 ? () => setStep(step - 1) : undefined}
            backLabel={t('common.back')}
            hint={t('wizard.stepOf', {
              current: String(step + 1), total: String(steps.length),
            })}
            onNext={goNext}
            nextLabel={step === 3 ? t('pipelines.add') : t('common.continue')}
            nextLoading={create.isPending}
            nextDisabled={step === 1 && discover.isPending}
          />
        </div>
      )}
    </div>
  );
}
