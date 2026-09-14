'use client';

/**
 * The workspace, taken from the address bar.
 *
 * It used to live in a claim inside the session cookie, which made it hidden
 * state with three consequences nobody could work around: two browser tabs
 * shared one workspace, a link to a pipeline opened whichever workspace you
 * were last in, and the back button could not undo a switch because nothing in
 * the address had changed.
 *
 * Now the segment is the answer. This layout publishes it to the API client
 * before any child renders, and tells the server to remember it so that a
 * bare `/` next time lands somewhere sensible. The cookie stops being the
 * truth and becomes a memory of where to go when there is no address.
 */

import * as React from 'react';
import { useParams, useRouter } from 'next/navigation';
import { useQueryClient } from '@tanstack/react-query';

import { authApi, setActiveWorkspace } from '@/lib/api';
import { qk } from '@/lib/queryKeys';
import { useCurrentUser } from '@/hooks/use-current-user';
import { useI18n } from '@/providers/LanguageProvider';
import { ErrorState } from '@/components/ui/Feedback';

export default function WorkspaceLayout({ children }: { children: React.ReactNode }) {
  const { t } = useI18n();
  const params = useParams<{ wsId: string }>();
  const router = useRouter();
  const queryClient = useQueryClient();
  const workspaceId = params.wsId;

  // Synchronously, before children render: a query fired by a child on this
  // same pass must carry the right workspace, and an effect runs too late for
  // that -- the first request would go to whichever workspace was last set.
  if (typeof window !== 'undefined') setActiveWorkspace(workspaceId);

  const { data: user, isLoading } = useCurrentUser();
  const reachable = user?.workspaces?.some((w) => w.id === workspaceId);

  React.useEffect(() => {
    setActiveWorkspace(workspaceId);
    // Nothing is purged here. Query keys already carry the workspace
    // (`['workspace', id, ...]`), so two workspaces never share a cache entry
    // -- and the blanket removal this used to do wiped the entry the page had
    // just started fetching, which left every list spinning forever. It made
    // sense when the workspace lived in a cookie and one key meant different
    // data at different times; it does not now.
  }, [workspaceId]);

  // Tell the server where we are, so a later visit to `/` opens here. Fire and
  // forget: the header above is what actually scopes every request, and a
  // failure to record the preference must not block the page.
  React.useEffect(() => {
    if (!reachable) return;
    if (user?.workspace?.id === workspaceId) return;
    authApi.switchWorkspace(workspaceId)
      .then((next) => queryClient.setQueryData(qk.me(), next))
      .catch(() => {});
  }, [reachable, user?.workspace?.id, workspaceId, queryClient]);

  React.useEffect(() => {
    // A workspace this account cannot open is not an error page, it is a wrong
    // turn: send them to the list of the ones they can.
    if (!isLoading && user && !reachable) router.replace('/workspaces');
  }, [isLoading, user, reachable, router]);

  if (!isLoading && user && !reachable) {
    return (
      <div className="flex flex-1 items-center justify-center px-4">
        <ErrorState
          title={t('workspace.notReachable')}
          message={t('workspace.notReachableBody')}
        />
      </div>
    );
  }

  return <>{children}</>;
}
