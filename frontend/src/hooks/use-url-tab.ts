'use client';

import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import * as React from 'react';

type QueryValue = string | null | undefined;

/**
 * Keeps tab selection and related view state in the URL.
 *
 * A tab is a navigable view, so refresh, back/forward and copied links should
 * restore it. Query updates preserve unrelated parameters unless explicitly
 * removed with `null`.
 */
export function useUrlTab<T extends string>(tabs: readonly T[], fallback: T) {
  const pathname = usePathname();
  const router = useRouter();
  const searchParams = useSearchParams();
  const query = searchParams.toString();
  const requested = searchParams.get('tab');
  const tab = tabs.includes(requested as T) ? requested as T : fallback;

  const hrefForQuery = React.useCallback((updates: Record<string, QueryValue>) => {
    const next = new URLSearchParams(query);
    for (const [key, value] of Object.entries(updates)) {
      if (value === null || value === undefined || value === '') next.delete(key);
      else next.set(key, value);
    }
    const suffix = next.toString();
    return suffix ? `${pathname}?${suffix}` : pathname;
  }, [pathname, query]);

  const setQuery = React.useCallback((
    updates: Record<string, QueryValue>,
    options: { replace?: boolean } = {},
  ) => {
    const href = hrefForQuery(updates);
    if (options.replace) router.replace(href, { scroll: false });
    else router.push(href, { scroll: false });
  }, [hrefForQuery, router]);

  const setTab = React.useCallback((next: T) => setQuery({ tab: next }), [setQuery]);
  const hrefForTab = React.useCallback((next: T) => hrefForQuery({ tab: next }), [hrefForQuery]);
  const queryValue = React.useCallback(
    (key: string) => new URLSearchParams(query).get(key),
    [query],
  );

  React.useEffect(() => {
    if (requested !== tab) setQuery({ tab }, { replace: true });
  }, [requested, setQuery, tab]);

  return { tab, setTab, hrefForTab, hrefForQuery, queryValue, setQuery };
}
