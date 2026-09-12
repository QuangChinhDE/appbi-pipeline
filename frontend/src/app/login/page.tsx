'use client';

import * as React from 'react';
import { useRouter } from 'next/navigation';
import { useQueryClient } from '@tanstack/react-query';
import { Activity } from 'lucide-react';

import { authApi } from '@/lib/api';
import { qk } from '@/lib/queryKeys';
import { Button } from '@/components/ui/Button';
import { Input, Label } from '@/components/ui/Input';
import { ErrorRemediationCard, fromApiError } from '@/components/integrations/ErrorRemediationCard';
import { useI18n } from '@/providers/LanguageProvider';

export default function LoginPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const { t } = useI18n();

  // No credential reaches this page, from anywhere.
  //
  // It used to read NEXT_PUBLIC_DEMO_EMAIL / _PASSWORD and, when they were
  // set, prefill both boxes and print the pair underneath -- along with a line
  // naming three more accounts that shared the password. `.env.example` shipped
  // those variables filled in, so every install that followed the documented
  // path served a working platform-admin credential to anyone who could reach
  // the sign-in page.
  //
  // The guard was real and worked; the default defeated it. So the mechanism
  // is gone rather than re-defaulted: the seeded password is printed once by
  // ./run.sh, in the terminal of the person installing it, and lives in their
  // .env. A web page is the wrong place for it under any flag.
  const [email, setEmail] = React.useState('');
  const [password, setPassword] = React.useState('');
  const [submitting, setSubmitting] = React.useState(false);
  const [error, setError] = React.useState<unknown>(null);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const user = await authApi.login(email, password);
      queryClient.setQueryData(qk.me(), user);
      // A bootstrapped or invited account may not enter the product yet; the
      // API refuses every route until the temporary password is replaced.
      router.replace(user.password_change_required ? '/change-password' : '/overview');
    } catch (caught) {
      setError(caught);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-surface-0 px-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex flex-col items-center text-center">
          <span className="mb-3 flex h-11 w-11 items-center justify-center rounded-xl bg-brand text-text-inverse">
            <Activity className="h-5 w-5" />
          </span>
          <h1 className="text-h2 font-emphasis text-text-primary">{t('login.title')}</h1>
          <p className="mt-1 text-caption text-text-tertiary">{t('login.subtitle')}</p>
        </div>

        <form
          onSubmit={submit}
          className="space-y-4 rounded-xl border border-[rgb(var(--border-line))] bg-surface-1 p-5 shadow-linear"
        >
          {error != null && <ErrorRemediationCard error={fromApiError(error)} compact />}

          <div>
            <Label htmlFor="email" required>{t('login.email')}</Label>
            <Input
              id="email"
              type="email"
              autoComplete="username"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              required
            />
          </div>

          <div>
            <Label htmlFor="password" required>{t('login.password')}</Label>
            <Input
              id="password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
            />
          </div>

          <Button type="submit" variant="primary" fullWidth loading={submitting}>
            {t('login.submit')}
          </Button>
        </form>

      </div>
    </div>
  );
}
