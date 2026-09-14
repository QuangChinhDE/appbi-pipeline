'use client';

/**
 * Where a person lands, which depends on what their day is.
 *
 * Somebody who administers several workspaces opens the product to ask "which
 * of them needs me today" — a question no workspace can answer from inside
 * itself. Dropping them into one particular workspace makes them navigate out
 * of it first, every morning.
 *
 * One workspace is the other case entirely: the console would be a page listing
 * the single thing they were already going to open. So the fork is on the
 * number, not on the role alone.
 */

import * as React from 'react';
import { useRouter } from 'next/navigation';

import { useCurrentUser } from '@/hooks/use-current-user';

export default function RootPage() {
  const router = useRouter();
  const { data: user, isLoading } = useCurrentUser();

  React.useEffect(() => {
    if (isLoading) return;
    if (!user) { router.replace('/login'); return; }
    const administersOrg = Boolean(user.organization_permissions?.includes('admin'))
      || user.is_platform_admin;
    const several = (user.workspaces?.length ?? 0) > 1;
    router.replace(administersOrg && several ? '/admin' : '/overview');
  }, [isLoading, user, router]);

  return null;
}
