import { Badge, Card, Code, Group, SimpleGrid, Stack, Text, Title } from '@mantine/core';
import { useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';
import { ChangePasswordForm } from '../auth/PasswordChange';
import { useAuth } from '../auth/AuthContext';
import ApiTokens from '../components/ApiTokens';
import TwoFactorCard from '../components/TwoFactorCard';
import { LevelBadge } from '../components/GrantsEditor';
import { useReferentials } from '../api/hooks';

export default function AccountPage() {
  const { me } = useAuth();
  const qc = useQueryClient();
  const { data: refs } = useReferentials();
  if (!me) return null;

  return (
    <Stack gap="lg">
      <div>
        <Title order={2}>My account</Title>
        <Text c="dimmed" size="sm">
          Profile, password and API access tokens.
        </Text>
      </div>
      <SimpleGrid cols={{ base: 1, md: 2 }}>
        <Card>
          <Title order={5} mb="sm">
            Profil
          </Title>
          <Stack gap={6}>
            <Text size="sm">
              Identifiant : <b>{me.username}</b>{' '}
              <Badge size="xs" variant="light">
                {me.auth_source === 'local' ? 'local account' : me.auth_source === 'ldap' ? 'LDAP directory' : 'OpenID Connect'}
              </Badge>
            </Text>
            {me.display_name && <Text size="sm">Name: {me.display_name}</Text>}
            {me.email && <Text size="sm">Email: {me.email}</Text>}
            <Group gap={6}>
              <Text size="sm">Role:</Text>
              <Badge color={me.is_admin ? 'red' : me.role === 'advanced' ? 'orange' : 'gray'} variant="light">
                {me.is_admin ? 'Administrator' : me.role === 'advanced' ? 'Advanced user' : 'User'}
              </Badge>
            </Group>
            {me.groups.length > 0 && (
              <Group gap={6}>
                <Text size="sm">Groups:</Text>
                {me.groups.map((g) => (
                  <Badge key={g} variant="outline" color="gray" tt="none">
                    {g}
                  </Badge>
                ))}
              </Group>
            )}
          </Stack>
          <Title order={6} mt="lg" mb="xs">
            My access
          </Title>
          {me.is_admin ? (
            <Text size="sm" c="dimmed">
              Full access to all referentials.
            </Text>
          ) : Object.keys(me.permissions).length === 0 ? (
            <Text size="sm" c="dimmed">
              No referential is available to you yet: contact an administrator.
            </Text>
          ) : (
            <Stack gap={4}>
              {(refs ?? []).map((r) => (
                <Group key={r.id} justify="space-between">
                  <Text size="sm">{r.name}</Text>
                  <LevelBadge level={me.permissions[r.id]} />
                </Group>
              ))}
            </Stack>
          )}
        </Card>
        <Card>
          <Title order={5} mb="sm">
            Password
          </Title>
          {me.auth_source === 'local' ? (
            <ChangePasswordForm onDone={() => qc.invalidateQueries({ queryKey: ['me'] })} />
          ) : (
            <Text size="sm" c="dimmed">
              Your account is managed by {me.auth_source === 'ldap' ? 'the LDAP directory' : 'the OpenID Connect provider'}: change your
              password in that tool. Your groups are synchronized at each sign-in.
            </Text>
          )}
        </Card>
      </SimpleGrid>

      {me.mfa?.available && <TwoFactorCard />}

      <Card>
        <Title order={5}>API tokens</Title>
        <Text size="sm" c="dimmed" mb="sm">
          For scripts and tools: <Code>Authorization: Bearer &lt;token&gt;</Code> header. For a shared tool, ask an administrator for a
          service account instead of using your own tokens.
        </Text>
        <ApiTokens
          source={{ queryKey: ['tokens'], list: api.tokens, create: api.createToken, remove: api.deleteToken }}
          hint="The token gives exactly your current rights on the referentials."
        />
      </Card>
    </Stack>
  );
}
