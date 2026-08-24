'use client';

import * as React from 'react';
import { useQuery } from '@tanstack/react-query';
import { Download, Search } from 'lucide-react';

import { runApi } from '@/lib/api';
import { qk } from '@/lib/queryKeys';
import { useWorkspaceId } from '@/hooks/use-current-user';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Spinner } from '@/components/ui/Feedback';
import { cn } from '@/lib/utils';
import { useI18n } from '@/providers/LanguageProvider';

const PAGE_SIZE = 500;

/**
 * Chunked log viewer. Connector logs can be enormous, so the browser pulls one
 * window at a time rather than the whole file (section 33.6).
 */
export function LogViewer({ runId, live }: { runId: string; live?: boolean }) {
  const { t } = useI18n();
  const workspaceId = useWorkspaceId();
  const [cursor, setCursor] = React.useState(0);
  const [lines, setLines] = React.useState<string[]>([]);
  const [filter, setFilter] = React.useState('');
  const [follow, setFollow] = React.useState(Boolean(live));
  const containerRef = React.useRef<HTMLDivElement>(null);

  const { data, isLoading, isFetching, refetch } = useQuery({
    queryKey: [...qk.runLogs(workspaceId, runId), cursor],
    queryFn: () => runApi.logs(runId, cursor, PAGE_SIZE),
    refetchInterval: live && follow ? 5_000 : false,
  });

  React.useEffect(() => {
    if (!data) return;
    setLines((previous) => (cursor === 0 ? data.lines : [...previous, ...data.lines]));
  }, [data, cursor]);

  // While a run is live, keep loading forward automatically.
  React.useEffect(() => {
    if (live && follow && data?.has_more && data.next_cursor !== null) {
      setCursor(data.next_cursor);
    }
  }, [live, follow, data]);

  React.useEffect(() => {
    if (follow && containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [lines, follow]);

  const visible = filter
    ? lines.filter((line) => line.toLowerCase().includes(filter.toLowerCase()))
    : lines;

  const download = () => {
    const blob = new Blob([lines.join('\n')], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `run-${runId}.log`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <div className="max-w-xs flex-1">
          <Input
            size="sm"
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
            placeholder={t('runs.logFilter')}
            aria-label={t('runs.logFilterLabel')}
            leadingIcon={<Search />}
          />
        </div>
        {live && (
          <Button
            size="xs"
            variant={follow ? 'primary' : 'secondary'}
            onClick={() => setFollow((value) => !value)}
          >
            {follow ? t('runs.logFollowing') : t('runs.logFollow')}
          </Button>
        )}
        <Button size="xs" variant="ghost" onClick={() => { setCursor(0); refetch(); }}>
          {t('common.reload')}
        </Button>
        <Button size="xs" variant="ghost" onClick={download}
                leadingIcon={<Download className="h-3 w-3" />}>
          {t('common.download')}
        </Button>
        <span className="ml-auto text-tiny text-text-quaternary">
          {t('runs.logLines', { shown: visible.length,
            total: data?.total_lines ?? lines.length })}
        </span>
      </div>

      {/* Always a dark terminal surface, so the syntax colours are fixed
          literals rather than theme tokens. */}
      <div
        ref={containerRef}
        className="max-h-[520px] overflow-auto rounded-lg border border-[rgb(var(--border-strong))] bg-[#0b0c0e] p-3"
        tabIndex={0}
        role="log"
        aria-label={t('runs.logRegion')}
      >
        {isLoading && lines.length === 0 ? (
          <Spinner />
        ) : visible.length === 0 ? (
          <p className="py-6 text-center text-caption text-text-quaternary">
            {t('runs.logEmpty')}
          </p>
        ) : (
          <pre className="whitespace-pre-wrap break-words font-mono text-tiny leading-relaxed">
            {visible.map((line, index) => (
              <div
                key={index}
                className={cn(
                  'select-text',
                  /\[error\]|ERROR|Exception|Caused by/i.test(line) ? 'text-[#ff8080]'
                    : /\[warn\]|WARN/i.test(line) ? 'text-[#f0b429]'
                    : /^=== |source exit=|destination exit=/.test(line) ? 'text-[#8fa2ff]'
                    : 'text-[#c9cdd4]',
                )}
              >
                {line}
              </div>
            ))}
          </pre>
        )}
      </div>

      {data?.has_more && !follow && (
        <Button
          size="sm"
          variant="secondary"
          fullWidth
          loading={isFetching}
          onClick={() => data.next_cursor !== null && setCursor(data.next_cursor)}
        >
          {t('runs.logLoadMore', { n: PAGE_SIZE })}
        </Button>
      )}
    </div>
  );
}
