'use client';

import { toast } from 'sonner';

import { ApiError } from '@/lib/api';
import { translate } from '@/lib/i18n';

/**
 * Toasts fire outside React, so they read the persisted locale directly rather
 * than the provider.
 */
function locale(): 'vi' | 'en' {
  if (typeof window === 'undefined') return 'vi';
  const stored = window.localStorage.getItem('appbi.integration.locale');
  return stored === 'en' ? 'en' : 'vi';
}

export function toastError(error: unknown, fallback?: string) {
  if (error instanceof ApiError) {
    toast.error(error.message, {
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
