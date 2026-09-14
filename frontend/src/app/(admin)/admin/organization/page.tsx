'use client';

/**
 * What the organisation itself is, and what its roles mean.
 *
 * Everything that acts on *people* moved to the People grid and everything
 * that acts on *workspaces* moved to the Workspaces page, so what is left here
 * is the organisation's own identity and the reference table explaining the
 * three roles — the two things somebody comes to an "organisation" page to
 * read rather than to change.
 */

import * as React from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Building2, ShieldCheck } from 'lucide-react';

import { organizationApi } from '@/lib/api';
import { useCurrentUser } from '@/hooks/use-current-user';
import { usePermissions } from '@/hooks/use-permissions';
import { toastError, toastSuccess } from '@/hooks/use-toast';
import { useI18n } from '@/providers/LanguageProvider';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Input, Label } from '@/components/ui/Input';
import { ErrorState } from '@/components/ui/Feedback';
import { Card } from '@/components/layout/PageLayout';

const ORG_ROLE_IDS = ['ORG_OWNER', 'ORG_ADMIN', 'ORG_MEMBER'];

export default function AdminOrganizationPage() {
  const { t, tf } = useI18n();
  const queryClient = useQueryClient();
  const { canOrg } = usePermissions();
  const { data: me } = useCurrentUser();

  const organization = useQuery({
    queryKey: ['organization'],
    queryFn: organizationApi.get,
  });

  const [name, setName] = React.useState('');
  React.useEffect(() => {
    if (organization.data) setName(organization.data.name);
  }, [organization.data]);

  const rename = useMutation({
    mutationFn: () => organizationApi.rename(name.trim()),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['organization'] });
      queryClient.invalidateQueries({ queryKey: ['me'] });
      toastSuccess(t('org.renamed'));
    },
    onError: (caught) => toastError(caught),
  });

  if (organization.error) {
    return (
      <ErrorState
        title={t('common.errorTitle')}
        message={(organization.error as Error).message}
        onRetry={() => organization.refetch()}
      />
    );
  }

  const dirty = name.trim() !== (organization.data?.name ?? '') && name.trim().length > 0;

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-h3 font-strong text-text-primary">{t('admin.organizationTitle')}</h1>
        <p className="mt-1 max-w-2xl text-caption text-text-tertiary">
          {t('admin.organizationSubtitle')}
        </p>
      </header>

      <Card title={t('org.identity')}>
        <div className="grid gap-4 md:grid-cols-2 md:items-start md:gap-x-8">
          <div>
            <Label htmlFor="org-name" required>{t('org.name')}</Label>
            <Input
              id="org-name"
              value={name}
              disabled={!canOrg('edit')}
              onChange={(event) => setName(event.target.value)}
            />
            {canOrg('edit') && (
              <div className="mt-2 flex items-center gap-2">
                <Button size="sm" variant="primary" disabled={!dirty}
                        loading={rename.isPending} onClick={() => rename.mutate()}>
                  {t('common.save')}
                </Button>
                {dirty && (
                  <Button size="sm" variant="ghost"
                          onClick={() => setName(organization.data?.name ?? '')}>
                    {t('common.cancel')}
                  </Button>
                )}
              </div>
            )}
          </div>

          <dl className="grid gap-x-6 gap-y-2 text-caption">
            <div className="flex items-center justify-between gap-3 border-b border-[rgb(var(--border-line))] py-1.5">
              <dt className="text-text-tertiary">{t('org.slug')}</dt>
              <dd className="font-mono text-tiny text-text-primary">
                {organization.data?.slug ?? '—'}
              </dd>
            </div>
            <div className="flex items-center justify-between gap-3 border-b border-[rgb(var(--border-line))] py-1.5">
              <dt className="text-text-tertiary">{t('org.yourRole')}</dt>
              <dd>
                {me?.organization?.role ? (
                  <Badge variant="subtle" size="sm">
                    {tf([`orgRole.${me.organization.role}`], me.organization.role)}
                  </Badge>
                ) : '—'}
              </dd>
            </div>
            <div className="flex items-center justify-between gap-3 py-1.5">
              <dt className="text-text-tertiary">{t('org.status')}</dt>
              <dd className="text-text-primary">{organization.data?.status ?? '—'}</dd>
            </div>
          </dl>
        </div>
      </Card>

      <Card title={t('org.roleDescriptions')}>
        {/* Three short rows. Down one column on a wide monitor they put the
            role and its meaning a screen apart. */}
        <ul className="grid gap-x-8 gap-y-1 lg:grid-cols-3">
          {ORG_ROLE_IDS.map((id) => (
            <li key={id} className="flex items-start gap-2 py-1.5">
              <ShieldCheck className="mt-0.5 h-3.5 w-3.5 flex-shrink-0 text-text-quaternary" />
              <span>
                <span className="block text-caption font-emphasis text-text-primary">
                  {tf([`orgRole.${id}`], id)}
                </span>
                <span className="text-tiny leading-relaxed text-text-tertiary">
                  {tf([`orgRole.${id}.hint`], '')}
                </span>
              </span>
            </li>
          ))}
        </ul>
      </Card>

      <p className="flex items-start gap-2 text-tiny leading-relaxed text-text-quaternary">
        <Building2 className="mt-0.5 h-3.5 w-3.5 flex-shrink-0" />
        <span className="max-w-3xl">{t('admin.organizationFootnote')}</span>
      </p>
    </div>
  );
}
