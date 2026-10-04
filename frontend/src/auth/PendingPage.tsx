import { Badge, Box, Button, Center, Group, Loader, Paper, Stack, Text, ThemeIcon, Title } from '@mantine/core';
import { IconHourglassHigh } from '@tabler/icons-react';
import { useQueryClient } from '@tanstack/react-query';
import { useEffect } from 'react';
import { useBranding } from '../api/hooks';
import { useAuth } from './AuthContext';

const SOURCES = { local: 'Local account', ldap: 'LDAP directory', oidc: 'OpenID Connect', service: 'Service account' } as const;

/** Shown to accounts created from LDAP / OpenID Connect until an administrator approves them. */
export default function PendingPage() {
  const { me, logout } = useAuth();
  const { data: branding } = useBranding();
  const qc = useQueryClient();

  useEffect(() => {
    const t = setInterval(() => qc.invalidateQueries({ queryKey: ['me'] }), 15_000);
    return () => clearInterval(t);
  }, [qc]);

  return (
    <Center mih="100vh" p="md" style={{ background: 'var(--app-subtle-bg)' }}>
      <Box w="100%" maw={520}>
        <Paper withBorder shadow="sm" p="xl" radius="md">
          <Stack align="center" gap="sm">
            <ThemeIcon size={64} radius="xl" variant="light" color="orange">
              <IconHourglassHigh size={34} />
            </ThemeIcon>
            <Title order={3} ta="center">
              Access request pending
            </Title>
            <Text ta="center" c="dimmed">
              Hello <b>{me?.display_name || me?.username}</b>, your identity has been verified. An administrator now has to approve
              your access to {branding?.title ?? 'RefExposer'}.
            </Text>
            <Group gap={6}>
              <Badge variant="light">{me ? SOURCES[me.auth_source] : ''}</Badge>
              {me?.groups.map((g) => (
                <Badge key={g} variant="outline" color="gray" tt="none">
                  {g}
                </Badge>
              ))}
            </Group>
            <Group gap={6} mt="xs">
              <Loader size="xs" type="dots" />
              <Text size="xs" c="dimmed">
                This page refreshes automatically once your access is approved.
              </Text>
            </Group>
            <Button variant="subtle" color="gray" onClick={logout} mt="sm">
              Sign out
            </Button>
          </Stack>
        </Paper>
      </Box>
    </Center>
  );
}
