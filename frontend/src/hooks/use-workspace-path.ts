'use client';

/**
 * A path inside the workspace you are currently in.
 *
 * Every link inside the product used to be absolute — `/pipelines`, `/runs` —
 * because there was only ever one workspace in play, held in a cookie. Now the
 * workspace is a route segment, so a link has to say which one it means.
 *
 * `ws('/pipelines')` rather than a string template at 51 call sites: the
 * prefix is one decision, and one decision belongs in one place.
 */

import { useCallback } from 'react';
import { useParams } from 'next/navigation';

import { useCurrentUser } from './use-current-user';

export function useWorkspaceId(): string {
  const params = useParams<{ wsId?: string }>();
  const { data } = useCurrentUser();
  // Outside a workspace route — the console, say — fall back to wherever the
  // session last was, so a link rendered there still goes somewhere real.
  return params?.wsId ?? data?.workspace?.id ?? '';
}

export function useWorkspacePath(): (path: string) => string {
  const workspaceId = useWorkspaceId();
  return useCallback(
    (path: string) => {
      if (!workspaceId) return path;
      return `/workspaces/${workspaceId}${path.startsWith('/') ? path : `/${path}`}`;
    },
    [workspaceId],
  );
}
