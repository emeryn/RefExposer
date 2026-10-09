import {
  ActionIcon,
  Avatar,
  Badge,
  AppShell,
  Center,
  Loader,
  Menu,
  UnstyledButton,
  Burger,
  Group,
  NavLink,
  ScrollArea,
  Text,
  TextInput,
  Tooltip,
  useComputedColorScheme,
  useMantineColorScheme,
} from '@mantine/core';
import { useDisclosure, useHotkeys } from '@mantine/hooks';
import {
  IconApi,
  IconChevronDown,
  IconCalendarTime,
  IconClipboardList,
  IconDatabaseCog,
  IconDatabasePlus,
  IconHelp,
  IconDownload,
  IconBell,
  IconFolderSearch,
  IconKey,
  IconSettings,
  IconLogout,
  IconUserCircle,
  IconUsers,
  IconUsersGroup,
  IconLayoutDashboard,
  IconMoon,
  IconCheck,
  IconDeviceDesktop,
  IconSearch,
  IconServer,
  IconSql,
  IconSun,
} from '@tabler/icons-react';
import { Suspense, lazy, useMemo, useRef, useState } from 'react';
import { Link, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import { useBranding, useReferentials } from './api/hooks';
import { BrandHeader } from './components/Brand';
import { useAuth } from './auth/AuthContext';
import LoginPage from './auth/LoginPage';
import ForcedPasswordChange from './auth/PasswordChange';
import PendingPage from './auth/PendingPage';
import { HEALTH, HealthDot } from './components/Status';
import Dashboard from './pages/Dashboard';
import ReferentialPage from './pages/ReferentialPage';
import SearchPage from './pages/SearchPage';
import NotFound from './pages/NotFound';

// Loaded on demand: rarely visited, admin-only or heavy pages (CodeMirror...)
const SettingsPage = lazy(() => import('./pages/admin/SettingsPage'));
const AccountPage = lazy(() => import('./pages/AccountPage'));
const AuditPage = lazy(() => import('./pages/admin/AuditPage'));
const TasksPage = lazy(() => import('./pages/admin/TasksPage'));
const GroupsPage = lazy(() => import('./pages/admin/GroupsPage'));
const UsersPage = lazy(() => import('./pages/admin/UsersPage'));
const ReferentialsAdminPage = lazy(() => import('./pages/admin/ReferentialsAdminPage'));
const ReferentialEditor = lazy(() => import('./pages/admin/ReferentialEditor'));
const SecretsPage = lazy(() => import('./pages/admin/SecretsPage'));
const DiscoveryPage = lazy(() => import('./pages/admin/DiscoveryPage'));
const NotificationsPage = lazy(() => import('./pages/admin/NotificationsPage'));
const DownloadsPage = lazy(() => import('./pages/DownloadsPage'));
const SqlConsole = lazy(() => import('./pages/SqlConsole'));
const SystemPage = lazy(() => import('./pages/SystemPage'));
const InternalEditor = lazy(() => import('./pages/InternalEditor'));
const HelpPage = lazy(() => import('./pages/help/HelpPage'));

const SCHEMES = [
  { value: 'light', label: 'Light', icon: IconSun },
  { value: 'dark', label: 'Dark', icon: IconMoon },
  { value: 'auto', label: 'System', icon: IconDeviceDesktop },
] as const;

function ColorSchemeMenu() {
  const { colorScheme, setColorScheme } = useMantineColorScheme();
  const computed = useComputedColorScheme('light');
  const Current = computed === 'dark' ? IconMoon : IconSun;
  return (
    <Menu position="bottom-end" width={190} shadow="md">
      <Menu.Target>
        <Tooltip label="Theme">
          <ActionIcon variant="default" size="lg" aria-label="Choose the theme">
            <Current size={18} />
          </ActionIcon>
        </Tooltip>
      </Menu.Target>
      <Menu.Dropdown>
        <Menu.Label>Interface theme</Menu.Label>
        {SCHEMES.map((s) => (
          <Menu.Item
            key={s.value}
            leftSection={<s.icon size={16} />}
            rightSection={colorScheme === s.value ? <IconCheck size={14} /> : null}
            onClick={() => setColorScheme(s.value)}
          >
            {s.label}
          </Menu.Item>
        ))}
      </Menu.Dropdown>
    </Menu>
  );
}

function GlobalSearch() {
  const navigate = useNavigate();
  const [value, setValue] = useState('');
  const ref = useRef<HTMLInputElement>(null);
  useHotkeys([['mod+K', () => ref.current?.focus()]]);
  return (
    <TextInput
      ref={ref}
      placeholder="Search all referentials… (Ctrl+K)"
      leftSection={<IconSearch size={16} />}
      value={value}
      onChange={(e) => setValue(e.currentTarget.value)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' && value.trim().length >= 2) {
          navigate(`/search?q=${encodeURIComponent(value.trim())}`);
          ref.current?.blur();
        }
      }}
      w={{ base: 200, sm: 340, md: 440 }}
      radius="xl"
    />
  );
}

function UserMenu() {
  const { me, logout } = useAuth();
  const navigate = useNavigate();
  if (!me) return null;
  const name = me.display_name || me.username;
  return (
    <Menu position="bottom-end" width={240} shadow="md">
      <Menu.Target>
        <UnstyledButton aria-label="User menu">
          <Group gap={8} wrap="nowrap">
            <Avatar size={34} radius="xl" color={me.is_admin ? 'red' : 'indigo'}>
              {name.slice(0, 2).toUpperCase()}
            </Avatar>
            <div style={{ lineHeight: 1.1 }}>
              <Text size="sm" fw={600} visibleFrom="md">
                {name}
              </Text>
              <Text size="xs" c="dimmed" visibleFrom="md">
                {me.is_admin ? 'Administrator' : me.role === 'advanced' ? 'Advanced user' : 'User'}
              </Text>
            </div>
            <IconChevronDown size={14} />
          </Group>
        </UnstyledButton>
      </Menu.Target>
      <Menu.Dropdown>
        <Menu.Label>{me.username}</Menu.Label>
        <Menu.Item leftSection={<IconUserCircle size={16} />} onClick={() => navigate('/account')}>
          My account & API tokens
        </Menu.Item>
        <Menu.Item leftSection={<IconHelp size={16} />} onClick={() => navigate('/help')}>
          Help
        </Menu.Item>
        <Menu.Divider />
        <Menu.Item leftSection={<IconLogout size={16} />} color="red" onClick={logout}>
          Sign out
        </Menu.Item>
      </Menu.Dropdown>
    </Menu>
  );
}

export default function App() {
  const { me, isLoading } = useAuth();
  useBranding(); // title of the browser tab and public URL, before sign-in too
  if (isLoading)
    return (
      <Center mih="100vh">
        <Loader />
      </Center>
    );
  if (!me) return <LoginPage />;
  if (me.status === 'pending') return <PendingPage />;
  if (me.must_change_password) return <ForcedPasswordChange />;
  return <Shell />;
}

function Shell() {
  const { me } = useAuth();
  const [opened, { toggle, close }] = useDisclosure();
  const location = useLocation();
  const { data: refs } = useReferentials();

  const byCategory = useMemo(() => {
    const groups = new Map<string, typeof refs>();
    for (const r of refs ?? []) {
      if (!groups.has(r.category)) groups.set(r.category, []);
      groups.get(r.category)!.push(r);
    }
    return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [refs]);

  const path = location.pathname;
  const main = [
    { to: '/', label: 'Dashboard', icon: IconLayoutDashboard, active: path === '/' },
    { to: '/search', label: 'Global search', icon: IconSearch, active: path.startsWith('/search') },
    ...(me?.can_use_sql ? [{ to: '/sql', label: 'SQL console', icon: IconSql, active: path.startsWith('/sql') }] : []),
    { to: '/downloads', label: 'Downloads', icon: IconDownload, active: path.startsWith('/downloads') },
    ...(me?.can_create_internal
      ? [{ to: '/internal/new', label: 'New internal referential', icon: IconDatabasePlus, active: path === '/internal/new' }]
      : []),
    { to: '/help', label: 'Help', icon: IconHelp, active: path.startsWith('/help') },
  ];
  const admin = [
    { to: '/admin/referentials', label: 'Referentials', icon: IconDatabaseCog, active: path.startsWith('/admin/referentials') },
    { to: '/admin/discovery', label: 'Bulk discovery', icon: IconFolderSearch, active: path.startsWith('/admin/discovery') },
    { to: '/admin/secrets', label: 'Secrets', icon: IconKey, active: path.startsWith('/admin/secrets') },
    { to: '/admin/notifications', label: 'Notifications', icon: IconBell, active: path.startsWith('/admin/notifications') },
    { to: '/admin/users', label: 'Users', icon: IconUsers, active: path.startsWith('/admin/users'), badge: me?.pending_requests },
    { to: '/admin/groups', label: 'Groups', icon: IconUsersGroup, active: path.startsWith('/admin/groups') },
    { to: '/admin/audit', label: 'Audit log', icon: IconClipboardList, active: path.startsWith('/admin/audit') },
    { to: '/admin/tasks', label: 'Tasks', icon: IconCalendarTime, active: path.startsWith('/admin/tasks') },
    { to: '/admin/settings', label: 'Settings', icon: IconSettings, active: path.startsWith('/admin/settings') },
    { to: '/system', label: 'System', icon: IconServer, active: path.startsWith('/system') },
  ] as { to: string; label: string; icon: typeof IconUsers; active: boolean; badge?: number }[];

  return (
    <AppShell
      header={{ height: 60 }}
      navbar={{ width: 280, breakpoint: 'sm', collapsed: { mobile: !opened } }}
      padding="lg"
    >
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between" wrap="nowrap">
          <Group gap="sm" wrap="nowrap">
            <Burger opened={opened} onClick={toggle} hiddenFrom="sm" size="sm" />
            <Link to="/" style={{ textDecoration: 'none', color: 'inherit', minWidth: 0 }}>
              <BrandHeader />
            </Link>
          </Group>
          <Group visibleFrom="sm">
            <GlobalSearch />
          </Group>
          <Group gap="xs" wrap="nowrap">
            <Tooltip label="API documentation (OpenAPI)">
              <ActionIcon variant="default" size="lg" component="a" href="/api/docs" target="_blank" aria-label="API">
                <IconApi size={18} />
              </ActionIcon>
            </Tooltip>
            <ColorSchemeMenu />
            <UserMenu />
          </Group>
        </Group>
      </AppShell.Header>

      <AppShell.Navbar p="sm">
        <AppShell.Section>
          {main.map((item) => (
            <NavLink
              key={item.to}
              component={Link}
              to={item.to}
              label={item.label}
              leftSection={<item.icon size={18} stroke={1.6} />}
              active={item.active}
              onClick={close}
              style={{ borderRadius: 8 }}
              fw={500}
            />
          ))}
          {me?.is_admin && (
            <>
              <Text size="xs" fw={700} c="dimmed" tt="uppercase" mt="md" mb={4} px="sm" lts={0.4}>
                Administration
              </Text>
              {admin.map((item) => (
                <NavLink
                  key={item.to}
                  component={Link}
                  to={item.to}
                  label={item.label}
                  leftSection={<item.icon size={18} stroke={1.6} />}
                  rightSection={
                    item.badge ? (
                      <Tooltip label={`${item.badge} pending access request(s)`}>
                        <Badge size="sm" color="orange" circle>
                          {item.badge}
                        </Badge>
                      </Tooltip>
                    ) : null
                  }
                  active={item.active}
                  onClick={close}
                  style={{ borderRadius: 8 }}
                  fw={500}
                />
              ))}
            </>
          )}
        </AppShell.Section>
        <Text size="xs" fw={700} c="dimmed" tt="uppercase" mt="lg" mb={4} px="sm" lts={0.4}>
          Referentials
        </Text>
        <AppShell.Section grow component={ScrollArea}>
          {byCategory.map(([category, items]) => (
            <div key={category}>
              <Text size="xs" c="dimmed" px="sm" mt={8} mb={2}>
                {category}
              </Text>
              {items!.map((r) => (
                <NavLink
                  key={r.id}
                  component={Link}
                  to={`/r/${r.id}`}
                  onClick={close}
                  active={path.startsWith(`/r/${r.id}`)}
                  style={{ borderRadius: 8 }}
                  label={
                    <Text size="sm" truncate>
                      {r.name}
                    </Text>
                  }
                  leftSection={
                    <Tooltip label={HEALTH[r.health].label} position="right">
                      <HealthDot health={r.health} />
                    </Tooltip>
                  }
                />
              ))}
            </div>
          ))}
        </AppShell.Section>
      </AppShell.Navbar>

      <AppShell.Main>
        <Suspense
          fallback={
            <Center h={200}>
              <Loader />
            </Center>
          }
        >
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/r/:id/:tab?" element={<ReferentialPage />} />
          <Route path="/search" element={<SearchPage />} />
          {me?.can_use_sql && <Route path="/sql" element={<SqlConsole />} />}
          <Route path="/account" element={<AccountPage />} />
          <Route path="/downloads" element={<DownloadsPage />} />
          <Route path="/help/:section?" element={<HelpPage />} />
          {me?.can_create_internal && (
            <>
              <Route path="/internal/new" element={<InternalEditor />} />
              <Route path="/internal/:id/schema" element={<InternalEditor />} />
            </>
          )}
          {me?.is_admin && (
            <>
              <Route path="/system" element={<SystemPage />} />
              <Route path="/admin/users" element={<UsersPage />} />
              <Route path="/admin/referentials" element={<ReferentialsAdminPage />} />
              <Route path="/admin/settings/:tab?" element={<SettingsPage />} />
              <Route path="/admin/referentials/new" element={<ReferentialEditor />} />
              <Route path="/admin/referentials/:id/edit" element={<ReferentialEditor />} />
              <Route path="/admin/groups" element={<GroupsPage />} />
              <Route path="/admin/secrets" element={<SecretsPage />} />
              <Route path="/admin/discovery" element={<DiscoveryPage />} />
              <Route path="/admin/notifications" element={<NotificationsPage />} />
              <Route path="/admin/audit" element={<AuditPage />} />
              <Route path="/admin/tasks" element={<TasksPage />} />
            </>
          )}
          <Route path="*" element={<NotFound />} />
        </Routes>
        </Suspense>
      </AppShell.Main>
    </AppShell>
  );
}
