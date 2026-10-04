import {
  Alert,
  Anchor,
  Badge,
  Button,
  Card,
  Group,
  Loader,
  SegmentedControl,
  Select,
  SimpleGrid,
  Stack,
  Text,
  TextInput,
  Title,
  Tooltip,
} from '@mantine/core';
import {
  IconAlertTriangle,
  IconArrowRight,
  IconClock,
  IconDatabase,
  IconDatabaseEdit,
  IconDatabasePlus,
  IconFileImport,
  IconFolderShare,
  IconFilter,
  IconHeartbeat,
  IconKey,
  IconListNumbers,
  IconPin,
  IconServer2,
  IconUserQuestion,
} from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api/client';
import { useReferentials } from '../api/hooks';
import { useAuth } from '../auth/AuthContext';
import type { ReferentialSummary } from '../api/types';
import { EmptyState, RefreshButton, StatCard } from '../components/Common';
import { HealthBadge, RunProgress, RunStatusBadge, TRIGGERS } from '../components/Status';
import { describeCron, fmtBytes, fmtCompact, fmtDelta, fmtDuration, fmtNumber, fmtRelative } from '../lib/format';

function RefCard({ r }: { r: ReferentialSummary }) {
  const delta = r.row_count != null && r.previous_row_count != null ? r.row_count - r.previous_row_count : null;
  return (
    <Card className="ref-card" padding="lg">
      <Stack gap="sm" h="100%">
        <Group justify="space-between" align="flex-start" wrap="nowrap">
          <Stack gap={4} style={{ minWidth: 0 }}>
            <Anchor component={Link} to={`/r/${r.id}`} fw={700} size="md" c="inherit" lineClamp={1}>
              {r.name}
            </Anchor>
            <Group gap={6}>
              <Badge size="xs" variant="light" color="grape">
                {r.category}
              </Badge>
              <Badge size="xs" variant="default" tt="uppercase">
                {r.format}
              </Badge>
              {r.large && (
                <Badge size="xs" variant="light" color="indigo">
                  large volume
                </Badge>
              )}
              {r.kind === 'internal' && (
              <Tooltip label="Internal referential: managed in RefExposer, no remote source">
              <Badge size="xs" variant="light" color="grape" leftSection={<IconDatabaseEdit size={10} />}>
              internal
              </Badge>
              </Tooltip>
              )}
              {r.manual_import && (
              <Tooltip label={`Current version imported manually (${r.manual_import.origin === 'upload' ? 'upload' : 'import folder'})`}>
              <Badge size="xs" variant="light" color="indigo" leftSection={<IconFileImport size={10} />}>
              manual import
              </Badge>
              </Tooltip>
              )}
              {r.sync_path && (
                <Tooltip label={`Synchronized from ${r.sync_path}`}>
                  <Badge size="xs" variant="light" color="teal" leftSection={<IconFolderShare size={10} />}>
                    synced
                  </Badge>
                </Tooltip>
              )}
              {r.pinned && (
              <Tooltip label="Version frozen: scheduled updates are suspended">
              <Badge size="xs" variant="light" color="indigo" leftSection={<IconPin size={10} />}>
              frozen
              </Badge>
              </Tooltip>
              )}
              {r.key && (
                <Tooltip label="Referential key">
                  <Badge size="xs" variant="default" tt="none" leftSection={<IconKey size={10} />}>
                    {r.key}
                  </Badge>
                </Tooltip>
              )}
            </Group>
          </Stack>
          <HealthBadge health={r.health} />
        </Group>

        <Text size="sm" c="dimmed" lineClamp={2} mih={40}>
          {r.description || 'No description.'}
        </Text>

        <SimpleGrid cols={3} spacing="xs">
          <div>
            <Text size="xs" c="dimmed">
              {r.kind === 'bloom' ? 'Elements' : r.kind === 'mmdb' ? 'Nodes' : 'Rows'}
            </Text>
            <Group gap={4} align="baseline" wrap="nowrap">
              <Text fw={700} className="tabular">
                {fmtCompact(r.row_count)}
              </Text>
              {delta != null && delta !== 0 && (
                <Text size="xs" c={delta > 0 ? 'teal' : 'red'} className="tabular">
                  {fmtDelta(delta)}
                </Text>
              )}
            </Group>
          </div>
          <div>
            <Text size="xs" c="dimmed">
              Columns
            </Text>
            <Text fw={700} className="tabular">
              {r.column_count || '—'}
            </Text>
          </div>
          <div>
            <Text size="xs" c="dimmed">
              Parquet
            </Text>
            <Text fw={700} className="tabular">
              {fmtBytes(r.parquet_size)}
            </Text>
          </div>
        </SimpleGrid>

        {r.current_run ? (
          <RunProgress run={r.current_run} compact />
        ) : r.last_error && (r.health === 'error' || r.status === 'corrupted') ? (
          <Alert color={r.status === 'corrupted' ? 'orange' : 'red'} variant="light" p="xs" icon={<IconAlertTriangle size={16} />}>
            <Text size="xs" lineClamp={2}>
              {r.status === 'corrupted' ? 'Corrupted source, current version kept: ' : ''}
              {r.last_error}
            </Text>
          </Alert>
        ) : (
          <Stack gap={2}>
            <Text size="xs" c="dimmed">
              <IconDatabase size={12} style={{ verticalAlign: -1 }} /> Data changed {fmtRelative(r.data_updated_at)}
              {r.last_checked_at && r.kind !== 'internal' && <> · checked {fmtRelative(r.last_checked_at)}</>}
            </Text>
            <Text size="xs" c="dimmed">
              <IconClock size={12} style={{ verticalAlign: -1 }} /> {r.kind === 'internal' ? 'Published on every edit' : r.sync_path ? 'When its files change' : r.pinned ? 'Updates suspended' : describeCron(r.schedule)}
              {r.next_run_at && !r.pinned && r.kind !== 'internal' && <> · next {fmtRelative(r.next_run_at)}</>}
            </Text>
          </Stack>
        )}

        <Group justify="space-between" mt="auto">
          <Button
            component={Link}
            to={`/r/${r.id}`}
            variant="light"
            size="xs"
            rightSection={<IconArrowRight size={14} />}
            disabled={!r.has_data && r.kind !== 'internal'}
          >
            Explore
          </Button>
          {r.access === 'manage' && r.kind !== 'internal' && <RefreshButton id={r.id} running={!!r.current_run} size="xs" />}
          {r.access === 'manage' && r.kind === 'internal' && (
          <Button component={Link} to={`/r/${r.id}/edit`} variant="default" size="xs" leftSection={<IconDatabaseEdit size={14} />}>
          Edit
          </Button>
          )}
        </Group>
      </Stack>
    </Card>
  );
}

function Activity() {
  const { data } = useQuery({ queryKey: ['activity'], queryFn: () => api.activity(12), refetchInterval: 5000 });
  return (
    <Card padding="lg">
      <Title order={4} mb="sm">
        Recent activity
      </Title>
      {!data?.length ? (
        <Text size="sm" c="dimmed">
          No update yet.
        </Text>
      ) : (
        <Stack gap="xs">
          {data.map((run) => {
            const delta = run.rows != null && run.previous_rows != null ? run.rows - run.previous_rows : null;
            return (
              <Group key={run.id} justify="space-between" wrap="nowrap" gap="sm">
                <Group gap="sm" wrap="nowrap" style={{ minWidth: 0 }}>
                  <RunStatusBadge status={run.status} />
                  <div style={{ minWidth: 0 }}>
                    <Anchor component={Link} to={`/r/${run.ref_id}/history`} size="sm" fw={600} c="inherit" truncate>
                      {run.ref_name ?? run.ref_id}
                    </Anchor>
                    <Text size="xs" c="dimmed" truncate>
                      {run.message ?? run.phase}
                      {run.changes && ` · +${fmtNumber(run.changes.added)} / −${fmtNumber(run.changes.removed)}`}
                      {delta != null && !run.changes && delta !== 0 && ` · ${fmtDelta(delta)} rows`}
                    </Text>
                  </div>
                </Group>
                <Stack gap={0} align="flex-end" style={{ flexShrink: 0 }}>
                  <Text size="xs">{fmtRelative(run.finished_at ?? run.started_at ?? run.queued_at)}</Text>
                  <Text size="xs" c="dimmed">
                    {(TRIGGERS[run.trigger] ?? run.trigger).toLowerCase()} ·{' '}
                    {fmtDuration(run.duration)}
                  </Text>
                </Stack>
              </Group>
            );
          })}
        </Stack>
      )}
    </Card>
  );
}

export default function Dashboard() {
  const { data: refs, isLoading, error } = useReferentials();
  const { me } = useAuth();
  const { data: system } = useQuery({ queryKey: ['system'], queryFn: api.system, refetchInterval: 30000, enabled: !!me?.is_admin });
  const [filter, setFilter] = useState('');
  const [health, setHealth] = useState('all');
  const [category, setCategory] = useState<string | null>(null);

  const categories = useMemo(() => [...new Set((refs ?? []).map((r) => r.category))].sort(), [refs]);
  const filtered = useMemo(() => {
    const f = filter.trim().toLowerCase();
    return (refs ?? []).filter((r) => {
      if (category && r.category !== category) return false;
      if (health === 'attention' && !['error', 'stale', 'empty'].includes(r.health)) return false;
      if (health === 'ok' && r.health !== 'ok') return false;
      if (health === 'running' && r.health !== 'running') return false;
      if (!f) return true;
      return [r.name, r.id, r.description, r.category, ...r.tags].join(' ').toLowerCase().includes(f);
    });
  }, [refs, filter, health, category]);

  if (isLoading)
    return (
      <Group justify="center" py="xl">
        <Loader />
      </Group>
    );
  if (error) return <Alert color="red" title="API unavailable">{(error as Error).message}</Alert>;

  const all = refs ?? [];
  const totalRows = all.reduce((s, r) => s + (r.row_count ?? 0), 0);
  const storage = all.reduce((s, r) => s + (r.storage_size ?? 0), 0);
  const okCount = all.filter((r) => r.health === 'ok').length;
  const attention = all.filter((r) => ['error', 'stale'].includes(r.health)).length;
  const running = all.filter((r) => r.health === 'running').length;

  return (
    <Stack gap="lg">
      <Group justify="space-between" align="flex-end">
        <div>
          <Title order={2}>Dashboard</Title>
          <Text c="dimmed" size="sm">
            State of the exposed referentials, their updates and their freshness.
          </Text>
        </div>
        {me?.can_create_internal && (
        <Button component={Link} to="/internal/new" variant="light" leftSection={<IconDatabasePlus size={16} />}>
        New internal referential
        </Button>
        )}
      </Group>

      {!!me?.pending_requests && (
        <Alert color="orange" icon={<IconUserQuestion />} title={`${me.pending_requests} pending access request(s)`}>
          <Text size="sm">
            Users signed in through the directory or OpenID Connect and are waiting for approval.{' '}
            <Anchor component={Link} to="/admin/users">
              Review the requests
            </Anchor>
          </Text>
        </Alert>
      )}

      {!!system?.config_errors.length && (
        <Alert color="orange" icon={<IconAlertTriangle />} title={`${system.config_errors.length} configuration error(s)`}>
          <Stack gap={2}>
            {system.config_errors.map((e) => (
              <Text size="sm" key={e.file}>
                <b>{e.file}</b>: {e.error}
              </Text>
            ))}
          </Stack>
        </Alert>
      )}

      <SimpleGrid cols={{ base: 1, xs: 2, lg: 4 }}>
        <StatCard
          label="Referentials"
          value={all.length}
          hint={`${all.filter((r) => r.has_data).length} with data · ${categories.length} categories`}
          icon={<IconDatabase size={20} />}
        />
        <StatCard label="Rows served" value={fmtCompact(totalRows)} hint={`${fmtNumber(totalRows)} rows`} icon={<IconListNumbers size={20} />} color="cyan" />
        <StatCard label="Storage" value={fmtBytes(storage)} hint="Parquet + source files" icon={<IconServer2 size={20} />} color="grape" />
        <StatCard
          label="Health"
          value={
            <>
              {okCount}
              <Text span c="dimmed" fz={16}>
                {' '}
                / {all.length} up to date
              </Text>
            </>
          }
          hint={
            attention || running ? (
              <>
                {attention > 0 && <Text span c="red" size="xs">{attention} to watch</Text>}
                {attention > 0 && running > 0 && ' · '}
                {running > 0 && <Text span c="blue" size="xs">{running} running</Text>}
              </>
            ) : (
              'Everything is up to date'
            )
          }
          icon={<IconHeartbeat size={20} />}
          color={attention ? 'red' : 'teal'}
        />
      </SimpleGrid>

      <Group gap="sm">
        <TextInput
          placeholder="Filter referentials…"
          leftSection={<IconFilter size={16} />}
          value={filter}
          onChange={(e) => setFilter(e.currentTarget.value)}
          w={260}
        />
        <Select placeholder="All categories" data={categories} value={category} onChange={setCategory} clearable w={200} />
        <SegmentedControl
          value={health}
          onChange={setHealth}
          data={[
            { value: 'all', label: 'All' },
            { value: 'ok', label: 'Up to date' },
            { value: 'attention', label: 'To watch' },
            { value: 'running', label: 'Running' },
          ]}
        />
      </Group>

      {filtered.length === 0 ? (
        <EmptyState icon={<IconDatabase size={28} />} title="No referential">
          {all.length === 0
            ? me?.is_admin
              ? 'Create one from Administration › Referentials, or add YAML definitions to the config/ folder and reload the configuration from the System page.'
              : 'No referential is available to you yet: ask an administrator for access.'
            : 'No referential matches the filters.'}
        </EmptyState>
      ) : (
        <SimpleGrid cols={{ base: 1, md: 2, xl: 3 }}>
          {filtered.map((r) => (
            <RefCard key={r.id} r={r} />
          ))}
        </SimpleGrid>
      )}

      <Activity />
    </Stack>
  );
}
