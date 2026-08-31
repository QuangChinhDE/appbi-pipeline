'use client';

import * as React from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CheckCircle2, Database, GitBranch, Link2, Plus, Warehouse } from 'lucide-react';

import { transformApi } from '@/lib/api';
import { qk } from '@/lib/queryKeys';
import { formatRelative } from '@/lib/format';
import { useWorkspaceId } from '@/hooks/use-current-user';
import { toastError, toastSuccess } from '@/hooks/use-toast';
import { useI18n } from '@/providers/LanguageProvider';
import { DetailBody, DetailHeader } from '@/components/layout/PageLayout';
import { Stepper, WizardFooter } from '@/components/integrations/Stepper';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Checkbox, Input, Label, Select } from '@/components/ui/Input';
import { EmptyState, ErrorState, Spinner } from '@/components/ui/Feedback';
import { cn } from '@/lib/utils';

export default function NewTransformPage() {
  const { locale } = useI18n();
  const copy = locale === 'vi' ? {
    title: 'Transform mới', back: 'Transform', warehouse: 'Warehouse', inputs: 'Dữ liệu đầu vào', create: 'Tạo',
    chooseWarehouse: 'Chọn Destination warehouse', supported: 'Hỗ trợ Transform', unavailable: 'Chưa hỗ trợ',
    chooseInputs: 'Chọn relation đầu vào', chooseInputsHelp: 'Chỉ các relation đã được AppBI xác minh mới có thể dùng.',
    noAssets: 'Chưa có relation đã xác minh', register: 'Dùng relation có sẵn trong warehouse',
    schema: 'Schema / Dataset', relation: 'Tên relation', catalog: 'Database / Project', pipeline: 'Pipeline nguồn', stream: 'Stream nguồn', verify: 'Xác minh relation',
    details: 'Thông tin Transform', name: 'Tên Transform', output: 'Output schema',
    continue: 'Tiếp tục', createAction: 'Tạo Transform', registered: 'Relation đã được xác minh',
    fromPipelines: 'Dữ liệu từ Pipeline đang đổ vào warehouse này',
    verifiedRelations: 'Relation đã xác minh trong warehouse',
    notResolved: 'Chưa xác định được bảng thực tế trong warehouse',
    resolve: 'Xác định', neverRun: 'Chưa chạy', warehouseRelation: 'Relation trong warehouse',
  } : {
    title: 'New transform', back: 'Transform', warehouse: 'Warehouse', inputs: 'Input data', create: 'Create',
    chooseWarehouse: 'Choose a warehouse Destination', supported: 'Transform supported', unavailable: 'Unavailable',
    chooseInputs: 'Choose input relations', chooseInputsHelp: 'Only relations verified by AppBI can be selected.',
    noAssets: 'No verified relations yet', register: 'Use an existing warehouse relation',
    schema: 'Schema / Dataset', relation: 'Relation name', catalog: 'Database / Project', pipeline: 'Upstream Pipeline', stream: 'Upstream stream', verify: 'Verify relation',
    details: 'Transform details', name: 'Transform name', output: 'Output schema',
    continue: 'Continue', createAction: 'Create transform', registered: 'Relation verified',
    fromPipelines: 'Data from Pipelines loading this warehouse',
    verifiedRelations: 'Verified relations in the warehouse',
    notResolved: 'The physical table has not been resolved yet',
    resolve: 'Resolve', neverRun: 'Never run', warehouseRelation: 'Warehouse relation',
  };
  const router = useRouter();
  const searchParams = useSearchParams();
  const workspaceId = useWorkspaceId();
  const queryClient = useQueryClient();
  const [step, setStep] = React.useState(0);
  const [destinationId, setDestinationId] = React.useState(searchParams.get('destination_id') ?? '');
  const [assetIds, setAssetIds] = React.useState<string[]>([]);
  const [name, setName] = React.useState('');
  const [outputSchema, setOutputSchema] = React.useState('analytics');
  const [showRegister, setShowRegister] = React.useState(false);
  const [assetForm, setAssetForm] = React.useState({
    catalog_name: '', schema_name: '', relation_name: '',
    pipeline_id: searchParams.get('pipeline_id') ?? '', pipeline_stream_id: '',
  });

  const destinations = useQuery({
    queryKey: qk.transformDestinations(workspaceId), queryFn: transformApi.destinations,
  });
  const candidates = useQuery({
    queryKey: qk.transformInputs(workspaceId, destinationId),
    queryFn: () => transformApi.inputCandidates(destinationId), enabled: Boolean(destinationId),
  });
  const selectedPipeline = candidates.data?.pipelines.find(
    (item) => item.pipeline.id === assetForm.pipeline_id,
  );

  const register = useMutation({
    mutationFn: () => transformApi.registerAsset(destinationId, {
      catalog_name: assetForm.catalog_name || undefined,
      schema_name: assetForm.schema_name,
      relation_name: assetForm.relation_name,
      pipeline_id: assetForm.pipeline_id || undefined,
      pipeline_stream_id: assetForm.pipeline_stream_id || undefined,
    }),
    onSuccess: async (asset) => {
      setAssetIds((current) => Array.from(new Set([...current, asset.id])));
      await queryClient.invalidateQueries({ queryKey: qk.transformInputs(workspaceId, destinationId) });
      setShowRegister(false);
      setAssetForm((current) => ({ ...current, schema_name: '', relation_name: '', pipeline_stream_id: '' }));
      toastSuccess(copy.registered);
    },
    onError: (error) => toastError(error),
  });
  const create = useMutation({
    mutationFn: () => transformApi.create({
      name, destination_id: destinationId, default_schema: outputSchema,
      input_asset_ids: assetIds,
    }),
    onSuccess: (created) => {
      queryClient.invalidateQueries({ queryKey: qk.transforms(workspaceId) });
      router.push(`/transforms/${created.id}`);
    },
    onError: (error) => toastError(error),
  });

  const nextDisabled = step === 0 ? !destinationId : step === 1 ? assetIds.length === 0
    : !name.trim() || !/^[A-Za-z_][A-Za-z0-9_]*$/.test(outputSchema);

  return (
    <div>
      <DetailHeader backHref="/transforms" backLabel={copy.back} title={copy.title} icon={<Database className="h-5 w-5 text-brand" />} />
      <DetailBody>
        <div className="mx-auto max-w-4xl">
          <Stepper steps={[
            { id: 'warehouse', label: copy.warehouse },
            { id: 'inputs', label: copy.inputs },
            { id: 'create', label: copy.create },
          ]} current={step} onStepClick={setStep} />

          <div className="mt-6 min-h-[420px]">
            {step === 0 && (
              <section>
                <h2 className="text-small font-strong text-text-primary">{copy.chooseWarehouse}</h2>
                {destinations.isLoading ? <Spinner /> : destinations.error ? (
                  <ErrorState title={copy.chooseWarehouse} message={(destinations.error as Error).message} onRetry={() => destinations.refetch()} />
                ) : (
                  <div className="mt-3 divide-y divide-[rgb(var(--border-line))] overflow-hidden rounded-lg border border-[rgb(var(--border-line))] bg-surface-1">
                    {destinations.data?.map((item) => (
                      <button
                        key={item.destination.id}
                        type="button"
                        disabled={!item.supported}
                        onClick={() => setDestinationId(item.destination.id)}
                        className={cn(
                          'flex w-full items-center gap-3 px-4 py-3 text-left transition-colors',
                          item.supported ? 'hover:bg-surface-2' : 'cursor-not-allowed opacity-55',
                          destinationId === item.destination.id && 'bg-brand/[0.06]',
                        )}
                      >
                        <span className="flex h-9 w-9 items-center justify-center rounded-md bg-surface-2 text-text-tertiary"><Warehouse className="h-4 w-4" /></span>
                        <span className="min-w-0 flex-1">
                          <span className="block text-caption font-emphasis text-text-primary">{item.destination.name}</span>
                          <span className="block text-tiny text-text-tertiary">{item.destination.connector_display_name}</span>
                        </span>
                        <Badge variant={item.supported ? 'success' : 'neutral'} size="xs" dot>{item.supported ? copy.supported : copy.unavailable}</Badge>
                        {destinationId === item.destination.id && <CheckCircle2 className="h-4 w-4 text-brand" />}
                      </button>
                    ))}
                  </div>
                )}
              </section>
            )}

            {step === 1 && (
              <section>
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div><h2 className="text-small font-strong text-text-primary">{copy.chooseInputs}</h2><p className="mt-1 text-caption text-text-tertiary">{copy.chooseInputsHelp}</p></div>
                  <Button size="sm" variant="secondary" leadingIcon={<Plus className="h-4 w-4" />} onClick={() => setShowRegister((value) => !value)}>{copy.register}</Button>
                </div>
                {showRegister && (
                  <div className="mt-3 rounded-lg border border-[rgb(var(--border-line))] bg-surface-1 p-4">
                    <div className="grid gap-3 sm:grid-cols-2">
                      <div><Label>{copy.catalog}</Label><Input value={assetForm.catalog_name} onChange={(event) => setAssetForm({ ...assetForm, catalog_name: event.target.value })} /></div>
                      <div><Label required>{copy.schema}</Label><Input value={assetForm.schema_name} onChange={(event) => setAssetForm({ ...assetForm, schema_name: event.target.value })} /></div>
                      <div><Label required>{copy.relation}</Label><Input value={assetForm.relation_name} onChange={(event) => setAssetForm({ ...assetForm, relation_name: event.target.value })} /></div>
                      <div><Label>{copy.pipeline}</Label><Select value={assetForm.pipeline_id} onChange={(event) => setAssetForm({ ...assetForm, pipeline_id: event.target.value, pipeline_stream_id: '' })}><option value="">Warehouse relation</option>{candidates.data?.pipelines.map((item) => <option key={item.pipeline.id} value={item.pipeline.id}>{item.pipeline.name}</option>)}</Select></div>
                      {selectedPipeline && <div><Label>{copy.stream}</Label><Select value={assetForm.pipeline_stream_id} onChange={(event) => setAssetForm({ ...assetForm, pipeline_stream_id: event.target.value })}><option value="">Select stream</option>{selectedPipeline.streams.map((stream) => <option key={stream.id} value={stream.id}>{stream.namespace ? `${stream.namespace}.` : ''}{stream.name}</option>)}</Select></div>}
                    </div>
                    <div className="mt-3 flex justify-end"><Button variant="primary" size="sm" loading={register.isPending} disabled={!assetForm.schema_name || !assetForm.relation_name || Boolean(assetForm.pipeline_id && !assetForm.pipeline_stream_id)} onClick={() => register.mutate()} leadingIcon={<Link2 className="h-4 w-4" />}>{copy.verify}</Button></div>
                  </div>
                )}
                {/* AppBI already knows which Pipelines load this warehouse, so
                    offer their tables directly instead of making the user recall
                    the dataset name and type it into the manual form. */}
                {(candidates.data?.pipelines.length ?? 0) > 0 && (
                  <div className="mt-4">
                    <h3 className="text-caption font-emphasis text-text-secondary">{copy.fromPipelines}</h3>
                    <div className="mt-2 space-y-3">
                      {candidates.data?.pipelines.map((item) => (
                        <div key={item.pipeline.id} className="overflow-hidden rounded-lg border border-[rgb(var(--border-line))] bg-surface-1">
                          <div className="flex items-center gap-2 border-b border-[rgb(var(--border-line))] px-4 py-2.5">
                            <GitBranch className="h-4 w-4 shrink-0 text-text-tertiary" />
                            <span className="min-w-0 flex-1 truncate text-caption font-emphasis text-text-primary">{item.pipeline.name}</span>
                            <span className="text-tiny text-text-quaternary">
                              {item.last_success_at ? formatRelative(item.last_success_at, locale) : copy.neverRun}
                            </span>
                          </div>
                          {item.streams.map((stream) => {
                            const asset = stream.asset_id
                              ? candidates.data?.assets.find((a) => a.id === stream.asset_id)
                              : undefined;
                            return (
                              <div key={stream.id} className="flex items-center gap-3 px-4 py-2.5 hover:bg-surface-2">
                                <Checkbox
                                  disabled={!asset}
                                  checked={Boolean(asset && assetIds.includes(asset.id))}
                                  aria-label={stream.name}
                                  onChange={(checked) => asset && setAssetIds((current) => checked
                                    ? [...current, asset.id]
                                    : current.filter((id) => id !== asset.id))}
                                />
                                <span className="min-w-0 flex-1">
                                  <span className="block truncate font-mono text-caption text-text-primary">{stream.name}</span>
                                  <span className="block truncate text-tiny text-text-tertiary">
                                    {asset ? `${asset.schema_name}.${asset.relation_name} · ${asset.columns.length} columns` : copy.notResolved}
                                  </span>
                                </span>
                                {asset
                                  ? <Badge variant="success" size="xs">READY</Badge>
                                  : <Button size="xs" variant="ghost" loading={register.isPending && assetForm.pipeline_stream_id === stream.id}
                                      leadingIcon={<Link2 className="h-3.5 w-3.5" />}
                                      onClick={() => {
                                        setAssetForm({
                                          catalog_name: '', schema_name: '', relation_name: stream.name,
                                          pipeline_id: item.pipeline.id, pipeline_stream_id: stream.id,
                                        });
                                        setShowRegister(true);
                                      }}>{copy.resolve}</Button>}
                              </div>
                            );
                          })}
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                <h3 className="mt-5 text-caption font-emphasis text-text-secondary">{copy.verifiedRelations}</h3>
                {candidates.isLoading ? <Spinner /> : candidates.error ? (
                  <div className="mt-2"><ErrorState title={copy.chooseInputs} message={(candidates.error as Error).message} onRetry={() => candidates.refetch()} /></div>
                ) : (candidates.data?.assets.length ?? 0) === 0 ? (
                  <div className="mt-2"><EmptyState title={copy.noAssets} compact /></div>
                ) : (
                  <div className="mt-2 divide-y divide-[rgb(var(--border-line))] overflow-hidden rounded-lg border border-[rgb(var(--border-line))] bg-surface-1">
                    {candidates.data?.assets.map((asset) => (
                      <div key={asset.id} className="flex items-center gap-3 px-4 py-3 hover:bg-surface-2">
                        <Checkbox checked={assetIds.includes(asset.id)} onChange={(checked) => setAssetIds((current) => checked ? [...current, asset.id] : current.filter((id) => id !== asset.id))} aria-label={`${asset.schema_name}.${asset.relation_name}`} />
                        <span className="min-w-0 flex-1"><span className="block font-mono text-caption text-text-primary">{asset.schema_name}.{asset.relation_name}</span><span className="block text-tiny text-text-tertiary">{asset.pipeline_name ?? copy.warehouseRelation} · {asset.columns.length} columns</span></span>
                        {/* A relation produced by another Transform is a valid
                            input, but picking one by accident is not, so it says
                            what it is. */}
                        {asset.owner_type === 'TRANSFORM'
                          ? <Badge variant="info" size="xs">{asset.asset_type === 'MART' ? 'MART' : 'MODEL'}</Badge>
                          : <Badge variant="success" size="xs">READY</Badge>}
                      </div>
                    ))}
                  </div>
                )}
              </section>
            )}

            {step === 2 && (
              <section className="max-w-xl">
                <h2 className="text-small font-strong text-text-primary">{copy.details}</h2>
                <div className="mt-4 space-y-4">
                  <div><Label required>{copy.name}</Label><Input autoFocus value={name} onChange={(event) => setName(event.target.value)} placeholder="Sales Analytics" /></div>
                  <div><Label required>{copy.output}</Label><Input value={outputSchema} onChange={(event) => setOutputSchema(event.target.value)} placeholder="analytics_sales" invalid={Boolean(outputSchema && !/^[A-Za-z_][A-Za-z0-9_]*$/.test(outputSchema))} /></div>
                  <div className="rounded-lg border border-[rgb(var(--border-line))] bg-surface-1 px-4 py-3 text-caption text-text-secondary">
                    <span className="font-emphasis text-text-primary">{destinations.data?.find((item) => item.destination.id === destinationId)?.destination.name}</span>
                    <span className="mx-2 text-text-quaternary">·</span>{assetIds.length} relations
                  </div>
                </div>
              </section>
            )}
          </div>

          <WizardFooter
            onBack={step > 0 ? () => setStep((current) => current - 1) : undefined}
            onNext={() => step < 2 ? setStep((current) => current + 1) : create.mutate()}
            nextDisabled={nextDisabled}
            nextLoading={create.isPending}
            nextLabel={step === 2 ? copy.createAction : copy.continue}
          />
        </div>
      </DetailBody>
    </div>
  );
}
