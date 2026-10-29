import { Alert, Button, Center, Group, Loader, Paper, Stack, Text } from '@mantine/core';
import { IconPlugConnectedX, IconRefresh } from '@tabler/icons-react';
import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useSyncExternalStore } from 'react';
import { checkNow, onRecovered, snapshot, subscribe, type Connection } from '../api/connection';
import { BrandHero } from './Brand';

/** Whether the backend is answering, as a hook. See `api/connection.ts` for how it is decided. */
export function useConnection(): Connection {
  return useSyncExternalStore(subscribe, snapshot, snapshot);
}

/**
 * Wires the reconnection to the cache: when the backend answers again, everything on screen is refetched.
 *
 * Renders nothing and is mounted once, above the sign-in branch, because an outage has to be recovered from
 * whether or not anybody is signed in at that moment.
 */
export function ConnectionWatcher() {
  const qc = useQueryClient();
  useEffect(() => {
    onRecovered(() => {
      // Everything, including `me`: after a restart the session may be valid or gone, and whichever it is
      // the next answer settles it rather than this guessing
      void qc.invalidateQueries();
    });
    return () => onRecovered(() => {});
  }, [qc]);
  return null;
}

const since = (at: number | null) => {
  if (at === null) return '';
  const seconds = Math.round((Date.now() - at) / 1000);
  return seconds < 60 ? ` for ${seconds}s` : ` for ${Math.round(seconds / 60)} min`;
};

/**
 * The notice over the application while the backend is away.
 *
 * What is on screen stays on screen — stale data is still the last thing the backend actually said, and
 * emptying the pages would lose the reader's place for no gain. The notice is what keeps that honest.
 */
export function ConnectionBanner() {
  const { reachable, attempts, checking, since: at } = useConnection();
  if (reachable) return null;
  return (
    <Alert
      color="orange"
      variant="filled"
      icon={<IconPlugConnectedX size={18} />}
      p="xs"
      mb="md"
      styles={{ message: { width: '100%' } }}
    >
      <Group justify="space-between" wrap="wrap" gap="xs">
        <Text size="sm">
          <b>The server is not answering{since(at)}.</b> What is shown was read before it went away; it is
          refreshed by itself as soon as it is back
          {attempts > 1 ? ` (${attempts} attempts)` : ''}.
        </Text>
        <Button
          size="compact-xs"
          variant="white"
          color="orange"
          leftSection={<IconRefresh size={13} />}
          loading={checking}
          onClick={checkNow}
        >
          Try now
        </Button>
      </Group>
    </Alert>
  );
}

/**
 * What is shown when the application starts and the backend is not there at all.
 *
 * It takes the place of the sign-in page on purpose: an unreachable backend is not a signed-out account, and
 * offering a form that cannot work would be a lie — somebody would type their password into it and conclude
 * their account is broken.
 */
export function UnreachableScreen() {
  const { attempts, checking } = useConnection();
  return (
    <Center mih="100vh" p="md" style={{ background: 'var(--app-subtle-bg)' }}>
      <div style={{ width: '100%', maxWidth: 420 }}>
        <BrandHero caption="Waiting for the server" />
        <Paper withBorder shadow="sm" p="xl" radius="md">
          <Stack gap="sm" align="center">
            <Loader size="sm" />
            <Text fw={600}>The server is not answering</Text>
            <Text size="sm" c="dimmed" ta="center">
              Nothing is wrong with your account: the application cannot be reached. It is being tried again
              {attempts > 1 ? ` — ${attempts} attempts so far` : ''}, and this page continues by itself as
              soon as it answers.
            </Text>
            <Button
              variant="default"
              leftSection={<IconRefresh size={16} />}
              loading={checking}
              onClick={checkNow}
              mt="xs"
            >
              Try now
            </Button>
          </Stack>
        </Paper>
        <Text size="xs" c="dimmed" ta="center" mt="md">
          If this lasts, the backend or the database is down — an administrator has the logs.
        </Text>
      </div>
    </Center>
  );
}
