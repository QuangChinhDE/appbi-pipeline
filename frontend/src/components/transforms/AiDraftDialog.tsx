'use client';

import * as React from 'react';
import {
  AlertTriangle, CheckCircle2, Wrench,
} from 'lucide-react';

import type { DataAsset, DraftedModel } from '@/lib/types';
import { cn } from '@/lib/utils';
import { Badge, type BadgeVariant } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Modal } from '@/components/ui/Modal';
import { Label, Select, Textarea } from '@/components/ui/Input';
import { Spinner } from '@/components/ui/Feedback';

export type AiDraftCopy = {
  title: string;
  description: string;
  sourceTable: string;
  intent: string;
  intentPlaceholder: string;
  intentHint: string;
  examples: string;
  draft: string;
  drafting: string;
  draftingHint: string;
  again: string;
  use: string;
  cancel: string;
  back: string;
  summary: string;
  assumptions: string;
  assumptionsHint: string;
  proposedTests: string;
  noTests: string;
  sql: string;
  validationOk: string;
  validationRepaired: string;
  validationFailed: string;
  validationSkipped: string;
  validationOkHint: string;
  validationRepairedHint: string;
  validationFailedHint: string;
  validationSkippedHint: string;
  confidence: Record<'HIGH' | 'MEDIUM' | 'LOW', string>;
  layerLabel: Record<string, string>;
  ruleLabel: Record<string, string>;
  exampleIntents: string[];
};

const VALIDATION_TONE: Record<DraftedModel['validation'], BadgeVariant> = {
  OK: 'success', REPAIRED: 'success', FAILED: 'danger', SKIPPED: 'neutral',
};

/**
 * Draft one model with the assistant, then read it before accepting it.
 *
 * The review step is the point. A drafted model is a proposal about somebody
 * else's data, and the two things a reviewer needs -- did the warehouse agree
 * to run this, and what did the assistant have to guess -- are both invisible
 * in the SQL itself. So they lead, and the SQL follows.
 */
export function AiDraftDialog({
  open, onClose, inputs, copy, drafting, draft, error, onDraft, onAccept, accepting,
}: {
  open: boolean;
  onClose: () => void;
  inputs: DataAsset[];
  copy: AiDraftCopy;
  drafting: boolean;
  draft: DraftedModel | null;
  error: string | null;
  onDraft: (assetId: string, intent: string) => void;
  onAccept: (draft: DraftedModel) => void;
  accepting: boolean;
}) {
  const [assetId, setAssetId] = React.useState('');
  const [intent, setIntent] = React.useState('');

  React.useEffect(() => {
    if (open && !assetId && inputs.length) setAssetId(inputs[0].id);
  }, [open, assetId, inputs]);

  const ready = Boolean(assetId) && intent.trim().length >= 8;
  const reviewing = Boolean(draft) && !drafting;

  const footer = reviewing && draft ? (
    <>
      <Button size="sm" variant="ghost" onClick={onClose}>{copy.cancel}</Button>
      <Button size="sm" variant="secondary" disabled={accepting}
        onClick={() => onDraft(assetId, intent)}>{copy.again}</Button>
      <Button size="sm" variant="primary" loading={accepting}
        onClick={() => onAccept(draft)}>{copy.use}</Button>
    </>
  ) : (
    <>
      <Button size="sm" variant="ghost" onClick={onClose}>{copy.cancel}</Button>
      <Button size="sm" variant="primary" loading={drafting} disabled={!ready}
        onClick={() => onDraft(assetId, intent)}>{copy.draft}</Button>
    </>
  );

  return (
    <Modal open={open} onClose={onClose} title={copy.title}
      description={copy.description} size={reviewing ? 'xl' : 'md'} footer={footer}>
      {reviewing && draft
        ? <Review draft={draft} copy={copy} />
        : <Compose
            inputs={inputs} assetId={assetId} setAssetId={setAssetId}
            intent={intent} setIntent={setIntent} copy={copy}
            drafting={drafting} error={error} />}
    </Modal>
  );
}

function Compose({
  inputs, assetId, setAssetId, intent, setIntent, copy, drafting, error,
}: {
  inputs: DataAsset[];
  assetId: string;
  setAssetId: (value: string) => void;
  intent: string;
  setIntent: (value: string) => void;
  copy: AiDraftCopy;
  drafting: boolean;
  error: string | null;
}) {
  if (drafting) {
    return (
      <div className="flex flex-col items-center gap-2 py-10 text-center">
        <Spinner />
        <p className="text-caption font-emphasis text-text-primary">{copy.drafting}</p>
        <p className="max-w-sm text-tiny text-text-tertiary">{copy.draftingHint}</p>
      </div>
    );
  }
  return (
    <div className="space-y-3">
      <div>
        <Label required>{copy.sourceTable}</Label>
        <Select value={assetId} onChange={(event) => setAssetId(event.target.value)}>
          {inputs.map((asset) => (
            <option key={asset.id} value={asset.id}>{asset.relation_name}</option>
          ))}
        </Select>
      </div>
      <div>
        <Label required>{copy.intent}</Label>
        <Textarea rows={4} value={intent} placeholder={copy.intentPlaceholder}
          onChange={(event) => setIntent(event.target.value)} />
        <p className="mt-1 text-tiny text-text-tertiary">{copy.intentHint}</p>
      </div>
      <div>
        <p className="mb-1.5 text-tiny font-emphasis uppercase tracking-wide text-text-tertiary">
          {copy.examples}
        </p>
        <div className="flex flex-wrap gap-1.5">
          {copy.exampleIntents.map((example) => (
            <button key={example} type="button" onClick={() => setIntent(example)}
              className="rounded-full border border-[rgb(var(--border-line))] px-2.5 py-1 text-tiny text-text-secondary transition-colors hover:border-brand hover:text-text-primary">
              {example}
            </button>
          ))}
        </div>
      </div>
      {error ? (
        <p className="rounded-md border border-danger/30 bg-danger/[0.06] px-2.5 py-2 text-caption text-danger">
          {error}
        </p>
      ) : null}
    </div>
  );
}

function Review({ draft, copy }: { draft: DraftedModel; copy: AiDraftCopy }) {
  const validationText = {
    OK: copy.validationOk, REPAIRED: copy.validationRepaired,
    FAILED: copy.validationFailed, SKIPPED: copy.validationSkipped,
  }[draft.validation];
  const validationHint = {
    OK: copy.validationOkHint, REPAIRED: copy.validationRepairedHint,
    FAILED: copy.validationFailedHint, SKIPPED: copy.validationSkippedHint,
  }[draft.validation];
  const failed = draft.validation === 'FAILED';
  const Icon = failed ? AlertTriangle : draft.validation === 'REPAIRED' ? Wrench : CheckCircle2;

  return (
    <div className="space-y-3">
      {/* Whether the engine accepted this SQL is the first thing a reviewer
          needs and the one thing reading the SQL will not tell them. */}
      <div className={cn(
        "flex items-start gap-2 rounded-md border px-3 py-2",
        failed
          ? "border-danger/30 bg-danger/[0.06]"
          : "border-success/30 bg-success/[0.06]",
      )}>
        <Icon className={cn("mt-0.5 h-4 w-4 shrink-0",
          failed ? "text-danger" : "text-success")} />
        <div className="min-w-0">
          <p className={cn("text-caption font-emphasis",
            failed ? "text-danger" : "text-text-primary")}>{validationText}</p>
          <p className="text-tiny text-text-secondary">{validationHint}</p>
          {draft.validation_error ? (
            <p className="mt-1 break-words font-mono text-tiny text-danger">
              {draft.validation_error}
            </p>
          ) : null}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-caption font-emphasis text-text-primary">
          {draft.name}
        </span>
        <Badge variant="neutral">{copy.layerLabel[draft.layer] ?? draft.layer}</Badge>
        <Badge variant="neutral">{draft.materialization}</Badge>
        <Badge variant={VALIDATION_TONE[draft.validation]}>
          {copy.confidence[draft.confidence]}
        </Badge>
      </div>

      <p className="text-caption text-text-secondary">{draft.summary}</p>

      {draft.assumptions.length ? (
        <section>
          <h4 className="mb-1 text-tiny font-emphasis uppercase tracking-wide text-text-tertiary">
            {copy.assumptions}
          </h4>
          <p className="mb-1.5 text-tiny text-text-tertiary">{copy.assumptionsHint}</p>
          <ul className="space-y-1">
            {draft.assumptions.map((item) => (
              <li key={item} className="flex gap-1.5 text-caption text-text-secondary">
                <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-warning" />
                <span>{item}</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <section>
        <h4 className="mb-1 text-tiny font-emphasis uppercase tracking-wide text-text-tertiary">
          {copy.sql}
        </h4>
        <pre className="max-h-72 overflow-auto rounded-md border border-[rgb(var(--border-line))] bg-surface-2 p-2.5 font-mono text-tiny leading-relaxed text-text-primary">
          {draft.sql}
        </pre>
      </section>

      <section>
        <h4 className="mb-1 text-tiny font-emphasis uppercase tracking-wide text-text-tertiary">
          {copy.proposedTests}
        </h4>
        {draft.tests.length ? (
          <ul className="space-y-1">
            {draft.tests.map((test) => (
              <li key={test.column_name + test.rule}
                className="flex flex-wrap items-baseline gap-x-1.5 text-caption">
                <span className="font-mono text-text-primary">{test.column_name}</span>
                <Badge variant="neutral">{copy.ruleLabel[test.rule] ?? test.rule}</Badge>
                <span className="text-text-tertiary">{test.reason}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-caption text-text-tertiary">{copy.noTests}</p>
        )}
      </section>
    </div>
  );
}
