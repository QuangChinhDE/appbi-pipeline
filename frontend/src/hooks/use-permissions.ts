'use client';

import { useCallback } from 'react';

import { useCurrentUser } from './use-current-user';
import type { PermissionMap } from '@/lib/types';

export type Module =
  | 'sources' | 'destinations' | 'pipelines' | 'transforms' | 'monitoring'
  | 'alerts' | 'audit' | 'members' | 'settings' | 'connectors';

export type Action = 'view' | 'create' | 'edit' | 'operate' | 'delete' | 'admin';

export function hasPermission(
  permissions: PermissionMap | undefined,
  module: Module,
  action: Action,
): boolean {
  return Boolean(permissions?.[module]?.includes(action));
}

/**
 * FE gating is UX only — the backend re-checks every call (section 4.2 rule).
 * Hiding a button the user cannot use just keeps the screen honest.
 */
export function usePermissions() {
  const { data } = useCurrentUser();
  const permissions = data?.permissions;

  const can = useCallback(
    (module: Module, action: Action) => hasPermission(permissions, module, action),
    [permissions],
  );

  return { permissions, can, isPlatformAdmin: Boolean(data?.is_platform_admin) };
}
