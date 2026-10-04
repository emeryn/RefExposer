import {
  Accordion,
  Alert,
  Anchor,
  Badge,
  Button,
  Card,
  Code,
  Group,
  JsonInput,
  Loader,
  ScrollArea,
  Select,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { IconApi, IconDownload, IconExternalLink, IconFileCode, IconKey, IconPlayerPlay, IconTrash } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { Fragment, useMemo, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { absoluteUrl, api } from '../../api/client';
import type { OpenApiOperation, OpenApiParam, OpenApiSpec, ReferentialDetail } from '../../api/types';
import { CodeSnippet } from '../../components/Common';
import QuickCommands from './QuickCommands';

const METHOD_COLORS: Record<string, string> = { get: 'blue', post: 'teal', put: 'orange', delete: 'red' };
const STANDARD = new Set(['q', 'sort', 'columns', 'limit', 'offset', 'count', 'all', 'format', 'kind', 'value', 'column']);

/** Inline markdown subset used by generated descriptions: `code`, **bold**, tables. */
function Inline({ text }: { text: string }) {
  const parts = text.split(/(`[^`]+`|\*\*[^*]+\*\*)/g);
  return (
    <>
      {parts.map((p, i) =>
        p.startsWith('`') ? (
          <Code key={i}>{p.slice(1, -1)}</Code>
        ) : p.startsWith('**') ? (
          <b key={i}>{p.slice(2, -2)}</b>
        ) : (
          <Fragment key={i}>{p}</Fragment>
        ),
      )}
    </>
  );
}

function MiniMarkdown({ text }: { text: string }) {
  const blocks: ReactNode[] = [];
  const lines = text.split('\n');
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (line.trim().startsWith('|')) {
      const rows: string[][] = [];
      while (i < lines.length && lines[i].trim().startsWith('|')) {
        const cells = lines[i].trim().slice(1, -1).split('|').map((c) => c.trim());
        if (!cells.every((c) => /^-+$/.test(c))) rows.push(cells);
        i++;
      }
      i--;
      const [head, ...body] = rows;
      blocks.push(
        <Table key={i} fz="xs" withTableBorder verticalSpacing={2} maw={560} my={4}>
          <Table.Thead>
            <Table.Tr>{head.map((h, j) => <Table.Th key={j}>{h}</Table.Th>)}</Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {body.map((r, k) => (
              <Table.Tr key={k}>{r.map((c, j) => <Table.Td key={j}><Inline text={c} /></Table.Td>)}</Table.Tr>
            ))}
          </Table.Tbody>
        </Table>,
      );
    } else if (line.trim()) {
      blocks.push(
        <Text key={i} size="sm">
          <Inline text={line} />
        </Text>,
      );
    }
  }
  return <Stack gap={4}>{blocks}</Stack>;
}

function typeLabel(p: OpenApiParam) {
  const t = Array.isArray(p.schema.type) ? p.schema.type.filter((x) => x !== 'null').join('|') : p.schema.type;
  return p.schema.enum ? `enum (${p.schema.enum.length})` : `${t ?? 'string'}${p.schema.format ? ` · ${p.schema.format}` : ''}`;
}

function ParamsTable({ params }: { params: OpenApiParam[] }) {
  return (
    <Table fz="xs" verticalSpacing={4} striped>
      <Table.Thead>
        <Table.Tr>
          <Table.Th>Parameter</Table.Th>
          <Table.Th>Type</Table.Th>
          <Table.Th>Description</Table.Th>
          <Table.Th>Example</Table.Th>
        </Table.Tr>
      </Table.Thead>
      <Table.Tbody>
        {params.map((p) => (
          <Table.Tr key={`${p.in}-${p.name}`}>
            <Table.Td style={{ whiteSpace: 'nowrap' }}>
              <Code>{p.name}</Code>
              {p.required && (
                <Text span c="red" size="xs">
                  {' '}
                  *
                </Text>
              )}
              {p.in === 'path' && (
                <Badge size="xs" variant="outline" ml={4}>
                  chemin
                </Badge>
              )}
            </Table.Td>
            <Table.Td style={{ whiteSpace: 'nowrap' }}>
              <Text size="xs" ff="monospace" c="dimmed">
                {typeLabel(p)}
              </Text>
            </Table.Td>
            <Table.Td>
              <Text size="xs">
                <Inline text={p.description ?? ''} />
                {p.schema.enum && (
                  <Text span c="dimmed" size="xs">
                    {' '}
                    Values: {p.schema.enum.join(', ')}
                  </Text>
                )}
              </Text>
            </Table.Td>
            <Table.Td>
              {p.example !== undefined && (
                <Text size="xs" ff="monospace" lineClamp={1} maw={180}>
                  {String(p.example)}
                </Text>
              )}
            </Table.Td>
          </Table.Tr>
        ))}
      </Table.Tbody>
    </Table>
  );
}

function TryIt({ path, method, op }: { path: string; method: string; op: OpenApiOperation }) {
  const params = op.parameters ?? [];
  const pathParams = params.filter((p) => p.in === 'path');
  const std = params.filter((p) => p.in === 'query' && STANDARD.has(p.name));
  const filters = params.filter((p) => p.in === 'query' && !STANDARD.has(p.name));
  const body = op.requestBody?.content['application/json']?.example;

  const [values, setValues] = useState<Record<string, string>>(() => {
    const v: Record<string, string> = {};
    for (const p of pathParams) v[p.name] = p.example != null ? String(p.example) : '';
    if (std.some((p) => p.name === 'limit')) v.limit = '5';
    return v;
  });
  const [extra, setExtra] = useState<{ name: string; value: string }[]>([]);
  const [bodyText, setBodyText] = useState(body ? JSON.stringify(body, null, 2) : '');
  const [result, setResult] = useState<{ status: number; ms: number; text: string } | null>(null);
  const [loading, setLoading] = useState(false);

  const url = useMemo(() => {
    let p = path;
    for (const pp of pathParams) p = p.replace(`{${pp.name}}`, encodeURIComponent(values[pp.name] ?? ''));
    const qs = new URLSearchParams();
    for (const s of std) if (values[s.name]) qs.set(s.name, values[s.name]);
    for (const e of extra) if (e.name && e.value !== '') qs.append(e.name, e.value);
    const q = qs.toString();
    return q ? `${p}?${q}` : p;
  }, [path, pathParams, std, values, extra]);

  const curl = [
    `curl${method !== 'get' ? ` -X ${method.toUpperCase()}` : ''}${op['x-download'] ? ' -OJ' : ''} \\`,
    `  -H "Authorization: Bearer $REFEX_TOKEN" \\`,
    ...(method !== 'get' && bodyText ? [`  -H "Content-Type: application/json" \\`, `  -d '${bodyText.replace(/\s+/g, ' ')}' \\`] : []),
    `  "${absoluteUrl(url)}"`,
  ].join('\n');

  const execute = async () => {
    setLoading(true);
    const t0 = performance.now();
    try {
      const res = await fetch(url, {
        method: method.toUpperCase(),
        credentials: 'same-origin',
        headers: { 'X-Requested-With': 'RefExposer', ...(method !== 'get' ? { 'Content-Type': 'application/json' } : {}) },
        body: method !== 'get' && bodyText ? bodyText : undefined,
      });
      let text = await res.text();
      try {
        text = JSON.stringify(JSON.parse(text), null, 2);
      } catch {
        /* not JSON */
      }
      if (text.length > 30000) text = `${text.slice(0, 30000)}
      … (response truncated for display)`;
      setResult({ status: res.status, ms: Math.round(performance.now() - t0), text });
    } catch (e) {
      setResult({ status: 0, ms: 0, text: String(e) });
    } finally {
      setLoading(false);
    }
  };

  return (
    <Card withBorder padding="sm" bg="var(--app-subtle-bg)">
      <Stack gap="xs">
        <Text size="sm" fw={600}>
          Essayer
        </Text>
        <Group gap="xs" align="flex-end">
          {[...pathParams, ...std].map((p) =>
            p.schema.enum ? (
              <Select
                key={p.name}
                size="xs"
                label={p.name}
                data={p.schema.enum}
                value={values[p.name] ?? null}
                onChange={(v) => setValues((s) => ({ ...s, [p.name]: v ?? '' }))}
                clearable={!p.required}
                w={160}
              />
            ) : (
              <TextInput
                key={p.name}
                size="xs"
                label={p.name}
                placeholder={p.example != null ? String(p.example) : undefined}
                value={values[p.name] ?? ''}
                onChange={(e) => {
                  const v = e.currentTarget.value;
                  setValues((s) => ({ ...s, [p.name]: v }));
                }}
                w={p.name === 'q' || p.name === 'value' ? 220 : 130}
              />
            ),
          )}
        </Group>
        {filters.length > 0 && (
          <Stack gap={4}>
            {extra.map((e, i) => (
              <Group key={i} gap="xs">
                <Select
                  size="xs"
                  data={filters.map((f) => f.name)}
                  value={e.name || null}
                  onChange={(v) => setExtra((s) => s.map((x, j) => (j === i ? { ...x, name: v ?? '' } : x)))}
                  searchable
                  w={240}
                  placeholder="filter"
                />
                <TextInput
                  size="xs"
                  value={e.value}
                  placeholder={String(filters.find((f) => f.name === e.name)?.example ?? 'value')}
                  onChange={(ev) => {
                    const v = ev.currentTarget.value;
                    setExtra((s) => s.map((x, j) => (j === i ? { ...x, value: v } : x)));
                  }}
                  w={220}
                />
                <Button size="compact-xs" variant="subtle" color="red" onClick={() => setExtra((s) => s.filter((_, j) => j !== i))}>
                  <IconTrash size={14} />
                </Button>
              </Group>
            ))}
            <Button size="compact-xs" variant="subtle" w="fit-content" onClick={() => setExtra((s) => [...s, { name: '', value: '' }])}>
              + Add a column filter
            </Button>
          </Stack>
        )}
        {method !== 'get' && body !== undefined && (
          <JsonInput label="Request body" value={bodyText} onChange={setBodyText} autosize minRows={3} formatOnBlur size="xs" />
        )}
        <Group gap="xs">
          {op['x-download'] ? (
            <Button size="xs" component="a" href={url} leftSection={<IconDownload size={14} />}>
              Download
            </Button>
          ) : (
            <Button size="xs" onClick={execute} loading={loading} leftSection={<IconPlayerPlay size={14} />}>
              Run
            </Button>
          )}
          <Code fz={11} style={{ wordBreak: 'break-all' }}>
            {method.toUpperCase()} {url}
          </Code>
        </Group>
        <CodeSnippet title="curl" code={curl} />
        {result && (
          <Stack gap={4}>
            <Group gap="xs">
              <Badge color={result.status >= 200 && result.status < 300 ? 'teal' : 'red'}>{result.status || 'error'}</Badge>
              <Text size="xs" c="dimmed">
                {result.ms} ms
              </Text>
            </Group>
            <ScrollArea.Autosize mah={360}>
              <Code block fz={11}>
                {result.text}
              </Code>
            </ScrollArea.Autosize>
          </Stack>
        )}
      </Stack>
    </Card>
  );
}

function RowSchema({ spec }: { spec: OpenApiSpec }) {
  const schema = Object.entries(spec.components.schemas).find(([k]) => k.endsWith('_row'))?.[1];
  if (!schema?.properties) return null;
  return (
    <Card>
      <Title order={5} mb={4}>
        Row format
      </Title>
      <Text size="sm" c="dimmed" mb="sm">
        Each item of <Code>rows</Code> (and the lookup response) is a JSON object with the following fields (each may be <Code>null</Code>).
        
      </Text>
      <Table fz="xs" verticalSpacing={4} striped>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Field</Table.Th>
            <Table.Th>Type JSON</Table.Th>
            <Table.Th>Detail</Table.Th>
            <Table.Th>Example</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {Object.entries(schema.properties).map(([name, p]) => {
            const t = Array.isArray(p.type) ? p.type.filter((x) => x !== 'null').join('|') : p.type;
            return (
              <Table.Tr key={name}>
                <Table.Td>
                  <Code>{name}</Code>
                </Table.Td>
                <Table.Td>
                  <Text size="xs" ff="monospace">
                    {t}
                    {t === 'array' && p.items?.type ? `<${p.items.type}>` : ''}
                    {p.format ? ` · ${p.format}` : ''}
                  </Text>
                </Table.Td>
                <Table.Td>
                  <Text size="xs" c="dimmed">
                    {p.description}
                  </Text>
                </Table.Td>
                <Table.Td>
                  <Text size="xs" ff="monospace" lineClamp={1} maw={220}>
                    {p.examples?.length ? JSON.stringify(p.examples[0]) : ''}
                  </Text>
                </Table.Td>
              </Table.Tr>
            );
          })}
        </Table.Tbody>
      </Table>
    </Card>
  );
}

export default function ApiTab({ r }: { r: ReferentialDetail }) {
  const { data: spec, isLoading, error } = useQuery({ queryKey: ['openapi', r.id, r.version], queryFn: () => api.openapi(r.id) });
  if (isLoading) return <Loader />;
  if (error || !spec) return <Alert color="red">{(error as Error)?.message}</Alert>;

  const ops = Object.entries(spec.paths).flatMap(([path, methods]) => Object.entries(methods).map(([method, op]) => ({ path, method, op })));
  const specUrl = `/api/referentials/${r.id}/openapi.json`;

  return (
    <Stack gap="md">
      <Card>
        <Group justify="space-between" align="flex-start">
          <div style={{ maxWidth: 760 }}>
            <Group gap="xs" mb={4}>
              <IconApi size={20} />
              <Title order={5}>Automatically generated documentation</Title>
              <Badge variant="light">{spec.info.version}</Badge>
            </Group>
            <Text size="sm" c="dimmed">
              Built from the actual schema of the referential: a typed filter per column, the exact row format and examples taken from
              the data. It is updated with every new version. Authenticate your scripts with a{' '}
              <Anchor component={Link} to="/account" size="sm">
                personal API token
              </Anchor>{' '}
              (<Code>export REFEX_TOKEN=rfx_…</Code>).
            </Text>
          </div>
          <Group gap="xs">
            <Button component="a" href={`/api/catalog/docs?ref=${r.id}`} target="_blank" variant="default" size="xs" leftSection={<IconExternalLink size={14} />}>
              Open in Swagger
            </Button>
            <Button component="a" href={specUrl} download={`${r.id}-openapi.json`} variant="default" size="xs" leftSection={<IconFileCode size={14} />}>
              OpenAPI specification
            </Button>
            <Button component={Link} to="/account" variant="default" size="xs" leftSection={<IconKey size={14} />}>
              Create a token
            </Button>
          </Group>
        </Group>
      </Card>

      <QuickCommands r={r} spec={spec} />

      <Accordion variant="separated" multiple defaultValue={[ops[1]?.op.operationId ?? '']}>
        {ops.map(({ path, method, op }) => {
          const params = op.parameters ?? [];
          const filters = params.filter((p) => p.in === 'query' && !STANDARD.has(p.name));
          const main = params.filter((p) => !(p.in === 'query' && !STANDARD.has(p.name)));
          const example = op.responses['200']?.content?.['application/json']?.example;
          return (
            <Accordion.Item key={op.operationId} value={op.operationId}>
              <Accordion.Control>
                <Group gap="sm" wrap="nowrap">
                  <Badge color={METHOD_COLORS[method]} variant="filled" w={58} radius="sm">
                    {method.toUpperCase()}
                  </Badge>
                  <Code fz={12}>{path}</Code>
                  <Text size="sm" fw={500} truncate>
                    {op.summary}
                  </Text>
                </Group>
              </Accordion.Control>
              <Accordion.Panel>
                <Stack gap="sm">
                  {op.description && <MiniMarkdown text={op.description} />}
                  {main.length > 0 && <ParamsTable params={main} />}
                  {filters.length > 0 && (
                    <Accordion variant="contained">
                      <Accordion.Item value="filters">
                        <Accordion.Control>
                          <Text size="sm" fw={500}>
                            Column filters ({filters.length})
                          </Text>
                        </Accordion.Control>
                        <Accordion.Panel>
                          <ScrollArea.Autosize mah={380}>
                            <ParamsTable params={filters} />
                          </ScrollArea.Autosize>
                        </Accordion.Panel>
                      </Accordion.Item>
                    </Accordion>
                  )}
                  {example != null && (
                    <div>
                      <Text size="xs" fw={600} mb={4}>
                        Example response
                      </Text>
                      <ScrollArea.Autosize mah={260}>
                        <Code block fz={11}>
                          {JSON.stringify(example, null, 2)}
                        </Code>
                      </ScrollArea.Autosize>
                    </div>
                  )}
                  <Group gap={6}>
                    {Object.entries(op.responses).map(([code, resp]) => (
                      <Badge key={code} variant="outline" color={code.startsWith('2') ? 'teal' : 'gray'} tt="none">
                        {code} · {resp.description}
                      </Badge>
                    ))}
                  </Group>
                  <TryIt path={path} method={method} op={op} />
                </Stack>
              </Accordion.Panel>
            </Accordion.Item>
          );
        })}
      </Accordion>
      <RowSchema spec={spec} />
    </Stack>
  );
}
