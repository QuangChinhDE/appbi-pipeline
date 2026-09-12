'use client';

/**
 * Importing SQL into a project that already exists.
 *
 * The same two steps as the wizard's fourth option -- choose files, review what
 * they become -- with one difference that matters: this project has work in it.
 * So the dialog says plainly that nothing is replaced, and the review is the
 * only place a name collision can be spotted before it happens.
 */

import * as React from 'react';
import { useMutation } from '@tanstack/react-query';
import { FileCode2, Upload } from 'lucide-react';

import { Button } from '@/components/ui/Button';
import { Modal } from '@/components/ui/Modal';
import { SqlImportReview } from '@/components/transforms/SqlImportReview';
import { transformApi } from '@/lib/api';
import { toastError, toastSuccess } from '@/hooks/use-toast';
import type {
  SqlImportAnalysis, SqlImportCandidate, SqlImportFile,
} from '@/lib/types';
import { useI18n } from '@/providers/LanguageProvider';

export function SqlImportDialog({
  open, onClose, projectId, onImported,
}: {
  open: boolean;
  onClose: () => void;
  projectId: string;
  onImported: (invocationId: string | null) => void;
}) {
  const { t } = useI18n();
  const [files, setFiles] = React.useState<SqlImportFile[]>([]);
  const [analysis, setAnalysis] = React.useState<SqlImportAnalysis | null>(null);

  React.useEffect(() => {
    if (!open) { setFiles([]); setAnalysis(null); }
  }, [open]);

  const analyse = useMutation({
    mutationFn: async () =>
      transformApi.analyseSqlImport({ files, project_id: projectId }),
    onSuccess: (result) => setAnalysis(result),
    onError: (caught) => toastError(caught),
  });

  const apply = useMutation({
    mutationFn: async () => transformApi.applySqlImport({
      files,
      decisions: (analysis?.candidates ?? []).map(decisionOf),
      project_id: projectId,
      verify: true,
    }),
    onSuccess: (result) => {
      toastSuccess(
        t('tfsql.imported', { n: result.written_paths.length }),
        result.renamed.length
          ? t('tfsql.renamed', { n: result.renamed.length })
          : undefined,
      );
      onImported(result.invocation_id);
      onClose();
    },
    onError: (caught) => toastError(caught),
  });

  const choose = async (list: FileList | null) => {
    setAnalysis(null);
    setFiles(await readAll(list));
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={t('tfsql.importTitle')}
      description={t('tfsql.importInto')}
      size="lg"
    >
      <div className="space-y-4">
        {analysis === null ? (
          <label className="flex cursor-pointer flex-col items-center gap-2 rounded-lg border border-dashed border-[rgb(var(--border-line))] px-4 py-8 text-center hover:bg-surface-2">
            <Upload className="h-5 w-5 text-text-quaternary" />
            <span className="text-caption text-text-secondary">{t('tfsql.choose')}</span>
            {files.length > 0 && (
              <span className="text-tiny text-text-tertiary">
                {t('tfnew.sqlChosen', { n: files.length })}
              </span>
            )}
            <input
              type="file" accept=".sql,text/plain" multiple className="hidden"
              onChange={(event) => choose(event.target.files)}
            />
          </label>
        ) : (
          <SqlImportReview
            analysis={analysis}
            onChange={(candidates) => setAnalysis({ ...analysis, candidates })}
          />
        )}

        <div className="flex justify-end gap-2 border-t border-[rgb(var(--border-line))] pt-4">
          {analysis !== null && (
            <Button variant="ghost" onClick={() => setAnalysis(null)}>
              {t('tfsql.back')}
            </Button>
          )}
          {analysis === null ? (
            <Button
              variant="primary"
              disabled={files.length === 0}
              loading={analyse.isPending}
              leadingIcon={<FileCode2 className="h-3.5 w-3.5" />}
              onClick={() => analyse.mutate()}
            >
              {analyse.isPending ? t('tfnew.analysing') : t('tfnew.analyse')}
            </Button>
          ) : (
            <Button
              variant="primary"
              disabled={analysis.candidates.length === 0 || analysis.cycle.length > 0}
              loading={apply.isPending}
              onClick={() => apply.mutate()}
            >
              {apply.isPending ? t('tfsql.applying') : t('tfsql.apply')}
            </Button>
          )}
        </div>
      </div>
    </Modal>
  );
}

/** Only the preferences travel back. The graph is the server's to work out. */
export function decisionOf(candidate: SqlImportCandidate) {
  return {
    key: candidate.key,
    name: candidate.name,
    layer: candidate.layer,
    materialized: candidate.materialized,
    description: candidate.description,
    tests: candidate.tests,
  };
}

/** Read the chosen files as text, skipping anything that is not SQL. */
export async function readAll(list: FileList | null): Promise<SqlImportFile[]> {
  const chosen = Array.from(list ?? []).filter(
    (file) => file.name.toLowerCase().endsWith('.sql'),
  );
  return Promise.all(chosen.map(async (file) => ({
    name: file.name,
    content: await file.text(),
  })));
}
