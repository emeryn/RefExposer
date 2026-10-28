import { Badge, Code, Collapse, Group, Text, UnstyledButton } from '@mantine/core';
import { IconChevronDown, IconChevronRight, IconTool, IconX } from '@tabler/icons-react';
import { useState } from 'react';
import type { AgentStep } from '../api/types';
import { fmtDuration } from '../lib/format';

/**
 * The trace of an agent: one line per tool call, expandable to what the tool answered.
 *
 * The same component shows the steps of a chat answer, of a watchdog investigation while it runs, and of the
 * one behind an alert — so reading what the agent did is the same gesture everywhere.
 */
export function StepRow({ step }: { step: AgentStep }) {
  const [open, setOpen] = useState(false);
  const args = Object.entries(step.arguments ?? {})
    .map(([k, v]) => `${k}=${typeof v === 'object' ? JSON.stringify(v) : String(v)}`)
    .join(' · ');
  // Older steps (stored before the result was kept) have nothing to unfold
  const expandable = !!step.result;
  return (
    <div>
      <UnstyledButton
        onClick={() => expandable && setOpen((v) => !v)}
        w="100%"
        style={{ cursor: expandable ? 'pointer' : 'default' }}
        aria-expanded={expandable ? open : undefined}
      >
        <Group gap="xs" wrap="nowrap">
          {expandable ? (
            open ? (
              <IconChevronDown size={13} opacity={0.6} />
            ) : (
              <IconChevronRight size={13} opacity={0.6} />
            )
          ) : (
            <IconTool size={13} opacity={0.6} />
          )}
          <Text size="xs" ff="monospace" c="dimmed" lineClamp={1}>
            {step.tool}
            {args ? ` (${args})` : ''}
          </Text>
          <Badge
            size="xs"
            variant="light"
            color={step.ok ? 'teal' : 'orange'}
            leftSection={step.ok ? null : <IconX size={10} />}
            style={{ flexShrink: 0 }}
          >
            {step.summary}
          </Badge>
          {step.ms != null && (
            <Text size="xs" c="dimmed" style={{ flexShrink: 0 }}>
              {fmtDuration(step.ms / 1000)}
            </Text>
          )}
        </Group>
      </UnstyledButton>
      <Collapse in={open}>
        <Code block fz={11} mt={4} mb={4} style={{ maxHeight: 280, overflow: 'auto' }}>
          {step.result}
        </Code>
      </Collapse>
    </div>
  );
}

export function AgentSteps({ steps, pl = 0 }: { steps: AgentStep[]; pl?: number }) {
  if (!steps.length) return null;
  return (
    <div style={{ paddingLeft: pl }}>
      {steps.map((s, i) => (
        <StepRow key={i} step={s} />
      ))}
    </div>
  );
}
