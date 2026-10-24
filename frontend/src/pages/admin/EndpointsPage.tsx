import { ActionIcon, Alert, Anchor, Badge, Button, Card, Code, Collapse, Group, Loader, Menu, SegmentedControl, SimpleGrid, Stack, Table, Text, TextInput, Title, Tooltip } from '@mantine/core';
import { IconAlertTriangle, IconChevronDown, IconChevronRight, IconClock, IconDatabase, IconDownload, IconLink, IconLock, IconLockOpen, IconSearch, IconServer2, IconShare, IconWorld } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { Fragment, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../../api/client';
import type { EndpointHost } from '../../api/types';
import { StatCard } from '../../components/Common';
import { describeCron, fmtBytes, fmtNumber, fmtRelative } from '../../lib/format';

const ROLE_LABEL: Record<string, string> = { source: 'source', delta: 'delta', git: 'Git', enrichment: 'enrichment (lookups)' };

function HostRow({ h, open, onToggle }: { h: EndpointHost; open: boolean; onToggle: () => void }) {
  return (
    <>
      <Table.Tr style={{ cursor: 'pointer' }} onClick={onToggle}>
        <Table.Td w={30}>{open ? <IconChevronDown size={16} /> : <IconChevronRight size={16} />}</Table.Td>
        <Table.Td>
          <Group gap={6} wrap="nowrap">
            {h.scheme === 'https' ? (
              <IconLock size={14} color="var(--mantine-color-teal-6)" />
            ) : (
              <Tooltip label="Not encrypted (http)">
                <IconLockOpen size={14} color="var(--mantine-color-orange-6)" />
              </Tooltip>
            )}
            <Text fw={600} size="sm" ff="monospace">
              {h.host.replace(/^https?:\/\//, '')}
            </Text>
          </Group>
          <Group gap={4} mt={2}>
            {h.roles.filter((r) => r !== 'source').map((r) => (
              <Badge key={r} size="xs" variant="outline" color="gray">
                {ROLE_LABEL[r] ?? r}
              </Badge>
            ))}
          </Group>
        </Table.Td>
        <Table.Td ta="right" className="tabular">
          {h.referential_count}
        </Table.Td>
        <Table.Td ta="right" className="tabular">
          {h.url_count}
        </Table.Td>
        <Table.Td ta="right" className="tabular">
          {fmtNumber(h.calls_per_day)}
        </Table.Td>
        <Table.Td>
          <Group gap={4}>
            {h.auth.length === 0 ? (
              <Text size="xs" c="dimmed">
                none
              </Text>
            ) : (
              h.auth.map((a) => (
                <Badge key={a} size="xs" variant="light" tt="none">
                  {a}
                </Badge>
              ))
            )}
            {h.secrets.map((s) => (
              <Badge key={s} size="xs" variant="light" color="indigo" tt="none">
                secret {s}
              </Badge>
            ))}
          </Group>
        </Table.Td>
        <Table.Td>
          <Text size="xs">{h.last_contact ? fmtRelative(h.last_contact) : '—'}</Text>
          {h.downloaded_bytes > 0 && (
            <Text size="xs" c="dimmed">
              {fmtBytes(h.downloaded_bytes)}
            </Text>
          )}
        </Table.Td>
        <Table.Td>
          {h.errors.length > 0 && (
            <Tooltip label={`In error: ${h.errors.join(', ')}`}>
              <Badge size="sm" color="red" variant="light" leftSection={<IconAlertTriangle size={12} />}>
                {h.errors.length}
              </Badge>
            </Tooltip>
          )}
        </Table.Td>
      </Table.Tr>
      <Table.Tr>
        <Table.Td colSpan={8} p={0} style={{ borderBottom: open ? undefined : 'none' }}>
          <Collapse in={open}>
            <Stack gap="xs" p="sm" pl={46} bg="var(--mantine-color-default-hover)">
              {h.urls.map((u) => (
                <div key={u.url}>
                  <Group gap={6}>
                    <Code fz={12} style={{ wordBreak: 'break-all' }}>
                      {u.url}
                    </Code>
                    {u.shared && (
                      <Tooltip label="The same URL is downloaded by several referentials">
                        <Badge size="xs" color="grape" variant="light" leftSection={<IconShare size={10} />}>
                          shared by {new Set(u.referentials.map((r) => r.referential)).size}
                        </Badge>
                      </Tooltip>
                    )}
                    <Text size="xs" c="dimmed">
                      {fmtNumber(u.calls_per_day)} call(s) / day
                    </Text>
                  </Group>
                  <Table fz="xs" verticalSpacing={2} mt={4} withRowBorders={false}>
                    <Table.Tbody>
                      {u.referentials.map((r) => (
                        <Table.Tr key={`${r.referential}-${r.role}`}>
                          <Table.Td w={260}>
                            <Anchor component={Link} to={`/r/${r.referential}`} size="xs" fw={500}>
                              {r.name}
                            </Anchor>{' '}
                            <Text span size="xs" c="dimmed" ff="monospace">
                              {r.referential}
                            </Text>
                          </Table.Td>
                          <Table.Td w={110}>
                            <Badge size="xs" variant="outline" color="gray">
                              {ROLE_LABEL[r.role] ?? r.role}
                            </Badge>
                          </Table.Td>
                          <Table.Td w={220}>
                            {!r.enabled ? (
                              <Text size="xs" c="dimmed">
                                disabled
                              </Text>
                            ) : r.role === 'enrichment' ? (
                              <Text size="xs" c="dimmed">
                                at lookup time
                              </Text>
                            ) : (
                              <Text size="xs">{describeCron(r.schedule)}</Text>
                            )}
                          </Table.Td>
                          <Table.Td>
                            <Text size="xs" c={r.health_status && ['error', 'corrupted', 'rejected'].includes(r.health_status) ? 'red' : 'dimmed'} lineClamp={1}>
                              {r.last_contact ? `contacted ${fmtRelative(r.last_contact)}` : 'never contacted'}
                              {r.not_modified ? ' (unchanged, HTTP 304)' : ''}
                              {r.last_error ? ` · ${r.last_error}` : ''}
                            </Text>
                          </Table.Td>
                        </Table.Tr>
                      ))}
                    </Table.Tbody>
                  </Table>
                </div>
              ))}
            </Stack>
          </Collapse>
        </Table.Td>
      </Table.Tr>
    </>
  );
}

export default function EndpointsPage() {
  const { data, isLoading } = useQuery({ queryKey: ['endpoints'], queryFn: api.endpoints });
  const [filter, setFilter] = useState('');
  const [view, setView] = useState<'all' | 'shared' | 'errors' | 'http'>('all');
  const [open, setOpen] = useState<Set<string>>(new Set());
  const hosts = useMemo(
    () =>
      (data?.hosts ?? [])
        .filter((h) => (view === 'shared' ? h.urls.some((u) => u.shared) : view === 'errors' ? h.errors.length > 0 : view === 'http' ? h.scheme === 'http' : true))
        .filter((h) => !filter || JSON.stringify(h).toLowerCase().includes(filter.toLowerCase())),
    [data, filter, view],
  );
  if (isLoading || !data) return <Loader />;
  const t = data.totals;
  const toggle = (host: string) =>
    setOpen((s) => {
      const next = new Set(s);
      if (next.has(host)) next.delete(host);
      else next.add(host);
      return next;
    });

  return (
    <Stack gap="lg">
      <Group justify="space-between" align="flex-end">
        <div>
          <Text c="dimmed" size="sm">
            Every URL the referentials contact, grouped by host: an API called by several referentials appears once, with all of them.
            Credentials are shown by name only.
          </Text>
        </div>
        <Menu position="bottom-end">
          <Menu.Target>
            <Button variant="default" leftSection={<IconDownload size={16} />}>
              Export CSV
            </Button>
          </Menu.Target>
          <Menu.Dropdown>
            <Menu.Item component="a" href="/api/admin/endpoints/export?hosts_only=true">
              Hosts (firewall / proxy allowlist)
            </Menu.Item>
            <Menu.Item component="a" href="/api/admin/endpoints/export">
              URLs and referentials
            </Menu.Item>
          </Menu.Dropdown>
        </Menu>
      </Group>
      <SimpleGrid cols={{ base: 2, md: 5 }}>
        <StatCard label="Hosts" value={fmtNumber(t.hosts)} icon={<IconServer2 size={20} />} />
        <StatCard label="URLs" value={fmtNumber(t.urls)} icon={<IconLink size={20} />} />
        <StatCard label="Referentials" value={fmtNumber(t.referentials)} icon={<IconDatabase size={20} />} />
        <StatCard label="Calls / day (estimated)" value={fmtNumber(t.calls_per_day)} icon={<IconClock size={20} />} />
        <StatCard label="URLs shared" value={fmtNumber(t.shared_urls)} icon={<IconShare size={20} />} color="grape" />
      </SimpleGrid>
      {t.insecure_hosts > 0 && (
        <Alert color="orange" variant="light" icon={<IconLockOpen size={16} />} p="sm">
          <Text size="sm">
            {t.insecure_hosts} host(s) contacted without encryption (http): the data can be read or altered on the way. Prefer https
            when the source offers it.
          </Text>
        </Alert>
      )}
      <Group justify="space-between">
        <SegmentedControl
          value={view}
          onChange={(v) => setView(v as typeof view)}
          data={[
            { value: 'all', label: `All (${data.hosts.length})` },
            { value: 'shared', label: 'Shared URLs' },
            { value: 'errors', label: 'With errors' },
            { value: 'http', label: 'Not encrypted' },
          ]}
        />
        <Group gap="xs">
          <TextInput placeholder="Host, URL, referential, secret…" leftSection={<IconSearch size={16} />} value={filter} onChange={(e) => setFilter(e.currentTarget.value)} w={300} />
          <ActionIcon variant="default" size="lg" onClick={() => setOpen(open.size ? new Set() : new Set(hosts.map((h) => h.host)))} aria-label="Expand all">
            {open.size ? <IconChevronDown size={16} /> : <IconChevronRight size={16} />}
          </ActionIcon>
        </Group>
      </Group>
      <Card padding={0}>
        <Table.ScrollContainer minWidth={900}>
          <Table verticalSpacing="sm" fz="sm" highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th />
                <Table.Th>Host</Table.Th>
                <Table.Th ta="right">Referentials</Table.Th>
                <Table.Th ta="right">URLs</Table.Th>
                <Table.Th ta="right">Calls / day</Table.Th>
                <Table.Th>Authentication</Table.Th>
                <Table.Th>Last contact</Table.Th>
                <Table.Th>Errors</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {hosts.map((h) => (
                <Fragment key={h.host}>
                  <HostRow h={h} open={open.has(h.host)} onToggle={() => toggle(h.host)} />
                </Fragment>
              ))}
              {hosts.length === 0 && (
                <Table.Tr>
                  <Table.Td colSpan={8}>
                    <Text size="sm" c="dimmed" ta="center" py="md">
                      No host
                    </Text>
                  </Table.Td>
                </Table.Tr>
              )}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      </Card>
      {data.others.length > 0 && (
        <Card>
          <Title order={5} mb="xs">
            Other outgoing connections of the instance
          </Title>
          <Table fz="sm" verticalSpacing={4}>
            <Table.Tbody>
              {data.others.map((o, i) => (
                <Table.Tr key={i} style={{ opacity: o.enabled ? 1 : 0.55 }}>
                  <Table.Td w={190}>
                    <Badge variant="light" color="gray" tt="none">
                      {o.kind}
                    </Badge>
                  </Table.Td>
                  <Table.Td>
                    <Code fz={12}>{o.url}</Code>
                  </Table.Td>
                  <Table.Td>
                    <Text size="xs" c="dimmed">
                      {o.name}
                      {o.detail ? ` · ${o.detail}` : ''}
                      {!o.enabled ? ' · disabled' : ''}
                    </Text>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Card>
      )}
      {data.local_sources.length > 0 && (
        <Text size="xs" c="dimmed">
          <IconWorld size={12} /> {data.local_sources.length} referential(s) contact nothing (uploaded files, sync folder, internal).
        </Text>
      )}
    </Stack>
  );
}
