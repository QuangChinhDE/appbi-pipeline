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
import { WarehouseBrowser } from '@/components/transforms/WarehouseBrowser';
import { ConnectionPicker } from '@/components/transforms/ConnectionPicker';

export default function NewTransformPage() {
  const { locale } = useI18n();
  const copy = locale === 'vi' ? {
    title: 'Transform mới', back: 'Transform', warehouse: 'Kết nối', inputs: 'Chọn bảng', create: 'Tạo',
    accountTitle: 'Tài khoản đọc dữ liệu',
    accountHelp: 'Tài khoản này quyết định bạn thấy được project và bảng nào ở bước sau. Để mặc định nếu dữ liệu nằm cùng chỗ với Destination.',
    chooseWarehouse: 'Chọn Destination warehouse', supported: 'Hỗ trợ Transform', unavailable: 'Chưa hỗ trợ',
    chooseInputs: 'Chọn relation đầu vào', chooseInputsHelp: 'Chỉ các relation đã được AppBI xác minh mới có thể dùng.',
    noAssets: 'Chưa có relation đã xác minh',
    register: 'Chọn bảng từ kho dữ liệu',
    manualEntry: 'Hoặc nhập tay tên bảng',
    browse: {
      hint: 'Đây là những gì thực sự có trong kho dữ liệu — kể cả dataset bạn tự tạo, không đi qua Pipeline.',
      account: 'Tài khoản:',
      accountDefault: 'dùng tài khoản của Destination',
      useAnother: 'Dùng tài khoản khác',
      useDefault: 'Quay lại tài khoản Destination',
      credentials: 'Service account JSON',
      credentialsHint: 'Tài khoản này phải vừa đọc được các bảng nguồn, vừa ghi được schema đích — dbt chỉ dùng một kết nối cho cả hai.',
      connect: 'Kết nối',
      project: 'Project',
      dataset: 'Dataset',
      chooseDataset: 'Chọn một dataset để xem các bảng bên trong',
      filter: 'Lọc theo tên bảng',
      noTables: 'Dataset này không có bảng nào',
      noMatch: 'Không có bảng nào khớp',
      loadFailed: 'Không đọc được danh sách bảng',
      alreadyAdded: 'Đã thêm',
      fromPipeline: 'từ',
      addSelected: 'Thêm bảng đã chọn',
      selectedCount: 'Đã chọn {n} bảng',
      nothingSelected: 'Tích vào bảng bạn cần dùng',
    },
    schema: 'Schema / Dataset', relation: 'Tên relation', catalog: 'Database / Project', pipeline: 'Pipeline nguồn', stream: 'Stream nguồn', verify: 'Xác minh relation',
    details: 'Thông tin Transform', name: 'Tên Transform', output: 'Output schema',
    continue: 'Tiếp tục', createAction: 'Tạo Transform', registered: 'Relation đã được xác minh',
    fromPipelines: 'Dữ liệu từ Pipeline đang đổ vào warehouse này',
    verifiedRelations: 'Relation đã xác minh trong warehouse',
    notResolved: 'Chưa xác định được bảng thực tế trong warehouse',
    resolve: 'Xác định', neverRun: 'Chưa chạy', warehouseRelation: 'Relation trong warehouse',
  } : {
    title: 'New transform', back: 'Transform', warehouse: 'Connect', inputs: 'Pick tables', create: 'Create',
    accountTitle: 'Account to read with',
    accountHelp: 'This account decides which projects and tables you can see in the next step. Leave it as it is if the data lives where the Destination does.',
    chooseWarehouse: 'Choose a warehouse Destination', supported: 'Transform supported', unavailable: 'Unavailable',
    chooseInputs: 'Choose input relations', chooseInputsHelp: 'Only relations verified by AppBI can be selected.',
    noAssets: 'No verified relations yet',
    register: 'Pick a table from the warehouse',
    manualEntry: 'Or type the table name',
    browse: {
      hint: 'This is what the warehouse actually holds, including datasets you built yourself without a Pipeline.',
      account: 'Account:',
      accountDefault: "using the Destination's own account",
      useAnother: 'Use another account',
      useDefault: "Back to the Destination's account",
      credentials: 'Service account JSON',
      credentialsHint: 'This account must both read the source tables and write the output schema — dbt uses one connection for both.',
      connect: 'Connect',
      project: 'Project',
      dataset: 'Dataset',
      chooseDataset: 'Choose a dataset to see the tables inside it',
      filter: 'Filter by table name',
      noTables: 'This dataset has no tables',
      noMatch: 'No table matches',
      loadFailed: 'Could not list the tables',
      alreadyAdded: 'Added',
      fromPipeline: 'from',
      addSelected: 'Add selected',
      selectedCount: '{n} selected',
      nothingSelected: 'Tick the tables you need',
    },
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
  const [connection, setConnection] = React.useState<
    import('@/components/transforms/ConnectionPicker').ChosenConnection | null
  >(null);
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
      secret_ref: connection?.secret_ref,
    }),
    onSuccess: async (asset) => {
      setAssetIds((current) => Array.from(new Set([...current, asset.id])));
      await queryClient.invalidateQueries({ queryKey: qk.transformInputs(workspaceId, destinationId) });
      await queryClient.invalidateQueries({
        queryKey: qk.transformWarehouseAll(workspaceId, destinationId),
      });
      setAssetForm((current) => ({ ...current, schema_name: '', relation_name: '', pipeline_stream_id: '' }));
      toastSuccess(copy.registered);
    },
    onError: (error) => toastError(error),
  });
  /**
   * Register the relations picked out of the warehouse, then select them.
   *
   * One at a time rather than in parallel: each is verified against the
   * warehouse, and a burst of concurrent metadata calls is how browsing a large
   * dataset turns into a rate limit.
   */
  const addBrowsed = useMutation({
    mutationFn: async (relations: {
      schema_name: string; relation_name: string; catalog_name: string | null;
    }[]) => {
      const added = [];
      for (const relation of relations) {
        added.push(await transformApi.registerAsset(destinationId, {
          catalog_name: relation.catalog_name || undefined,
          schema_name: relation.schema_name,
          relation_name: relation.relation_name,
          secret_ref: connection?.secret_ref,
        }));
      }
      return added;
    },
    onSuccess: async (assets) => {
      setAssetIds((current) => Array.from(
        new Set([...current, ...assets.map((item) => item.id)]),
      ));
      await queryClient.invalidateQueries({
        queryKey: qk.transformInputs(workspaceId, destinationId),
      });
      // Prefix, not the exact key: the listing for the open dataset carries the
      // schema as a final segment, and it is the one that has to redraw so the
      // row switches from Add to Added.
      await queryClient.invalidateQueries({
        queryKey: qk.transformWarehouseAll(workspaceId, destinationId),
      });
      toastSuccess(copy.registered);
    },
    onError: (error) => toastError(error),
  });

  const create = useMutation({
    mutationFn: () => transformApi.create({
      name, destination_id: destinationId, default_schema: outputSchema,
      input_asset_ids: assetIds,
      warehouse_secret_ref: connection?.secret_ref,
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
    // A flex column so the body can claim the height the header leaves, which
    // is what puts the wizard footer at the foot instead of halfway down.
    <div className="flex min-h-0 flex-1 flex-col">
      <DetailHeader backHref="/transforms" backLabel={copy.back} title={copy.title} icon={<Database className="h-5 w-5 text-brand" />} />
      <DetailBody>
        {/* Full height with the footer at the foot: a short step used to leave
            the buttons stranded halfway down the page above an empty half. */}
        <div className="mx-auto flex w-full min-h-0 max-w-4xl flex-1 flex-col">
          <Stepper steps={[
            { id: 'warehouse', label: copy.warehouse },
            { id: 'inputs', label: copy.inputs },
            { id: 'create', label: copy.create },
          ]} current={step} onStepClick={setStep} />

          <div className="mt-6 min-h-0 flex-1">
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

                {/* The account belongs here, not later: it decides which
                    projects and tables exist at all, so choosing inputs before
                    choosing it means choosing from the wrong list. */}
                {destinationId && (
                  <div className="mt-4">
                    <h3 className="text-caption font-emphasis text-text-secondary">
                      {copy.accountTitle}
                    </h3>
                    <p className="mb-2 mt-0.5 text-tiny text-text-tertiary">
                      {copy.accountHelp}
                    </p>
                    <ConnectionPicker
                      destinationId={destinationId} copy={copy.browse}
                      connection={connection} onChange={setConnection} />
                  </div>
                )}
              </section>
            )}

            {step === 1 && (
              <section>
                <div>
                  <h2 className="text-small font-strong text-text-primary">{copy.chooseInputs}</h2>
                  <p className="mt-1 text-caption text-text-tertiary">{copy.chooseInputsHelp}</p>
                </div>

                {/* Browsing is the way in, not an alternative to a list of
                    Pipeline streams. A table either exists in the warehouse or
                    it does not; whether a Pipeline keeps it fresh is a label on
                    that table, not a second place to look for it. */}
                <div className="mt-3 rounded-lg border border-[rgb(var(--border-line))] bg-surface-1 p-4">
                  <WarehouseBrowser
                    destinationId={destinationId} copy={copy.browse}
                    connection={connection}
                    adding={addBrowsed.isPending}
                    onAdd={(relations) => addBrowsed.mutate(relations)} />
                  <details className="mt-4 border-t border-[rgb(var(--border-line))] pt-3">
                    <summary className="cursor-pointer text-caption text-text-secondary">
                      {copy.manualEntry}
                    </summary>
                    <div className="mt-3 grid gap-3 sm:grid-cols-2">
                      <div><Label>{copy.catalog}</Label><Input value={assetForm.catalog_name} onChange={(event) => setAssetForm({ ...assetForm, catalog_name: event.target.value })} /></div>
                      <div><Label required>{copy.schema}</Label><Input value={assetForm.schema_name} onChange={(event) => setAssetForm({ ...assetForm, schema_name: event.target.value })} /></div>
                      <div><Label required>{copy.relation}</Label><Input value={assetForm.relation_name} onChange={(event) => setAssetForm({ ...assetForm, relation_name: event.target.value })} /></div>
                      <div><Label>{copy.pipeline}</Label><Select value={assetForm.pipeline_id} onChange={(event) => setAssetForm({ ...assetForm, pipeline_id: event.target.value, pipeline_stream_id: '' })}><option value="">{copy.warehouseRelation}</option>{candidates.data?.pipelines.map((item) => <option key={item.pipeline.id} value={item.pipeline.id}>{item.pipeline.name}</option>)}</Select></div>
                      {selectedPipeline && <div><Label>{copy.stream}</Label><Select value={assetForm.pipeline_stream_id} onChange={(event) => setAssetForm({ ...assetForm, pipeline_stream_id: event.target.value })}><option value="">Select stream</option>{selectedPipeline.streams.map((stream) => <option key={stream.id} value={stream.id}>{stream.namespace ? `${stream.namespace}.` : ''}{stream.name}</option>)}</Select></div>}
                    </div>
                    <div className="mt-3 flex justify-end"><Button variant="primary" size="sm" loading={register.isPending} disabled={!assetForm.schema_name || !assetForm.relation_name || Boolean(assetForm.pipeline_id && !assetForm.pipeline_stream_id)} onClick={() => register.mutate()} leadingIcon={<Link2 className="h-4 w-4" />}>{copy.verify}</Button></div>
                  </details>
                </div>

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
                        <span className="min-w-0 flex-1">
                          <span className="block truncate font-mono text-caption text-text-primary">
                            {asset.catalog_name ? `${asset.catalog_name}.` : ''}{asset.schema_name}.{asset.relation_name}
                          </span>
                          <span className="block text-tiny text-text-tertiary">
                            {asset.pipeline_name
                              ? `${copy.browse.fromPipeline} ${asset.pipeline_name}`
                              : copy.warehouseRelation} · {asset.columns.length} columns
                          </span>
                        </span>
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
