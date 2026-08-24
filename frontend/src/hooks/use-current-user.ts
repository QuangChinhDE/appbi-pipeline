'use client';

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useRouter } from 'next/navigation';
import { useCallback } from 'react';

import { ApiError, authApi } from '@/lib/api';
import { qk } from '@/lib/queryKeys';
import type { CurrentUser } from '@/lib/types';

export function useCurrentUser() {
  return useQuery<CurrentUser>({
    queryKey: qk.me(),
    queryFn: authApi.me,
    retry: (count, error) => !(error instanceof ApiError && error.status === 401) && count < 1,
    staleTime: 60_000,
  });
}

export function useWorkspaceId(): string {
  const { data } = useCurrentUser();
  return data?.workspace?.id ?? 'anonymous';
}

/**
 * Switching workspace must drop every cached tenant-scoped query, otherwise the
 * previous tenant's rows flash on screen (section 10.2 MUST).
 */
export function useWorkspaceSwitch() {
  const queryClient = useQueryClient();
  const router = useRouter();

  return useCallback(
    async (workspaceId: string) => {
      const next = await authApi.switchWorkspace(workspaceId);
      queryClient.removeQueries({ queryKey: ['workspace'] });
      queryClient.setQueryData(qk.me(), next);
      router.refresh();
      return next;
    },
    [queryClient, router],
  );
}
