'use client';

/**
 * A file input whose words are the product's own.
 *
 * `<input type="file">` renders "Choose Files" and "No file chosen" from the
 * browser, in the browser's language, and nothing in the page can change them.
 * On a Vietnamese page that is two English strings sitting in the middle of a
 * form, and no amount of translating the catalog reaches them.
 *
 * So the input is hidden behind a label -- which is what makes the whole area
 * clickable and keeps it operable from the keyboard -- and the text beside it
 * is ours.
 */

import * as React from 'react';
import { Upload } from 'lucide-react';

import { cn } from '@/lib/utils';
import { useI18n } from '@/providers/LanguageProvider';

export function FilePicker({
  accept, multiple, onChoose, chosen, className, id,
}: {
  accept?: string;
  multiple?: boolean;
  onChoose: (files: FileList | null) => void;
  /** What to say about the current selection, when there is one. */
  chosen?: string | null;
  className?: string;
  id?: string;
}) {
  const { t } = useI18n();
  return (
    <label
      className={cn(
        'flex cursor-pointer flex-col items-center gap-1.5 rounded-lg border',
        'border-dashed border-[rgb(var(--border-line))] px-4 py-6 text-center',
        'transition-colors hover:bg-surface-2',
        'focus-within:ring-1 focus-within:ring-brand/40',
        className,
      )}
    >
      <Upload className="h-4 w-4 text-text-quaternary" aria-hidden />
      <span className="text-caption text-text-secondary">
        {multiple ? t('common.chooseFiles') : t('common.chooseFile')}
      </span>
      <span className="text-tiny text-text-tertiary">
        {chosen || t('common.noFileChosen')}
      </span>
      <input
        id={id}
        type="file"
        accept={accept}
        multiple={multiple}
        className="sr-only"
        onChange={(event) => onChoose(event.target.files)}
      />
    </label>
  );
}
