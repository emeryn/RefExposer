import { Badge, Card, Code, Group, Pagination, Select, Stack, Table, Text, TextInput, Title, Tooltip } from '@mantine/core';
import { useDebouncedValue } from '@mantine/hooks';
import { IconSearch } from '@tabler/icons-react';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../../api/client';
import { fmtDate, fmtNumber } from '../../lib/format';

const PAGE = 50;

const ACTIONS: Record<string, string> = {
  'auth.login': 'Sign-in',
  'auth.access_request': 'Access request',
  'auth.logout': 'Sign-out',
  'auth.password_change': 'Password change',
  'token.create': 'Token created',
  'token.delete': 'Token revoked',
  'user.create': 'User created',
  'user.update': 'User changed',
  'user.delete': 'User deleted',
  'user.approve': 'Access approved',
  'user.reject': 'Access refused',
  'user.sessions_revoke': 'Sessions closed',
  'user.bootstrap': 'Initial admin created',
  'user.bootstrap_reset': 'Admin reset',
  'group.create': 'Group created',
  'group.update': 'Group changed',
  'group.delete': 'Group deleted',
  'grant.set': 'Right granted',
  'grant.delete': 'Right removed',
  'referential.refresh': 'Update started',
  'referential.create': 'Referential created',
  'referential.update': 'Referential configured',
  'referential.delete': 'Referential deleted',
  'referential.upload': 'Source file uploaded',
  'referential.import': 'Manual import',
  'referential.unpin': 'Automatic updates resumed',
  'referential.download': 'Download',
  'referential.cancel': 'Update cancelled',
  'referential.export': 'Export',
  'sql.query': 'SQL query',
  'internal.create': 'Internal referential created',
  'internal.update': 'Internal referential columns changed',
  'internal.delete': 'Internal referential deleted',
  'record.create': 'Row added',
  'record.update': 'Row changed',
  'record.delete': 'Row deleted',
  'record.bulk': 'Rows synchronized (API)',
  'record.import': 'Rows loaded from a file',
  'settings.proxy': 'Proxy settings',
  'settings.ldap': 'LDAP settings',
  'settings.oidc': 'OpenID Connect settings',
  'system.reload': 'Configuration reloaded',
};

const CATEGORIES = [
  { value: 'auth', label: 'Authentication' },
  { value: 'token', label: 'API tokens' },
  { value: 'user', label: 'Users' },
  { value: 'group', label: 'Groups' },
  { value: 'grant', label: 'Rights' },
  { value: 'referential', label: 'Referentials' },
  { value: 'internal', label: 'Internal referentials' },
  { value: 'record', label: 'Internal rows' },
  { value: 'settings', label: 'Settings' },
  { value: 'sql', label: 'SQL' },
  { value: 'system', label: 'System' },
];

function Detail({ detail }: { detail: Record<string, unknown> | null }) {
  if (!detail || !Object.keys(detail).length) return null;
  if (typeof detail.sql === 'string')
    return (
      <Tooltip label={<Code block>{detail.sql}</Code>} multiline w={500}>
        <Text size="xs" ff="monospace" lineClamp={1} maw={420}>
          {detail.sql}
        </Text>
      </Tooltip>
    );
  return (
    <Text size="xs" c="dimmed" lineClamp={2} maw={420}>
      {Object.entries(detail)
        .map(([k, v]) => `${k}: ${typeof v === 'object' ? JSON.stringify(v) : String(v)}`)
        .join(' · ')}
    </Text>
  );
}

export default function AuditPage() {
  const [page, setPage] = useState(1);
  const [user, setUser] = useState('');
  const [debouncedUser] = useDebouncedValue(user, 300);
  const [action, setAction] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const params = new URLSearchParams({ limit: String(PAGE), offset: String((page - 1) * PAGE) });
  if (debouncedUser) params.set('username', debouncedUser);
  if (action) params.set('action', action);
  if (success) params.set('success', success);
  const { data } = useQuery({
    queryKey: ['audit', params.toString()],
    queryFn: () => api.audit(params),
    placeholderData: keepPreviousData,
    refetchInterval: 10000,
  });

  return (
    <Stack gap="lg">
      <div>
        <Title order={2}>Audit log</Title>
        <Text c="dimmed" size="sm">
          Sign-ins, administration, updates, exports and SQL queries.
        </Text>
      </div>
      <Group gap="sm">
        <TextInput
          placeholder="User"
          leftSection={<IconSearch size={16} />}
          value={user}
          onChange={(e) => {
            setUser(e.currentTarget.value);
            setPage(1);
          }}
          w={200}
        />
        <Select placeholder="All actions" data={CATEGORIES} value={action} onChange={(v) => { setAction(v); setPage(1); }} clearable w={220} />
        <Select
          placeholder="All results"
          data={[
            { value: 'true', label: 'Successes' },
            { value: 'false', label: 'Failures' },
          ]}
          value={success}
          onChange={(v) => { setSuccess(v); setPage(1); }}
          clearable
          w={180}
        />
      </Group>
      <Card padding={0}>
        <Table.ScrollContainer minWidth={900}>
          <Table verticalSpacing="xs" fz="sm" striped>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Date</Table.Th>
                <Table.Th>User</Table.Th>
                <Table.Th>Action</Table.Th>
                <Table.Th>Target</Table.Th>
                <Table.Th>Detail</Table.Th>
                <Table.Th>IP</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {data?.rows.map((a) => (
                <Table.Tr key={a.id}>
                  <Table.Td style={{ whiteSpace: 'nowrap' }}>{fmtDate(a.at)}</Table.Td>
                  <Table.Td>{a.username ?? '—'}</Table.Td>
                  <Table.Td>
                    <Badge variant="light" color={a.success ? 'blue' : 'red'} tt="none">
                      {ACTIONS[a.action] ?? a.action}
                      {!a.success && ' (failed)'}
                    </Badge>
                  </Table.Td>
                  <Table.Td>
                    <Text size="xs" ff="monospace">
                      {a.target ?? ''}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <Detail detail={a.detail} />
                  </Table.Td>
                  <Table.Td>
                    <Text size="xs" c="dimmed">
                      {a.ip}
                    </Text>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      </Card>
      <Group justify="space-between">
        <Text size="sm" c="dimmed">
          {data ? `${fmtNumber(data.total)} event(s)` : ''}
        </Text>
        <Pagination total={Math.max(1, Math.ceil((data?.total ?? 0) / PAGE))} value={page} onChange={setPage} size="sm" />
      </Group>
    </Stack>
  );
}
