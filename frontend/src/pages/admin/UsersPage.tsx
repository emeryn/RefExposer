import {
  Alert,
  Badge,
  Button,
  Card,
  Code,
  Tooltip,
  Divider,
  Drawer,
  Group,
  Loader,
  Menu,
  Modal,
  MultiSelect,
  PasswordInput,
  SegmentedControl,
  Stack,
  Switch,
  Table,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import {
  IconCheck,
  IconDots,
  IconLock,
  IconLockOpen,
  IconLogout,
  IconPlus,
  IconRobot,
  IconSearch,
  IconTrash,
  IconUserCircle,
  IconUserQuestion,
  IconX,
} from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { api } from '../../api/client';
import type { AdminUser, Role } from '../../api/types';
import { useAuth } from '../../auth/AuthContext';
import { PasswordStrength } from '../../auth/PasswordChange';
import ApiTokens from '../../components/ApiTokens';
import GrantsEditor from '../../components/GrantsEditor';
import { fmtDate, fmtRelative } from '../../lib/format';

function useAdminMutation<T>(fn: (arg: T) => Promise<unknown>, success: string, after?: () => void) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-users'] });
      qc.invalidateQueries({ queryKey: ['admin-groups'] });
      notifications.show({ message: success, color: 'teal' });
      after?.();
    },
    onError: (e: Error) => notifications.show({ title: 'Failed', message: e.message, color: 'red' }),
  });
}

function UserForm({ user, onDone }: { user?: AdminUser; onDone: () => void }) {
  const { data: groups } = useQuery({ queryKey: ['admin-groups'], queryFn: api.groups });
  const [username, setUsername] = useState(user?.username ?? '');
  const [displayName, setDisplayName] = useState(user?.display_name ?? '');
  const [email, setEmail] = useState(user?.email ?? '');
  const [role, setRole] = useState<string>(user?.role ?? 'user');
  const service = !!user?.is_service;
  const external = !!user && user.auth_source !== 'local' && !service;
  const [groupIds, setGroupIds] = useState<string[]>(user?.groups.filter((g) => g.source === 'local').map((g) => String(g.id)) ?? []);
  const [password, setPassword] = useState('');
  const [mustChange, setMustChange] = useState(true);
  const [active, setActive] = useState(user?.is_active ?? true);

  const save = useAdminMutation(
    () => {
      const body: Record<string, unknown> = {
        display_name: displayName || null,
        email: email || null,
        group_ids: groupIds.map(Number),
      };
      if (!service) body.role = role;
      if (!user) return api.createUser({ ...body, username, password, must_change_password: mustChange });
      body.is_active = active;
      if (password) {
        body.password = password;
        body.must_change_password = mustChange;
      }
      return api.updateUser(user.id, body);
    },
    user ? 'User updated' : 'User created',
    onDone,
  );

  return (
    <Stack>
      <TextInput
        label="Username"
        description={user ? undefined : 'Minuscules, chiffres, . _ @ -'}
        value={username}
        onChange={(e) => setUsername(e.currentTarget.value.toLowerCase())}
        disabled={!!user}
        required
      />
      <Group grow>
        <TextInput label="Display name" value={displayName} onChange={(e) => setDisplayName(e.currentTarget.value)} />
        <TextInput label="Email" type="email" value={email} onChange={(e) => setEmail(e.currentTarget.value)} />
      </Group>
      {!service && (
        <div>
          <Text size="sm" fw={500} mb={4}>
            Role
          </Text>
          <SegmentedControl
            value={role}
            onChange={setRole}
            data={ROLES}
          />
          <Text size="xs" c="dimmed" mt={4}>
            {ROLE_HELP[role as Role]}
          </Text>
        </div>
      )}
      {external && user!.groups.some((g) => g.source !== 'local') && (
        <div>
          <Text size="sm" fw={500}>
            Synchronized groups ({SOURCES[user!.auth_source]})
          </Text>
          <Group gap={4} mt={4}>
            {user!.groups
              .filter((g) => g.source !== 'local')
              .map((g) => (
                <Badge key={g.id} variant="outline" color="cyan" tt="none">
                  {g.name}
                </Badge>
              ))}
          </Group>
        </div>
      )}
      <MultiSelect
        label={external ? 'Additional local groups' : 'Groups'}
        data={(groups ?? []).filter((g) => g.source === 'local').map((g) => ({ value: String(g.id), label: g.name }))}
        value={groupIds}
        onChange={setGroupIds}
        searchable
        clearable
      />
      {service ? (
        <Alert color="violet" p="xs" icon={<IconRobot size={16} />}>
          <Text size="xs">
            Service account: it never signs in to the interface and calls the API with the tokens below, with the rights given to
            it (directly or through its groups), like a user.
          </Text>
        </Alert>
      ) : external ? (
        <Alert color="gray" p="xs">
          <Text size="xs">
            {SOURCES[user!.auth_source]} account: the password, the name and the synchronized groups are managed by the identity provider
            {user!.external_id && (
              <>
                {' '}
                (<Code fz={10}>{user!.external_id}</Code>)
              </>
            )}
            .
          </Text>
        </Alert>
      ) : (
        <>
          <Divider label={user ? 'Reset the password (optional)' : 'Initial password'} labelPosition="left" />
          <PasswordInput value={password} onChange={(e) => setPassword(e.currentTarget.value)} autoComplete="new-password" required={!user} />
          {password && <PasswordStrength password={password} username={username} />}
          {(!user || password) && (
            <Switch label="Require a change at next sign-in" checked={mustChange} onChange={(e) => setMustChange(e.currentTarget.checked)} />
          )}
        </>
      )}
      {user && <Switch label="Active account" checked={active} onChange={(e) => setActive(e.currentTarget.checked)} />}
      <Button onClick={() => save.mutate(undefined)} loading={save.isPending} disabled={!username || (!user && !password)}>
        {user ? 'Save' : 'Create the user'}
      </Button>
    </Stack>
  );
}

const SOURCES = { local: 'Local', ldap: 'LDAP directory', oidc: 'OpenID Connect', service: 'Service account' } as const;

function ServiceAccountForm({ onDone }: { onDone: () => void }) {
  const { data: groups } = useQuery({ queryKey: ['admin-groups'], queryFn: api.groups });
  const [username, setUsername] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [email, setEmail] = useState('');
  const [groupIds, setGroupIds] = useState<string[]>([]);
  const save = useAdminMutation(
    () => api.createServiceAccount({ username, display_name: displayName || null, email: email || null, group_ids: groupIds.map(Number) }),
    'Service account created: create its API token from its page',
    onDone,
  );
  return (
    <Stack>
      <Text size="sm" c="dimmed">
        For a tool or a script (SIEM, enrichment, CI…). A service account cannot sign in to the interface: it only calls the API with
        tokens that administrators create from its page. It lists and queries the referentials it is granted, like a user.
      </Text>
      <TextInput
        label="Account name"
        description="Lowercase letters, digits, . _ @ -"
        placeholder="siem-connector"
        value={username}
        onChange={(e) => setUsername(e.currentTarget.value.toLowerCase())}
        required
      />
      <TextInput label="Purpose" placeholder="SIEM enrichment" value={displayName} onChange={(e) => setDisplayName(e.currentTarget.value)} />
      <TextInput label="Contact email" description="Team owning the tool" type="email" value={email} onChange={(e) => setEmail(e.currentTarget.value)} />
      <MultiSelect
        label="Groups"
        description="The account gets the rights of its groups; direct rights can be added from its page."
        data={(groups ?? []).filter((g) => g.source === 'local').map((g) => ({ value: String(g.id), label: g.name }))}
        value={groupIds}
        onChange={setGroupIds}
        searchable
        clearable
      />
      <Button onClick={() => save.mutate(undefined)} loading={save.isPending} disabled={!username}>
        Create the service account
      </Button>
    </Stack>
  );
}
const ROLES = [
  { value: 'user', label: 'User' },
  { value: 'advanced', label: 'Advanced user' },
  { value: 'admin', label: 'Administrator' },
];
const ROLE_HELP: Record<Role, string> = {
  admin: 'Full access: all referentials, users, rights, settings and system.',
  advanced: 'Granted referentials only, and can create internal referentials (and manage the ones they create).',
  user: 'Access limited to the granted referentials (directly or through groups).',
};
const ROLE_BADGE: Record<Role, { label: string; color: string }> = {
  admin: { label: 'Admin', color: 'red' },
  advanced: { label: 'Advanced', color: 'orange' },
  user: { label: 'User', color: 'gray' },
};

function PendingRequests({ users }: { users: AdminUser[] }) {
  const qc = useQueryClient();
  const { data: groups } = useQuery({ queryKey: ['admin-groups'], queryFn: api.groups });
  const [approving, setApproving] = useState<AdminUser | null>(null);
  const [rejecting, setRejecting] = useState<AdminUser | null>(null);
  const [role, setRole] = useState('user');
  const [groupIds, setGroupIds] = useState<string[]>([]);
  const [reason, setReason] = useState('');
  const done = (msg: string) => {
    notifications.show({ message: msg, color: 'teal' });
    qc.invalidateQueries({ queryKey: ['admin-users'] });
    qc.invalidateQueries({ queryKey: ['me'] });
    setApproving(null);
    setRejecting(null);
  };
  const onError = (e: Error) => notifications.show({ title: 'Failed', message: e.message, color: 'red' });
  const approve = useMutation({ mutationFn: (u: AdminUser) => api.approveUser(u.id, { role, group_ids: groupIds.map(Number) }), onSuccess: () => done('Access approved'), onError });
  const reject = useMutation({ mutationFn: (u: AdminUser) => api.rejectUser(u.id, reason || undefined), onSuccess: () => done('Request refused'), onError });
  if (!users.length) return null;
  return (
    <Card padding="lg" withBorder style={{ borderColor: 'var(--mantine-color-orange-4)' }}>
      <Group gap="xs" mb="sm">
        <IconUserQuestion size={20} color="var(--mantine-color-orange-6)" />
        <Title order={5}>Pending access requests ({users.length})</Title>
      </Group>
      <Text size="sm" c="dimmed" mb="sm">
        These people signed in through the directory or OpenID Connect. Their access stays blocked until an administrator
        approves it. Remember to give them rights (directly or through a group) on the useful referentials.
      </Text>
      <Stack gap="xs">
        {users.map((u) => (
          <Group key={u.id} justify="space-between" wrap="nowrap" p="xs" style={{ borderRadius: 8, background: 'var(--app-subtle-bg)' }}>
            <div style={{ minWidth: 0 }}>
              <Group gap={6}>
                <Text size="sm" fw={600}>
                  {u.display_name || u.username}
                </Text>
                <Badge size="xs" variant="outline" color="cyan">
                  {u.auth_source === 'ldap' ? 'LDAP' : 'OIDC'}
                </Badge>
                {u.role === 'admin' && (
                  <Tooltip label="Member of the configured administrators group">
                    <Badge size="xs" color="red">
                      admin
                    </Badge>
                  </Tooltip>
                )}
              </Group>
              <Text size="xs" c="dimmed">
                {u.username}
                {u.email && ` · ${u.email}`} · demande {fmtRelative(u.created_at)}
              </Text>
              {u.groups.length > 0 && (
                <Group gap={4} mt={2}>
                  {u.groups.map((g) => (
                    <Badge key={g.id} size="xs" variant="outline" color="gray" tt="none">
                      {g.name}
                    </Badge>
                  ))}
                </Group>
              )}
            </div>
            <Group gap="xs" wrap="nowrap">
              <Button
                size="xs"
                color="teal"
                leftSection={<IconCheck size={14} />}
                onClick={() => {
                  setRole(u.role);
                  setGroupIds([]);
                  setApproving(u);
                }}
              >
                Approve
              </Button>
              <Button
                size="xs"
                variant="light"
                color="red"
                leftSection={<IconX size={14} />}
                onClick={() => {
                  setReason('');
                  setRejecting(u);
                }}
              >
                Refuse
              </Button>
            </Group>
          </Group>
        ))}
      </Stack>
      <Modal opened={!!approving} onClose={() => setApproving(null)} title={<Text fw={700}>Approve the access of {approving?.display_name || approving?.username}</Text>}>
        <Stack>
          <SegmentedControl value={role} onChange={setRole} data={ROLES} />
          <Text size="xs" c="dimmed">
            {ROLE_HELP[role as Role]}
          </Text>
          <MultiSelect
            label="Add to local groups"
            description="The groups synchronized from the identity provider are already applied."
            data={(groups ?? []).filter((g) => g.source === 'local').map((g) => ({ value: String(g.id), label: g.name }))}
            value={groupIds}
            onChange={setGroupIds}
            searchable
            clearable
          />
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setApproving(null)}>
              Cancel
            </Button>
            <Button color="teal" loading={approve.isPending} onClick={() => approving && approve.mutate(approving)}>
              Approve
            </Button>
          </Group>
        </Stack>
      </Modal>
      <Modal opened={!!rejecting} onClose={() => setRejecting(null)} title={<Text fw={700}>Refuse the access of {rejecting?.display_name || rejecting?.username}</Text>}>
        <Stack>
          <TextInput label="Reason (audit log)" value={reason} onChange={(e) => setReason(e.currentTarget.value)} />
          <Text size="xs" c="dimmed">
            The person will no longer be able to sign in. You can reverse this decision by approving the account from the list.
          </Text>
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setRejecting(null)}>
              Cancel
            </Button>
            <Button color="red" loading={reject.isPending} onClick={() => rejecting && reject.mutate(rejecting)}>
              Refuse
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Card>
  );
}

function ResetMfa({ user }: { user: AdminUser }) {
  const reset = useAdminMutation(() => api.resetUserMfa(user.id), 'Second factor reset');
  return (
    <Alert color="teal" p="xs">
      <Group justify="space-between" wrap="nowrap">
        <Text size="xs">Second factor (TOTP) enabled. If the phone is lost, reset it: the user enrols again at the next sign-in when it is required.</Text>
        <Button size="compact-xs" color="red" variant="light" onClick={() => reset.mutate(undefined)} loading={reset.isPending}>
          Reset
        </Button>
      </Group>
    </Alert>
  );
}

function UserDrawer({ user, onClose }: { user: AdminUser | null; onClose: () => void }) {
  return (
    <Drawer opened={user != null} onClose={onClose} position="right" size="lg" title={<Text fw={700}>{user?.display_name || user?.username}</Text>}>
      {user && (
        <Stack>
          <UserForm key={user.id} user={user} onDone={onClose} />
          {user.mfa_enabled && <ResetMfa user={user} />}
          {user.is_service && (
            <>
              <Divider label="API tokens" labelPosition="left" mt="md" />
              <ApiTokens
                source={{
                  queryKey: ['admin-user-tokens', user.id],
                  list: () => api.userTokens(user.id),
                  create: (name, days) => api.createUserToken(user.id, name, days),
                  remove: (tokenId) => api.deleteUserToken(user.id, tokenId),
                }}
                hint="The token gives the rights of this service account on the referentials (direct and through its groups)."
              />
            </>
          )}
          <Divider label="Referential access" labelPosition="left" mt="md" />
          {user.role === 'admin' ? (
            <Alert color="gray">Administrator: full access to all referentials.</Alert>
          ) : (
            <>
              {user.groups.length > 0 && (
                <Text size="xs" c="dimmed">
                  Also inherits the rights of: {user.groups.map((g) => g.name).join(', ')}
                </Text>
              )}
              <GrantsEditor scope={{ userId: user.id }} />
            </>
          )}
        </Stack>
      )}
    </Drawer>
  );
}

export default function UsersPage() {
  const { me } = useAuth();
  const { data: users, isLoading } = useQuery({ queryKey: ['admin-users'], queryFn: api.users });
  const [filter, setFilter] = useState('');
  const [creating, setCreating] = useState(false);
  const [creatingService, setCreatingService] = useState(false);
  const [editing, setEditing] = useState<AdminUser | null>(null);
  const [deleting, setDeleting] = useState<AdminUser | null>(null);

  const unlock = useAdminMutation((u: AdminUser) => api.updateUser(u.id, { unlock: true }), 'Account unlocked');
  const approveRejected = useAdminMutation((u: AdminUser) => api.approveUser(u.id, {}), 'Access approved');
  const revoke = useAdminMutation((u: AdminUser) => api.revokeUserSessions(u.id), 'Sessions closed');
  const remove = useAdminMutation((u: AdminUser) => api.deleteUser(u.id), 'User deleted', () => setDeleting(null));

  const filtered = useMemo(() => {
    const f = filter.toLowerCase();
    return (users ?? []).filter((u) => [u.username, u.display_name, u.email, ...u.groups.map((g) => g.name)].join(' ').toLowerCase().includes(f));
  }, [users, filter]);
  const current = editing && users?.find((u) => u.id === editing.id);

  return (
    <Stack gap="lg">
      <Group justify="space-between" align="flex-end">
        <div>
          <Title order={2}>Users</Title>
          <Text c="dimmed" size="sm">
            Local accounts, service accounts, roles, groups and referential access.
          </Text>
        </div>
        <Group gap="xs">
          <Button variant="default" leftSection={<IconRobot size={16} />} onClick={() => setCreatingService(true)}>
            New service account
          </Button>
          <Button leftSection={<IconPlus size={16} />} onClick={() => setCreating(true)}>
            New user
          </Button>
        </Group>
      </Group>
      <PendingRequests users={(users ?? []).filter((u) => u.status === 'pending')} />
      <TextInput placeholder="Filter…" leftSection={<IconSearch size={16} />} value={filter} onChange={(e) => setFilter(e.currentTarget.value)} w={300} />
      <Card padding={0}>
        {isLoading ? (
          <Loader m="md" />
        ) : (
          <Table.ScrollContainer minWidth={900}>
            <Table highlightOnHover verticalSpacing="sm" fz="sm">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>User</Table.Th>
                  <Table.Th>Role</Table.Th>
                  <Table.Th>Groups</Table.Th>
                  <Table.Th>Status</Table.Th>
                  <Table.Th>Last sign-in</Table.Th>
                  <Table.Th>Sessions / tokens</Table.Th>
                  <Table.Th />
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {filtered.map((u) => (
                  <Table.Tr key={u.id} style={{ cursor: 'pointer' }} onClick={() => setEditing(u)}>
                    <Table.Td>
                      <Group gap="sm" wrap="nowrap">
                        {u.is_service ? (
                          <IconRobot size={28} stroke={1.3} color="var(--mantine-color-violet-5)" />
                        ) : (
                          <IconUserCircle size={28} stroke={1.3} color="var(--mantine-color-dimmed)" />
                        )}
                        <div>
                          <Text size="sm" fw={600}>
                            {u.display_name || u.username}
                            {u.id === me?.id && (
                              <Badge ml={6} size="xs" variant="outline">
                                you
                              </Badge>
                            )}
                          </Text>
                          <Group gap={6}>
                            <Text size="xs" c="dimmed">
                              {u.username}
                              {u.email && ` · ${u.email}`}
                            </Text>
                            {u.mfa_enabled && (
                              <Badge size="xs" variant="light" color="teal">
                                2FA
                              </Badge>
                            )}
                            {u.is_service ? (
                              <Badge size="xs" variant="light" color="violet">
                                service
                              </Badge>
                            ) : (
                              u.auth_source !== 'local' && (
                                <Badge size="xs" variant="outline" color="cyan">
                                  {u.auth_source === 'ldap' ? 'LDAP' : 'OIDC'}
                                </Badge>
                              )
                            )}
                          </Group>
                        </div>
                      </Group>
                    </Table.Td>
                    <Table.Td>
                      <Badge color={ROLE_BADGE[u.role].color} variant="light">
                        {ROLE_BADGE[u.role].label}
                      </Badge>
                    </Table.Td>
                    <Table.Td>
                      <Group gap={4}>
                        {u.groups.map((g) => (
                          <Badge key={g.id} size="sm" variant="outline" color={g.source === 'local' ? 'gray' : 'cyan'} tt="none">
                            {g.name}
                          </Badge>
                        ))}
                      </Group>
                    </Table.Td>
                    <Table.Td>
                      <Group gap={4}>
                        {u.status === 'pending' ? (
                          <Badge color="orange">Pending</Badge>
                        ) : u.status === 'rejected' ? (
                          <Badge color="red" variant="light">
                            Refused
                          </Badge>
                        ) : !u.is_active ? (
                          <Badge color="gray">Disabled</Badge>
                        ) : u.locked ? (
                          <Badge color="orange" leftSection={<IconLock size={10} />}>
                            Locked
                          </Badge>
                        ) : (
                          <Badge color="teal" variant="light">
                            Active
                          </Badge>
                        )}
                        {u.must_change_password && (
                          <Badge color="yellow" variant="light" size="sm">
                            password to change
                          </Badge>
                        )}
                      </Group>
                    </Table.Td>
                    {u.is_service ? (
                      <Table.Td title={fmtDate(u.token_last_used_at)}>
                        {u.token_last_used_at ? `API ${fmtRelative(u.token_last_used_at)}` : 'API: never'}
                      </Table.Td>
                    ) : (
                      <Table.Td title={fmtDate(u.last_login_at)}>{u.last_login_at ? fmtRelative(u.last_login_at) : 'never'}</Table.Td>
                    )}
                    <Table.Td>
                      {u.session_count} / {u.token_count}
                    </Table.Td>
                    <Table.Td w={40} onClick={(e) => e.stopPropagation()}>
                      <Menu position="bottom-end" withinPortal>
                        <Menu.Target>
                          <Button variant="subtle" color="gray" size="compact-sm" aria-label="Actions">
                            <IconDots size={16} />
                          </Button>
                        </Menu.Target>
                        <Menu.Dropdown>
                          {u.status === 'rejected' && (
                            <Menu.Item leftSection={<IconCheck size={14} />} onClick={() => approveRejected.mutate(u)}>
                              Approve after all
                            </Menu.Item>
                          )}
                          {u.locked && (
                            <Menu.Item leftSection={<IconLockOpen size={14} />} onClick={() => unlock.mutate(u)}>
                              Unlock
                            </Menu.Item>
                          )}
                          <Menu.Item leftSection={<IconLogout size={14} />} onClick={() => revoke.mutate(u)} disabled={u.session_count === 0 || u.is_service}>
                            Close all sessions
                          </Menu.Item>
                          <Menu.Item leftSection={<IconTrash size={14} />} color="red" onClick={() => setDeleting(u)} disabled={u.id === me?.id}>
                            Delete
                          </Menu.Item>
                        </Menu.Dropdown>
                      </Menu>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        )}
      </Card>

      <Modal opened={creating} onClose={() => setCreating(false)} title={<Text fw={700}>New user</Text>} size="lg">
        <UserForm onDone={() => setCreating(false)} />
      </Modal>
      <Modal opened={creatingService} onClose={() => setCreatingService(false)} title={<Text fw={700}>New service account</Text>} size="lg">
        <ServiceAccountForm onDone={() => setCreatingService(false)} />
      </Modal>
      <UserDrawer user={current ?? null} onClose={() => setEditing(null)} />
      <Modal opened={deleting != null} onClose={() => setDeleting(null)} title={<Text fw={700}>Delete the user?</Text>}>
        <Stack>
          <Text size="sm">
            The account <b>{deleting?.username}</b>, its sessions, API tokens and rights will be deleted. The audit log is
            kept. To simply block access, disable the account instead.
          </Text>
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setDeleting(null)}>
              Cancel
            </Button>
            <Button color="red" onClick={() => deleting && remove.mutate(deleting)} loading={remove.isPending}>
              Delete
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
