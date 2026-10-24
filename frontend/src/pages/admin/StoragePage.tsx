import { Alert, Anchor, Badge, Button, Card, Code, Group, Loader, Progress, SimpleGrid, Stack, Table, Text, Tooltip } from '@mantine/core';
import { IconAlertTriangle, IconRefresh } from '@tabler/icons-react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../../api/client';
import type { StorageReferential, StorageReport } from '../../api/types';
import { fmtBytes, fmtRelative } from '../../lib/format';

const COLORS: Record<string, string> = {
  published: 'indigo',
  indexes: 'violet',
  previous: 'grape',
  snapshots: 'blue',
  base: 'cyan',
  sources: 'orange',
  downloading: 'red',
  work: 'yellow',
  manual: 'teal',
  exports: 'lime',
  other: 'gray',
};
const LEVELS: Record<string, string> = { ok: 'teal', warning: 'orange', critical: 'red' };

/** What can be done about the space a referential takes (read only page: the actions are elsewhere). */
function hints(r: StorageReferential): string[] {
  const c = r.categories;
  const big = (n?: number) => (n ?? 0) > 512 * 1024 ** 2;
  const out: string[] = [];
  if (r.orphan) out.push(`Data of a deleted referential: the folder data/${r.id} can be removed`);
  if (big(c.sources) && r.keep_raw) out.push('Source files kept after the build: storage.keep_raw: false frees them');
  if (big(c.previous) && r.keep_previous) out.push('Previous version kept for rollback: storage.keep_previous: false frees it');
  if (big(c.downloading)) out.push('Interrupted download: resumed or removed by the next update');
  if (big(c.work)) out.push('Build in progress (or left by an interrupted one): removed by the next update');
  if (big(c.exports)) out.push('Generated downloads: rebuilt on demand');
  // Hard links: the figure overstates what the snapshots really hold until the version they share is replaced
  if (big(c.snapshots))
    out.push('Past versions kept for time travel: lower storage.max_snapshots, or delete some from the History & diff tab');
  return out;
}

function Bar({ parts, total }: { parts: Record<string, number>; total: number }) {
  return (
    <Progress.Root size="lg">
      {Object.entries(parts)
        .filter(([, n]) => n > 0)
        .map(([k, n]) => (
          <Tooltip key={k} label={`${k}: ${fmtBytes(n)}`}>
            <Progress.Section value={total ? (n / total) * 100 : 0} color={COLORS[k] ?? 'gray'} />
          </Tooltip>
        ))}
    </Progress.Root>
  );
}

function Volumes({ doc }: { doc: StorageReport }) {
  return (
    <SimpleGrid cols={{ base: 1, md: Math.min(doc.volumes.length, 3) }}>
      {doc.volumes.map((v) => (
        <Card key={v.name} padding="lg">
          <Stack gap="xs">
            <Group justify="space-between">
              <Text fw={600}>{v.name}</Text>
              <Badge color={LEVELS[v.level]} variant="light">
                {v.free_pct}% free
              </Badge>
            </Group>
            <Progress.Root size="xl">
              <Tooltip label={`Used: ${fmtBytes(v.used)}`}>
                <Progress.Section value={(v.used / v.total) * 100} color={LEVELS[v.level]} />
              </Tooltip>
            </Progress.Root>
            <Text size="sm">
              <b>{fmtBytes(v.free)}</b> free of {fmtBytes(v.total)}
            </Text>
            {v.name.startsWith('data') && (
              <Text size="xs" c="dimmed">
                Updates always keep {fmtBytes(doc.reserve)} free (<Code>REFEX_MIN_FREE_DISK</Code>): they stop with a clear error
                instead of filling the disk.
              </Text>
            )}
          </Stack>
        </Card>
      ))}
    </SimpleGrid>
  );
}

export default function StoragePage() {
  const qc = useQueryClient();
  const [refreshing, setRefreshing] = useState(false);
  const { data: doc, isLoading } = useQuery({ queryKey: ['storage'], queryFn: () => api.storage() });
  const refresh = async () => {
    setRefreshing(true);
    try {
      qc.setQueryData(['storage'], await api.storage(true));
    } finally {
      setRefreshing(false);
    }
  };
  if (isLoading || !doc) return <Loader />;
  const dataTotal = doc.categories.reduce((s, c) => s + c.size, 0);
  const orphans = doc.referentials.filter((r) => r.orphan && r.total > 0);
  return (
    <Stack gap="lg">
      <Group justify="space-between" align="flex-start">
        <div>
          <Text c="dimmed" size="sm">
            Free space of the volumes and what takes the space in the data folder. Read only: nothing is deleted from this page.
          </Text>
        </div>
        <Group gap="xs">
          <Text size="xs" c="dimmed">
            Measured {fmtRelative(doc.computed_at)} in {doc.duration}s
          </Text>
          <Button variant="default" size="xs" leftSection={<IconRefresh size={14} />} onClick={refresh} loading={refreshing}>
            Measure again
          </Button>
        </Group>
      </Group>
      {doc.partial && (
        <Alert color="orange" icon={<IconAlertTriangle size={16} />}>
          More than 2 million files: the figures below are partial.
        </Alert>
      )}
      <Volumes doc={doc} />

      <Card padding="lg">
        <Stack gap="sm">
          <Group justify="space-between">
            <Text fw={600}>What takes the space in data/</Text>
            <Text size="sm">{fmtBytes(doc.data_total)}</Text>
          </Group>
          <Bar parts={Object.fromEntries(doc.categories.map((c) => [c.id, c.size]))} total={dataTotal} />
          <Group gap="lg">
            {doc.categories.map((c) => (
              <Group key={c.id} gap={6}>
                <Badge color={COLORS[c.id]} variant="filled" size="xs" circle> </Badge>
                <Text size="xs">
                  {c.label}: <b>{fmtBytes(c.size)}</b>
                </Text>
              </Group>
            ))}
          </Group>
          {orphans.length > 0 && (
            <Alert color="orange" variant="light" p="xs">
              <Text size="sm">
                {orphans.length} folder(s) of deleted referentials take {fmtBytes(orphans.reduce((s, r) => s + r.total, 0))}:{' '}
                {orphans.map((r) => `data/${r.id}`).join(', ')}.
              </Text>
            </Alert>
          )}
        </Stack>
      </Card>

      <Card padding="lg">
        <Text fw={600} mb="sm">
          By referential
        </Text>
        <Table striped highlightOnHover verticalSpacing={6}>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Referential</Table.Th>
              <Table.Th ta="right">Size</Table.Th>
              <Table.Th w="35%">Breakdown</Table.Th>
              <Table.Th>Can be freed</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {doc.referentials
              .filter((r) => r.total >= 1024 ** 2)
              .slice(0, 50)
              .map((r) => (
                <Table.Tr key={r.id}>
                  <Table.Td>
                    {r.orphan ? (
                      <Group gap={6}>
                        <Text size="sm" ff="monospace">
                          {r.id}
                        </Text>
                        <Badge color="orange" variant="light" size="xs">
                          deleted
                        </Badge>
                      </Group>
                    ) : (
                      <Anchor component={Link} to={`/r/${r.id}`} size="sm">
                        {r.name ?? r.id}
                      </Anchor>
                    )}
                  </Table.Td>
                  <Table.Td ta="right" className="tabular">
                    {fmtBytes(r.total)}
                  </Table.Td>
                  <Table.Td>
                    <Bar parts={r.categories} total={r.total} />
                  </Table.Td>
                  <Table.Td>
                    {hints(r).map((h) => (
                      <Text key={h} size="xs" c="dimmed">
                        {h}
                      </Text>
                    ))}
                  </Table.Td>
                </Table.Tr>
              ))}
          </Table.Tbody>
        </Table>
      </Card>

      <SimpleGrid cols={{ base: 1, lg: 2 }}>
        <Card padding="lg">
          <Text fw={600} mb="sm">
            Largest files
          </Text>
          <Table verticalSpacing={4}>
            <Table.Tbody>
              {doc.largest_files.map((f) => (
                <Table.Tr key={f.path}>
                  <Table.Td>
                    <Text size="xs" ff="monospace" style={{ wordBreak: 'break-all' }}>
                      {f.path}
                    </Text>
                  </Table.Td>
                  <Table.Td ta="right" className="tabular" w={100}>
                    {fmtBytes(f.size)}
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Card>
        <Card padding="lg">
          <Text fw={600} mb="sm">
            Other folders
          </Text>
          <Table verticalSpacing={4}>
            <Table.Tbody>
              {[...doc.system, ...doc.outside].map((s) => (
                <Table.Tr key={s.name}>
                  <Table.Td>
                    <Text size="sm">{s.name}</Text>
                  </Table.Td>
                  <Table.Td ta="right" className="tabular" w={100}>
                    {fmtBytes(s.size)}
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
          <Text size="xs" c="dimmed" mt="xs">
            data/.system: backups and history · data/.tmp: uploads and previews · /tmp: files converted for reading (in memory
            in the container)
          </Text>
        </Card>
      </SimpleGrid>
    </Stack>
  );
}
