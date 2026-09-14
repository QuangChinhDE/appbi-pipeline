'use client';

/**
 * The organisation console: a different place, not another tab.
 *
 * Organisation work used to happen on a settings tab *inside* a workspace, so
 * an administrator answering "which of my workspaces is failing" was always
 * standing in one particular workspace while asking about all of them. The
 * chrome said one thing and the question meant another.
 *
 * So this shell drops the workspace sidebar entirely. Nothing here is scoped
 * to a workspace, there is no switcher, and the only way back into the product
 * is an explicit door -- which is the same separation Databricks draws between
 * its account console and a workspace, for the same reason.
 */

import * as React from 'react';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { ArrowLeft, Building2, Home, LayoutGrid, Users } from 'lucide-react';

import { cn } from '@/lib/utils';
import { useCurrentUser } from '@/hooks/use-current-user';
import { usePermissions } from '@/hooks/use-permissions';
import { useI18n } from '@/providers/LanguageProvider';
import { ErrorState } from '@/components/ui/Feedback';

const TABS = [
  { href: '/admin', labelKey: 'admin.overview', icon: Home, exact: true },
  { href: '/admin/workspaces', labelKey: 'admin.workspaces', icon: LayoutGrid },
  { href: '/admin/people', labelKey: 'admin.people', icon: Users },
  { href: '/admin/organization', labelKey: 'admin.organization', icon: Building2 },
];

export function AdminShell({ children }: { children: React.ReactNode }) {
  const { t } = useI18n();
  const pathname = usePathname();
  const router = useRouter();
  const { data: user, isLoading } = useCurrentUser();
  const { canOrg, isPlatformAdmin } = usePermissions();

  const allowed = isPlatformAdmin || canOrg('admin');

  React.useEffect(() => {
    // Sent back rather than shown a locked door. Somebody who lands here from a
    // stale link is not being denied a thing they asked for -- they followed a
    // link that stopped applying to them.
    if (!isLoading && user && !allowed) router.replace('/overview');
  }, [isLoading, user, allowed, router]);

  if (!isLoading && user && !allowed) {
    return (
      <div className="flex min-h-screen items-center justify-center px-4">
        <ErrorState title={t('admin.notAllowed')} message={t('admin.notAllowedBody')} />
      </div>
    );
  }

  return (
    <div className="flex min-h-screen flex-col bg-surface-0">
      <header className="border-b border-[rgb(var(--border-line))] bg-surface-1">
        <div className="flex w-full flex-wrap items-center gap-x-4 gap-y-2 px-4 pb-0 pt-3 sm:px-6 xl:px-8 2xl:px-10">
          <div className="flex min-w-0 items-center gap-2.5">
            <span className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-lg bg-brand text-text-inverse">
              <Building2 className="h-3.5 w-3.5" />
            </span>
            <div className="min-w-0">
              <p className="truncate text-small font-strong text-text-primary">
                {user?.organization?.name ?? t('admin.title')}
              </p>
              <p className="truncate text-tiny text-text-quaternary">{t('admin.title')}</p>
            </div>
          </div>

          <Link
            href="/overview"
            className="ml-auto inline-flex items-center gap-1.5 rounded-md border border-[rgb(var(--border-line))] px-2.5 py-1.5 text-caption text-text-secondary transition-colors hover:bg-surface-2 hover:text-text-primary"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            {t('admin.backToProduct', { name: user?.workspace?.name ?? '' })}
          </Link>

          <nav
            aria-label={t('admin.title')}
            className="-mb-px flex w-full items-center gap-1 overflow-x-auto"
          >
            {TABS.map((tab) => {
              const active = tab.exact
                ? pathname === tab.href
                : pathname.startsWith(tab.href);
              const Icon = tab.icon;
              return (
                <Link
                  key={tab.href}
                  href={tab.href}
                  aria-current={active ? 'page' : undefined}
                  className={cn(
                    'flex flex-shrink-0 items-center gap-1.5 border-b-2 px-3 py-2 text-caption font-emphasis transition-colors',
                    active
                      ? 'border-brand text-text-primary'
                      : 'border-transparent text-text-tertiary hover:text-text-primary',
                  )}
                >
                  <Icon className="h-3.5 w-3.5" />
                  {t(tab.labelKey)}
                </Link>
              );
            })}
          </nav>
        </div>
      </header>

      <main className="flex w-full flex-1 flex-col px-4 py-5 sm:px-6 xl:px-8 2xl:px-10">
        {children}
      </main>
    </div>
  );
}
