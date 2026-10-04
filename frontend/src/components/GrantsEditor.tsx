import { ActionIcon, Alert, Badge, Button, Group, Loader, SegmentedControl, Select, Stack, Table, Text, Tooltip } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconTrash, IconUser, IconUsersGroup, IconWorld } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api/client';
import type { GrantInfo } from '../api/types';
import { useReferentials } from '../api/hooks';

export const LEVELS = [
  { value: 'read', label: 'Read' },
  { value: 'manage', label: 'Manage' },
];
export const LEVEL_HELP = 'Read: browse, search, export, SQL (advanced users) · Manage: read + run/cancel updates, manual imports, editing internal referentials';

export function LevelBadge({ level }: { level: string }) {
  return (
    <Badge variant="light" color={level === 'manage' ? 'orange' : 'blue'} size="sm">
      {level === 'manage' ? 'Manage' : 'Read'}
    </Badge>
  );
}

type Scope = { referentialId: string } | { userId: number } | { groupId: number };

/** List and edit grants for a fixed referential, user or group. */
export default function GrantsEditor({ scope }: { scope: Scope }) {
  const qc = useQueryClient();
  const params =
    'referentialId' in scope ? { referential_id: scope.referentialId } : 'userId' in scope ? { user_id: scope.userId } : { group_id: scope.groupId };
  const key = ['grants', params];
  const { data: grants, isLoading } = useQuery({ queryKey: key, queryFn: () => api.grants(params) });
  const { data: refs } = useReferentials();
  const fixedRef = 'referentialId' in scope;
  const { data: users } = useQuery({ queryKey: ['admin-users'], queryFn: api.users, enabled: fixedRef });
  const { data: groups } = useQuery({ queryKey: ['admin-groups'], queryFn: api.groups, enabled: fixedRef });

  const [subjectType, setSubjectType] = useState<'user' | 'group'>('group');
  const [subject, setSubject] = useState<string | null>(null);
  const [refId, setRefId] = useState<string | null>(null);
  const [level, setLevel] = useState<string>('read');

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['grants'] });
    qc.invalidateQueries({ queryKey: ['admin-users'] });
    qc.invalidateQueries({ queryKey: ['admin-groups'] });
  };
  const onError = (e: Error) => notifications.show({ title: 'Failed', message: e.message, color: 'red' });
  const save = useMutation({ mutationFn: api.setGrant, onSuccess: invalidate, onError });
  const remove = useMutation({ mutationFn: api.deleteGrant, onSuccess: invalidate, onError });

  const add = () => {
    const body: { referential_id: string; user_id?: number; group_id?: number; level: string } = {
      referential_id: fixedRef ? scope.referentialId : refId!,
      level,
    };
    if ('userId' in scope) body.user_id = scope.userId;
    else if ('groupId' in scope) body.group_id = scope.groupId;
    else if (subjectType === 'user') body.user_id = Number(subject);
    else body.group_id = Number(subject);
    save.mutate(body, { onSuccess: () => (fixedRef ? setSubject(null) : setRefId(null)) });
  };

  const isInherited = (g: GrantInfo) => fixedRef && g.referential_id === '*';
  const subjectLabel = (g: GrantInfo) =>
    g.user ? (
      <Group gap={6} wrap="nowrap">
        <IconUser size={14} />
        <Text size="sm">{g.user.display_name ? `${g.user.display_name} (${g.user.username})` : g.user.username}</Text>
      </Group>
    ) : (
      <Group gap={6} wrap="nowrap">
        <IconUsersGroup size={14} />
        <Text size="sm">{g.group?.name}</Text>
      </Group>
    );
  const refLabel = (g: GrantInfo) =>
    g.referential_id === '*' ? (
      <Group gap={6} wrap="nowrap">
        <IconWorld size={14} />
        <Text size="sm" fw={600}>
          All referentials
        </Text>
      </Group>
    ) : (
      <Text size="sm" component={Link} to={`/r/${g.referential_id}`} c="inherit">
        {g.referential_name ?? g.referential_id}
      </Text>
    );

  const subjectOptions =
    subjectType === 'user'
      ? (users ?? []).filter((u) => u.role !== 'admin').map((u) => ({ value: String(u.id), label: u.display_name ? `${u.display_name} (${u.username})` : u.username }))
      : (groups ?? []).map((g) => ({ value: String(g.id), label: g.name }));
  const refOptions = [{ value: '*', label: '★ All referentials' }, ...(refs ?? []).map((r) => ({ value: r.id, label: r.name }))];

  return (
    <Stack gap="sm">
      {isLoading ? (
        <Loader size="sm" />
      ) : !grants?.length ? (
        <Text size="sm" c="dimmed">
          No access granted.
        </Text>
      ) : (
        <Table verticalSpacing={6} fz="sm">
          <Table.Tbody>
            {grants.map((g) => (
              <Table.Tr key={g.id}>
                <Table.Td>{fixedRef ? subjectLabel(g) : refLabel(g)}</Table.Td>
                <Table.Td w={150}>
                  {isInherited(g) ? (
                    <Tooltip label="Global right on all referentials: edit it from the user or group page">
                      <Group gap={4} wrap="nowrap">
                        <LevelBadge level={g.level} />
                        <Badge size="xs" variant="outline" color="gray">
                          global
                        </Badge>
                      </Group>
                    </Tooltip>
                  ) : (
                    <Select
                      size="xs"
                      data={LEVELS}
                      value={g.level}
                      allowDeselect={false}
                      onChange={(v) =>
                        v &&
                        save.mutate({ referential_id: g.referential_id, user_id: g.user?.id, group_id: g.group?.id, level: v })
                      }
                    />
                  )}
                </Table.Td>
                <Table.Td w={40}>
                  {!isInherited(g) && (
                    <Tooltip label="Remove this right">
                      <ActionIcon variant="subtle" color="red" onClick={() => remove.mutate(g.id)} aria-label="Remove">
                        <IconTrash size={16} />
                      </ActionIcon>
                    </Tooltip>
                  )}
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}

      <Group gap="xs" align="flex-end" wrap="wrap">
        {fixedRef ? (
          <>
            <SegmentedControl
              size="xs"
              value={subjectType}
              onChange={(v) => {
                setSubjectType(v as 'user' | 'group');
                setSubject(null);
              }}
              data={[
                { value: 'group', label: 'Group' },
                { value: 'user', label: 'User' },
              ]}
            />
            <Select
              size="xs"
              placeholder={subjectType === 'user' ? 'Choose a user' : 'Choose a group'}
              data={subjectOptions}
              value={subject}
              onChange={setSubject}
              searchable
              w={240}
              nothingFoundMessage={subjectType === 'group' ? 'No group' : 'No user'}
            />
          </>
        ) : (
          <Select size="xs" placeholder="Choose a referential" data={refOptions} value={refId} onChange={setRefId} searchable w={280} />
        )}
        <Select size="xs" data={LEVELS} value={level} onChange={(v) => setLevel(v ?? 'read')} allowDeselect={false} w={110} />
        <Button size="xs" onClick={add} disabled={fixedRef ? !subject : !refId} loading={save.isPending}>
          Grant
        </Button>
      </Group>
      <Text size="xs" c="dimmed">
        {LEVEL_HELP}
      </Text>
      {fixedRef && subjectType === 'user' && (
        <Alert variant="light" color="gray" p="xs">
          <Text size="xs">Administrators can access every referential and are not listed here.</Text>
        </Alert>
      )}
    </Stack>
  );
}
