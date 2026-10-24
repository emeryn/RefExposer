import {
  ActionIcon,
  Alert,
  Badge,
  Card,
  Group,
  Loader,
  Pagination,
  SegmentedControl,
  Select,
  SimpleGrid,
  Stack,
  Table,
  Text,
  Tooltip,
} from '@mantine/core';
import { IconAlertTriangle, IconClockHour4, IconInfoCircle, IconTrash } from '@tabler/icons-react';
import { notifications } from '@mantine/notifications';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import { api } from '../../api/client';
import type { ReferentialDetail, Row } from '../../api/types';
import { CellValue } from '../../components/CellValue';
import { EmptyState } from '../../components/Common';
import { DataGrid } from '../../components/DataGrid';
import { RevealSwitch } from '../../components/Governance';
import { fmtBytes, fmtDate, fmtNumber, fmtRelative } from '../../lib/format';

const PAGE = 50;

type Modified = { key: string; changes: { column: string; before: unknown; after: unknown }[] };

function Count({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <Card withBorder padding="sm">
      <Text size="xs" c="dimmed">
        {label}
      </Text>
      <Text fw={700} fz={24} c={color} className="tabular">
        {fmtNumber(value)}
      </Text>
    </Card>
  );
}

export default function TimeTravelTab({ r }: { r: ReferentialDetail }) {
  const qc = useQueryClient();
  const [from, setFrom] = useState<string | null>(null);
  const [to, setTo] = useState<string>('latest');
  const [kind, setKind] = useState<'added' | 'removed' | 'modified'>('added');
  const [page, setPage] = useState(1);
  const [reveal, setReveal] = useState(false);

  const { data: listing, isLoading } = useQuery({
    queryKey: ['snapshots', r.id, r.version],
    queryFn: () => api.snapshots(r.id),
  });

  const options = useMemo(() => {
    if (!listing) return [];
    const latest = {
      value: 'latest',
      label: `Published version${listing.latest.version ? ` (v${listing.latest.version})` : ''} — ${fmtRelative(
        listing.latest.created_at,
      )}`,
    };
    return [
      latest,
      ...listing.snapshots.map((s) => ({
        value: String(s.id),
        label: `v${s.version} — ${fmtDate(s.created_at)} (${fmtNumber(s.row_count)} rows)`,
        disabled: !s.available,
      })),
    ];
  }, [listing]);

  // The useful default: the version before the one published now
  useEffect(() => {
    if (from === null && listing?.snapshots.length) {
      const previous = listing.snapshots.find((s) => s.version !== listing.latest.version && s.available);
      setFrom(previous ? String(previous.id) : null);
    }
  }, [listing, from]);

  const summary = useQuery({
    queryKey: ['diff', r.id, from, to],
    queryFn: () => api.diff(r.id, from!, to),
    enabled: !!from,
  });
  const rows = useQuery({
    queryKey: ['diff-rows', r.id, from, to, kind, page, reveal],
    queryFn: () => api.diffRows(r.id, from!, to, kind, PAGE, (page - 1) * PAGE, reveal),
    enabled: !!from && !!summary.data,
    placeholderData: keepPreviousData,
  });
  const remove = useMutation({
    mutationFn: (id: number) => api.deleteSnapshot(r.id, id),
    onSuccess: (res) => {
      notifications.show({ message: `Snapshot of version ${res.version} deleted`, color: 'teal' });
      setFrom(null);
      qc.invalidateQueries({ queryKey: ['snapshots', r.id] });
    },
    onError: (e: Error) => notifications.show({ title: 'Failed', message: e.message, color: 'red' }),
  });

  if (isLoading) return <Loader />;
  if (!listing?.snapshots.length)
    return (
      <EmptyState icon={<IconClockHour4 size={28} />} title="No past version yet">
        {listing?.enabled
          ? 'Time travel is on: the next update that publishes a new version will keep it, and two versions can then be compared.'
          : 'Turn on time travel in the configuration of this referential to keep its published versions.'}
      </EmptyState>
    );

  const d = summary.data;
  return (
    <Stack gap="md">
      <Card padding="lg">
        <Stack gap="sm">
          <Group justify="space-between" align="flex-end" wrap="wrap">
            <Group gap="sm" align="flex-end">
              <Select
                label="Compare from"
                description="the older version"
                data={options.filter((o) => o.value !== to)}
                value={from}
                onChange={(v) => {
                  setFrom(v);
                  setPage(1);
                }}
                w={320}
                allowDeselect={false}
              />
              <Select
                label="to"
                description="the newer version"
                data={options.filter((o) => o.value !== from)}
                value={to}
                onChange={(v) => {
                  setTo(v ?? 'latest');
                  setPage(1);
                }}
                w={320}
                allowDeselect={false}
              />
            </Group>
            <Group gap="sm">
              <RevealSwitch pii={r.pii} value={reveal} onChange={setReveal} />
              <Text size="xs" c="dimmed">
                {listing.snapshots.length} / {listing.max_snapshots} versions kept · {fmtBytes(listing.stored_size)}
              </Text>
            </Group>
          </Group>

          {summary.error && <Alert color="red">{(summary.error as Error).message}</Alert>}
          {d && (
            <>
              <SimpleGrid cols={{ base: 1, xs: 3 }} spacing="sm">
                <Count label="Added" value={d.added} color="teal" />
                <Count label="Removed" value={d.removed} color="red" />
                <Count label="Modified" value={d.modified} color="orange" />
              </SimpleGrid>
              <Group gap="xs">
                <Text size="sm" c="dimmed">
                  <IconInfoCircle size={14} style={{ verticalAlign: -2 }} /> {d.columns.length} columns compared
                  {d.keys.length ? `, rows identified by ${d.keys.join(' + ')}` : ''}
                </Text>
                {d.masked.length > 0 && !d.revealed && (
                  <Badge size="xs" variant="light" color="orange">
                    {d.masked.join(', ')} masked
                  </Badge>
                )}
              </Group>
              {!d.comparable && (
                <Alert color="blue" variant="light" icon={<IconInfoCircle />} p="xs">
                  <Text size="sm">
                    No column identifies a row here, so the two versions are compared whole: a change shows up as one
                    removal and one addition. Set <b>primary_key_columns</b> (or the key of the referential) to see
                    modified rows with their before and after.
                  </Text>
                </Alert>
              )}
              {d.schema_changed.length > 0 && (
                <Alert color="orange" variant="light" icon={<IconAlertTriangle />} p="xs">
                  <Text size="sm">
                    The schema changed between the two versions: {d.schema_changed.join(', ')}{' '}
                    {d.schema_changed.length > 1 ? 'are' : 'is'} in only one of them and left out of the comparison.
                  </Text>
                </Alert>
              )}
            </>
          )}
        </Stack>
      </Card>

      {d && (
        <Stack gap="sm">
          <SegmentedControl
            value={kind}
            onChange={(v) => {
              setKind(v as typeof kind);
              setPage(1);
            }}
            data={[
              { value: 'added', label: `Added (${fmtNumber(d.added)})` },
              { value: 'removed', label: `Removed (${fmtNumber(d.removed)})` },
              { value: 'modified', label: `Modified (${fmtNumber(d.modified)})`, disabled: !d.comparable },
            ]}
            w="fit-content"
          />
          {rows.isLoading && <Loader />}
          {rows.error && <Alert color="red">{(rows.error as Error).message}</Alert>}
          {rows.data?.note && (
            <Text size="sm" c="dimmed">
              {rows.data.note}
            </Text>
          )}
          {rows.data && rows.data.total === 0 && (
            <Text c="dimmed" size="sm" py="lg" ta="center">
              No row in this category.
            </Text>
          )}
          {rows.data && rows.data.total > 0 && kind !== 'modified' && (
            <DataGrid
              columns={rows.data.columns}
              rows={rows.data.rows as Row[]}
              keyColumn={d.keys[0] ?? null}
              offset={(page - 1) * PAGE}
            />
          )}
          {rows.data && rows.data.total > 0 && kind === 'modified' && (
            <Stack gap="xs">
              {(rows.data.rows as Modified[]).map((m) => (
                <Card key={m.key} padding="sm">
                  <Text fw={700} size="sm" mb={4}>
                    {m.key}
                  </Text>
                  <Table fz="xs" withRowBorders={false} verticalSpacing={2}>
                    <Table.Tbody>
                      {m.changes.map((ch) => (
                        <Table.Tr key={ch.column}>
                          <Table.Td w={200} fw={600} c="dimmed">
                            {ch.column}
                          </Table.Td>
                          <Table.Td
                            style={{
                              background: 'var(--mantine-color-red-light)',
                              textDecoration: 'line-through',
                              maxWidth: 400,
                              wordBreak: 'break-word',
                            }}
                          >
                            <CellValue value={ch.before} name={ch.column} />
                          </Table.Td>
                          <Table.Td
                            style={{ background: 'var(--mantine-color-teal-light)', maxWidth: 400, wordBreak: 'break-word' }}
                          >
                            <CellValue value={ch.after} name={ch.column} />
                          </Table.Td>
                        </Table.Tr>
                      ))}
                    </Table.Tbody>
                  </Table>
                </Card>
              ))}
            </Stack>
          )}
          {rows.data && rows.data.total > PAGE && (
            <Group justify="flex-end">
              <Pagination size="sm" total={Math.ceil(rows.data.total / PAGE)} value={page} onChange={setPage} />
            </Group>
          )}
        </Stack>
      )}

      <Card padding="lg">
        <Text fw={600} mb="xs">
          Versions kept
        </Text>
        <Table fz="sm" highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Version</Table.Th>
              <Table.Th>Kept</Table.Th>
              <Table.Th ta="right">Rows</Table.Th>
              <Table.Th ta="right">Size</Table.Th>
              <Table.Th>Schema</Table.Th>
              <Table.Th />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {listing.snapshots.map((s) => (
              <Table.Tr key={s.id}>
                <Table.Td fw={600}>v{s.version}</Table.Td>
                <Table.Td>
                  <Tooltip label={fmtDate(s.created_at)}>
                    <span>{fmtRelative(s.created_at)}</span>
                  </Tooltip>
                </Table.Td>
                <Table.Td ta="right" className="tabular">
                  {fmtNumber(s.row_count)}
                </Table.Td>
                <Table.Td ta="right" className="tabular">
                  {fmtBytes(s.size)}
                </Table.Td>
                <Table.Td>
                  <Text size="xs" c="dimmed" ff="monospace">
                    {s.schema_hash}
                  </Text>
                </Table.Td>
                <Table.Td ta="right">
                  {!s.available && (
                    <Badge size="xs" color="red" variant="light">
                      file gone
                    </Badge>
                  )}
                  {r.access === 'manage' && (
                    <Tooltip label="Delete this snapshot">
                      <ActionIcon
                        variant="subtle"
                        color="red"
                        loading={remove.isPending}
                        onClick={() => remove.mutate(s.id)}
                        aria-label={`Delete the snapshot of version ${s.version}`}
                      >
                        <IconTrash size={16} />
                      </ActionIcon>
                    </Tooltip>
                  )}
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
        <Text size="xs" c="dimmed" mt="xs">
          A snapshot is a hard link on the published file: it takes no extra space until that version is replaced. The
          oldest ones are dropped beyond {listing.max_snapshots} versions.
        </Text>
      </Card>
    </Stack>
  );
}
