import {
  Alert,
  Anchor,
  Badge,
  Button,
  Card,
  Checkbox,
  Code,
  Group,
  List,
  Loader,
  Modal,
  MultiSelect,
  PasswordInput,
  ScrollArea,
  Stack,
  Switch,
  Table,
  Text,
  TextInput,
  Title,
  Tooltip,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconPencil, IconPlugConnected, IconPlus, IconRefresh, IconTrash, IconWorldShare } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../../api/client';
import type { Peer, PeerDataset } from '../../api/types';
import { EmptyState } from '../../components/Common';
import { describeCron, fmtDate, fmtNumber, fmtRelative } from '../../lib/format';

const STATUS_COLORS: Record<string, string> = { success: 'teal', warning: 'orange', error: 'red' };

function PeerForm({ peer, onDone }: { peer?: Peer; onDone: () => void }) {
  const qc = useQueryClient();
  const [name, setName] = useState(peer?.name ?? '');
  const [url, setUrl] = useState(peer?.url ?? '');
  const [token, setToken] = useState('');
  const [prefix, setPrefix] = useState(peer?.prefix ?? '');
  const [all, setAll] = useState(!peer?.datasets.length);
  const [datasets, setDatasets] = useState<string[]>(peer?.datasets ?? []);
  const [schedule, setSchedule] = useState(peer?.schedule ?? '0 * * * *');
  const [category, setCategory] = useState(peer?.category ?? '');
  const [groups, setGroups] = useState<string[]>(peer?.grant_groups ?? []);
  const [enabled, setEnabled] = useState(peer?.enabled ?? true);
  const [shared, setShared] = useState<PeerDataset[] | null>(null);
  const { data: allGroups } = useQuery({ queryKey: ['groups'], queryFn: api.groups });

  const test = useMutation({
    mutationFn: () => api.peerCatalog(token ? { url, token } : { url, peer: peer?.name }),
    onSuccess: (doc) => {
      setShared(doc.datasets);
      notifications.show({ message: `Connected as ${doc.instance?.account ?? '?'}: ${doc.datasets.length} referential(s) shared`, color: 'teal' });
    },
    onError: (e: Error) => notifications.show({ title: 'Connection failed', message: e.message, color: 'red', autoClose: 10000 }),
  });
  const save = useMutation({
    mutationFn: () =>
      api.savePeer(name.trim(), {
        url,
        token: token || null,
        prefix,
        datasets: all ? [] : datasets,
        schedule,
        enabled,
        category,
        grant_groups: groups,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['peers'] });
      notifications.show({ message: peer ? 'Peer saved' : 'Peer added: synchronize it to create the replicas', color: 'teal' });
      onDone();
    },
    onError: (e: Error) => notifications.show({ title: 'Not saved', message: e.message, color: 'red', autoClose: 10000 }),
  });

  const choices = shared ?? datasets.map((id) => ({ id, name: id }) as PeerDataset);
  return (
    <Stack>
      <TextInput
        label="Name"
        description="Lower case letters, digits, '-' and '_'. It cannot be changed."
        placeholder="production"
        value={name}
        onChange={(e) => setName(e.currentTarget.value.toLowerCase())}
        disabled={!!peer}
        required
      />
      <TextInput label="URL of the RefExposer instance" placeholder="https://refexposer.example.com" value={url} onChange={(e) => setUrl(e.currentTarget.value)} required />
      <PasswordInput
        label={peer ? 'New API token' : 'API token'}
        description={
          peer
            ? 'Empty: the stored token is kept. It is kept in the secret manager, only sent to the host of the peer.'
            : 'Token of a service account of the peer, with the read right on the referentials to share. Kept in the secret manager, only sent to the host of the peer.'
        }
        placeholder="rfx_…"
        value={token}
        onChange={(e) => setToken(e.currentTarget.value)}
        autoComplete="new-password"
        required={!peer}
      />
      <Group>
        <Button variant="default" leftSection={<IconPlugConnected size={16} />} onClick={() => test.mutate()} loading={test.isPending} disabled={!url || (!token && !peer)}>
          Test the connection
        </Button>
        {shared && (
          <Text size="sm" c="dimmed">
            {shared.length} referential(s) shared with this account
          </Text>
        )}
      </Group>
      <Switch label="Every shared referential (also the ones shared later)" checked={all} onChange={(e) => setAll(e.currentTarget.checked)} />
      {!all && (
        <Stack gap={4}>
          {!shared && (
            <Text size="xs" c="dimmed">
              Test the connection to choose among the shared referentials.
            </Text>
          )}
          <ScrollArea.Autosize mah={240}>
            <Stack gap={4}>
              {choices.map((d) => (
                <Checkbox
                  key={d.id}
                  size="xs"
                  checked={datasets.includes(d.id)}
                  onChange={(e) => setDatasets(e.currentTarget.checked ? [...datasets, d.id] : datasets.filter((x) => x !== d.id))}
                  label={
                    <Text size="sm">
                      {d.name} <Code>{d.id}</Code>
                      {d.row_count != null && (
                        <Text span size="xs" c="dimmed">
                          {' '}
                          · {fmtNumber(d.row_count)} rows{d.quality_rules ? ` · ${d.quality_rules} quality rule(s)` : ''}
                          {d.transform ? ' · SQL transformation' : ''}
                        </Text>
                      )}
                    </Text>
                  }
                />
              ))}
            </Stack>
          </ScrollArea.Autosize>
        </Stack>
      )}
      <Group grow align="flex-start">
        <TextInput
          label="Prefix of the identifiers"
          description="Replica of “iocs”: prefix + iocs (e.g. prod-iocs). Avoids clashes with the referentials of this instance."
          placeholder="prod-"
          value={prefix}
          onChange={(e) => setPrefix(e.currentTarget.value.toLowerCase())}
        />
        <TextInput label="Schedule (cron)" description={describeCron(schedule)} value={schedule} onChange={(e) => setSchedule(e.currentTarget.value)} />
      </Group>
      <Group grow align="flex-start">
        <TextInput label="Category of the replicas" description="The category of the peer when empty" value={category} onChange={(e) => setCategory(e.currentTarget.value)} />
        <MultiSelect
          label="Read right for the groups"
          description="Given on the replicas when they are created"
          data={(allGroups ?? []).map((g) => g.name)}
          value={groups}
          onChange={setGroups}
          searchable
        />
      </Group>
      <Switch label="Synchronized on schedule" checked={enabled} onChange={(e) => setEnabled(e.currentTarget.checked)} />
      <Button onClick={() => save.mutate()} disabled={!name.trim() || !url || (!peer && !token) || (!all && !datasets.length)} loading={save.isPending}>
        {peer ? 'Save' : 'Add the peer'}
      </Button>
    </Stack>
  );
}

function PeerCard({ peer, onEdit }: { peer: Peer; onEdit: () => void }) {
  const qc = useQueryClient();
  const [removing, setRemoving] = useState(false);
  const [removeReplicas, setRemoveReplicas] = useState(false);
  const sync = useMutation({
    mutationFn: () => api.syncPeer(peer.name),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ['peers'] });
      qc.invalidateQueries({ queryKey: ['referentials'] });
      notifications.show({ title: `Synchronization with ${peer.name}`, message: r.message, color: STATUS_COLORS[r.status], autoClose: 10000 });
    },
    onError: (e: Error) => {
      qc.invalidateQueries({ queryKey: ['peers'] });
      notifications.show({ title: 'Synchronization failed', message: e.message, color: 'red', autoClose: 10000 });
    },
  });
  const remove = useMutation({
    mutationFn: () => api.deletePeer(peer.name, removeReplicas),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ['peers'] });
      qc.invalidateQueries({ queryKey: ['referentials'] });
      notifications.show({ message: `Peer removed${r.replicas_removed.length ? `, ${r.replicas_removed.length} replica(s) deleted` : ''}`, color: 'teal' });
      setRemoving(false);
    },
    onError: (e: Error) => notifications.show({ title: 'Not removed', message: e.message, color: 'red' }),
  });
  const report = peer.last_report;
  return (
    <Card padding="lg">
      <Stack gap="sm">
        <Group justify="space-between" align="flex-start">
          <Stack gap={2}>
            <Group gap="xs">
              <Title order={4}>{peer.name}</Title>
              {peer.status && (
                <Badge color={STATUS_COLORS[peer.status]} variant="light">
                  {peer.status}
                </Badge>
              )}
              {!peer.enabled && (
                <Badge color="gray" variant="light">
                  not scheduled
                </Badge>
              )}
            </Group>
            <Anchor href={peer.url} target="_blank" size="sm">
              {peer.url}
            </Anchor>
            <Text size="xs" c="dimmed">
              {peer.datasets.length ? `${peer.datasets.length} chosen referential(s)` : 'Every shared referential'}
              {peer.prefix && (
                <>
                  {' '}
                  · prefix <Code>{peer.prefix}</Code>
                </>
              )}{' '}
              · {peer.enabled ? describeCron(peer.schedule) : 'by hand only'}
              {peer.instance && ` · account ${peer.instance.account}, RefExposer ${peer.instance.refexposer}`}
            </Text>
          </Stack>
          <Group gap="xs">
            <Button leftSection={<IconRefresh size={16} />} onClick={() => sync.mutate()} loading={sync.isPending}>
              Synchronize now
            </Button>
            <Button variant="default" leftSection={<IconPencil size={16} />} onClick={onEdit}>
              Edit
            </Button>
            <Button variant="subtle" color="red" leftSection={<IconTrash size={16} />} onClick={() => setRemoving(true)}>
              Remove
            </Button>
          </Group>
        </Group>
        {peer.message && (
          <Alert color={STATUS_COLORS[peer.status ?? 'success']} variant="light" p="xs">
            <Text size="sm">
              {fmtRelative(peer.last_sync_at)}: {peer.message}
              {peer.next_sync_at && ` · next ${fmtRelative(peer.next_sync_at)}`}
            </Text>
            {report && (report.conflicts.length > 0 || report.errors.length > 0) && (
              <List size="xs" mt={4}>
                {[...report.conflicts, ...report.errors].map((c) => (
                  <List.Item key={c.id}>
                    <Code>{c.id}</Code>: {c.error}
                  </List.Item>
                ))}
              </List>
            )}
          </Alert>
        )}
        {peer.replicas.length > 0 && (
          <Table striped highlightOnHover verticalSpacing={4}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Referential</Table.Th>
                <Table.Th>Replica</Table.Th>
                <Table.Th ta="right">Version over there</Table.Th>
                <Table.Th ta="right">Rows here</Table.Th>
                <Table.Th>State</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {peer.replicas.map((r) => (
                <Table.Tr key={r.remote_id}>
                  <Table.Td>
                    <Text size="sm">{r.name ?? r.remote_id}</Text>
                    <Text size="xs" c="dimmed" ff="monospace">
                      {r.remote_id}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    {r.exists && r.local_id ? (
                      <Anchor component={Link} to={`/r/${r.local_id}`} size="sm" ff="monospace">
                        {r.local_id}
                      </Anchor>
                    ) : (
                      <Text size="sm" c="dimmed">
                        —
                      </Text>
                    )}
                  </Table.Td>
                  <Table.Td ta="right" className="tabular">
                    {r.remote_version ?? '—'}
                    {r.replicated_version != null && r.replicated_version !== r.remote_version && (
                      <Text span size="xs" c="orange">
                        {' '}
                        (replicating)
                      </Text>
                    )}
                  </Table.Td>
                  <Table.Td ta="right" className="tabular">
                    <Tooltip label={r.last_success_at ? `Replicated ${fmtDate(r.last_success_at)}` : 'Not replicated yet'}>
                      <span>{fmtNumber(r.rows)}</span>
                    </Tooltip>
                  </Table.Td>
                  <Table.Td>
                    {r.missing ? (
                      <Tooltip label="No longer shared by the peer (removed, or right withdrawn): the replica is kept as it is">
                        <Badge color="orange" variant="light">
                          missing over there
                        </Badge>
                      </Tooltip>
                    ) : (
                      <Badge color={r.status === 'ok' ? 'teal' : r.status ? 'red' : 'gray'} variant="light">
                        {r.status ?? 'pending'}
                      </Badge>
                    )}
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
      </Stack>
      <Modal opened={removing} onClose={() => setRemoving(false)} title={`Remove the peer ${peer.name}`}>
        <Stack>
          <Text size="sm">The synchronization stops. Without the option below, the replicas stay here with their last data, no longer updated.</Text>
          <Checkbox
            label="Also delete the replicas, their data and the token"
            checked={removeReplicas}
            onChange={(e) => setRemoveReplicas(e.currentTarget.checked)}
          />
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setRemoving(false)}>
              Cancel
            </Button>
            <Button color="red" onClick={() => remove.mutate()} loading={remove.isPending}>
              Remove
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Card>
  );
}

export default function PeersPage() {
  const { data, isLoading } = useQuery({ queryKey: ['peers'], queryFn: api.peers, refetchInterval: 15000 });
  const [editing, setEditing] = useState<Peer | 'new' | null>(null);
  return (
    <Stack gap="lg">
      <Group justify="space-between">
        <div>
          <Title order={2}>Synchronized instances</Title>
          <Text c="dimmed" size="sm">
            Referentials replicated from other RefExposer instances: definition, quality rules, SQL transformation and data.
          </Text>
        </div>
        <Button leftSection={<IconPlus size={16} />} onClick={() => setEditing('new')}>
          Add a peer
        </Button>
      </Group>
      <Alert variant="light" color="blue" icon={<IconWorldShare size={18} />}>
        <Text size="sm">
          <b>On the instance that shares</b>: create a service account (Users › Service account), give its group the read right on the
          referentials to share, then create an API token for it. <b>Here</b>: add the peer with its URL and this token. Each synchronization
          creates or updates the replicas (identifier: prefix + identifier over there), then downloads the published data of those whose
          version changed. Replicas are read-only here: their definition follows the peer.
        </Text>
      </Alert>
      {isLoading ? (
        <Loader />
      ) : !data?.peers.length ? (
        <EmptyState icon={<IconWorldShare size={28} />} title="No peer">
          Add another RefExposer instance to replicate its referentials here.
        </EmptyState>
      ) : (
        data.peers.map((p) => <PeerCard key={p.name} peer={p} onEdit={() => setEditing(p)} />)
      )}
      <Modal opened={editing !== null} onClose={() => setEditing(null)} size="lg" title={editing === 'new' ? 'Add a peer' : `Peer ${editing?.name ?? ''}`}>
        {editing !== null && <PeerForm peer={editing === 'new' ? undefined : editing} onDone={() => setEditing(null)} />}
      </Modal>
    </Stack>
  );
}
