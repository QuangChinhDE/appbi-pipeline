'use client';

import * as React from 'react';
import { QueryCache, QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { toast } from 'sonner';

import { ApiError } from '@/lib/api';

export function QueryProvider({ children }: { children: React.ReactNode }) {
  const [client] = React.useState(
    () =>
      new QueryClient({
        queryCache: new QueryCache({
          onError: (error) => {
            // 401 is handled by the shell (redirect to login); 403/404 belong to
            // the screen that asked. Only surface unexpected failures globally.
            if (error instanceof ApiError && [401, 403, 404].includes(error.status)) return;
            if (error instanceof ApiError && error.status >= 500) {
              toast.error(error.message, { description: `trace: ${error.traceId}` });
            }
          },
        }),
        defaultOptions: {
          queries: {
            staleTime: 15_000,
            gcTime: 5 * 60_000,
            refetchOnWindowFocus: false,
            retry: (failureCount, error) => {
              if (error instanceof ApiError && error.status < 500) return false;
              return failureCount < 2;
            },
          },
          mutations: { retry: false },
        },
      }),
  );

  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
