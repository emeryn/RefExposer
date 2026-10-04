import { Alert, Anchor, Badge, Button, Card, Code, Group, Loader, SimpleGrid, Stack, Table, Text, Title } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconAlertTriangle, IconFolder, IconFolderShare, IconReload } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api/client';
import { RunProgress } from '../components/Status';
import { fmtBytes, fmtDate, fmtNumber, fmtRelative } from '../lib/format';

function Item({ label, value }: { label: string; value: ReactNode }) {
  return (
    <Group justify="space-between" py={6} style={{ borderBottom: '1px solid var(--mantine-color-default-border)' }}>
      <Text size="sm" c="dimmed">
        {label}
      </Text>
      <Text size="sm" fw={500} component="div">
        {value}
      </Text>
    </Group>
  );
}

export default function SystemPage() {
  const qc = useQueryClient();
  const { data: sys, isLoading } = useQuery({ queryKey: ['system'], queryFn: api.system, refetchInterval: 5000 });
  const reload = useMutation({
    mutationFn: api.reload,
    onSuccess: (res) => {
      notifications.show({
        title: 'Configuration reloaded',
        message: `${res.loaded} referential(s) loaded${res.errors.length ? `, ${res.errors.length} error(s)` : ''}`,
        color: res.errors.length ? 'orange' : 'teal',
      });
      qc.invalidateQueries();
    },
    onError: (e: Error) => notifications.show({ title: 'Failed', message: e.message, color: 'red' }),
  });

  if (isLoading || !sys) return <Loader />;

  return (
    <Stack gap="lg">
      <Group justify="space-between" align="flex-end">
        <div>
          <Title order={2}>System</Title>
          <Text c="dimmed" size="sm">
            Service configuration, scheduling and limits.
          </Text>
        </div>
        <Button leftSection={<IconReload size={16} />} onClick={() => reload.mutate()} loading={reload.isPending}>
          Reload the configuration
        </Button>
      </Group>

      {sys.config_errors.length > 0 && (
        <Alert color="orange" icon={<IconAlertTriangle />} title="Configuration errors">
          <Stack gap={4}>
            {sys.config_errors.map((e) => (
              <Text size="sm" key={e.file}>
                <b>{e.file}</b>: {e.error}
              </Text>
            ))}
          </Stack>
        </Alert>
      )}

      <SimpleGrid cols={{ base: 1, md: 2 }}>
        <Card>
          <Title order={5} mb="xs">
            Service
          </Title>
          <Item label="Version" value={`RefExposer ${sys.version}`} />
          <Item
            label="Public URL"
            value={sys.public_url ? <code>{sys.public_url}</code> : <Text span size="sm" c="orange">not set (REFEX_PUBLIC_URL), derived from each request</Text>}
          />
          <Item label="Engine" value={`DuckDB ${sys.duckdb_version}`} />
          <Item label="Started" value={`${fmtRelative(sys.started_at)} (${fmtDate(sys.started_at)})`} />
          <Item label="Data folder" value={<code>{sys.data_dir}</code>} />
          <Item label="Configuration folder" value={<code>{sys.config_dir}</code>} />
          <Item label="Storage used" value={fmtBytes(sys.storage_size)} />
          <Item label="Time zone" value={sys.timezone} />
          <Item
            label="Excel support"
            value={<Badge color={sys.excel_support ? 'teal' : 'gray'} variant="light">{sys.excel_support ? 'enabled' : 'unavailable'}</Badge>}
          />
          <Item label="API documentation" value={<Anchor href="/api/docs" target="_blank">/api/docs</Anchor>} />
        </Card>
        <Card>
          <Title order={5} mb="xs">
            Updates & limits
          </Title>
          <Item
            label="Scheduler"
            value={<Badge color={sys.scheduler_running ? 'teal' : 'gray'} variant="light">{sys.scheduler_running ? 'running' : 'stopped'}</Badge>}
          />
          <Item label="At startup" value={{ missing: 'imports referentials without data', all: 'updates everything', none: 'nothing' }[sys.refresh_on_startup] ?? sys.refresh_on_startup} />
          <Item label="Concurrent updates" value={sys.max_concurrent_jobs} />
          <Item label="Max rows per API page" value={sys.limits.api_max_limit} />
          <Item label="Max SQL query time" value={`${sys.limits.sql_timeout} s`} />
          <Item label="Max rows of an SQL result" value={fmtNumber(sys.limits.sql_max_rows)} />
          <Item label="Max rows of an export" value={fmtNumber(sys.limits.export_max_rows)} />
        </Card>
      </SimpleGrid>
      
      <Card>
        <Group gap="xs" mb="xs">
          <IconFolderShare size={18} />
          <Title order={5}>Sync folder</Title>
        </Group>
        {sys.sync_dir ? (
          <Text size="sm" c="dimmed">
            Each file or folder of <Code>{sys.sync_dir}</Code> is a referential, imported again when its files change (checked every{' '}
            {sys.sync_poll_seconds} s, once untouched for {sys.sync_settle_seconds} s). {sys.sync_referentials.length} referential(s):{' '}
            {sys.sync_referentials.length ? sys.sync_referentials.map((id, i) => (
              <span key={id}>
                {i > 0 && ', '}
                <Anchor component={Link} to={`/r/${id}`} size="sm">
                  {id}
                </Anchor>
              </span>
            )) : 'none yet'}
            .
          </Text>
        ) : (
          <Text size="sm" c="dimmed">
            Not configured: mount a folder and set <Code>REFEX_SYNC_DIR</Code>.
          </Text>
        )}
      </Card>

      <Card>
      <Group gap="xs" mb="xs">
      <IconFolder size={18} />
      <Title order={5}>Import folder</Title>
      </Group>
      {sys.import_dir ? (
      <>
      <Text size="sm" c="dimmed" mb="xs">
      Files copied into <Code>{sys.import_dir}/&lt;referential id&gt;/</Code> are imported automatically as a new version (checked every{' '}
      {sys.import_poll_seconds} s). Imported files move to <Code>.done/</Code>, refused ones to <Code>.failed/</Code> with an
      <Code>ERROR.txt</Code> explaining why. Each referential folder contains a <Code>README.txt</Code>.
      </Text>
      {sys.import_unknown_folders.length > 0 && (
      <Alert color="orange" p="xs" icon={<IconAlertTriangle size={16} />}>
      <Text size="sm">
      Folders that match no referential (ignored): {sys.import_unknown_folders.join(', ')}
      </Text>
      </Alert>
      )}
      </>
      ) : (
      <Text size="sm" c="dimmed">
      Not configured. Mount a volume and set <Code>REFEX_IMPORT_DIR</Code> to let operators drop files for manual imports.
      Uploads from the referential page work without it.
      </Text>
      )}
      </Card>

      {sys.active_runs.length > 0 && (
        <Card>
          <Title order={5} mb="sm">
            Running updates
          </Title>
          <Stack>
            {sys.active_runs.map((r) => (
              <div key={r.id}>
                <Anchor component={Link} to={`/r/${r.ref_id}/history`} size="sm" fw={600}>
                  {r.ref_id}
                </Anchor>
                <RunProgress run={r} compact />
              </div>
            ))}
          </Stack>
        </Card>
      )}

      <Card>
        <Title order={5} mb="sm">
          Schedule
        </Title>
        <Table fz="sm">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Referential</Table.Th>
              <Table.Th>Next run</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {sys.scheduled_jobs.map((j) => (
              <Table.Tr key={j.id}>
                <Table.Td>
                  <Anchor component={Link} to={`/r/${j.id}`} size="sm">
                    {j.name}
                  </Anchor>
                </Table.Td>
                <Table.Td>
                  {fmtDate(j.next_run_at)}{' '}
                  <Text span c="dimmed" size="xs">
                    ({fmtRelative(j.next_run_at)})
                  </Text>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Card>
    </Stack>
  );
}
