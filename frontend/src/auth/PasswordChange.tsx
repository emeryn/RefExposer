import { Alert, Box, Button, Center, List, Paper, PasswordInput, Progress, Stack, Text, Title } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconCheck, IconShieldLock, IconX } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';
import { api } from '../api/client';
import { useAuth } from './AuthContext';

export function passwordChecks(password: string, minLength: number, username?: string) {
  const classes = [/[a-z]/, /[A-Z]/, /\d/, /[^A-Za-z0-9]/].filter((r) => r.test(password)).length;
  return [
    { ok: password.length >= minLength, label: `At least ${minLength} characters` },
    { ok: classes >= 3, label: '3 kinds among lowercase, uppercase, digits, symbols' },
    { ok: !username || !password.toLowerCase().includes(username.toLowerCase()), label: 'Does not contain the username' },
  ];
}

export function PasswordStrength({ password, username }: { password: string; username?: string }) {
  const { data: policy } = useQuery({ queryKey: ['password-policy'], queryFn: api.passwordPolicy, staleTime: Infinity });
  const checks = passwordChecks(password, policy?.min_length ?? 12, username);
  const score = checks.filter((c) => c.ok).length;
  return (
    <Stack gap={6}>
      <Progress value={(score / checks.length) * 100} color={score === checks.length ? 'teal' : score >= 2 ? 'yellow' : 'red'} size="xs" />
      <List size="xs" spacing={2} center>
        {checks.map((c) => (
          <List.Item
            key={c.label}
            icon={c.ok ? <IconCheck size={13} color="var(--mantine-color-teal-6)" /> : <IconX size={13} color="var(--mantine-color-red-6)" />}
          >
            <Text size="xs" c={c.ok ? 'dimmed' : undefined}>
              {c.label}
            </Text>
          </List.Item>
        ))}
      </List>
    </Stack>
  );
}

export function ChangePasswordForm({ onDone }: { onDone?: () => void }) {
  const { me } = useAuth();
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (next !== confirm) {
      setError('The two passwords do not match.');
      return;
    }
    setLoading(true);
    setError(null);
    try {
      await api.changePassword(current, next);
      notifications.show({ title: 'Password changed', message: 'Your other sessions have been closed.', color: 'teal' });
      setCurrent('');
      setNext('');
      setConfirm('');
      onDone?.();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <form onSubmit={submit}>
      <Stack>
        {error && <Alert color="red">{error}</Alert>}
        <PasswordInput label="Current password" value={current} onChange={(e) => setCurrent(e.currentTarget.value)} autoComplete="current-password" required />
        <PasswordInput label="New password" value={next} onChange={(e) => setNext(e.currentTarget.value)} autoComplete="new-password" required />
        {next && <PasswordStrength password={next} username={me?.username} />}
        <PasswordInput
          label="Confirmation"
          value={confirm}
          onChange={(e) => setConfirm(e.currentTarget.value)}
          autoComplete="new-password"
          error={confirm && confirm !== next ? 'Does not match' : undefined}
          required
        />
        <Button type="submit" loading={loading}>
          Change password
        </Button>
      </Stack>
    </form>
  );
}

/** Full-screen page shown when the account must change its password before anything else. */
export default function ForcedPasswordChange() {
  const { me, setMe, logout } = useAuth();
  return (
    <Center mih="100vh" p="md" style={{ background: 'var(--app-subtle-bg)' }}>
      <Box w="100%" maw={440}>
        <Paper withBorder shadow="sm" p="xl" radius="md">
          <Stack gap="xs" mb="md" align="center">
            <IconShieldLock size={40} color="var(--mantine-color-indigo-6)" />
            <Title order={3}>New password required</Title>
            <Text size="sm" c="dimmed" ta="center">
              Hello <b>{me?.display_name || me?.username}</b>, please choose a new password before continuing.
            </Text>
          </Stack>
          <ChangePasswordForm onDone={() => me && setMe({ ...me, must_change_password: false })} />
          <Button variant="subtle" color="gray" fullWidth mt="sm" onClick={logout}>
            Sign out
          </Button>
        </Paper>
      </Box>
    </Center>
  );
}
