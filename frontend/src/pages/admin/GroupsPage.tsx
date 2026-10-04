import { Badge, Button, Card, Divider, Drawer, Group, Loader, Modal, MultiSelect, SimpleGrid, Stack, Text, TextInput, Title } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconPlus, IconTrash, IconUsersGroup } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../../api/client';
import type { AdminGroup } from '../../api/types';
import { EmptyState } from '../../components/Common';
import GrantsEditor from '../../components/GrantsEditor';

function GroupForm({ group, onDone }: { group?: AdminGroup; onDone: () => void }) {
  const qc = useQueryClient();
  const { data: users } = useQuery({ queryKey: ['admin-users'], queryFn: api.users });
  const [name, setName] = useState(group?.name ?? '');
  const [description, setDescription] = useState(group?.description ?? '');
  const [members, setMembers] = useState<string[]>(group?.members.map((m) => String(m.id)) ?? []);
  const save = useMutation({
    mutationFn: () => {
      const body = { name, description: description || null, ...(group && group.source !== 'local' ? {} : { member_ids: members.map(Number) }) };
      return group ? api.updateGroup(group.id, body) : api.createGroup(body);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-groups'] });
      qc.invalidateQueries({ queryKey: ['admin-users'] });
      notifications.show({ message: group ? 'Group updated' : 'Group created', color: 'teal' });
      onDone();
    },
    onError: (e: Error) => notifications.show({ title: 'Failed', message: e.message, color: 'red' }),
  });
  return (
    <Stack>
      <TextInput label="Name" value={name} onChange={(e) => setName(e.currentTarget.value)} required />
      <TextInput label="Description" value={description} onChange={(e) => setDescription(e.currentTarget.value)} />
      <MultiSelect
        disabled={!!group && group.source !== 'local'}
        description={group && group.source !== 'local' ? `Members synchronized from ${group.source === 'ldap' ? 'the LDAP directory' : 'OpenID Connect'} at each sign-in` : undefined}
        label="Membres"
        data={(users ?? []).map((u) => ({ value: String(u.id), label: u.display_name ? `${u.display_name} (${u.username})` : u.username }))}
        value={members}
        onChange={setMembers}
        searchable
        clearable
      />
      <Button onClick={() => save.mutate()} disabled={!name.trim()} loading={save.isPending}>
        {group ? 'Save' : 'Create the group'}
      </Button>
    </Stack>
  );
}

export default function GroupsPage() {
  const qc = useQueryClient();
  const { data: groups, isLoading } = useQuery({ queryKey: ['admin-groups'], queryFn: api.groups });
  const [creating, setCreating] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const editing = groups?.find((g) => g.id === editingId) ?? null;
  const remove = useMutation({
    mutationFn: api.deleteGroup,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-groups'] });
      qc.invalidateQueries({ queryKey: ['admin-users'] });
      setEditingId(null);
      notifications.show({ message: 'Group deleted', color: 'teal' });
    },
  });

  return (
    <Stack gap="lg">
      <Group justify="space-between" align="flex-end">
        <div>
          <Title order={2}>Groups</Title>
          <Text c="dimmed" size="sm">
            Gather users to give them rights at once.
          </Text>
        </div>
        <Button leftSection={<IconPlus size={16} />} onClick={() => setCreating(true)}>
          New group
        </Button>
      </Group>
      {isLoading ? (
        <Loader />
      ) : !groups?.length ? (
        <EmptyState icon={<IconUsersGroup size={28} />} title="No group">
          For example, create a "Security team" group and give it read access to the cybersecurity referentials.
        </EmptyState>
      ) : (
        <SimpleGrid cols={{ base: 1, md: 2, xl: 3 }}>
          {groups.map((g) => (
            <Card key={g.id} padding="lg" className="ref-card" style={{ cursor: 'pointer' }} onClick={() => setEditingId(g.id)}>
              <Group justify="space-between" mb={4}>
                <Group gap="xs">
                  <IconUsersGroup size={18} />
                  <Text fw={700}>{g.name}</Text>
                  {g.source !== 'local' && (
                    <Badge size="xs" variant="outline" color="cyan">
                      {g.source === 'ldap' ? 'LDAP' : 'OIDC'}
                    </Badge>
                  )}
                </Group>
                <Badge variant="light">{g.grant_count} right(s)</Badge>
              </Group>
              <Text size="sm" c="dimmed" mb="sm" lineClamp={2}>
                {g.description || 'No description.'}
              </Text>
              <Group gap={4}>
                {g.members.length === 0 ? (
                  <Text size="xs" c="dimmed">
                    No member
                  </Text>
                ) : (
                  g.members.slice(0, 8).map((m) => (
                    <Badge key={m.id} variant="outline" color="gray" tt="none" size="sm">
                      {m.username}
                    </Badge>
                  ))
                )}
                {g.members.length > 8 && (
                  <Text size="xs" c="dimmed">
                    +{g.members.length - 8}
                  </Text>
                )}
              </Group>
            </Card>
          ))}
        </SimpleGrid>
      )}

      <Modal opened={creating} onClose={() => setCreating(false)} title={<Text fw={700}>New group</Text>}>
        <GroupForm onDone={() => setCreating(false)} />
      </Modal>
      <Drawer opened={editing != null} onClose={() => setEditingId(null)} position="right" size="lg" title={<Text fw={700}>{editing?.name}</Text>}>
        {editing && (
          <Stack>
            <GroupForm key={editing.id} group={editing} onDone={() => setEditingId(null)} />
            <Divider label="Referential access" labelPosition="left" mt="md" />
            <GrantsEditor scope={{ groupId: editing.id }} />
            <Divider mt="md" />
            <Button variant="light" color="red" leftSection={<IconTrash size={16} />} onClick={() => remove.mutate(editing.id)} loading={remove.isPending}>
              Delete the group
            </Button>
          </Stack>
        )}
      </Drawer>
    </Stack>
  );
}
