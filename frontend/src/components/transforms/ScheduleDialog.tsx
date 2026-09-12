'use client';

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Clock } from 'lucide-react';
import * as React from 'react';

import { ScheduleEditor } from '@/components/integrations/ScheduleEditor';
import { Button } from '@/components/ui/Button';
import { Checkbox, Input, Label, Select } from '@/components/ui/Input';
import { Modal } from '@/components/ui/Modal';
import { toastError, toastSuccess } from '@/hooks/use-toast';
import { transformApi } from '@/lib/api';
import { formatDateTime } from '@/lib/format';
import type { ScheduleConfig, TransformDetail } from '@/lib/types';
import { useI18n } from '@/providers/LanguageProvider';

/**
 * dbt commands a schedule may run.
 *
 * Mirrors `SCHEDULABLE` in the backend, which refuses anything else with
 * TRANSFORM_COMMAND_NOT_SCHEDULABLE. Offering only these is the difference
 * between a picker and a guess followed by a rejection.
 */
const COMMANDS = ['build', 'run', 'test', 'seed', 'snapshot', 'source-freshness'] as const;

/**
 * Setting a Transform project to run on its own.
 *
 * The whole mechanism existed and none of it was reachable: the model carries
 * the schedule columns, the service validates and computes the next firing,
 * and `transform_scheduler_loop` in the worker has been polling for them the
 * entire time. There was no way to switch one on -- no field in the workbench,
 * and the API did not report the schedule it accepted, so nothing could have
 * drawn an editor from what it returned.
 *
 * A dialog rather than a panel: the workbench is a file editor, and a schedule
 * is set once and then left alone.
 */
export function ScheduleDialog({
  project, open, onClose, canEdit,
}: {
  project: TransformDetail;
  open: boolean;
  onClose: () => void;
  canEdit: boolean;
}) {
  const { t, locale } = useI18n();
  const queryClient = useQueryClient();

  const [schedule, setSchedule] = React.useState<ScheduleConfig>(() => ({
    type: (project.schedule_type as ScheduleConfig['type']) ?? 'MANUAL',
    interval_seconds: project.schedule_config?.interval_seconds ?? null,
    time_of_day: project.schedule_config?.time_of_day ?? null,
    cron_expression: project.schedule_config?.cron_expression ?? null,
    timezone: project.schedule_config?.timezone ?? project.timezone ?? 'Asia/Bangkok',
  }));
  const [command, setCommand] = React.useState(project.schedule_command?.command ?? 'build');
  const [selector, setSelector] = React.useState(project.schedule_command?.selector ?? '');
  const [exclude, setExclude] = React.useState(project.schedule_command?.exclude ?? '');
  const [fullRefresh, setFullRefresh] = React.useState(
    Boolean(project.schedule_command?.full_refresh));

  // Re-seeded when the dialog is opened again, so a cancelled edit does not
  // linger into the next one.
  React.useEffect(() => {
    if (!open) return;
    setSchedule({
      type: (project.schedule_type as ScheduleConfig['type']) ?? 'MANUAL',
      interval_seconds: project.schedule_config?.interval_seconds ?? null,
      time_of_day: project.schedule_config?.time_of_day ?? null,
      cron_expression: project.schedule_config?.cron_expression ?? null,
      timezone: project.schedule_config?.timezone ?? project.timezone ?? 'Asia/Bangkok',
    });
    setCommand(project.schedule_command?.command ?? 'build');
    setSelector(project.schedule_command?.selector ?? '');
    setExclude(project.schedule_command?.exclude ?? '');
    setFullRefresh(Boolean(project.schedule_command?.full_refresh));
  }, [open, project]);

  const save = useMutation({
    mutationFn: () => transformApi.update(project.id, {
      schedule_type: schedule.type,
      schedule_config: {
        interval_seconds: schedule.interval_seconds ?? null,
        time_of_day: schedule.time_of_day ?? null,
        cron_expression: schedule.cron_expression ?? null,
        timezone: schedule.timezone,
      },
      schedule_command: {
        command,
        selector: selector.trim() || null,
        exclude: exclude.trim() || null,
        full_refresh: fullRefresh,
      },
      timezone: schedule.timezone,
    }),
    onSuccess: () => {
      // Everything tenant-scoped hangs off `['workspace', id]`, so this is the
      // key that actually reaches the project query. Inventing `['transform',
      // id]` here left the header showing the previous schedule after a save
      // that had already been written -- the save looked like it had failed.
      queryClient.invalidateQueries({ queryKey: ['workspace'] });
      toastSuccess(t('tfsch.saved'));
      onClose();
    },
    onError: (caught) => toastError(caught),
  });

  // A schedule runs the published release, never the draft. Saying so here is
  // cheaper than a run at 03:00 that finds nothing to run -- which is exactly
  // what the backend sets health_message about, where nobody sees it until the
  // morning after.
  const nothingPublished = !project.active_release;

  return (
    <Modal open={open} onClose={onClose} title={t('tfsch.title')}>
      <div className="space-y-4">
        <p className="text-caption text-text-tertiary">
          {t('tfsch.introPrefix')}{' '}
          <strong className="text-text-secondary">{t('tfsch.publishedVersion')}</strong>
          {t('tfsch.introSuffix')}
        </p>

        {nothingPublished && schedule.type !== 'MANUAL' && (
          <div className="rounded-md border border-[rgb(var(--warning))] bg-warning-subtle px-3 py-2">
            <p className="text-caption text-text-primary">
              {t('tfsch.nothingPublished')}
            </p>
          </div>
        )}

        <ScheduleEditor value={schedule} onChange={setSchedule} />

        {schedule.type !== 'MANUAL' && (
          <div className="space-y-3 border-t border-[rgb(var(--border-line))] pt-4">
            <div>
              <Label htmlFor="tf-command">{t('tfsch.command')}</Label>
              <Select
                id="tf-command"
                value={command}
                onChange={(event) => setCommand(event.target.value)}
              >
                {COMMANDS.map((name) => (
                  <option key={name} value={name}>dbt {name}</option>
                ))}
              </Select>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <div>
                <Label htmlFor="tf-selector">{t('tfsch.selector')}</Label>
                <Input
                  id="tf-selector"
                  value={selector}
                  placeholder={t('tfsch.selectorPlaceholder')}
                  onChange={(event) => setSelector(event.target.value)}
                />
              </div>
              <div>
                <Label htmlFor="tf-exclude">{t('tfsch.exclude')}</Label>
                <Input
                  id="tf-exclude"
                  value={exclude}
                  placeholder={t('tfsch.excludePlaceholder')}
                  onChange={(event) => setExclude(event.target.value)}
                />
              </div>
            </div>
            {/* Its own permission, held by OWNER alone: it rebuilds every
                incremental model and throws away the materialised history.
                Offering the box to somebody the command will refuse is the
                same fault as a form field the API always rejects. */}
            {project.permissions.can_reset ? (
              <Checkbox
                checked={fullRefresh}
                onChange={setFullRefresh}
                label={t('tfsch.fullRefresh')}
              />
            ) : (
              <p className="text-tiny text-text-tertiary">
                {t('tfsch.fullRefreshDenied')}
              </p>
            )}
          </div>
        )}

        {project.next_run_at && schedule.type !== 'MANUAL' && (
          <p className="flex items-center gap-1.5 text-caption text-text-tertiary">
            <Clock className="h-3.5 w-3.5" />
            {t('tfsch.nextRun', {
              at: formatDateTime(project.next_run_at, locale),
            })}
          </p>
        )}

        <div className="flex justify-end gap-2 border-t border-[rgb(var(--border-line))] pt-4">
          <Button variant="ghost" onClick={onClose}>{t('tfsch.cancel')}</Button>
          <Button
            variant="primary"
            disabled={!canEdit}
            loading={save.isPending}
            onClick={() => save.mutate()}
          >
            {t('tfsch.save')}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
