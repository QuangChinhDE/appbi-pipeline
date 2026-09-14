'use client';

import * as React from 'react';
import Script from 'next/script';
import { useRouter } from 'next/navigation';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Activity } from 'lucide-react';

import { authApi } from '@/lib/api';
import { qk } from '@/lib/queryKeys';
import { Button } from '@/components/ui/Button';
import { Input, Label } from '@/components/ui/Input';
import { ErrorRemediationCard, fromApiError } from '@/components/integrations/ErrorRemediationCard';
import { useI18n } from '@/providers/LanguageProvider';
import type { CurrentUser } from '@/lib/types';

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
  // What this deployment actually offers. Asked rather than compiled in, so
  // turning Google on is an environment change rather than a rebuild -- and so
  // a page that cannot offer Google says so instead of rendering a button that
  // fails after somebody has already chosen an account.
  const config = useQuery({
    queryKey: ['auth-config'],
    queryFn: authApi.config,
    staleTime: Infinity,
    retry: false,
  });
  const googleReady = Boolean(config.data?.google && config.data.google_client_id);
  const passwordReady = config.data?.password !== false;

  const googleSlot = React.useRef<HTMLDivElement | null>(null);
  const [scriptLoaded, setScriptLoaded] = React.useState(false);
  const [googleBusy, setGoogleBusy] = React.useState(false);

  const [email, setEmail] = React.useState('');
  const [password, setPassword] = React.useState('');
  const [submitting, setSubmitting] = React.useState(false);
  const [error, setError] = React.useState<unknown>(null);

  const finish = React.useCallback((user: CurrentUser) => {
    queryClient.setQueryData(qk.me(), user);
    // Signing in lands on the list of workspaces, not inside one. Which
    // workspace to open is the reader's first decision, and `/overview` made
    // it for them -- picking whichever one the session last remembered.
    //
    // A bootstrapped or invited account may not enter the product yet; the API
    // refuses every route until the temporary password is replaced.
    router.replace(user.password_change_required ? '/change-password' : '/workspaces');
  }, [queryClient, router]);

  const signInWithGoogle = React.useCallback(async (credential: string) => {
    setGoogleBusy(true);
    setError(null);
    try {
      finish(await authApi.google(credential));
    } catch (caught) {
      setError(caught);
    } finally {
      setGoogleBusy(false);
    }
  }, [finish]);

  // Google renders its own button, which is a condition of using the sign-in:
  // its branding rules do not allow reimplementing it. It is drawn into a slot
  // here once both the script and the client id are in hand.
  React.useEffect(() => {
    if (!googleReady || !scriptLoaded || !googleSlot.current) return;
    const accounts = (window as unknown as {
      google?: { accounts?: { id?: {
        initialize: (o: Record<string, unknown>) => void;
        renderButton: (el: HTMLElement, o: Record<string, unknown>) => void;
      } } };
    }).google?.accounts?.id;
    if (!accounts) return;
    googleSlot.current.innerHTML = '';
    accounts.initialize({
      client_id: config.data?.google_client_id,
      callback: (response: { credential?: string }) => {
        if (response?.credential) void signInWithGoogle(response.credential);
      },
    });
    accounts.renderButton(googleSlot.current, {
      theme: 'outline', size: 'large', width: 320,
      text: 'signin_with', shape: 'rectangular',
    });
  }, [googleReady, scriptLoaded, config.data?.google_client_id, signInWithGoogle]);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      finish(await authApi.login(email, password));
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

        <div className="space-y-4 rounded-xl border border-[rgb(var(--border-line))] bg-surface-1 p-5 shadow-linear">
          {error != null && <ErrorRemediationCard error={fromApiError(error)} compact />}

          {googleReady && (
            <div className="space-y-2">
              {/* Google's own button, drawn by Google's script into this slot.
                  Their branding terms do not allow reimplementing it, and a
                  hand-rolled lookalike is also how people learn not to trust
                  sign-in buttons. */}
              <div ref={googleSlot} className="flex min-h-[42px] justify-center" />
              {googleBusy && (
                <p className="text-center text-caption text-text-tertiary">
                  {t('login.googleWorking')}
                </p>
              )}
              {config.data?.google_domains?.length ? (
                <p className="text-center text-tiny text-text-quaternary">
                  {t('login.googleDomains', { domains: config.data.google_domains.join(', ') })}
                </p>
              ) : null}
            </div>
          )}

          {googleReady && passwordReady && (
            <div className="flex items-center gap-3">
              <span className="h-px flex-1 bg-[rgb(var(--border-line))]" />
              <span className="text-tiny uppercase tracking-[0.08em] text-text-quaternary">
                {t('login.or')}
              </span>
              <span className="h-px flex-1 bg-[rgb(var(--border-line))]" />
            </div>
          )}

          {!passwordReady && !googleReady && (
            <p className="text-center text-caption text-text-tertiary">
              {t('login.nothingConfigured')}
            </p>
          )}

          {passwordReady && (
          <form onSubmit={submit} className="space-y-4">
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
          )}
        </div>

        {googleReady && (
          <Script
            src="https://accounts.google.com/gsi/client"
            strategy="afterInteractive"
            onLoad={() => setScriptLoaded(true)}
          />
        )}
      </div>
    </div>
  );
}
