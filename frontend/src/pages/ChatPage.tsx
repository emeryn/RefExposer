import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Card,
  Group,
  Loader,
  Menu,
  Paper,
  ScrollArea,
  Stack,
  Switch,
  Text,
  Textarea,
  Title,
  Tooltip,
  UnstyledButton,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import {
  IconAlertTriangle,
  IconCheck,
  IconDotsVertical,
  IconDownload,
  IconMessagePlus,
  IconPlayerStop,
  IconRobot,
  IconSend,
  IconSql,
  IconTool,
  IconTrash,
  IconUser,
  IconX,
} from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { askAssistant, type AssistantEvent } from '../api/assistant-stream';
import { api } from '../api/client';
import type { AgentStep, Artifact, ChatMessage } from '../api/types';
import { EmptyState } from '../components/Common';
import { Markdown } from '../components/Markdown';
import { fmtRelative } from '../lib/format';

const EXAMPLES = [
  'Which referentials hold IP addresses, and how fresh are they?',
  'Is 9.9.9.9 in any referential?',
  'Cross the exploited vulnerabilities with their EPSS score and give me the top 20',
  'Which referentials failed their last update, and why?',
];

type Pending = { content: string; steps: AgentStep[] };

export default function ChatPage() {
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const conversationId = params.get('c');
  const { data: status, isLoading: statusLoading } = useQuery({ queryKey: ['assistant'], queryFn: api.assistant });
  const { data: conversations } = useQuery({
    queryKey: ['conversations'],
    queryFn: api.conversations,
    enabled: !!status?.available,
  });
  const { data: detail } = useQuery({
    queryKey: ['conversation', conversationId],
    queryFn: () => api.conversation(conversationId!),
    enabled: !!conversationId && !!status?.available,
  });

  const [question, setQuestion] = useState('');
  const [agent, setAgent] = useState(true);
  const [pending, setPending] = useState<Pending | null>(null);
  const [asked, setAsked] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  const messages = useMemo(() => detail?.messages ?? [], [detail]);
  const busy = pending !== null;
  /**
   * The question being answered is shown from `asked`, not from the conversation.
   *
   * Starting a conversation puts its id in the URL, which makes the query below load it while the answer is
   * still streaming — and the question is already stored by then, so it would appear twice. The stored copy
   * is dropped while it is the one in flight.
   */
  const stored = useMemo(() => {
    if (asked === null) return messages;
    const last = messages[messages.length - 1];
    return last?.role === 'user' && last.content === asked ? messages.slice(0, -1) : messages;
  }, [messages, asked]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [stored.length, asked, pending?.content, pending?.steps.length]);

  const select = useCallback(
    (id: string | null) => {
      setParams(id ? { c: id } : {}, { replace: true });
      setError(null);
    },
    [setParams],
  );

  const start = useMutation({
    mutationFn: api.newConversation,
    onSuccess: (conv) => {
      qc.invalidateQueries({ queryKey: ['conversations'] });
      select(conv.id);
    },
  });
  const remove = useMutation({
    mutationFn: api.deleteConversation,
    onSuccess: (_, id) => {
      qc.invalidateQueries({ queryKey: ['conversations'] });
      if (id === conversationId) select(null);
    },
  });
  const reset = useMutation({
    mutationFn: api.resetConversations,
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ['conversations'] });
      select(null);
      notifications.show({ color: 'teal', icon: <IconCheck size={16} />, message: `${r.deleted} conversation(s) deleted` });
    },
  });

  async function send(text: string) {
    const content = text.trim();
    if (!content || busy) return;
    let id = conversationId;
    if (!id) {
      id = (await api.newConversation()).id;
      qc.invalidateQueries({ queryKey: ['conversations'] });
      select(id);
    }
    setQuestion('');
    setAsked(content);
    setError(null);
    setPending({ content: '', steps: [] });
    const controller = new AbortController();
    abort.current = controller;
    try {
      await askAssistant(
        id,
        content,
        agent && !!status?.agent,
        (event: AssistantEvent) => {
          if (event.type === 'token') {
            setPending((p) => ({ steps: p?.steps ?? [], content: (p?.content ?? '') + event.text }));
          } else if (event.type === 'step') {
            const { type, ...step } = event;
            setPending((p) => ({ content: p?.content ?? '', steps: [...(p?.steps ?? []), step] }));
          } else if (event.type === 'error') {
            setError(event.message);
          }
        },
        controller.signal,
      );
    } catch (e) {
      if ((e as Error).name !== 'AbortError') setError((e as Error).message);
    } finally {
      abort.current = null;
      // The conversation is reloaded before the optimistic question and answer are dropped, so the exchange
      // never blinks out between the two
      await qc.invalidateQueries({ queryKey: ['conversation', id] });
      setPending(null);
      setAsked(null);
      qc.invalidateQueries({ queryKey: ['conversations'] });
    }
  }

  function stop() {
    abort.current?.abort();
  }

  if (statusLoading) return <Loader />;
  if (!status?.available) {
    return (
      <Stack gap="lg">
        <div>
          <Title order={2}>Assistant</Title>
        </div>
        <Alert color="gray" icon={<IconRobot size={18} />} title="The assistant is not available">
          <Text size="sm">
            {status?.enabled && !status?.configured
              ? 'No inference server or no model is configured yet.'
              : 'An administrator has not enabled it for this account.'}{' '}
            It is set up in <Link to="/admin/settings/assistant">Settings › Assistant</Link>.
          </Text>
        </Alert>
      </Stack>
    );
  }

  return (
    <Stack
      gap="md"
      style={{
        // Fills the content area of the shell: its header, and its padding above and below
        height: 'calc(100dvh - var(--app-shell-header-height, 60px) - 2 * var(--mantine-spacing-lg))',
        minHeight: 420,
      }}
    >
      <Group justify="space-between" align="flex-end" wrap="nowrap">
        <div>
          <Title order={2}>Assistant</Title>
          <Text c="dimmed" size="sm">
            Answers from the referentials you may read, with {status.model}.
            {status.agent ? ' It can query them for you.' : ' Tools are disabled: it answers from the conversation only.'}
            {status.confidential ? '' : ' Confidential referentials are out of its reach.'}
          </Text>
        </div>
        <Group gap="xs" wrap="nowrap">
          {status.agent && (
            <Tooltip label="Let the assistant query the referentials and act with your own rights">
              <Switch
                checked={agent}
                onChange={(e) => setAgent(e.currentTarget.checked)}
                label="Agent"
                disabled={busy}
              />
            </Tooltip>
          )}
          <Button
            variant="light"
            leftSection={<IconMessagePlus size={16} />}
            onClick={() => start.mutate()}
            loading={start.isPending}
            disabled={busy}
          >
            New
          </Button>
          <Menu position="bottom-end" shadow="md">
            <Menu.Target>
              <ActionIcon variant="default" size="lg" aria-label="Conversations">
                <IconDotsVertical size={18} />
              </ActionIcon>
            </Menu.Target>
            <Menu.Dropdown>
              <Menu.Label>Conversations</Menu.Label>
              {(conversations ?? []).map((c) => (
                <Menu.Item
                  key={c.id}
                  onClick={() => select(c.id)}
                  rightSection={
                    <Text size="xs" c="dimmed">
                      {fmtRelative(c.updated_at)}
                    </Text>
                  }
                  fw={c.id === conversationId ? 700 : 400}
                >
                  <Text size="sm" lineClamp={1} maw={260}>
                    {c.title}
                  </Text>
                </Menu.Item>
              ))}
              {!conversations?.length && <Menu.Item disabled>No conversation yet</Menu.Item>}
              <Menu.Divider />
              {conversationId && (
                <Menu.Item
                  color="red"
                  leftSection={<IconTrash size={14} />}
                  onClick={() => remove.mutate(conversationId)}
                  disabled={busy}
                >
                  Delete this conversation
                </Menu.Item>
              )}
              <Menu.Item
                color="red"
                leftSection={<IconTrash size={14} />}
                onClick={() => reset.mutate()}
                disabled={busy || !conversations?.length}
              >
                Delete all and start over
              </Menu.Item>
            </Menu.Dropdown>
          </Menu>
        </Group>
      </Group>

      {/* minHeight 0: without it a flex child refuses to shrink and the scroll area overflows the page */}
      <ScrollArea style={{ flex: 1, minHeight: 0 }} offsetScrollbars>
        <Stack gap="md" pb="md">
          {!messages.length && !busy && (
            <EmptyState icon={<IconRobot size={28} />} title="Ask about your referentials">
              <Stack gap={6} mt="xs" align="flex-start">
                {EXAMPLES.map((e) => (
                  <UnstyledButton key={e} onClick={() => send(e)}>
                    <Text size="sm" c="indigo">
                      ▸ {e}
                    </Text>
                  </UnstyledButton>
                ))}
              </Stack>
            </EmptyState>
          )}
          {stored.map((m) => (
            <Message key={m.id} message={m} />
          ))}
          {asked && <Bubble role="user">{asked}</Bubble>}
          {pending && (
            <Stack gap="xs">
              {pending.steps.map((s, i) => (
                <StepRow key={i} step={s} />
              ))}
              <Bubble role="assistant">
                {pending.content ? (
                  <Markdown text={pending.content} />
                ) : (
                  <Group gap="xs">
                    <Loader size="xs" type="dots" />
                    <Text size="sm" c="dimmed">
                      {pending.steps.length ? 'reading the answers…' : 'thinking…'}
                    </Text>
                  </Group>
                )}
              </Bubble>
            </Stack>
          )}
          {error && (
            <Alert color="red" icon={<IconAlertTriangle size={18} />} title="The assistant could not answer">
              <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>
                {error}
              </Text>
            </Alert>
          )}
          <div ref={bottom} />
        </Stack>
      </ScrollArea>

      <Card padding="xs" withBorder>
        <Textarea
          placeholder="Ask a question about the referentials…"
          autosize
          minRows={2}
          maxRows={8}
          value={question}
          maxLength={status.max_chars}
          onChange={(e) => setQuestion(e.currentTarget.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              send(question);
            }
          }}
          variant="unstyled"
          px="xs"
        />
        <Group justify="space-between" mt={4} px="xs">
          <Text size="xs" c="dimmed">
            Enter sends, Shift+Enter adds a line. The assistant only sees what your account may read.
          </Text>
          {busy ? (
            <Button size="xs" variant="default" leftSection={<IconPlayerStop size={14} />} onClick={stop}>
              Stop
            </Button>
          ) : (
            <Button size="xs" leftSection={<IconSend size={14} />} onClick={() => send(question)} disabled={!question.trim()}>
              Send
            </Button>
          )}
        </Group>
      </Card>
    </Stack>
  );
}

function Bubble({ role, children }: { role: 'user' | 'assistant'; children: React.ReactNode }) {
  const user = role === 'user';
  return (
    <Group gap="sm" align="flex-start" wrap="nowrap" justify={user ? 'flex-end' : 'flex-start'}>
      {!user && (
        <ActionIcon variant="light" color="indigo" radius="xl" size="md" component="div" aria-hidden>
          <IconRobot size={16} />
        </ActionIcon>
      )}
      <Paper
        withBorder={!user}
        bg={user ? 'var(--mantine-color-indigo-light)' : undefined}
        p="sm"
        radius="md"
        maw={user ? 640 : '100%'}
        style={{ flex: user ? undefined : 1 }}
      >
        {typeof children === 'string' ? (
          <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>
            {children}
          </Text>
        ) : (
          children
        )}
      </Paper>
      {user && (
        <ActionIcon variant="default" radius="xl" size="md" component="div" aria-hidden>
          <IconUser size={16} />
        </ActionIcon>
      )}
    </Group>
  );
}

function Message({ message }: { message: ChatMessage }) {
  if (message.role === 'user') return <Bubble role="user">{message.content}</Bubble>;
  return (
    <Stack gap="xs">
      {(message.steps ?? []).map((s, i) => (
        <StepRow key={i} step={s} />
      ))}
      <Bubble role="assistant">
        <Stack gap="sm">
          {message.content ? <Markdown text={message.content} /> : <Text size="sm" c="dimmed">No answer.</Text>}
          {(message.artifacts ?? []).map((a, i) => (
            <ArtifactCard key={i} artifact={a} />
          ))}
          {message.error && (
            <Text size="xs" c="orange">
              {message.error}
            </Text>
          )}
        </Stack>
      </Bubble>
    </Stack>
  );
}

/** One tool call of the agent: what it did, and whether it worked. */
function StepRow({ step }: { step: AgentStep }) {
  const args = Object.entries(step.arguments ?? {})
    .map(([k, v]) => `${k}=${typeof v === 'object' ? JSON.stringify(v) : String(v)}`)
    .join(' · ');
  return (
    <Group gap="xs" wrap="nowrap" pl={44}>
      <IconTool size={14} opacity={0.6} />
      <Text size="xs" ff="monospace" c="dimmed" lineClamp={1}>
        {step.tool}
        {args ? ` (${args})` : ''}
      </Text>
      <Badge size="xs" variant="light" color={step.ok ? 'teal' : 'orange'} leftSection={step.ok ? null : <IconX size={10} />}>
        {step.summary}
      </Badge>
    </Group>
  );
}

const MIME: Record<string, string> = {
  json: 'application/json',
  csv: 'text/csv',
  sql: 'application/sql',
  yaml: 'application/yaml',
  txt: 'text/plain',
};

function ArtifactCard({ artifact }: { artifact: Artifact }) {
  const navigate = useNavigate();
  if (artifact.kind === 'sql') {
    return (
      <Card withBorder padding="xs">
        <Group justify="space-between" wrap="nowrap" mb={6}>
          <Text size="sm" fw={600}>
            {artifact.title}
          </Text>
          <Button
            size="xs"
            variant="light"
            leftSection={<IconSql size={14} />}
            onClick={() => navigate(`/sql?sql=${encodeURIComponent(artifact.sql)}`)}
          >
            Open in the SQL console
          </Button>
        </Group>
        {artifact.explanation && (
          <Text size="xs" c="dimmed" mb={6}>
            {artifact.explanation}
          </Text>
        )}
        <Text size="xs" ff="monospace" style={{ whiteSpace: 'pre-wrap' }}>
          {artifact.sql}
        </Text>
      </Card>
    );
  }
  const download = () => {
    const url = URL.createObjectURL(new Blob([artifact.content], { type: MIME[artifact.format] ?? 'text/plain' }));
    const a = document.createElement('a');
    a.href = url;
    a.download = artifact.filename;
    a.click();
    URL.revokeObjectURL(url);
  };
  return (
    <Card withBorder padding="xs">
      <Group justify="space-between" wrap="nowrap">
        <div>
          <Text size="sm" fw={600}>
            {artifact.filename}
          </Text>
          <Text size="xs" c="dimmed">
            {artifact.format.toUpperCase()} · {artifact.content.length.toLocaleString()} characters
          </Text>
        </div>
        <Button size="xs" variant="light" leftSection={<IconDownload size={14} />} onClick={download}>
          Download
        </Button>
      </Group>
    </Card>
  );
}
