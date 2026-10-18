import { Alert, Badge, Button, Card, Code, Group, Loader, Progress, RingProgress, SimpleGrid, Stack, Table, Text, Title, Tooltip } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconCheck, IconRefresh, IconShieldCheck, IconX } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { api } from '../../api/client';
import type { ReferentialDetail } from '../../api/types';
import { EmptyState } from '../../components/Common';
import { QUALITY_STATUS } from '../../components/Quality';
import { fmtDate, fmtNumber, fmtRelative } from '../../lib/format';

export default function QualityTab({ r }: { r: ReferentialDetail }) {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ['quality', r.id], queryFn: () => api.quality(r.id) });
  const check = useMutation({
    mutationFn: () => api.checkQuality(r.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['quality', r.id] });
      qc.invalidateQueries({ queryKey: ['referential', r.id] });
      notifications.show({ message: 'Quality checked again', color: 'teal' });
    },
    onError: (e: Error) => notifications.show({ title: 'Not checked', message: e.message, color: 'red' }),
  });
  if (isLoading || !data) return <Loader />;
  const q = data.quality;
  const manage = r.access === 'manage';
  if (!q) {
    return (
      <EmptyState icon={<IconShieldCheck size={28} />} title="No quality rule">
        Declare rules in the definition (filled, unique, format IP / domain / hash / CVE…, allowed values, range, SQL condition): they
        are checked on every new version.{' '}
        {manage && r.origin === 'database' && <Link to={`/admin/referentials/${r.id}/edit`}>Configure the referential</Link>}
      </EmptyState>
    );
  }
  const s = QUALITY_STATUS[q.status];
  const history = [...data.history].reverse();
  return (
    <Stack>
      <SimpleGrid cols={{ base: 1, md: 3 }}>
        <Card padding="lg">
          <Group>
            <RingProgress
              size={92}
              thickness={9}
              roundCaps
              sections={[{ value: q.score ?? 0, color: s.color }]}
              label={
                <Text ta="center" fw={700} size="lg">
                  {q.score ?? '—'}
                </Text>
              }
            />
            <div>
              <Badge color={s.color} variant="light" size="lg">
                {s.label}
              </Badge>
              <Text size="xs" c="dimmed" mt={6}>
                {q.checks.filter((c) => c.ok).length} / {q.checks.length} rule(s) met · {fmtNumber(q.rows)} rows
              </Text>
              <Text size="xs" c="dimmed">
                checked {fmtRelative(q.checked_at)}
                {q.recheck ? ' (rules changed, published version checked again)' : ''}
                {q.sampled ? ' on a sample of the rows (large referential)' : ''} in {q.duration}s
              </Text>
            </div>
          </Group>
        </Card>
        <Card padding="lg" style={{ gridColumn: 'span 2' }}>
          <Group justify="space-between" mb="xs">
            <Title order={6}>Score of the last versions</Title>
            {manage && (
              <Button size="compact-sm" variant="light" leftSection={<IconRefresh size={14} />} onClick={() => check.mutate()} loading={check.isPending} disabled={!!r.current_run}>
                Check again
              </Button>
            )}
          </Group>
          {history.length === 0 ? (
            <Text size="sm" c="dimmed">
              No history yet: the score is recorded with every update.
            </Text>
          ) : (
            <Group gap={3} align="flex-end" h={70} wrap="nowrap" style={{ overflowX: 'auto' }}>
              {history.map((h) => {
                const color = h.status === 'rejected' ? 'red' : (QUALITY_STATUS[(h as { status: string }).status as keyof typeof QUALITY_STATUS] ?? QUALITY_STATUS.ok).color;
                return (
                  <Tooltip key={h.run} label={`${fmtDate(h.at)} · score ${h.score ?? '—'}${h.version ? ` · version ${h.version}` : ''}`}>
                    <div
                      style={{
                        width: 14,
                        minWidth: 14,
                        height: `${Math.max(4, ((h.score ?? 0) / 100) * 70)}px`,
                        background: `var(--mantine-color-${color}-5)`,
                        borderRadius: 3,
                      }}
                    />
                  </Tooltip>
                );
              })}
            </Group>
          )}
        </Card>
      </SimpleGrid>
      {q.status === 'failed' && (
        <Alert color="red" p="sm">
          <Text size="sm">A rule of severity “error” is not met: new versions in this state are rejected and the published one is kept.</Text>
        </Alert>
      )}
      <Card padding={0}>
        <Table verticalSpacing="sm" fz="sm">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Rule</Table.Th>
              <Table.Th w={260}>Rows passing</Table.Th>
              <Table.Th>Failing</Table.Th>
              <Table.Th>Examples of failing values</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {q.checks.map((c) => (
              <Table.Tr key={c.name}>
                <Table.Td>
                  <Group gap={6} wrap="nowrap">
                    {c.ok ? <IconCheck size={16} color="var(--mantine-color-teal-6)" /> : <IconX size={16} color={`var(--mantine-color-${c.severity === 'error' ? 'red' : 'orange'}-6)`} />}
                    <div>
                      <Text size="sm" fw={500}>
                        {c.name}
                      </Text>
                      <Group gap={4}>
                        <Badge size="xs" variant="outline" color={c.severity === 'error' ? 'red' : 'gray'}>
                          {c.severity === 'error' ? 'blocking' : 'warning'}
                        </Badge>
                        {c.automatic && (
                          <Badge size="xs" variant="outline" color="gray">
                            automatic
                          </Badge>
                        )}
                      </Group>
                    </div>
                  </Group>
                </Table.Td>
                <Table.Td>
                  {c.error ? (
                    <Text size="xs" c={c.skipped ? 'dimmed' : 'red'}>
                      {c.error}
                    </Text>
                  ) : (
                    <>
                      <Group justify="space-between" mb={2}>
                        <Text size="xs" fw={600}>
                          {c.pass_pct} %
                        </Text>
                        <Text size="xs" c="dimmed">
                          threshold {c.threshold} %
                        </Text>
                      </Group>
                      <Progress value={c.pass_pct} color={c.ok ? 'teal' : c.severity === 'error' ? 'red' : 'orange'} size="sm" />
                    </>
                  )}
                </Table.Td>
                <Table.Td className="tabular">
                  {c.failing != null ? `${fmtNumber(c.failing)} / ${fmtNumber(c.checked)}` : '—'}
                </Table.Td>
                <Table.Td>
                  <Group gap={4}>
                    {(c.examples ?? []).map((e, i) => (
                      <Code key={i} fz={11}>
                        {e}
                      </Code>
                    ))}
                    {r.confidential && !c.ok && (
                      <Text size="xs" c="dimmed">
                        not shown (confidential)
                      </Text>
                    )}
                  </Group>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Card>
    </Stack>
  );
}
