'use client';

import * as React from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, ArrowLeft, CheckCircle2, Lock, PlugZap } from 'lucide-react';

import { ApiError, connectorApi, destinationApi, oauthApi, sourceApi } from '@/lib/api';
import { qk } from '@/lib/queryKeys';
import { useWorkspaceId } from '@/hooks/use-current-user';
import { usePermissions } from '@/hooks/use-permissions';
import { toastSuccess } from '@/hooks/use-toast';
import { useI18n } from '@/providers/LanguageProvider';
import { Button } from '@/components/ui/Button';
import { Input, Label, Textarea } from '@/components/ui/Input';
import { CardSkeleton, EmptyState, Spinner } from '@/components/ui/Feedback';
import { ConnectorPicker } from './ConnectorPicker';
import { ConnectorIcon } from './ConnectorIcon';
import { ErrorRemediationCard, fromApiError, type RemediationInput } from './ErrorRemediationCard';
import { Stepper, WizardFooter } from './Stepper';
import {
  DynamicConnectorForm, applyDefaults, splitSecrets, validateAgainstSpec, type FormValues,
} from './DynamicConnectorForm';
import { ConnectorDocs } from './ConnectorDocs';
import type { ActorTestResult } from '@/lib/types';

type Kind = 'source' | 'destination';

/**
 * Create Source / Create Destination wizard (section 12.2).
 * Step state lives here for the whole wizard, so going Back never loses input.
 */
export function ActorWizard({ kind }: { kind: Kind }) {
  const { t } = useI18n();
  const router = useRouter();
  const searchParams = useSearchParams();

  // Where this wizard sits in the first-run journey.
  //
  //   /sources/new?journey=1              -> on save, go and make a destination
  //   /destinations/new?source=<id>       -> on save, go and make the pipeline
  //
  // Read from the URL rather than held in a provider so Back works, a refresh
  // does not lose the thread, and the plain `/sources/new` entry point behaves
  // exactly as it did.
  const sourceId = searchParams.get('source');
  // Set when the provider has redirected back through the API.
  const [oauthGrant, setOauthGrant] = React.useState<string | null>(
    () => searchParams.get('oauth_grant'));
  const oauthOutcome = searchParams.get('oauth');
  const journey = searchParams.get('journey') === '1' || Boolean(sourceId);
  const next = !journey ? null : kind === 'source' ? 'destination' : 'pipeline';
  const queryClient = useQueryClient();
  const workspaceId = useWorkspaceId();
  const { can } = usePermissions();

  const isSource = kind === 'source';
  const api = isSource ? sourceApi : destinationApi;
  const basePath = isSource ? '/sources' : '/destinations';
  const connectorType = isSource ? 'SOURCE' : 'DESTINATION';

  const [step, setStep] = React.useState(0);
  const [connectorKey, setConnectorKey] = React.useState<string | null>(null);
  const [name, setName] = React.useState('');
  const [description, setDescription] = React.useState('');
  const [values, setValues] = React.useState<FormValues>({});
  const [errors, setErrors] = React.useState<Record<string, string>>({});
  const [testResult, setTestResult] = React.useState<ActorTestResult | null>(null);
  const [checkToken, setCheckToken] = React.useState<string | null>(null);
  const [failure, setFailure] = React.useState<RemediationInput | null>(null);

  // Only what this deployment actually offers.
  //
  // This used to fetch the whole catalogue -- 598 sources, about 515 KB -- to
  // show the five a user can pick, and rendered the rest as locked cards. The
  // first screen of the primary flow was therefore mostly things that cannot
  // be chosen, which is a browser, not a wizard. The full catalogue still
  // exists under Connectors, where beta and blocked states are information
  // somebody wants.
  const connectors = useQuery({
    queryKey: qk.connectors(workspaceId, { type: connectorType, selectable: true }),
    queryFn: () => connectorApi.list({ type: connectorType, selectable: 'true' }),
  });

  // Which connectors this deployment can authorise by sign-in. A provider with
  // no registered application is absent from this list, so the wizard shows the
  // service-account path alone rather than a button that cannot work.
  const oauthProviders = useQuery({
    queryKey: ['workspace', workspaceId, 'oauth-providers'],
    queryFn: () => oauthApi.providers(),
    staleTime: 5 * 60_000,
  });
  const oauthFor = oauthProviders.data?.find((p) => p.connector_key === connectorKey);

  // Whose account was connected, so the wizard can say so rather than showing
  // an anonymous tick.
  const grantDetail = useQuery({
    queryKey: ['workspace', workspaceId, 'oauth-grant', oauthGrant],
    queryFn: () => oauthApi.grant(oauthGrant as string),
    enabled: Boolean(oauthGrant),
  });

  const connector = useQuery({
    queryKey: qk.connector(workspaceId, connectorKey ?? ''),
    queryFn: () => connectorApi.detail(connectorKey as string),
    enabled: Boolean(connectorKey),
  });

  // Coming back from the provider, the page has been reloaded from scratch:
  // pick up the connector the consent was for and move past the picker, or the
  // user lands on step 1 with no sign that anything happened.
  React.useEffect(() => {
    const returning = searchParams.get('connector');
    if (returning && !connectorKey) {
      setConnectorKey(returning);
      setStep(1);
    }
  }, [searchParams, connectorKey]);

  // Seed spec defaults exactly once per connector choice.
  React.useEffect(() => {
    if (connector.data) {
      setValues((current) => applyDefaults(connector.data.spec_schema, current));
    }
  }, [connector.data]);

  const spec = connector.data?.spec_schema;

  const test = useMutation({
    mutationFn: async () => {
      if (!spec || !connectorKey) throw new Error(t('wizard.noConnectorSelected'));
      const { configuration, credentials } = splitSecrets(spec, values);
      return api.testDraft({ connector_key: connectorKey, configuration, credentials });
    },
    onSuccess: (result) => {
      setTestResult(result);
      setCheckToken(result.check_token ?? null);
      setFailure(
        result.succeeded
          ? null
          : {
              code: result.error_code,
              message: result.message ?? t('sources.testFailed'),
              category: result.category,
              technicalMessage: result.technical_message,
              affects: name || undefined,
            },
      );
    },
    onError: (caught) => {
      setTestResult(null);
      setFailure(fromApiError(caught, name || undefined));
    },
  });

  const startOauth = useMutation({
    mutationFn: async () => {
      if (!connectorKey) throw new Error(t('wizard.noConnectorSelected'));
      return oauthApi.start(connectorKey);
    },
    // A full-page navigation, not a popup: the provider's consent screen
    // refuses to render in a frame, and a popup is the first thing a browser
    // blocks.
    onSuccess: (result) => { window.location.href = result.authorize_url; },
    onError: (caught) => setFailure(fromApiError(caught)),
  });

  const save = useMutation({
    mutationFn: async () => {
      if (!spec || !connectorKey) throw new Error(t('wizard.noConnectorSelected'));
      const { configuration, credentials } = splitSecrets(spec, values);
      return api.create({
        name: name.trim(),
        connector_key: connectorKey,
        description: description || null,
        configuration,
        credentials,
        // The check already ran in step 3; the signed token lets the backend
        // trust it instead of starting the connector container again.
        test_before_save: !checkToken,
        check_token: checkToken,
        // An opaque handle to a completed consent. The refresh token behind it
        // never came through this page.
        oauth_grant_id: oauthGrant,
      });
    },
    onSuccess: (created) => {
      queryClient.invalidateQueries({ queryKey: ['workspace', workspaceId] });
      toastSuccess(t(isSource ? 'sources.created' : 'destinations.created'), created.name);
      // Keep going rather than stopping at a detail page with no way onward.
      // Saving a source used to land on its detail screen, which offers test,
      // discover, enable and delete -- and no hint that a destination and a
      // pipeline are still needed before anything moves. `next` carries the
      // journey; without it the behaviour is unchanged.
      if (next === 'destination') {
        router.push(`/destinations/new?source=${created.id}`);
      } else if (next === 'pipeline') {
        router.push(`/pipelines/new?source=${sourceId ?? ''}&destination=${created.id}`);
      } else {
        router.push(`${basePath}/${created.id}`);
      }
    },
    onError: (caught) => setFailure(fromApiError(caught, name || undefined)),
  });

  // A role that cannot create must not be walked into a wizard that will 403
  // on save (the backend blocks it either way; this avoids the dead end).
  const canCreate = can(isSource ? 'sources' : 'destinations', 'create');

  const steps = [
    { id: 'connector', label: t('wizard.step.connector') },
    { id: 'configure', label: t('wizard.step.configure') },
    { id: 'test', label: t('wizard.step.test') },
  ];

  const goToConfigure = () => {
    if (!connectorKey) return;
    setStep(1);
  };

  const goToTest = () => {
    if (!spec) return;
    const found = validateAgainstSpec(spec, values, t);
    if (!name.trim()) found.__name = `${t('common.name')} ${t('common.required')}`;
    setErrors(found);
    if (Object.keys(found).length > 0) {
      // A blocked Continue must never be silent: name the fields and jump to
      // the first one, otherwise a long connector form hides the problem.
      const first = Object.keys(found)[0];
      const target = document.getElementById(first === '__name' ? 'actor-name' : `cfg-${first}`);
      target?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      target?.focus({ preventScroll: true });
      return;
    }
    setStep(2);
    setTestResult(null);
    setCheckToken(null);
    setFailure(null);
    test.mutate();
  };

  return (
    <div className="px-4 py-6 sm:px-6 xl:px-8">
      <Link
        href={basePath}
        className="-ml-1 mb-2 inline-flex items-center gap-1 rounded-md px-1 py-1.5 text-caption text-text-tertiary transition-colors hover:text-text-primary"
      >
        <ArrowLeft className="h-3.5 w-3.5" />
        {isSource ? t('sources.title') : t('destinations.title')}
      </Link>

      <h1 className="text-h3 font-strong text-text-primary">
        {t(isSource ? 'sources.connectTitle' : 'destinations.connectTitle')}
      </h1>
      <p className="mt-1 text-caption text-text-tertiary">
        {t(isSource ? 'sources.connectSubtitle' : 'destinations.connectSubtitle')}
      </p>

      {!canCreate ? (
        <div className="mt-6 max-w-2xl">
          <EmptyState
            icon={Lock}
            title={t('wizard.noPermissionTitle')}
            description={t('wizard.noPermissionBody')}
            action={
              <Link href={basePath}>
                <Button variant="secondary">{t('common.back')}</Button>
              </Link>
            }
          />
        </div>
      ) : (
      <>
      <div className="my-5">
        <Stepper steps={steps} current={step} onStepClick={setStep} />
      </div>

      {/* The picker is a browse surface over the whole catalogue and wants the
          width; a config form is easier to read kept narrow. */}
      <div className={step === 0 ? 'max-w-6xl' : 'max-w-3xl'}>
        {step === 0 && (
          connectors.isLoading ? (
            <CardSkeleton count={6} />
          ) : (
            <ConnectorPicker
              connectors={connectors.data ?? []}
              value={connectorKey}
              onChange={(key) => {
                setConnectorKey(key);
                setValues({});
                setErrors({});
                setTestResult(null);
              }}
            />
          )
        )}

        {step === 1 && (
          connector.isLoading || !spec ? (
            <Spinner label={t('wizard.loadingConnector')} />
          ) : (
            <div className="space-y-5">
              {/* Sign in, or paste a key. Both are offered where the connector
                  supports both, because they suit different situations: a
                  service account belongs to the organisation and survives
                  people leaving, while OAuth lets somebody grant access to
                  their own files without sharing every one of them with a
                  robot address first. */}
              {oauthFor && (
                <div className="rounded-lg border border-[rgb(var(--border-line))] bg-surface-1 p-3.5">
                  {oauthGrant && grantDetail.data ? (
                    <p className="flex items-center gap-2 text-caption text-text-secondary">
                      <CheckCircle2 className="h-4 w-4 text-success" />
                      {t('oauth.connectedAs', {
                        provider: oauthFor.label,
                        account: grantDetail.data.account_label || t('oauth.thisAccount'),
                      })}
                    </p>
                  ) : (
                    <>
                      <p className="text-caption font-emphasis text-text-primary">
                        {t('oauth.title', { provider: oauthFor.label })}
                      </p>
                      <p className="mt-0.5 text-tiny text-text-tertiary">
                        {t('oauth.subtitle')}
                      </p>
                      <Button
                        type="button"
                        variant="secondary"
                        size="sm"
                        className="mt-2.5"
                        loading={startOauth.isPending}
                        onClick={() => startOauth.mutate()}
                      >
                        {t('oauth.connect', { provider: oauthFor.label })}
                      </Button>
                      {oauthOutcome && (
                        <p className="mt-2 text-tiny text-danger">
                          {t(`oauth.outcome.${oauthOutcome}`)}
                        </p>
                      )}
                    </>
                  )}
                </div>
              )}

              {Object.keys(errors).length > 0 && (
                <div role="alert" className="rounded-lg border border-warning/30 bg-warning/5 p-3">
                  <p className="flex items-center gap-1.5 text-caption font-emphasis text-text-primary">
                    <AlertTriangle className="h-3.5 w-3.5 text-warning" />
                    {t('wizard.missingFields', { n: Object.keys(errors).length })}
                  </p>
                  <ul className="mt-1 list-inside list-disc text-caption text-text-secondary">
                    {Object.values(errors).map((message) => (
                      <li key={message}>{message}</li>
                    ))}
                  </ul>
                </div>
              )}

              <div className="flex items-center gap-2.5 rounded-lg border border-[rgb(var(--border-line))] bg-surface-1 p-3">
                <ConnectorIcon icon={connector.data?.icon} connectorKey={connector.data?.connector_key} size="md" />
                <div className="min-w-0">
                  <p className="text-caption font-strong text-text-primary">
                    {connector.data?.display_name}
                  </p>
                  {/* The version, only where it means something.
                      A connector this product defines runs on a generic
                      manifest runner, so `version` is the runner's tag --
                      "v7.28.2" beside "Base HRM" tells a user nothing and
                      invites them to think it is Base's version. For those,
                      say what they actually want to know. */}
                  <p className="text-tiny text-text-quaternary">
                    {connector.data?.stream_names?.length
                      ? t('docs.streamCountShort', {
                          n: String(connector.data.stream_names.length),
                        })
                      : <span className="font-mono">v{connector.data?.version}</span>}
                  </p>
                </div>
              </div>

              <div className="space-y-4 rounded-lg border border-[rgb(var(--border-line))] bg-surface-1 p-4">
                <div>
                  <Label htmlFor="actor-name" required>
                    {t(isSource ? 'sources.nameLabel' : 'destinations.nameLabel')}
                  </Label>
                  <Input
                    id="actor-name"
                    value={name}
                    invalid={Boolean(errors.__name)}
                    placeholder={connector.data?.display_name
                      ? `${connector.data.display_name} ${isSource ? '(nguồn)' : '(đích)'}`
                      : (isSource ? 'Production Postgres' : 'Analytics Warehouse')}
                    onChange={(event) => setName(event.target.value)}
                  />
                  {errors.__name && <p className="mt-1 text-tiny text-danger">{errors.__name}</p>}
                </div>
                <div>
                  <Label htmlFor="actor-desc" hint={t('common.optional')}>
                    {t('common.description')}
                  </Label>
                  <Textarea
                    id="actor-desc"
                    rows={2}
                    value={description}
                    onChange={(event) => setDescription(event.target.value)}
                  />
                </div>
              </div>

              {/* Fields on the left, what they mean on the right — the shape
                  Airbyte uses, and the reason is the same: a connector form is
                  a list of names that only make sense to somebody who already
                  knows the system. Sending them to a documentation site loses
                  the form. Stacks on narrow screens, docs first, because on a
                  phone the explanation is more useful than a head start on
                  typing. */}
              <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_20rem] lg:items-start">
                <div className="order-2 rounded-lg border border-[rgb(var(--border-line))] bg-surface-1 p-4 lg:order-1">
                  <DynamicConnectorForm
                    spec={spec}
                    values={values}
                    errors={errors}
                    onChange={setValues}
                  />
                </div>
                {connector.data && (
                  <div className="order-1 lg:sticky lg:top-4 lg:order-2">
                    <ConnectorDocs connector={connector.data} />
                  </div>
                )}
              </div>
            </div>
          )
        )}

        {step === 2 && (
          <div className="space-y-4">
            {test.isPending && (
              <div className="flex items-center gap-3 rounded-lg border border-[rgb(var(--border-line))] bg-surface-1 p-4">
                <span className="h-4 w-4 animate-spin rounded-full border-2 border-brand border-t-transparent" />
                <div>
                  <p className="text-caption font-emphasis text-text-primary">
                    {t('sources.testing')}
                  </p>
                  <p className="text-tiny text-text-tertiary">
                    {t('wizard.testRunning')}
                  </p>
                </div>
              </div>
            )}

            {testResult?.succeeded && (
              <div className="flex items-start gap-3 rounded-lg border border-success/30 bg-success/5 p-4">
                <CheckCircle2 className="mt-0.5 h-4 w-4 flex-shrink-0 text-success" />
                <div>
                  <p className="text-caption font-strong text-text-primary">
                    {t('sources.testSuccess')}
                  </p>
                  <p className="mt-0.5 text-tiny text-text-tertiary">
                    {t('wizard.testPassed', {
                      seconds: ((testResult.duration_ms ?? 0) / 1000).toFixed(1) })}
                  </p>
                </div>
              </div>
            )}

            {failure && (
              <ErrorRemediationCard
                error={{ ...failure, onAction: () => setStep(1), onRetry: () => test.mutate() }}
              />
            )}

            {!test.isPending && !testResult && !failure && (
              <div className="rounded-lg border border-[rgb(var(--border-line))] bg-surface-1 p-4">
                <p className="text-caption text-text-secondary">
                  {t('wizard.testPrompt')}
                </p>
                <Button
                  className="mt-3"
                  variant="secondary"
                  leadingIcon={<PlugZap className="h-3.5 w-3.5" />}
                  onClick={() => test.mutate()}
                >
                  {t('sources.testConnection')}
                </Button>
              </div>
            )}
          </div>
        )}

        <WizardFooter
          onBack={step > 0 ? () => setStep(step - 1) : undefined}
          backLabel={t('common.back')}
          hint={t('wizard.stepOf', { current: String(step + 1), total: String(steps.length) })}
          onNext={
            step === 0 ? goToConfigure
              : step === 1 ? goToTest
              : () => save.mutate()
          }
          nextLabel={step === 2 ? t('common.save') : t('common.continue')}
          nextDisabled={
            (step === 0 && !connectorKey) ||
            (step === 2 && (!testResult?.succeeded || test.isPending))
          }
          nextLoading={save.isPending}
          extra={
            step === 2 && !test.isPending ? (
              <Button variant="secondary" onClick={() => test.mutate()}>
                {t('sources.testConnection')}
              </Button>
            ) : null
          }
        />
      </div>
      </>
      )}
    </div>
  );
}
