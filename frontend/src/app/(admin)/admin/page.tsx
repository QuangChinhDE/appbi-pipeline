'use client';

/**
 * The console's front door moved to `/workspaces`.
 *
 * Choosing where to work is not an administrative act, and putting the list of
 * workspaces behind `/admin` said it was. The redirect stays because the path
 * was live and linked.
 */

import * as React from 'react';
import { useRouter } from 'next/navigation';

export default function AdminIndexMoved() {
  const router = useRouter();
  React.useEffect(() => { router.replace('/workspaces'); }, [router]);
  return null;
}
