'use client';

import Link from 'next/link';

import { usePermissions } from '@/hooks/use-permissions';
import { useI18n } from '@/providers/LanguageProvider';
import { cn } from '@/lib/utils';

interface SettingsTab {
  id: 'workspace' | 'access' | 'engine';
  href: string;
  labelKey: string;
  adminOnly?: boolean;
}

const TABS: SettingsTab[] = [
  { id: 'workspace', href: '/settings/workspace', labelKey: 'settings.workspace' },
  { id: 'access', href: '/settings/access', labelKey: 'settings.access' },
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
      {TABS.filter((tab) => !tab.adminOnly || isPlatformAdmin).map((tab) => (
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
