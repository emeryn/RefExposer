import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Card,
  Code,
  Group,
  Loader,
  Modal,
  NumberInput,
  Select,
  SimpleGrid,
  Stack,
  Switch,
  TagsInput,
  Text,
  Textarea,
  TextInput,
  Title,
  Tooltip,
} from '@mantine/core';
import {
  IconBolt,
  IconCheck,
  IconCopy,
  IconPencil,
  IconPlugConnected,
  IconPlus,
  IconTrash,
  IconX,
} from '@tabler/icons-react';
import { notifications } from '@mantine/notifications';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api/client';
import type { LlmProfile, LlmProfileDraft } from '../api/types';
import { EmptyState } from './Common';
import { SavedSecret } from './SavedSecret';
import { fmtRelative } from '../lib/format';

/** The mask the backend understands as "keep the stored value" for a header. */
const MASK = '********';

const PROVIDERS = [
  { value: 'ollama', label: 'Ollama (/api/chat)' },
  { value: 'openai', label: 'OpenAI-compatible (/v1/chat/completions)' },
];

type HeaderRow = { name: string; value: string };

const EMPTY: LlmProfileDraft = {
  name: '',
  description: '',
  provider: 'ollama',
  base_url: '',
  model: '',
  verify_tls: true,
  timeout: 120,
  temperature: 0.2,
  top_p: 0.95,
  max_tokens: 2048,
  context_tokens: 0,
  stop: [],
  options: {},
  small_model: false,
  api_key: '',
};

function draftOf(profile: LlmProfile): LlmProfileDraft {
  return {
    name: profile.name,
    description: profile.description,
    provider: profile.provider,
    base_url: profile.base_url,
    model: profile.model,
    verify_tls: profile.verify_tls,
    timeout: profile.timeout,
    temperature: profile.temperature,
    top_p: profile.top_p,
    max_tokens: profile.max_tokens,
    context_tokens: profile.context_tokens,
    stop: profile.stop,
    options: profile.options,
    small_model: profile.small_model,
    api_key: '',
  };
}

function Editor({ profile, onClose }: { profile: LlmProfile | null; onClose: () => void }) {
  const qc = useQueryClient();
  const [draft, setDraft] = useState<LlmProfileDraft>(profile ? draftOf(profile) : EMPTY);
  // Only the names of the stored headers came back: their values stay on the server, so an untouched row is
  // sent back as the mask and keeps what is stored
  const [headers, setHeaders] = useState<HeaderRow[]>(
    (profile?.header_names ?? []).map((name) => ({ name, value: MASK })),
  );
  const [models, setModels] = useState<string[]>(profile?.model ? [profile.model] : []);
  const [optionsText, setOptionsText] = useState(() => JSON.stringify(profile?.options ?? {}, null, 2));
  const [optionsError, setOptionsError] = useState<string | null>(null);
  const [clearKey, setClearKey] = useState(false);
  const set = <K extends keyof LlmProfileDraft>(key: K, value: LlmProfileDraft[K]) =>
    setDraft((d) => ({ ...d, [key]: value }));

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

  const body = (): LlmProfileDraft => ({
    ...draft,
    headers: Object.fromEntries(headers.filter((h) => h.name.trim()).map((h) => [h.name.trim(), h.value])),
    ...(clearKey ? { api_key_clear: true, api_key: '' } : {}),
  });

  const save = useMutation({
    mutationFn: () => (profile ? api.updateLlmProfile(profile.id, body()) : api.createLlmProfile(body())),
    onSuccess: (saved) => {
      qc.invalidateQueries({ queryKey: ['llm-profiles'] });
      qc.invalidateQueries({ queryKey: ['settings'] });
      qc.invalidateQueries({ queryKey: ['assistant'] });
      notifications.show({ color: 'teal', icon: <IconCheck size={16} />, message: `Profile “${saved.name}” saved` });
      onClose();
    },
    onError: (e: Error) => notifications.show({ color: 'red', icon: <IconX size={16} />, message: e.message }),
  });

  // A profile has to be saved before it can be tested: the key never leaves the server, so the test runs there
  const test = useMutation({
    mutationFn: async () => {
      const saved = profile ? await api.updateLlmProfile(profile.id, body()) : await api.createLlmProfile(body());
      qc.invalidateQueries({ queryKey: ['llm-profiles'] });
      return { saved, result: await api.testLlmProfile(saved.id) };
    },
    onError: (e: Error) => notifications.show({ color: 'red', icon: <IconX size={16} />, message: e.message }),
    onSuccess: ({ result }) => {
      if (result.ok && result.models?.length) {
        setModels(result.models);
        if (!draft.model || !result.models.includes(draft.model)) set('model', result.models[0]);
      }
    },
  });
  const result = test.data?.result;

  return (
    <Modal opened onClose={onClose} size="xl" title={profile ? `Edit “${profile.name}”` : 'New connector'}>
      <Stack gap="md">
        <SimpleGrid cols={{ base: 1, sm: 2 }}>
          <TextInput
            label="Name"
            required
            placeholder="Workstation, GPU server, Gateway…"
            value={draft.name}
            onChange={(e) => set('name', e.currentTarget.value)}
          />
          <TextInput
            label="Note"
            placeholder="What this one is for"
            value={draft.description}
            onChange={(e) => set('description', e.currentTarget.value)}
          />
        </SimpleGrid>

        <Title order={6}>Server</Title>
        <Text size="xs" c="dimmed">
          RefExposer is its only client: the browser never calls it. The connection uses the proxy and the
          certificate authorities of <Link to="/admin/settings/network">Network &amp; proxy</Link>, so an internal
          root CA needs nothing here.
        </Text>
        <SimpleGrid cols={{ base: 1, sm: 2 }}>
          <Select
            label="API"
            data={PROVIDERS}
            value={draft.provider}
            onChange={(v) => set('provider', (v as LlmProfileDraft['provider']) ?? 'ollama')}
            allowDeselect={false}
          />
          <TextInput
            label="Address"
            placeholder="http://ollama.internal:11434"
            value={draft.base_url}
            onChange={(e) => set('base_url', e.currentTarget.value)}
            description="Without a path for Ollama; with or without /v1 for an OpenAI-compatible server."
          />
        </SimpleGrid>
        <SimpleGrid cols={{ base: 1, sm: 2 }}>
          <SavedSecret
            label="API key"
            description="Optional: most local servers have none. Sent as Authorization: Bearer, and never returned."
            value={draft.api_key ?? ''}
            isSet={!!profile?.api_key_set}
            clear={clearKey}
            onChange={(v) => {
              set('api_key', v);
              if (v) setClearKey(false);
            }}
            onClear={setClearKey}
          />
          <NumberInput
            label="Timeout (seconds)"
            min={5}
            max={900}
            value={draft.timeout}
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
                Their values are stored encrypted and never returned — a row left at {MASK} keeps its value.
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
                  style={{ flex: '0 0 220px' }}
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
          checked={draft.verify_tls}
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
            disabled={!draft.base_url || !draft.name.trim() || !!optionsError}
          >
            Save and list the models
          </Button>
          <Select
            label="Model"
            placeholder={models.length ? 'Choose a model' : 'Test the connection to list the models'}
            data={models}
            value={draft.model || null}
            onChange={(v) => set('model', v ?? '')}
            searchable
            disabled={!models.length}
            style={{ flex: 1 }}
            description={models.length ? `${models.length} model(s) served` : 'The server is asked what it serves.'}
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

        <Switch
          checked={draft.small_model}
          onChange={(e) => set('small_model', e.currentTarget.checked)}
          label="This is a small model (1B, 3B)"
          description="Adapts agent mode to it: a short list of read-only tools, a compact prompt, one tool call at a time, fewer steps, small results and small row limits. A tool call the model writes as text instead of calling properly is run anyway."
        />
        {draft.small_model && (
          <Alert color="blue" variant="light" icon={<IconBolt size={18} />}>
            <Text size="sm">
              A model this size answers questions over one or two referentials well. It will not write a referential,
              hand you a file or build a query for you: those tools are not offered to it, because choosing between
              twenty tools is what it gets wrong. Use a bigger model for that, and switch back here when you need it.
            </Text>
          </Alert>
        )}

        <Title order={6}>Generation</Title>
        <SimpleGrid cols={{ base: 1, sm: 4 }}>
          <NumberInput label="Temperature" min={0} max={2} step={0.1} decimalScale={2} value={draft.temperature}
            onChange={(v) => set('temperature', Number(v))} />
          <NumberInput label="top_p" min={0} max={1} step={0.05} decimalScale={2} value={draft.top_p}
            onChange={(v) => set('top_p', Number(v))} />
          <NumberInput
            label="Answer limit (tokens)"
            min={64}
            max={32768}
            step={128}
            value={draft.max_tokens}
            onChange={(v) => set('max_tokens', Number(v) || 2048)}
            description={draft.provider === 'ollama' ? 'num_predict' : 'max_tokens'}
          />
          <NumberInput
            label="Context window (tokens)"
            min={0}
            max={1048576}
            step={1024}
            value={draft.context_tokens}
            onChange={(v) => set('context_tokens', Number(v) || 0)}
            description={
              draft.provider === 'ollama'
                ? 'num_ctx. 0: whatever the server decides, which is often small.'
                : 'Not a parameter of this API: it comes from how the server loaded the model.'
            }
            disabled={draft.provider !== 'ollama'}
          />
        </SimpleGrid>
        <TagsInput
          label="Stop sequences"
          placeholder="Add a sequence"
          value={draft.stop}
          onChange={(v) => set('stop', v)}
          description="The generation stops when the model writes one of these. Rarely needed."
        />
        <Textarea
          label="Other options"
          description={
            draft.provider === 'ollama'
              ? 'Sent as they are inside the options object of /api/chat: repeat_penalty, top_k, min_p, mirostat, seed… A key set here wins over the fields above.'
              : 'Sent as they are at the top level of /v1/chat/completions: repetition_penalty, top_k, frequency_penalty, seed… A key set here wins over the fields above.'
          }
          autosize
          minRows={3}
          maxRows={12}
          value={optionsText}
          error={optionsError}
          onChange={(e) => readOptions(e.currentTarget.value)}
          placeholder={'{ "repeat_penalty": 1.15, "top_k": 40 }'}
          styles={{ input: { fontFamily: 'var(--mantine-font-family-monospace)', fontSize: 12 } }}
        />

        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            Cancel
          </Button>
          <Button
            loading={save.isPending}
            disabled={!draft.name.trim() || !!optionsError}
            onClick={() => save.mutate()}
          >
            {profile ? 'Save' : 'Create'}
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}

function Row({ profile, onEdit }: { profile: LlmProfile; onEdit: () => void }) {
  const qc = useQueryClient();
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['llm-profiles'] });
    qc.invalidateQueries({ queryKey: ['settings'] });
    qc.invalidateQueries({ queryKey: ['assistant'] });
  };
  const notify = (e: Error) => notifications.show({ color: 'red', icon: <IconX size={16} />, message: e.message });

  const activate = useMutation({
    mutationFn: () => api.activateLlmProfile(profile.id),
    onSuccess: (p) => {
      notifications.show({ color: 'teal', icon: <IconCheck size={16} />, message: `“${p.name}” is now in use` });
      invalidate();
    },
    onError: notify,
  });
  const test = useMutation({ mutationFn: () => api.testLlmProfile(profile.id), onSuccess: invalidate, onError: notify });
  const duplicate = useMutation({
    mutationFn: () => api.duplicateLlmProfile(profile.id),
    onSuccess: invalidate,
    onError: notify,
  });
  const remove = useMutation({
    mutationFn: () => api.deleteLlmProfile(profile.id),
    onSuccess: invalidate,
    onError: notify,
  });
  const result = test.data;

  return (
    <Card withBorder padding="sm" style={profile.active ? { borderColor: 'var(--mantine-color-blue-5)' } : undefined}>
      <Group justify="space-between" wrap="nowrap" align="flex-start">
        <Stack gap={4} style={{ minWidth: 0 }}>
          <Group gap="xs">
            <Text fw={600}>{profile.name}</Text>
            {profile.active && <Badge color="blue">in use</Badge>}
            {profile.small_model && (
              <Tooltip label="Agent mode adapted to a small model: fewer tools, a shorter prompt, one call at a time">
                <Badge variant="light" color="grape" leftSection={<IconBolt size={10} />}>
                  small model
                </Badge>
              </Tooltip>
            )}
            {profile.api_key_set && (
              <Badge variant="default" size="sm">
                API key
              </Badge>
            )}
            {profile.header_names.map((h) => (
              <Badge key={h} variant="default" size="sm" tt="none">
                {h}
              </Badge>
            ))}
          </Group>
          <Text size="xs" c="dimmed">
            {profile.provider === 'ollama' ? 'Ollama' : 'OpenAI-compatible'} · {profile.base_url || 'no address'} ·{' '}
            <Code>{profile.model || 'no model'}</Code>
          </Text>
          {profile.description && (
            <Text size="xs" c="dimmed">
              {profile.description}
            </Text>
          )}
          {profile.last_test_at && (
            <Text size="xs" c={profile.last_test_ok ? 'teal' : 'red'}>
              {profile.last_test_ok ? 'Answered' : 'Failed'} {fmtRelative(profile.last_test_at)}
              {profile.last_test_error ? ` — ${profile.last_test_error}` : ''}
            </Text>
          )}
        </Stack>
        <Group gap="xs" wrap="nowrap">
          {!profile.active && (
            <Button size="xs" variant="light" loading={activate.isPending} onClick={() => activate.mutate()}>
              Use this one
            </Button>
          )}
          <Tooltip label="Test the connection">
            <ActionIcon variant="default" loading={test.isPending} onClick={() => test.mutate()} aria-label="Test">
              <IconPlugConnected size={16} />
            </ActionIcon>
          </Tooltip>
          <Tooltip label="Duplicate (keeps the key)">
            <ActionIcon variant="default" loading={duplicate.isPending} onClick={() => duplicate.mutate()} aria-label="Duplicate">
              <IconCopy size={16} />
            </ActionIcon>
          </Tooltip>
          <Tooltip label="Edit">
            <ActionIcon variant="default" onClick={onEdit} aria-label="Edit">
              <IconPencil size={16} />
            </ActionIcon>
          </Tooltip>
          <Tooltip label="Delete">
            <ActionIcon variant="subtle" color="red" loading={remove.isPending} onClick={() => remove.mutate()} aria-label="Delete">
              <IconTrash size={16} />
            </ActionIcon>
          </Tooltip>
        </Group>
      </Group>
      {result && (
        <Alert mt="xs" p="xs" color={result.ok ? 'teal' : 'red'} variant="light">
          <Text size="xs">
            {result.ok
              ? `${result.models?.length ?? 0} model(s)${result.model_found === false ? ` — ${profile.model} is not among them` : ''}`
              : result.error}
          </Text>
        </Alert>
      )}
    </Card>
  );
}

/** The saved connectors: where the model is, which one, and how it answers. */
export function LlmProfiles() {
  const [editing, setEditing] = useState<LlmProfile | null | undefined>(undefined);
  const { data, isLoading, error } = useQuery({ queryKey: ['llm-profiles'], queryFn: api.llmProfiles });

  return (
    <Stack gap="sm">
      <Group justify="space-between" align="flex-end">
        <div>
          <Title order={5}>Connectors</Title>
          <Text c="dimmed" size="sm">
            Save as many as you like and switch with one click: a small model on a workstation to try something, a
            bigger one on the GPU server for the real work. The assistant, the agent and the watchdogs all follow
            the one in use. API keys are stored encrypted and never leave the server.
          </Text>
        </div>
        <Button leftSection={<IconPlus size={16} />} onClick={() => setEditing(null)}>
          New connector
        </Button>
      </Group>

      {error && <Alert color="red">{(error as Error).message}</Alert>}
      {isLoading && <Loader size="sm" />}
      {data?.length === 0 && (
        <EmptyState icon={<IconPlugConnected size={24} />} title="No connector yet">
          Save one to tell the assistant where the model is. Until then it uses nothing at all.
        </EmptyState>
      )}
      <Stack gap="xs">
        {data?.map((p) => (
          <Row key={p.id} profile={p} onEdit={() => setEditing(p)} />
        ))}
      </Stack>

      {editing !== undefined && <Editor profile={editing} onClose={() => setEditing(undefined)} />}
    </Stack>
  );
}
