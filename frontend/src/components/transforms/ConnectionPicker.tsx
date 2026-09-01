'use client';

import * as React from 'react';
import { useMutation } from '@tanstack/react-query';
import { KeyRound } from 'lucide-react';

import { transformApi } from '@/lib/api';
import { Button } from '@/components/ui/Button';
import { Label, Textarea } from '@/components/ui/Input';

export type ChosenConnection = { secret_ref: string; account: string | null };

export type ConnectionPickerCopy = {
  account: string;
  accountDefault: string;
  useAnother: string;
  useDefault: string;
  credentials: string;
  credentialsHint: string;
  connect: string;
};

/**
 * Which warehouse account this Transform will run as.
 *
 * A Destination's credential exists so a Pipeline can write. A Transform often
 * has to read a project the Pipeline never touches, and widening the ingestion
 * account to allow it is a change to production ingestion made for the sake of
 * a report. Naming a different account here avoids that.
 *
 * Still one account. dbt reads its sources and writes its models through a
 * single profile, so whatever is named here has to do both -- which the hint
 * says outright, because discovering it from a failed run is expensive.
 */
export function ConnectionPicker({
  destinationId, copy, connection, onChange, disabled,
}: {
  destinationId: string;
  copy: ConnectionPickerCopy;
  connection: ChosenConnection | null;
  onChange: (value: ChosenConnection | null) => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = React.useState(false);
  const [credentials, setCredentials] = React.useState('');

  const connect = useMutation({
    mutationFn: () => transformApi.verifyConnection(destinationId, {
      credentials_json: credentials.trim(),
    }),
    onSuccess: (result) => {
      onChange({ secret_ref: result.secret_ref, account: result.account });
      setOpen(false);
      setCredentials('');
    },
  });

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2 rounded-md border border-[rgb(var(--border-line))] bg-surface-2/50 px-3 py-2">
        <KeyRound className="h-3.5 w-3.5 shrink-0 text-text-quaternary" />
        <span className="shrink-0 text-tiny text-text-tertiary">{copy.account}</span>
        <span className="min-w-0 flex-1 truncate font-mono text-tiny text-text-secondary">
          {connection?.account ?? copy.accountDefault}
        </span>
        {connection ? (
          <Button size="xs" variant="ghost" disabled={disabled}
            onClick={() => onChange(null)}>{copy.useDefault}</Button>
        ) : (
          <Button size="xs" variant="ghost" disabled={disabled || !destinationId}
            onClick={() => setOpen((value) => !value)}>{copy.useAnother}</Button>
        )}
      </div>

      {open && (
        <div className="rounded-md border border-[rgb(var(--border-line))] p-3">
          <Label required>{copy.credentials}</Label>
          <Textarea rows={5} value={credentials} spellCheck={false}
            placeholder={'{ "type": "service_account", ... }'}
            onChange={(event) => setCredentials(event.target.value)} />
          <p className="mt-1 text-tiny text-text-tertiary">{copy.credentialsHint}</p>
          {connect.error ? (
            <p className="mt-1.5 text-caption text-danger">
              {(connect.error as Error).message}
            </p>
          ) : null}
          <div className="mt-2 flex justify-end">
            <Button size="xs" variant="primary" loading={connect.isPending}
              disabled={credentials.trim().length < 20}
              onClick={() => connect.mutate()}>{copy.connect}</Button>
          </div>
        </div>
      )}
    </div>
  );
}
