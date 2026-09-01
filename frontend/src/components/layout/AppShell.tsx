'use client';

import * as React from 'react';
import { useRouter } from 'next/navigation';
import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, Menu } from 'lucide-react';

import { Sidebar } from './Sidebar';
import { ApiError, opsApi } from '@/lib/api';
import { qk } from '@/lib/queryKeys';
import { cn } from '@/lib/utils';
import { useCurrentUser, useWorkspaceId } from '@/hooks/use-current-user';
import { useI18n } from '@/providers/LanguageProvider';
import { Spinner } from '@/components/ui/Feedback';

const COLLAPSE_KEY = 'appbi.integration.sidebar-collapsed';

/**
 * Engine degradation is a banner, not a blocked app: the product must stay
 * readable from its own database when the engine is down (UAT-012).
 */
function EngineHealthBanner() {
  const { t } = useI18n();
  const workspaceId = useWorkspaceId();
  const { data } = useQuery({
    queryKey: qk.engine(workspaceId),
    queryFn: opsApi.engineStatus,
    refetchInterval: 60_000,
    retry: false,
  });

  if (!data || data.operational) return null;
  return (
    <div className="flex items-start gap-2 border-b border-warning/30 bg-warning/10 px-4 py-2 sm:px-6">
      <AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0 text-warning" aria-hidden />
      <p className="text-caption text-text-secondary">
        <span className="font-emphasis text-text-primary">{t('engine.degraded')}.</span>{' '}
        {t('engine.degradedHelp')}
      </p>
    </div>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const { t } = useI18n();
  const router = useRouter();
  const { data: user, isLoading, error } = useCurrentUser();
  const [collapsed, setCollapsed] = React.useState(false);
  const [mobileOpen, setMobileOpen] = React.useState(false);

  React.useEffect(() => {
    setCollapsed(window.localStorage.getItem(COLLAPSE_KEY) === '1');
  }, []);

  React.useEffect(() => {
    if (!(error instanceof ApiError)) return;
    if (error.status === 401) {
      router.replace('/login');
      return;
    }
    // The account authenticated but may not use the product yet. Without this
    // the shell fell through to `if (!user) return null` and painted nothing:
    // a signed-in user staring at a white page, which is what a fresh
    // production deployment did to its own bootstrap admin.
    if (error.status === 403 && error.code === 'PASSWORD_CHANGE_REQUIRED') {
      router.replace('/change-password');
    }
  }, [error, router]);

  // Belt and braces: the session can also be read successfully and simply say
  // the password must change -- on a reload, `/auth/me` answers rather than
  // failing, so there is no error to react to.
  React.useEffect(() => {
    if (user?.password_change_required) router.replace('/change-password');
  }, [user, router]);

  const toggle = () => {
    setCollapsed((previous) => {
      const next = !previous;
      window.localStorage.setItem(COLLAPSE_KEY, next ? '1' : '0');
      return next;
    });
  };

  if (isLoading) {
    return (
      <div className="flex h-screen items-center justify-center">
        <Spinner label={t('common.loadingSession')} />
      </div>
    );
  }
  if (!user) return null;

  return (
    <div className="min-h-screen bg-surface-0">
      <Sidebar
        collapsed={collapsed}
        onToggle={toggle}
        mobileOpen={mobileOpen}
        onCloseMobile={() => setMobileOpen(false)}
      />
      {/* A viewport-tall flex column: whatever bars sit above `main` -- the
          mobile header, a health banner -- simply reduce its share, instead of
          adding their height on top of a `min-h-screen` main and pushing the
          page past the fold. */}
      <div className={cn('flex h-screen flex-col transition-[padding] duration-300',
        collapsed ? 'lg:pl-14' : 'lg:pl-60')}>
        <div className="flex h-12 items-center gap-2 border-b border-[rgb(var(--border-line))] bg-surface-1 px-4 lg:hidden">
          <button
            type="button"
            onClick={() => setMobileOpen(true)}
            aria-label={t('common.openMenu')}
            className="rounded-md p-1.5 text-text-secondary hover:bg-surface-2"
          >
            <Menu className="h-5 w-5" />
          </button>
          <span className="text-caption font-strong text-text-primary">AppBI Integration</span>
        </div>
        <EngineHealthBanner />
        {/* A flex column, not a block: pages that fill the viewport (the
            Transform workbench) size their panes against it. */}
        <main className="flex min-h-0 flex-1 flex-col overflow-y-auto">{children}</main>
      </div>
    </div>
  );
}
