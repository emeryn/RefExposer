import { Anchor, Badge, Card, Code, Group, SimpleGrid, Stack, Table, Text, Title } from '@mantine/core';
import type { ReferentialDetail } from '../../api/types';
import { describeCron, fmtBytes, fmtDate } from '../../lib/format';

export default function SourceTab({ r }: { r: ReferentialDetail }) {
  const validation = (r.config.validation ?? {}) as Record<string, unknown>;
  return (
    <Stack gap="md">
      <Card>
        <Title order={5} mb="sm">
          Sources
        </Title>
        {r.sources.length === 0 ? (
          <Stack gap={4}>
            {r.source_urls.map((u) => (
              <Anchor key={u} href={u} size="sm" target="_blank" style={{ wordBreak: 'break-all' }}>
                {u}
              </Anchor>
            ))}
            <Text size="sm" c="dimmed">
              No download yet.
            </Text>
          </Stack>
        ) : (
          <Table.ScrollContainer minWidth={800}>
            <Table fz="sm" verticalSpacing="xs">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Source</Table.Th>
                  <Table.Th>Produced files</Table.Th>
                  <Table.Th>Date distante</Table.Th>
                  <Table.Th>Downloaded</Table.Th>
                  <Table.Th>Last check</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {r.sources.map((s, i) => (
                  <Table.Tr key={i}>
                    <Table.Td maw={380}>
                      {s.url ? (
                        <Anchor href={s.url} target="_blank" size="sm" style={{ wordBreak: 'break-all' }}>
                          {s.url}
                        </Anchor>
                      ) : (
                        <Badge variant="light">Local files</Badge>
                      )}
                      {s.etag && (
                        <Text size="xs" c="dimmed" ff="monospace" truncate>
                          ETag {s.etag}
                        </Text>
                      )}
                    </Table.Td>
                    <Table.Td>
                      {s.files.map((f) => (
                        <Text key={f} size="xs" ff="monospace">
                          {f}
                        </Text>
                      ))}
                    </Table.Td>
                    <Table.Td>{fmtDate(s.remote_date)}</Table.Td>
                    <Table.Td>
                      {s.not_modified ? (
                        <Badge size="xs" variant="light" color="indigo">
                          304 not modified
                        </Badge>
                      ) : (
                        <>
                          {fmtBytes(s.downloaded_bytes)}
                          <Text size="xs" c="dimmed">
                            {fmtDate(s.fetched_at)}
                          </Text>
                        </>
                      )}
                    </Table.Td>
                    <Table.Td>{fmtDate(s.checked_at)}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        )}
      </Card>

      <SimpleGrid cols={{ base: 1, md: 3 }}>
        <Card>
          <Title order={6} mb={6}>
            Schedule
          </Title>
          <Text size="sm">{describeCron(r.schedule)}</Text>
          {r.schedule && (
            <Code fz="xs" mt={4}>
              {r.schedule}
            </Code>
          )}
          {r.max_age && (
            <Text size="xs" c="dimmed" mt={6}>
              Considered stale after {r.max_age}
            </Text>
          )}
        </Card>
        <Card>
          <Title order={6} mb={6}>
            Validation rules
          </Title>
          <Stack gap={2}>
            <Text size="sm">Minimum rows: {String(validation.min_rows ?? 1)}</Text>
            <Text size="sm">
              Maximum drop tolerated: {validation.max_drop_pct != null ? `${validation.max_drop_pct} %` : 'unlimited'}
            </Text>
            <Text size="sm">Unique key required: {validation.unique_key ? 'yes' : 'no'}</Text>
          </Stack>
        </Card>
        <Card>
          <Title order={6} mb={6}>
            Search
          </Title>
          <Group gap={4}>
            {(r.search_columns.length ? r.search_columns : ['all text columns']).map((c) => (
              <Badge key={c} variant="default" tt="none">
                {c}
              </Badge>
            ))}
          </Group>
        </Card>
      </SimpleGrid>

      <Card>
        <Group justify="space-between" mb="sm">
          <Title order={5}>Definition</Title>
          <Text size="xs" c="dimmed">
            {r.origin === 'database' ? 'Created from the administration interface' : `config/${r.config_file}`}
          </Text>
        </Group>
        <Code block fz={12}>
          {JSON.stringify(r.config, null, 2)}
        </Code>
      </Card>
    </Stack>
  );
}
