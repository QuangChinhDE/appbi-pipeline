'use client';

/**
 * Everybody starts at home.
 *
 * The first question anybody has is the same one — what can I reach — and no
 * workspace can answer it from inside itself. Somebody with one workspace sees
 * one and walks in; somebody with eight sees which of them needs them today.
 * Forking on the count would have meant two different products depending on
 * how your account happened to be set up.
 */

import * as React from 'react';
import { useRouter } from 'next/navigation';

import { useCurrentUser } from '@/hooks/use-current-user';

export default function RootPage() {
  const router = useRouter();
  const { data: user, isLoading } = useCurrentUser();

  React.useEffect(() => {
    if (isLoading) return;
    router.replace(user ? '/workspaces' : '/login');
  }, [isLoading, user, router]);

  return null;
}
