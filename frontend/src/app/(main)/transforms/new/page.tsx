'use client';

import * as React from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Database, Link2, Table2, X } from 'lucide-react';

import { transformApi } from '@/lib/api';
import type { DataAsset } from '@/lib/types';
import { qk } from '@/lib/queryKeys';
import { useWorkspaceId } from '@/hooks/use-current-user';
import { toastError, toastSuccess } from '@/hooks/use-toast';
import { useI18n } from '@/providers/LanguageProvider';
import { DetailBody, DetailHeader } from '@/components/layout/PageLayout';
import { Stepper, WizardFooter } from '@/components/integrations/Stepper';
import { Badge } from '@/components/ui/Badge';
import { Button, IconButton } from '@/components/ui/Button';
import { Input, Label, Select } from '@/components/ui/Input';
import { cn } from '@/lib/utils';
import { WarehouseBrowser } from '@/components/transforms/WarehouseBrowser';
import { ConnectionPicker } from '@/components/transforms/ConnectionPicker';

export default function NewTransformPage() {
  const { locale, t } = useI18n();
  // Only what a step actually renders. The screen had accumulated copy for
  // three earlier versions of itself -- a warehouse chooser, a Pipeline stream
  // list, a per-step account form -- and every unused key is a phrase somebody
  // has to read before working out it does not apply.
  const copy = locale === 'vi' ? {
    title: 'Transform mới', back: 'Transform',
    warehouse: 'Kết nối', inputs: 'Chọn bảng', create: 'Đặt tên',
    connect: {
      systemTitle: 'Hệ thống dữ liệu',
      systemHelp: 'Nơi Transform sẽ đọc và ghi.',
      connectionTitle: 'Kết nối',
      connectionHelp: 'Kết nối quyết định bạn thấy được dữ liệu nào ở bước sau.',
      defaultKey: 'từ Đích dữ liệu',
      noAccount: 'chưa đọc được tài khoản',
      projects: 'project',
      none: 'Chưa có kết nối nào',
      addKey: 'Tạo kết nối mới',
      newKeyTitle: 'Kết nối mới',
      keyName: 'Tên kết nối',
      keyNamePlaceholder: 'VD: Kho Sale',
      authMethod: 'Cách đăng nhập',
      authLabel: {
        service_account: 'Service account',
        oauth: 'Đăng nhập Google',
        password: 'Tài khoản / mật khẩu',
        inherited: 'Dùng chung với Đích dữ liệu',
      } as Record<string, string>,
      project: 'Project',
      location: 'Vùng dữ liệu',
      credentials: 'Service account JSON',
      credentialsHint: 'Cần quyền đọc bảng nguồn và quyền ghi vào nơi chứa kết quả. Được mã hoá khi lưu và không hiển thị lại.',
      host: 'Host / IP',
      port: 'Cổng',
      database: 'Database',
      username: 'Tài khoản',
      password: 'Mật khẩu',
      oauthHint: 'Đăng nhập bằng tài khoản Google có quyền trên project BigQuery. AppBI không thấy mật khẩu của bạn.',
      oauthStart: 'Đăng nhập với Google',
      oauthDone: 'Đã đăng nhập:',
      save: 'Kiểm tra và lưu',
      cancel: 'Hủy',
      remove: 'Xoá kết nối',
      loadFailed: 'Không tải được danh sách kết nối',
    },
    chooseInputs: 'Bảng đầu vào',
    chooseInputsHelp: 'Tích những bảng Transform này sẽ đọc — kể cả dataset bạn tự tạo, không đi qua Pipeline.',
    chosenTables: 'Đã chọn',
    nothingChosen: 'Chưa chọn bảng nào',
    clearAll: 'Bỏ hết',
    removeTable: 'Bỏ bảng này',
    fromTransform: 'Do Transform khác tạo',
    columnsShort: 'cột',
    manualEntry: 'Không thấy bảng cần tìm? Nhập tay tên bảng',
    browse: {
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
    schema: 'Dataset', relation: 'Tên bảng', catalog: 'Project',
    pipeline: 'Pipeline nguồn', verify: 'Kiểm tra và thêm',
    details: 'Đặt tên cho Transform', name: 'Tên Transform',
    output: 'Nơi chứa kết quả',
    outputHelp: 'Tên dataset (hoặc schema) Transform sẽ ghi bảng kết quả vào.',
    continue: 'Tiếp tục', createAction: 'Tạo Transform',
    registered: 'Đã thêm bảng vào Transform',
    warehouseRelation: 'Bảng có sẵn trong kho',
  } : {
    title: 'New transform', back: 'Transform',
    warehouse: 'Connect', inputs: 'Pick tables', create: 'Name it',
    connect: {
      systemTitle: 'Data system',
      systemHelp: 'Where the Transform reads and writes.',
      connectionTitle: 'Connection',
      connectionHelp: 'The connection decides what you can see in the next step.',
      defaultKey: 'from a Destination',
      noAccount: 'account unavailable',
      projects: 'projects',
      none: 'No connection yet',
      addKey: 'New connection',
      newKeyTitle: 'New connection',
      keyName: 'Connection name',
      keyNamePlaceholder: 'e.g. Sale warehouse',
      authMethod: 'Sign in with',
      authLabel: {
        service_account: 'Service account',
        oauth: 'Google sign-in',
        password: 'User and password',
        inherited: "Shared with the Destination",
      } as Record<string, string>,
      project: 'Project',
      location: 'Data location',
      credentials: 'Service account JSON',
      credentialsHint: 'Needs to read the source tables and write where the results go. Encrypted at rest and never shown again.',
      host: 'Host / IP',
      port: 'Port',
      database: 'Database',
      username: 'User',
      password: 'Password',
      oauthHint: 'Sign in with a Google account that has access to the BigQuery project. AppBI never sees your password.',
      oauthStart: 'Sign in with Google',
      oauthDone: 'Signed in as',
      save: 'Check and save',
      cancel: 'Cancel',
      remove: 'Remove connection',
      loadFailed: 'Could not load the connections',
    },
    chooseInputs: 'Input tables',
    chooseInputsHelp: 'Tick the tables this Transform reads — including datasets you built yourself, without a Pipeline.',
    chosenTables: 'Chosen',
    nothingChosen: 'No table chosen yet',
    clearAll: 'Clear all',
    removeTable: 'Remove',
    fromTransform: 'Built by another Transform',
    columnsShort: 'columns',
    manualEntry: "Can't find a table? Type its name",
    browse: {
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
    schema: 'Dataset', relation: 'Table name', catalog: 'Project',
    pipeline: 'Upstream Pipeline', verify: 'Check and add',
    details: 'Name the Transform', name: 'Transform name',
    output: 'Where results go',
    outputHelp: 'The dataset (or schema) this Transform writes its result tables into.',
    continue: 'Continue', createAction: 'Create transform',
    registered: 'Tables added to the Transform',
    warehouseRelation: 'Table already in the warehouse',
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
        {/* Natural height. Stretching this column to the viewport put the
            buttons at the very bottom with a third of the page empty above
            them on a short step; the footer is `sticky bottom-0`, so it
            reaches the foot by itself only when there is enough to scroll. */}
        <div className="mx-auto w-full max-w-4xl">
          <Stepper steps={[
            { id: 'warehouse', label: copy.warehouse },
            { id: 'inputs', label: copy.inputs },
            { id: 'create', label: copy.create },
          ]} current={step} onStepClick={setStep} />

          <div className="mt-6">
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
                    that table, not a second place to look for it.
                    No card around it: the controls and the table list carry
                    their own frames, and a third one only ate the width. */}
                <div className="mt-3">
                  <WarehouseBrowser
                    copy={copy.browse}
                    connectionId={connectionId ?? ''}
                    chosen={assetIds}
                    adding={addBrowsed.isPending}
                    onAdd={(relations) => addBrowsed.mutate(relations)} />
                  <details className="mt-3">
                    <summary className="cursor-pointer text-caption text-text-tertiary hover:text-text-secondary">
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
                  // A one-line note, not a framed empty state: the basket is
                  // empty for the whole first half of this step, and a 130px
                  // box saying so pushed the buttons off a laptop screen.
                  <p className="mt-1 text-caption text-text-quaternary">{copy.nothingChosen}</p>
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
                  <div>
                    <Label required>{copy.output}</Label>
                    <Input value={outputSchema} placeholder="analytics_sales"
                      onChange={(event) => setOutputSchema(event.target.value)}
                      invalid={Boolean(outputSchema && !/^[A-Za-z_][A-Za-z0-9_]*$/.test(outputSchema))} />
                    <p className="mt-1 text-tiny text-text-quaternary">{copy.outputHelp}</p>
                  </div>
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
            // Left half of the bar was empty on the first step, which read as
            // a rendering fault rather than as a bar with one button.
            hint={t('wizard.stepOf', { current: String(step + 1), total: '3' })}
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
