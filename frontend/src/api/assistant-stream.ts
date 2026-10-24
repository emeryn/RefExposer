import type { AgentStep, Artifact } from './types';

/** Events the backend sends while it answers (server-sent events over the POST response). */
export type AssistantEvent =
  | { type: 'token'; text: string }
  | ({ type: 'step' } & AgentStep)
  | { type: 'error'; message: string }
  | {
      type: 'done';
      content: string;
      steps: AgentStep[];
      artifacts: Artifact[];
      usage: Record<string, number>;
      error: string | null;
    };

/**
 * Ask a question and read the answer as it is written.
 *
 * `EventSource` cannot POST, so the stream is read from the response body. `signal` stops the generation:
 * aborting the request closes it, and the backend stops asking the model.
 */
export async function askAssistant(
  conversationId: string,
  content: string,
  agent: boolean,
  onEvent: (event: AssistantEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`/api/assistant/conversations/${encodeURIComponent(conversationId)}/messages`, {
    method: 'POST',
    credentials: 'same-origin',
    signal,
    headers: {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
      'X-Requested-With': 'RefExposer',
    },
    body: JSON.stringify({ content, agent }),
  });
  if (!res.ok || !res.body) {
    let message = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (typeof body.detail === 'string') message = body.detail;
    } catch {
      /* not JSON */
    }
    throw new Error(message);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    // Events are separated by a blank line; an incomplete one stays in the buffer
    const parts = buffer.split('\n\n');
    buffer = parts.pop() ?? '';
    for (const part of parts) {
      const line = part.split('\n').find((l) => l.startsWith('data: '));
      if (!line) continue;
      const payload = line.slice(6);
      if (payload === '[DONE]') return;
      try {
        onEvent(JSON.parse(payload) as AssistantEvent);
      } catch {
        /* a partial event is dropped rather than breaking the stream */
      }
    }
  }
}
