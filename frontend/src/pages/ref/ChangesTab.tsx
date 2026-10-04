import { Alert, Card, Group, Loader, Pagination, SegmentedControl, Stack, Table, Text } from '@mantine/core';
import { IconGitCompare, IconInfoCircle } from '@tabler/icons-react';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../../api/client';
import type { ReferentialDetail, Row } from '../../api/types';
import { CellValue } from '../../components/CellValue';
import { EmptyState } from '../../components/Common';
import { DataGrid, RowDrawer } from '../../components/DataGrid';
import { fmtNumber } from '../../lib/format';

const PAGE = 50;

type Modified = { key: string; changes: { column: string; before: unknown; after: unknown }[] };

export default function ChangesTab({ r }: { r: ReferentialDetail }) {
  const [kind, setKind] = useState<'added' | 'removed' | 'modified'>('added');
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<Row | null>(null);
  const enabled = !!r.key && r.has_previous;
  const { data, isLoading, error } = useQuery({
    queryKey: ['changes', r.id, r.version, kind, page],
    queryFn: () => api.changes(r.id, kind, PAGE, (page - 1) * PAGE),
    enabled,
    placeholderData: keepPreviousData,
  });

  if (!r.key)
    return (
      <EmptyState icon={<IconGitCompare size={28} />} title="Change tracking unavailable">
        Define a key (<code>key</code>) in the referential configuration to compare successive versions.
      </EmptyState>
    );
  if (!r.has_previous)
    return (
      <EmptyState icon={<IconGitCompare size={28} />} title="No previous version yet">
        Differences will appear after the next update that changes the content of the referential.
      </EmptyState>
    );

  const c = r.changes;
  const label = (k: string, n: number | null | undefined) => `${k} (${n == null ? '?' : fmtNumber(n)})`;
  return (
    <Stack gap="sm">
      <Group justify="space-between">
        <SegmentedControl
          value={kind}
          onChange={(v) => {
            setKind(v as typeof kind);
            setPage(1);
          }}
          data={[
            { value: 'added', label: label('Ajouts', c?.added) },
            { value: 'removed', label: label('Suppressions', c?.removed) },
            { value: 'modified', label: label('Modifications', c?.modified) },
          ]}
        />
        <Text size="sm" c="dimmed">
          <IconInfoCircle size={14} style={{ verticalAlign: -2 }} /> Version {r.version} compared with version {(r.version ?? 1) - 1}
        </Text>
      </Group>
      {error && <Alert color="red">{(error as Error).message}</Alert>}
      {isLoading && <Loader />}
      {data && data.total === 0 && (
        <Text c="dimmed" size="sm" py="lg" ta="center">
          No row in this category.
        </Text>
      )}
      {data && data.total > 0 && kind !== 'modified' && (
        <DataGrid
          columns={data.columns}
          rows={data.rows as Row[]}
          keyColumn={r.key}
          offset={(page - 1) * PAGE}
          onRowClick={(row) => setSelected(row)}
        />
      )}
      {data && data.total > 0 && kind === 'modified' && (
        <Stack gap="xs">
          {(data.rows as Modified[]).map((m) => (
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
                      <Table.Td style={{ background: 'var(--mantine-color-red-light)', textDecoration: 'line-through', maxWidth: 400, wordBreak: 'break-word' }}>
                        <CellValue value={ch.before} name={ch.column} />
                      </Table.Td>
                      <Table.Td style={{ background: 'var(--mantine-color-teal-light)', maxWidth: 400, wordBreak: 'break-word' }}>
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
      {data && data.total > PAGE && (
        <Group justify="flex-end">
          <Pagination size="sm" total={Math.ceil(data.total / PAGE)} value={page} onChange={setPage} />
        </Group>
      )}
      <RowDrawer row={selected} columns={data?.columns ?? []} keyColumn={r.key} onClose={() => setSelected(null)} />
    </Stack>
  );
}
