'use client';

import Link from 'next/link';

import { usePermissions } from '@/hooks/use-permissions';
import { useI18n } from '@/providers/LanguageProvider';
import { cn } from '@/lib/utils';

interface SettingsTab {
  id: 'workspace' | 'engine';
  href: string;
  labelKey: string;
  adminOnly?: boolean;
}

// What is left is what genuinely belongs to *this* workspace. Members and the
// organisation both moved to the console, because both are questions about
// more than one workspace and neither could be answered from inside one.
const TABS: SettingsTab[] = [
  { id: 'workspace', href: '/settings/workspace', labelKey: 'settings.workspace' },
  { id: 'engine', href: '/settings/engine', labelKey: 'settings.engine', adminOnly: true },
];

export function SettingsTabs({ active }: { active: SettingsTab['id'] }) {
  const { t } = useI18n();
  const { isPlatformAdmin } = usePermissions();

  return (
    <nav
      aria-label={t('settings.nav')}
      className="mb-4 flex items-center gap-1 border-b border-[rgb(var(--border-line))]"
    >
      {TABS
        .filter((tab) => !tab.adminOnly || isPlatformAdmin)
        .map((tab) => (
        <Link
          key={tab.id}
          href={tab.href}
          aria-current={tab.id === active ? 'page' : undefined}
          className={cn(
            '-mb-px border-b-2 px-3 py-2 text-caption font-emphasis transition-colors',
            tab.id === active
              ? 'border-brand text-text-primary'
              : 'border-transparent text-text-tertiary hover:text-text-primary',
          )}
        >
          {t(tab.labelKey)}
        </Link>
      ))}
    </nav>
  );
}
