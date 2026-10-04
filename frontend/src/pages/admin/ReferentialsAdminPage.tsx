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
  Menu,
  Modal,
  ScrollArea,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
  Tooltip,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconDatabaseEdit, IconDatabasePlus, IconDots, IconFolderShare, IconEdit, IconFileCode, IconFileText, IconPlus, IconRefresh, IconSearch, IconTrash, IconUpload, IconWorld } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api } from '../../api/client';
import { useRefresh } from '../../api/hooks';
import type { Definition } from '../../api/types';
import { CopyIcon } from '../../components/Common';
import { HealthBadge } from '../../components/Status';
import { describeCron, fmtNumber, fmtRelative } from '../../lib/format';

function sourceLabel(d: Definition) {
  const s = d.config.source;
  if (d.kind === 'internal') return { icon: <IconDatabaseEdit size={14} />, text: 'Internal (edited in RefExposer)' };
  if (d.origin === 'sync') return { icon: <IconFolderShare size={14} />, text: d.summary.sync_path ?? 'Sync folder' };
  if (s.type === 'local') return { icon: <IconUpload size={14} />, text: d.origin === 'database' ? 'Uploaded files' : `Local files (${s.path})` };
  const hosts = [...new Set(s.urls.map((u) => { try { return new URL(u).host; } catch { return u; } }))];
  return { icon: <IconWorld size={14} />, text: `${hosts.join(', ')}${s.urls.length > 1 ? ` · ${s.urls.length} URLs` : ''}` };
}

function YamlModal({ id, onClose }: { id: string | null; onClose: () => void }) {
  const { data } = useQuery({ queryKey: ['definition-yaml', id], queryFn: () => api.definitionYaml(id!), enabled: !!id });
  return (
    <Modal opened={!!id} onClose={onClose} size="xl" title={<Text fw={700}>YAML definition — {id}</Text>}>
      <Stack gap="xs">
        <Text size="sm" c="dimmed">
          Put it in a file of the <Code>config/</Code> folder to version this referential with the rest of the configuration.
        </Text>
        <Group justify="flex-end">
          <CopyIcon value={data ?? ''} label="Copy the YAML" />
        </Group>
        <ScrollArea.Autosize mah={520}>
          <Code block fz={12}>
            {data ?? 'Chargement…'}
          </Code>
        </ScrollArea.Autosize>
      </Stack>
    </Modal>
  );
}

export default function ReferentialsAdminPage() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const refresh = useRefresh();
  const { data, isLoading } = useQuery({
    queryKey: ['definitions'],
    queryFn: api.definitions,
    // Fast while an update runs, slow otherwise
    refetchInterval: (q) => (q.state.data?.some((d) => d.summary?.current_run || d.run) ? 2000 : 15000),
  });
  const [filter, setFilter] = useState('');
  const [yamlId, setYamlId] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<Definition | null>(null);
  const [purge, setPurge] = useState(true);

  const remove = useMutation({
    mutationFn: (d: Definition) => (d.kind === 'internal' ? api.deleteInternal(d.id) : api.deleteDefinition(d.id, purge)),
    onSuccess: () => {
      notifications.show({ message: 'Referential deleted', color: 'teal' });
      setDeleting(null);
      qc.invalidateQueries();
    },
    onError: (e: Error) => notifications.show({ title: 'Failed', message: e.message, color: 'red' }),
  });

  const rows = useMemo(
    () =>
      (data ?? []).filter((d) =>
        `${d.id} ${d.config.name} ${d.config.category} ${d.config.source.urls.join(' ')}`.toLowerCase().includes(filter.toLowerCase()),
      ),
    [data, filter],
  );

  return (
    <Stack gap="lg">
      <Group justify="space-between" align="flex-end">
        <div>
          <Title order={2}>Referentials</Title>
          <Text c="dimmed" size="sm">
            Add a source (URL or file), configure how it is read, its schedule and its access.
          </Text>
        </div>
        <Group gap="xs">
          <Button variant="light" leftSection={<IconDatabasePlus size={16} />} onClick={() => navigate('/internal/new')}>
            New internal referential
          </Button>
          <Button leftSection={<IconPlus size={16} />} onClick={() => navigate('/admin/referentials/new')}>
            New referential
          </Button>
        </Group>
      </Group>
      <Alert variant="light" color="gray" icon={<IconFileText size={18} />}>
        <Text size="sm">
          Referentials created here are stored in the database and editable from the interface. Those defined in the YAML files of the
          <Code>config/</Code> folder are read-only here: edit the file then reload the configuration (System page). Files pushed into
          the sync folder become referentials by themselves (see Help › Sync folder).
        </Text>
      </Alert>
      <TextInput placeholder="Filter…" leftSection={<IconSearch size={16} />} value={filter} onChange={(e) => setFilter(e.currentTarget.value)} w={300} />
      <Card padding={0}>
        {isLoading ? (
          <Loader m="md" />
        ) : (
          <Table.ScrollContainer minWidth={1050}>
            <Table verticalSpacing="sm" fz="sm" highlightOnHover>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Referential</Table.Th>
                  <Table.Th>Definition</Table.Th>
                  <Table.Th>Source</Table.Th>
                  <Table.Th>Schedule</Table.Th>
                  <Table.Th>Status</Table.Th>
                  <Table.Th ta="right">Rows</Table.Th>
                  <Table.Th />
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {rows.map((d) => {
                  const src = sourceLabel(d);
                  const s = d.summary;
                  return (
                    <Table.Tr key={d.id}>
                      <Table.Td>
                        <Anchor component={Link} to={`/r/${d.id}`} fw={600} c="inherit" size="sm">
                          {d.config.name}
                        </Anchor>
                        <Group gap={6}>
                          <Text size="xs" c="dimmed" ff="monospace">
                            {d.id}
                          </Text>
                          <Badge size="xs" variant="default" tt="uppercase">
                            {d.config.format}
                          </Badge>
                        </Group>
                      </Table.Td>
                      <Table.Td>
                        {d.origin === 'sync' ? (
                          <Badge variant="light" color="teal" leftSection={<IconFolderShare size={10} />}>
                            Sync folder
                          </Badge>
                        ) : d.origin === 'database' ? (
                          <Badge variant="light" color="indigo">
                            {d.kind === 'internal' ? 'Internal' : 'Interface'}
                          </Badge>
                        ) : (
                          <Tooltip label={`config/${d.config_file}`}>
                            <Badge variant="light" color="gray" leftSection={<IconFileCode size={10} />}>
                              YAML
                            </Badge>
                          </Tooltip>
                        )}
                      </Table.Td>
                      <Table.Td maw={280}>
                        <Group gap={6} wrap="nowrap">
                          {src.icon}
                          <Text size="xs" truncate>
                            {src.text}
                          </Text>
                        </Group>
                      </Table.Td>
                      <Table.Td>
                        <Text size="xs">{d.kind === 'internal' ? 'On every edit' : d.origin === 'sync' ? 'When its files change' : s.pinned ? 'Suspended (frozen)' : describeCron(d.config.schedule)}</Text>
                        {s.last_success_at && (
                          <Text size="xs" c="dimmed">
                            last import {fmtRelative(s.last_success_at)}
                          </Text>
                        )}
                      </Table.Td>
                      <Table.Td>
                        <HealthBadge health={s.health} />
                      </Table.Td>
                      <Table.Td ta="right" className="tabular">
                        {fmtNumber(s.row_count)}
                      </Table.Td>
                      <Table.Td>
                        <Group gap={4} wrap="nowrap" justify="flex-end">
                          {d.editable && (
                            <Button size="compact-sm" variant="light" leftSection={<IconEdit size={14} />} onClick={() => navigate(d.kind === 'internal' ? `/internal/${d.id}/schema` : `/admin/referentials/${d.id}/edit`)}>
                              Configure
                            </Button>
                          )}
                          {d.kind !== 'internal' && (
                          <Tooltip label="Update now">
                            <ActionIcon variant="subtle" onClick={() => refresh.mutate({ id: d.id })} loading={!!s.current_run} aria-label="Update">
                              <IconRefresh size={16} />
                            </ActionIcon>
                          </Tooltip>
                          )}
                          <Menu position="bottom-end" withinPortal>
                            <Menu.Target>
                              <ActionIcon variant="subtle" color="gray" aria-label="Actions">
                                <IconDots size={16} />
                              </ActionIcon>
                            </Menu.Target>
                            <Menu.Dropdown>
                              <Menu.Item leftSection={<IconRefresh size={14} />} onClick={() => refresh.mutate({ id: d.id, force: true })}>
                                Force a rebuild
                              </Menu.Item>
                              <Menu.Item leftSection={<IconFileCode size={14} />} onClick={() => setYamlId(d.id)}>
                                View as YAML
                              </Menu.Item>
                              <Menu.Divider />
                              <Menu.Item leftSection={<IconTrash size={14} />} color="red" disabled={!d.editable} onClick={() => setDeleting(d)}>
                                Delete
                              </Menu.Item>
                            </Menu.Dropdown>
                          </Menu>
                        </Group>
                      </Table.Td>
                    </Table.Tr>
                  );
                })}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        )}
      </Card>
      <YamlModal id={yamlId} onClose={() => setYamlId(null)} />
      <Modal opened={!!deleting} onClose={() => setDeleting(null)} title={<Text fw={700}>Delete “{deleting?.config.name}”?</Text>}>
        <Stack>
          <Text size="sm">The definition and its access rights will be deleted. The audit history is kept.</Text>
          {deleting?.kind === 'internal' ? (
            <Text size="sm" c="red">
              This is an internal referential: all its rows are deleted too.
            </Text>
          ) : (
            <Checkbox label="Also delete the downloaded data and the published versions" checked={purge} onChange={(e) => setPurge(e.currentTarget.checked)} />
          )}
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setDeleting(null)}>
              Cancel
            </Button>
            <Button color="red" loading={remove.isPending} onClick={() => deleting && remove.mutate(deleting)}>
              Delete
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
