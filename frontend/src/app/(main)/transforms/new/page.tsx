'use client';

import * as React from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Database, Link2, Table2, X } from 'lucide-react';

import { transformApi } from '@/lib/api';
import type { DataAsset } from '@/lib/types';
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
      systemTitle: 'Chọn hệ thống',
      systemHelp: 'Transform sẽ chạy trên hệ thống này. Chỉ hiện những hệ thống đã có adapter dbt được chứng nhận.',
      connectionTitle: 'Chọn kết nối',
      connectionHelp: 'Dùng kết nối đã có, hoặc tạo kết nối mới. Kết nối quyết định bạn đọc được project và bảng nào ở bước sau.',
      defaultKey: 'sẵn có từ Đích dữ liệu',
      noAccount: '(chưa đọc được tài khoản)',
      projects: 'project',
      none: 'Chưa có kết nối nào cho hệ thống này',
      addKey: 'Tạo kết nối mới',
      newKeyTitle: 'Kết nối mới',
      keyName: 'Tên kết nối',
      keyNamePlaceholder: 'VD: Kho Sale',
      authMethod: 'Cách đăng nhập',
      authLabel: {
        service_account: 'Service account',
        oauth: 'Đăng nhập Google',
        password: 'Tài khoản / mật khẩu',
        inherited: 'Dùng key của Đích dữ liệu',
      } as Record<string, string>,
      project: 'Project',
      location: 'Vùng dữ liệu',
      credentials: 'Service account JSON',
      credentialsHint: 'Tài khoản này phải vừa đọc được bảng nguồn, vừa ghi được schema đích — dbt chỉ dùng một kết nối cho cả hai. Được mã hoá khi lưu và không hiển thị lại.',
      host: 'Host / IP',
      port: 'Cổng',
      database: 'Database',
      username: 'Tài khoản',
      password: 'Mật khẩu',
      oauthHint: 'Đăng nhập bằng tài khoản Google có quyền trên project BigQuery. AppBI chỉ giữ refresh token, không thấy mật khẩu của bạn.',
      oauthStart: 'Đăng nhập với Google',
      oauthDone: 'Đã đăng nhập:',
      save: 'Kiểm tra và lưu',
      cancel: 'Hủy',
      remove: 'Xoá kết nối',
      loadFailed: 'Không tải được danh sách kết nối',
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
      systemTitle: 'Choose a system',
      systemHelp: 'The Transform runs on this. Only systems with a certified dbt adapter are offered.',
      connectionTitle: 'Choose a connection',
      connectionHelp: 'Use one you already have, or make a new one. The connection decides which projects and tables you can read in the next step.',
      defaultKey: 'from a Destination',
      noAccount: '(account unavailable)',
      projects: 'projects',
      none: 'No connection for this system yet',
      addKey: 'New connection',
      newKeyTitle: 'New connection',
      keyName: 'Connection name',
      keyNamePlaceholder: 'e.g. Sale warehouse',
      authMethod: 'Sign in with',
      authLabel: {
        service_account: 'Service account',
        oauth: 'Google sign-in',
        password: 'User and password',
        inherited: "The Destination's own key",
      } as Record<string, string>,
      project: 'Project',
      location: 'Data location',
      credentials: 'Service account JSON',
      credentialsHint: 'This must both read the source tables and write the output schema — dbt uses one connection for both. Encrypted at rest and never shown again.',
      host: 'Host / IP',
      port: 'Port',
      database: 'Database',
      username: 'User',
      password: 'Password',
      oauthHint: 'Sign in with a Google account that has access to the BigQuery project. AppBI keeps only a refresh token and never sees your password.',
      oauthStart: 'Sign in with Google',
      oauthDone: 'Signed in as',
      save: 'Check and save',
      cancel: 'Cancel',
      remove: 'Remove connection',
      loadFailed: 'Could not load the connections',
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
  const [name, setName] = React.useState('');
  const [outputSchema, setOutputSchema] = React.useState('analytics');
  const [connectionId, setConnectionId] = React.useState<string | null>(null);
  const connectionList = useQuery({
    queryKey: qk.transformConnections(workspaceId), queryFn: transformApi.connections,
  });
  // The basket holds the assets it registered rather than looking them up
  // again: registration already returned them, and a second source of truth is
  // how the list and the count drift apart.
  const [chosenAssets, setChosenAssets] = React.useState<DataAsset[]>([]);
  const assetIds = React.useMemo(
    () => chosenAssets.map((item) => item.id), [chosenAssets],
  );
  const [assetForm, setAssetForm] = React.useState({
    catalog_name: '', schema_name: '', relation_name: '',
    pipeline_id: searchParams.get('pipeline_id') ?? '', pipeline_stream_id: '',
  });


  const register = useMutation({
    mutationFn: () => transformApi.registerAsset(connectionId ?? '', {
      catalog_name: assetForm.catalog_name || undefined,
      schema_name: assetForm.schema_name,
      relation_name: assetForm.relation_name,
      pipeline_id: assetForm.pipeline_id || undefined,
      pipeline_stream_id: assetForm.pipeline_stream_id || undefined,
    }),
    onSuccess: async (asset) => {
      setChosenAssets((current) => current.some((row) => row.id === asset.id)
        ? current : [...current, asset]);
      await queryClient.invalidateQueries({ queryKey: qk.transformInputs(workspaceId, connectionId ?? '') });
      await queryClient.invalidateQueries({
        queryKey: qk.transformWarehouseAll(workspaceId, connectionId ?? ''),
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
        added.push(await transformApi.registerAsset(connectionId ?? '', {
          catalog_name: relation.catalog_name || undefined,
          schema_name: relation.schema_name,
          relation_name: relation.relation_name,
            }));
      }
      return added;
    },
    onSuccess: async (assets) => {
      setChosenAssets((current) => [
        ...current,
        ...assets.filter((item) => !current.some((row) => row.id === item.id)),
      ]);
      await queryClient.invalidateQueries({
        queryKey: qk.transformInputs(workspaceId, connectionId ?? ''),
      });
      // Prefix, not the exact key: the listing for the open dataset carries the
      // schema as a final segment, and it is the one that has to redraw so the
      // row switches from Add to Added.
      await queryClient.invalidateQueries({
        queryKey: qk.transformWarehouseAll(workspaceId, connectionId ?? ''),
      });
      toastSuccess(copy.registered);
    },
    onError: (error) => toastError(error),
  });

  const create = useMutation({
    mutationFn: () => transformApi.create({
      name, warehouse_connection_id: connectionId ?? '',
      default_schema: outputSchema, input_asset_ids: assetIds,
    }),
    onSuccess: (created) => {
      queryClient.invalidateQueries({ queryKey: qk.transforms(workspaceId) });
      router.push(`/transforms/${created.id}`);
    },
    onError: (error) => toastError(error),
  });

  const chosenConnectionName = connectionId
    ? (connectionList.data ?? []).find((item) => item.id === connectionId)?.name ?? ''
    : '';

  const nextDisabled = step === 0 ? !connectionId : step === 1 ? assetIds.length === 0
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
                copy={copy.connect} value={connectionId} onChange={setConnectionId} />
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
                    copy={copy.browse}
                    connectionId={connectionId ?? ''}
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
                      <div><Label>{copy.pipeline}</Label><Select value={assetForm.pipeline_id} onChange={(event) => setAssetForm({ ...assetForm, pipeline_id: event.target.value, pipeline_stream_id: '' })}><option value="">{copy.warehouseRelation}</option></Select></div>
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
                    <Button size="xs" variant="ghost" onClick={() => setChosenAssets([])}>
                      {copy.clearAll}
                    </Button>
                  )}
                </div>
                {assetIds.length === 0 ? (
                  <div className="mt-2"><EmptyState title={copy.nothingChosen} compact /></div>
                ) : (
                  <div className="mt-2 divide-y divide-[rgb(var(--border-line))] overflow-hidden rounded-lg border border-[rgb(var(--border-line))] bg-surface-1">
                    {assetIds.map((id) => {
                      const asset = chosenAssets.find((item) => item.id === id);
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
                            onClick={() => setChosenAssets(
                              (current) => current.filter((item) => item.id !== id),
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
                    <span className="font-emphasis text-text-primary">{chosenConnectionName}</span>
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
