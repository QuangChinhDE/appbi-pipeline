'use client';

/**
 * Where the old addresses went.
 *
 * Every screen inside a workspace used to live at the top level — `/pipelines`,
 * `/runs/abc` — because there was only ever one workspace in play and it lived
 * in a cookie. They are `/workspaces/{id}/…` now, so the whole product's link
 * history, every bookmark and every URL anybody pasted into a chat points at
 * nothing.
 *
 * One catch-all rather than twenty-six redirect files: the rule is the same
 * for all of them, and twenty-six copies of one rule is twenty-six chances to
 * write it differently. The workspace comes from the session — which is what
 * the old address meant anyway.
 *
 * Only the known sections are forwarded. Anything else is genuinely not a page,
 * and sending it into a workspace would turn one 404 into a redirect loop.
 */

import * as React from 'react';
import { useParams, useRouter } from 'next/navigation';

import { useCurrentUser } from '@/hooks/use-current-user';
import { useI18n } from '@/providers/LanguageProvider';
import { ErrorState } from '@/components/ui/Feedback';

const SECTIONS = new Set([
  'overview', 'pipelines', 'sources', 'destinations', 'runs', 'alerts',
  'monitoring', 'connectors', 'audit', 'settings', 'transforms', 'builder',
]);

export default function LegacyPathRedirect() {
  const { t } = useI18n();
  const params = useParams<{ legacy?: string[] }>();
  const router = useRouter();
  const { data: user, isLoading } = useCurrentUser();

  const segments = React.useMemo(
    () => (Array.isArray(params?.legacy) ? params.legacy : []),
    [params?.legacy],
  );
  const known = segments.length > 0 && SECTIONS.has(segments[0]);
  const workspaceId = user?.workspace?.id ?? user?.workspaces?.[0]?.id;

  React.useEffect(() => {
    if (isLoading) return;
    if (!user) { router.replace('/login'); return; }
    if (!known) return;
    // No workspace at all is its own answer: the chooser says so plainly.
    if (!workspaceId) { router.replace('/workspaces'); return; }
    const rest = segments.join('/');
    const query = typeof window !== 'undefined' ? window.location.search : '';
    router.replace(`/workspaces/${workspaceId}/${rest}${query}`);
  }, [isLoading, user, known, workspaceId, segments, router]);

  if (!isLoading && !known) {
    return (
      <div className="flex min-h-screen items-center justify-center px-4">
        <ErrorState title={t('common.notFound')} message={t('common.notFoundBody')} />
      </div>
    );
  }
  return null;
}
