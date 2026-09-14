'use client';

/**
 * The organisation moved out of Settings.
 *
 * Organisation work used to happen on a tab inside a workspace, so an
 * administrator asking about all their workspaces was always standing in one
 * particular workspace while asking. It has its own console now.
 *
 * This route stays as a redirect rather than a 404: it was linked from the
 * settings nav for months, and somebody's bookmark should land where the thing
 * went instead of nowhere.
 */

import * as React from 'react';
import { useRouter } from 'next/navigation';

export default function OrganizationSettingsMoved() {
  const router = useRouter();
  React.useEffect(() => { router.replace('/admin/organization'); }, [router]);
  return null;
}
