'use client';

import * as React from 'react';
import { useRouter } from 'next/navigation';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle, ArrowLeft, FileCode2, Github, Table2,
} from 'lucide-react';

import { transformApi } from '@/lib/api';
import { qk } from '@/lib/queryKeys';
import { cn } from '@/lib/utils';
import { useWorkspaceId } from '@/hooks/use-current-user';
import { toastError, toastSuccess } from '@/hooks/use-toast';
import { useI18n } from '@/providers/LanguageProvider';
import { DetailBody, DetailHeader } from '@/components/layout/PageLayout';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Checkbox, Input, Label, Select } from '@/components/ui/Input';
import { Spinner } from '@/components/ui/Feedback';
import { ConnectionPicker } from '@/components/transforms/ConnectionPicker';

const KIND_LABEL: Record<string, string> = { DBT: 'dbt', DATAFORM: 'Dataform' };

/**
 * Bring an existing dbt or Dataform repository in as a Transform.
 *
 * Two screens rather than one, and the order matters: read the conversion,
 * then decide. A repository carries things that do not exist here -- macros,
 * JavaScript helpers, seeds -- and the honest way to handle them is to name
 * them before anything is created, not to fail on the first run afterwards.
 */
export default function ImportTransformPage() {
  const { locale } = useI18n();
  const copy = locale === 'vi' ? vi : en;
  const router = useRouter();
  const workspaceId = useWorkspaceId();
  const queryClient = useQueryClient();

  const [repoUrl, setRepoUrl] = React.useState('');
  const [ref, setRef] = React.useState('');
  const [subdirectory, setSubdirectory] = React.useState('');
  const [token, setToken] = React.useState('');
  const [name, setName] = React.useState('');
  const [outputSchema, setOutputSchema] = React.useState('analytics');
  const [connectionId, setConnectionId] = React.useState<string | null>(null);
  const [autoPull, setAutoPull] = React.useState(true);
  const [intervalMinutes, setIntervalMinutes] = React.useState(30);

  const inspect = useMutation({
    mutationFn: () => transformApi.inspectRepository({
      repo_url: repoUrl.trim(),
      ref: ref.trim() || undefined,
      subdirectory: subdirectory.trim() || undefined,
      token: token.trim() || undefined,
    }),
    onSuccess: (preview) => {
      if (!name) setName(preview.project_name || preview.origin.repo);
    },
    onError: (error) => toastError(error),
  });
  const preview = inspect.data;

  const create = useMutation({
    mutationFn: () => transformApi.importRepository({
      repo_url: repoUrl.trim(),
      ref: ref.trim() || undefined,
      subdirectory: subdirectory.trim() || undefined,
      token: token.trim() || undefined,
      name: name.trim(), default_schema: outputSchema.trim(),
      auto_pull: autoPull, interval_minutes: intervalMinutes,
      warehouse_connection_id: connectionId ?? '',
    }),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: qk.transforms(workspaceId) });
      toastSuccess(copy.created.replace('{n}', String(result.transform.models.length)));
      router.push(`/transforms/${result.transform.id}`);
    },
    onError: (error) => toastError(error),
  });

  const canInspect = Boolean(connectionId)
    && /^(https?:\/\/(www\.)?github\.com\/|git@github\.com:)/.test(repoUrl.trim());
  const canCreate = Boolean(
    preview && name.trim() && connectionId
    && /^[A-Za-z_][A-Za-z0-9_]*$/.test(outputSchema.trim()),
  );

  return (
    <>
      <DetailHeader
        backHref="/transforms" backLabel={copy.back}
        title={copy.title}
        icon={<Github className="h-5 w-5 text-brand" />}
        subtitle={<span className="text-caption text-text-tertiary">{copy.subtitle}</span>}
      />
      <DetailBody>
        <div className="mx-auto w-full max-w-3xl space-y-5">
          {/* Same first step as creating a Transform by hand: which key. The
              repository's source tables are checked against it, so asking
              afterwards would mean showing a preview compared to nothing. */}
          <section className="rounded-lg border border-[rgb(var(--border-line))] bg-surface-1 p-4">
            <ConnectionPicker
              copy={copy.connect} value={connectionId} onChange={setConnectionId} />
          </section>

          <section className="rounded-lg border border-[rgb(var(--border-line))] bg-surface-1 p-4">
            <h2 className="text-small font-strong text-text-primary">{copy.repository}</h2>
            <p className="mt-1 text-caption text-text-tertiary">{copy.repositoryHelp}</p>
            <div className="mt-3 space-y-3">
              <div>
                <Label required>{copy.repoUrl}</Label>
                <Input value={repoUrl} placeholder="https://github.com/cong-ty/dataform-analytics"
                  onChange={(event) => setRepoUrl(event.target.value)} />
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <div>
                  <Label>{copy.branch}</Label>
                  <Input value={ref} placeholder={copy.branchPlaceholder}
                    onChange={(event) => setRef(event.target.value)} />
                </div>
                <div>
                  <Label>{copy.subdirectory}</Label>
                  <Input value={subdirectory} placeholder={copy.subdirectoryPlaceholder}
                    onChange={(event) => setSubdirectory(event.target.value)} />
                </div>
              </div>
              <div>
                <Label>{copy.token}</Label>
                {/* Typed as a password and sent once. It is used for this one
                    request and never written down anywhere. */}
                <Input type="password" value={token} autoComplete="off"
                  placeholder={copy.tokenPlaceholder}
                  onChange={(event) => setToken(event.target.value)} />
                <p className="mt-1 text-tiny text-text-tertiary">{copy.tokenHint}</p>
              </div>
            </div>
            <div className="mt-3 flex justify-end">
              <Button size="sm" variant="primary" loading={inspect.isPending}
                disabled={!canInspect}
                onClick={() => inspect.mutate()}>{copy.inspect}</Button>
            </div>
          </section>

          {inspect.isPending && (
            <div className="flex flex-col items-center gap-2 py-8 text-center">
              <Spinner />
              <p className="text-caption text-text-secondary">{copy.reading}</p>
            </div>
          )}

          {preview && !inspect.isPending && (
            <>
              <section className="rounded-lg border border-[rgb(var(--border-line))] bg-surface-1 p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <h2 className="text-small font-strong text-text-primary">{copy.found}</h2>
                  <Badge variant="brand">{KIND_LABEL[preview.kind] ?? preview.kind}</Badge>
                  {preview.project_name && (
                    <span className="font-mono text-caption text-text-tertiary">
                      {preview.project_name}
                    </span>
                  )}
                </div>
                <div className="mt-3 grid gap-3 sm:grid-cols-2">
                  <Tally icon={FileCode2} value={preview.models.length} label={copy.models} />
                  <Tally icon={Table2} value={preview.sources.length} label={copy.sources} />
                  <Tally icon={FileCode2} value={preview.tests.length} label={copy.tests} />
                </div>

                {preview.warnings.length > 0 && (
                  // Ahead of the model list on purpose. What did not convert is
                  // the thing a user has to decide about; what did convert is
                  // just reassurance.
                  <div className="mt-4 rounded-md border border-warning/30 bg-warning/[0.06] p-3">
                    <p className="flex items-center gap-1.5 text-caption font-emphasis text-text-primary">
                      <AlertTriangle className="h-4 w-4 text-warning" />
                      {copy.warnings.replace('{n}', String(preview.warnings.length))}
                    </p>
                    <ul className="mt-1.5 space-y-1">
                      {preview.warnings.map((item) => (
                        <li key={item} className="flex gap-1.5 text-caption text-text-secondary">
                          <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-warning" />
                          <span>{item}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                <div className="mt-4 grid gap-4 md:grid-cols-2">
                  <div>
                    <h3 className="text-tiny font-emphasis uppercase tracking-wide text-text-tertiary">
                      {copy.models}
                    </h3>
                    <ul className="mt-1.5 max-h-64 space-y-1 overflow-y-auto pr-1">
                      {preview.models.map((item) => (
                        <li key={item.path} className="flex items-center gap-1.5">
                          <span className="min-w-0 flex-1 truncate font-mono text-caption text-text-secondary"
                            title={item.path}>{item.name}</span>
                          <Badge variant="neutral" size="xs">{copy.layerLabel[item.layer] ?? item.layer}</Badge>
                          <Badge variant="subtle" size="xs">{item.materialization}</Badge>
                        </li>
                      ))}
                    </ul>
                  </div>
                  <div>
                    <h3 className="text-tiny font-emphasis uppercase tracking-wide text-text-tertiary">
                      {copy.sources}
                    </h3>
                    {preview.sources.length === 0 ? (
                      <p className="mt-1.5 text-caption text-text-tertiary">{copy.noSources}</p>
                    ) : (
                      <ul className="mt-1.5 max-h-64 space-y-1 overflow-y-auto pr-1">
                        {preview.sources.map((item) => (
                          <li key={`${item.alias}.${item.table}`}
                            className="truncate font-mono text-caption text-text-secondary"
                            title={`${item.catalog ?? ''}.${item.schema_name}.${item.relation}`}>
                            {/* The project is the whole point: a source in a
                                different project from the Destination is why an
                                import ends up with no inputs, and hiding it
                                hides the reason. */}
                            {item.catalog ? (
                              <span className="text-text-quaternary">{item.catalog}.</span>
                            ) : null}
                            {item.schema_name}.{item.relation}
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                </div>
              </section>

              <section className="rounded-lg border border-[rgb(var(--border-line))] bg-surface-1 p-4">
                <h2 className="text-small font-strong text-text-primary">{copy.createTitle}</h2>
                <p className="mt-1 text-caption text-text-tertiary">{copy.createHelp}</p>
                <div className="mt-3 grid gap-3 sm:grid-cols-2">
                  <div>
                    <Label required>{copy.name}</Label>
                    <Input value={name} onChange={(event) => setName(event.target.value)} />
                  </div>
                  <div>
                    <Label required>{copy.output}</Label>
                    <Input value={outputSchema}
                      onChange={(event) => setOutputSchema(event.target.value)} />
                  </div>
                </div>
                {/* Offered here rather than after the fact: somebody importing
                    from Git almost always wants it to keep following Git, and
                    finding the switch later means the first divergence is a
                    surprise. */}
                <div className="mt-4 border-t border-[rgb(var(--border-line))] pt-3">
                  <Checkbox checked={autoPull} onChange={setAutoPull}
                    label={copy.keepPulling} />
                  <p className="ml-6 text-tiny text-text-tertiary">{copy.keepPullingHint}</p>
                  {autoPull && (
                    <div className="ml-6 mt-2 max-w-[220px]">
                      <Label>{copy.checkEvery}</Label>
                      <Select value={String(intervalMinutes)}
                        onChange={(event) => setIntervalMinutes(Number(event.target.value))}>
                        <option value="15">15</option>
                        <option value="30">30</option>
                        <option value="60">60</option>
                        <option value="360">360</option>
                        <option value="1440">1440</option>
                      </Select>
                    </div>
                  )}
                </div>

                <div className="mt-4 flex items-center justify-between gap-3">
                  <Button size="sm" variant="ghost"
                    leadingIcon={<ArrowLeft className="h-4 w-4" />}
                    onClick={() => inspect.reset()}>{copy.startOver}</Button>
                  <Button size="sm" variant="primary" loading={create.isPending}
                    disabled={!canCreate}
                    onClick={() => create.mutate()}>{copy.createAction}</Button>
                </div>
              </section>
            </>
          )}
        </div>
      </DetailBody>
    </>
  );
}

function Tally({ icon: Icon, value, label }: {
  icon: typeof FileCode2; value: number; label: string;
}) {
  return (
    <div className="flex items-center gap-2 rounded-md border border-[rgb(var(--border-line))] px-3 py-2">
      <Icon className="h-4 w-4 shrink-0 text-text-quaternary" />
      <span className="text-small font-strong tabular-nums text-text-primary">{value}</span>
      <span className="text-caption text-text-tertiary">{label}</span>
    </div>
  );
}

const vi = {
  back: 'Transform',
  title: 'Import từ GitHub',
  subtitle: 'Lấy một project dbt hoặc Dataform có sẵn về làm Transform',
  repository: 'Repository',
  repositoryHelp: 'Dán địa chỉ repository trên GitHub. Hệ thống tự nhận biết đây là project dbt hay Dataform, và chỉ đọc — không ghi gì lên repository của bạn.',
  repoUrl: 'Địa chỉ repository',
  branch: 'Nhánh hoặc tag',
  branchPlaceholder: 'Để trống dùng nhánh mặc định',
  subdirectory: 'Thư mục con',
  subdirectoryPlaceholder: 'Nếu project nằm trong thư mục con',
  token: 'GitHub access token',
  tokenPlaceholder: 'Chỉ cần với repository riêng tư',
  tokenHint: 'Chỉ cần quyền đọc (Contents: Read).',
  inspect: 'Đọc thử',
  reading: 'Đang tải và chuyển đổi repository…',
  found: 'Kết quả chuyển đổi',
  models: 'bảng dữ liệu',
  sources: 'bảng nguồn',
  tests: 'kiểm tra',
  noSources: 'Project này không khai báo bảng nguồn nào.',
  warnings: '{n} điểm cần biết trước khi import',
  connect: {
    systemTitle: 'Hệ thống dữ liệu',
    systemHelp: 'Repository sẽ được đối chiếu với hệ thống này.',
    connectionTitle: 'Kết nối',
    connectionHelp: 'Chọn trước khi đọc repository, để các bảng nguồn được đối chiếu với đúng kết nối này.',
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
    credentialsHint: 'Nếu bảng nguồn nằm ở project khác, hãy dùng kết nối đọc được project đó và ghi được nơi chứa kết quả.',
    host: 'Host / IP',
    port: 'Cổng',
    database: 'Database',
    username: 'Tài khoản',
    password: 'Mật khẩu',
    oauthHint: 'Đăng nhập bằng tài khoản Google có quyền trên project BigQuery.',
    oauthStart: 'Đăng nhập với Google',
    oauthDone: 'Đã đăng nhập:',
    save: 'Kiểm tra và lưu',
    cancel: 'Hủy',
    remove: 'Xoá kết nối',
    loadFailed: 'Không tải được danh sách kết nối',
  },
  createTitle: 'Tạo Transform',
  createHelp: 'Đặt tên và chọn nơi Transform ghi các bảng sẽ sinh ra.',
  name: 'Tên Transform',
  output: 'Nơi chứa kết quả',
  keepPulling: 'Tự động lấy code mới khi repository có commit mới',
  keepPullingHint: 'Một chiều — chỉ đọc về, không bao giờ ghi ngược lên GitHub. Có thể tắt sau trong Cài đặt.',
  checkEvery: 'Kiểm tra mỗi (phút)',
  startOver: 'Đọc repository khác',
  createAction: 'Import',
  created: 'Đã import {n} bảng dữ liệu',
  layerLabel: { STAGING: 'Làm sạch', CORE: 'Tổng hợp', MART: 'Báo cáo' } as Record<string, string>,
};

const en: typeof vi = {
  back: 'Transform',
  title: 'Import from GitHub',
  subtitle: 'Turn an existing dbt or Dataform project into a Transform',
  repository: 'Repository',
  repositoryHelp: 'Paste a GitHub repository address. Whether it is dbt or Dataform is worked out for you, and the repository is only ever read from.',
  repoUrl: 'Repository address',
  branch: 'Branch or tag',
  branchPlaceholder: 'Leave blank for the default branch',
  subdirectory: 'Subdirectory',
  subdirectoryPlaceholder: 'If the project lives in a subfolder',
  token: 'GitHub access token',
  tokenPlaceholder: 'Only needed for a private repository',
  tokenHint: 'Read access is enough (Contents: Read).',
  inspect: 'Read it',
  reading: 'Downloading and converting the repository…',
  found: 'What the conversion produced',
  models: 'models',
  sources: 'source tables',
  tests: 'tests',
  noSources: 'This project declares no source tables.',
  warnings: '{n} things to know before importing',
  connect: {
    systemTitle: 'Data system',
    systemHelp: 'The repository is checked against this system.',
    connectionTitle: 'Connection',
    connectionHelp: 'Chosen before the repository is read, so its source tables are checked against it.',
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
      inherited: 'Shared with the Destination',
    } as Record<string, string>,
    project: 'Project',
    location: 'Data location',
    credentials: 'Service account JSON',
    credentialsHint: 'If the source tables live in another project, use a connection that can read it and write where the results go.',
    host: 'Host / IP',
    port: 'Port',
    database: 'Database',
    username: 'User',
    password: 'Password',
    oauthHint: 'Sign in with a Google account that has access to the BigQuery project.',
    oauthStart: 'Sign in with Google',
    oauthDone: 'Signed in as',
    save: 'Check and save',
    cancel: 'Cancel',
    remove: 'Remove connection',
    loadFailed: 'Could not load the connections',
  },
  createTitle: 'Create the Transform',
  createHelp: 'Name it, and choose where its models write to.',
  name: 'Transform name',
  output: 'Where results go',
  keepPulling: 'Pull new commits from this repository automatically',
  keepPullingHint: 'One direction — read only, never written back to GitHub. Can be turned off later in the settings.',
  checkEvery: 'Check every (minutes)',
  startOver: 'Read a different repository',
  createAction: 'Import',
  created: 'Imported {n} models',
  layerLabel: { STAGING: 'Staging', CORE: 'Core', MART: 'Mart' } as Record<string, string>,
};
