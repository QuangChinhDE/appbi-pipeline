'use client';

import * as React from 'react';
import { Plus, Trash2 } from 'lucide-react';

import { Button, IconButton } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { Input, Select } from '@/components/ui/Input';
import { Tabs } from '@/components/ui/Tabs';
import { useI18n } from '@/providers/LanguageProvider';
import { Field, JinjaInput } from '@/components/builder/BuilderField';
import type { BuilderKeyValue, BuilderStream, BuilderUserInput } from '@/lib/types';

export type BuilderStreamSection =
  | 'request'
  | 'pagination'
  | 'incremental'
  | 'partition'
  | 'transform'
  | 'errors';

/**
 * The full low-code surface for one stream.
 *
 * Airbyte's CDK offers far more than a request URL, and a builder that hides
 * pagination, partitioning, transformations and retries is a demo. It is also
 * a lot of fields at once, so the editor keeps one focused section visible and
 * lets the page persist that section in the URL.
 */
export function StreamEditor({
  stream, streamNames, fields, userInputs, activeSection, onSectionChange,
  onCreateInput, disabled, onChange,
}: {
  stream: BuilderStream;
  /** Other streams, offered as a parent for substream partitioning. */
  streamNames: string[];
  /** Fields a test read has seen, so cursors and keys can be picked not typed. */
  fields: string[];
  /** The connector's own inputs, offered on every field that is a template. */
  userInputs: BuilderUserInput[];
  /** The focused editor section. Kept by the page in the URL. */
  activeSection: BuilderStreamSection;
  onSectionChange: (section: BuilderStreamSection) => void;
  onCreateInput?: () => void;
  disabled?: boolean;
  onChange: (next: Partial<BuilderStream>) => void;
}) {
  const { t } = useI18n();
  /* Only the fields that really are Jinja templates in the compiled manifest
   * get the picker. A parameter *name* is not one, and putting the icon there
   * would teach the wrong thing about where interpolation works. */
  const jinja = { userInputs, onCreateInput, disabled };

  return (
    <div className="space-y-4">
      <div className="flex min-w-0 flex-wrap items-center gap-1.5">
        <code
          className="max-w-full truncate rounded-sm bg-surface-2 px-2 py-1 text-tiny text-text-tertiary"
          title={stream.path || '/'}
        >
          {stream.path || '/'}
        </code>
        {stream.pagination?.mode && stream.pagination.mode !== 'none' && (
          <Badge variant="info" size="xs" pill={false}>
            {t('builder.chipPagination', {
              mode: t(`builder.page${paginationLabel(stream.pagination.mode)}`),
            })}
          </Badge>
        )}
        {stream.incremental && (
          <Badge variant="success" size="xs" pill={false}>
            {t('builder.chipIncremental')}
          </Badge>
        )}
        {stream.partition?.mode === 'parent' && stream.partition.parent_stream && (
          <Badge variant="brand" size="xs" pill={false}>
            {t('builder.chipParent', { name: stream.partition.parent_stream })}
          </Badge>
        )}
        {(stream.transformations ?? []).length > 0 && (
          <Badge variant="neutral" size="xs" pill={false}>
            {t('builder.chipTransforms', {
              n: String((stream.transformations ?? []).length),
            })}
          </Badge>
        )}
        {(stream.error_handler?.max_retries ?? 0) > 0 && (
          <Badge variant="warning" size="xs" pill={false}>
            {t('builder.chipRetries', {
              n: String(stream.error_handler?.max_retries ?? 0),
            })}
          </Badge>
        )}
      </div>

      <Tabs
        value={activeSection}
        onChange={(section) => onSectionChange(section as BuilderStreamSection)}
        className="-mt-1"
        items={[
          { id: 'request', label: t('builder.groupRequest') },
          { id: 'pagination', label: t('builder.groupPagination') },
          { id: 'incremental', label: t('builder.groupIncremental') },
          { id: 'partition', label: t('builder.groupPartition') },
          { id: 'transform', label: t('builder.groupTransform') },
          { id: 'errors', label: t('builder.groupErrors') },
        ]}
      />

      <Group title={t('builder.groupRequest')} active={activeSection === 'request'}>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label={t('builder.streamName')} htmlFor="stream-name" required>
            <Input id="stream-name" size="sm" value={stream.name} disabled={disabled}
                   onChange={(e) => onChange({ name: e.target.value })} />
          </Field>
          <Field label={t('builder.streamPath')} htmlFor="stream-path" required
                 hint={t('builder.streamPathHint')}>
            <JinjaInput id="stream-path" value={stream.path} {...jinja}
                        onChange={(value) => onChange({ path: value })}
                        placeholder="/v1/orders" />
          </Field>
        </div>

        <div className="grid gap-3 sm:grid-cols-3">
          <Field label={t('builder.httpMethod')} htmlFor="stream-method">
            <Select id="stream-method" size="sm" value={stream.http_method} disabled={disabled}
                    onChange={(e) => onChange({ http_method: e.target.value as 'GET' | 'POST' })}>
              <option value="GET">GET</option>
              <option value="POST">POST</option>
            </Select>
          </Field>
          <Field label={t('builder.recordSelector')} htmlFor="stream-selector"
                 hint={t('builder.recordSelectorHint')}>
            <Input id="stream-selector" size="sm" value={stream.record_selector}
                   disabled={disabled}
                   onChange={(e) => onChange({ record_selector: e.target.value })}
                   placeholder="data.items" />
          </Field>
          <Field label={t('builder.primaryKey')} htmlFor="stream-pk"
                 hint={t('builder.primaryKeyHint')}>
            <Picker id="stream-pk" value={stream.primary_key} options={fields}
                    disabled={disabled}
                    onChange={(value) => onChange({ primary_key: value })} />
          </Field>
        </div>

        <KeyValueRows
          label={t('builder.queryParams')}
          rows={stream.query_params}
          jinja={jinja}
          disabled={disabled}
          onChange={(rows) => onChange({ query_params: rows })}
        />
        <KeyValueRows
          label={t('builder.headers')}
          rows={stream.headers}
          jinja={jinja}
          disabled={disabled}
          onChange={(rows) => onChange({ headers: rows })}
        />

        {stream.http_method === 'POST' && (
          <>
            <Field label={t('builder.bodyMode')} htmlFor="body-mode">
              <Select id="body-mode" size="sm" disabled={disabled}
                      value={stream.request_body?.mode ?? 'json'}
                      onChange={(e) => onChange({
                        request_body: {
                          mode: e.target.value as 'json' | 'form',
                          entries: stream.request_body?.entries ?? [],
                        },
                      })}>
                <option value="json">JSON</option>
                <option value="form">Form</option>
              </Select>
            </Field>
            <KeyValueRows
              label={t('builder.bodyFields')}
              rows={stream.request_body?.entries ?? []}
              jinja={jinja}
              disabled={disabled}
              onChange={(rows) => onChange({
                request_body: { mode: stream.request_body?.mode ?? 'json', entries: rows },
              })}
            />
          </>
        )}
      </Group>

      <Group title={t('builder.groupPagination')} active={activeSection === 'pagination'}
             summary={t(`builder.page${paginationLabel(stream.pagination?.mode)}`)}>
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label={t('builder.paginationMode')} htmlFor="stream-pagination">
            <Select id="stream-pagination" size="sm" disabled={disabled}
                    value={stream.pagination?.mode ?? 'none'}
                    onChange={(e) => onChange({
                      pagination: { ...stream.pagination, mode: e.target.value as never },
                    })}>
              <option value="none">{t('builder.pageNone')}</option>
              <option value="page">{t('builder.pageNumber')}</option>
              <option value="offset">{t('builder.pageOffset')}</option>
              <option value="cursor">{t('builder.pageCursor')}</option>
              <option value="link_header">{t('builder.pageLinkHeader')}</option>
            </Select>
          </Field>

          {stream.pagination?.mode !== 'none' && (
            <Field label={t('builder.pageSize')} htmlFor="page-size"
                   hint={t('builder.pageSizeHint')}>
              <Input id="page-size" size="sm" type="number" min={1} disabled={disabled}
                     value={stream.pagination?.page_size == null
                       ? '' : String(stream.pagination.page_size)}
                     placeholder={t('builder.pageSizeNone')}
                     onChange={(e) => onChange({
                       pagination: {
                         ...stream.pagination,
                         page_size: e.target.value.trim()
                           ? Number(e.target.value) : null,
                       },
                     })} />
            </Field>
          )}

          {['page', 'offset', 'cursor'].includes(stream.pagination?.mode ?? '') && (
            <Field label={t('builder.pageInject')} htmlFor="page-inject"
                   hint={t('builder.injectHint')}>
              <Select id="page-inject" size="sm" disabled={disabled}
                      value={stream.pagination?.inject_into ?? 'request_parameter'}
                      onChange={(e) => onChange({
                        pagination: {
                          ...stream.pagination,
                          inject_into: e.target.value as never,
                        },
                      })}>
                <option value="request_parameter">{t('builder.injectQuery')}</option>
                <option value="body_data">{t('builder.injectBodyForm')}</option>
                <option value="body_json">{t('builder.injectBodyJson')}</option>
                <option value="header">{t('builder.injectHeader')}</option>
              </Select>
            </Field>
          )}

          {['page', 'offset', 'cursor'].includes(stream.pagination?.mode ?? '') && (
            <Field label={t('builder.pageParam')} htmlFor="page-param">
              <Input id="page-param" size="sm" disabled={disabled}
                     value={stream.pagination?.page_param ?? ''}
                     onChange={(e) => onChange({
                       pagination: { ...stream.pagination, page_param: e.target.value },
                     })}
                     placeholder={stream.pagination?.mode === 'page' ? 'page' : 'offset'} />
            </Field>
          )}
        </div>

        {stream.pagination?.mode === 'cursor' && (
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label={t('builder.cursorPath')} htmlFor="cursor-path" required
                   hint={t('builder.cursorPathHint')}>
              <Input id="cursor-path" size="sm" disabled={disabled}
                     value={stream.pagination?.cursor_path ?? ''}
                     onChange={(e) => onChange({
                       pagination: { ...stream.pagination, cursor_path: e.target.value },
                     })}
                     placeholder="meta.next_cursor" />
            </Field>
            <Field label={t('builder.stopCondition')} htmlFor="stop-condition"
                   hint={t('builder.stopConditionHint')}>
              <Input id="stop-condition" size="sm" disabled={disabled}
                     value={stream.pagination?.stop_condition ?? ''}
                     onChange={(e) => onChange({
                       pagination: { ...stream.pagination, stop_condition: e.target.value },
                     })} />
            </Field>
          </div>
        )}
      </Group>

      <Group title={t('builder.groupIncremental')} active={activeSection === 'incremental'}
             summary={stream.incremental ? (stream.cursor_field || '—') : t('builder.off')}>
        <label className="flex items-center gap-2 text-caption text-text-secondary">
          <input type="checkbox" checked={stream.incremental} disabled={disabled}
                 onChange={(e) => onChange({ incremental: e.target.checked })}
                 className="h-3.5 w-3.5 rounded border-[rgb(var(--border-strong))]" />
          {t('builder.incremental')}
        </label>

        {stream.incremental && (
          <>
            <div className="grid gap-3 sm:grid-cols-3">
              <Field label={t('builder.cursorField')} htmlFor="cursor-field" required>
                <Picker id="cursor-field" value={stream.cursor_field ?? ''} options={fields}
                        disabled={disabled}
                        onChange={(value) => onChange({ cursor_field: value })} />
              </Field>
              <Field label={t('builder.cursorFormat')} htmlFor="cursor-format">
                <Input id="cursor-format" size="sm" disabled={disabled}
                       value={stream.cursor_format ?? ''}
                       onChange={(e) => onChange({ cursor_format: e.target.value })}
                       placeholder="%Y-%m-%dT%H:%M:%SZ" />
              </Field>
              <Field label={t('builder.cursorFilterMode')} htmlFor="cursor-filter-mode"
                     hint={t('builder.cursorFilterModeHint')}>
                <Select id="cursor-filter-mode" size="sm" disabled={disabled}
                        value={stream.cursor_filter_mode ?? 'server'}
                        onChange={(e) => onChange({
                          cursor_filter_mode: e.target.value as never,
                        })}>
                  <option value="server">{t('builder.cursorFilterServer')}</option>
                  <option value="client">{t('builder.cursorFilterClient')}</option>
                </Select>
              </Field>
            </div>

            {(stream.cursor_filter_mode ?? 'server') === 'client' ? (
              <p className="text-caption text-text-tertiary">
                {t('builder.cursorFilterClientNote')}
              </p>
            ) : (
              <div className="grid gap-3 sm:grid-cols-3">
                <Field label={t('builder.cursorParam')} htmlFor="cursor-param"
                       hint={t('builder.cursorParamHint')}>
                  <Input id="cursor-param" size="sm" disabled={disabled}
                         value={stream.cursor_param ?? ''}
                         onChange={(e) => onChange({ cursor_param: e.target.value })}
                         placeholder="updated_since" />
                </Field>
                <Field label={t('builder.cursorEndParam')} htmlFor="cursor-end">
                  <Input id="cursor-end" size="sm" disabled={disabled}
                         value={stream.cursor_end_param ?? ''}
                         onChange={(e) => onChange({ cursor_end_param: e.target.value })}
                         placeholder="updated_before" />
                </Field>
                <Field label={t('builder.cursorInject')} htmlFor="cursor-inject"
                       hint={t('builder.injectHint')}>
                  <Select id="cursor-inject" size="sm" disabled={disabled}
                          value={stream.cursor_inject_into ?? 'request_parameter'}
                          onChange={(e) => onChange({
                            cursor_inject_into: e.target.value as never,
                          })}>
                    <option value="request_parameter">{t('builder.injectQuery')}</option>
                    <option value="body_data">{t('builder.injectBodyForm')}</option>
                    <option value="body_json">{t('builder.injectBodyJson')}</option>
                    <option value="header">{t('builder.injectHeader')}</option>
                  </Select>
                </Field>
              </div>
            )}

            <div className="grid gap-3 sm:grid-cols-3">
              <Field label={t('builder.step')} htmlFor="cursor-step"
                     hint={t('builder.stepHint')}>
                <Input id="cursor-step" size="sm" disabled={disabled}
                       value={stream.step ?? ''}
                       onChange={(e) => onChange({ step: e.target.value })}
                       placeholder="P30D" />
              </Field>
              <Field label={t('builder.lookback')} htmlFor="cursor-lookback"
                     hint={t('builder.lookbackHint')}>
                <Input id="cursor-lookback" size="sm" disabled={disabled}
                       value={stream.lookback ?? ''}
                       onChange={(e) => onChange({ lookback: e.target.value })}
                       placeholder="P1D" />
              </Field>
            </div>
          </>
        )}
      </Group>

      <Group title={t('builder.groupPartition')} active={activeSection === 'partition'}
             summary={stream.partition?.mode && stream.partition.mode !== 'none'
               ? t(`builder.partition${stream.partition.mode === 'list' ? 'List' : 'Parent'}`)
               : t('builder.off')}>
        <Field label={t('builder.partitionMode')} htmlFor="partition-mode"
               hint={t('builder.partitionHint')}>
          <Select id="partition-mode" size="sm" disabled={disabled}
                  value={stream.partition?.mode ?? 'none'}
                  onChange={(e) => onChange({
                    partition: { ...stream.partition, mode: e.target.value as never },
                  })}>
            <option value="none">{t('builder.off')}</option>
            <option value="list">{t('builder.partitionList')}</option>
            <option value="parent">{t('builder.partitionParent')}</option>
          </Select>
        </Field>

        {stream.partition?.mode === 'list' && (
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label={t('builder.partitionValues')} htmlFor="partition-values" required
                   hint={t('builder.partitionValuesHint')}>
              <Input id="partition-values" size="sm" disabled={disabled}
                     value={stream.partition?.values ?? ''}
                     onChange={(e) => onChange({
                       partition: { ...stream.partition!, values: e.target.value },
                     })}
                     placeholder="us, eu, apac" />
            </Field>
            <Field label={t('builder.partitionParam')} htmlFor="partition-param">
              <Input id="partition-param" size="sm" disabled={disabled}
                     value={stream.partition?.param ?? ''}
                     onChange={(e) => onChange({
                       partition: { ...stream.partition!, param: e.target.value },
                     })}
                     placeholder="region" />
            </Field>
          </div>
        )}

        {stream.partition?.mode === 'parent' && (
          <div className="grid gap-3 sm:grid-cols-3">
            <Field label={t('builder.parentStream')} htmlFor="parent-stream" required>
              <Select id="parent-stream" size="sm" disabled={disabled}
                      value={stream.partition?.parent_stream ?? ''}
                      onChange={(e) => onChange({
                        partition: { ...stream.partition!, parent_stream: e.target.value },
                      })}>
                <option value="">— {t('builder.parentStream')} —</option>
                {streamNames.filter((n) => n !== stream.name).map((name) => (
                  <option key={name} value={name}>{name}</option>
                ))}
              </Select>
            </Field>
            <Field label={t('builder.parentKey')} htmlFor="parent-key">
              <Input id="parent-key" size="sm" disabled={disabled}
                     value={stream.partition?.parent_key ?? ''}
                     onChange={(e) => onChange({
                       partition: { ...stream.partition!, parent_key: e.target.value },
                     })}
                     placeholder="id" />
            </Field>
            <Field label={t('builder.partitionField')} htmlFor="partition-field"
                   hint={t('builder.partitionFieldHint')}>
              <Input id="partition-field" size="sm" disabled={disabled}
                     value={stream.partition?.partition_field ?? ''}
                     onChange={(e) => onChange({
                       partition: { ...stream.partition!, partition_field: e.target.value },
                     })}
                     placeholder="parent_id" />
            </Field>
          </div>
        )}

        {stream.partition?.mode === 'parent' && (
          <>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label={t('builder.parentParam')} htmlFor="parent-param"
                     hint={t('builder.parentParamHint')}>
                <Input id="parent-param" size="sm" disabled={disabled}
                       value={stream.partition?.param ?? ''}
                       onChange={(e) => onChange({
                         partition: { ...stream.partition!, param: e.target.value },
                       })}
                       placeholder="service_id" />
              </Field>
              <Field label={t('builder.parentInject')} htmlFor="parent-inject"
                     hint={t('builder.injectHint')}>
                <Select id="parent-inject" size="sm" disabled={disabled}
                        value={stream.partition?.inject_into ?? 'request_parameter'}
                        onChange={(e) => onChange({
                          partition: {
                            ...stream.partition!,
                            inject_into: e.target.value as never,
                          },
                        })}>
                  <option value="request_parameter">{t('builder.injectQuery')}</option>
                  <option value="body_data">{t('builder.injectBodyForm')}</option>
                  <option value="body_json">{t('builder.injectBodyJson')}</option>
                  <option value="header">{t('builder.injectHeader')}</option>
                </Select>
              </Field>
            </div>

            {!stream.partition?.param && (
              <p className="text-caption text-text-tertiary">
                {t('builder.parentParamMissing')}
              </p>
            )}

            <label className="flex items-start gap-2 text-caption text-text-secondary">
              <input type="checkbox" className="mt-0.5 h-3.5 w-3.5 rounded
                                                border-[rgb(var(--border-strong))]"
                     checked={Boolean(stream.partition?.incremental_parent)}
                     disabled={disabled}
                     onChange={(e) => onChange({
                       partition: {
                         ...stream.partition!,
                         incremental_parent: e.target.checked,
                       },
                     })} />
              <span>
                {t('builder.incrementalParent')}
                <span className="block text-text-tertiary">
                  {t('builder.incrementalParentWarning')}
                </span>
              </span>
            </label>
          </>
        )}
      </Group>

      <Group title={t('builder.groupTransform')} active={activeSection === 'transform'}
             summary={String((stream.transformations ?? []).length || t('builder.off'))}>
        <Field label={t('builder.recordFilter')} htmlFor="record-filter"
               hint={t('builder.recordFilterHint')}>
          <JinjaInput id="record-filter" value={stream.record_filter ?? ''} {...jinja}
                      onChange={(value) => onChange({ record_filter: value })}
                 placeholder="{{ record.status == 'active' }}" />
        </Field>

        <div className="space-y-1.5">
          <p className="text-label text-text-secondary">{t('builder.transformations')}</p>
          {(stream.transformations ?? []).map((item, index) => (
            <div
              key={index}
              className="grid grid-cols-[minmax(0,1fr)_2rem] gap-1.5 rounded-md border border-[rgb(var(--border-line))] p-2 sm:grid-cols-[7rem_minmax(8rem,1fr)_minmax(9rem,1fr)_2rem] sm:border-0 sm:p-0"
            >
              <Select size="sm" aria-label={t('builder.transformKind')} disabled={disabled}
                      value={item.type}
                      onChange={(e) => onChange({
                        transformations: (stream.transformations ?? []).map((row, i) =>
                          i === index ? { ...row, type: e.target.value as 'add' | 'remove' } : row),
                      })}>
                <option value="add">{t('builder.transformAdd')}</option>
                <option value="remove">{t('builder.transformRemove')}</option>
              </Select>
              <Input size="sm" aria-label={t('builder.transformPath')} disabled={disabled}
                     className="col-span-2 min-w-0 sm:col-span-1"
                     value={item.path} placeholder="field.path"
                     onChange={(e) => onChange({
                       transformations: (stream.transformations ?? []).map((row, i) =>
                         i === index ? { ...row, path: e.target.value } : row),
                     })} />
              {item.type === 'add' && (
                <Input size="sm" aria-label={t('builder.transformValue')} disabled={disabled}
                       className="col-span-2 min-w-0 sm:col-span-1"
                       value={item.value ?? ''} placeholder="{{ now_utc() }}"
                       onChange={(e) => onChange({
                         transformations: (stream.transformations ?? []).map((row, i) =>
                           i === index ? { ...row, value: e.target.value } : row),
                       })} />
              )}
              <IconButton size="xs" variant="ghost" disabled={disabled}
                          className="col-start-2 row-start-1 sm:col-start-4"
                          aria-label={t('builder.removeTransform')}
                          title={t('builder.removeTransform')}
                          onClick={() => onChange({
                        transformations: (stream.transformations ?? [])
                          .filter((_, i) => i !== index),
                          })}>
                <Trash2 className="h-3.5 w-3.5" />
              </IconButton>
            </div>
          ))}
          {!disabled && (
            <Button size="xs" variant="ghost" leadingIcon={<Plus className="h-3 w-3" />}
                    onClick={() => onChange({
                      transformations: [...(stream.transformations ?? []),
                        { type: 'add', path: '', value: '' }],
                    })}>
              {t('builder.addTransform')}
            </Button>
          )}
        </div>
      </Group>

      <Group title={t('builder.groupErrors')} active={activeSection === 'errors'}
             summary={stream.error_handler?.backoff?.mode
               && stream.error_handler.backoff.mode !== 'none'
               ? stream.error_handler.backoff.mode : t('builder.default')}>
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label={t('builder.maxRetries')} htmlFor="max-retries">
            <Input id="max-retries" size="sm" type="number" min={0} disabled={disabled}
                   value={stream.error_handler?.max_retries ?? ''}
                   onChange={(e) => onChange({
                     error_handler: {
                       ...stream.error_handler,
                       max_retries: e.target.value === '' ? undefined : Number(e.target.value),
                     },
                   })}
                   placeholder="5" />
          </Field>
          <Field label={t('builder.backoff')} htmlFor="backoff-mode">
            <Select id="backoff-mode" size="sm" disabled={disabled}
                    value={stream.error_handler?.backoff?.mode ?? 'none'}
                    onChange={(e) => onChange({
                      error_handler: {
                        ...stream.error_handler,
                        backoff: {
                          ...stream.error_handler?.backoff,
                          mode: e.target.value as never,
                        },
                      },
                    })}>
              <option value="none">{t('builder.default')}</option>
              <option value="constant">{t('builder.backoffConstant')}</option>
              <option value="exponential">{t('builder.backoffExponential')}</option>
              <option value="header">{t('builder.backoffHeader')}</option>
            </Select>
          </Field>
          {stream.error_handler?.backoff?.mode === 'constant' && (
            <Field label={t('builder.backoffSeconds')} htmlFor="backoff-seconds">
              <Input id="backoff-seconds" size="sm" type="number" min={1} disabled={disabled}
                     value={String(stream.error_handler?.backoff?.seconds ?? 5)}
                     onChange={(e) => onChange({
                       error_handler: {
                         ...stream.error_handler,
                         backoff: {
                           ...stream.error_handler!.backoff!,
                           seconds: Number(e.target.value) || 5,
                         },
                       },
                     })} />
            </Field>
          )}
          {stream.error_handler?.backoff?.mode === 'header' && (
            <Field label={t('builder.backoffHeaderName')} htmlFor="backoff-header">
              <Input id="backoff-header" size="sm" disabled={disabled}
                     value={stream.error_handler?.backoff?.header ?? ''}
                     onChange={(e) => onChange({
                       error_handler: {
                         ...stream.error_handler,
                         backoff: {
                           ...stream.error_handler!.backoff!,
                           header: e.target.value,
                         },
                       },
                     })}
                     placeholder="Retry-After" />
            </Field>
          )}
        </div>
      </Group>
    </div>
  );
}

function paginationLabel(mode?: string): string {
  if (mode === 'page') return 'Number';
  if (mode === 'offset') return 'Offset';
  if (mode === 'cursor') return 'Cursor';
  if (mode === 'link_header') return 'LinkHeader';
  return 'None';
}

/** The selected stream section. Tabs above it keep the form short and stable. */
function Group({
  title, active, children,
}: {
  title: string;
  active: boolean;
  summary?: React.ReactNode;
  defaultOpen?: boolean;
  children: React.ReactNode;
}) {
  if (!active) return null;
  return (
    <section aria-label={title} className="space-y-3">
      {children}
    </section>
  );
}


/**
 * Free text until a test read has shown which fields exist, then a picker —
 * typing a column name from memory is where most builder mistakes come from.
 */
function Picker({
  id, value, options, disabled, onChange,
}: {
  id: string;
  value: string;
  options: string[];
  disabled?: boolean;
  onChange: (value: string) => void;
}) {
  return (
    <>
      <Input id={id} size="sm" value={value} disabled={disabled}
             list={options.length ? `${id}-options` : undefined}
             onChange={(event) => onChange(event.target.value)} />
      {options.length > 0 && (
        <datalist id={`${id}-options`}>
          {options.map((option) => <option key={option} value={option} />)}
        </datalist>
      )}
    </>
  );
}

function KeyValueRows({
  label, rows, disabled, onChange, jinja,
}: {
  label: string;
  rows: BuilderKeyValue[];
  disabled?: boolean;
  onChange: (rows: BuilderKeyValue[]) => void;
  /** Passed to the value column: `{{ config['token'] }}` belongs there. */
  jinja?: {
    userInputs: BuilderUserInput[];
    onCreateInput?: () => void;
    disabled?: boolean;
  };
}) {
  const { t } = useI18n();
  return (
    <div>
      <p className="mb-1 text-label text-text-secondary">{label}</p>
      <div className="space-y-1.5">
        {rows.length > 0 && (
          <div className="hidden grid-cols-[minmax(7rem,0.72fr)_minmax(12rem,1.35fr)_2rem] gap-1.5 px-0.5 text-tiny text-text-quaternary sm:grid">
            <span>{t('builder.inputKey')}</span>
            <span>{t('builder.transformValue')}</span>
            <span aria-hidden />
          </div>
        )}
        {rows.map((row, index) => (
          <div
            key={index}
            className="grid grid-cols-[minmax(0,1fr)_2rem] gap-1.5 rounded-md border border-[rgb(var(--border-line))] p-2 sm:grid-cols-[minmax(7rem,0.72fr)_minmax(12rem,1.35fr)_2rem] sm:border-0 sm:p-0"
          >
            <Input size="sm" className="min-w-0"
                   aria-label={`${label} — ${t('builder.inputKey')} ${index + 1}`}
                   value={row.key} disabled={disabled} placeholder={t('builder.inputKey')}
                   onChange={(event) => onChange(rows.map((r, i) =>
                     i === index ? { ...r, key: event.target.value } : r))} />
            <div className="col-span-2 min-w-0 sm:col-span-1">
              {jinja ? (
                <JinjaInput
                  id={`${label}-value-${index}`}
                  value={row.value}
                  {...jinja}
                  onChange={(value) => onChange(rows.map((r, i) =>
                    i === index ? { ...r, value } : r))}
                  placeholder={t('builder.transformValue')}
                />
              ) : (
                <Input size="sm" className="min-w-0"
                       aria-label={`${label} — ${t('builder.transformValue')} ${index + 1}`}
                       value={row.value} disabled={disabled}
                       placeholder={t('builder.transformValue')}
                       onChange={(event) => onChange(rows.map((r, i) =>
                         i === index ? { ...r, value: event.target.value } : r))} />
              )}
            </div>
            <IconButton size="xs" variant="ghost" aria-label={t('builder.removeParam')}
                        title={t('builder.removeParam')}
                        className="col-start-2 row-start-1 sm:col-start-3"
                        disabled={disabled}
                        onClick={() => onChange(rows.filter((_, i) => i !== index))}>
              <Trash2 className="h-3.5 w-3.5" />
            </IconButton>
          </div>
        ))}
        {!disabled && (
          <Button size="xs" variant="ghost" leadingIcon={<Plus className="h-3 w-3" />}
                  onClick={() => onChange([...rows, { key: '', value: '' }])}>
            {t('builder.addParam')}
          </Button>
        )}
      </div>
    </div>
  );
}
