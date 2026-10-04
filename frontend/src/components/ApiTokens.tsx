import { ActionIcon, Alert, Badge, Button, Code, Group, Modal, NumberInput, Stack, Table, Text, TextInput, Tooltip } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconKey, IconPlus, IconTrash } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';
import { absoluteUrl } from '../api/client';
import type { ApiTokenInfo } from '../api/types';
import { fmtDate, fmtRelative } from '../lib/format';
import { CodeSnippet, CopyIcon } from './Common';

/** API calls of a token list: the current user's own tokens, or those of a service account (admins). */
export interface TokenSource {
  queryKey: unknown[];
  list: () => Promise<ApiTokenInfo[]>;
  /** absent: tokens cannot be created from here */
  create?: (name: string, expiresInDays: number | null) => Promise<ApiTokenInfo>;
  remove: (id: number) => Promise<unknown>;
}

function NewTokenModal({ source, opened, onClose, hint }: { source: TokenSource; opened: boolean; onClose: () => void; hint: ReactNode }) {
  const qc = useQueryClient();
  const [name, setName] = useState('');
  const [days, setDays] = useState<number | string>(90);
  const [created, setCreated] = useState<ApiTokenInfo | null>(null);
  const create = useMutation({
    mutationFn: () => source.create!(name.trim(), days === '' ? null : Number(days)),
    onSuccess: (t) => {
      setCreated(t);
      qc.invalidateQueries({ queryKey: source.queryKey });
      qc.invalidateQueries({ queryKey: ['admin-users'] });
    },
    onError: (e: Error) => notifications.show({ title: 'Failed', message: e.message, color: 'red' }),
  });
  const close = () => {
    setCreated(null);
    setName('');
    onClose();
  };
  return (
    <Modal opened={opened} onClose={close} title={<Text fw={700}>New API token</Text>} size="lg">
      {created ? (
        <Stack>
          <Alert color="orange" title="Copy this token now">
            It will never be shown again: only its fingerprint (SHA3-256) is stored.
          </Alert>
          <Group gap="xs" wrap="nowrap">
            <Code block style={{ flex: 1, wordBreak: 'break-all' }}>
              {created.token}
            </Code>
            <CopyIcon value={created.token!} label="Copy the token" />
          </Group>
          <CodeSnippet title="Usage" code={`curl -H "Authorization: Bearer ${created.token}" \\\n  "${absoluteUrl('/api/referentials')}"`} />
          <Button onClick={close}>I have copied the token</Button>
        </Stack>
      ) : (
        <Stack>
          <TextInput label="Name" description="What the token is for (script, tool…)" value={name} onChange={(e) => setName(e.currentTarget.value)} required />
          <NumberInput
            label="Validity (days)"
            description="Leave empty for a token that never expires (if allowed)"
            value={days}
            onChange={setDays}
            min={1}
            max={3650}
          />
          <Text size="xs" c="dimmed">
            {hint}
          </Text>
          <Button onClick={() => create.mutate()} disabled={!name.trim()} loading={create.isPending}>
            Create
          </Button>
        </Stack>
      )}
    </Modal>
  );
}

export default function ApiTokens({ source, hint }: { source: TokenSource; hint: ReactNode }) {
  const qc = useQueryClient();
  const [opened, setOpened] = useState(false);
  const { data: tokens } = useQuery({ queryKey: source.queryKey, queryFn: source.list });
  const del = useMutation({
    mutationFn: source.remove,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: source.queryKey });
      qc.invalidateQueries({ queryKey: ['admin-users'] });
      notifications.show({ message: 'Token revoked', color: 'teal' });
    },
    onError: (e: Error) => notifications.show({ title: 'Failed', message: e.message, color: 'red' }),
  });
  return (
    <Stack gap="sm">
      {source.create && (
        <Group justify="flex-end">
          <Button size="xs" leftSection={<IconPlus size={14} />} onClick={() => setOpened(true)}>
            New token
          </Button>
        </Group>
      )}
      {!tokens?.length ? (
        <Text size="sm" c="dimmed">
          No token.
        </Text>
      ) : (
        <Table.ScrollContainer minWidth={560}>
          <Table fz="sm" verticalSpacing="xs">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Name</Table.Th>
                <Table.Th>Prefix</Table.Th>
                <Table.Th>Created</Table.Th>
                <Table.Th>Expires</Table.Th>
                <Table.Th>Last used</Table.Th>
                <Table.Th />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {tokens.map((t) => (
                <Table.Tr key={t.id}>
                  <Table.Td>
                    <Group gap={6} wrap="nowrap">
                      <IconKey size={14} />
                      {t.name}
                      {t.expired && (
                        <Badge color="red" size="xs" variant="light">
                          expired
                        </Badge>
                      )}
                    </Group>
                  </Table.Td>
                  <Table.Td>
                    <Code>{t.prefix}…</Code>
                  </Table.Td>
                  <Table.Td>{fmtDate(t.created_at)}</Table.Td>
                  <Table.Td>{t.expires_at ? fmtDate(t.expires_at) : 'never'}</Table.Td>
                  <Table.Td>{t.last_used_at ? fmtRelative(t.last_used_at) : 'never'}</Table.Td>
                  <Table.Td w={40}>
                    <Tooltip label="Revoke">
                      <ActionIcon variant="subtle" color="red" onClick={() => del.mutate(t.id)} aria-label="Revoke">
                        <IconTrash size={16} />
                      </ActionIcon>
                    </Tooltip>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      )}
      {source.create && <NewTokenModal source={source} opened={opened} onClose={() => setOpened(false)} hint={hint} />}
    </Stack>
  );
}
