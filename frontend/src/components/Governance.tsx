import { Avatar, Badge, Group, Switch, Text, Tooltip } from '@mantine/core';
import { IconEyeOff, IconLock, IconUser, IconWorld } from '@tabler/icons-react';
import type { PiiState, Visibility } from '../api/types';

const STRATEGY: Record<string, string> = {
  hide: 'replaced by ***',
  partial: 'first and last character kept',
  hash: 'replaced by a stable pseudonym',
};

/** Internal (company data) or public (open data, external feed) — told apart at a glance. */
export function VisibilityBadge({ visibility, size = 'xs' }: { visibility: Visibility; size?: 'xs' | 'sm' | 'md' }) {
  const internal = visibility !== 'public';
  return (
    <Tooltip
      multiline
      maw={320}
      label={
        internal
          ? 'Internal referential: company data. Treat it as such — it is not published anywhere else.'
          : 'Public referential: open data or an external feed, already published outside the company.'
      }
    >
      <Badge
        size={size}
        variant="filled"
        color={internal ? 'violet' : 'teal'}
        leftSection={internal ? <IconLock size={10} /> : <IconWorld size={10} />}
      >
        {internal ? 'Internal' : 'Public'}
      </Badge>
    </Tooltip>
  );
}

/** The personal data columns of a referential and how they are served. */
export function MaskedBadge({ pii, size = 'xs' }: { pii: PiiState; size?: 'xs' | 'sm' | 'md' }) {
  return (
    <Tooltip
      multiline
      maw={360}
      label={
        `Personal data: ${pii.columns.join(', ')} — ${STRATEGY[pii.strategy] ?? pii.strategy}. ` +
        (pii.can_reveal ? 'You may ask to see the real values.' : 'You may not see the real values.') +
        (pii.note ? ` ${pii.note}` : '')
      }
    >
      <Badge size={size} variant="light" color="orange" leftSection={<IconEyeOff size={10} />}>
        {pii.columns.length} masked
      </Badge>
    </Tooltip>
  );
}

/** Who answers for the referential. */
export function Owner({ owner, team, size = 'sm' }: { owner: string | null; team?: string | null; size?: 'xs' | 'sm' }) {
  if (!owner && !team) return null;
  const label = owner || team!;
  const initials = label
    .replace(/@.*/, '')
    .split(/[\s._-]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((p) => p[0]!.toUpperCase())
    .join('');
  return (
    <Tooltip label={`Owner: ${owner ?? '—'}${team ? ` · ${team}` : ''}`}>
      <Group gap={6} wrap="nowrap" style={{ minWidth: 0 }}>
        <Avatar size={size === 'xs' ? 18 : 22} radius="xl" color="blue">
          <Text size="10px" fw={700}>
            {initials || <IconUser size={10} />}
          </Text>
        </Avatar>
        <Text size={size} c="dimmed" truncate>
          {owner ?? team}
          {owner && team ? ` · ${team}` : ''}
        </Text>
      </Group>
    </Tooltip>
  );
}

/**
 * The explicit opt-in: masked is what everyone gets, and asking for the real values is a deliberate act
 * which is written to the audit log. Shown only to an account that may lift it.
 */
export function RevealSwitch({
  pii,
  value,
  onChange,
  label = 'Show personal data',
}: {
  pii: PiiState | null | undefined;
  value: boolean;
  onChange: (v: boolean) => void;
  label?: string;
}) {
  if (!pii || !pii.columns.length || !pii.can_reveal) return null;
  return (
    <Tooltip label={`Shows ${pii.columns.join(', ')} in clear. Each request is recorded in the audit log.`}>
      <Switch
        size="sm"
        color="orange"
        checked={value}
        onChange={(e) => onChange(e.currentTarget.checked)}
        label={
          <Group gap={4} wrap="nowrap">
            <IconEyeOff size={14} />
            <Text size="sm">{label}</Text>
          </Group>
        }
      />
    </Tooltip>
  );
}
