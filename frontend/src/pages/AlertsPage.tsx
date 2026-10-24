import {
  ActionIcon,
  Alert as Callout,
  Anchor,
  Badge,
  Card,
  Drawer,
  Group,
  Loader,
  SegmentedControl,
  SimpleGrid,
  Stack,
  Table,
  Text,
  Title,
  Tooltip,
  UnstyledButton,
} from '@mantine/core';
import {
  IconAlertTriangle,
  IconArrowRight,
  IconBell,
  IconChartArrowsVertical,
  IconClock,
  IconDog,
  IconRefresh,
  IconShieldCheck,
} from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api/client';
import { markAlertsSeen, useAlerts } from '../api/hooks';
import type { Alert, AlertKind, QualityResult, Run, Severity, WatchdogRun } from '../api/types';
import { EmptyState, StatCard } from '../components/Common';
import { Markdown } from '../components/Markdown';
import { RunStatusBadge } from '../components/Status';
import { fmtDate, fmtNumber, fmtRelative } from '../lib/format';

const SEVERITY_COLOR: Record<Severity, string> = {
  LOW: 'gray',
  MEDIUM: 'yellow',
  HIGH: 'orange',
  CRITICAL: 'red',
};

const KIND: Record<AlertKind, { label: string; icon: typeof IconBell; hint: string }> = {
  watchdog: { label: 'Watchdog', icon: IconDog, hint: 'A scheduled investigation found something, or could not finish' },
  failure: { label: 'Update', icon: IconAlertTriangle, hint: 'The last update failed, was rejected, or found a corrupted source' },
  quality: { label: 'Quality', icon: IconShieldCheck, hint: 'The published version does not pass its quality rules' },
  stale: { label: 'Stale', icon: IconClock, hint: 'No new version within the expected delay' },
  drift: { label: 'Drift', icon: IconChartArrowsVertical, hint: 'The last version changed the volume a lot' },
};

function AlertRow({ alert, onOpen }: { alert: Alert; onOpen: () => void }) {
  const kind = KIND[alert.kind];
  return (
    <UnstyledButton onClick={onOpen} w="100%">
      <Card withBorder padding="sm">
        <Group justify="space-between" wrap="nowrap" gap="sm">
          <Group gap="sm" wrap="nowrap" style={{ minWidth: 0 }}>
            <Badge color={SEVERITY_COLOR[alert.severity]} variant="filled" w={84} style={{ flexShrink: 0 }}>
              {alert.severity}
            </Badge>
            <Tooltip label={kind.hint}>
              <Badge variant="light" color="gray" leftSection={<kind.icon size={10} />} style={{ flexShrink: 0 }}>
                {kind.label}
              </Badge>
            </Tooltip>
            <div style={{ minWidth: 0 }}>
              <Text size="sm" fw={600} truncate>
                {alert.title}
              </Text>
              <Text size="xs" c="dimmed" lineClamp={1}>
                {alert.referential_name ?? alert.watchdog_name ?? ''}
                {alert.summary ? ` — ${alert.summary}` : ''}
              </Text>
            </div>
          </Group>
          <Text size="xs" c="dimmed" style={{ flexShrink: 0 }}>
            {fmtRelative(alert.at)}
          </Text>
        </Group>
      </Card>
    </UnstyledButton>
  );
}

function Detail({ id, onClose }: { id: string; onClose: () => void }) {
  const { data, isLoading, error } = useQuery({ queryKey: ['alert', id], queryFn: () => api.alert(id) });
  const alert = data?.alert;
  return (
    <Drawer opened onClose={onClose} position="right" size="xl" title={alert?.title ?? 'Alert'}>
      {isLoading && <Loader />}
      {error && <Callout color="red">{(error as Error).message}</Callout>}
      {data && alert && (
        <Stack gap="md">
          <Group gap="sm">
            <Badge color={SEVERITY_COLOR[alert.severity]} variant="filled">
              {alert.severity}
            </Badge>
            <Badge variant="light" color="gray">
              {KIND[alert.kind].label}
            </Badge>
            <Text size="sm" c="dimmed">
              {fmtDate(alert.at)}
            </Text>
          </Group>

          <Text size="sm">{alert.summary || KIND[alert.kind].hint}</Text>

          {/* A watchdog finding: the report the agent wrote, and how it got there */}
          {alert.kind === 'watchdog' && data.run && (
            <>
              {data.watchdog && (
                <Card withBorder padding="sm">
                  <Text size="xs" c="dimmed">
                    Watchdog
                  </Text>
                  <Text size="sm" fw={600}>
                    {data.watchdog.name}
                  </Text>
                  {data.watchdog.description && (
                    <Text size="xs" c="dimmed">
                      {data.watchdog.description}
                    </Text>
                  )}
                  <Group gap={6} mt={6}>
                    {data.watchdog.referential_ids.map((r) => (
                      <Badge key={r} size="xs" variant="outline" component={Link} to={`/r/${r}`} style={{ cursor: 'pointer' }}>
                        {r}
                      </Badge>
                    ))}
                  </Group>
                </Card>
              )}
              {(data.run as WatchdogRun).affected_items_count != null && (
                <Text size="sm">
                  <b>{fmtNumber((data.run as WatchdogRun).affected_items_count)}</b> item(s) concerned.
                </Text>
              )}
              {(data.run as WatchdogRun).error && <Callout color="red">{(data.run as WatchdogRun).error}</Callout>}
              {(data.run as WatchdogRun).details_markdown && (
                <Card withBorder padding="sm" bg="var(--mantine-color-default-hover)">
                  <Markdown text={(data.run as WatchdogRun).details_markdown!} />
                </Card>
              )}
              {!!(data.run as WatchdogRun).steps?.length && (
                <div>
                  <Text size="xs" c="dimmed" mb={4}>
                    How it looked
                  </Text>
                  <Group gap={4}>
                    {(data.run as WatchdogRun).steps.map((s, i) => (
                      <Tooltip key={i} label={`${JSON.stringify(s.arguments)} → ${s.summary}`} multiline maw={360}>
                        <Badge size="xs" variant="default" color={s.ok ? undefined : 'red'}>
                          {s.tool}
                        </Badge>
                      </Tooltip>
                    ))}
                  </Group>
                </div>
              )}
              {!!(data.run as WatchdogRun).notifications?.length && (
                <Group gap="xs">
                  {(data.run as WatchdogRun).notifications.map((n, i) => (
                    <Badge key={i} size="sm" variant="light" color={n.ok ? 'teal' : 'red'}>
                      {n.target}: {n.detail}
                    </Badge>
                  ))}
                </Group>
              )}
            </>
          )}

          {/* A failed update: the log of the run that failed */}
          {alert.kind === 'failure' && data.run && (
            <Card withBorder padding="sm">
              <Group gap="sm" mb="xs">
                <RunStatusBadge status={(data.run as Run).status} />
                <Text size="sm">{(data.run as Run).message}</Text>
              </Group>
              {!!(data.run as Run).logs?.length && (
                <div className="logs">
                  {(data.run as Run).logs!.slice(-40).map((l, i) => (
                    <div
                      key={i}
                      style={{
                        color:
                          l.level === 'error'
                            ? 'var(--mantine-color-red-6)'
                            : l.level === 'warn'
                              ? 'var(--mantine-color-orange-6)'
                              : undefined,
                      }}
                    >
                      {l.msg}
                    </div>
                  ))}
                </div>
              )}
            </Card>
          )}

          {/* Quality: the rules that are failing */}
          {alert.kind === 'quality' && data.quality && (
            <Table fz="sm" withTableBorder>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Rule</Table.Th>
                  <Table.Th>Column</Table.Th>
                  <Table.Th ta="right">Pass rate</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {((data.quality as QualityResult).checks ?? [])
                  .filter((c) => !c.ok && !c.skipped)
                  .map((c, i) => (
                    <Table.Tr key={i}>
                      <Table.Td>{c.name || c.check}</Table.Td>
                      <Table.Td>{c.column ?? '—'}</Table.Td>
                      <Table.Td ta="right" className="tabular">
                        {c.pass_pct == null ? '—' : `${c.pass_pct.toFixed(1)} %`}
                      </Table.Td>
                    </Table.Tr>
                  ))}
              </Table.Tbody>
            </Table>
          )}

          {/* Drift and stale data: the figures */}
          {!!data.figures && (
            <SimpleGrid cols={2} spacing="xs">
              {Object.entries(data.figures)
                .filter(([, v]) => v !== null && v !== undefined && typeof v !== 'object')
                .map(([k, v]) => (
                  <Text key={k} size="sm">
                    <Text span c="dimmed">
                      {k.replace(/_/g, ' ')}:
                    </Text>{' '}
                    <b>{typeof v === 'number' ? fmtNumber(v) : String(v)}</b>
                  </Text>
                ))}
            </SimpleGrid>
          )}

          <Anchor component={Link} to={alert.link} onClick={onClose} size="sm">
            Open {alert.referential_name ?? alert.watchdog_name ?? 'it'} <IconArrowRight size={14} style={{ verticalAlign: -2 }} />
          </Anchor>
        </Stack>
      )}
    </Drawer>
  );
}

export default function AlertsPage() {
  const { data, isLoading, error, refetch, isFetching } = useAlerts(200);
  const [kind, setKind] = useState('all');
  const [open, setOpen] = useState<string | null>(null);

  // Opening the page is what marks them read: the bell counts what arrived since
  useEffect(() => {
    if (data) markAlertsSeen();
  }, [data]);

  const shown = useMemo(
    () => (data?.alerts ?? []).filter((a) => kind === 'all' || a.kind === kind),
    [data, kind],
  );
  const counts = data?.counts ?? {};

  if (isLoading) return <Loader />;
  if (error) return <Callout color="red">{(error as Error).message}</Callout>;

  return (
    <Stack gap="lg">
      <Group justify="space-between" align="flex-end">
        <div>
          <Title order={2}>Alerts</Title>
          <Text c="dimmed" size="sm">
            What needs a look, newest first: the findings of your watchdogs and the referentials that drifted —
            an update that failed, data gone stale, quality rules failing, a volume that moved a lot.
          </Text>
        </div>
        <ActionIcon variant="default" size="lg" loading={isFetching} onClick={() => refetch()} aria-label="Refresh">
          <IconRefresh size={18} />
        </ActionIcon>
      </Group>

      <SimpleGrid cols={{ base: 2, sm: 4 }}>
        <StatCard label="Open alerts" value={counts.total ?? 0} icon={<IconBell size={20} />} color={counts.total ? 'orange' : 'teal'} />
        <StatCard label="Watchdog findings" value={counts.watchdog ?? 0} icon={<IconDog size={20} />} color="grape" />
        <StatCard
          label="Referentials"
          value={(counts.failure ?? 0) + (counts.stale ?? 0) + (counts.quality ?? 0) + (counts.drift ?? 0)}
          icon={<IconAlertTriangle size={20} />}
          color="yellow"
        />
        <StatCard
          label="High or critical"
          value={(counts.high ?? 0) + (counts.critical ?? 0)}
          icon={<IconAlertTriangle size={20} />}
          color="red"
        />
      </SimpleGrid>

      <SegmentedControl
        value={kind}
        onChange={setKind}
        w="fit-content"
        data={[
          { value: 'all', label: `All (${counts.total ?? 0})` },
          ...(Object.keys(KIND) as AlertKind[]).map((k) => ({
            value: k,
            label: `${KIND[k].label} (${counts[k] ?? 0})`,
          })),
        ]}
      />

      {shown.length === 0 ? (
        <EmptyState icon={<IconBell size={28} />} title="Nothing to report">
          {kind === 'all'
            ? 'No watchdog finding, and every referential you can see is up to date and passing its rules.'
            : 'No alert of this kind.'}
        </EmptyState>
      ) : (
        <Stack gap={6}>
          {shown.map((a) => (
            <AlertRow key={a.id} alert={a} onOpen={() => setOpen(a.id)} />
          ))}
        </Stack>
      )}

      <Text size="xs" c="dimmed">
        This list is built from the state of the referentials and the recorded watchdog runs: an alert
        disappears on its own once the thing behind it is fixed. Nothing is sent from here — e-mails and
        webhooks are configured per channel in the notification settings.
      </Text>

      {open && <Detail id={open} onClose={() => setOpen(null)} />}
    </Stack>
  );
}
