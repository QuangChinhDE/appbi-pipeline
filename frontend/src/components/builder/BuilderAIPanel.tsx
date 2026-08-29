'use client';

import * as React from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  AlertTriangle, Check, CornerDownRight, FileText, Link2, RefreshCw, RotateCcw,
  Send, Sparkles, TestTube2, X,
} from 'lucide-react';

import { builderAiApi } from '@/lib/api';
import type {
  BuilderAIChangeSet, BuilderProjectDetail,
} from '@/lib/types';
import { useI18n } from '@/providers/LanguageProvider';
import { toastError, toastSuccess } from '@/hooks/use-toast';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Textarea } from '@/components/ui/Input';
import { Skeleton } from '@/components/ui/Feedback';

type LocalMessage = { id: string; role: 'user' | 'assistant'; content: string };

export function BuilderAIPanel({
  project,
  streamName,
  section,
  testRunId,
  canEdit,
  onApplied,
  onRetest,
}: {
  project: BuilderProjectDetail;
  streamName?: string;
  section?: string;
  testRunId?: string;
  canEdit: boolean;
  onApplied: (project: BuilderProjectDetail) => void;
  onRetest: (project: BuilderProjectDetail) => Promise<void>;
}) {
  const { t } = useI18n();
  const [composer, setComposer] = React.useState('');
  const [messages, setMessages] = React.useState<LocalMessage[]>([]);
  const [changeSet, setChangeSet] = React.useState<BuilderAIChangeSet | null>(null);
  const [progress, setProgress] = React.useState('');
  const [sending, setSending] = React.useState(false);
  const [deciding, setDeciding] = React.useState<string | null>(null);
  const [inlineError, setInlineError] = React.useState('');

  const session = useQuery({
    queryKey: ['builder-ai-session', project.id],
    queryFn: () => builderAiApi.session(project.id),
  });

  React.useEffect(() => {
    if (!session.data) return;
    setMessages(session.data.messages.map((item) => ({
      id: item.id, role: item.role, content: item.content,
    })));
    setChangeSet(session.data.change_set);
  }, [session.data]);

  const send = async (override?: string) => {
    const message = (override ?? composer).trim();
    if (!message || sending) return;
    setComposer(''); setInlineError(''); setSending(true);
    setMessages((current) => [...current, {
      id: `local-${Date.now()}`, role: 'user', content: message,
    }]);
    try {
      await builderAiApi.chat(project.id, {
        message, stream_name: streamName, section, test_run_id: testRunId,
      }, (event, data) => {
        if (event === 'progress') {
          setProgress(String(data.message ?? ''));
          return;
        }
        setMessages((current) => [...current, {
          id: `assistant-${Date.now()}`,
          role: 'assistant', content: String(data.message ?? ''),
        }]);
        setChangeSet((data.change_set as BuilderAIChangeSet | null) ?? null);
      });
    } catch (error) {
      setInlineError((error as Error).message);
    } finally {
      setSending(false); setProgress('');
    }
  };

  const decide = async (action: 'apply' | 'reject' | 'undo', retest = false) => {
    if (!changeSet) return;
    setDeciding(action); setInlineError('');
    try {
      const result = action === 'apply'
        ? await builderAiApi.apply(project.id, changeSet.id)
        : action === 'reject'
          ? await builderAiApi.reject(project.id, changeSet.id)
          : await builderAiApi.undo(project.id, changeSet.id);
      setChangeSet(result.change_set);
      onApplied(result.project);
      toastSuccess(t(`builder.aiChange.${action}Success`));
      if (action === 'apply' && retest) await onRetest(result.project);
    } catch (error) {
      toastError(error);
      setInlineError((error as Error).message);
    } finally { setDeciding(null); }
  };

  const quickActions = [
    { label: t('builder.aiQuickExplain'), value: t('builder.aiPromptExplain') },
    { label: t('builder.aiQuickReview'), value: t('builder.aiPromptReview') },
    ...(testRunId ? [{ label: t('builder.aiQuickFix'), value: t('builder.aiPromptFix') }] : []),
  ];
  const aiAvailable = session.data?.available === true;
  const aiUnavailable = session.isError || session.data?.available === false;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-1.5" aria-label={t('builder.aiContext')}>
        <Badge variant="outline" size="xs">{project.name}</Badge>
        {streamName && <Badge variant="subtle" size="xs">{streamName}</Badge>}
        {section && <Badge variant="subtle" size="xs">{section}</Badge>}
        {testRunId && <Badge variant="success" size="xs" dot>{t('builder.aiTestAttached')}</Badge>}
      </div>

      {session.data?.sources.length ? (
        <div className="flex min-w-0 items-center gap-1.5 text-tiny text-text-quaternary">
          <FileText className="h-3.5 w-3.5 shrink-0" aria-hidden />
          <span className="shrink-0">{t('builder.aiSourcesAttached')}</span>
          <span className="truncate text-text-tertiary">
            {session.data.sources.map((item) => item.name).join(', ')}
          </span>
        </div>
      ) : null}

      {aiUnavailable && (
        <div className="flex items-start gap-2 rounded-md border border-warning/20 bg-warning/5 px-3 py-2.5">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warning" aria-hidden />
          <div className="min-w-0 flex-1">
            <p className="text-caption font-emphasis text-text-primary">{t('builder.aiUnavailableTitle')}</p>
            <p className="mt-0.5 text-tiny leading-relaxed text-text-tertiary">
              {session.error?.message || t('builder.aiUnavailable')}
            </p>
          </div>
          <Button size="xs" variant="ghost" leadingIcon={<RefreshCw className="h-3 w-3" />}
                  onClick={() => void session.refetch()}>
            {t('common.retry')}
          </Button>
        </div>
      )}

      <div className="flex flex-wrap gap-1.5">
        {quickActions.map((item) => (
          <Button key={item.label} size="xs" variant="subtle"
                  disabled={sending || !canEdit || !aiAvailable}
                  onClick={() => void send(item.value)}>
            {item.label}
          </Button>
        ))}
      </div>

      <div className="max-h-[42vh] min-h-48 space-y-3 overflow-y-auto border-y border-[rgb(var(--border-line))] py-3">
        {session.isLoading ? (
          <div className="space-y-2"><Skeleton className="h-12 w-full" /><Skeleton className="h-20 w-full" /></div>
        ) : messages.length === 0 ? (
          <div className="px-2 py-8 text-center">
            <span className="mx-auto flex h-8 w-8 items-center justify-center rounded-md bg-brand/10 text-brand">
              <Sparkles className="h-4 w-4" />
            </span>
            <p className="mt-2 text-caption font-emphasis text-text-secondary">{t('builder.aiEmptyTitle')}</p>
            <p className="mx-auto mt-1 max-w-64 text-tiny leading-relaxed text-text-quaternary">
              {t('builder.aiEmpty')}
            </p>
          </div>
        ) : messages.map((message) => (
          <div key={message.id} className={message.role === 'user' ? 'pl-7' : 'pr-2'}>
            <div className={message.role === 'user'
              ? 'rounded-md bg-surface-2 px-3 py-2 text-caption leading-relaxed text-text-secondary'
              : 'text-caption leading-relaxed text-text-secondary'}>
              <p className="whitespace-pre-wrap break-words">{message.content}</p>
            </div>
          </div>
        ))}
        {sending && (
          <div className="flex items-center gap-2 text-tiny text-text-quaternary">
            <span className="h-3 w-3 animate-spin rounded-full border-2 border-brand border-t-transparent" />
            {progress || t('builder.aiThinking')}
          </div>
        )}
      </div>

      {changeSet && ['PROPOSED', 'APPLIED'].includes(changeSet.status) && (
        <div className="rounded-md border border-[rgb(var(--border-strong))] bg-surface-2/50 p-3">
          <div className="flex items-start gap-2">
            <CornerDownRight className="mt-0.5 h-3.5 w-3.5 shrink-0 text-brand" />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <p className="text-caption font-strong text-text-primary">{t('builder.aiChangeTitle')}</p>
                <Badge variant={changeSet.status === 'APPLIED' ? 'success' : 'brand'} size="xs">
                  {t(`builder.aiChange.${changeSet.status.toLowerCase()}`)}
                </Badge>
              </div>
              <p className="mt-1 text-tiny leading-relaxed text-text-tertiary">{changeSet.reason}</p>
            </div>
          </div>
          <div className="mt-2 divide-y divide-[rgb(var(--border-line))] border-y border-[rgb(var(--border-line))]">
            {changeSet.operations.slice(0, 8).map((operation, index) => (
              <div key={`${operation.path}-${index}`} className="flex gap-2 py-1.5 text-tiny">
                <Badge variant="outline" size="xs" pill={false}>{operation.op}</Badge>
                <span className="min-w-0 flex-1 truncate text-text-secondary">{operation.label}</span>
                <code className="max-w-32 truncate text-text-quaternary">{operation.path}</code>
              </div>
            ))}
          </div>
          {changeSet.evidence.length > 0 && (
            <div className="mt-2 space-y-1">
              {changeSet.evidence.slice(0, 4).map((evidence, index) => (
                <div key={`${evidence.source_id}-${index}`}
                     className="flex items-start gap-1.5 text-tiny leading-relaxed text-text-tertiary">
                  {evidence.location.startsWith('http')
                    ? <Link2 className="mt-0.5 h-3 w-3 shrink-0" aria-hidden />
                    : <FileText className="mt-0.5 h-3 w-3 shrink-0" aria-hidden />}
                  <span><span className="font-emphasis text-text-secondary">{evidence.location}</span>
                    {' '}{evidence.detail}</span>
                </div>
              ))}
            </div>
          )}
          {changeSet.status === 'PROPOSED' ? (
            <div className="mt-3 flex flex-wrap gap-2">
              <Button size="xs" variant="primary" loading={deciding === 'apply'}
                      disabled={!canEdit} leadingIcon={<Check className="h-3 w-3" />}
                      onClick={() => void decide('apply')}>
                {t('builder.aiApply')}
              </Button>
              <Button size="xs" variant="secondary" disabled={!canEdit || Boolean(deciding)}
                      leadingIcon={<TestTube2 className="h-3 w-3" />}
                      onClick={() => void decide('apply', true)}>
                {t('builder.aiApplyRetest')}
              </Button>
              <Button size="xs" variant="ghost" loading={deciding === 'reject'}
                      disabled={!canEdit} leadingIcon={<X className="h-3 w-3" />}
                      onClick={() => void decide('reject')}>
                {t('builder.aiReject')}
              </Button>
            </div>
          ) : (
            <Button className="mt-3" size="xs" variant="ghost" loading={deciding === 'undo'}
                    disabled={!canEdit} leadingIcon={<RotateCcw className="h-3 w-3" />}
                    onClick={() => void decide('undo')}>
              {t('builder.aiUndo')}
            </Button>
          )}
        </div>
      )}

      {inlineError && (
        <p className="rounded-md bg-danger/5 px-3 py-2 text-tiny leading-relaxed text-danger" role="alert">
          {inlineError}
        </p>
      )}

      <div className="relative">
        <Textarea value={composer} rows={3} disabled={!canEdit || sending || !aiAvailable}
                  placeholder={t('builder.aiComposerPlaceholder')}
                  className="min-h-20 pr-10"
                  onChange={(event) => setComposer(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' && !event.shiftKey) {
                      event.preventDefault(); void send();
                    }
                  }} />
        <Button size="xs" variant="primary" aria-label={t('builder.aiSend')}
                className="absolute bottom-2 right-2 h-7 w-7 px-0"
                disabled={!composer.trim() || sending || !canEdit || !aiAvailable}
                onClick={() => void send()}>
          <Send className="h-3.5 w-3.5" />
        </Button>
      </div>
      <p className="text-tiny text-text-quaternary">{t('builder.aiReviewNotice')}</p>
    </div>
  );
}
