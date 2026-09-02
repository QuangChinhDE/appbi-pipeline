'use client';

/**
 * New Transform project: three steps, in the order the blueprint sets out.
 *
 *   1. Where the project comes from
 *   2. Which warehouse it runs on
 *   3. What to call it, and where it writes
 *
 * "Connect an existing Git repository" does not convert anything. The repository
 * is checked out as it is and stays a dbt project -- which is why step 1 offers
 * inspection rather than a conversion preview.
 */

import * as React from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ArrowLeft, ArrowRight, CircleAlert, FilePlus2, FolderGit2, Loader2,
  PackageOpen, Search,
} from 'lucide-react';

import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { ConnectionPicker } from '@/components/transforms/ConnectionPicker';
import { useWorkspaceId } from '@/hooks/use-current-user';
import { toastError, toastSuccess } from '@/hooks/use-toast';
import { transformApi } from '@/lib/api';
import { qk } from '@/lib/queryKeys';
import type { RepositoryInspectResult } from '@/lib/types';
import { cn } from '@/lib/utils';

type Source = 'NEW' | 'GIT' | 'UPLOAD';

const SOURCES: { id: Source; title: string; description: string; icon: typeof FilePlus2 }[] = [
  {
    id: 'NEW',
    title: 'Tạo dự án dbt mới',
    description: 'Bắt đầu từ một dự án dbt chuẩn, có sẵn model và test mẫu.',
    icon: FilePlus2,
  },
  {
    id: 'GIT',
    title: 'Kết nối repository đã có',
    description:
      'Lấy nguyên dự án dbt từ GitHub. Không chuyển đổi gì — vẫn là dự án đó.',
    icon: FolderGit2,
  },
  {
    id: 'UPLOAD',
    title: 'Tải lên dự án dbt',
    description: 'Nhận tệp ZIP chứa dbt_project.yml và các thư mục của nó.',
    icon: PackageOpen,
  },
];

export default function NewTransformPage() {
  const router = useRouter();
  const workspaceId = useWorkspaceId();
  const queryClient = useQueryClient();

  const [step, setStep] = React.useState(1);
  const [source, setSource] = React.useState<Source>('NEW');
  const [connectionId, setConnectionId] = React.useState<string | null>(null);

  const [name, setName] = React.useState('');
  const [dbtProjectName, setDbtProjectName] = React.useState('');
  const [devSchema, setDevSchema] = React.useState('');
  const [prodSchema, setProdSchema] = React.useState('');
  const [sourceSchema, setSourceSchema] = React.useState('raw');
  const [perUser, setPerUser] = React.useState(false);
  const [withExamples, setWithExamples] = React.useState(true);

  const [repoUrl, setRepoUrl] = React.useState('');
  const [branch, setBranch] = React.useState('');
  const [subdirectory, setSubdirectory] = React.useState('');
  const [token, setToken] = React.useState('');
  const [autoPull, setAutoPull] = React.useState(false);
  const [inspection, setInspection] = React.useState<RepositoryInspectResult | null>(null);

  const [file, setFile] = React.useState<File | null>(null);

  const { data: systems = [] } = useQuery({
    queryKey: qk.transformSystems(workspaceId),
    queryFn: () => transformApi.systems(),
  });
  const { data: connections = [], refetch: refetchConnections } = useQuery({
    queryKey: qk.transformConnections(workspaceId),
    queryFn: () => transformApi.connections(),
  });

  const inspect = useMutation({
    mutationFn: () => transformApi.inspectRepository({
      repo_url: repoUrl,
      branch: branch || undefined,
      subdirectory: subdirectory || undefined,
      token: token || undefined,
    }),
    onSuccess: (result) => {
      setInspection(result);
      // Prefill from what the repository actually says, so nothing has to be
      // retyped and nothing is guessed.
      if (result.dbt_project_name && !name) setName(result.dbt_project_name);
      if (result.branch && !branch) setBranch(result.branch);
      if (result.detected_root && !subdirectory) setSubdirectory(result.detected_root);
      toastSuccess(`Đã tìm thấy dự án dbt với ${result.model_count} model.`);
    },
    onError: (error) => { setInspection(null); toastError(error); },
  });

  const create = useMutation({
    mutationFn: async () => {
      if (source === 'UPLOAD') {
        if (!file || !connectionId) throw new Error('missing');
        const form = new FormData();
        form.append('file', file);
        const query = new URLSearchParams({
          name, connection_id: connectionId,
          ...(devSchema ? { development_schema: devSchema } : {}),
          ...(prodSchema ? { production_schema: prodSchema } : {}),
        });
        const response = await fetch(
          `/api/v1/transforms/upload?${query.toString()}`,
          { method: 'POST', body: form, credentials: 'include' },
        );
        if (!response.ok) {
          const payload = await response.json().catch(() => null);
          throw new Error(payload?.error?.message ?? 'Không tải lên được.');
        }
        return response.json();
      }
      return transformApi.create({
        name,
        connection_id: connectionId!,
        source,
        dbt_project_name: dbtProjectName || undefined,
        development_schema: devSchema || undefined,
        production_schema: prodSchema || undefined,
        source_schema: sourceSchema || undefined,
        per_user_schemas: perUser,
        with_examples: withExamples,
        repo_url: source === 'GIT' ? repoUrl : undefined,
        branch: source === 'GIT' ? (branch || undefined) : undefined,
        subdirectory: source === 'GIT' ? (subdirectory || undefined) : undefined,
        token: source === 'GIT' ? (token || undefined) : undefined,
        auto_pull: source === 'GIT' ? autoPull : undefined,
      });
    },
    onSuccess: (project: { id: string }) => {
      queryClient.invalidateQueries({ queryKey: ['workspace', workspaceId] });
      toastSuccess('Đã tạo dự án. Đang đọc dự án bằng dbt…');
      router.push(`/transforms/${project.id}`);
    },
    onError: (error) => toastError(error),
  });

  const canAdvance =
    step === 1
      ? (source === 'NEW'
        || (source === 'GIT' && Boolean(inspection))
        || (source === 'UPLOAD' && Boolean(file)))
      : step === 2
        ? Boolean(connectionId)
        : Boolean(name.trim());

  return (
    <div className="mx-auto flex h-full max-w-3xl flex-col px-4 pt-5 sm:px-6">
      <header className="mb-4 shrink-0">
        <Link
          href="/transforms"
          className="mb-2 inline-flex items-center gap-1 text-caption text-text-tertiary hover:text-text-primary"
        >
          <ArrowLeft className="h-3.5 w-3.5" /> Transform
        </Link>
        <h1 className="text-h3 font-strong text-text-primary">Dự án Transform mới</h1>
        <ol className="mt-3 flex items-center gap-2">
          {['Nguồn dự án', 'Kho dữ liệu', 'Thiết lập'].map((label, index) => (
            <li key={label} className="flex items-center gap-2">
              <span
                className={cn(
                  'flex h-5 w-5 items-center justify-center rounded-full text-tiny',
                  step > index + 1
                    ? 'bg-success text-white'
                    : step === index + 1
                      ? 'bg-brand text-text-inverse'
                      : 'bg-surface-2 text-text-tertiary',
                )}
              >
                {index + 1}
              </span>
              <span
                className={cn(
                  'text-caption',
                  step === index + 1 ? 'text-text-primary font-emphasis' : 'text-text-tertiary',
                )}
              >
                {label}
              </span>
              {index < 2 && <span className="text-text-quaternary">›</span>}
            </li>
          ))}
        </ol>
      </header>

      <div className="min-h-0 flex-1 overflow-auto pb-4">
        {step === 1 && (
          <div className="space-y-3">
            {SOURCES.map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => { setSource(item.id); setInspection(null); }}
                className={cn(
                  'flex w-full items-start gap-3 rounded-lg border p-3 text-left transition-colors',
                  source === item.id
                    ? 'border-brand bg-brand/5'
                    : 'border-[rgb(var(--border-line))] hover:bg-surface-2',
                )}
              >
                <item.icon className="mt-0.5 h-4 w-4 shrink-0 text-brand" />
                <div>
                  <p className="text-small font-emphasis text-text-primary">{item.title}</p>
                  <p className="mt-0.5 text-caption text-text-tertiary">{item.description}</p>
                </div>
              </button>
            ))}

            {source === 'GIT' && (
              <div className="space-y-2.5 rounded-lg border border-[rgb(var(--border-line))] p-3">
                <Field label="Địa chỉ repository">
                  <Input
                    value={repoUrl}
                    onChange={(event) => { setRepoUrl(event.target.value); setInspection(null); }}
                    placeholder="https://github.com/acme/analytics"
                  />
                </Field>
                <div className="grid grid-cols-2 gap-2">
                  <Field label="Nhánh" hint="Bỏ trống để dùng nhánh mặc định">
                    <Input value={branch} onChange={(event) => setBranch(event.target.value)} placeholder="main" />
                  </Field>
                  <Field label="Thư mục con" hint="Nếu dbt_project.yml nằm trong thư mục con">
                    <Input
                      value={subdirectory}
                      onChange={(event) => setSubdirectory(event.target.value)}
                      placeholder="transform"
                    />
                  </Field>
                </div>
                <Field label="Access token" hint="Cần cho repo riêng tư, và để commit ngược lên">
                  <Input
                    type="password" value={token}
                    onChange={(event) => setToken(event.target.value)}
                    placeholder="ghp_…"
                  />
                </Field>
                <Button
                  variant="secondary" size="sm"
                  onClick={() => inspect.mutate()}
                  loading={inspect.isPending}
                  disabled={!repoUrl.trim()}
                  leadingIcon={inspect.isPending
                    ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    : <Search className="h-3.5 w-3.5" />}
                >
                  Kiểm tra repository
                </Button>

                {inspection && (
                  <div className="space-y-2 rounded-md bg-surface-2 p-2.5">
                    <p className="text-caption font-emphasis text-text-primary">
                      Đã tìm thấy dự án dbt
                    </p>
                    <dl className="space-y-0.5 text-tiny">
                      <Pair label="Tên dự án dbt" value={inspection.dbt_project_name ?? '—'} />
                      <Pair label="Thư mục" value={inspection.detected_root || '/'} />
                      <Pair label="Số model" value={String(inspection.model_count)} />
                      <Pair label="Tổng số tệp" value={String(inspection.file_count)} />
                      {inspection.packages.length > 0 && (
                        <Pair label="Package" value={inspection.packages.join(', ')} />
                      )}
                    </dl>
                    <div className="flex flex-wrap gap-1">
                      {inspection.resource_directories.map((directory) => (
                        <Badge key={directory} variant="subtle" size="xs">{directory}</Badge>
                      ))}
                    </div>
                    {inspection.warnings.map((warning) => (
                      <p
                        key={warning}
                        className="flex items-start gap-1.5 text-tiny text-warning"
                      >
                        <CircleAlert className="mt-0.5 h-3 w-3 shrink-0" />
                        {warning}
                      </p>
                    ))}
                    <label className="flex items-center gap-2 text-caption text-text-secondary">
                      <input
                        type="checkbox" checked={autoPull}
                        onChange={(event) => setAutoPull(event.target.checked)}
                        className="h-3.5 w-3.5 accent-[rgb(var(--brand))]"
                      />
                      Tự động lấy commit mới
                    </label>
                  </div>
                )}
              </div>
            )}

            {source === 'UPLOAD' && (
              <div className="rounded-lg border border-[rgb(var(--border-line))] p-3">
                <Field label="Tệp ZIP dự án dbt">
                  <input
                    type="file"
                    accept=".zip"
                    onChange={(event) => setFile(event.target.files?.[0] ?? null)}
                    className="block w-full text-caption text-text-secondary file:mr-3 file:rounded-md file:border-0 file:bg-surface-2 file:px-3 file:py-1.5 file:text-caption file:text-text-primary hover:file:bg-surface-3"
                  />
                </Field>
                {file && (
                  <p className="mt-1.5 text-tiny text-text-tertiary">
                    {file.name} · {Math.round(file.size / 1024)} KB
                  </p>
                )}
              </div>
            )}
          </div>
        )}

        {step === 2 && (
          <ConnectionPicker
            systems={systems}
            connections={connections}
            value={connectionId}
            onChange={setConnectionId}
            onCreated={() => refetchConnections()}
          />
        )}

        {step === 3 && (
          <div className="space-y-3">
            <Field label="Tên dự án">
              <Input
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="Phân tích bán hàng"
                autoFocus
              />
            </Field>

            {source === 'NEW' && (
              <Field
                label="Tên dbt project"
                hint="Tên trong dbt_project.yml. Chỉ chữ thường, số và gạch dưới."
              >
                <Input
                  value={dbtProjectName}
                  onChange={(event) => setDbtProjectName(event.target.value)}
                  placeholder="sales_analytics"
                  className="font-mono"
                />
              </Field>
            )}

            <div className="grid grid-cols-2 gap-2">
              <Field label="Schema khi phát triển" hint="Nơi bản nháp ghi kết quả">
                <Input
                  value={devSchema} onChange={(event) => setDevSchema(event.target.value)}
                  placeholder="analytics_dev" className="font-mono"
                />
              </Field>
              <Field label="Schema chạy thật" hint="Nơi bản đã xuất bản ghi kết quả">
                <Input
                  value={prodSchema} onChange={(event) => setProdSchema(event.target.value)}
                  placeholder="analytics" className="font-mono"
                />
              </Field>
            </div>

            {source === 'NEW' && (
              <Field
                label="Schema dữ liệu nguồn"
                hint="Nơi dữ liệu thô đang nằm, dùng cho source mẫu"
              >
                <Input
                  value={sourceSchema} onChange={(event) => setSourceSchema(event.target.value)}
                  placeholder="raw" className="font-mono"
                />
              </Field>
            )}

            <label className="flex items-start gap-2">
              <input
                type="checkbox" checked={perUser}
                onChange={(event) => setPerUser(event.target.checked)}
                className="mt-0.5 h-3.5 w-3.5 accent-[rgb(var(--brand))]"
              />
              <span className="text-caption text-text-secondary">
                Mỗi người một schema riêng khi phát triển
                <span className="block text-tiny text-text-tertiary">
                  Để hai người cùng sửa một dự án không ghi đè bảng của nhau.
                </span>
              </span>
            </label>

            {source === 'NEW' && (
              <label className="flex items-start gap-2">
                <input
                  type="checkbox" checked={withExamples}
                  onChange={(event) => setWithExamples(event.target.checked)}
                  className="mt-0.5 h-3.5 w-3.5 accent-[rgb(var(--brand))]"
                />
                <span className="text-caption text-text-secondary">
                  Tạo kèm model và test mẫu
                  <span className="block text-tiny text-text-tertiary">
                    Một staging model, một mart, và YAML đi kèm — để có thứ chạy thử ngay.
                  </span>
                </span>
              </label>
            )}
          </div>
        )}
      </div>

      <div className="flex shrink-0 items-center justify-between border-t border-[rgb(var(--border-line))] py-3">
        <Button
          variant="ghost"
          onClick={() => (step === 1 ? router.push('/transforms') : setStep(step - 1))}
        >
          {step === 1 ? 'Huỷ' : 'Quay lại'}
        </Button>
        {step < 3 ? (
          <Button
            variant="primary"
            disabled={!canAdvance}
            onClick={() => setStep(step + 1)}
            trailingIcon={<ArrowRight className="h-4 w-4" />}
          >
            Tiếp tục
          </Button>
        ) : (
          <Button
            variant="primary"
            disabled={!canAdvance}
            loading={create.isPending}
            onClick={() => create.mutate()}
          >
            Tạo dự án
          </Button>
        )}
      </div>
    </div>
  );
}

function Field({
  label, hint, children,
}: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-caption text-text-secondary">{label}</span>
      {children}
      {hint && <span className="mt-0.5 block text-tiny text-text-quaternary">{hint}</span>}
    </label>
  );
}

function Pair({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex gap-2">
      <dt className="w-32 shrink-0 text-text-tertiary">{label}</dt>
      <dd className="min-w-0 flex-1 break-all text-text-secondary">{value}</dd>
    </div>
  );
}
