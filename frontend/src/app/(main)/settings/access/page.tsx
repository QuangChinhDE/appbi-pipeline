'use client';

/**
 * Members moved to the console.
 *
 * Administering people from inside a workspace meant doing it once per
 * workspace — and the thing an administrator actually wants to see, who can
 * reach what, is a question no single workspace can answer. It lives beside
 * the list of workspaces now.
 *
 * This route stays as a redirect rather than a 404: it was in the settings nav
 * for months, and a bookmark should land where the thing went.
 */

import * as React from 'react';
import { useRouter } from 'next/navigation';

export default function AccessSettingsMoved() {
  const router = useRouter();
  React.useEffect(() => { router.replace('/admin/people'); }, [router]);
  return null;
}
