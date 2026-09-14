'use client';

import { useCallback } from 'react';

import { useCurrentUser } from './use-current-user';
import type { PermissionMap } from '@/lib/types';

export type Module =
  | 'sources' | 'destinations' | 'pipelines' | 'transforms' | 'monitoring'
  | 'alerts' | 'audit' | 'members' | 'settings' | 'connectors';

/** What an organisation role may do. `create` makes workspaces, `admin`
 * manages organisation members, `delete` removes a workspace or the org. */
export type OrgAction = 'view' | 'create' | 'edit' | 'delete' | 'admin';

export type Action =
  | 'view' | 'create' | 'edit' | 'operate' | 'delete'
  // Split out of the four above because they are the decisions people get
  // wrong: reading rows, re-reading history, and replacing a credential.
  | 'view_data' | 'reset' | 'manage_credentials';

/** The three that are worth calling out on their own. */
export const SENSITIVE_ACTIONS: Action[] = ['view_data', 'reset', 'manage_credentials'];

/**
 * One plain-language level per module, plus any sensitive powers.
 *
 * The level is no longer worked out here. This file used to keep its own
 * ladder -- with rungs called `manage` and `full` that the backend has never
 * had, matched against an `admin` action no module can be granted -- so the
 * same permission was described one way on this screen and another by the API
 * that enforces it. The API sends the level now; this only decides what is
 * worth flagging beside it.
 */
export function summarisePermissions(
  actions: string[] = [],
  level: string = 'none',
): { level: string; flags: Action[] } {
  const held = new Set(actions);
  // "Full access" already says the sensitive powers are included, so repeating
  // them on every row of an owner's list is thirty chips carrying no
  // information. They matter exactly where they are not implied.
  const flags = level === 'full' ? [] : SENSITIVE_ACTIONS.filter((a) => held.has(a));
  return { level, flags };
}

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

  // Authority over the organisation is a separate list from the module map,
  // because it answers a separate question: not "what may I do in here" but
  // "which workspaces exist and who may open them".
  const orgPermissions = data?.organization_permissions;
  const canOrg = useCallback(
    (action: OrgAction) => Boolean(orgPermissions?.includes(action)),
    [orgPermissions],
  );

  return {
    permissions,
    levels: data?.levels,
    can,
    canOrg,
    organization: data?.organization ?? null,
    orgRole: data?.organization?.role ?? null,
    isPlatformAdmin: Boolean(data?.is_platform_admin),
  };
}
