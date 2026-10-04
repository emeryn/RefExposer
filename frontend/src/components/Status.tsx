import { Badge, Box, Group, Progress, Stack, Text, Tooltip } from '@mantine/core';
import {
  IconAlertTriangle,
  IconBan,
  IconCircleCheck,
  IconCircleDashed,
  IconClockExclamation,
  IconEqual,
  IconFileAlert,
  IconLoader2,
  IconPlayerStop,
  IconX,
} from '@tabler/icons-react';
import type { Health, Run, RunStatus } from '../api/types';
import { fmtBytes } from '../lib/format';

export const HEALTH: Record<Health, { label: string; color: string; icon: typeof IconCircleCheck; help: string }> = {
  ok: { label: 'Up to date', color: 'teal', icon: IconCircleCheck, help: 'Data available and fresh' },
  stale: { label: 'Stale', color: 'yellow', icon: IconClockExclamation, help: 'Last successful update is older than expected' },
  error: { label: 'Error', color: 'red', icon: IconAlertTriangle, help: 'The last update failed (the published version, if any, is kept)' },
  running: { label: 'Updating', color: 'blue', icon: IconLoader2, help: 'Update in progress' },
  empty: { label: 'No data', color: 'gray', icon: IconCircleDashed, help: 'Nothing imported yet' },
  disabled: { label: 'Disabled', color: 'gray', icon: IconBan, help: 'Referential disabled in its definition' },
};

export function HealthBadge({ health, size = 'sm' }: { health: Health; size?: 'xs' | 'sm' | 'md' | 'lg' }) {
  const h = HEALTH[health];
  const Icon = h.icon;
  return (
    <Tooltip label={h.help}>
      <Badge
        color={h.color}
        variant="light"
        size={size}
        style={{ flexShrink: 0 }}
        leftSection={<Icon size={12} className={health === 'running' ? 'spin' : undefined} />}
      >
        {h.label}
      </Badge>
    </Tooltip>
  );
}

export const RUN_STATUS: Record<RunStatus, { label: string; color: string; icon: typeof IconCircleCheck; help?: string }> = {
  queued: { label: 'Queued', color: 'gray', icon: IconCircleDashed },
  running: { label: 'Running', color: 'blue', icon: IconLoader2 },
  success: { label: 'Published', color: 'teal', icon: IconCircleCheck },
  unchanged: { label: 'Unchanged', color: 'indigo', icon: IconEqual },
  error: { label: 'Error', color: 'red', icon: IconX },
  rejected: { label: 'Rejected', color: 'orange', icon: IconAlertTriangle, help: 'The new version failed validation; the previous one is kept' },
  corrupted: { label: 'Corrupted source', color: 'red', icon: IconFileAlert, help: 'The source is empty or unusable; the published version is kept' },
  cancelled: { label: 'Cancelled', color: 'gray', icon: IconPlayerStop },
};

export const TRIGGERS: Record<string, string> = {
  manual: 'Manual',
  schedule: 'Scheduled',
  startup: 'Startup',
  upload: 'Manual upload',
  inbox: 'Import folder',
  sync: 'Sync folder',
  edit: 'Edit',
};

export function RunStatusBadge({ status }: { status: RunStatus }) {
  const s = RUN_STATUS[status] ?? RUN_STATUS.queued;
  const Icon = s.icon;
  const badge = (
    <Badge color={s.color} variant="light" size="sm" leftSection={<Icon size={12} />}>
      {s.label}
    </Badge>
  );
  return s.help ? <Tooltip label={s.help}>{badge}</Tooltip> : badge;
}

const PHASES: Record<string, string> = {
  queued: 'Queued',
  download: 'Downloading',
  transform: 'Transforming',
  validate: 'Validating',
  profile: 'Profiling',
  downloads: 'Preparing downloads',
  publish: 'Publishing',
  done: 'Done',
};

export function RunProgress({ run, compact = false }: { run: Run; compact?: boolean }) {
  const { done, total } = run.progress;
  const downloading = run.phase === 'download';
  const pct = downloading && total > 0 ? Math.min(100, (done / total) * 100) : null;
  return (
    <Stack gap={4}>
      <Group justify="space-between" gap="xs">
        <Text size="xs" fw={500} c="blue">
          {PHASES[run.phase] ?? run.phase}
        </Text>
        {downloading && done > 0 && (
          <Text size="xs" c="dimmed" className="tabular">
            {fmtBytes(done)}
            {total > 0 && ` / ${fmtBytes(total)}`}
          </Text>
        )}
      </Group>
      <Progress size={compact ? 'sm' : 'md'} value={pct ?? 100} animated={pct == null} striped={pct == null} aria-label="Progress" />
    </Stack>
  );
}

export function HealthDot({ health }: { health: Health }) {
  const color = `var(--mantine-color-${HEALTH[health].color}-6)`;
  return (
    <Box
      aria-label={HEALTH[health].label}
      w={9}
      h={9}
      style={{
        borderRadius: 99,
        background: color,
        boxShadow: health === 'running' ? `0 0 0 3px var(--mantine-color-${HEALTH[health].color}-light)` : undefined,
      }}
    />
  );
}
