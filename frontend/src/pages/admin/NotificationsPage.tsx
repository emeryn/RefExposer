import {
  ActionIcon,
  Alert,
  Anchor,
  Badge,
  Button,
  Card,
  Checkbox,
  Code,
  Group,
  Loader,
  Modal,
  MultiSelect,
  NumberInput,
  SegmentedControl,
  Select,
  SimpleGrid,
  Stack,
  Switch,
  Table,
  TagsInput,
  Text,
  Textarea,
  TextInput,
  Title,
  Tooltip,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconBell, IconCheck, IconMail, IconPlus, IconSend, IconTrash, IconWebhook, IconX } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../../api/client';
import type { AlertKind, AlertsSettings, NotificationChannel, NotificationEvent, Severity } from '../../api/types';
import { EmptyState } from '../../components/Common';
import { SecretInput } from '../../components/SecretInput';
import { fmtDate } from '../../lib/format';

const EVENTS: { value: NotificationEvent; label: string; description: string }[] = [
  { value: 'failure', label: 'Failure', description: 'update in error, corrupted source or rejected version' },
  { value: 'recovered', label: 'Recovery', description: 'first success after a failure' },
  { value: 'published', label: 'New version', description: 'every published version' },
  { value: 'quality', label: 'Quality warning', description: 'a version published with quality rules not met' },
];

/** What each kind of the alert feed reports, said for whoever decides to show it or not. */
const ALERT_KINDS: Record<AlertKind, { label: string; description: string }> = {
  watchdog: { label: 'Watchdog findings', description: 'A scheduled investigation found something, or could not finish.' },
  failure: { label: 'Failed updates', description: 'The last update failed, was rejected, or found a corrupted source.' },
  quality: { label: 'Quality rules', description: 'The published version does not pass the quality rules of its referential.' },
  stale: { label: 'Stale data', description: 'No new version within the delay the referential expects (max_age).' },
  drift: { label: 'Volume drift', description: 'The last version changed the number of rows a lot — see the threshold below.' },
};

const SEVERITIES: { value: Severity; label: string }[] = [
  { value: 'LOW', label: 'Low — everything is reported' },
  { value: 'MEDIUM', label: 'Medium and above' },
  { value: 'HIGH', label: 'High and critical only' },
  { value: 'CRITICAL', label: 'Critical only' },
];

/**
 * What counts as an alert in this instance.
 *
 * The Alerts page is built from the state of the referentials and the recorded watchdog runs, so there is
 * nothing to acknowledge centrally — but not every instance cares about every kind. What is hidden here is
 * out of the page and out of the count of the bell, for everybody.
 */
function AlertsCard() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ['settings'], queryFn: api.settings });
  const [form, setForm] = useState<AlertsSettings | null>(null);
  const cfg = form ?? data?.alerts;
  const kinds = data?.alert_kinds ?? (Object.keys(ALERT_KINDS) as AlertKind[]);
  const save = useMutation({
    mutationFn: (body: AlertsSettings) => api.saveSettings('alerts', body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['settings'] });
      qc.invalidateQueries({ queryKey: ['alerts'] });
      notifications.show({ color: 'teal', icon: <IconCheck size={16} />, message: 'Alert settings saved' });
    },
    onError: (e: Error) => notifications.show({ color: 'red', icon: <IconX size={16} />, message: e.message }),
  });
  if (!cfg) return null;
  const set = <K extends keyof AlertsSettings>(key: K, value: AlertsSettings[K]) =>
    setForm({ ...cfg, [key]: value });
  const shown = kinds.filter((k) => !cfg.hidden_kinds.includes(k));

  return (
    <Card withBorder padding="lg">
      <Stack gap="md">
        <div>
          <Title order={4}>What counts as an alert</Title>
          <Text size="sm" c="dimmed">
            The <Anchor component={Link} to="/alerts" size="sm">Alerts</Anchor> page gathers what needs a look.
            Leave out the kinds that do not apply here: a kind unticked is out of the page and out of the badge
            of the bell, for everybody. Nothing stops being watched, and the notification channels below keep
            their own configuration.
          </Text>
        </div>
        <Checkbox.Group
          value={shown}
          onChange={(v) => set('hidden_kinds', kinds.filter((k) => !v.includes(k)))}
        >
          <Stack gap="xs">
            {kinds.map((k) => (
              <Checkbox key={k} value={k} label={ALERT_KINDS[k].label} description={ALERT_KINDS[k].description} />
            ))}
          </Stack>
        </Checkbox.Group>
        {!shown.length && (
          <Alert color="orange" variant="light">
            <Text size="sm">Nothing is reported at all: the Alerts page stays empty for everyone.</Text>
          </Alert>
        )}
        <SimpleGrid cols={{ base: 1, sm: 3 }}>
          <Select
            label="Report from this severity upwards"
            description="A watchdog finding keeps the severity the model gave it; the other kinds have a fixed one."
            data={SEVERITIES}
            value={cfg.min_severity}
            onChange={(v) => set('min_severity', (v ?? 'LOW') as Severity)}
            allowDeselect={false}
          />
          <NumberInput
            label="A drift is a volume change of at least"
            description="In percent, up or down, between the published version and the one before it."
            suffix=" %"
            min={1}
            max={1000}
            value={Math.round(cfg.drift_ratio * 100)}
            onChange={(v) => set('drift_ratio', Math.max(1, Number(v) || 20) / 100)}
          />
          <NumberInput
            label="…on a referential of at least"
            description="Rows before the change. Below that a percentage means nothing."
            suffix=" rows"
            min={1}
            value={cfg.drift_min_rows}
            onChange={(v) => set('drift_min_rows', Math.max(1, Number(v) || 100))}
          />
        </SimpleGrid>
        <Group justify="flex-end">
          <Button onClick={() => save.mutate(cfg)} loading={save.isPending} disabled={!form}>
            Save
          </Button>
        </Group>
      </Stack>
    </Card>
  );
}

const TEMPLATES = [
  { value: '', label: 'Generic JSON (event, text, referential, run…)', template: '' },
  { value: 'text', label: 'Slack, Mattermost, Rocket.Chat, Google Chat, Teams (classic webhook)', template: '{"text": "{{text}}"}' },
  { value: 'discord', label: 'Discord', template: '{"content": "{{text}}"}' },
  {
    value: 'card',
    label: 'Detailed message',
    template: '{\n  "title": "{{referential.name}}: {{event}}",\n  "text": "{{text}}",\n  "status": "{{run.status}}",\n  "message": "{{run.message}}",\n  "link": "{{referential.url}}"\n}',
  },
];

const EMPTY: Omit<NotificationChannel, 'id'> = {
  name: '',
  type: 'email',
  enabled: true,
  events: ['failure', 'recovered'],
  repeat_failures: false,
  referentials: [],
  categories: [],
  recipients: [],
  notify_owner: false,
  url: '',
  headers: {},
  signing_secret: '',
  template: '',
};

function ChannelForm({ channel, onDone }: { channel?: NotificationChannel; onDone: () => void }) {
  const qc = useQueryClient();
  const { data: refs } = useQuery({ queryKey: ['referentials'], queryFn: api.referentials });
  const { data: meta } = useQuery({ queryKey: ['notifications'], queryFn: api.notifications });
  const { data: secrets } = useQuery({ queryKey: ['secrets'], queryFn: api.secrets });
  const [c, setC] = useState<Omit<NotificationChannel, 'id'>>(channel ?? EMPTY);
  const [headers, setHeaders] = useState(Object.entries(channel?.headers ?? {}).map(([k, v]) => ({ k, v })));
  const set = <K extends keyof NotificationChannel>(k: K, v: NotificationChannel[K]) => setC((x) => ({ ...x, [k]: v }));
  const save = useMutation({
    mutationFn: () => {
      const body = { ...c, headers: Object.fromEntries(headers.filter((h) => h.k.trim()).map((h) => [h.k.trim(), h.v])) };
      return channel ? api.updateNotification(channel.id, body) : api.createNotification(body);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['notifications'] });
      qc.invalidateQueries({ queryKey: ['secrets'] });
      notifications.show({ message: channel ? 'Channel updated' : 'Channel created', color: 'teal' });
      onDone();
    },
    onError: (e: Error) => notifications.show({ title: 'Not saved', message: e.message, color: 'red', autoClose: 10000 }),
  });
  return (
    <Stack>
      <Group grow align="flex-end">
        <TextInput label="Name" placeholder="SOC team" value={c.name} onChange={(e) => set('name', e.currentTarget.value)} required />
        <SegmentedControl
          value={c.type}
          onChange={(v) => set('type', v as NotificationChannel['type'])}
          data={[
            { value: 'email', label: 'E-mail' },
            { value: 'webhook', label: 'Webhook' },
          ]}
        />
      </Group>
      <div>
        <Text size="sm" fw={500} mb={4}>
          Events
        </Text>
        <Stack gap={4}>
          {EVENTS.map((e) => (
            <Checkbox
              key={e.value}
              label={
                <>
                  {e.label} <Text span size="xs" c="dimmed">— {e.description}</Text>
                </>
              }
              checked={c.events.includes(e.value)}
              onChange={(ev) => set('events', ev.currentTarget.checked ? [...c.events, e.value] : c.events.filter((x) => x !== e.value))}
            />
          ))}
        </Stack>
      </div>
      <Switch
        label="Every failed update"
        description="Off: once, when the referential starts failing (then at its recovery)"
        checked={c.repeat_failures}
        onChange={(e) => set('repeat_failures', e.currentTarget.checked)}
        disabled={!c.events.includes('failure')}
      />
      <SimpleGrid cols={{ base: 1, sm: 2 }}>
        <MultiSelect
          label="Referentials"
          description="Every referential when empty"
          data={(refs ?? []).map((r) => ({ value: r.id, label: `${r.name} (${r.id})` }))}
          value={c.referentials}
          onChange={(v) => set('referentials', v)}
          searchable
          clearable
        />
        <MultiSelect
          label="Categories"
          description="Or the referentials of these categories"
          data={meta?.categories ?? []}
          value={c.categories}
          onChange={(v) => set('categories', v)}
          searchable
          clearable
        />
      </SimpleGrid>
      {c.type === 'email' ? (
        <>
          {meta && !meta.smtp_configured && (
            <Alert color="orange" p="xs">
              <Text size="sm">
                No SMTP server yet: configure it in <Anchor component={Link} to="/admin/settings/smtp">Settings › E-mail</Anchor>.
              </Text>
            </Alert>
          )}
          <TagsInput
            label="Recipients"
            placeholder="soc@example.com"
            value={c.recipients}
            onChange={(v) => set('recipients', v)}
            splitChars={[',', ' ', ';']}
          />
          <Checkbox
            label="Also the owner of the referential (when its 'owner' field is an e-mail address)"
            checked={c.notify_owner}
            onChange={(e) => set('notify_owner', e.currentTarget.checked)}
          />
        </>
      ) : (
        <>
          <TextInput label="URL" placeholder="https://hooks.example.com/services/…" value={c.url} onChange={(e) => set('url', e.currentTarget.value)} required />
          <Select
            label="Body"
            data={TEMPLATES.map(({ value, label }) => ({ value, label }))}
            value={TEMPLATES.find((t) => t.template === c.template)?.value ?? null}
            placeholder="Custom template"
            onChange={(v) => set('template', TEMPLATES.find((t) => t.value === v)?.template ?? '')}
          />
          <Textarea
            label="Template (JSON)"
            description={
              <>
                Placeholders between quotes: <Code>{'{{text}}'}</Code>, <Code>{'{{event}}'}</Code>, <Code>{'{{referential.id}}'}</Code>,{' '}
                <Code>{'{{referential.name}}'}</Code>, <Code>{'{{referential.url}}'}</Code>, <Code>{'{{run.status}}'}</Code>,{' '}
                <Code>{'{{run.message}}'}</Code>, <Code>{'{{previous_status}}'}</Code>… Empty: the generic JSON document.
              </>
            }
            autosize
            minRows={2}
            styles={{ input: { fontFamily: 'var(--mantine-font-family-monospace)' } }}
            value={c.template}
            onChange={(e) => set('template', e.currentTarget.value)}
          />
          <Text size="sm" fw={500}>
            Headers
          </Text>
          {headers.map((h, i) => (
            <Group key={i} gap="xs" align="flex-start">
              <TextInput size="xs" placeholder="Authorization" value={h.k} onChange={(e) => { const v = e.currentTarget.value; setHeaders((s) => s.map((x, j) => (j === i ? { ...x, k: v } : x))); }} w={180} />
              <SecretInput size="xs" placeholder={'Bearer ${secret:hook-token}'} value={h.v} onChange={(v) => setHeaders((s) => s.map((x, j) => (j === i ? { ...x, v } : x)))} style={{ flex: 1 }} />
              <ActionIcon size="sm" variant="subtle" color="red" onClick={() => setHeaders((s) => s.filter((_, j) => j !== i))}>
                <IconTrash size={14} />
              </ActionIcon>
            </Group>
          ))}
          <Button size="compact-xs" variant="subtle" w="fit-content" onClick={() => setHeaders((s) => [...s, { k: '', v: '' }])}>
            + Header
          </Button>
          <Select
            label="Signature (HMAC-SHA256)"
            description="The body is signed with this secret, in X-RefExposer-Signature: sha256=<hex>"
            data={(secrets ?? []).map((s) => ({ value: s.reference, label: s.name }))}
            value={c.signing_secret || null}
            onChange={(v) => set('signing_secret', v ?? '')}
            placeholder="No signature"
            clearable
          />
        </>
      )}
      <Switch label="Enabled" checked={c.enabled} onChange={(e) => set('enabled', e.currentTarget.checked)} />
      <Button onClick={() => save.mutate()} loading={save.isPending} disabled={!c.name.trim() || !c.events.length}>
        {channel ? 'Save' : 'Create the channel'}
      </Button>
    </Stack>
  );
}

export default function NotificationsPage() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ['notifications'], queryFn: api.notifications });
  const { data: deliveries } = useQuery({ queryKey: ['notification-deliveries'], queryFn: () => api.notificationDeliveries(100), refetchInterval: 15_000 });
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<NotificationChannel | null>(null);
  const test = useMutation({
    mutationFn: api.testNotification,
    onSuccess: (d) => {
      qc.invalidateQueries({ queryKey: ['notification-deliveries'] });
      notifications.show({ title: d.ok ? 'Test sent' : 'Test failed', message: d.detail, color: d.ok ? 'teal' : 'red', autoClose: d.ok ? 4000 : 10000 });
    },
    onError: (e: Error) => notifications.show({ title: 'Test failed', message: e.message, color: 'red' }),
  });
  const remove = useMutation({
    mutationFn: api.deleteNotification,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['notifications'] }),
  });

  return (
    <Stack gap="lg">
      <AlertsCard />

      <Group justify="space-between" align="flex-end">
        <div>
          <Title order={4}>Notification channels</Title>
          <Text c="dimmed" size="sm">
            E-mails and webhooks when a referential fails, recovers or publishes a new version. A failing referential is notified once,
            when it starts failing.
          </Text>
        </div>
        <Button leftSection={<IconPlus size={16} />} onClick={() => setCreating(true)}>
          New channel
        </Button>
      </Group>
      {isLoading ? (
        <Loader />
      ) : !data?.channels.length ? (
        <EmptyState icon={<IconBell size={28} />} title="No channel">
          For example an e-mail to the team in charge of the sources, and a webhook to your chat or incident tool.
        </EmptyState>
      ) : (
        <SimpleGrid cols={{ base: 1, md: 2, xl: 3 }}>
          {data.channels.map((c) => (
            <Card key={c.id} padding="lg" withBorder style={{ opacity: c.enabled ? 1 : 0.6 }}>
              <Group justify="space-between" mb={6} wrap="nowrap">
                <Group gap="xs" wrap="nowrap">
                  {c.type === 'email' ? <IconMail size={18} /> : <IconWebhook size={18} />}
                  <Text fw={700}>{c.name}</Text>
                  {!c.enabled && (
                    <Badge size="xs" color="gray">
                      disabled
                    </Badge>
                  )}
                </Group>
                <Group gap={4} wrap="nowrap">
                  <Tooltip label="Send a test now">
                    <ActionIcon variant="subtle" onClick={() => test.mutate(c.id)} loading={test.isPending && test.variables === c.id}>
                      <IconSend size={16} />
                    </ActionIcon>
                  </Tooltip>
                  <ActionIcon
                    variant="subtle"
                    color="red"
                    onClick={() => window.confirm(`Delete the channel '${c.name}'?`) && remove.mutate(c.id)}
                  >
                    <IconTrash size={16} />
                  </ActionIcon>
                </Group>
              </Group>
              <Text size="sm" c="dimmed" lineClamp={2} style={{ wordBreak: 'break-all' }}>
                {c.type === 'email' ? [...c.recipients, c.notify_owner ? 'owner' : null].filter(Boolean).join(', ') : c.url}
              </Text>
              <Group gap={4} mt="sm">
                {c.events.map((e) => (
                  <Badge key={e} size="sm" variant="light" color={e === 'failure' ? 'red' : e === 'recovered' ? 'teal' : e === 'quality' ? 'orange' : 'indigo'}>
                    {EVENTS.find((x) => x.value === e)?.label}
                  </Badge>
                ))}
                <Badge size="sm" variant="outline" color="gray" tt="none">
                  {c.referentials.length || c.categories.length
                    ? [...c.referentials, ...c.categories.map((x) => `category ${x}`)].join(', ')
                    : 'all referentials'}
                </Badge>
              </Group>
              <Button size="compact-sm" variant="light" mt="md" onClick={() => setEditing(c)}>
                Edit
              </Button>
            </Card>
          ))}
        </SimpleGrid>
      )}

      <div>
        <Title order={4} mb="xs">
          Last deliveries
        </Title>
        {!deliveries?.length ? (
          <Text size="sm" c="dimmed">
            Nothing sent yet.
          </Text>
        ) : (
          <Table striped fz="sm" verticalSpacing={4}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Date</Table.Th>
                <Table.Th>Channel</Table.Th>
                <Table.Th>Event</Table.Th>
                <Table.Th>Referential</Table.Th>
                <Table.Th>Result</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {deliveries.map((d, i) => (
                <Table.Tr key={i}>
                  <Table.Td>{fmtDate(d.at)}</Table.Td>
                  <Table.Td>{d.channel}</Table.Td>
                  <Table.Td>{d.event}</Table.Td>
                  <Table.Td>
                    <Anchor component={Link} to={`/r/${d.referential}`} size="sm">
                      {d.referential}
                    </Anchor>
                  </Table.Td>
                  <Table.Td>
                    <Badge size="sm" color={d.ok ? 'teal' : 'red'} variant="light" mr="xs">
                      {d.ok ? 'sent' : 'failed'}
                    </Badge>
                    <Text span size="xs" c="dimmed">
                      {d.detail}
                      {d.attempts > 1 ? ` (${d.attempts} attempts)` : ''}
                    </Text>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
      </div>

      <Modal opened={creating} onClose={() => setCreating(false)} title={<Text fw={700}>New notification channel</Text>} size="lg">
        <ChannelForm onDone={() => setCreating(false)} />
      </Modal>
      <Modal opened={!!editing} onClose={() => setEditing(null)} title={<Text fw={700}>{editing?.name}</Text>} size="lg">
        {editing && <ChannelForm key={editing.id} channel={editing} onDone={() => setEditing(null)} />}
      </Modal>
    </Stack>
  );
}
