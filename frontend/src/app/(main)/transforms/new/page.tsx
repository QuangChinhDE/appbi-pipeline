'use client';

import * as React from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Database, Link2, Table2, X } from 'lucide-react';

import { transformApi } from '@/lib/api';
import { qk } from '@/lib/queryKeys';
import { formatRelative } from '@/lib/format';
import { useWorkspaceId } from '@/hooks/use-current-user';
import { toastError, toastSuccess } from '@/hooks/use-toast';
import { useI18n } from '@/providers/LanguageProvider';
import { DetailBody, DetailHeader } from '@/components/layout/PageLayout';
import { Stepper, WizardFooter } from '@/components/integrations/Stepper';
import { Badge } from '@/components/ui/Badge';
import { Button, IconButton } from '@/components/ui/Button';
import { Checkbox, Input, Label, Select } from '@/components/ui/Input';
import { EmptyState, ErrorState, Spinner } from '@/components/ui/Feedback';
import { cn } from '@/lib/utils';
import { WarehouseBrowser } from '@/components/transforms/WarehouseBrowser';
import { ConnectionPicker } from '@/components/transforms/ConnectionPicker';

export default function NewTransformPage() {
  const { locale } = useI18n();
  const copy = locale === 'vi' ? {
    title: 'Transform mới', back: 'Transform', warehouse: 'Kết nối', inputs: 'Chọn bảng', create: 'Tạo',
    connect: {
      title: 'Chọn key để kết nối kho dữ liệu',
      help: 'Key quyết định bạn đọc được project và bảng nào ở bước sau. Chọn key có sẵn, hoặc tạo key mới nếu dữ liệu nằm ở nơi key hiện tại không với tới.',
      defaultKey: 'key mặc định',
      noAccount: '(chưa đọc được tài khoản)',
      projects: 'project',
      addKey: 'Tạo key mới',
      newKeyTitle: 'Key mới',
      keyName: 'Tên key',
      keyNamePlaceholder: 'VD: Key đọc kho Sale',
      keyWarehouse: 'Kho dữ liệu',
      credentials: 'Service account JSON',
      credentialsHint: 'Key này phải vừa đọc được bảng nguồn, vừa ghi được schema đích — dbt chỉ dùng một kết nối cho cả hai. Được mã hoá khi lưu và không hiển thị lại.',
      save: 'Kiểm tra và lưu',
      cancel: 'Hủy',
      remove: 'Xoá key',
      loadFailed: 'Không tải được danh sách key',
    },
    chosenTables: 'Bảng đã chọn',
    nothingChosen: 'Chưa chọn bảng nào',
    clearAll: 'Bỏ hết',
    removeTable: 'Bỏ bảng này',
    fromTransform: 'Do Transform khác tạo',
    columnsShort: 'cột',
    accountTitle: 'Tài khoản đọc dữ liệu',
    accountHelp: 'Tài khoản này quyết định bạn thấy được project và bảng nào ở bước sau. Để mặc định nếu dữ liệu nằm cùng chỗ với Destination.',
    chooseWarehouse: 'Chọn kho dữ liệu', supported: 'Hỗ trợ Transform', unavailable: 'Chưa hỗ trợ',
    chooseInputs: 'Chọn bảng cho Transform', chooseInputsHelp: 'Duyệt kho dữ liệu và tích những bảng Transform này sẽ đọc.',
    noAssets: 'Chưa có bảng nào',
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
      addSelected: 'Thêm vào Transform',
      selectedCount: 'Đã chọn {n} bảng',
      nothingSelected: 'Tích vào bảng bạn cần dùng',
    },
    schema: 'Dataset', relation: 'Tên bảng', catalog: 'Project', pipeline: 'Pipeline nguồn', stream: 'Stream nguồn', verify: 'Kiểm tra và thêm',
    details: 'Đặt tên cho Transform', name: 'Tên Transform', output: 'Schema đích',
    continue: 'Tiếp tục', createAction: 'Tạo Transform', registered: 'Đã thêm bảng vào Transform',
    fromPipelines: 'Dữ liệu từ Pipeline đang đổ vào warehouse này',
    verifiedRelations: 'Bảng đã chọn',
    notResolved: 'Chưa tìm thấy bảng này trong kho dữ liệu',
    resolve: 'Xác định', neverRun: 'Chưa chạy', warehouseRelation: 'Bảng có sẵn trong kho',
  } : {
    title: 'New transform', back: 'Transform', warehouse: 'Connect', inputs: 'Pick tables', create: 'Create',
    connect: {
      title: 'Choose a key to reach the warehouse',
      help: 'The key decides which projects and tables you can read in the next step. Pick a saved one, or add a key if the data lives somewhere the current one cannot reach.',
      defaultKey: 'default key',
      noAccount: '(account unavailable)',
      projects: 'projects',
      addKey: 'Add a key',
      newKeyTitle: 'New key',
      keyName: 'Key name',
      keyNamePlaceholder: 'e.g. Sale warehouse reader',
      keyWarehouse: 'Warehouse',
      credentials: 'Service account JSON',
      credentialsHint: 'This key must both read the source tables and write the output schema — dbt uses one connection for both. Encrypted at rest and never shown again.',
      save: 'Check and save',
      cancel: 'Cancel',
      remove: 'Remove key',
      loadFailed: 'Could not load the key list',
    },
    chosenTables: 'Chosen tables',
    nothingChosen: 'No table chosen yet',
    clearAll: 'Clear all',
    removeTable: 'Remove',
    fromTransform: 'Built by another Transform',
    columnsShort: 'columns',
    accountTitle: 'Account to read with',
    accountHelp: 'This account decides which projects and tables you can see in the next step. Leave it as it is if the data lives where the Destination does.',
    chooseWarehouse: 'Choose a warehouse Destination', supported: 'Transform supported', unavailable: 'Unavailable',
    chooseInputs: 'Choose the tables this Transform reads', chooseInputsHelp: 'Browse the warehouse and tick the tables this Transform will read.',
    noAssets: 'No tables yet',
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
      addSelected: 'Add to Transform',
      selectedCount: '{n} selected',
      nothingSelected: 'Tick the tables you need',
    },
    schema: 'Dataset', relation: 'Table name', catalog: 'Project', pipeline: 'Upstream Pipeline', stream: 'Upstream stream', verify: 'Check and add',
    details: 'Name the Transform', name: 'Transform name', output: 'Output schema',
    continue: 'Continue', createAction: 'Create transform', registered: 'Tables added to the Transform',
    fromPipelines: 'Data from Pipelines loading this warehouse',
    verifiedRelations: 'Chosen tables',
    notResolved: 'This table has not been found in the warehouse yet',
    resolve: 'Resolve', neverRun: 'Never run', warehouseRelation: 'Table already in the warehouse',
  };
  const router = useRouter();
  const searchParams = useSearchParams();
  const workspaceId = useWorkspaceId();
  const queryClient = useQueryClient();
  const [step, setStep] = React.useState(0);
  const [assetIds, setAssetIds] = React.useState<string[]>([]);
  const [name, setName] = React.useState('');
  const [outputSchema, setOutputSchema] = React.useState('analytics');
  const [warehouse, setWarehouse] = React.useState<
    import('@/lib/types').ChosenWarehouse | null
  >(null);
  const destinationId = warehouse?.destination_id ?? '';
  const [assetForm, setAssetForm] = React.useState({
    catalog_name: '', schema_name: '', relation_name: '',
    pipeline_id: searchParams.get('pipeline_id') ?? '', pipeline_stream_id: '',
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
      connection_id: warehouse?.connection_id,
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
          connection_id: warehouse?.connection_id,
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
      warehouse_connection_id: warehouse?.connection_id,
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
              // One list, one click. A key row already says which warehouse it
              // reaches, so asking for the warehouse separately would be asking
              // the same question twice.
              <ConnectionPicker
                copy={copy.connect} value={warehouse} onChange={setWarehouse} />
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
                    connectionId={warehouse?.connection_id ?? null}
                    chosen={assetIds}
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

                {/* A basket, not a second list to tick. The upper list is
                    where you choose; this is what you chose. One checkbox with
                    two meanings was the thing that made this screen confusing. */}
                <div className="mt-5 flex items-center justify-between gap-3">
                  <h3 className="text-caption font-emphasis text-text-secondary">
                    {copy.chosenTables} ({assetIds.length})
                  </h3>
                  {assetIds.length > 0 && (
                    <Button size="xs" variant="ghost" onClick={() => setAssetIds([])}>
                      {copy.clearAll}
                    </Button>
                  )}
                </div>
                {assetIds.length === 0 ? (
                  <div className="mt-2"><EmptyState title={copy.nothingChosen} compact /></div>
                ) : (
                  <div className="mt-2 divide-y divide-[rgb(var(--border-line))] overflow-hidden rounded-lg border border-[rgb(var(--border-line))] bg-surface-1">
                    {assetIds.map((id) => {
                      const asset = candidates.data?.assets.find((item) => item.id === id);
                      if (!asset) return null;
                      return (
                        <div key={id} className="flex items-center gap-3 px-4 py-2.5">
                          <Table2 className="h-3.5 w-3.5 shrink-0 text-text-quaternary" />
                          <span className="min-w-0 flex-1">
                            <span className="block truncate font-mono text-caption text-text-primary">
                              {asset.schema_name}.{asset.relation_name}
                            </span>
                            <span className="block truncate text-tiny text-text-tertiary">
                              {asset.pipeline_name
                                ? `${copy.browse.fromPipeline} ${asset.pipeline_name}`
                                : copy.warehouseRelation}
                              {asset.columns.length
                                ? ` · ${asset.columns.length} ${copy.columnsShort}` : ''}
                            </span>
                          </span>
                          {asset.owner_type === 'TRANSFORM' && (
                            <Badge variant="info" size="xs">{copy.fromTransform}</Badge>
                          )}
                          <IconButton size="xs" variant="ghost"
                            aria-label={copy.removeTable} title={copy.removeTable}
                            onClick={() => setAssetIds(
                              (current) => current.filter((item) => item !== id),
                            )}>
                            <X className="h-3.5 w-3.5" />
                          </IconButton>
                        </div>
                      );
                    })}
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
                    <span className="font-emphasis text-text-primary">{warehouse?.name}</span>
                    <span className="mx-2 text-text-quaternary">·</span>
                    {assetIds.length} {copy.chosenTables.toLowerCase()}
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
