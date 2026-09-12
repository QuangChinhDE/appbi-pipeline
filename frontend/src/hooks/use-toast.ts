'use client';

import { toast } from 'sonner';

import { ApiError } from '@/lib/api';
import { translate, translateError } from '@/lib/i18n';

/**
 * Toasts fire outside React, so they read the persisted locale directly rather
 * than the provider.
 *
 * The fallback matches the provider's: English until somebody chooses
 * otherwise. It used to be Vietnamese, so on an install where nobody had
 * touched the setting the page was English and its toasts were not.
 */
function locale(): 'vi' | 'en' {
  if (typeof window === 'undefined') return 'en';
  const stored = window.localStorage.getItem('appbi.integration.locale');
  return stored === 'vi' ? 'vi' : 'en';
}

export function toastError(error: unknown, fallback?: string) {
  if (error instanceof ApiError) {
    toast.error(translateError(locale(), error.code, error.message, error.details), {
      description: error.traceId ? `trace: ${error.traceId}` : undefined,
    });
    return;
  }
  toast.error(
    error instanceof Error
      ? error.message
      : (fallback ?? translate(locale(), 'common.actionFailed')),
  );
}

export function toastSuccess(message: string, description?: string) {
  toast.success(message, { description });
}

export { toast };
