import { Accordion, Badge, Group, Loader, Paper, Stack, Text } from '@mantine/core';
import { IconHistory } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { api } from '../../api/client';
import type { ReferentialDetail } from '../../api/types';
import { EmptyState } from '../../components/Common';
import { RunStatusBadge, TRIGGERS } from '../../components/Status';
import { LOCALE, fmtBytes, fmtDate, fmtDelta, fmtDuration, fmtNumber } from '../../lib/format';



export default function HistoryTab({ r }: { r: ReferentialDetail }) {
  const { data, isLoading } = useQuery({
    queryKey: ['runs', r.id],
    queryFn: () => api.runs(r.id, 100),
    refetchInterval: r.current_run ? 1500 : 15000,
  });
  if (isLoading) return <Loader />;
  if (!data?.length) return <EmptyState icon={<IconHistory size={28} />} title="No update yet" />;

  return (
    <Paper withBorder>
      <Accordion variant="default" chevronPosition="left">
        {data.map((run) => {
          const delta = run.rows != null && run.previous_rows != null ? run.rows - run.previous_rows : null;
          return (
            <Accordion.Item key={run.id} value={run.id}>
              <Accordion.Control>
                <Group justify="space-between" wrap="nowrap">
                  <Group gap="sm" wrap="nowrap">
                    <RunStatusBadge status={run.status} />
                    <div>
                      <Text size="sm" fw={600}>
                        {fmtDate(run.started_at ?? run.queued_at)}
                        {run.version != null && run.status === 'success' && (
                          <Badge ml={8} size="xs" variant="outline">
                            v{run.version}
                          </Badge>
                        )}
                      </Text>
                      <Text size="xs" c="dimmed" lineClamp={1}>
                        {run.message ?? run.phase}
                      </Text>
                    </div>
                  </Group>
                  <Group gap="lg" wrap="nowrap" visibleFrom="sm">
                    <Stat
                      label="Trigger"
                      value={(TRIGGERS[run.trigger] ?? run.trigger) + (run.force ? ' (forced)' : '') + (run.user ? ` · ${run.user}` : '')}
                    />
                    <Stat label="Duration" value={fmtDuration(run.duration)} />
                    <Stat label="Downloaded" value={fmtBytes(run.downloaded_bytes ?? run.progress.done)} />
                    <Stat
                      label="Rows"
                      value={
                        <>
                          {fmtNumber(run.rows)}
                          {delta != null && delta !== 0 && (
                            <Text span size="xs" c={delta > 0 ? 'teal' : 'red'} ml={4}>
                              {fmtDelta(delta)}
                            </Text>
                          )}
                        </>
                      }
                    />
                    <Stat
                      label="+ / − / ~"
                      value={run.changes ? `${fmtNumber(run.changes.added)} / ${fmtNumber(run.changes.removed)} / ${fmtNumber(run.changes.modified)}` : '—'}
                    />
                  </Group>
                </Group>
              </Accordion.Control>
              <Accordion.Panel>
                <Stack gap={0} className="logs">
                  {(run.logs ?? []).map((l, i) => (
                    <div key={i}>
                      <Text span c="dimmed" inherit>
                        {new Date(l.t).toLocaleTimeString(LOCALE)}{' '}
                      </Text>
                      <Text
                        span
                        inherit
                        c={l.level === 'error' ? 'red' : l.level === 'warn' ? 'orange' : undefined}
                      >
                        {l.msg}
                      </Text>
                    </div>
                  ))}
                </Stack>
              </Accordion.Panel>
            </Accordion.Item>
          );
        })}
      </Accordion>
    </Paper>
  );
}

function Stat({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div style={{ minWidth: 80 }}>
      <Text size="xs" c="dimmed">
        {label}
      </Text>
      <Text size="sm" className="tabular" component="div">
        {value}
      </Text>
    </div>
  );
}
