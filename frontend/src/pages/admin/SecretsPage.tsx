import { Alert, Anchor, Badge, Button, Code, Group, Loader, Modal, PasswordInput, Stack, Table, TagsInput, Text, TextInput, Title } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconKey, IconLock, IconPlus, IconTrash } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../../api/client';
import type { SourceSecret } from '../../api/types';
import { CopyIcon, EmptyState } from '../../components/Common';
import { fmtDate } from '../../lib/format';

function SecretForm({ secret, onDone }: { secret?: SourceSecret; onDone: () => void }) {
  const qc = useQueryClient();
  const [name, setName] = useState(secret?.name ?? '');
  const [value, setValue] = useState('');
  const [description, setDescription] = useState(secret?.description ?? '');
  const [hosts, setHosts] = useState<string[]>(secret?.hosts ?? []);
  const save = useMutation({
    mutationFn: () =>
      secret
        ? api.updateSecret(secret.name, { value: value || null, description, hosts })
        : api.createSecret({ name: name.trim(), value, description: description || null, hosts }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['secrets'] });
      notifications.show({ message: secret ? 'Secret updated' : 'Secret created', color: 'teal' });
      onDone();
    },
    onError: (e: Error) => notifications.show({ title: 'Failed', message: e.message, color: 'red' }),
  });
  return (
    <Stack>
      <TextInput
        label="Name"
        description="Letters, digits, '.', '_' and '-'. It cannot be changed: definitions refer to it."
        placeholder="vendor-api-key"
        value={name}
        onChange={(e) => setName(e.currentTarget.value)}
        disabled={!!secret}
        required
      />
      <PasswordInput
        label={secret ? 'New value' : 'Value'}
        description={secret ? 'Empty: the stored value is kept. It is never shown again.' : 'Encrypted at rest, never shown again.'}
        value={value}
        onChange={(e) => setValue(e.currentTarget.value)}
        autoComplete="new-password"
        required={!secret}
      />
      <TextInput label="Description" placeholder="API key of the vendor feed (owner: SOC team)" value={description} onChange={(e) => setDescription(e.currentTarget.value)} />
      <TagsInput
        label="Allowed hosts"
        description="The secret is only sent to these hosts (e.g. api.vendor.com, *.vendor.com). Any host when empty: restrict it whenever possible."
        placeholder="api.vendor.com"
        value={hosts}
        onChange={setHosts}
        splitChars={[',', ' ']}
      />
      <Button onClick={() => save.mutate()} disabled={!name.trim() || (!secret && !value)} loading={save.isPending}>
        {secret ? 'Save' : 'Create the secret'}
      </Button>
    </Stack>
  );
}

export default function SecretsPage() {
  const qc = useQueryClient();
  const { data: secrets, isLoading } = useQuery({ queryKey: ['secrets'], queryFn: api.secrets });
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<SourceSecret | null>(null);
  const remove = useMutation({
    mutationFn: ({ name, force }: { name: string; force: boolean }) => api.deleteSecret(name, force),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['secrets'] });
      notifications.show({ message: 'Secret deleted', color: 'teal' });
    },
    onError: (e: Error) => notifications.show({ title: 'Not deleted', message: e.message, color: 'red' }),
  });

  return (
    <Stack gap="lg">
      <Group justify="space-between" align="flex-end">
        <div>
          <Title order={2}>Secrets</Title>
          <Text c="dimmed" size="sm">
            API keys, tokens and passwords of the sources. Definitions only hold a <Code>{'${secret:<name>}'}</Code> reference, resolved
            when a request is sent.
          </Text>
        </div>
        <Button leftSection={<IconPlus size={16} />} onClick={() => setCreating(true)}>
          New secret
        </Button>
      </Group>
      <Alert color="indigo" icon={<IconLock size={16} />} p="sm">
        <Text size="sm">
          Values are encrypted (AES-256-GCM, key derived from <Code>REFEX_SECRET_KEY</Code>) and write-only: neither the interface nor the
          API nor the MCP server ever returns them. Use them in header values, HTTP Basic authentication and Git tokens, with the key
          button of the <Anchor component={Link} to="/admin/referentials/new">referential editor</Anchor>.
        </Text>
      </Alert>
      {isLoading ? (
        <Loader />
      ) : !secrets?.length ? (
        <EmptyState icon={<IconKey size={28} />} title="No secret">
          Create one for each credential of your sources, then pick it in the editor of the referential.
        </EmptyState>
      ) : (
        <Table striped highlightOnHover verticalSpacing="sm">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Name</Table.Th>
              <Table.Th>Reference</Table.Th>
              <Table.Th>Allowed hosts</Table.Th>
              <Table.Th>Used by</Table.Th>
              <Table.Th>Updated</Table.Th>
              <Table.Th />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {secrets.map((s) => (
              <Table.Tr key={s.id}>
                <Table.Td>
                  <Text fw={600} size="sm">
                    {s.name}
                  </Text>
                  {s.description && (
                    <Text size="xs" c="dimmed">
                      {s.description}
                    </Text>
                  )}
                </Table.Td>
                <Table.Td>
                  <Group gap={4} wrap="nowrap">
                    <Code>{s.reference}</Code>
                    <CopyIcon value={s.reference} label="Copy the reference" />
                  </Group>
                </Table.Td>
                <Table.Td>
                  {s.hosts.length ? (
                    <Group gap={4}>
                      {s.hosts.map((h) => (
                        <Badge key={h} variant="light" tt="none" size="sm">
                          {h}
                        </Badge>
                      ))}
                    </Group>
                  ) : (
                    <Text size="xs" c="orange">
                      any host
                    </Text>
                  )}
                </Table.Td>
                <Table.Td>
                  {s.used_by.length ? (
                    <Group gap={4}>
                      {s.used_by.map((id) => (
                        <Badge key={id} component={Link} to={`/r/${id}`} variant="outline" color="gray" tt="none" size="sm" style={{ cursor: 'pointer' }}>
                          {id}
                        </Badge>
                      ))}
                    </Group>
                  ) : (
                    <Text size="xs" c="dimmed">
                      unused
                    </Text>
                  )}
                </Table.Td>
                <Table.Td>
                  <Text size="xs">{fmtDate(s.updated_at)}</Text>
                  <Text size="xs" c="dimmed">
                    {s.updated_by}
                  </Text>
                </Table.Td>
                <Table.Td>
                  <Group gap="xs" justify="flex-end" wrap="nowrap">
                    <Button size="compact-sm" variant="light" onClick={() => setEditing(s)}>
                      Edit
                    </Button>
                    <Button
                      size="compact-sm"
                      variant="subtle"
                      color="red"
                      leftSection={<IconTrash size={14} />}
                      loading={remove.isPending && remove.variables?.name === s.name}
                      onClick={() => {
                        const msg = s.used_by.length
                          ? `The secret '${s.name}' is used by ${s.used_by.join(', ')}: their updates will fail. Delete it anyway?`
                          : `Delete the secret '${s.name}'?`;
                        if (window.confirm(msg)) remove.mutate({ name: s.name, force: s.used_by.length > 0 });
                      }}
                    >
                      Delete
                    </Button>
                  </Group>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
      <Modal opened={creating} onClose={() => setCreating(false)} title={<Text fw={700}>New secret</Text>}>
        <SecretForm onDone={() => setCreating(false)} />
      </Modal>
      <Modal opened={!!editing} onClose={() => setEditing(null)} title={<Text fw={700}>Secret {editing?.name}</Text>}>
        {editing && <SecretForm key={editing.id} secret={editing} onDone={() => setEditing(null)} />}
      </Modal>
    </Stack>
  );
}
