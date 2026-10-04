import { ActionIcon, Anchor, Badge, Button, Card, Code, Group, Loader, Menu, Stack, Table, Text, TextInput, Title, Tooltip } from '@mantine/core';
import { IconDownload, IconFileZip, IconLink, IconSearch } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { absoluteUrl, api, downloadUrl } from '../api/client';
import { CopyIcon, EmptyState } from '../components/Common';
import { FormatStatus } from '../components/Downloads';
import { fmtBytes, fmtDate, fmtNumber, fmtRelative } from '../lib/format';

const MAIN_FORMATS = ['csv', 'xlsx', 'parquet'];

export default function DownloadsPage() {
  const { data, isLoading } = useQuery({ queryKey: ['download-catalog'], queryFn: api.downloadCatalog, refetchInterval: 30000 });
  const [filter, setFilter] = useState('');
  const rows = useMemo(
    () => (data ?? []).filter((r) => `${r.name} ${r.id} ${r.category}`.toLowerCase().includes(filter.toLowerCase())),
    [data, filter],
  );

  return (
    <Stack gap="lg">
      <div>
        <Title order={2}>Downloads</Title>
        <Text c="dimmed" size="sm">
          Complete referentials in their latest published version. Each file is generated once per version: links are
          stable and can be used by your tools with an API token.
        </Text>
      </div>
      <TextInput placeholder="Filter…" leftSection={<IconSearch size={16} />} value={filter} onChange={(e) => setFilter(e.currentTarget.value)} w={300} />
      {isLoading ? (
        <Loader />
      ) : rows.length === 0 ? (
        <EmptyState icon={<IconDownload size={28} />} title="No downloadable referential" />
      ) : (
        <Card padding={0}>
          <Table.ScrollContainer minWidth={1000}>
            <Table verticalSpacing="sm" fz="sm" highlightOnHover>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Referential</Table.Th>
                  <Table.Th>Version</Table.Th>
                  <Table.Th ta="right">Rows</Table.Th>
                  <Table.Th>Formats</Table.Th>
                  <Table.Th>Sources</Table.Th>
                  <Table.Th />
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {rows.map((r) => {
                  // Bloom filter / MaxMind DB: one raw file, fetched by tools (downloaded again only when changed)
                  const raw = r.kind === 'bloom' || r.kind === 'mmdb';
                  const name = r.sources[0]?.name ?? 'file';
                  const curl = raw
                    ? `curl -fsS -u x:$REFEX_TOKEN -z ${name} -o ${name} ${absoluteUrl(`/api/referentials/${r.id}/raw`)}`
                    : `curl -OJ -H "Authorization: Bearer $REFEX_TOKEN" ${absoluteUrl(downloadUrl(r.id, 'csv'))}`;
                  return (
                  <Table.Tr key={r.id}>
                    <Table.Td>
                      <Anchor component={Link} to={`/r/${r.id}`} fw={600} c="inherit" size="sm">
                        {r.name}
                      </Anchor>
                      <Group gap={6}>
                        <Badge size="xs" variant="light" color="grape">
                          {r.category}
                        </Badge>
                        {r.license && (
                          <Text size="xs" c="dimmed">
                            {r.license}
                          </Text>
                        )}
                      </Group>
                    </Table.Td>
                    <Table.Td>
                      <Text size="sm">v{r.version}</Text>
                      <Tooltip label={fmtDate(r.data_updated_at)}>
                        <Text size="xs" c="dimmed">
                          {fmtRelative(r.data_updated_at)}
                        </Text>
                      </Tooltip>
                    </Table.Td>
                    <Table.Td ta="right" className="tabular">
                      {fmtNumber(r.row_count)}
                    </Table.Td>
                    <Table.Td>
                      <Group gap={6} wrap="nowrap">
                        {r.formats
                          .filter((f) => MAIN_FORMATS.includes(f.format))
                          .map((f) => (
                            <Tooltip key={f.format} label={f.unavailable_reason ?? (f.ready ? `${fmtBytes(f.size)} · ready` : 'generated on demand')}>
                              <Button
                                component="a"
                                href={f.unavailable_reason ? undefined : downloadUrl(r.id, f.format)}
                                size="compact-xs"
                                variant={f.ready ? 'light' : 'default'}
                                disabled={!!f.unavailable_reason}
                              >
                                {f.label}
                              </Button>
                            </Tooltip>
                          ))}
                        <Menu position="bottom-end" width={320} withinPortal>
                          <Menu.Target>
                            <Button size="compact-xs" variant="subtle">
                              Autres…
                            </Button>
                          </Menu.Target>
                          <Menu.Dropdown>
                            {r.formats
                              .filter((f) => !MAIN_FORMATS.includes(f.format))
                              .map((f) => (
                                <Menu.Item key={f.format} component="a" href={downloadUrl(r.id, f.format)} rightSection={<FormatStatus f={f} />}>
                                  {f.label}
                                </Menu.Item>
                              ))}
                          </Menu.Dropdown>
                        </Menu>
                      </Group>
                    </Table.Td>
                    <Table.Td>
                      {r.sources.length > 0 && (
                        <Button component="a" href={downloadUrl(r.id, 'source')} size="compact-xs" variant="subtle" leftSection={<IconFileZip size={12} />}>
                          {r.sources.length > 1 ? `${r.sources.length} files` : fmtBytes(r.sources[0].size)}
                        </Button>
                      )}
                    </Table.Td>
                    <Table.Td w={40}>
                      <Menu position="bottom-end" width={460} withinPortal>
                        <Menu.Target>
                          <ActionIcon variant="subtle" color="gray" aria-label="Liens">
                            <IconLink size={16} />
                          </ActionIcon>
                        </Menu.Target>
                        <Menu.Dropdown>
                          <Menu.Label>Stable links (authentication required)</Menu.Label>
                          {(raw ? [`/api/referentials/${r.id}/raw`] : ['csv', 'csv.gz', 'parquet'].map((f) => downloadUrl(r.id, f))).map((u) => (
                            <Group key={u} px="sm" py={4} justify="space-between" wrap="nowrap">
                              <Code fz={11}>{absoluteUrl(u)}</Code>
                              <CopyIcon value={absoluteUrl(u)} />
                            </Group>
                          ))}
                          <Menu.Divider />
                          <Group px="sm" py={4} justify="space-between" wrap="nowrap">
                            <Code fz={11}>{curl}</Code>
                            <CopyIcon value={curl} />
                          </Group>
                        </Menu.Dropdown>
                      </Menu>
                    </Table.Td>
                  </Table.Tr>
                  );
                })}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        </Card>
      )}
    </Stack>
  );
}
