import { Card, Code, Group, Stack, Text, TextInput, Title } from '@mantine/core';
import { IconTerminal2 } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { absoluteUrl, api } from '../../api/client';
import type { OpenApiSpec, ReferentialDetail } from '../../api/types';
import { CodeSnippet } from '../../components/Common';

const AUTH = '-H "Authorization: Bearer $REFEX_TOKEN"';

/** Example of the lookup value (key, IP or tested value) taken from the generated specification. */
function lookupExample(spec: OpenApiSpec, r: ReferentialDetail): string {
  const path = Object.keys(spec.paths).find((p) => /\/lookup\/\{[^}]+\}$/.test(p));
  const param = path ? spec.paths[path].get?.parameters?.find((p) => p.in === 'path') : undefined;
  if (param?.example != null) return String(param.example);
  return r.kind === 'mmdb' ? '81.2.69.142' : '';
}

/** Ready-to-copy curl commands: download the raw referential, query one entry. */
export default function QuickCommands({ r, spec }: { r: ReferentialDetail; spec: OpenApiSpec }) {
  const { data: dl } = useQuery({ queryKey: ['downloads', r.id], queryFn: () => api.downloads(r.id) });
  const [value, setValue] = useState(() => lookupExample(spec, r));

  const base = absoluteUrl(`/api/referentials/${encodeURIComponent(r.id)}`);
  const artifact = r.kind === 'mmdb' || r.kind === 'bloom';
  // Raw file: the published file (MaxMind DB, Bloom filter) or the original source files of a table.
  // Without source files (internal referential, keep_raw: false), the whole data as CSV.
  const hasRaw = artifact || (dl?.sources.length ?? 0) > 0;
  const download = hasRaw
    ? {
        title: artifact ? 'Download the raw file' : 'Download the raw source files',
        code: `curl -fSL -OJ ${AUTH} \\\n  "${base}/raw"`,
      }
    : { title: 'Download the referential (CSV)', code: `curl -fSL -OJ ${AUTH} \\\n  "${base}/download/csv"` };

  const v = encodeURIComponent(value || 'VALUE');
  const byKey = artifact || !!r.key;
  const lookup = byKey
    ? {
        title: r.kind === 'mmdb' ? 'Look up an IP address' : r.kind === 'bloom' ? 'Test a value' : `Look up an entry by ${r.key}`,
        code: `curl -fsS ${AUTH} \\\n  "${base}/lookup/${v}"`,
      }
    : { title: 'Search entries', code: `curl -fsS ${AUTH} \\\n  "${base}/rows?q=${v}&limit=10"` };
  const label = r.kind === 'mmdb' ? 'IP address' : r.kind === 'bloom' ? 'Value' : r.key ? `Value of ${r.key}` : 'Searched text';

  return (
    <Card>
      <Group gap="xs" mb={4}>
        <IconTerminal2 size={20} />
        <Title order={5}>Quick commands</Title>
      </Group>
      <Text size="sm" c="dimmed" mb="sm">
        Set your token first: <Code>export REFEX_TOKEN=rfx_…</Code>
      </Text>
      <Stack gap="sm">
        <CodeSnippet title={download.title} code={download.code} />
        <TextInput size="xs" label={label} value={value} onChange={(e) => setValue(e.currentTarget.value)} placeholder="VALUE" maw={360} />
        <CodeSnippet title={lookup.title} code={lookup.code} />
      </Stack>
    </Card>
  );
}
