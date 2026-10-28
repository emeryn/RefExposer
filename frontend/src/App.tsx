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
  Indicator,
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
  IconDatabaseCog,
  IconBell,
  IconDatabasePlus,
  IconDog,
  IconHelp,
  IconDownload,
  IconSettings,
  IconLogout,
  IconUserCircle,
  IconUsers,
  IconLayoutDashboard,
  IconRobot,
  IconMoon,
  IconCheck,
  IconDeviceDesktop,
  IconSearch,
  IconServer,
  IconSql,
  IconSun,
} from '@tabler/icons-react';
import { Suspense, lazy, useMemo, useRef, useState } from 'react';
import { Link, Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import { useAlerts, useAssistant, useBranding, useReferentials } from './api/hooks';
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

// Loaded on demand: rarely visited, admin-only or heavy pages (CodeMirror...). The administration pages are
// grouped into three sections, each loading its own tabs when one is opened.
const ReferentialsSection = lazy(() => import('./pages/admin/ReferentialsSection'));
const AccessSection = lazy(() => import('./pages/admin/AccessSection'));
const SystemSection = lazy(() => import('./pages/admin/SystemSection'));
const SettingsPage = lazy(() => import('./pages/admin/SettingsPage'));
const ReferentialEditor = lazy(() => import('./pages/admin/ReferentialEditor'));
const AccountPage = lazy(() => import('./pages/AccountPage'));
const DownloadsPage = lazy(() => import('./pages/DownloadsPage'));
const SqlConsole = lazy(() => import('./pages/SqlConsole'));
const ChatPage = lazy(() => import('./pages/ChatPage'));
const WatchdogPage = lazy(() => import('./pages/WatchdogPage'));
const AlertsPage = lazy(() => import('./pages/AlertsPage'));
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

/** What needs a look, with what this account has not marked as read. */
function AlertsBell() {
  const { data } = useAlerts(200);
  const unread = data?.counts.unread ?? 0;
  const total = data?.counts.total ?? 0;
  return (
    <Tooltip label={total ? `${total} alert(s)${unread ? `, ${unread} unread` : ', all read'}` : 'No alert'}>
      <Indicator
        disabled={!unread}
        label={unread > 99 ? '99+' : unread}
        size={16}
        offset={4}
        color="red"
        aria-label={`${unread} unread alert(s)`}
      >
        <ActionIcon variant="default" size="lg" component={Link} to="/alerts" aria-label="Alerts">
          <IconBell size={18} />
        </ActionIcon>
      </Indicator>
    </Tooltip>
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
  const { data: assistant } = useAssistant();
  const { data: alerts } = useAlerts(200);

  const byCategory = useMemo(() => {
    const groups = new Map<string, typeof refs>();
    for (const r of refs ?? []) {
      if (!groups.has(r.category)) groups.set(r.category, []);
      groups.get(r.category)!.push(r);
    }
    return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [refs]);

  const path = location.pathname;
  const main: { to: string; label: string; icon: typeof IconBell; active: boolean; badge?: number }[] = [
    { to: '/', label: 'Dashboard', icon: IconLayoutDashboard, active: path === '/' },
    { to: '/search', label: 'Global search', icon: IconSearch, active: path.startsWith('/search') },
    ...(me?.can_use_sql ? [{ to: '/sql', label: 'SQL console', icon: IconSql, active: path.startsWith('/sql') }] : []),
    // Only when an administrator enabled the assistant for this account
    ...(assistant?.available ? [{ to: '/chat', label: 'Assistant', icon: IconRobot, active: path.startsWith('/chat') }] : []),
    // Scheduled investigations: the same agent, so the same condition — plus its agent mode
    ...(assistant?.watchdogs ? [{ to: '/watchdogs', label: 'Watchdogs', icon: IconDog, active: path.startsWith('/watchdogs') }] : []),
    { to: '/alerts', label: 'Alerts', icon: IconBell, active: path.startsWith('/alerts'), badge: alerts?.counts.total },
    { to: '/downloads', label: 'Downloads', icon: IconDownload, active: path.startsWith('/downloads') },
    ...(me?.can_create_internal
      ? [{ to: '/internal/new', label: 'New internal referential', icon: IconDatabasePlus, active: path === '/internal/new' }]
      : []),
    { to: '/help', label: 'Help', icon: IconHelp, active: path.startsWith('/help') },
  ];
  const inError = (refs ?? []).filter((r) => r.health === 'error').length;
  // Four entries, each holding its related pages as tabs: the sidebar stays readable as the
  // administration grows, and related screens are reached without going back to it
  const admin = [
    {
      to: inError ? '/admin/referentials?status=error' : '/admin/referentials',
      label: 'Referentials',
      icon: IconDatabaseCog,
      active: path.startsWith('/admin/referentials'),
      badge: inError,
      badgeColor: 'red',
      badgeLabel: `${inError} referential(s) in error`,
    },
    {
      to: '/admin/users',
      label: 'Users and access',
      icon: IconUsers,
      active: path.startsWith('/admin/users'),
      badge: me?.pending_requests,
      badgeLabel: `${me?.pending_requests} pending access request(s)`,
    },
    { to: '/admin/settings', label: 'Settings', icon: IconSettings, active: path.startsWith('/admin/settings') },
    { to: '/system', label: 'System', icon: IconServer, active: path.startsWith('/system') },
  ] as { to: string; label: string; icon: typeof IconUsers; active: boolean; badge?: number; badgeColor?: string; badgeLabel?: string }[];

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
            <AlertsBell />
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
              rightSection={
                item.badge ? (
                  <Badge size="sm" color="orange" circle>
                    {item.badge}
                  </Badge>
                ) : null
              }
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
                      <Tooltip label={item.badgeLabel}>
                        <Badge size="sm" color={item.badgeColor ?? 'orange'} circle>
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
          <Route path="/chat" element={<ChatPage />} />
          <Route path="/watchdogs" element={<WatchdogPage />} />
          <Route path="/alerts" element={<AlertsPage />} />
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
              <Route path="/admin/referentials/new" element={<ReferentialEditor />} />
              <Route path="/admin/referentials/:id/edit" element={<ReferentialEditor />} />
              <Route path="/admin/referentials/:tab?" element={<ReferentialsSection />} />
              <Route path="/admin/users/:tab?" element={<AccessSection />} />
              <Route path="/admin/settings/:tab?" element={<SettingsPage />} />
              <Route path="/system/:tab?" element={<SystemSection />} />
              {/* Paths of the pages that are a tab of a section: links and bookmarks naming them keep working */}
              <Route path="/admin/discovery" element={<Navigate to="/admin/referentials/discovery" replace />} />
              <Route path="/admin/peers" element={<Navigate to="/admin/referentials/peers" replace />} />
              <Route path="/admin/groups" element={<Navigate to="/admin/users/groups" replace />} />
              <Route path="/admin/endpoints" element={<Navigate to="/admin/settings/endpoints" replace />} />
              <Route path="/admin/notifications" element={<Navigate to="/admin/settings/notifications" replace />} />
              <Route path="/admin/secrets" element={<Navigate to="/admin/settings/secrets" replace />} />
              <Route path="/admin/storage" element={<Navigate to="/system/storage" replace />} />
              <Route path="/admin/tasks" element={<Navigate to="/system/tasks" replace />} />
              <Route path="/admin/audit" element={<Navigate to="/system/audit" replace />} />
            </>
          )}
          <Route path="*" element={<NotFound />} />
        </Routes>
        </Suspense>
      </AppShell.Main>
    </AppShell>
  );
}
