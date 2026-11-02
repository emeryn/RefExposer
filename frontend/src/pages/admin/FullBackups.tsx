/**
 * Full backups: the whole instance in one archive — every table of the database, the definitions and the
 * published data — on the server and / or in an S3 bucket.
 *
 * The configuration backups above it carry the configuration alone and restore onto a fresh instance that then
 * downloads its sources again. This card is for putting an instance back exactly as it was, data included, so
 * every restore here asks for a confirmation word and says plainly what it replaces.
 */

import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Card,
  Checkbox,
  Code,
  Group,
  List,
  Loader,
  Modal,
  Stack,
  Table,
  Tabs,
  Text,
  TextInput,
  Title,
  Tooltip,
} from '@mantine/core';
import { FileButton } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import {
  IconAlertTriangle,
  IconCloudDownload,
  IconCloudUpload,
  IconDatabaseExport,
  IconDownload,
  IconInfoCircle,
  IconPlayerPlay,
  IconRestore,
  IconTrash,
  IconUpload,
} from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../../api/client';
import type { FullBackup, RemoteFullBackup } from '../../api/types';
import { fmtBytes, fmtDate } from '../../lib/format';

const CONFIRM = 'RESTORE EVERYTHING';

type Target = { name?: string; file?: File };

function RestoreModal({ target, onClose }: { target: Target; onClose: () => void }) {
  const [confirm, setConfirm] = useState('');
  const [removeExtra, setRemoveExtra] = useState(false);
  const label = target.file ? target.file.name : target.name!;
  const restore = useMutation({
    mutationFn: () =>
      target.file
        ? api.restoreFullBackupUpload(target.file, confirm, removeExtra)
        : api.restoreFullBackup(target.name!, confirm, removeExtra),
    onSuccess: () => {
      notifications.show({ message: 'Instance restored: sign in again', color: 'teal' });
      setTimeout(() => (window.location.href = '/'), 1500);
    },
    onError: (e: Error) =>
      notifications.show({ title: 'Restore failed', message: e.message, color: 'red', autoClose: 15000 }),
  });
  return (
    <Modal opened onClose={onClose} title={<Text fw={700}>Restore {label}</Text>} size="lg">
      <Stack>
        <Alert color="red" icon={<IconAlertTriangle size={16} />}>
          Everything is replaced by what the backup holds:
          <List size="sm" mt={6}>
            <List.Item>every account, group, right, API token and setting;</List.Item>
            <List.Item>every referential definition, and the rows of the internal ones;</List.Item>
            <List.Item>the published data of <Code>data/</Code>, their indexes and their history;</List.Item>
            <List.Item>the audit log, the saved searches and the watchdogs.</List.Item>
          </List>
          Everybody is signed out. This cannot be undone.
        </Alert>
        <Checkbox
          label="Also delete the data of referentials the backup does not carry"
          description="Off: a referential created since the backup keeps its data, and only its definition goes away with the database."
          checked={removeExtra}
          onChange={(e) => setRemoveExtra(e.currentTarget.checked)}
        />
        <Text size="sm" c="dimmed">
          An archive made with another <Code>REFEX_SECRET_KEY</Code> cannot be read: keep that key with the backup, never inside
          it.
        </Text>
        <TextInput
          label={`Type ${CONFIRM} to confirm`}
          value={confirm}
          onChange={(e) => setConfirm(e.currentTarget.value)}
          placeholder={CONFIRM}
        />
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            Cancel
          </Button>
          <Button color="red" disabled={confirm !== CONFIRM} loading={restore.isPending} onClick={() => restore.mutate()}>
            Restore everything
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}

function ManifestModal({ name, onClose }: { name: string; onClose: () => void }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ['full-backup-manifest', name],
    queryFn: () => api.fullBackupManifest(name),
  });
  return (
    <Modal opened onClose={onClose} title={<Code fz={12}>{name}</Code>} size="lg">
      {isLoading && <Loader />}
      {error && <Alert color="red">{(error as Error).message}</Alert>}
      {data && (
        <Stack gap="xs">
          <Text size="sm">
            Written on {fmtDate(data.created_at)} by RefExposer {data.app_version}
            {data.encrypted ? ', encrypted' : ', NOT encrypted'}
            {data.include_raw ? ', downloaded sources included' : ''}.
          </Text>
          <Text size="sm">
            {data.data_files} file(s) of <Code>data/</Code> ({fmtBytes(data.data_bytes)}), {data.rows.toLocaleString()} rows of
            database, {data.config_files.length} definition file(s).
          </Text>
          <div>
            <Text size="sm" fw={500} mb={4}>
              Referentials ({data.referentials.length})
            </Text>
            <Group gap={4}>
              {data.referentials.map((r) => (
                <Badge key={r} variant="light" size="sm">
                  {r}
                </Badge>
              ))}
            </Group>
          </div>
          <div>
            <Text size="sm" fw={500} mb={4}>
              Tables
            </Text>
            <Table fz="xs" verticalSpacing={2}>
              <Table.Tbody>
                {Object.entries(data.counts)
                  .filter(([, n]) => n > 0)
                  .map(([table, n]) => (
                    <Table.Tr key={table}>
                      <Table.Td>
                        <Code fz={11}>{table}</Code>
                      </Table.Td>
                      <Table.Td ta="right">{n.toLocaleString()}</Table.Td>
                    </Table.Tr>
                  ))}
              </Table.Tbody>
            </Table>
          </div>
        </Stack>
      )}
    </Modal>
  );
}

function LocalTable({ backups, onRestore }: { backups: FullBackup[]; onRestore: (t: Target) => void }) {
  const qc = useQueryClient();
  const [manifest, setManifest] = useState<string | null>(null);
  const remove = useMutation({
    mutationFn: api.deleteFullBackup,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['full-backups'] });
      notifications.show({ message: 'Backup deleted', color: 'teal' });
    },
    onError: (e: Error) => notifications.show({ title: 'Failed', message: e.message, color: 'red' }),
  });
  if (!backups.length)
    return (
      <Text size="sm" c="dimmed">
        No full backup yet: run it now, or give the “Full backup” task above a schedule.
      </Text>
    );
  return (
    <>
      <Table fz="sm" verticalSpacing="xs">
        <Table.Tbody>
          {backups.map((b) => (
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
              <Table.Td w={160}>
                <Group gap={4} wrap="nowrap">
                  <Tooltip label="What it holds">
                    <ActionIcon variant="subtle" color="gray" onClick={() => setManifest(b.name)} aria-label="Contents">
                      <IconInfoCircle size={16} />
                    </ActionIcon>
                  </Tooltip>
                  <Tooltip label="Download">
                    <ActionIcon
                      variant="subtle"
                      component="a"
                      href={`/api/admin/full-backups/${encodeURIComponent(b.name)}`}
                      aria-label="Download"
                    >
                      <IconDownload size={16} />
                    </ActionIcon>
                  </Tooltip>
                  <Tooltip label="Restore everything">
                    <ActionIcon variant="subtle" color="orange" onClick={() => onRestore({ name: b.name })} aria-label="Restore">
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
      {manifest && <ManifestModal name={manifest} onClose={() => setManifest(null)} />}
    </>
  );
}

function RemoteTable({ bucket }: { bucket: string }) {
  const qc = useQueryClient();
  const { data, isLoading, error } = useQuery({ queryKey: ['full-backups-remote'], queryFn: api.remoteFullBackups });
  const after = (message: string) => () => {
    qc.invalidateQueries({ queryKey: ['full-backups'] });
    qc.invalidateQueries({ queryKey: ['full-backups-remote'] });
    notifications.show({ message, color: 'teal' });
  };
  const fail = (e: Error) => notifications.show({ title: 'Failed', message: e.message, color: 'red', autoClose: 12000 });
  const fetchOne = useMutation({ mutationFn: api.fetchRemoteFullBackup, onSuccess: after('Brought back onto the server'), onError: fail });
  const remove = useMutation({ mutationFn: api.deleteRemoteFullBackup, onSuccess: after('Deleted from the bucket'), onError: fail });
  if (isLoading) return <Loader size="sm" />;
  if (error) return <Alert color="red">{(error as Error).message}</Alert>;
  if (!data?.length)
    return (
      <Text size="sm" c="dimmed">
        Nothing in <Code>{bucket}</Code> yet.
      </Text>
    );
  return (
    <Table fz="sm" verticalSpacing="xs">
      <Table.Tbody>
        {data.map((b: RemoteFullBackup) => (
          <Table.Tr key={b.key}>
            <Table.Td>
              <Code fz={11}>{b.name}</Code>{' '}
              {!b.encrypted && (
                <Badge size="xs" color="orange" variant="light">
                  not encrypted
                </Badge>
              )}
            </Table.Td>
            <Table.Td>{fmtDate(b.last_modified)}</Table.Td>
            <Table.Td>{fmtBytes(b.size)}</Table.Td>
            <Table.Td w={100}>
              <Group gap={4} wrap="nowrap">
                <Tooltip label="Bring it back onto the server, then restore it from the list above">
                  <ActionIcon
                    variant="subtle"
                    onClick={() => fetchOne.mutate(b.name)}
                    loading={fetchOne.isPending && fetchOne.variables === b.name}
                    aria-label="Fetch"
                  >
                    <IconCloudDownload size={16} />
                  </ActionIcon>
                </Tooltip>
                <Tooltip label="Delete from the bucket">
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
  );
}

export default function FullBackups() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ['full-backups'], queryFn: api.fullBackups, refetchInterval: 10000 });
  const [restoring, setRestoring] = useState<Target | null>(null);
  const run = useMutation({
    mutationFn: api.runFullBackup,
    onSuccess: () => {
      notifications.show({ message: 'Full backup started: follow it in the history of the “Full backup” task', color: 'teal' });
      qc.invalidateQueries({ queryKey: ['tasks'] });
      setTimeout(() => qc.invalidateQueries({ queryKey: ['full-backups'] }), 3000);
    },
    onError: (e: Error) => notifications.show({ title: 'Failed', message: e.message, color: 'red', autoClose: 12000 }),
  });
  return (
    <Card padding="lg">
      <Group justify="space-between" mb="sm" wrap="nowrap" align="flex-start">
        <div>
          <Group gap="xs">
            <IconDatabaseExport size={18} />
            <Title order={5}>Full backups</Title>
          </Group>
          <Text size="sm" c="dimmed">
            Everything in one archive: the database, the definitions and the published data of <Code>data/</Code>. Restorable as
            it was. The downloaded source files and the generated downloads are left out — they come back on their own. Set how
            often it runs with the schedule of the “Full backup” task above, and where it goes in Settings › Backup.
          </Text>
        </div>
        <Group gap="xs" wrap="nowrap">
          <FileButton onChange={(f) => f && setRestoring({ file: f })} accept=".enc,.gz">
            {(props) => (
              <Button {...props} variant="default" leftSection={<IconUpload size={16} />}>
                Restore a file…
              </Button>
            )}
          </FileButton>
          <Button leftSection={<IconPlayerPlay size={16} />} loading={run.isPending} onClick={() => run.mutate()}>
            Back up now
          </Button>
        </Group>
      </Group>
      <Alert color="yellow" variant="light" mb="sm" icon={<IconAlertTriangle size={16} />}>
        The archive never carries the encryption key (<Code>data/.secret_key</Code> or <Code>REFEX_SECRET_KEY</Code>). Keep that
        key somewhere else: without it an encrypted archive cannot be read, and the secrets of a restored instance cannot be
        decrypted.
      </Alert>
      {data?.s3_enabled && data.bucket ? (
        <Tabs defaultValue="local">
          <Tabs.List mb="sm">
            <Tabs.Tab value="local">On the server</Tabs.Tab>
            <Tabs.Tab value="remote" leftSection={<IconCloudUpload size={14} />}>
              In {data.bucket}
            </Tabs.Tab>
          </Tabs.List>
          <Tabs.Panel value="local">
            <LocalTable backups={data.backups} onRestore={setRestoring} />
          </Tabs.Panel>
          <Tabs.Panel value="remote">
            <RemoteTable bucket={data.bucket} />
          </Tabs.Panel>
        </Tabs>
      ) : (
        <LocalTable backups={data?.backups ?? []} onRestore={setRestoring} />
      )}
      {restoring && <RestoreModal target={restoring} onClose={() => setRestoring(null)} />}
    </Card>
  );
}
