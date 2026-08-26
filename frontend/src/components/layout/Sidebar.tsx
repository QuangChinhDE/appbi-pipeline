'use client';

import * as React from 'react';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Hammer,
  Activity, Bell, Boxes, ChevronLeft, ChevronRight, ChevronsUpDown, Check, Database,
  GitBranch, Globe, Home, LogOut, PlayCircle, Radar, ScrollText, Settings, Warehouse, X,
} from 'lucide-react';

import { authApi, opsApi, pipelineApi } from '@/lib/api';
import { qk } from '@/lib/queryKeys';
import { LOCALE_NAMES } from '@/lib/i18n';
import { cn } from '@/lib/utils';
import { useCurrentUser, useWorkspaceId, useWorkspaceSwitch } from '@/hooks/use-current-user';
import { hasPermission, type Action, type Module } from '@/hooks/use-permissions';
import { useI18n } from '@/providers/LanguageProvider';

interface NavItem {
  labelKey: string;
  href: string;
  icon: React.ReactNode;
  module?: Module;
  action?: Action;
  /** Hidden until this workspace has a pipeline. See `NavGroup.advanced`. */
  advancedItem?: boolean;
}

interface NavGroup {
  labelKey?: string;
  items: NavItem[];
  /**
   * Folded away until the workspace has its first pipeline.
   *
   * On a fresh deployment every module is equally prominent, so the two things
   * a new user actually needs -- build something, then watch it run -- sit
   * beside the connector catalogue, the Builder, the audit log and alert
   * rules. None of those mean anything before a pipeline exists.
   */
  advanced?: boolean;
}

// Grouped by user intent, mirroring AppBI's sidebar (section 6.1).
const NAV_GROUPS: NavGroup[] = [
  { items: [{ labelKey: 'sidebar.overview', href: '/overview', icon: <Home className="h-4 w-4" />, module: 'monitoring' }] },
  {
    labelKey: 'sidebar.group.build',
    items: [
      { labelKey: 'sidebar.sources', href: '/sources', icon: <Database className="h-4 w-4" />, module: 'sources' },
      { labelKey: 'sidebar.destinations', href: '/destinations', icon: <Warehouse className="h-4 w-4" />, module: 'destinations' },
      { labelKey: 'sidebar.pipelines', href: '/pipelines', icon: <GitBranch className="h-4 w-4" />, module: 'pipelines' },
    ],
  },
  {
    labelKey: 'sidebar.group.operate',
    items: [
      { labelKey: 'sidebar.runs', href: '/runs', icon: <PlayCircle className="h-4 w-4" />, module: 'monitoring' },
      // Monitoring dashboards and alert rules are for a system that is already
      // running; before the first pipeline they are empty screens.
      { labelKey: 'sidebar.monitoring', href: '/monitoring', icon: <Radar className="h-4 w-4" />, module: 'monitoring', advancedItem: true },
      { labelKey: 'sidebar.alerts', href: '/alerts', icon: <Bell className="h-4 w-4" />, module: 'alerts', advancedItem: true },
    ],
  },
  {
    labelKey: 'sidebar.group.manage',
    advanced: true,
    items: [
      { labelKey: 'sidebar.connectors', href: '/connectors', icon: <Boxes className="h-4 w-4" />, module: 'connectors' },
      { labelKey: 'sidebar.builder', href: '/builder', icon: <Hammer className="h-4 w-4" />, module: 'connectors' },
      { labelKey: 'sidebar.audit', href: '/audit', icon: <ScrollText className="h-4 w-4" />, module: 'audit' },
    ],
  },
];

function initials(name: string): string {
  return name.split(' ').map((word) => word[0]).join('').toUpperCase().slice(0, 2);
}

export function Sidebar({
  collapsed, onToggle, mobileOpen, onCloseMobile,
}: {
  collapsed: boolean;
  onToggle: () => void;
  mobileOpen: boolean;
  onCloseMobile: () => void;
}) {
  const pathname = usePathname();
  const router = useRouter();
  const queryClient = useQueryClient();
  const { t, locale, setLocale } = useI18n();
  const { data: user } = useCurrentUser();
  const workspaceId = useWorkspaceId();
  const switchWorkspace = useWorkspaceSwitch();

  const [menuOpen, setMenuOpen] = React.useState(false);
  const [workspaceMenuOpen, setWorkspaceMenuOpen] = React.useState(false);
  const menuRef = React.useRef<HTMLDivElement>(null);

  const { data: unread } = useQuery({
    queryKey: qk.unread(workspaceId),
    queryFn: opsApi.unreadCount,
    enabled: Boolean(user),
    refetchInterval: 30_000,
  });
  const unreadCount = unread?.count ?? 0;

  React.useEffect(() => {
    if (!menuOpen) return;
    const onPointerDown = (event: MouseEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) setMenuOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setMenuOpen(false);
    };
    document.addEventListener('mousedown', onPointerDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onPointerDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [menuOpen]);

  const permissions = user?.permissions;
  // First run: no pipeline has ever been created here. Until one has, the
  // sidebar shows the path through the product rather than every module at
  // once. `showAdvanced` is a deliberate escape hatch, not a hidden feature --
  // and once a pipeline exists the whole navigation returns permanently.
  const pipelineCount = useQuery({
    queryKey: qk.pipelines(workspaceId, { probe: 'first-run' }),
    queryFn: () => pipelineApi.list({ limit: 1 }),
    staleTime: 60_000,
  });
  const firstRun = pipelineCount.data?.items?.length === 0;
  const [showAdvanced, setShowAdvanced] = React.useState(false);
  const folded = firstRun && !showAdvanced;

  const visibleGroups = NAV_GROUPS
    .filter((group) => !(folded && group.advanced))
    .map((group) => ({
      ...group,
      items: group.items.filter(
        (item) =>
          (!item.module || hasPermission(permissions, item.module, item.action ?? 'view'))
          && !(folded && item.advancedItem),
      ),
    }))
    .filter((group) => group.items.length > 0);

  const isActive = (href: string) => pathname === href || pathname.startsWith(`${href}/`);

  const logout = async () => {
    try {
      await authApi.logout();
    } finally {
      queryClient.clear();
      router.push('/login');
    }
  };

  const width = collapsed ? 'w-14' : 'w-60';

  return (
    <>
      {mobileOpen && (
        <div
          className="fixed inset-0 z-40 bg-overlay/40 lg:hidden"
          onClick={onCloseMobile}
          aria-hidden
        />
      )}
      <aside
        className={cn(
          'fixed left-0 top-0 z-40 flex h-screen flex-col',
          'border-r border-[rgb(var(--border-line))] bg-surface-1',
          'transition-[width,transform] duration-300',
          width,
          mobileOpen ? 'translate-x-0' : '-translate-x-full lg:translate-x-0',
        )}
      >
        {/* Brand */}
        <div className="flex h-14 items-center px-3">
          {!collapsed ? (
            <div className="flex w-full items-center justify-between">
              <Link
                href="/overview"
                className="flex min-w-0 items-center gap-2 rounded-md px-1.5 py-1 transition-colors hover:bg-surface-2"
              >
                <span className="flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-md bg-brand text-text-inverse">
                  <Activity className="h-3.5 w-3.5" />
                </span>
                <span className="truncate text-small font-strong tracking-[-0.011em] text-text-primary">
                  AppBI
                </span>
              </Link>
              <button
                type="button"
                onClick={onCloseMobile}
                aria-label={t('common.close')}
                className="rounded-md p-1 text-text-tertiary hover:bg-surface-2 lg:hidden"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          ) : (
            <Link
              href="/overview"
              className="mx-auto flex h-8 w-8 items-center justify-center rounded-md bg-brand text-text-inverse"
              aria-label="AppBI"
            >
              <Activity className="h-4 w-4" />
            </Link>
          )}
        </div>

        {/* Workspace switcher */}
        {user && user.workspaces.length > 0 && !collapsed && (
          <div className="relative px-2 pb-2">
            <button
              type="button"
              onClick={() => setWorkspaceMenuOpen((v) => !v)}
              className="flex w-full items-center gap-2 rounded-md border border-[rgb(var(--border-line))] px-2 py-1.5 text-left transition-colors hover:bg-surface-2"
            >
              <div className="min-w-0 flex-1">
                <p className="truncate text-caption font-emphasis text-text-primary">
                  {user.workspace?.name ?? '—'}
                </p>
                <p className="truncate text-tiny text-text-quaternary">{user.role}</p>
              </div>
              <ChevronsUpDown className="h-3.5 w-3.5 flex-shrink-0 text-text-quaternary" />
            </button>
            {workspaceMenuOpen && (
              <div className="absolute left-2 right-2 z-50 mt-1 overflow-hidden rounded-lg border border-[rgb(var(--border-strong))] bg-surface-1 shadow-popover">
                {user.workspaces.map((workspace) => (
                  <button
                    key={workspace.id}
                    type="button"
                    onClick={async () => {
                      setWorkspaceMenuOpen(false);
                      if (workspace.id !== user.workspace?.id) await switchWorkspace(workspace.id);
                    }}
                    className="flex w-full items-center gap-2 px-3 py-2 text-left text-caption text-text-secondary hover:bg-surface-2"
                  >
                    <span className="min-w-0 flex-1 truncate">{workspace.name}</span>
                    {workspace.id === user.workspace?.id && (
                      <Check className="h-3.5 w-3.5 text-brand" />
                    )}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Nav */}
        <nav className={cn('flex-1 overflow-y-auto px-2 py-1', collapsed ? 'space-y-1.5' : 'space-y-3')}>
          {visibleGroups.map((group, groupIndex) => (
            <div key={group.labelKey ?? `group-${groupIndex}`}>
              {group.labelKey &&
                (collapsed ? (
                  groupIndex > 0 && (
                    <div className="mx-auto mb-1.5 h-px w-6 bg-[rgb(var(--border-line))]" aria-hidden />
                  )
                ) : (
                  <p className="px-2.5 pb-1 pt-0.5 text-[10px] font-emphasis uppercase tracking-[0.14em] text-text-quaternary">
                    {t(group.labelKey)}
                  </p>
                ))}
              <ul className="space-y-0.5">
                {group.items.map((item) => {
                  const active = isActive(item.href);
                  const label = t(item.labelKey);
                  const badge = item.href === '/alerts' && unreadCount > 0 ? unreadCount : null;
                  return (
                    <li key={item.href}>
                      <Link
                        href={item.href}
                        onClick={onCloseMobile}
                        aria-current={active ? 'page' : undefined}
                        className={cn(
                          'group flex items-center rounded-md transition-colors',
                          collapsed ? 'mx-auto h-8 w-8 justify-center' : 'h-8 gap-2 px-2.5',
                          active
                            ? 'bg-surface-2 text-text-primary'
                            : 'text-text-tertiary hover:bg-surface-2 hover:text-text-primary',
                        )}
                        title={collapsed ? label : undefined}
                      >
                        <span
                          className={cn(
                            'relative flex-shrink-0',
                            active ? 'text-brand' : 'text-text-tertiary group-hover:text-text-secondary',
                          )}
                        >
                          {item.icon}
                          {badge && collapsed && (
                            <span className="absolute -right-1 -top-1 h-1.5 w-1.5 rounded-full bg-danger" />
                          )}
                        </span>
                        {!collapsed && (
                          <>
                            <span className="flex-1 truncate text-caption font-emphasis">{label}</span>
                            {badge && (
                              <span className="rounded-full bg-danger px-1.5 text-[10px] font-strong text-white">
                                {badge > 99 ? '99+' : badge}
                              </span>
                            )}
                          </>
                        )}
                      </Link>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}

          {/* Folded, not removed. The rest of the product is one click away and
              says so, which is the difference between a simplified first run
              and a feature somebody has to go looking for. */}
          {folded && !collapsed && (
            <button
              type="button"
              onClick={() => setShowAdvanced(true)}
              className="mt-1 w-full rounded-md px-3 py-1.5 text-left text-tiny text-text-quaternary hover:bg-surface-2 hover:text-text-secondary"
            >
              {t('sidebar.showAdvanced')}
            </button>
          )}
        </nav>

        {/* User + collapse */}
        <div className="border-t border-[rgb(var(--border-line))]">
          {user && (
            <div ref={menuRef} className="relative px-2 pt-2">
              <button
                type="button"
                // A disclosure that announces neither its state nor that it opens
                // a menu is invisible to assistive tech.
                aria-haspopup="menu"
                aria-expanded={menuOpen}
                aria-label={t('sidebar.account')}
                onClick={() => setMenuOpen((v) => !v)}
                className={cn(
                  'w-full rounded-md transition-colors hover:bg-surface-2',
                  collapsed ? 'flex justify-center py-2' : 'flex items-center gap-2 px-2 py-1.5',
                )}
                title={collapsed ? user.full_name : undefined}
              >
                <span className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-full bg-brand text-tiny font-strong text-text-inverse">
                  {initials(user.full_name || user.email)}
                </span>
                {!collapsed && (
                  <span className="min-w-0 flex-1 text-left">
                    <span className="block truncate text-caption font-emphasis text-text-primary">
                      {user.full_name}
                    </span>
                    <span className="block truncate text-tiny text-text-quaternary">{user.email}</span>
                  </span>
                )}
              </button>

              {menuOpen && (
                <div
                  className={cn(
                    'absolute bottom-full z-50 mb-1 overflow-hidden rounded-lg',
                    'border border-[rgb(var(--border-strong))] bg-surface-1 shadow-popover',
                    collapsed ? 'left-full ml-2 w-56' : 'left-2 right-2',
                  )}
                >
                  <div className="border-b border-[rgb(var(--border-line))] px-3 py-2">
                    <p className="truncate text-caption font-emphasis text-text-primary">
                      {user.full_name}
                    </p>
                    <p className="truncate text-tiny text-text-tertiary">{user.email}</p>
                  </div>

                  {hasPermission(permissions, 'settings', 'view') && (
                    <>
                      <MenuLink href="/settings/workspace" onClick={() => setMenuOpen(false)}
                        icon={<Settings className="h-3.5 w-3.5" />} label={t('settings.workspace')} />
                      <MenuLink href="/settings/access" onClick={() => setMenuOpen(false)}
                        icon={<Settings className="h-3.5 w-3.5" />} label={t('settings.access')} />
                      <MenuLink href="/settings/engine" onClick={() => setMenuOpen(false)}
                        icon={<Settings className="h-3.5 w-3.5" />} label={t('settings.engine')} />
                    </>
                  )}

                  <div className="flex items-center gap-2 border-t border-[rgb(var(--border-line))] px-3 py-2">
                    <Globe className="h-3.5 w-3.5 text-text-tertiary" />
                    <span className="flex-1 text-caption text-text-secondary">
                      {t('sidebar.language')}
                    </span>
                    <div className="inline-flex overflow-hidden rounded-md border border-[rgb(var(--border-line))]">
                      {(['vi', 'en'] as const).map((code) => (
                        <button
                          key={code}
                          type="button"
                          // The active locale is signalled by background alone; without
                          // aria-pressed a screen reader cannot tell which one is on.
                          aria-pressed={locale === code}
                          aria-label={LOCALE_NAMES[code]}
                          onClick={() => setLocale(code)}
                          className={cn(
                            'px-2.5 py-1 text-tiny font-emphasis uppercase',
                            locale === code
                              ? 'bg-surface-3 text-text-primary'
                              : 'text-text-tertiary hover:text-text-primary',
                          )}
                        >
                          {code}
                        </button>
                      ))}
                    </div>
                  </div>

                  <button
                    type="button"
                    onClick={logout}
                    className="flex w-full items-center gap-2 border-t border-[rgb(var(--border-line))] px-3 py-2 text-caption text-danger hover:bg-surface-2"
                  >
                    <LogOut className="h-3.5 w-3.5" />
                    {t('sidebar.logout')}
                  </button>
                </div>
              )}
            </div>
          )}

          <button
            type="button"
            onClick={onToggle}
            aria-label={collapsed ? t('common.expand') : t('common.collapse')}
            className={cn(
              'hidden w-full items-center gap-2 px-3 py-2 text-text-quaternary transition-colors hover:text-text-secondary lg:flex',
              collapsed && 'justify-center px-0',
            )}
          >
            {collapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronLeft className="h-4 w-4" />}
            {!collapsed && <span className="text-tiny">{t('common.collapse')}</span>}
          </button>
        </div>
      </aside>
    </>
  );
}

function MenuLink({
  href, label, icon, onClick,
}: {
  href: string;
  label: string;
  icon: React.ReactNode;
  onClick: () => void;
}) {
  return (
    <Link
      href={href}
      onClick={onClick}
      className="flex items-center gap-2 px-3 py-2 text-caption text-text-secondary hover:bg-surface-2"
    >
      {icon}
      {label}
    </Link>
  );
}
