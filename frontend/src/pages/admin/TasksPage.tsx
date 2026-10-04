import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Card,
  Code,
  Drawer,
  FileButton,
  Group,
  Loader,
  Modal,
  NumberInput,
  Select,
  Stack,
  Switch,
  Table,
  Text,
  TextInput,
  Title,
  Tooltip,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import {
  IconAdjustments,
  IconDatabaseExport,
  IconDownload,
  IconHistory,
  IconPlayerPlay,
  IconRestore,
  IconTrash,
  IconUpload,
} from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../../api/client';
import type { SystemTask, TaskParamDef, TaskRun } from '../../api/types';
import { fmtBytes, fmtDate, fmtDuration, fmtRelative } from '../../lib/format';

const CATEGORIES: { id: SystemTask['category']; title: string; description: string }[] = [
  { id: 'maintenance', title: 'Maintenance', description: 'Sessions, audit log retention, disk space.' },
  { id: 'integrity', title: 'Integrity and monitoring', description: 'Published data, stale referentials, sources, certificate.' },
  { id: 'security', title: 'Security policy', description: 'Inactive accounts, API tokens, access requests, directory.' },
  { id: 'backup', title: 'Backup', description: 'Configuration backups, restorable below.' },
  { id: 'system', title: 'Folder scans', description: 'Import and sync folders (interval set by environment variables).' },
];

const PRESETS = [
  { value: '0 * * * *', label: 'Every hour' },
  { value: '0 2 * * *', label: 'Every day at 2:00' },
  { value: '0 6 * * *', label: 'Every day at 6:00' },
  { value: '0 9 * * 1-5', label: 'Weekdays at 9:00' },
  { value: '0 6 * * 1', label: 'Every Monday at 6:00' },
  { value: '0 6 1 * *', label: 'On the 1st of the month at 6:00' },
];

const STATUS_COLOR: Record<TaskRun['status'], string> = { success: 'teal', warning: 'orange', error: 'red' };

function StatusBadge({ run }: { run: TaskRun | null }) {
  if (!run) return <Text size="xs" c="dimmed">never run</Text>;
  return (
    <Tooltip label={`${fmtDate(run.started_at)} · ${fmtDuration(run.duration)} · ${run.trigger}${run.user ? ` by ${run.user}` : ''}`}>
      <Badge color={STATUS_COLOR[run.status]} variant="light" size="sm">
        {run.status}
      </Badge>
    </Tooltip>
  );
}

function useTaskMutation<T>(fn: (arg: T) => Promise<unknown>, success: string, after?: () => void) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['tasks'] });
      notifications.show({ message: success, color: 'teal' });
      after?.();
    },
    onError: (e: Error) => notifications.show({ title: 'Failed', message: e.message, color: 'red', autoClose: 10000 }),
  });
}

function ParamField({ def, value, onChange }: { def: TaskParamDef; value: unknown; onChange: (v: unknown) => void }) {
  if (def.type === 'bool') return <Switch label={def.label} checked={!!value} onChange={(e) => onChange(e.currentTarget.checked)} />;
  if (def.type === 'int')
    return <NumberInput label={def.label} min={def.min ?? undefined} max={def.max ?? undefined} value={Number(value)} onChange={(v) => onChange(Number(v))} />;
  if (def.type === 'choice')
    return <Select label={def.label} data={def.choices ?? []} value={String(value)} onChange={(v) => v && onChange(v)} allowDeselect={false} />;
  return <TextInput label={def.label} value={String(value ?? '')} onChange={(e) => onChange(e.currentTarget.value)} />;
}

function SettingsModal({ task, onClose }: { task: SystemTask; onClose: () => void }) {
  const [schedule, setSchedule] = useState(task.schedule ?? '');
  const [params, setParams] = useState<Record<string, unknown>>(task.params);
  const save = useTaskMutation(
    () => api.updateTask(task.id, { ...(task.interval ? {} : { schedule }), params }),
    'Task updated',
    onClose,
  );
  const preset = PRESETS.some((p) => p.value === schedule) ? schedule : 'custom';
  return (
    <Modal opened onClose={onClose} title={<Text fw={700}>{task.name}</Text>} size="lg">
      <Stack>
        <Text size="sm" c="dimmed">
          {task.description}
        </Text>
        {task.interval ? (
          <Text size="sm">
            Runs every {task.interval} s (environment variable).
          </Text>
        ) : (
          <Group grow align="flex-end">
            <Select
              label="Schedule"
              data={[...PRESETS, { value: 'custom', label: 'Custom (cron)…' }]}
              value={preset}
              onChange={(v) => v && v !== 'custom' && setSchedule(v)}
              allowDeselect={false}
            />
            <TextInput
              label="Cron expression"
              description={`Default: ${task.default_schedule}`}
              value={schedule}
              onChange={(e) => setSchedule(e.currentTarget.value)}
              styles={{ input: { fontFamily: 'var(--mantine-font-family-monospace)' } }}
            />
          </Group>
        )}
        {task.param_defs.map((d) => (
          <ParamField key={d.name} def={d} value={params[d.name]} onChange={(v) => setParams((p) => ({ ...p, [d.name]: v }))} />
        ))}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={() => save.mutate(undefined)} loading={save.isPending}>
            Save
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}

function HistoryDrawer({ task, onClose }: { task: SystemTask; onClose: () => void }) {
  const { data, isLoading } = useQuery({ queryKey: ['task-runs', task.id], queryFn: () => api.taskRuns(task.id) });
  return (
    <Drawer opened onClose={onClose} position="right" size="xl" title={<Text fw={700}>{task.name} · history</Text>}>
      {isLoading ? (
        <Loader />
      ) : !data?.length ? (
        <Text size="sm" c="dimmed">
          No run recorded.
        </Text>
      ) : (
        <Stack gap="sm">
          {data.map((r) => (
            <Card key={r.id} padding="sm" withBorder>
              <Group justify="space-between" mb={4}>
                <Group gap="xs">
                  <StatusBadge run={r} />
                  <Text size="sm">{fmtDate(r.started_at)}</Text>
                  <Text size="xs" c="dimmed">
                    {fmtDuration(r.duration)} · {r.trigger}
                    {r.user ? ` by ${r.user}` : ''}
                  </Text>
                </Group>
              </Group>
              <Text size="sm" style={{ wordBreak: 'break-word' }}>
                {r.message}
              </Text>
              {r.details && Object.keys(r.details).length > 0 && (
                <Code block fz={11} mt={4} style={{ maxHeight: 200, overflow: 'auto' }}>
                  {JSON.stringify(r.details, null, 2)}
                </Code>
              )}
            </Card>
          ))}
        </Stack>
      )}
    </Drawer>
  );
}

function TaskRow({ task }: { task: SystemTask }) {
  const [editing, setEditing] = useState(false);
  const [history, setHistory] = useState(false);
  const toggle = useTaskMutation((enabled: boolean) => api.updateTask(task.id, { enabled }), task.enabled ? 'Task disabled' : 'Task enabled');
  const runNow = useTaskMutation(() => api.runTask(task.id), 'Task started');
  const qc = useQueryClient();
  const last = task.last_run;
  return (
    <Table.Tr opacity={task.available ? 1 : 0.55}>
      <Table.Td maw={360}>
        <Text size="sm" fw={600}>
          {task.name}
        </Text>
        <Text size="xs" c="dimmed">
          {task.available ? task.description : 'Not available with the current configuration.'}
        </Text>
      </Table.Td>
      <Table.Td>
        {task.interval ? (
          <Text size="xs">every {task.interval} s</Text>
        ) : (
          <Code fz={11}>{task.schedule}</Code>
        )}
        {task.enabled && task.next_run_at && (
          <Text size="xs" c="dimmed">
            next {fmtRelative(task.next_run_at)}
          </Text>
        )}
      </Table.Td>
      <Table.Td>
        <Switch checked={task.enabled} disabled={!task.available} onChange={(e) => toggle.mutate(e.currentTarget.checked)} aria-label="Enabled" />
      </Table.Td>
      <Table.Td maw={380}>
        {task.running ? (
          <Group gap={6}>
            <Loader size="xs" />
            <Text size="xs">running…</Text>
          </Group>
        ) : (
          <>
            <Group gap={6}>
              <StatusBadge run={last} />
              {last && (
                <Text size="xs" c="dimmed">
                  {fmtRelative(last.finished_at ?? last.started_at)}
                </Text>
              )}
            </Group>
            {last?.message && (
              <Text size="xs" lineClamp={2} title={last.message}>
                {last.message}
              </Text>
            )}
          </>
        )}
      </Table.Td>
      <Table.Td w={130}>
        <Group gap={4} wrap="nowrap">
          <Tooltip label="Run now">
            <ActionIcon
              variant="subtle"
              disabled={!task.available || !!task.running}
              loading={runNow.isPending}
              onClick={() => {
                runNow.mutate(undefined);
                setTimeout(() => qc.invalidateQueries({ queryKey: ['tasks'] }), 1500);
              }}
              aria-label="Run now"
            >
              <IconPlayerPlay size={16} />
            </ActionIcon>
          </Tooltip>
          <Tooltip label="Schedule and parameters">
            <ActionIcon variant="subtle" color="gray" onClick={() => setEditing(true)} aria-label="Settings">
              <IconAdjustments size={16} />
            </ActionIcon>
          </Tooltip>
          <Tooltip label="History">
            <ActionIcon variant="subtle" color="gray" onClick={() => setHistory(true)} aria-label="History">
              <IconHistory size={16} />
            </ActionIcon>
          </Tooltip>
        </Group>
        {editing && <SettingsModal task={task} onClose={() => setEditing(false)} />}
        {history && <HistoryDrawer task={task} onClose={() => setHistory(false)} />}
      </Table.Td>
    </Table.Tr>
  );
}

function RestoreModal({ name, file, onClose }: { name?: string; file?: File; onClose: () => void }) {
  const [confirm, setConfirm] = useState('');
  const restore = useMutation({
    mutationFn: () => (file ? api.restoreBackupUpload(file, confirm) : api.restoreBackup(name!, confirm)),
    onSuccess: () => {
      notifications.show({ message: 'Configuration restored: sign in again', color: 'teal' });
      setTimeout(() => (window.location.href = '/'), 1200);
    },
    onError: (e: Error) => notifications.show({ title: 'Restore failed', message: e.message, color: 'red', autoClose: 10000 }),
  });
  return (
    <Modal opened onClose={onClose} title={<Text fw={700}>Restore {file ? file.name : name}</Text>}>
      <Stack>
        <Alert color="red">
          The current referential definitions, accounts, groups, rights, API tokens, settings and internal rows are replaced by
          those of the backup. Everybody is signed out. The published data, the history and the audit log are kept.
        </Alert>
        <TextInput label="Type RESTORE to confirm" value={confirm} onChange={(e) => setConfirm(e.currentTarget.value)} />
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            Cancel
          </Button>
          <Button color="red" disabled={confirm !== 'RESTORE'} loading={restore.isPending} onClick={() => restore.mutate()}>
            Restore
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}

function Backups() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ['backups'], queryFn: api.backups, refetchInterval: 10000 });
  const [restoring, setRestoring] = useState<{ name?: string; file?: File } | null>(null);
  const remove = useMutation({
    mutationFn: api.deleteBackup,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['backups'] });
      notifications.show({ message: 'Backup deleted', color: 'teal' });
    },
  });
  return (
    <Card padding="lg">
      <Group justify="space-between" mb="sm">
        <div>
          <Group gap="xs">
            <IconDatabaseExport size={18} />
            <Title order={5}>Configuration backups</Title>
          </Group>
          <Text size="sm" c="dimmed">
            Encrypted with <Code>REFEX_SECRET_KEY</Code>: restorable only with the same key. They do not replace the backup of the
            database and of <Code>data/</Code>.
          </Text>
        </div>
        <FileButton onChange={(f) => f && setRestoring({ file: f })}>
          {(props) => (
            <Button {...props} variant="default" leftSection={<IconUpload size={16} />}>
              Restore a file…
            </Button>
          )}
        </FileButton>
      </Group>
      {!data?.length ? (
        <Text size="sm" c="dimmed">
          No backup yet: run the “Configuration backup” task.
        </Text>
      ) : (
        <Table fz="sm" verticalSpacing="xs">
          <Table.Tbody>
            {data.map((b) => (
              <Table.Tr key={b.name}>
                <Table.Td>
                  <Code fz={11}>{b.name}</Code>{' '}
                  {!b.encrypted && (
                    <Badge size="xs" color="orange" variant="light">
                      not encrypted
                    </Badge>
                  )}
                </Table.Td>
                <Table.Td>{fmtDate(b.created_at)}</Table.Td>
                <Table.Td>{fmtBytes(b.size)}</Table.Td>
                <Table.Td w={120}>
                  <Group gap={4} wrap="nowrap">
                    <Tooltip label="Download">
                      <ActionIcon variant="subtle" component="a" href={`/api/admin/backups/${encodeURIComponent(b.name)}`} aria-label="Download">
                        <IconDownload size={16} />
                      </ActionIcon>
                    </Tooltip>
                    <Tooltip label="Restore">
                      <ActionIcon variant="subtle" color="orange" onClick={() => setRestoring({ name: b.name })} aria-label="Restore">
                        <IconRestore size={16} />
                      </ActionIcon>
                    </Tooltip>
                    <Tooltip label="Delete">
                      <ActionIcon variant="subtle" color="red" onClick={() => remove.mutate(b.name)} aria-label="Delete">
                        <IconTrash size={16} />
                      </ActionIcon>
                    </Tooltip>
                  </Group>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
      {restoring && <RestoreModal name={restoring.name} file={restoring.file} onClose={() => setRestoring(null)} />}
    </Card>
  );
}

export default function TasksPage() {
  const { data, isLoading } = useQuery({
    queryKey: ['tasks'],
    queryFn: api.tasks,
    refetchInterval: (q) => (q.state.data?.some((t) => t.running) ? 1500 : 10000),
  });
  if (isLoading || !data) return <Loader />;
  return (
    <Stack gap="lg">
      <div>
        <Title order={2}>System tasks</Title>
        <Text c="dimmed" size="sm">
          Scheduled maintenance, monitoring, security policy and backups. Every run that does something is written to the audit log
          (and forwarded to syslog when configured).
        </Text>
      </div>
      {!data[0]?.scheduler_running && (
        <Alert color="orange">The scheduler is disabled (REFEX_SCHEDULER_ENABLED=false): tasks only run with “Run now”.</Alert>
      )}
      {CATEGORIES.map((c) => {
        const tasks = data.filter((t) => t.category === c.id);
        if (!tasks.length) return null;
        return (
          <Card key={c.id} padding="lg">
            <Title order={5}>{c.title}</Title>
            <Text size="sm" c="dimmed" mb="sm">
              {c.description}
            </Text>
            <Table.ScrollContainer minWidth={900}>
              <Table verticalSpacing="sm" fz="sm">
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Task</Table.Th>
                    <Table.Th>Schedule</Table.Th>
                    <Table.Th>Enabled</Table.Th>
                    <Table.Th>Last run</Table.Th>
                    <Table.Th />
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {tasks.map((t) => (
                    <TaskRow key={t.id} task={t} />
                  ))}
                </Table.Tbody>
              </Table>
            </Table.ScrollContainer>
          </Card>
        );
      })}
      <Backups />
    </Stack>
  );
}
