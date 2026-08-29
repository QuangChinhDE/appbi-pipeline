'use client';

import * as React from 'react';
import {
  ChevronDown, ChevronUp, FileText, Link2, Sparkles, Trash2, Upload, WandSparkles,
} from 'lucide-react';

import { builderAiApi, builderApi } from '@/lib/api';
import type {
  BuilderAIConfidence, BuilderAIPlan, BuilderAISource, BuilderIconKey, BuilderProjectDetail,
} from '@/lib/types';
import { useI18n } from '@/providers/LanguageProvider';
import { toastError } from '@/hooks/use-toast';
import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/Badge';
import { Button, IconButton } from '@/components/ui/Button';
import { Checkbox, Input, Label, Textarea } from '@/components/ui/Input';
import { Modal } from '@/components/ui/Modal';
import { SegmentedControl } from '@/components/ui/Tabs';
import { BuilderIconPicker } from '@/components/builder/BuilderIconPicker';

function ConfidenceBadge({ value }: { value: BuilderAIConfidence }) {
  const { t } = useI18n();
  const variant = value === 'confirmed' ? 'success' : value === 'likely' ? 'warning' : 'subtle';
  return <Badge variant={variant} size="xs">{t(`builder.aiConfidence.${value}`)}</Badge>;
}

export function BuilderCreateDialog({
  open, onClose, onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (project: BuilderProjectDetail) => void;
}) {
  const { t } = useI18n();
  const [mode, setMode] = React.useState<'ai' | 'manual'>('ai');
  const [name, setName] = React.useState('');
  const [description, setDescription] = React.useState('');
  const [icon, setIcon] = React.useState<BuilderIconKey>('api');
  const [url, setUrl] = React.useState('');
  const [intent, setIntent] = React.useState('');
  const [sources, setSources] = React.useState<BuilderAISource[]>([]);
  const [plan, setPlan] = React.useState<BuilderAIPlan | null>(null);
  const [planName, setPlanName] = React.useState('');
  const [planDescription, setPlanDescription] = React.useState('');
  const [streamReview, setStreamReview] = React.useState<
    { source_name: string; name: string; enabled: boolean }[]
  >([]);
  const [evidenceStream, setEvidenceStream] = React.useState<string | null>(null);
  const [dragging, setDragging] = React.useState(false);
  const [busy, setBusy] = React.useState<string | null>(null);
  const inputRef = React.useRef<HTMLInputElement>(null);
  const selectedStreamNames = streamReview
    .filter((item) => item.enabled)
    .map((item) => item.name.trim().toLocaleLowerCase());
  const canCreatePlan = Boolean(
    planName.trim()
    && selectedStreamNames.length
    && selectedStreamNames.every(Boolean)
    && new Set(selectedStreamNames).size === selectedStreamNames.length,
  );
  const streamReviewError = !selectedStreamNames.length
    ? t('builder.aiStreamRequired')
    : selectedStreamNames.some((item) => !item)
      ? t('builder.aiStreamNameRequired')
      : new Set(selectedStreamNames).size !== selectedStreamNames.length
        ? t('builder.aiStreamNameDuplicate') : '';

  const reset = React.useCallback(() => {
    setMode('ai'); setName(''); setDescription(''); setIcon('api');
    setUrl(''); setIntent(''); setSources([]); setPlan(null); setBusy(null);
    setPlanName(''); setPlanDescription(''); setStreamReview([]); setEvidenceStream(null);
    setDragging(false);
  }, []);

  const close = () => {
    const temporarySources = sources;
    const temporaryPlan = plan;
    reset();
    onClose();
    // Sources created inside this modal have no project owner until a draft is
    // created. Closing the flow removes them instead of leaving document blobs
    // that the user can no longer reach from the UI.
    void Promise.allSettled([
      ...(temporaryPlan ? [builderAiApi.removePlan(temporaryPlan.id)] : []),
      ...temporarySources.map((source) => builderAiApi.removeSource(source.id)),
    ]);
  };

  const discardPlan = () => {
    if (plan) void builderAiApi.removePlan(plan.id).catch(toastError);
    setPlan(null); setPlanName(''); setPlanDescription(''); setStreamReview([]);
    setEvidenceStream(null);
  };

  const upload = async (files: FileList | null) => {
    if (!files?.length) return;
    setBusy('source');
    try {
      const uploaded: BuilderAISource[] = [];
      for (const file of Array.from(files).slice(0, 10)) {
        uploaded.push(await builderAiApi.uploadSource(file));
      }
      setSources((current) => [...current, ...uploaded]);
      setPlan(null);
    } catch (error) { toastError(error); } finally {
      setBusy(null);
      if (inputRef.current) inputRef.current.value = '';
    }
  };

  const addUrl = async () => {
    if (!url.trim()) return;
    setBusy('url');
    try {
      const source = await builderAiApi.addUrl(url.trim());
      setSources((current) => [...current, source]);
      setUrl(''); setPlan(null);
    } catch (error) { toastError(error); } finally { setBusy(null); }
  };

  const removeSource = async (source: BuilderAISource) => {
    setBusy(source.id);
    try {
      await builderAiApi.removeSource(source.id);
      setSources((current) => current.filter((item) => item.id !== source.id));
      setPlan(null);
    } catch (error) { toastError(error); } finally { setBusy(null); }
  };

  const generatePlan = async () => {
    if (!sources.length) return;
    setBusy('plan');
    try {
      const next = await builderAiApi.createPlan(sources.map((item) => item.id), intent.trim());
      setPlan(next);
      setIcon(next.plan.icon);
      setPlanName(next.plan.name);
      setPlanDescription(next.plan.description);
      setStreamReview(next.plan.streams.map((stream) => ({
        source_name: stream.name, name: stream.name, enabled: true,
      })));
    } catch (error) { toastError(error); } finally { setBusy(null); }
  };

  const createManual = async () => {
    if (!name.trim()) return;
    setBusy('create');
    try {
      onCreated(await builderApi.create({
        name: name.trim(), description: description.trim() || undefined, icon,
      }));
      reset();
    } catch (error) { toastError(error); } finally { setBusy(null); }
  };

  const createFromPlan = async () => {
    if (!plan) return;
    setBusy('create');
    try {
      onCreated(await builderAiApi.createProject(plan.id, {
        name: planName.trim(), description: planDescription.trim(), icon, streams: streamReview,
      }));
      reset();
    } catch (error) { toastError(error); } finally { setBusy(null); }
  };

  return (
    <Modal
      open={open}
      onClose={close}
      title={t('builder.createTitle')}
      description={t('builder.createDescription')}
      size="lg"
      footer={mode === 'manual' ? (
        <>
          <Button size="sm" variant="ghost" onClick={close}>{t('common.cancel')}</Button>
          <Button size="sm" variant="primary" loading={busy === 'create'}
                  disabled={!name.trim()} onClick={createManual}>
            {t('builder.createAndOpen')}
          </Button>
        </>
      ) : plan ? (
        <>
          <Button size="sm" variant="ghost" onClick={discardPlan}>{t('common.back')}</Button>
          <Button size="sm" variant="primary" loading={busy === 'create'}
                  leadingIcon={<WandSparkles className="h-3.5 w-3.5" />}
                  disabled={!canCreatePlan}
                  onClick={createFromPlan}>
            {t('builder.aiCreateDraft')}
          </Button>
        </>
      ) : undefined}
    >
      <div className="space-y-5">
        <SegmentedControl
          value={mode}
          onChange={(value) => { setMode(value); setPlan(null); }}
          options={[
            { value: 'ai', label: t('builder.aiRecommended') },
            { value: 'manual', label: t('builder.manual') },
          ]}
        />

        {mode === 'manual' ? (
          <div className="space-y-4">
            <div>
              <Label htmlFor="builder-name" required>{t('builder.nameLabel')}</Label>
              <Input id="builder-name" value={name} autoFocus
                     placeholder={t('builder.namePlaceholder')}
                     onChange={(event) => setName(event.target.value)} />
            </div>
            <div>
              <Label htmlFor="builder-description">{t('common.description')}</Label>
              <Textarea id="builder-description" value={description}
                        onChange={(event) => setDescription(event.target.value)} />
            </div>
            <BuilderIconPicker value={icon} onChange={setIcon} label={t('builder.iconLabel')} />
          </div>
        ) : plan ? (
          <div className="space-y-4">
            <div className="flex items-start gap-3 border-b border-[rgb(var(--border-line))] pb-4">
              <Sparkles className="mt-0.5 h-4 w-4 shrink-0 text-brand" />
              <div className="min-w-0 flex-1 space-y-2">
                <Input size="sm" value={planName} aria-label={t('builder.nameLabel')}
                       onChange={(event) => setPlanName(event.target.value)} />
                <Textarea rows={2} value={planDescription} aria-label={t('common.description')}
                          onChange={(event) => setPlanDescription(event.target.value)} />
                <p className="mt-2 break-all font-mono text-tiny text-text-quaternary">
                  {plan.plan.base_url}
                </p>
              </div>
            </div>
            <div>
              <div className="mb-2 flex items-center justify-between gap-2">
                <h3 className="text-caption font-strong text-text-primary">
                  {t('builder.aiPlanStreamsSelected', {
                    selected: String(streamReview.filter((item) => item.enabled).length),
                    total: String(plan.plan.streams.length),
                  })}
                </h3>
                <span className="text-tiny text-text-quaternary">
                  {t('builder.aiAuth')}: {plan.plan.auth.method}
                </span>
              </div>
              <div className="divide-y divide-[rgb(var(--border-line))] rounded-md border border-[rgb(var(--border-line))]">
                {plan.plan.streams.map((stream) => {
                  const review = streamReview.find((item) => item.source_name === stream.name);
                  const evidenceOpen = evidenceStream === stream.name;
                  return (
                    <div key={`${stream.http_method}-${stream.name}`} className="px-3 py-2.5">
                      <div className="flex items-center gap-2">
                        <Checkbox checked={review?.enabled ?? true}
                                  aria-label={t('builder.aiSelectStream', { name: stream.name })}
                                  onChange={(enabled) => setStreamReview((current) => current.map((item) => (
                                    item.source_name === stream.name ? { ...item, enabled } : item
                                  )))} />
                        <Badge variant={stream.http_method === 'POST' ? 'info' : 'outline'}
                               size="xs" pill={false}>{stream.http_method}</Badge>
                        <Input size="sm" className="min-w-0 flex-1" value={review?.name ?? stream.name}
                               disabled={!review?.enabled}
                               aria-label={t('builder.aiRenameStream', { name: stream.name })}
                               onChange={(event) => setStreamReview((current) => current.map((item) => (
                                 item.source_name === stream.name ? { ...item, name: event.target.value } : item
                               )))} />
                        <ConfidenceBadge value={stream.confidence} />
                      </div>
                      <div className="ml-6 mt-1 flex min-w-0 items-center justify-between gap-2">
                        <p className="truncate font-mono text-tiny text-text-quaternary">{stream.path}</p>
                        {stream.evidence.length > 0 && (
                          <Button size="xs" variant="ghost"
                                  trailingIcon={evidenceOpen
                                    ? <ChevronUp className="h-3 w-3" />
                                    : <ChevronDown className="h-3 w-3" />}
                                  onClick={() => setEvidenceStream(evidenceOpen ? null : stream.name)}>
                            {t('builder.aiEvidence')}
                          </Button>
                        )}
                      </div>
                      {evidenceOpen && (
                        <div className="ml-6 mt-2 space-y-1 border-l-2 border-brand/20 pl-2.5">
                          {stream.evidence.slice(0, 4).map((evidence, index) => (
                            <p key={`${evidence.source_id}-${index}`}
                               className="text-tiny leading-relaxed text-text-tertiary">
                              <span className="font-emphasis text-text-secondary">{evidence.location}</span>
                              {' '}{evidence.detail}
                            </p>
                          ))}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
              {streamReviewError && (
                <p className="mt-1.5 text-tiny text-danger" role="alert">{streamReviewError}</p>
              )}
            </div>
            {(plan.plan.unknowns.length > 0 || plan.plan.assumptions.length > 0) && (
              <div className="rounded-md bg-warning/5 px-3 py-2.5 text-tiny leading-relaxed text-text-secondary">
                {[...plan.plan.unknowns, ...plan.plan.assumptions].slice(0, 6).map((item) => (
                  <p key={item}>• {item}</p>
                ))}
              </div>
            )}
            <BuilderIconPicker value={icon} onChange={setIcon} label={t('builder.iconLabel')} />
          </div>
        ) : (
          <div className="space-y-4">
            <div>
              <h3 className="text-caption font-strong text-text-primary">{t('builder.aiSourcesTitle')}</h3>
              <p className="mt-0.5 text-tiny leading-relaxed text-text-quaternary">
                {t('builder.aiSourcesHint')}
              </p>
            </div>
            <div className="grid gap-2 sm:grid-cols-[1fr_auto]">
              <Input value={url} leadingIcon={<Link2 />} placeholder="https://docs.example.com/api"
                     onChange={(event) => setUrl(event.target.value)}
                     onKeyDown={(event) => {
                       if (event.key === 'Enter') { event.preventDefault(); void addUrl(); }
                     }} />
              <Button size="md" variant="secondary" loading={busy === 'url'}
                      disabled={!url.trim()} onClick={addUrl}>{t('builder.aiAddUrl')}</Button>
            </div>
            <input ref={inputRef} className="sr-only" type="file" multiple
                   accept=".txt,.md,.html,.json,.yaml,.yml,.pdf,.png,.jpg,.jpeg,.webp"
                   onChange={(event) => void upload(event.target.files)} />
            <div
              className={cn(
                'flex min-h-28 flex-col items-center justify-center rounded-lg border border-dashed px-4 py-5 text-center transition-colors',
                dragging
                  ? 'border-brand bg-brand/5'
                  : 'border-[rgb(var(--border-strong))] bg-surface-2/40',
              )}
              onDragEnter={(event) => { event.preventDefault(); setDragging(true); }}
              onDragOver={(event) => { event.preventDefault(); setDragging(true); }}
              onDragLeave={(event) => {
                if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false);
              }}
              onDrop={(event) => {
                event.preventDefault(); setDragging(false); void upload(event.dataTransfer.files);
              }}
            >
              <Upload className="h-4 w-4 text-text-tertiary" aria-hidden />
              <p className="mt-2 text-caption font-emphasis text-text-secondary">
                {t('builder.aiDropTitle')}
              </p>
              <p className="mt-0.5 text-tiny text-text-quaternary">{t('builder.aiDropHint')}</p>
              <Button className="mt-3" size="xs" variant="secondary"
                      loading={busy === 'source'} onClick={() => inputRef.current?.click()}>
                {t('builder.aiUpload')}
              </Button>
            </div>

            {sources.length > 0 && (
              <div className="divide-y divide-[rgb(var(--border-line))] rounded-md border border-[rgb(var(--border-line))]">
                {sources.map((source) => (
                  <div key={source.id} className="flex min-w-0 items-center gap-2 px-3 py-2">
                    {source.source_type === 'URL'
                      ? <Link2 className="h-3.5 w-3.5 shrink-0 text-text-tertiary" />
                      : <FileText className="h-3.5 w-3.5 shrink-0 text-text-tertiary" />}
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-caption text-text-primary">{source.name}</p>
                      <p className="text-tiny text-text-quaternary">
                        {(source.size_bytes / 1024).toFixed(1)} KB
                      </p>
                    </div>
                    <IconButton size="xs" variant="ghost" aria-label={t('common.delete')}
                                loading={busy === source.id}
                                onClick={() => void removeSource(source)}>
                      <Trash2 className="h-3.5 w-3.5" />
                    </IconButton>
                  </div>
                ))}
              </div>
            )}
            <div>
              <Label htmlFor="builder-ai-intent">{t('builder.aiIntent')}</Label>
              <Textarea id="builder-ai-intent" rows={3} value={intent}
                        placeholder={t('builder.aiIntentPlaceholder')}
                        onChange={(event) => setIntent(event.target.value)} />
            </div>
            <Button variant="primary" fullWidth loading={busy === 'plan'}
                    disabled={!sources.length} leadingIcon={<Sparkles className="h-4 w-4" />}
                    onClick={generatePlan}>
              {t('builder.aiGeneratePlan')}
            </Button>
          </div>
        )}
      </div>
    </Modal>
  );
}
