import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Code,
  Divider,
  Group,
  MultiSelect,
  NumberInput,
  Select,
  SimpleGrid,
  Stack,
  TagsInput,
  Switch,
  Text,
  TextInput,
  Textarea,
  Title,
  Tooltip,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconCheck, IconPlugConnected, IconPlus, IconRestore, IconTrash, IconX } from '@tabler/icons-react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../../api/client';
import type { AssistantSettings } from '../../api/types';
import { SavedSecret } from '../../components/SavedSecret';

type HeaderRow = { name: string; value: string };

const PROVIDERS = [
  { value: 'ollama', label: 'Ollama (/api/chat)' },
  { value: 'openai', label: 'OpenAI-compatible (/v1/chat/completions)' },
];

export function AssistantTab({
  initial,
  defaultPrompt,
  groups,
  users,
  savedParameters,
}: {
  initial: AssistantSettings;
  defaultPrompt: string;
  groups: string[];
  users: { value: string; label: string }[];
  /** What the configured API receives today, computed by the backend from the saved settings. */
  savedParameters: Record<string, unknown>;
}) {
  const qc = useQueryClient();
  const [form, setForm] = useState<AssistantSettings>(initial);
  const [headers, setHeaders] = useState<HeaderRow[]>(
    Object.entries(initial.headers ?? {}).map(([name, value]) => ({ name, value })),
  );
  const [models, setModels] = useState<string[]>(initial.model ? [initial.model] : []);
  // The options are edited as JSON: anything the inference server accepts goes through, so there is no list
  // of fields to keep up to date with it
  const [optionsText, setOptionsText] = useState(() => JSON.stringify(initial.options ?? {}, null, 2));
  const [optionsError, setOptionsError] = useState<string | null>(null);
  const set = <K extends keyof AssistantSettings>(key: K, value: AssistantSettings[K]) =>
    setForm((f) => ({ ...f, [key]: value }));

  const readOptions = (text: string) => {
    setOptionsText(text);
    if (!text.trim()) {
      setOptionsError(null);
      set('options', {});
      return;
    }
    try {
      const parsed = JSON.parse(text);
      if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
        setOptionsError('A JSON object is expected, e.g. {"repeat_penalty": 1.15}');
        return;
      }
      setOptionsError(null);
      set('options', parsed as Record<string, unknown>);
    } catch (e) {
      setOptionsError((e as Error).message);
    }
  };

  const body = () => ({
    ...form,
    headers: Object.fromEntries(headers.filter((h) => h.name.trim()).map((h) => [h.name.trim(), h.value])),
  });

  const save = useMutation({
    mutationFn: () => api.saveSettings('assistant', body()),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['settings'] });
      qc.invalidateQueries({ queryKey: ['assistant'] });
      notifications.show({ color: 'teal', icon: <IconCheck size={16} />, message: 'Assistant settings saved' });
    },
    onError: (e: Error) => notifications.show({ color: 'red', icon: <IconX size={16} />, message: e.message }),
  });

  // The model list comes from the server itself: it is offered once the connection worked
  const test = useMutation({
    mutationFn: async () => {
      await api.saveSettings('assistant', body());
      return api.assistantTest();
    },
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ['settings'] });
      if (r.ok && r.models?.length) {
        setModels(r.models);
        if (!form.model || !r.models.includes(form.model)) set('model', r.models[0]);
      }
    },
  });

  const result = test.data;

  return (
    <Stack gap="lg" maw={900}>
      <Switch
        checked={form.enabled}
        onChange={(e) => set('enabled', e.currentTarget.checked)}
        label="Offer the assistant in the application"
        description="Off: no Assistant entry in the menu, and its API answers that it is unavailable."
      />

      <div>
        <Title order={5}>Inference server</Title>
        <Text c="dimmed" size="sm">
          RefExposer is its only client: the browser never calls it, so the server does not have to be reachable
          from the workstations. The connection uses the proxy and the certificate authorities of{' '}
          <Link to="/admin/settings/network">Network &amp; proxy</Link>, so an internal root CA needs nothing more
          here.
        </Text>
      </div>
      <SimpleGrid cols={{ base: 1, sm: 2 }}>
        <Select
          label="API"
          data={PROVIDERS}
          value={form.provider}
          onChange={(v) => set('provider', (v as AssistantSettings['provider']) ?? 'ollama')}
          allowDeselect={false}
        />
        <TextInput
          label="Address"
          placeholder="http://ollama.internal:11434"
          value={form.base_url}
          onChange={(e) => set('base_url', e.currentTarget.value)}
          description="Without a path for Ollama; with or without /v1 for an OpenAI-compatible server."
        />
      </SimpleGrid>
      <SimpleGrid cols={{ base: 1, sm: 2 }}>
        <SavedSecret
          label="API key"
          description="Optional: most local servers have none. Sent as Authorization: Bearer."
          value={form.api_key}
          isSet={initial.api_key_set}
          onChange={(v) => set('api_key', v)}
        />
        <NumberInput
          label="Timeout (seconds)"
          min={5}
          max={900}
          value={form.timeout}
          onChange={(v) => set('timeout', Number(v) || 120)}
          description="A model loaded on demand can take a while to answer the first time."
        />
      </SimpleGrid>

      <div>
        <Group justify="space-between" align="flex-end">
          <div>
            <Text size="sm" fw={500}>
              Extra headers
            </Text>
            <Text size="xs" c="dimmed">
              For a gateway that expects its own: a tenant, a routing key, or the API key under another name.
            </Text>
          </div>
          <Button
            size="xs"
            variant="default"
            leftSection={<IconPlus size={14} />}
            onClick={() => setHeaders((h) => [...h, { name: '', value: '' }])}
          >
            Add a header
          </Button>
        </Group>
        <Stack gap="xs" mt="xs">
          {headers.map((h, i) => (
            <Group key={i} gap="xs" wrap="nowrap">
              <TextInput
                placeholder="X-Api-Key"
                value={h.name}
                onChange={(e) =>
                  setHeaders((rows) => rows.map((r, n) => (n === i ? { ...r, name: e.currentTarget.value } : r)))
                }
                style={{ flex: '0 0 240px' }}
              />
              <TextInput
                placeholder="value"
                value={h.value}
                onChange={(e) =>
                  setHeaders((rows) => rows.map((r, n) => (n === i ? { ...r, value: e.currentTarget.value } : r)))
                }
                style={{ flex: 1 }}
              />
              <ActionIcon
                variant="subtle"
                color="red"
                aria-label="Remove this header"
                onClick={() => setHeaders((rows) => rows.filter((_, n) => n !== i))}
              >
                <IconTrash size={16} />
              </ActionIcon>
            </Group>
          ))}
          {!headers.length && (
            <Text size="xs" c="dimmed">
              None.
            </Text>
          )}
        </Stack>
      </div>

      <Switch
        checked={form.verify_tls}
        onChange={(e) => set('verify_tls', e.currentTarget.checked)}
        label="Verify the TLS certificate of the server"
        description="Keep it on: add your certificate authority in Network & proxy rather than turning this off."
      />

      <Group align="flex-end" gap="sm">
        <Button
          variant="light"
          leftSection={<IconPlugConnected size={16} />}
          onClick={() => test.mutate()}
          loading={test.isPending}
          disabled={!form.base_url || !!optionsError}
        >
          Test and list the models
        </Button>
        <Select
          label="Model"
          placeholder={models.length ? 'Choose a model' : 'Test the connection to list the models'}
          data={models}
          value={form.model || null}
          onChange={(v) => set('model', v ?? '')}
          searchable
          disabled={!models.length}
          style={{ flex: 1 }}
          description={
            models.length ? `${models.length} model(s) served` : 'The server is asked for the models it serves.'
          }
        />
      </Group>
      {result && (
        <Alert
          color={result.ok ? 'teal' : 'red'}
          icon={result.ok ? <IconCheck size={18} /> : <IconX size={18} />}
          title={result.ok ? 'The server answered' : 'The server could not be reached'}
        >
          <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>
            {result.ok ? `${result.models?.length ?? 0} model(s): ${(result.models ?? []).join(', ')}` : result.error}
          </Text>
        </Alert>
      )}

      <Divider />

      <div>
        <Title order={5}>Generation</Title>
        <Text c="dimmed" size="sm">
          Some servers ignore what they do not support; what the model actually applied is what its own logs show.
        </Text>
      </div>
      <SimpleGrid cols={{ base: 1, sm: 4 }}>
        <NumberInput label="Temperature" min={0} max={2} step={0.1} decimalScale={2} value={form.temperature}
          onChange={(v) => set('temperature', Number(v))} />
        <NumberInput label="top_p" min={0} max={1} step={0.05} decimalScale={2} value={form.top_p}
          onChange={(v) => set('top_p', Number(v))} />
        <NumberInput
          label="Answer limit (tokens)"
          min={64}
          max={32768}
          step={128}
          value={form.max_tokens}
          onChange={(v) => set('max_tokens', Number(v) || 2048)}
          description={form.provider === 'ollama' ? 'num_predict' : 'max_tokens'}
        />
        <NumberInput label="Messages kept" min={2} max={200} value={form.history_messages}
          onChange={(v) => set('history_messages', Number(v) || 20)}
          description="Older ones are dropped from the prompt." />
      </SimpleGrid>
      <SimpleGrid cols={{ base: 1, sm: 2 }}>
        <NumberInput
          label="Context window (tokens)"
          min={0}
          max={1048576}
          step={1024}
          value={form.context_tokens}
          onChange={(v) => set('context_tokens', Number(v) || 0)}
          description={
            form.provider === 'ollama'
              ? 'num_ctx. 0: whatever the server decides, which is often small — raise it when the agent reads large tool results.'
              : 'This API has no such parameter: the context window comes from how the server loaded the model.'
          }
          disabled={form.provider !== 'ollama'}
        />
        <TagsInput
          label="Stop sequences"
          placeholder="Add a sequence"
          value={form.stop}
          onChange={(v) => set('stop', v)}
          description="The generation stops when the model writes one of these. Rarely needed."
        />
      </SimpleGrid>

      <div>
        <Textarea
          label="Other options"
          description={
            form.provider === 'ollama'
              ? 'Sent as they are inside the options object of /api/chat: repeat_penalty, top_k, min_p, mirostat, seed, num_thread… A key set here wins over the fields above.'
              : 'Sent as they are at the top level of /v1/chat/completions: repetition_penalty, top_k, frequency_penalty, seed… A key set here wins over the fields above.'
          }
          autosize
          minRows={3}
          maxRows={14}
          value={optionsText}
          error={optionsError}
          onChange={(e) => readOptions(e.currentTarget.value)}
          placeholder={'{ "repeat_penalty": 1.15, "top_k": 40 }'}
          styles={{ input: { fontFamily: 'var(--mantine-font-family-monospace)', fontSize: 12 } }}
        />
      </div>
      <div>
        <Text size="xs" fw={500} mb={4}>
          Sent to the model right now
        </Text>
        <Code block style={{ fontSize: 11 }}>
          {JSON.stringify(
            form.provider === 'ollama' ? { options: savedParameters } : savedParameters,
            null,
            2,
          )}
        </Code>
        <Text size="xs" c="dimmed" mt={4}>
          The saved configuration, refreshed when you save. Some servers ignore what they do not support; what
          the model actually applied is what its own logs show.
        </Text>
      </div>

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
        <Button onClick={() => save.mutate()} loading={save.isPending} disabled={!!optionsError}>
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
