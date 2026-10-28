import {
  Alert,
  Badge,
  Button,
  Code,
  Divider,
  Group,
  MultiSelect,
  NumberInput,
  SimpleGrid,
  Stack,
  Switch,
  Text,
  Textarea,
  Title,
  Tooltip,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconCheck, IconRestore, IconX } from '@tabler/icons-react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../../api/client';
import type { AssistantSettings } from '../../api/types';
import { LlmProfiles } from '../../components/LlmProfiles';

export function AssistantTab({
  initial,
  defaultPrompt,
  groups,
  users,
}: {
  initial: AssistantSettings;
  defaultPrompt: string;
  groups: string[];
  users: { value: string; label: string }[];
}) {
  const qc = useQueryClient();
  // The connection, the model and the generation parameters belong to the saved connectors (LlmProfiles):
  // what is left here is policy, which does not change when you switch model
  const [form, setForm] = useState<AssistantSettings>(initial);
  const set = <K extends keyof AssistantSettings>(key: K, value: AssistantSettings[K]) =>
    setForm((f) => ({ ...f, [key]: value }));

  const save = useMutation({
    mutationFn: () => api.saveSettings('assistant', form),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['settings'] });
      qc.invalidateQueries({ queryKey: ['assistant'] });
      notifications.show({ color: 'teal', icon: <IconCheck size={16} />, message: 'Assistant settings saved' });
    },
    onError: (e: Error) => notifications.show({ color: 'red', icon: <IconX size={16} />, message: e.message }),
  });

  return (
    <Stack gap="lg" maw={900}>
      <Switch
        checked={form.enabled}
        onChange={(e) => set('enabled', e.currentTarget.checked)}
        label="Offer the assistant in the application"
        description="Off: no Assistant entry in the menu, and its API answers that it is unavailable."
      />

      <LlmProfiles />

      <Divider />

      <div>
        <Title order={5}>Agent mode</Title>
        <Text c="dimmed" size="sm">
          The assistant queries the referentials itself, through the REST API, <b>with the session of the person
          who is chatting</b>: it can never read or do anything that person could not do in the interface.
        </Text>
      </div>
      <Switch
        checked={form.agent_enabled}
        onChange={(e) => set('agent_enabled', e.currentTarget.checked)}
        label="Let the assistant use the application"
        description="Off: it answers from the conversation only, with no access to any data."
      />
      <Switch
        checked={form.allow_writes}
        onChange={(e) => set('allow_writes', e.currentTarget.checked)}
        disabled={!form.agent_enabled}
        label="Allow actions that change something"
        description="Start an update, create a referential, grant a right… Each one still obeys the rights of the account: an administrator can create a referential from the chat, a reader cannot."
      />
      <Switch
        checked={form.allow_confidential}
        onChange={(e) => set('allow_confidential', e.currentTarget.checked)}
        label="Confidential referentials may be queried"
        description="Off (recommended): nothing of a confidential referential ever reaches the model — not its rows, not its columns, not even its name. It is removed from the rights of every call the assistant makes."
      />
      {form.allow_confidential && (
        <Alert color="orange" variant="light">
          <Text size="sm">
            The rows of confidential referentials will be sent to {form.base_url || 'the inference server'} as part of
            the prompts. Only leave this on for a server you host and whose logs you control.
          </Text>
        </Alert>
      )}
      <Switch
        checked={form.watchdogs_enabled}
        onChange={(e) => set('watchdogs_enabled', e.currentTarget.checked)}
        disabled={!form.agent_enabled}
        label="Scheduled watchdogs"
        description="Lets administrators and advanced users schedule an investigation over referentials they choose, alerting only on a finding. Nobody else sees the menu. Turning this off stops every schedule at once."
      />
      {form.watchdogs_enabled && (
        <Alert color="blue" variant="light">
          <Text size="sm">
            A watchdog calls {form.base_url || 'the inference server'} on its own schedule, with nobody watching:
            count its runs in what that server has to serve. It is confined to the referentials it names, runs
            with the rights of the account it belongs to, and never writes — whatever the switch above allows in
            the chat.
          </Text>
        </Alert>
      )}
      <SimpleGrid cols={{ base: 1, sm: 3 }}>
        <NumberInput label="Steps per answer" min={1} max={30} value={form.max_steps}
          onChange={(v) => set('max_steps', Number(v) || 8)} disabled={!form.agent_enabled}
          description="Tool calls before it must conclude." />
        <NumberInput label="Questions per minute" min={1} max={600} value={form.rate_per_minute}
          onChange={(v) => set('rate_per_minute', Number(v) || 20)} description="Per account." />
        <NumberInput label="Conversation retention (days)" min={0} max={3650} value={form.retention_days}
          onChange={(v) => set('retention_days', Number(v) ?? 30)}
          description="0 keeps them. Purged by a system task." />
      </SimpleGrid>
      <SimpleGrid cols={{ base: 1, sm: 2 }}>
        <MultiSelect
          label="Groups allowed"
          data={groups}
          value={form.groups}
          onChange={(v) => set('groups', v)}
          searchable
          clearable
          description="Members of these groups may use the assistant."
        />
        <MultiSelect
          label="Accounts allowed"
          data={users}
          value={form.user_ids.map(String)}
          onChange={(v) => set('user_ids', v.map(Number))}
          searchable
          clearable
          description="Named accounts, on top of the groups above."
        />
      </SimpleGrid>
      <Text size="xs" c="dimmed" mt={-8}>
        {form.groups.length || form.user_ids.length
          ? 'Only these groups and accounts, plus every administrator.'
          : 'Nothing is restricted: every account may use the assistant.'}{' '}
        Service accounts never can: they have no interface session.
      </Text>

      <Divider />

      <div>
        <Group justify="space-between" align="flex-end">
          <div>
            <Title order={5}>System prompt</Title>
            <Text c="dimmed" size="sm">
              What the assistant is told before every conversation. Empty: the prompt shipped with the application.
            </Text>
          </div>
          <Group gap="xs">
            {!form.system_prompt && <Badge variant="light">default</Badge>}
            <Tooltip label="Put the shipped prompt in the box, to adjust it">
              <Button size="xs" variant="default" leftSection={<IconRestore size={14} />}
                onClick={() => set('system_prompt', defaultPrompt)}>
                Load the default
              </Button>
            </Tooltip>
            <Button size="xs" variant="default" onClick={() => set('system_prompt', '')}
              disabled={!form.system_prompt}>
              Back to default
            </Button>
          </Group>
        </Group>
        <Textarea
          mt="xs"
          autosize
          minRows={6}
          maxRows={24}
          placeholder="The prompt shipped with the application is used."
          value={form.system_prompt}
          onChange={(e) => set('system_prompt', e.currentTarget.value)}
          styles={{ input: { fontFamily: 'var(--mantine-font-family-monospace)', fontSize: 12 } }}
        />
        <Text size="xs" c="dimmed" mt={4}>
          Never put a credential or an internal address in it: assume anything in the prompt can be read back by
          whoever is chatting. The account, its role and the confidential rule are appended automatically.
        </Text>
      </div>

      <Group justify="flex-end">
        <Button onClick={() => save.mutate()} loading={save.isPending}>
          Save
        </Button>
      </Group>
      <Text size="xs" c="dimmed">
        The assistant appears for the allowed accounts once it is enabled, a server is reachable and a model is
        chosen. Tool calls are in the audit log under <Code>assistant.ask</Code>.
      </Text>
    </Stack>
  );
}
