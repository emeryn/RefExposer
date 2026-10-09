import {
  Alert,
  Anchor,
  Badge,
  Button,
  Card,
  Checkbox,
  Code,
  Group,
  MultiSelect,
  NumberInput,
  SegmentedControl,
  Select,
  SimpleGrid,
  Stack,
  Switch,
  Table,
  TagsInput,
  Text,
  TextInput,
  Title,
  Tooltip,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconAlertTriangle, IconCheck, IconFolderSearch, IconGitBranch, IconWorldSearch } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../../api/client';
import type { DiscoveryApplyResult, DiscoveryCandidate, DiscoveryScan, DiscoverySource } from '../../api/types';
import { SecretInput } from '../../components/SecretInput';
import { describeCron, fmtNumber } from '../../lib/format';

const FORMATS = ['csv', 'tsv', 'json', 'jsonl', 'txt', 'parquet', 'xlsx', 'xml', 'bloom', 'mmdb', 'sqlite'];
const SCHEDULES = [
  { value: '', label: 'Manual only' },
  { value: '0 * * * *', label: 'Every hour' },
  { value: '0 5 * * *', label: 'Every day at 5:00' },
  { value: '0 5 * * 1', label: 'Every Monday at 5:00' },
];

export default function DiscoveryPage() {
  const qc = useQueryClient();
  const { data: meta } = useQuery({ queryKey: ['definition-meta'], queryFn: api.definitionMeta });
  const [type, setType] = useState<'git' | 'http'>('git');
  const [repository, setRepository] = useState('');
  const [ref, setRef] = useState('');
  const [path, setPath] = useState('');
  const [token, setToken] = useState('');
  const [username, setUsername] = useState('');
  const [url, setUrl] = useState('');
  const [pattern, setPattern] = useState('');
  const [depth, setDepth] = useState<number>(0);
  const [headerName, setHeaderName] = useState('');
  const [headerValue, setHeaderValue] = useState('');
  const [basicAuth, setBasicAuth] = useState('');
  const [analyse, setAnalyse] = useState(true);
  const [category, setCategory] = useState('');
  const [tags, setTags] = useState<string[]>([]);
  const [schedule, setSchedule] = useState('0 5 * * *');
  const [groupIds, setGroupIds] = useState<string[]>([]);
  const [pull, setPull] = useState(true);
  const [scan, setScan] = useState<DiscoveryScan | null>(null);
  const [rows, setRows] = useState<DiscoveryCandidate[]>([]);
  const [result, setResult] = useState<DiscoveryApplyResult | null>(null);

  const source = (): DiscoverySource =>
    type === 'git'
      ? { type, repository: repository.trim(), ref: ref.trim() || null, path: path.trim() || null, token: token || null, username: username.trim() || null }
      : {
          type,
          url: url.trim(),
          pattern: pattern.trim() || null,
          depth,
          headers: headerName.trim() && headerValue ? { [headerName.trim()]: headerValue } : {},
          basic_auth: basicAuth || null,
        };

  const run = useMutation({
    mutationFn: () => api.discoveryScan({ source: source(), analyse, category: category.trim() || null, tags, schedule: schedule || null }),
    onSuccess: (res) => {
      setScan(res);
      setRows(res.candidates);
      setResult(null);
    },
    onError: (e: Error) => notifications.show({ title: 'Scan failed', message: e.message, color: 'red' }),
  });

  const selected = rows.filter((r) => r.selected);
  const apply = useMutation({
    mutationFn: () => api.discoveryApply({ configs: selected.map((r) => r.config), pull, grant_group_ids: groupIds.map(Number) }),
    onSuccess: (res) => {
      setResult(res);
      qc.invalidateQueries({ queryKey: ['referentials'] });
      qc.invalidateQueries({ queryKey: ['definitions'] });
      qc.invalidateQueries({ queryKey: ['secrets'] });
      const created = new Set(res.created);
      setRows((rs) => rs.map((r) => (created.has(r.config.id) ? { ...r, selected: false, duplicate_of: r.config.id } : r)));
      notifications.show({
        title: `${res.created.length} referential(s) created`,
        message: res.errors.length ? `${res.errors.length} refused: see the list` : res.runs ? `${res.runs} import(s) started` : 'Done',
        color: res.errors.length ? 'orange' : 'teal',
      });
    },
    onError: (e: Error) => notifications.show({ title: 'Failed', message: e.message, color: 'red' }),
  });

  const update = (i: number, patch: Partial<DiscoveryCandidate> | ((r: DiscoveryCandidate) => DiscoveryCandidate)) =>
    setRows((rs) => rs.map((r, j) => (j === i ? (typeof patch === 'function' ? patch(r) : { ...r, ...patch }) : r)));
  const setConfig = (i: number, k: 'id' | 'name' | 'format', v: string) => update(i, (r) => ({ ...r, config: { ...r.config, [k]: v } }));
  const errors = useMemo(() => new Map((result?.errors ?? []).map((e) => [e.id, e.error])), [result]);
  const ready = type === 'git' ? !!repository.trim() : /^https?:\/\//.test(url.trim());

  return (
    <Stack gap="lg">
      <div>
        <Title order={2}>Bulk discovery</Title>
        <Text c="dimmed" size="sm">
          One referential per data file of a Git repository (e.g. <Anchor href="https://github.com/emeryn/cybref" target="_blank">cybref</Anchor>,{' '}
          <Code>output/*</Code>) or of an HTTP folder (directory listing): scan, review the proposals, create them at once.
        </Text>
      </div>

      <Card withBorder padding="lg">
        <Stack>
          <SegmentedControl
            w="fit-content"
            value={type}
            onChange={(v) => setType(v as 'git' | 'http')}
            data={[
              { value: 'git', label: (<Group gap={6} wrap="nowrap"><IconGitBranch size={16} /> Git repository</Group>) },
              { value: 'http', label: (<Group gap={6} wrap="nowrap"><IconWorldSearch size={16} /> HTTP folder</Group>) },
            ]}
          />
          {type === 'git' ? (
            <>
              <TextInput label="Repository (HTTPS)" placeholder="https://github.com/emeryn/cybref.git" value={repository} onChange={(e) => setRepository(e.currentTarget.value)} required />
              <SimpleGrid cols={{ base: 1, sm: 2 }}>
                <TextInput label="Files" description="Glob in the repository; every file when empty" placeholder="output/*" value={path} onChange={(e) => setPath(e.currentTarget.value)} />
                <TextInput label="Branch, tag or commit" description="Default branch when empty" placeholder="main" value={ref} onChange={(e) => setRef(e.currentTarget.value)} />
                <SecretInput label="Access token (private repository)" description="A secret of the secret manager (key button): copied as a reference into every definition" placeholder={'${secret:git-token}'} value={token} onChange={setToken} />
                <TextInput label="User name of the token" placeholder="oauth2" value={username} onChange={(e) => setUsername(e.currentTarget.value)} />
              </SimpleGrid>
            </>
          ) : (
            <>
              <TextInput label="Folder URL" description="Page listing the files (Apache, nginx, Caddy autoindex…)" placeholder="https://data.example.org/feeds/" value={url} onChange={(e) => setUrl(e.currentTarget.value)} required />
              <SimpleGrid cols={{ base: 1, sm: 3 }}>
                <TextInput label="Files" description="Glob relative to the folder" placeholder="*.csv" value={pattern} onChange={(e) => setPattern(e.currentTarget.value)} />
                <NumberInput label="Sub-folder levels" min={0} max={5} value={depth} onChange={(v) => setDepth(Number(v) || 0)} />
                <SecretInput label="HTTP Basic authentication" placeholder={'user:${secret:feeds}'} keepPrefix value={basicAuth} onChange={setBasicAuth} />
                <TextInput label="Header" placeholder="Authorization" value={headerName} onChange={(e) => setHeaderName(e.currentTarget.value)} />
                <SecretInput label="Header value" placeholder={'Bearer ${secret:feeds-token}'} value={headerValue} onChange={setHeaderValue} />
              </SimpleGrid>
            </>
          )}
          <SimpleGrid cols={{ base: 1, sm: 3 }}>
            <TextInput label="Category" placeholder="Discovered" value={category} onChange={(e) => setCategory(e.currentTarget.value)} />
            <TagsInput label="Tags" placeholder="cybref" value={tags} onChange={setTags} />
            <Select label="Schedule" data={SCHEDULES} value={schedule} onChange={(v) => setSchedule(v ?? '')} description={schedule ? describeCron(schedule) : 'Updated on demand'} />
          </SimpleGrid>
          <Group justify="space-between">
            <Switch label="Analyse each file (format, JSON record path, columns): slower, more accurate" checked={analyse} onChange={(e) => setAnalyse(e.currentTarget.checked)} />
            <Button leftSection={<IconFolderSearch size={16} />} onClick={() => run.mutate()} loading={run.isPending} disabled={!ready}>
              Scan
            </Button>
          </Group>
        </Stack>
      </Card>

      {scan && (
        <Stack gap="sm">
          <Group justify="space-between">
            <Text size="sm">
              <b>{scan.count}</b> data file(s){scan.commit && <> at commit <Code>{scan.commit.slice(0, 12)}</Code></>}
              {scan.skipped_count > 0 && (
                <Tooltip label={scan.skipped.join(', ')} multiline w={400} withArrow>
                  <Text span c="dimmed"> · {scan.skipped_count} other file(s) left out</Text>
                </Tooltip>
              )}
              {scan.truncated && <Text span c="orange"> · list truncated</Text>}
            </Text>
            <Group gap="xs">
              <Button size="compact-sm" variant="subtle" onClick={() => setRows((rs) => rs.map((r) => ({ ...r, selected: !r.duplicate_of })))}>
                Select all
              </Button>
              <Button size="compact-sm" variant="subtle" onClick={() => setRows((rs) => rs.map((r) => ({ ...r, selected: false })))}>
                None
              </Button>
            </Group>
          </Group>
          <Table striped verticalSpacing={6} fz="sm">
            <Table.Thead>
              <Table.Tr>
                <Table.Th w={30} />
                <Table.Th>File</Table.Th>
                <Table.Th>Identifier</Table.Th>
                <Table.Th>Name</Table.Th>
                <Table.Th w={120}>Format</Table.Th>
                <Table.Th>Analysis</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {rows.map((r, i) => {
                const a = r.analysis;
                const err = errors.get(r.config.id);
                return (
                  <Table.Tr key={r.path}>
                    <Table.Td>
                      <Checkbox checked={r.selected} onChange={(e) => update(i, { selected: e.currentTarget.checked })} />
                    </Table.Td>
                    <Table.Td>
                      <Text size="xs" ff="monospace">
                        {r.path}
                      </Text>
                      {r.duplicate_of && (
                        <Badge size="xs" color="gray" variant="light" component={Link} to={`/r/${r.duplicate_of}`} style={{ cursor: 'pointer' }}>
                          already defined: {r.duplicate_of}
                        </Badge>
                      )}
                      {err && (
                        <Text size="xs" c="red">
                          {err}
                        </Text>
                      )}
                    </Table.Td>
                    <Table.Td>
                      <TextInput size="xs" value={r.config.id} onChange={(e) => setConfig(i, 'id', e.currentTarget.value)} />
                    </Table.Td>
                    <Table.Td>
                      <TextInput size="xs" value={r.config.name} onChange={(e) => setConfig(i, 'name', e.currentTarget.value)} />
                    </Table.Td>
                    <Table.Td>
                      <Select size="xs" data={FORMATS} value={r.config.format} onChange={(v) => v && setConfig(i, 'format', v)} />
                    </Table.Td>
                    <Table.Td>
                      {!a ? (
                        <Text size="xs" c="dimmed">
                          {scan.analysed ? '—' : 'not analysed'}
                        </Text>
                      ) : a.error ? (
                        <Tooltip label={a.error} multiline w={400} withArrow>
                          <Badge size="sm" color="red" variant="light" leftSection={<IconAlertTriangle size={12} />}>
                            error
                          </Badge>
                        </Tooltip>
                      ) : (
                        <Tooltip label={(a.columns ?? []).join(', ')} multiline w={400} withArrow disabled={!a.columns?.length}>
                          <Text size="xs">
                            {a.columns?.length ?? 0} column(s)
                            {a.total_rows != null && <> · {fmtNumber(a.total_rows)} rows</>}
                            {r.config.options && Object.keys(r.config.options).length > 0 && (
                              <Text span c="dimmed"> · {Object.entries(r.config.options).map(([k, v]) => `${k}=${String(v)}`).join(', ')}</Text>
                            )}
                          </Text>
                        </Tooltip>
                      )}
                    </Table.Td>
                  </Table.Tr>
                );
              })}
            </Table.Tbody>
          </Table>
          <Card withBorder padding="md">
            <Group justify="space-between" align="flex-end">
              <Group align="flex-end">
                <MultiSelect
                  label="Read access for the groups"
                  data={(meta?.groups ?? []).map((g) => ({ value: String(g.id), label: g.name }))}
                  value={groupIds}
                  onChange={setGroupIds}
                  w={320}
                  clearable
                />
                <Switch label="Start the imports" checked={pull} onChange={(e) => setPull(e.currentTarget.checked)} mb={6} />
              </Group>
              <Button leftSection={<IconCheck size={16} />} disabled={!selected.length} loading={apply.isPending} onClick={() => apply.mutate()}>
                Create {selected.length} referential(s)
              </Button>
            </Group>
          </Card>
          {result && result.created.length > 0 && (
            <Alert color="teal" icon={<IconCheck size={16} />}>
              {result.created.length} referential(s) created{result.runs ? `, ${result.runs} import(s) started` : ''}. Follow them on the{' '}
              <Anchor component={Link} to="/admin/referentials">
                referentials page
              </Anchor>
              .
            </Alert>
          )}
        </Stack>
      )}
    </Stack>
  );
}
