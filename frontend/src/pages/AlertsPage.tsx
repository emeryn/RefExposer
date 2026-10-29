import {
  ActionIcon,
  Alert as Callout,
  Anchor,
  Badge,
  Button,
  Card,
  Drawer,
  Group,
  Loader,
  SegmentedControl,
  SimpleGrid,
  Stack,
  Switch,
  Table,
  Text,
  Title,
  Tooltip,
  UnstyledButton,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import {
  IconAlertTriangle,
  IconArrowRight,
  IconBell,
  IconChartArrowsVertical,
  IconClock,
  IconDog,
  IconArrowBackUp,
  IconMail,
  IconMailOpened,
  IconRefresh,
  IconShieldCheck,
  IconTrash,
} from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api/client';
import { useAlerts } from '../api/hooks';
import type { Alert, AlertKind, QualityResult, Run, Severity, WatchdogRun } from '../api/types';
import { AgentSteps } from '../components/AgentSteps';
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

function AlertRow({
  alert,
  onOpen,
  onRead,
  onDismiss,
}: {
  alert: Alert;
  onOpen: () => void;
  onRead: (read: boolean) => void;
  onDismiss: (dismissed: boolean) => void;
}) {
  const kind = KIND[alert.kind];
  return (
    <Card withBorder padding="sm" opacity={alert.read ? 0.6 : 1}>
      <Group justify="space-between" wrap="nowrap" gap="sm">
        <UnstyledButton onClick={onOpen} style={{ minWidth: 0, flex: 1 }}>
          <Group gap="sm" wrap="nowrap" style={{ minWidth: 0 }}>
            <Badge color={SEVERITY_COLOR[alert.severity]} variant={alert.read ? 'light' : 'filled'} w={84} style={{ flexShrink: 0 }}>
              {alert.severity}
            </Badge>
            <Tooltip label={kind.hint}>
              <Badge variant="light" color="gray" leftSection={<kind.icon size={10} />} style={{ flexShrink: 0 }}>
                {kind.label}
              </Badge>
            </Tooltip>
            <div style={{ minWidth: 0 }}>
              <Text size="sm" fw={alert.read ? 400 : 600} truncate>
                {alert.title}
              </Text>
              <Text size="xs" c="dimmed" lineClamp={1}>
                {alert.referential_name ?? alert.watchdog_name ?? ''}
                {alert.summary ? ` — ${alert.summary}` : ''}
              </Text>
            </div>
          </Group>
        </UnstyledButton>
        <Group gap="xs" wrap="nowrap" style={{ flexShrink: 0 }}>
          <Text size="xs" c="dimmed">
            {fmtRelative(alert.at)}
          </Text>
          {!alert.dismissed && (
            <Tooltip label={alert.read ? 'Mark as unread' : 'Mark as read'}>
              <ActionIcon
                variant="subtle"
                color={alert.read ? 'gray' : 'blue'}
                onClick={() => onRead(!alert.read)}
                aria-label={alert.read ? 'Mark as unread' : 'Mark as read'}
              >
                {alert.read ? <IconMailOpened size={16} /> : <IconMail size={16} />}
              </ActionIcon>
            </Tooltip>
          )}
          <Tooltip
            label={
              alert.dismissed
                ? 'Put it back in the feed'
                : 'Delete it from your feed — it comes back if it happens again'
            }
          >
            <ActionIcon
              variant="subtle"
              color={alert.dismissed ? 'blue' : 'red'}
              onClick={() => onDismiss(!alert.dismissed)}
              aria-label={alert.dismissed ? 'Restore' : 'Delete'}
            >
              {alert.dismissed ? <IconArrowBackUp size={16} /> : <IconTrash size={16} />}
            </ActionIcon>
          </Tooltip>
        </Group>
      </Group>
    </Card>
  );
}

function Detail({
  id,
  onClose,
  onRead,
  onDismiss,
}: {
  id: string;
  onClose: () => void;
  onRead: (read: boolean) => void;
  onDismiss: (dismissed: boolean) => void;
}) {
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
                    How it looked — click a step to see what came back
                  </Text>
                  <AgentSteps steps={(data.run as WatchdogRun).steps} />
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

          <Group justify="space-between">
            <Anchor component={Link} to={alert.link} onClick={onClose} size="sm">
              Open {alert.referential_name ?? alert.watchdog_name ?? 'it'} <IconArrowRight size={14} style={{ verticalAlign: -2 }} />
            </Anchor>
            <Group gap="xs">
              {!alert.dismissed && (
                <Button
                  size="xs"
                  variant="default"
                  leftSection={alert.read ? <IconMail size={14} /> : <IconMailOpened size={14} />}
                  onClick={() => onRead(!alert.read)}
                >
                  {alert.read ? 'Mark as unread' : 'Mark as read'}
                </Button>
              )}
              <Button
                size="xs"
                variant="default"
                color={alert.dismissed ? 'blue' : 'red'}
                leftSection={alert.dismissed ? <IconArrowBackUp size={14} /> : <IconTrash size={14} />}
                onClick={() => {
                  onDismiss(!alert.dismissed);
                  onClose();
                }}
              >
                {alert.dismissed ? 'Put it back' : 'Delete'}
              </Button>
            </Group>
          </Group>
        </Stack>
      )}
    </Drawer>
  );
}

export default function AlertsPage() {
  const qc = useQueryClient();
  // Two lists: the feed, and what this account has deleted from it
  const [view, setView] = useState<'open' | 'deleted'>('open');
  const deletedView = view === 'deleted';
  const { data, isLoading, error, refetch, isFetching } = useAlerts(200, deletedView);
  const [kind, setKind] = useState('all');
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [open, setOpen] = useState<string | null>(null);

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['alerts'] });
    qc.invalidateQueries({ queryKey: ['alert'] });
  };
  const failed = (e: Error) => notifications.show({ title: 'Failed', message: e.message, color: 'red' });

  /** Read is per account and kept on the server: `null` means every alert of this feed. */
  const mark = useMutation({
    mutationFn: ({ ids, read }: { ids: string[] | null; read: boolean }) => api.markAlerts(ids, read),
    onSuccess: invalidate,
    onError: failed,
  });
  const read = (ids: string[] | null, value: boolean) => mark.mutate({ ids, read: value });

  /**
   * Deleting is per account too: the line leaves the feed and its counts, and comes back on its own if the
   * thing behind it happens again. `null` takes the whole list shown.
   */
  const dismiss = useMutation({
    mutationFn: ({ ids, dismissed }: { ids: string[] | null; dismissed: boolean }) => api.dismissAlerts(ids, dismissed),
    onSuccess: (answer, { ids, dismissed }) => {
      invalidate();
      const what = ids ? `${answer.changed} alert(s)` : 'Every alert shown';
      notifications.show({
        title: dismissed ? 'Deleted' : 'Put back',
        message: dismissed
          ? `${what} removed from your feed — it comes back if it happens again.`
          : `${what} back in your feed.`,
        color: dismissed ? 'orange' : 'teal',
      });
    },
    onError: failed,
  });
  const remove = (ids: string[] | null, value = true) => dismiss.mutate({ ids, dismissed: value });

  const shown = useMemo(
    () =>
      (data?.alerts ?? []).filter(
        // Everything in the deleted list is read by definition: the unread filter does not apply there
        (a) => (kind === 'all' || a.kind === kind) && (deletedView || !unreadOnly || !a.read),
      ),
    [data, kind, unreadOnly, deletedView],
  );
  const counts = data?.counts ?? {};
  const deleted = deletedView ? (data?.alerts.length ?? 0) : (counts.deleted ?? 0);
  // Only the kinds this instance reports: an administrator can hide some of them in the settings
  const kinds = data?.kinds ?? (Object.keys(KIND) as AlertKind[]);

  if (isLoading) return <Loader />;
  if (error) return <Callout color="red">{(error as Error).message}</Callout>;

  return (
    <Stack gap="lg">
      <Group justify="space-between" align="flex-end">
        <div>
          <Title order={2}>Alerts</Title>
          <Text c="dimmed" size="sm">
            {deletedView
              ? 'What you removed from your feed. Putting one back brings it into the list again.'
              : `What needs a look, newest first: the findings of your watchdogs and the referentials that drifted —
                 an update that failed, data gone stale, quality rules failing, a volume that moved a lot.`}
          </Text>
        </div>
        <Group gap="xs">
          {deletedView ? (
            <Button
              variant="default"
              leftSection={<IconArrowBackUp size={16} />}
              onClick={() => remove(null, false)}
              loading={dismiss.isPending}
              disabled={!(data?.alerts.length ?? 0)}
            >
              Put them all back
            </Button>
          ) : (
            <>
              <Button
                variant="default"
                leftSection={<IconMailOpened size={16} />}
                onClick={() => read(null, true)}
                loading={mark.isPending}
                disabled={!counts.unread}
              >
                Mark all as read
              </Button>
              <Button
                variant="default"
                color="red"
                leftSection={<IconTrash size={16} />}
                onClick={() => remove(shown.map((a) => a.id))}
                loading={dismiss.isPending}
                disabled={!shown.length}
              >
                Delete {kind === 'all' && !unreadOnly ? 'all' : 'these'}
              </Button>
            </>
          )}
          <ActionIcon variant="default" size="lg" loading={isFetching} onClick={() => refetch()} aria-label="Refresh">
            <IconRefresh size={18} />
          </ActionIcon>
        </Group>
      </Group>

      <SimpleGrid cols={{ base: 2, sm: deletedView ? 3 : 4 }}>
        <StatCard
          label={deletedView ? 'Deleted alerts' : 'Open alerts'}
          value={counts.total ?? 0}
          icon={<IconBell size={20} />}
          color={counts.total ? 'orange' : 'teal'}
        />
        {/* Everything in the deleted list is read, so that figure says nothing there */}
        {!deletedView && (
          <StatCard label="Unread" value={counts.unread ?? 0} icon={<IconMail size={20} />} color={counts.unread ? 'blue' : 'teal'} />
        )}
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

      <Group justify="space-between" wrap="wrap" gap="sm">
        <SegmentedControl
          value={kind}
          onChange={setKind}
          w="fit-content"
          data={[
            { value: 'all', label: `All (${counts.total ?? 0})` },
            ...kinds.map((k) => ({ value: k, label: `${KIND[k].label} (${counts[k] ?? 0})` })),
          ]}
        />
        <Group gap="sm">
          {!deletedView && (
            <Switch
              label="Unread only"
              checked={unreadOnly}
              onChange={(e) => setUnreadOnly(e.currentTarget.checked)}
            />
          )}
          {/* The deleted ones stay reachable: nothing is lost, it is only out of the feed */}
          <SegmentedControl
            value={view}
            onChange={(v) => setView(v as 'open' | 'deleted')}
            w="fit-content"
            data={[
              { value: 'open', label: 'Feed' },
              { value: 'deleted', label: `Deleted${deleted ? ` (${deleted})` : ''}` },
            ]}
          />
        </Group>
      </Group>

      {shown.length === 0 ? (
        <EmptyState
          icon={<IconBell size={28} />}
          title={deletedView ? 'Nothing deleted' : unreadOnly ? 'Nothing unread' : 'Nothing to report'}
        >
          {deletedView
            ? 'You have not removed any alert from your feed.'
            : unreadOnly
              ? 'Every alert you can see has been read.'
              : kind === 'all'
                ? 'No watchdog finding, and every referential you can see is up to date and passing its rules.'
                : 'No alert of this kind.'}
        </EmptyState>
      ) : (
        <Stack gap={6}>
          {shown.map((a) => (
            <AlertRow
              key={a.id}
              alert={a}
              onOpen={() => {
                setOpen(a.id);
                if (!a.read) read([a.id], true);
              }}
              onRead={(value) => read([a.id], value)}
              onDismiss={(value) => remove([a.id], value)}
            />
          ))}
        </Stack>
      )}

      <Text size="xs" c="dimmed">
        This list is built from the state of the referentials and the recorded watchdog runs: an alert
        disappears on its own once the thing behind it is fixed, and one you have read — or deleted — comes
        back if it happens again. Read and deleted are yours alone and change nothing about the referential or
        the watchdog behind the alert; what counts as an alert here is set in{' '}
        <Anchor component={Link} to="/admin/settings/notifications" size="xs">
          Settings › Notifications &amp; alerts
        </Anchor>
        . Nothing is sent from here — e-mails and webhooks are configured per channel.
      </Text>

      {open && (
        <Detail
          id={open}
          onClose={() => setOpen(null)}
          onRead={(value) => read([open], value)}
          onDismiss={(value) => remove([open], value)}
        />
      )}
    </Stack>
  );
}
