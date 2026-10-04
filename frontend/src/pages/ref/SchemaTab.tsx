import { Alert, Badge, Card, Group, Loader, Modal, Progress, SimpleGrid, Stack, Table, Text, Tooltip } from '@mantine/core';
import { IconChartBar, IconKey } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../../api/client';
import type { ColumnStats, ReferentialDetail } from '../../api/types';
import { LOCALE, fmtNumber } from '../../lib/format';
import { filterParam, typeKind } from '../../lib/filters';

function Histogram({ stats }: { stats: ColumnStats }) {
  const bins = stats.histogram ?? [];
  const max = Math.max(1, ...bins.map((b) => b.count));
  if (!bins.length) return null;
  return (
    <div>
      <Text size="sm" fw={600} mb={4}>
        Distribution{stats.bucket ? ` (per ${stats.bucket})` : ''}
      </Text>
      <div className="hist" role="img" aria-label={`Distribution of ${stats.column}`}>
        {bins.map((b, i) => (
          <Tooltip
            key={i}
            label={
              <>
                {b.from != null ? `${b.from.toLocaleString(LOCALE, { maximumFractionDigits: 3 })} – ${b.to!.toLocaleString(LOCALE, { maximumFractionDigits: 3 })}` : b.label}
                {' : '}
                <b>{fmtNumber(b.count)}</b>
              </>
            }
            openDelay={0}
          >
            <div className="hist-bar" style={{ height: `${(b.count / max) * 100}%` }} />
          </Tooltip>
        ))}
      </div>
      <Group justify="space-between" mt={4}>
        <Text size="xs" c="dimmed">
          {bins[0].label}
        </Text>
        <Text size="xs" c="dimmed">
          {bins[bins.length - 1].to != null ? stats.max : bins[bins.length - 1].label}
        </Text>
      </Group>
    </div>
  );
}

function ColumnStatsModal({ r, column, onClose }: { r: ReferentialDetail; column: string | null; onClose: () => void }) {
  const navigate = useNavigate();
  const { data, isLoading, error } = useQuery({
    queryKey: ['stats', r.id, r.version, column],
    queryFn: () => api.columnStats(r.id, column!),
    enabled: column != null,
  });
  const maxTop = Math.max(1, ...(data?.top.map((t) => t.count) ?? [1]));
  const kind = data ? data.kind : 'text';
  const filterOn = (value: string) => {
    navigate(`/r/${r.id}?${new URLSearchParams({ [filterParam(column!, 'eq')]: value })}`);
  };
  return (
    <Modal opened={column != null} onClose={onClose} title={<Text fw={700}>Column “{column}”</Text>} size="lg">
      {isLoading && <Loader />}
      {error && <Alert color="red">{(error as Error).message}</Alert>}
      {data && (
        <Stack>
          <SimpleGrid cols={4}>
            {[
              ['Type', <Badge variant="default" tt="lowercase" ff="monospace">{data.type}</Badge>],
              ['Remplies', `${fmtNumber(data.non_null)}`],
              ['Empty', `${fmtNumber(data.nulls)} (${data.total ? ((data.nulls / data.total) * 100).toFixed(1) : 0} %)`],
              ['Distinctes ≈', fmtNumber(data.distinct)],
            ].map(([label, value], i) => (
              <div key={i}>
                <Text size="xs" c="dimmed">
                  {label}
                </Text>
                <Text size="sm" fw={600} component="div">
                  {value}
                </Text>
              </div>
            ))}
          </SimpleGrid>
          {data.sampled && (
            <Text size="xs" c="dimmed">
              Statistics computed on a sample (~1% of the blocks) of the referential.
            </Text>
          )}
          {data.min != null && (
            <Text size="sm">
              Range: <b>{data.min}</b> → <b>{data.max}</b>
            </Text>
          )}
          <Histogram stats={data} />
          {data.mostly_unique && (
            <Text size="sm" c="dimmed">
              Values too spread out (nearly all distinct): no significant frequent values.
            </Text>
          )}
          {data.top.length > 0 && (
            <div>
              <Text size="sm" fw={600} mb={4}>
                Most frequent values{kind === 'list' ? ' (list items)' : ''}
              </Text>
              <Stack gap={2}>
                {data.top.map((t) => (
                  <Tooltip key={t.value} label="Click to filter on this value" position="left">
                    <div className="topbar-row" onClick={() => filterOn(t.value)}>
                      <div className="topbar-fill" style={{ width: `${(t.count / maxTop) * 100}%` }} />
                      <Group justify="space-between" wrap="nowrap" style={{ position: 'relative' }}>
                        <Text size="sm" truncate>
                          {t.value === '' ? <span className="null">(empty)</span> : t.value}
                        </Text>
                        <Text size="sm" className="tabular" c="dimmed" style={{ flexShrink: 0 }}>
                          {fmtNumber(t.count)} · {((t.count / Math.max(1, data.non_null)) * 100).toFixed(1)} %
                        </Text>
                      </Group>
                    </div>
                  </Tooltip>
                ))}
              </Stack>
            </div>
          )}
        </Stack>
      )}
    </Modal>
  );
}

export default function SchemaTab({ r }: { r: ReferentialDetail }) {
  const [column, setColumn] = useState<string | null>(null);
  const profile = new Map(r.profile.map((p) => [p.name, p]));
  return (
    <Card padding={0}>
      {r.profile_sampled && (
        <Text size="xs" c="dimmed" px="md" pt="sm">
          Profile computed on a sample of one million rows (large referential): fill rates and cardinalities are estimates.
        </Text>
      )}
      <Table.ScrollContainer minWidth={900}>
        <Table highlightOnHover verticalSpacing="xs" fz="sm">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Column</Table.Th>
              <Table.Th>Type</Table.Th>
              <Table.Th w={180}>Remplissage</Table.Th>
              <Table.Th ta="right">Distinctes ≈</Table.Th>
              <Table.Th>Min</Table.Th>
              <Table.Th>Max</Table.Th>
              <Table.Th>Median / mean</Table.Th>
              <Table.Th />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {r.columns.map((c) => {
              const p = profile.get(c.name);
              const fill = p?.null_percentage != null ? 100 - p.null_percentage : null;
              const numeric = typeKind(c.type) === 'number';
              return (
                <Table.Tr key={c.name} style={{ cursor: 'pointer' }} onClick={() => setColumn(c.name)}>
                  <Table.Td>
                    <Group gap={6} wrap="nowrap">
                      {r.key === c.name && (
                        <Tooltip label="Referential key">
                          <IconKey size={14} color="var(--mantine-color-yellow-6)" />
                        </Tooltip>
                      )}
                      <Text size="sm" fw={600}>
                        {c.name}
                      </Text>
                    </Group>
                  </Table.Td>
                  <Table.Td>
                    <Badge variant="default" tt="lowercase" ff="monospace" size="sm" style={{ maxWidth: 220 }}>
                      {c.type}
                    </Badge>
                  </Table.Td>
                  <Table.Td>
                    {fill != null ? (
                      <Group gap={8} wrap="nowrap">
                        <Progress value={fill} size="sm" w={90} color={fill > 90 ? 'teal' : fill > 50 ? 'yellow' : 'red'} aria-label="Fill rate" />
                        <Text size="xs" className="tabular">
                          {fill.toFixed(fill === 100 ? 0 : 1)} %
                        </Text>
                      </Group>
                    ) : (
                      '—'
                    )}
                  </Table.Td>
                  <Table.Td ta="right" className="tabular">
                    {fmtNumber(p?.approx_unique)}
                  </Table.Td>
                  <Table.Td maw={160}>
                    <Text size="xs" truncate ff={numeric ? undefined : 'monospace'}>
                      {p?.min ?? '—'}
                    </Text>
                  </Table.Td>
                  <Table.Td maw={160}>
                    <Text size="xs" truncate ff={numeric ? undefined : 'monospace'}>
                      {p?.max ?? '—'}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <Text size="xs" className="tabular">
                      {numeric && p?.q50 != null ? `${Number(p.q50).toLocaleString(LOCALE)} / ${Number(p.avg).toLocaleString(LOCALE, { maximumFractionDigits: 3 })}` : '—'}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <IconChartBar size={16} color="var(--mantine-color-dimmed)" />
                  </Table.Td>
                </Table.Tr>
              );
            })}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
      <ColumnStatsModal r={r} column={column} onClose={() => setColumn(null)} />
    </Card>
  );
}
