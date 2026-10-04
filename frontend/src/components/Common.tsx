import { ActionIcon, Button, Card, Code, CopyButton, Group, Menu, Paper, Stack, Text, ThemeIcon, Tooltip } from '@mantine/core';
import { IconCheck, IconChevronDown, IconCopy, IconPlayerStop, IconRefresh } from '@tabler/icons-react';
import type { ReactNode } from 'react';
import { useCancel, useRefresh } from '../api/hooks';

export function StatCard({
  label,
  value,
  hint,
  icon,
  color = 'indigo',
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  icon: ReactNode;
  color?: string;
}) {
  return (
    <Card padding="md">
      <Group justify="space-between" align="flex-start" wrap="nowrap">
        <Stack gap={2}>
          <Text size="xs" c="dimmed" tt="uppercase" fw={600} lts={0.3}>
            {label}
          </Text>
          <Text fw={700} fz={26} className="tabular" lh={1.2}>
            {value}
          </Text>
          {hint && (
            <Text size="xs" c="dimmed">
              {hint}
            </Text>
          )}
        </Stack>
        <ThemeIcon variant="light" color={color} size={38} radius="md">
          {icon}
        </ThemeIcon>
      </Group>
    </Card>
  );
}

export function CopyIcon({ value, label = 'Copy' }: { value: string; label?: string }) {
  return (
    <CopyButton value={value} timeout={1500}>
      {({ copied, copy }) => (
        <Tooltip label={copied ? 'Copied!' : label}>
          <ActionIcon variant="subtle" color={copied ? 'teal' : 'gray'} onClick={copy} size="sm">
            {copied ? <IconCheck size={14} /> : <IconCopy size={14} />}
          </ActionIcon>
        </Tooltip>
      )}
    </CopyButton>
  );
}

export function CodeSnippet({ code, title }: { code: string; title?: string }) {
  return (
    <Paper withBorder p={0} style={{ overflow: 'hidden' }}>
      <Group justify="space-between" px="sm" py={4} bg="var(--mantine-color-default-hover)">
        <Text size="xs" fw={600} c="dimmed">
          {title ?? 'Example'}
        </Text>
        <CopyIcon value={code} />
      </Group>
      <Code block fz={12} style={{ borderRadius: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
        {code}
      </Code>
    </Paper>
  );
}

export function RefreshButton({ id, running, size = 'sm' }: { id: string; running: boolean; size?: 'xs' | 'sm' }) {
  const refresh = useRefresh();
  const cancel = useCancel();
  if (running)
    return (
      <Button
        size={size}
        variant="light"
        color="orange"
        leftSection={<IconPlayerStop size={16} />}
        loading={cancel.isPending}
        onClick={() => cancel.mutate(id)}
      >
        Cancel
      </Button>
    );
  return (
    <Group gap={0} wrap="nowrap">
      <Button
        size={size}
        leftSection={<IconRefresh size={16} />}
        loading={refresh.isPending}
        onClick={() => refresh.mutate({ id })}
        style={{ borderTopRightRadius: 0, borderBottomRightRadius: 0 }}
      >
        Update
      </Button>
      <Menu position="bottom-end" withinPortal>
        <Menu.Target>
          <ActionIcon
            size={size === 'xs' ? 30 : 36}
            variant="filled"
            aria-label="Update options"
            style={{ borderTopLeftRadius: 0, borderBottomLeftRadius: 0, borderLeft: '1px solid rgba(255,255,255,.3)' }}
          >
            <IconChevronDown size={16} />
          </ActionIcon>
        </Menu.Target>
        <Menu.Dropdown>
          <Menu.Item leftSection={<IconRefresh size={14} />} onClick={() => refresh.mutate({ id, force: true })}>
            Force a rebuild
            <Text size="xs" c="dimmed">
              Downloads and processes the source again, even if unchanged
            </Text>
          </Menu.Item>
        </Menu.Dropdown>
      </Menu>
    </Group>
  );
}

export function EmptyState({ icon, title, children }: { icon: ReactNode; title: string; children?: ReactNode }) {
  return (
    <Stack align="center" gap="xs" py={48}>
      <ThemeIcon size={56} radius="xl" variant="light" color="gray">
        {icon}
      </ThemeIcon>
      <Text fw={600}>{title}</Text>
      {children && (
        <Text size="sm" c="dimmed" ta="center" maw={520}>
          {children}
        </Text>
      )}
    </Stack>
  );
}
