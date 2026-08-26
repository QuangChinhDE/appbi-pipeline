'use client';

/**
 * The one screen an account with a temporary password is allowed to reach.
 *
 * It lives outside `AppShell` deliberately. The shell's first act is to read
 * `/auth/me` and render the product chrome, and every product route answers
 * `403 PASSWORD_CHANGE_REQUIRED` until this form is completed -- so putting
 * this inside the shell would put the only way out of the state behind the
 * state itself. That was the bug: a fresh production deployment signed in as
 * its bootstrap admin and got a white screen with no way forward.
 */

import * as React from 'react';
import { useRouter } from 'next/navigation';
import { useQueryClient } from '@tanstack/react-query';
import { KeyRound } from 'lucide-react';

import { authApi } from '@/lib/api';
import { qk } from '@/lib/queryKeys';
import { Button } from '@/components/ui/Button';
import { Input, Label } from '@/components/ui/Input';
import { ErrorRemediationCard, fromApiError } from '@/components/integrations/ErrorRemediationCard';
import { useI18n } from '@/providers/LanguageProvider';

export default function ChangePasswordPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const { t } = useI18n();

  const [currentPassword, setCurrentPassword] = React.useState('');
  const [newPassword, setNewPassword] = React.useState('');
  const [confirmPassword, setConfirmPassword] = React.useState('');
  const [submitting, setSubmitting] = React.useState(false);
  const [error, setError] = React.useState<unknown>(null);
  const [mismatch, setMismatch] = React.useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    if (newPassword !== confirmPassword) {
      setMismatch(true);
      return;
    }
    setMismatch(false);
    setSubmitting(true);
    try {
      const user = await authApi.changePassword(currentPassword, newPassword);
      // The response carries a fresh cookie; every previously issued token for
      // this account is now invalid. Replace the cached session rather than
      // invalidating it, so the next screen does not flash a loading state.
      queryClient.setQueryData(qk.me(), user);
      router.replace('/overview');
    } catch (caught) {
      setError(caught);
    } finally {
      setSubmitting(false);
    }
  };

  const signOut = async () => {
    try {
      await authApi.logout();
    } finally {
      queryClient.clear();
      router.replace('/login');
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-surface-0 px-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex flex-col items-center text-center">
          <span className="mb-3 flex h-11 w-11 items-center justify-center rounded-xl bg-brand text-text-inverse">
            <KeyRound className="h-5 w-5" />
          </span>
          <h1 className="text-h2 font-emphasis text-text-primary">{t('password.title')}</h1>
          <p className="mt-1 text-caption text-text-tertiary">{t('password.subtitle')}</p>
        </div>

        <form
          onSubmit={submit}
          className="space-y-4 rounded-xl border border-[rgb(var(--border-line))] bg-surface-1 p-5 shadow-linear"
        >
          {error != null && <ErrorRemediationCard error={fromApiError(error)} compact />}

          <div>
            <Label htmlFor="current-password" required>{t('password.current')}</Label>
            <Input
              id="current-password"
              type="password"
              autoComplete="current-password"
              value={currentPassword}
              onChange={(event) => setCurrentPassword(event.target.value)}
              required
            />
          </div>

          <div>
            <Label htmlFor="new-password" required>{t('password.new')}</Label>
            <Input
              id="new-password"
              type="password"
              autoComplete="new-password"
              value={newPassword}
              onChange={(event) => setNewPassword(event.target.value)}
              required
            />
            <p className="mt-1 text-tiny text-text-quaternary">{t('password.rules')}</p>
          </div>

          <div>
            <Label htmlFor="confirm-password" required>{t('password.confirm')}</Label>
            <Input
              id="confirm-password"
              type="password"
              autoComplete="new-password"
              value={confirmPassword}
              onChange={(event) => {
                setConfirmPassword(event.target.value);
                setMismatch(false);
              }}
              required
              aria-invalid={mismatch}
            />
            {mismatch && (
              <p className="mt-1 text-tiny text-danger">{t('password.mismatch')}</p>
            )}
          </div>

          <Button type="submit" variant="primary" fullWidth loading={submitting}>
            {t('password.submit')}
          </Button>

          <p className="text-tiny text-text-quaternary">{t('password.revokeNote')}</p>
        </form>

        <button
          type="button"
          onClick={signOut}
          className="mt-4 w-full text-center text-caption text-text-tertiary underline-offset-2 hover:underline"
        >
          {t('password.signOut')}
        </button>
      </div>
    </div>
  );
}
