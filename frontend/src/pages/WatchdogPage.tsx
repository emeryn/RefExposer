import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Card,
  Checkbox,
  Group,
  Loader,
  Modal,
  MultiSelect,
  Select,
  SimpleGrid,
  Stack,
  Switch,
  TagsInput,
  Text,
  Textarea,
  TextInput,
  Title,
  Tooltip,
  UnstyledButton,
} from '@mantine/core';
import {
  IconAlertTriangle,
  IconBolt,
  IconCheck,
  IconCircleOff,
  IconChevronDown,
  IconChevronRight,
  IconClockHour4,
  IconDog,
  IconMail,
  IconPlayerPlay,
  IconPlus,
  IconRefresh,
  IconTrash,
} from '@tabler/icons-react';
import { notifications } from '@mantine/notifications';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api/client';
import { useAssistant, useReferentials } from '../api/hooks';
import { useAuth } from '../auth/AuthContext';
import type { Severity, WatchdogRun, WatchdogTask, WatchdogTemplate } from '../api/types';
import { AgentSteps } from '../components/AgentSteps';
import { EmptyState } from '../components/Common';
import { Markdown } from '../components/Markdown';
import { describeCron, fmtDate, fmtNumber, fmtRelative } from '../lib/format';

const SEVERITIES: { value: Severity; label: string; color: string }[] = [
  { value: 'LOW', label: 'Low', color: 'gray' },
  { value: 'MEDIUM', label: 'Medium', color: 'yellow' },
  { value: 'HIGH', label: 'High', color: 'orange' },
  { value: 'CRITICAL', label: 'Critical', color: 'red' },
];

const SCHEDULES = [
  { value: '0 7 * * *', label: 'Every day at 7:00' },
  { value: '0 */4 * * *', label: 'Every 4 hours' },
  { value: '0 7 * * 1', label: 'Every Monday at 7:00' },
  { value: '0 7 1 * *', label: 'On the 1st of the month at 7:00' },
  { value: 'custom', label: 'Custom (cron)…' },
];

type Draft = {
  name: string;
  description: string;
  referential_ids: string[];
  all_referentials: boolean;
  only_new_data: boolean;
  trigger_type: 'cron' | 'on_sync';
  schedule: string;
  prompt: string;
  template: string | null;
  notify_emails: string[];
  notify_channels: string[];
  min_severity: Severity;
  enabled: boolean;
  user_id: number | null;
};

const EMPTY: Draft = {
  name: '',
  description: '',
  referential_ids: [],
  all_referentials: false,
  only_new_data: false,
  trigger_type: 'cron',
  schedule: '0 7 * * *',
  prompt: '',
  template: null,
  notify_emails: [],
  notify_channels: [],
  min_severity: 'LOW',
  enabled: true,
  user_id: null,
};

function severityColor(severity: Severity | null | undefined) {
  return SEVERITIES.find((s) => s.value === severity)?.color ?? 'gray';
}

function RunBadge({ run }: { run: WatchdogRun }) {
  if (run.running)
    return (
      <Badge color="blue" variant="light" leftSection={<Loader size={8} color="blue" type="dots" />}>
        investigating…
      </Badge>
    );
  // No end date and not running any more: the service stopped in the middle of it, which is not a failure
  if (!run.finished_at)
    return (
      <Tooltip label="The service stopped while this investigation was running. Start it again.">
        <Badge color="gray" variant="light" leftSection={<IconCircleOff size={10} />}>
          interrupted
        </Badge>
      </Tooltip>
    );
  if (run.status === 'error')
    return (
      <Tooltip label={run.error ?? 'Failed'} multiline maw={360}>
        <Badge color="red" variant="light" leftSection={<IconAlertTriangle size={10} />}>
          failed
        </Badge>
      </Tooltip>
    );
  if (!run.finding_detected)
    return (
      <Badge color="teal" variant="light" leftSection={<IconCheck size={10} />}>
        nothing to report
      </Badge>
    );
  return (
    <Badge color={severityColor(run.severity)} variant="filled">
      {run.severity} · finding
    </Badge>
  );
}

function RunCard({ run, taskName }: { run: WatchdogRun; taskName?: string }) {
  const [open, setOpen] = useState(false);
  // A running investigation is unfolded by itself: its steps are the point of looking at the page
  const show = open || run.running;
  const detail = useQuery({
    queryKey: ['watchdog-run', run.task_id, run.id],
    queryFn: () => api.watchdogRuns(run.task_id, 50),
    enabled: show && (run.details_markdown === undefined || run.running),
    // While it works, the trace is written as it goes: follow it
    refetchInterval: run.running ? 3000 : false,
  });
  const full = detail.data?.find((r) => r.id === run.id) ?? run;
  return (
    <Card withBorder padding="sm">
      <UnstyledButton onClick={() => setOpen((v) => !v)} w="100%">
        <Group justify="space-between" wrap="nowrap">
          <Group gap="sm" wrap="nowrap" style={{ minWidth: 0 }}>
            {show ? <IconChevronDown size={16} /> : <IconChevronRight size={16} />}
            <RunBadge run={run} />
            <Text size="sm" truncate>
              {taskName ?? run.name ? <b>{taskName ?? run.name}: </b> : null}
              {run.summary || run.error || (run.running ? 'in progress…' : '—')}
            </Text>
          </Group>
          <Group gap="sm" wrap="nowrap" style={{ flexShrink: 0 }}>
            {run.affected_items_count ? (
              <Badge size="sm" variant="default">
                {fmtNumber(run.affected_items_count)} items
              </Badge>
            ) : null}
            <Text size="xs" c="dimmed">
              {run.trigger.startsWith('on_sync') ? `after ${run.trigger.slice(8)}` : run.trigger} ·{' '}
              {fmtRelative(run.started_at)}
            </Text>
          </Group>
        </Group>
      </UnstyledButton>
      {show && (
        <Stack gap="xs" mt="sm" pl={28}>
          {detail.isLoading && <Loader size="sm" />}
          {full?.error && <Alert color="red" variant="light" p="xs">{full.error}</Alert>}
          {full?.details_markdown ? (
            <Card withBorder padding="sm" bg="var(--mantine-color-default-hover)">
              <Markdown text={full.details_markdown} />
            </Card>
          ) : (
            !detail.isLoading &&
            !run.running && (
              <Text size="sm" c="dimmed">
                No detail was reported.
              </Text>
            )
          )}
          {!!full?.notifications.length && (
            <Group gap="xs">
              {full.notifications.map((n, i) => (
                <Badge key={i} size="sm" variant="light" color={n.ok ? 'teal' : 'red'} leftSection={<IconMail size={10} />}>
                  {n.target}: {n.detail}
                </Badge>
              ))}
            </Group>
          )}
          {!!full?.steps.length && (
            <div>
              <Text size="xs" c="dimmed" mb={4}>
                What it did ({full.steps.length} step{full.steps.length > 1 ? 's' : ''}) — click one to see what
                came back
              </Text>
              <AgentSteps steps={full.steps} />
            </div>
          )}
          {run.running && !full?.steps.length && (
            <Group gap="xs">
              <Loader size="xs" type="dots" />
              <Text size="xs" c="dimmed">
                reading the referentials in its scope…
              </Text>
            </Group>
          )}
          <Text size="xs" c="dimmed">
            Started {fmtDate(run.started_at)}
            {run.finished_at ? ` · finished ${fmtDate(run.finished_at)}` : ''}
          </Text>
        </Stack>
      )}
    </Card>
  );
}

function TaskCard({ task, onEdit }: { task: WatchdogTask; onEdit: () => void }) {
  const qc = useQueryClient();
  const [showRuns, setShowRuns] = useState(false);
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['watchdogs'] });
    qc.invalidateQueries({ queryKey: ['watchdog-runs'] });
  };
  const start = useMutation({
    mutationFn: () => api.runWatchdog(task.id),
    onSuccess: () => {
      notifications.show({ message: `“${task.name}” is investigating`, color: 'blue' });
      invalidate();
    },
    onError: (e: Error) => notifications.show({ title: 'Failed', message: e.message, color: 'red' }),
  });
  const toggle = useMutation({
    mutationFn: (enabled: boolean) => api.updateWatchdog(task.id, { enabled }),
    onSuccess: invalidate,
    onError: (e: Error) => notifications.show({ title: 'Failed', message: e.message, color: 'red' }),
  });
  const remove = useMutation({
    mutationFn: () => api.deleteWatchdog(task.id),
    onSuccess: () => {
      notifications.show({ message: `“${task.name}” deleted`, color: 'teal' });
      invalidate();
    },
    onError: (e: Error) => notifications.show({ title: 'Failed', message: e.message, color: 'red' }),
  });
  // While it is investigating, its executions are shown without being asked for: that is the status
  const openRuns = showRuns || task.running;
  const runs = useQuery({
    queryKey: ['watchdog-runs', task.id],
    queryFn: () => api.watchdogRuns(task.id),
    enabled: openRuns,
    refetchInterval: task.running ? 3000 : false,
  });

  return (
    <Card padding="lg">
      <Stack gap="sm">
        <Group justify="space-between" align="flex-start" wrap="nowrap">
          <Stack gap={4} style={{ minWidth: 0 }}>
            <Group gap="sm">
              <Title order={5}>{task.name}</Title>
              {task.running && <Badge color="blue" variant="light">running…</Badge>}
              {!task.enabled && <Badge color="gray" variant="light">paused</Badge>}
            </Group>
            {task.description && (
              <Text size="sm" c="dimmed">
                {task.description}
              </Text>
            )}
            <Group gap={6}>
              {task.all_referentials ? (
                <Tooltip label="Everything the account it runs as may read, as its rights change">
                  <Badge size="xs" variant="light" color="indigo">
                    every referential
                  </Badge>
                </Tooltip>
              ) : (
                task.referential_ids.map((id) => (
                  <Badge key={id} size="xs" variant="outline" component={Link} to={`/r/${id}`} style={{ cursor: 'pointer' }}>
                    {id}
                  </Badge>
                ))
              )}
              {task.only_new_data && (
                <Tooltip label="It is asked about the rows the latest versions added or changed, not the whole data">
                  <Badge size="xs" variant="light" color="teal">
                    new data only
                  </Badge>
                </Tooltip>
              )}
            </Group>
          </Stack>
          <Group gap="xs" wrap="nowrap">
            <Switch
              size="sm"
              checked={task.enabled}
              onChange={(e) => toggle.mutate(e.currentTarget.checked)}
              aria-label="Enabled"
            />
            <Tooltip label="Run now">
              <ActionIcon variant="light" loading={start.isPending || task.running} onClick={() => start.mutate()} aria-label="Run now">
                <IconPlayerPlay size={16} />
              </ActionIcon>
            </Tooltip>
            <Button variant="default" size="xs" onClick={onEdit}>
              Edit
            </Button>
            <Tooltip label="Delete">
              <ActionIcon variant="subtle" color="red" loading={remove.isPending} onClick={() => remove.mutate()} aria-label="Delete">
                <IconTrash size={16} />
              </ActionIcon>
            </Tooltip>
          </Group>
        </Group>

        <SimpleGrid cols={{ base: 1, sm: 3 }} spacing="sm">
          <div>
            <Text size="xs" c="dimmed">
              Runs
            </Text>
            <Text size="sm" fw={600}>
              {task.trigger_type === 'cron' ? describeCron(task.schedule) : 'After each synchronization in scope'}
            </Text>
            {task.next_run_at && task.trigger_type === 'cron' && (
              <Text size="xs" c="dimmed">
                next {fmtRelative(task.next_run_at)}
              </Text>
            )}
          </div>
          <div>
            <Text size="xs" c="dimmed">
              Alerts
            </Text>
            <Text size="sm" fw={600}>
              {task.notify_emails.length + task.notify_channels.length === 0
                ? 'Recorded only'
                : `${task.notify_emails.length + task.notify_channels.length} recipient(s)`}
            </Text>
            <Text size="xs" c="dimmed">
              from {task.min_severity} upwards, only on a finding
            </Text>
          </div>
          <div>
            <Text size="xs" c="dimmed">
              Last run
            </Text>
            {task.last_run ? (
              <Group gap={6}>
                <RunBadge run={task.last_run} />
                <Text size="xs" c="dimmed">
                  {fmtRelative(task.last_run.started_at)}
                </Text>
              </Group>
            ) : (
              <Text size="sm" c="dimmed">
                Never
              </Text>
            )}
          </div>
        </SimpleGrid>

        <Group gap="xs">
          <Button variant="subtle" size="xs" onClick={() => setShowRuns((v) => !v)} disabled={task.running}>
            {openRuns ? 'Hide' : 'Show'} executions
          </Button>
          <Text size="xs" c="dimmed">
            Runs as {task.username}
          </Text>
        </Group>
        {openRuns && (
          <Stack gap={6}>
            {runs.isLoading && <Loader size="sm" />}
            {runs.data?.length === 0 && (
              <Text size="sm" c="dimmed">
                No execution yet.
              </Text>
            )}
            {runs.data?.map((run) => (
              <RunCard key={run.id} run={run} />
            ))}
          </Stack>
        )}
      </Stack>
    </Card>
  );
}

function Editor({
  task,
  templates,
  onClose,
}: {
  task: WatchdogTask | null;
  templates: WatchdogTemplate[];
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const { me } = useAuth();
  const { data: refs } = useReferentials();
  const { data: channels } = useQuery({ queryKey: ['notifications'], queryFn: api.notifications });
  const [draft, setDraft] = useState<Draft>(
    task
      ? {
          name: task.name,
          description: task.description,
          referential_ids: task.referential_ids,
          all_referentials: task.all_referentials,
          only_new_data: task.only_new_data,
          trigger_type: task.trigger_type,
          schedule: task.schedule ?? '0 7 * * *',
          prompt: task.prompt,
          template: task.template,
          notify_emails: task.notify_emails,
          notify_channels: task.notify_channels,
          min_severity: task.min_severity,
          enabled: task.enabled,
          user_id: task.user_id,
        }
      : EMPTY,
  );
  const [customCron, setCustomCron] = useState(
    !!task?.schedule && !SCHEDULES.some((s) => s.value === task.schedule),
  );
  const set = <K extends keyof Draft>(key: K, value: Draft[K]) => setDraft((d) => ({ ...d, [key]: value }));

  const save = useMutation({
    mutationFn: () => {
      const body = {
        ...draft,
        // Watching everything is a scope of its own: the list is cleared so the two never disagree
        referential_ids: draft.all_referentials ? [] : draft.referential_ids,
        schedule: draft.trigger_type === 'cron' ? draft.schedule : null,
        user_id: me?.is_admin ? draft.user_id : null,
      };
      return task ? api.updateWatchdog(task.id, body) : api.createWatchdog(body);
    },
    onSuccess: () => {
      notifications.show({ message: task ? 'Watchdog saved' : 'Watchdog created', color: 'teal' });
      qc.invalidateQueries({ queryKey: ['watchdogs'] });
      onClose();
    },
    onError: (e: Error) => notifications.show({ title: 'Failed', message: e.message, color: 'red' }),
  });

  const apply = (t: WatchdogTemplate) =>
    setDraft((d) => ({
      ...d,
      template: t.id,
      prompt: t.prompt || d.prompt,
      name: d.name || (t.id === 'custom' ? '' : t.label),
      description: d.description || (t.id === 'custom' ? '' : t.description),
    }));

  const ready =
    draft.name.trim() && (draft.all_referentials || draft.referential_ids.length > 0) && draft.prompt.trim();
  return (
    <Modal opened onClose={onClose} title={task ? `Edit “${task.name}”` : 'New watchdog'} size="xl">
      <Stack gap="md">
        {!task && (
          <div>
            <Text size="sm" fw={500} mb={4}>
              Start from an example
            </Text>
            <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="xs">
              {templates.map((t) => (
                <Card
                  key={t.id}
                  withBorder
                  padding="xs"
                  onClick={() => apply(t)}
                  style={{ cursor: 'pointer', borderColor: draft.template === t.id ? 'var(--mantine-color-blue-5)' : undefined }}
                >
                  <Text size="sm" fw={600}>
                    {t.label}
                  </Text>
                  <Text size="xs" c="dimmed" lineClamp={3}>
                    {t.description}
                  </Text>
                </Card>
              ))}
            </SimpleGrid>
          </div>
        )}

        <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="sm">
          <TextInput label="Name" required value={draft.name} onChange={(e) => set('name', e.currentTarget.value)} />
          <TextInput
            label="Description"
            value={draft.description}
            onChange={(e) => set('description', e.currentTarget.value)}
          />
        </SimpleGrid>

        <Card withBorder padding="sm">
          <Stack gap="sm">
            <Switch
              checked={draft.all_referentials}
              onChange={(e) => set('all_referentials', e.currentTarget.checked)}
              label="It may look at every referential"
              description="Instead of a list chosen here. Its scope is then exactly what the account it runs as may read, and follows those rights as they change — confidential referentials stay out unless the assistant settings allow them."
            />
            {!draft.all_referentials && (
              <MultiSelect
                label="Referentials it may read"
                description="The only ones the investigation can reach. Nothing else exists for it, whatever the model asks for."
                required
                searchable
                data={(refs ?? []).map((r) => ({ value: r.id, label: `${r.name} (${r.id})` }))}
                value={draft.referential_ids}
                onChange={(v) => set('referential_ids', v)}
              />
            )}
            <Switch
              checked={draft.only_new_data}
              onChange={(e) => set('only_new_data', e.currentTarget.checked)}
              label="Only investigate what is new"
              description="The question carries the published version of each referential in scope and what its last version changed, and asks for a finding about the rows added or changed since the previous investigation — not about data that was already there. Pairs well with “after a synchronization”."
            />
          </Stack>
        </Card>

        <Textarea
          label="What to investigate"
          description="The instruction given to the agent. It must return a structured verdict, which the application asks for on its own."
          required
          autosize
          minRows={4}
          maxRows={12}
          value={draft.prompt}
          onChange={(e) => set('prompt', e.currentTarget.value)}
        />

        <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="sm">
          <Select
            label="When it runs"
            data={[
              { value: 'cron', label: 'On a schedule' },
              { value: 'on_sync', label: 'After a synchronization of one of its referentials' },
            ]}
            value={draft.trigger_type}
            onChange={(v) => set('trigger_type', (v ?? 'cron') as 'cron' | 'on_sync')}
            allowDeselect={false}
          />
          {draft.trigger_type === 'cron' && (
            <Select
              label="Schedule"
              data={SCHEDULES}
              value={customCron ? 'custom' : draft.schedule}
              onChange={(v) => {
                if (v === 'custom') setCustomCron(true);
                else {
                  setCustomCron(false);
                  set('schedule', v ?? '0 7 * * *');
                }
              }}
              allowDeselect={false}
            />
          )}
        </SimpleGrid>
        {draft.trigger_type === 'cron' && customCron && (
          <TextInput
            label="Cron expression"
            description={draft.schedule ? describeCron(draft.schedule) : 'minute hour day month day-of-week'}
            placeholder="30 */4 * * *"
            value={draft.schedule}
            onChange={(e) => set('schedule', e.currentTarget.value)}
          />
        )}

        <Card withBorder padding="sm">
          <Stack gap="sm">
            <Text size="sm" fw={600}>
              Who is told — and only when something was found
            </Text>
            <TagsInput
              label="E-mail addresses"
              description="Nothing is sent when the investigation finds nothing."
              placeholder="soc@example.com"
              value={draft.notify_emails}
              onChange={(v) => set('notify_emails', v)}
            />
            <MultiSelect
              label="Notification channels"
              description="Channels configured in Settings › Notifications (webhooks, or their e-mail recipients)."
              data={(channels?.channels ?? []).map((c) => ({ value: c.id, label: `${c.name} (${c.type})` }))}
              value={draft.notify_channels}
              onChange={(v) => set('notify_channels', v)}
              placeholder={channels?.channels.length ? 'None' : 'No channel configured'}
            />
            <Select
              label="Alert from this severity upwards"
              description="A finding below it is recorded but nobody is woken up."
              data={SEVERITIES.map((s) => ({ value: s.value, label: s.label }))}
              value={draft.min_severity}
              onChange={(v) => set('min_severity', (v ?? 'LOW') as Severity)}
              allowDeselect={false}
            />
          </Stack>
        </Card>

        <Group justify="space-between">
          <Checkbox
            label="Enabled"
            checked={draft.enabled}
            onChange={(e) => set('enabled', e.currentTarget.checked)}
          />
          <Group>
            <Button variant="default" onClick={onClose}>
              Cancel
            </Button>
            <Button loading={save.isPending} disabled={!ready} onClick={() => save.mutate()}>
              {task ? 'Save' : 'Create'}
            </Button>
          </Group>
        </Group>
      </Stack>
    </Modal>
  );
}

export default function WatchdogPage() {
  const { data: assistant } = useAssistant();
  const { me } = useAuth();
  const [editing, setEditing] = useState<WatchdogTask | null | undefined>(undefined);
  const tasks = useQuery({
    queryKey: ['watchdogs'],
    queryFn: api.watchdogs,
    refetchInterval: (q) => (q.state.data?.some((t) => t.running) ? 3000 : 30_000),
  });
  const templates = useQuery({ queryKey: ['watchdog-templates'], queryFn: api.watchdogTemplates });
  const recent = useQuery({
    queryKey: ['watchdog-runs'],
    queryFn: () => api.recentWatchdogRuns(25),
    refetchInterval: () => (tasks.data?.some((t) => t.running) ? 3000 : 60_000),
  });
  const findings = useMemo(() => (recent.data ?? []).filter((r) => r.finding_detected).length, [recent.data]);
  const canManage = me?.is_admin || me?.role === 'advanced';

  if (!assistant?.watchdogs)
    return (
      <EmptyState icon={<IconDog size={28} />} title="Watchdogs are not available">
        A watchdog is the assistant investigating on its own, which is why they are off by default and reserved
        to administrators and advanced users. An administrator turns them on in{' '}
        <Link to="/admin/settings/assistant">Settings › Assistant</Link>, with the agent mode.
      </EmptyState>
    );

  return (
    <Stack gap="lg">
      <Group justify="space-between" align="flex-end">
        <div>
          <Title order={2}>Watchdogs</Title>
          <Text c="dimmed" size="sm">
            The assistant investigating a fixed set of referentials on its own — on a schedule, or right after one of
            them is synchronized. It only tells you when it finds something.
          </Text>
        </div>
        {canManage && (
          <Button leftSection={<IconPlus size={16} />} onClick={() => setEditing(null)}>
            New watchdog
          </Button>
        )}
      </Group>

      {tasks.error && <Alert color="red">{(tasks.error as Error).message}</Alert>}
      {tasks.isLoading && <Loader />}

      {tasks.data?.length === 0 && (
        <EmptyState icon={<IconDog size={28} />} title="No watchdog yet">
          {canManage
            ? 'Create one from an example: shadow IT against the CMDB, external exposure drift, or orphan accounts.'
            : 'Administrators and advanced users can set one up.'}
        </EmptyState>
      )}

      <Stack gap="md">
        {tasks.data?.map((task) => (
          <TaskCard key={task.id} task={task} onEdit={() => setEditing(task)} />
        ))}
      </Stack>

      {!!recent.data?.length && (
        <Card padding="lg">
          <Group justify="space-between" mb="sm">
            <Group gap="sm">
              <Title order={4}>Latest executions</Title>
              {findings > 0 && (
                <Badge color="orange" variant="light" leftSection={<IconBolt size={10} />}>
                  {findings} with a finding
                </Badge>
              )}
            </Group>
            <ActionIcon variant="subtle" onClick={() => recent.refetch()} aria-label="Refresh">
              <IconRefresh size={16} />
            </ActionIcon>
          </Group>
          <Stack gap={6}>
            {recent.data.map((run) => (
              <RunCard key={run.id} run={run} />
            ))}
          </Stack>
          <Text size="xs" c="dimmed" mt="sm">
            <IconClockHour4 size={12} style={{ verticalAlign: -2 }} /> The last {recent.data.length} runs. A watchdog
            keeps its 100 most recent ones.
          </Text>
        </Card>
      )}

      {editing !== undefined && (
        <Editor task={editing} templates={templates.data ?? []} onClose={() => setEditing(undefined)} />
      )}
    </Stack>
  );
}
