'use client';

import * as React from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, ArrowLeft, CheckCircle2, Lock, PlugZap } from 'lucide-react';

import { ApiError, connectorApi, destinationApi, sourceApi } from '@/lib/api';
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
import type { ActorTestResult } from '@/lib/types';

type Kind = 'source' | 'destination';

/**
 * Create Source / Create Destination wizard (section 12.2).
 * Step state lives here for the whole wizard, so going Back never loses input.
 */
export function ActorWizard({ kind }: { kind: Kind }) {
  const { t } = useI18n();
  const router = useRouter();
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

  const connectors = useQuery({
    queryKey: qk.connectors(workspaceId, { type: connectorType }),
    queryFn: () => connectorApi.list({ type: connectorType }),
  });

  const connector = useQuery({
    queryKey: qk.connector(workspaceId, connectorKey ?? ''),
    queryFn: () => connectorApi.detail(connectorKey as string),
    enabled: Boolean(connectorKey),
  });

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
      });
    },
    onSuccess: (created) => {
      queryClient.invalidateQueries({ queryKey: ['workspace', workspaceId] });
      toastSuccess(t(isSource ? 'sources.created' : 'destinations.created'), created.name);
      router.push(`${basePath}/${created.id}`);
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
                  <p className="font-mono text-tiny text-text-quaternary">
                    v{connector.data?.version}
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
                    placeholder={isSource ? 'Production Postgres' : 'Analytics Warehouse'}
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

              <div className="rounded-lg border border-[rgb(var(--border-line))] bg-surface-1 p-4">
                <DynamicConnectorForm
                  spec={spec}
                  values={values}
                  errors={errors}
                  onChange={setValues}
                />
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
