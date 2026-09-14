'use client';

/**
 * Home: the place everybody lands, above any one workspace.
 *
 * It began as an organisation console gated on administering the organisation,
 * which made it a room most people were bounced out of. But the question it
 * answers -- "what can I reach, and what needs me" -- is everybody's, and a
 * person with one workspace should see one rather than be redirected past the
 * page that would have told them so.
 *
 * So the shell is open and the *tabs* are gated, on what the API says this
 * reader may do rather than on a role name. Databricks and Airbyte both scope
 * one console this way instead of building a screen per kind of administrator:
 * an organisation admin sees every workspace, a workspace owner sees theirs,
 * and an analyst sees a list with no management in it at all.
 */

import * as React from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useQuery } from '@tanstack/react-query';
import { Building2, Home, Users } from 'lucide-react';

import { organizationApi } from '@/lib/api';
import { cn } from '@/lib/utils';
import { useCurrentUser } from '@/hooks/use-current-user';
import { useI18n } from '@/providers/LanguageProvider';

//: `Workspaces` is gone: the home page already lists them with their health,
//: and a second tab listing the same rows with fewer facts was a menu item
//: that cost a click to learn less.
interface ConsoleTab {
  href: string;
  labelKey: string;
  icon: typeof Home;
  exact?: boolean;
  /** What the reader must administer for this tab to exist. Absent means
   *  everybody -- home is not a permission. */
  needs?: 'members' | 'org';
}

const TABS: ConsoleTab[] = [
  { href: '/workspaces', labelKey: 'admin.workspaces', icon: Home, exact: true },
  { href: '/admin/people', labelKey: 'admin.people', icon: Users, needs: 'members' },
  { href: '/admin/organization', labelKey: 'admin.organization', icon: Building2, needs: 'org' },
];

export function AdminShell({ children }: { children: React.ReactNode }) {
  const { t } = useI18n();
  const pathname = usePathname();
  const { data: user } = useCurrentUser();

  // What this reader may do, answered by the API rather than guessed from a
  // role name. The same call the home page makes, so the tabs and the page
  // below them cannot disagree about who is looking.
  const overview = useQuery({
    queryKey: ['org-overview'],
    queryFn: organizationApi.overview,
  });
  const may = {
    members: Boolean(overview.data?.administers_members),
    org: Boolean(overview.data?.administers_organization),
  };
  const tabs = TABS.filter((tab) => !tab.needs || may[tab.needs]);

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

          {/* No "back to a workspace" button. This *is* home now, and the
              workspaces are listed on it -- a button naming whichever one the
              session last remembered asked the reader to go where the cookie
              wanted rather than where they were going. */}

          <nav
            aria-label={t('admin.title')}
            className={cn(
              '-mb-px ml-auto w-full items-center gap-1 overflow-x-auto',
              // A row of one is not a choice, so it is not drawn as one.
              tabs.length > 1 ? 'flex' : 'hidden',
            )}
          >
            {tabs.map((tab) => {
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
