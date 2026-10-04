import {
  Alert,
  Anchor,
  Badge,
  Button,
  Card,
  Code,
  Group,
  Loader,
  ScrollArea,
  SimpleGrid,
  Stack,
  Tabs,
  Text,
  Title,
  Tooltip,
} from '@mantine/core';
import {
  IconAlertTriangle,
  IconDatabaseEdit,
  IconFileImport,
  IconFolderShare,
  IconPencil,
  IconPin,
  IconApi,
  IconArrowLeft,
  IconExternalLink,
  IconGitCompare,
  IconHistory,
  IconKey,
  IconListDetails,
  IconLock,
  IconSearch,
  IconSettings,
  IconTable,
} from '@tabler/icons-react';
import { notifications } from '@mantine/notifications';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { api } from '../api/client';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useReferential } from '../api/hooks';
import { useAuth } from '../auth/AuthContext';
import GrantsEditor from '../components/GrantsEditor';
import { DownloadMenu } from '../components/Downloads';
import { ImportButton } from '../components/ImportButton';
import type { ReferentialDetail } from '../api/types';
import { EmptyState, RefreshButton } from '../components/Common';
import { HealthBadge, RunProgress } from '../components/Status';
import { describeCron, fmtBytes, fmtDate, fmtDelta, fmtNumber, fmtRelative } from '../lib/format';
import ApiTab from './ref/ApiTab';
import ChangesTab from './ref/ChangesTab';
import DataTab from './ref/DataTab';
import EditTab from './ref/EditTab';
import HistoryTab from './ref/HistoryTab';
import SchemaTab from './ref/SchemaTab';
import SearchTab from './ref/SearchTab';
import SourceTab from './ref/SourceTab';

function Info({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <div>
      <Text size="xs" c="dimmed" fw={500}>
        {label}
      </Text>
      <Tooltip label={hint} disabled={!hint}>
        <Text size="sm" fw={600} className="tabular" component="div">
          {children}
        </Text>
      </Tooltip>
    </div>
  );
}

function Header({ r }: { r: ReferentialDetail }) {
  const { me } = useAuth();
  const internal = r.kind === 'internal';
  const pinned = !!r.pinned;
  const delta = r.row_count != null && r.previous_row_count != null ? r.row_count - r.previous_row_count : null;
  return (
    <Card padding="lg">
      <Stack gap="md">
        <Group justify="space-between" align="flex-start" wrap="nowrap">
          <Stack gap={6} style={{ minWidth: 0 }}>
            <Group gap="sm">
              <Title order={2}>{r.name}</Title>
              <HealthBadge health={r.health} size="md" />
            </Group>
            <Group gap={6}>
              <Badge variant="light" color="grape">
                {r.category}
              </Badge>
              <Badge variant="default" tt="uppercase">
                {r.format}
              </Badge>
              {r.kind === 'mmdb' && (
                <Tooltip label="IP lookups; the raw .mmdb file is downloadable by tools">
                  <Badge variant="light" color="cyan">
                    MaxMind DB{r.mmdb ? ` · ${r.mmdb.database_type}` : ''}
                  </Badge>
                </Tooltip>
              )}
              {r.confidential && (
                <Tooltip
                  multiline
                  maw={360}
                  label={r.encrypted ? 'Encrypted at rest (AES-256-GCM): published files, previous version, internal rows and their keys in the database, column profile. Source files and generated downloads are not kept on disk. Transparent for users with access.' : 'Confidential: encrypted at the next publication (refresh the referential)'}
                >
                  <Badge variant="light" color={r.encrypted ? 'red' : 'orange'} leftSection={<IconLock size={10} />}>
                    confidential{r.encrypted ? '' : ' · pending'}
                  </Badge>
                </Tooltip>
              )}
              {r.kind === 'bloom' && (
                <Tooltip label="Offline membership tests: “absent” is certain, “present” is probable">
                  <Badge variant="light" color="cyan">
                    Bloom filter
                  </Badge>
                </Tooltip>
              )}
              {r.large && (
                <Badge variant="light" color="indigo">
                  large volume
                </Badge>
              )}
              <Tooltip label="Table name in the SQL console">
                <Badge variant="default" tt="none" ff="monospace">
                  {r.table}
                </Badge>
              </Tooltip>
              {r.tags.map((t) => (
                <Badge key={t} variant="outline" color="gray" tt="none" size="sm">
                  #{t}
                </Badge>
              ))}
            </Group>
            {r.description && (
              <Text size="sm" c="dimmed" maw={900}>
                {r.description}
              </Text>
            )}
            <Group gap="md">
              {r.homepage && (
                <Anchor href={r.homepage} target="_blank" size="sm">
                  <IconExternalLink size={14} style={{ verticalAlign: -2 }} /> Reference website
                </Anchor>
              )}
              {r.owner && (
                <Text size="sm" c="dimmed">
                  Producer: {r.owner}
                </Text>
              )}
              {r.license && (
                <Text size="sm" c="dimmed">
                  License: {r.license}
                </Text>
              )}
            </Group>
          </Stack>
          <Group gap="xs" wrap="nowrap">
            {internal && r.access === 'manage' && me?.can_create_internal && (
              <Button component={Link} to={`/internal/${r.id}/schema`} variant="default" leftSection={<IconPencil size={16} />}>
                Columns
              </Button>
            )}
            {me?.is_admin && r.origin === 'database' && !internal && (
              <Button component={Link} to={`/admin/referentials/${r.id}/edit`} variant="default" leftSection={<IconSettings size={16} />}>
                Configure
              </Button>
            )}
            {r.has_data && <DownloadMenu refId={r.id} />}
            {r.access === 'manage' && !internal && !r.sync_path && <ImportButton r={r} />}
            {r.access === 'manage' && !internal && <RefreshButton id={r.id} running={!!r.current_run} />}
          </Group>
        </Group>

        <SimpleGrid cols={{ base: 2, sm: 4, lg: 8 }} spacing="md">
          <Info label={r.kind === 'bloom' ? 'Elements' : r.kind === 'mmdb' ? 'Nodes' : 'Rows'}>
            {fmtNumber(r.row_count)}
            {delta != null && delta !== 0 && (
              <Text span size="xs" c={delta > 0 ? 'teal' : 'red'} ml={4}>
                {fmtDelta(delta)}
              </Text>
            )}
          </Info>
          {r.kind === 'mmdb' ? (
            <Info label="Built" hint={r.mmdb ? `IPv${r.mmdb.ip_version} · ${r.mmdb.file_name}` : undefined}>
              {r.mmdb ? r.mmdb.build_date.slice(0, 10) : '—'}
            </Info>
          ) : r.kind === 'bloom' ? (
            <Info label="False positives" hint="Probability that an absent value is reported present">
              {r.bloom ? `≈ ${r.bloom.estimated_fp_rate.toExponential(1)}` : '—'}
            </Info>
          ) : (
            <Info label="Columns">{r.column_count || '—'}</Info>
          )}
          <Info label="Version">{r.version ?? '—'}</Info>
          <Info label="Data changed" hint={fmtDate(r.data_updated_at)}>
            {fmtRelative(r.data_updated_at)}
          </Info>
          <Info label="Last check" hint={fmtDate(r.last_checked_at)}>
            {fmtRelative(r.last_checked_at)}
          </Info>
          <Info label="Schedule" hint={r.schedule ?? undefined}>
            {internal ? 'On every edit' : r.sync_path ? 'When its files change' : pinned ? 'Suspended (frozen)' : describeCron(r.schedule)}
            {r.next_run_at && !internal && !pinned && (
              <Text size="xs" c="dimmed" fw={400}>
                next {fmtRelative(r.next_run_at)}
              </Text>
            )}
          </Info>
          <Info label="Storage" hint={`Parquet ${fmtBytes(r.parquet_size)} · sources ${fmtBytes(r.raw_size)}`}>
            {fmtBytes(r.storage_size)}
          </Info>
          <Info label="Key">
            {r.key ? (
              <Group gap={4} wrap="nowrap">
                <IconKey size={14} color="var(--mantine-color-yellow-6)" />
                <Text size="sm" fw={600} truncate>
                  {r.key}
                </Text>
                {r.key_unique === false && (
                  <Tooltip label="Values are not unique">
                    <IconAlertTriangle size={14} color="var(--mantine-color-orange-6)" />
                  </Tooltip>
                )}
              </Group>
            ) : (
              '—'
            )}
          </Info>
        </SimpleGrid>

        {r.current_run && (
          <Card withBorder padding="sm" bg="var(--mantine-color-blue-light)">
            <Stack gap="xs">
              <RunProgress run={r.current_run} />
              {r.current_run.logs && r.current_run.logs.length > 0 && (
                <ScrollArea.Autosize mah={110}>
                  <div className="logs">
                    {r.current_run.logs.slice(-6).map((l, i) => (
                      <div key={i} style={{ color: l.level === 'error' ? 'var(--mantine-color-red-6)' : l.level === 'warn' ? 'var(--mantine-color-orange-6)' : undefined }}>
                        {l.msg}
                      </div>
                    ))}
                  </div>
                </ScrollArea.Autosize>
              )}
            </Stack>
          </Card>
        )}
        <OriginNotices r={r} />
        {!r.current_run && r.last_error && ['error', 'rejected', 'corrupted', 'cancelled'].includes(r.status ?? '') && (
          <Alert
            color={r.status === 'rejected' || r.status === 'corrupted' ? 'orange' : 'red'}
            icon={<IconAlertTriangle />}
            title={
              r.status === 'corrupted'
                ? 'Corrupted remote source — current version kept'
                : r.status === 'rejected'
                  ? 'Last version rejected by validation'
                  : r.status === 'cancelled'
                    ? 'Last update cancelled'
                    : 'Last update failed'
            }
          >
            <Text size="sm">{r.last_error}</Text>
            {r.has_data && (
              <Text size="xs" c="dimmed" mt={4}>
                The previous version is still served.
              </Text>
            )}
          </Alert>
        )}
      </Stack>
    </Card>
  );
}

/** Where the current version comes from, when it is not the usual automatic source. */
function OriginNotices({ r }: { r: ReferentialDetail }) {
  const qc = useQueryClient();
  const unpin = useMutation({
    mutationFn: () => api.unpin(r.id),
    onSuccess: () => {
      notifications.show({ message: 'Automatic updates resumed', color: 'teal' });
      qc.invalidateQueries({ queryKey: ['referential', r.id] });
      qc.invalidateQueries({ queryKey: ['referentials'] });
    },
    onError: (e: Error) => notifications.show({ title: 'Failed', message: e.message, color: 'red' }),
  });
  if (r.kind === 'internal')
    return (
      <Alert color="grape" variant="light" icon={<IconDatabaseEdit />} p="sm">
        <Text size="sm">
          <b>Internal referential — managed in RefExposer.</b> It has no remote source: its rows are edited in the application (Edit
          tab) or through the API, and every change publishes a new version.
        </Text>
      </Alert>
    );
  if (r.sync_path)
    return (
      <Alert color="teal" variant="light" icon={<IconFolderShare />} p="sm">
        <Text size="sm">
          <b>Synchronized from the file system</b> — <Code>{r.sync_path}</Code>. Imported again automatically when its files
          change; remove them from the folder to remove the referential.
        </Text>
      </Alert>
    );
  const m = r.manual_import;
  if (!m && !r.pinned) return null;
  return (
    <Alert color="indigo" variant="light" icon={r.pinned ? <IconPin /> : <IconFileImport />} p="sm">
      <Group justify="space-between" wrap="nowrap" align="flex-start">
        <Stack gap={2}>
          {m && (
            <Text size="sm">
              <b>Current version imported manually</b> {m.origin === 'upload' ? `(upload by ${m.by ?? 'unknown'})` : '(import folder)'}{' '}
              {fmtRelative(m.at)} — {m.files.join(', ')}
            </Text>
          )}
          {r.pinned && (
            <Text size="sm">
              <b>Version frozen</b> by {r.pinned.by ?? 'unknown'} {fmtRelative(r.pinned.at)}: scheduled updates from the remote source
              are suspended.
            </Text>
          )}
          {m && !r.pinned && (
            <Text size="xs" c="dimmed">
              The next scheduled update will replace it with the remote source.
            </Text>
          )}
        </Stack>
        {r.pinned && r.access === 'manage' && (
          <Button size="xs" variant="light" loading={unpin.isPending} onClick={() => unpin.mutate()} style={{ flexShrink: 0 }}>
            Resume automatic updates
          </Button>
        )}
      </Group>
    </Alert>
  );
}

// Raw files (Bloom filter, MaxMind DB): lookups only, no table
const ARTIFACT_TABS = ['search', 'history', 'api', 'source', 'access'];

const TABS: { value: string; label: string; icon: typeof IconTable; admin?: boolean; internal?: boolean }[] = [
  { value: 'data', label: 'Data', icon: IconTable },
  { value: 'edit', label: 'Edit', icon: IconDatabaseEdit, internal: true },
  { value: 'search', label: 'Search', icon: IconSearch },
  { value: 'schema', label: 'Schema & profile', icon: IconListDetails },
  { value: 'changes', label: 'Changes', icon: IconGitCompare },
  { value: 'history', label: 'History', icon: IconHistory },
  { value: 'api', label: 'API', icon: IconApi },
  { value: 'source', label: 'Source & configuration', icon: IconSettings },
  { value: 'access', label: 'Access', icon: IconLock, admin: true },
];

export default function ReferentialPage() {
  const { id = '', tab: tabParam } = useParams();
  const navigate = useNavigate();
  const { data: r, isLoading, error } = useReferential(id);
  const { me } = useAuth();
  const artifact = r?.kind === 'bloom' || r?.kind === 'mmdb';
  const tab = tabParam ?? (artifact ? 'search' : 'data');

  if (isLoading)
    return (
      <Group justify="center" py="xl">
        <Loader />
      </Group>
    );
  if (error || !r)
    return (
      <Alert color="red" title="Referential not found">
        {(error as Error)?.message}
      </Alert>
    );

  const needsData = ['data', 'search', 'schema', 'changes', 'api'].includes(tab) && r.kind !== 'internal';
  const accessBadge = !me?.is_admin && r.access && (
    <Badge variant="outline" color={r.access === 'manage' ? 'orange' : 'blue'} size="sm" leftSection={<IconLock size={10} />}>
      {r.access === 'manage' ? 'Manage' : 'Read only'}
    </Badge>
  );
  return (
    <Stack gap="md">
      <Group justify="space-between">
        <Anchor component={Link} to="/" size="sm" c="dimmed">
          <IconArrowLeft size={14} style={{ verticalAlign: -2 }} /> Dashboard
        </Anchor>
        {accessBadge}
      </Group>
      <Header r={r} />
      <Tabs value={tab} onChange={(v) => navigate(`/r/${id}/${v === 'data' ? '' : v}`)} keepMounted={false}>
        <Tabs.List mb="md">
          {TABS.filter(
            (t) => (!t.admin || me?.is_admin) && (!t.internal || r.kind === 'internal') && (!artifact || ARTIFACT_TABS.includes(t.value)),
          ).map((t) => (
            <Tabs.Tab key={t.value} value={t.value} leftSection={<t.icon size={16} />}>
              {t.label}
            </Tabs.Tab>
          ))}
        </Tabs.List>
        {r.kind === 'internal' && (
          <Tabs.Panel value="edit">
            <EditTab r={r} />
          </Tabs.Panel>
        )}
        {needsData && !r.has_data ? (
          <EmptyState icon={<IconTable size={28} />} title="No data yet">
            {r.current_run
              ? 'The first import is running, the data will appear automatically.'
              : r.access === 'manage' ? 'Run an update or import files to load this referential.' : 'This referential has not been loaded yet.'}
          </EmptyState>
        ) : (
          <>
            <Tabs.Panel value="data">
              <DataTab r={r} />
            </Tabs.Panel>
            <Tabs.Panel value="search">
              <SearchTab r={r} />
            </Tabs.Panel>
            <Tabs.Panel value="schema">
              <SchemaTab r={r} />
            </Tabs.Panel>
            <Tabs.Panel value="changes">
              <ChangesTab r={r} />
            </Tabs.Panel>
            <Tabs.Panel value="history">
              <HistoryTab r={r} />
            </Tabs.Panel>
            <Tabs.Panel value="api">
              <ApiTab r={r} />
            </Tabs.Panel>
            <Tabs.Panel value="source">
              <SourceTab r={r} />
            </Tabs.Panel>
            {me?.is_admin && (
              <Tabs.Panel value="access">
                <Card padding="lg" maw={860}>
                  <Title order={5} mb={4}>
                    Who can access this referential?
                  </Title>
                  <Text size="sm" c="dimmed" mb="md">
                    Administrators always have access. Global rights ("all referentials") given to a user or a group also apply.
                  </Text>
                  <GrantsEditor scope={{ referentialId: r.id }} />
                </Card>
              </Tabs.Panel>
            )}
          </>
        )}
      </Tabs>
    </Stack>
  );
}
