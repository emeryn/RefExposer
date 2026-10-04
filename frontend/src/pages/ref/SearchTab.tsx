import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Card,
  Checkbox,
  CloseButton,
  Code,
  CopyButton,
  Grid,
  Group,
  Loader,
  Menu,
  Pagination,
  ScrollArea,
  SegmentedControl,
  Select,
  Stack,
  Text,
  Textarea,
  TextInput,
  Title,
  Tooltip,
  UnstyledButton,
} from '@mantine/core';
import { useDebouncedValue } from '@mantine/hooks';
import {
  IconCheck,
  IconDownload,
  IconExternalLink,
  IconFilterOff,
  IconLink,
  IconListSearch,
  IconSearch,
} from '@tabler/icons-react';
import { keepPreviousData, useMutation, useQuery } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { absoluteUrl, api, exportUrl } from '../../api/client';
import type { Facet, RangeFacet, ReferentialDetail, Row } from '../../api/types';
import { DataGrid, RowDrawer } from '../../components/DataGrid';
import { fmtNumber } from '../../lib/format';
import { RESERVED, readFilters } from '../../lib/filters';

const MONO = { input: { fontFamily: 'var(--mantine-font-family-monospace)' } };

const PAGE = 25;
const SHOWN = 8;

function parseList(v: string | null): string[] {
  if (!v) return [];
  try {
    const a = JSON.parse(v);
    return Array.isArray(a) ? a.map(String) : [];
  } catch {
    return v.split(',').map((x) => x.trim()).filter(Boolean);
  }
}

function FacetBox({ f, selected, onToggle }: { f: Facet; selected: string[]; onToggle: (v: string) => void }) {
  const [all, setAll] = useState(false);
  const [filter, setFilter] = useState('');
  const values = f.values.filter((v) => v.value.toLowerCase().includes(filter.toLowerCase()));
  // Selected values stay visible even when absent from the current counts
  const missing = selected.filter((s) => !f.values.some((v) => v.value === s)).map((value) => ({ value, count: 0 }));
  const list = [...missing, ...values];
  const shown = all || filter ? list : list.slice(0, SHOWN);
  return (
    <div>
      <Group justify="space-between" mb={4}>
        <Text size="sm" fw={600}>
          {f.column}
        </Text>
        {selected.length > 0 && (
          <Badge size="xs" variant="light">
            {selected.length}
          </Badge>
        )}
      </Group>
      {f.values.length > SHOWN && (
        <TextInput size="xs" placeholder="Filter values…" value={filter} onChange={(e) => setFilter(e.currentTarget.value)} mb={4} />
      )}
      <Stack gap={3}>
        {shown.map((v) => (
          <Group key={v.value} justify="space-between" wrap="nowrap" gap={6}>
            <Checkbox
              size="xs"
              checked={selected.includes(v.value)}
              onChange={() => onToggle(v.value)}
              label={
                <Text size="xs" lineClamp={1} title={v.value}>
                  {f.kind === 'boolean' ? (v.value === 'true' ? 'yes' : 'no') : v.value}
                </Text>
              }
              styles={{ body: { alignItems: 'center' }, labelWrapper: { minWidth: 0 } }}
              style={{ minWidth: 0 }}
            />
            <Text size="xs" c="dimmed" className="tabular" style={{ flexShrink: 0 }}>
              {fmtNumber(v.count)}
            </Text>
          </Group>
        ))}
        {f.values.length === 0 && (
          <Text size="xs" c="dimmed">
            No value
          </Text>
        )}
      </Stack>
      {!filter && list.length > SHOWN && (
        <UnstyledButton onClick={() => setAll((a) => !a)} mt={4}>
          <Text size="xs" c="indigo">
            {all ? 'Show less' : `Show all (${list.length}${f.more ? '+' : ''})`}
          </Text>
        </UnstyledButton>
      )}
    </div>
  );
}

function RangeBox({ r, min, max, onChange }: { r: RangeFacet; min: string; max: string; onChange: (bound: 'gte' | 'lte', v: string) => void }) {
  const [lo, setLo] = useState(min);
  const [hi, setHi] = useState(max);
  useEffect(() => setLo(min), [min]);
  useEffect(() => setHi(max), [max]);
  const temporal = r.kind === 'temporal';
  const fmt = (v: string | null) => (v == null ? '' : temporal ? v.slice(0, 10) : v);
  const commit = (bound: 'gte' | 'lte', v: string) => onChange(bound, v.trim());
  return (
    <div>
      <Text size="sm" fw={600} mb={4}>
        {r.column}
      </Text>
      <Group gap={6} wrap="nowrap">
        <TextInput
          size="xs"
          type={temporal ? 'date' : 'text'}
          placeholder={fmt(r.min) || 'min'}
          value={lo}
          onChange={(e) => setLo(e.currentTarget.value)}
          onBlur={() => lo !== min && commit('gte', lo)}
          onKeyDown={(e) => e.key === 'Enter' && commit('gte', lo)}
          aria-label={`${r.column} minimum`}
          style={{ flex: 1, minWidth: 0 }}
        />
        <Text size="xs" c="dimmed">
          →
        </Text>
        <TextInput
          size="xs"
          type={temporal ? 'date' : 'text'}
          placeholder={fmt(r.max) || 'max'}
          value={hi}
          onChange={(e) => setHi(e.currentTarget.value)}
          onBlur={() => hi !== max && commit('lte', hi)}
          onKeyDown={(e) => e.key === 'Enter' && commit('lte', hi)}
          aria-label={`${r.column} maximum`}
          style={{ flex: 1, minWidth: 0 }}
        />
      </Group>
      {r.min != null && (
        <Text size="xs" c="dimmed" mt={2}>
          from {fmt(r.min)} to {fmt(r.max)}
        </Text>
      )}
    </div>
  );
}

function FacetedSearch({ r }: { r: ReferentialDetail }) {
  const [params, setParams] = useSearchParams();
  const [text, setText] = useState(params.get('q') ?? '');
  const [debounced] = useDebouncedValue(text, 350);
  const [selected, setSelected] = useState<Row | null>(null);

  const update = (fn: (p: URLSearchParams) => void) => {
    const next = new URLSearchParams(params);
    fn(next);
    next.delete('offset');
    setParams(next, { replace: true });
  };
  useEffect(() => {
    if ((params.get('q') ?? '') !== debounced) update((p) => (debounced ? p.set('q', debounced) : p.delete('q')));
  }, [debounced]);

  const offset = Number(params.get('offset') ?? 0);
  const filterParams = useMemo(() => {
    const p = new URLSearchParams();
    params.forEach((v, k) => {
      if (k !== 'offset' && k !== 'limit') p.append(k, v);
    });
    return p;
  }, [params]);
  const rowParams = useMemo(() => {
    const p = new URLSearchParams(filterParams);
    p.set('limit', String(PAGE));
    p.set('offset', String(offset));
    return p;
  }, [filterParams, offset]);

  const facets = useQuery({
    queryKey: ['facets', r.id, r.version, filterParams.toString()],
    queryFn: () => api.facets(r.id, filterParams),
    placeholderData: keepPreviousData,
  });
  const rows = useQuery({
    queryKey: ['rows', r.id, r.version, rowParams.toString()],
    queryFn: () => api.rows(r.id, rowParams),
    placeholderData: keepPreviousData,
  });

  const active = readFilters(params).filter((f) => !RESERVED.has(f.param));
  const total = rows.data?.total ?? 0;
  const highlight = (params.get('q') ?? '').trim();
  const toggle = (col: string, value: string) =>
    update((p) => {
      const key = `${col}__in`;
      const cur = parseList(p.get(key));
      const next = cur.includes(value) ? cur.filter((v) => v !== value) : [...cur, value];
      if (next.length) p.set(key, JSON.stringify(next));
      else p.delete(key);
    });
  const setRange = (col: string, bound: 'gte' | 'lte', v: string) =>
    update((p) => (v ? p.set(`${col}__${bound}`, v) : p.delete(`${col}__${bound}`)));
  const link = absoluteUrl(`/r/${r.id}/search?${filterParams}`);

  return (
    <Grid gutter="md">
      <Grid.Col span={{ base: 12, md: 4, xl: 3 }}>
        <Card padding="md">
          <Stack gap="md">
            <Group justify="space-between">
              <Title order={6}>Affiner</Title>
              {(active.length > 0 || highlight) && (
                <Button
                  size="compact-xs"
                  variant="subtle"
                  color="gray"
                  leftSection={<IconFilterOff size={12} />}
                  onClick={() => {
                    setText('');
                    setParams(new URLSearchParams(), { replace: true });
                  }}
                >
                  Reset
                </Button>
              )}
            </Group>
            {facets.isLoading && <Loader size="sm" />}
            {facets.data?.facets.map((f) => (
              <FacetBox key={f.column} f={f} selected={parseList(params.get(`${f.column}__in`))} onToggle={(v) => toggle(f.column, v)} />
            ))}
            {facets.data?.ranges.map((rg) => (
              <RangeBox
                key={rg.column}
                r={rg}
                min={params.get(`${rg.column}__gte`) ?? ''}
                max={params.get(`${rg.column}__lte`) ?? ''}
                onChange={(b, v) => setRange(rg.column, b, v)}
              />
            ))}
            {facets.data?.disabled && (
              <Text size="xs" c="dimmed">
                {facets.data.disabled}
              </Text>
            )}
            {facets.data && !facets.data.disabled && !facets.data.facets.length && !facets.data.ranges.length && (
              <Text size="xs" c="dimmed">
                No column is suited to facets (too many values): use the text search or the explorer.
              </Text>
            )}
          </Stack>
        </Card>
      </Grid.Col>
      <Grid.Col span={{ base: 12, md: 8, xl: 9 }}>
        <Stack gap="sm">
          <TextInput
            size="md"
            radius="xl"
            placeholder={`Search ${r.name}${r.search_columns.length ? ` (${r.search_columns.slice(0, 4).join(', ')})` : ''}…`}
            leftSection={rows.isFetching ? <Loader size={16} /> : <IconSearch size={18} />}
            rightSection={text ? <CloseButton onClick={() => setText('')} aria-label="Effacer" /> : null}
            value={text}
            onChange={(e) => setText(e.currentTarget.value)}
          />
          <Group justify="space-between">
            <Group gap={6}>
              <Text size="sm" fw={600} className="tabular">
                {fmtNumber(total)} result{total > 1 ? 's' : ''}
              </Text>
              {facets.data && (
                <Text size="xs" c="dimmed">
                  of {fmtNumber(r.row_count)} · {facets.data.elapsed_ms} ms
                </Text>
              )}
              {active.map((f) => (
                <Badge
                  key={f.param}
                  variant="light"
                  tt="none"
                  rightSection={<CloseButton size="xs" onClick={() => update((p) => p.delete(f.param))} aria-label="Remove" />}
                >
                  {f.column} {f.op === 'in' ? '∈' : f.op === 'gte' ? '≥' : f.op === 'lte' ? '≤' : f.op}{' '}
                  {f.op === 'in' ? parseList(f.value).join(', ') : f.value}
                </Badge>
              ))}
            </Group>
            <Group gap="xs">
              <Button component={Link} to={`/r/${r.id}?${filterParams}`} size="xs" variant="default" leftSection={<IconExternalLink size={14} />}>
                Open in the explorer
              </Button>
              <Menu position="bottom-end">
                <Menu.Target>
                  <Button size="xs" variant="default" leftSection={<IconDownload size={14} />}>
                    Export
                  </Button>
                </Menu.Target>
                <Menu.Dropdown>
                  <Menu.Label>{fmtNumber(total)} results</Menu.Label>
                  {[
                    ['csv', 'CSV'],
                    ['xlsx', 'Excel'],
                    ['json', 'JSON'],
                    ['parquet', 'Parquet'],
                  ].map(([f, l]) => (
                    <Menu.Item key={f} component="a" href={exportUrl(r.id, filterParams, f)}>
                      {l}
                    </Menu.Item>
                  ))}
                </Menu.Dropdown>
              </Menu>
              <CopyButton value={link}>
                {({ copied, copy }) => (
                  <Tooltip label={copied ? 'Link copied' : 'Copy the link of this search'}>
                    <ActionIcon variant="default" size={30} onClick={copy} aria-label="Copy the link">
                      {copied ? <IconCheck size={14} /> : <IconLink size={14} />}
                    </ActionIcon>
                  </Tooltip>
                )}
              </CopyButton>
            </Group>
          </Group>
          {rows.error ? (
            <Alert color="red">{(rows.error as Error).message}</Alert>
          ) : (
            <div style={{ opacity: rows.isFetching ? 0.65 : 1, transition: 'opacity 150ms' }}>
              <DataGrid
                columns={rows.data?.columns ?? r.columns}
                rows={rows.data?.rows ?? []}
                keyColumn={r.key}
                offset={offset}
                highlight={highlight}
                onRowClick={setSelected}
                maxHeight="calc(100vh - 360px)"
              />
            </div>
          )}
          {total > PAGE && (
            <Group justify="flex-end">
              <Pagination
                size="sm"
                total={Math.ceil(total / PAGE)}
                value={Math.floor(offset / PAGE) + 1}
                onChange={(pg) => {
                  const next = new URLSearchParams(params);
                  next.set('offset', String((pg - 1) * PAGE));
                  setParams(next, { replace: true });
                }}
              />
            </Group>
          )}
        </Stack>
      </Grid.Col>
      <RowDrawer row={selected} columns={r.columns} keyColumn={r.key} onClose={() => setSelected(null)} />
    </Grid>
  );
}

function toCsv(columns: string[], rows: Row[]) {
  const esc = (v: unknown) => {
    if (v == null) return '';
    const s = typeof v === 'object' ? JSON.stringify(v) : String(v);
    return /[",\n;]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return [columns.map(esc).join(','), ...rows.map((r) => columns.map((c) => esc(r[c])).join(','))].join('\n');
}

function ListSearch({ r }: { r: ReferentialDetail }) {
  const bloom = r.kind === 'bloom';
  const mmdb = r.kind === 'mmdb';
  const artifact = bloom || mmdb;
  const canEnrich = bloom && !!(r.config.options as Record<string, unknown> | undefined)?.enrich_bulk_url;
  const [column, setColumn] = useState<string | null>(bloom ? 'value' : mmdb ? 'ip' : r.key);
  const [enrich, setEnrich] = useState(false);
  const [text, setText] = useState('');
  const values = useMemo(() => [...new Set(text.split(/[\n,;\t]+/).map((v) => v.trim()).filter(Boolean))], [text]);
  const lookup = useMutation({ mutationFn: () => api.lookupBatch(r.id, values, artifact ? null : column, enrich, mmdb) });
  const [selected, setSelected] = useState<Row | null>(null);

  const found = useMemo(() => {
    if (!lookup.data) return [] as Row[];
    return Object.entries(lookup.data.results)
      .filter(([, v]) => v != null)
      .map(([, v]) => {
        const row = { ...(v as Row) };
        // Online enrichment: flatten the most useful details next to the value
        const details = row.details as Record<string, unknown> | undefined;
        if (details) {
          delete row.details;
          for (const [k, val] of Object.entries(details)) if (typeof val !== 'object') row[k] = val;
        }
        return row;
      });
  }, [lookup.data]);
  const foundColumns = useMemo(() => {
    if (!artifact) return r.columns;
    const names = [...new Set(found.flatMap((row) => Object.keys(row)))];
    return names.map((name) => ({ name, type: name === 'present' || name === 'probabilistic' || name === 'confirmed' ? 'BOOLEAN' : 'VARCHAR' }));
  }, [artifact, found, r.columns]);
  const download = () => {
    const cols = (artifact ? foundColumns : r.columns).map((c) => c.name);
    const header = ['searched_value', 'found', ...cols];
    const rows = Object.entries(lookup.data!.results).map(([k, v]) => ({ searched_value: k, found: v ? 'yes' : 'no', ...(v ?? {}) }));
    const url = URL.createObjectURL(new Blob([toCsv(header, rows as Row[])], { type: 'text/csv' }));
    const a = document.createElement('a');
    a.href = url;
    a.download = `${r.id}-list-search.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <Grid gutter="md">
      <Grid.Col span={{ base: 12, md: 4 }}>
        <Card padding="md">
          <Stack gap="sm">
            <Title order={6}>Search a list of values</Title>
            <Text size="xs" c="dimmed">
              {mmdb
                ? `Paste IPv4 / IPv6 addresses (one per line): each one gets the record of the network containing it (${r.mmdb?.database_type ?? 'MaxMind DB'}). Up to 10,000 addresses.`
                : bloom
                ? `Paste values (one per line): the Bloom filter tells, offline, which ones are absent (certain) or present (probable, false positives ≈ ${r.bloom ? r.bloom.estimated_fp_rate.toExponential(1) : '?'}). Up to 10,000 values.`
                : 'Paste values (one per line, or comma separated): you will know which ones exist in the referential and get their details. Up to 10,000 values, exact match.'}
            </Text>
            {!artifact && (
              <Select
                label="Column"
                data={r.columns.map((c) => ({ value: c.name, label: c.name === r.key ? `${c.name} (key)` : c.name }))}
                value={column}
                onChange={setColumn}
                searchable
                allowDeselect={false}
              />
            )}
            {canEnrich && (
              <Checkbox
                label="Confirm and detail the present values with the online API"
                description="Only the values present in the filter are sent to the external service."
                checked={enrich}
                onChange={(e) => setEnrich(e.currentTarget.checked)}
              />
            )}
            <Textarea
              label={`Values (${fmtNumber(values.length)})`}
              placeholder={mmdb ? '81.2.69.142\n2001:db8::1\n…' : r.key ? 'CVE-2021-44228\nCVE-2014-0160\n…' : 'value 1\nvalue 2'}
              autosize
              minRows={8}
              maxRows={18}
              styles={MONO}
              value={text}
              onChange={(e) => setText(e.currentTarget.value)}
            />
            <Button leftSection={<IconListSearch size={16} />} onClick={() => lookup.mutate()} loading={lookup.isPending} disabled={!values.length || !column || values.length > 10000}>
              Search
            </Button>
          </Stack>
        </Card>
      </Grid.Col>
      <Grid.Col span={{ base: 12, md: 8 }}>
        {lookup.error && <Alert color="red">{(lookup.error as Error).message}</Alert>}
        {!lookup.data && !lookup.error && (
          <Alert color="gray" icon={<IconListSearch size={18} />}>
            Results will appear here.
          </Alert>
        )}
        {lookup.data && (
          <Stack gap="sm">
            <Group justify="space-between">
              <Group gap="xs">
                <Badge color="teal" size="lg" variant="light">
                  {fmtNumber(lookup.data.found)} found
                </Badge>
                <Badge color={lookup.data.missing.length ? 'orange' : 'gray'} size="lg" variant="light">
                  {fmtNumber(lookup.data.missing.length)} missing
                </Badge>
              </Group>
              <Button size="xs" variant="default" leftSection={<IconDownload size={14} />} onClick={download}>
                Export the result (CSV)
              </Button>
            </Group>
            {lookup.data.missing.length > 0 && (
              <Card padding="sm" withBorder>
                <Group justify="space-between" mb={4}>
                  <Text size="sm" fw={600}>
                    Missing values
                  </Text>
                  <CopyButton value={lookup.data.missing.join('\n')}>
                    {({ copied, copy }) => (
                      <Button size="compact-xs" variant="subtle" onClick={copy}>
                        {copied ? 'Copied' : 'Copy the list'}
                      </Button>
                    )}
                  </CopyButton>
                </Group>
                <ScrollArea.Autosize mah={110}>
                  <Group gap={4}>
                    {lookup.data.missing.slice(0, 500).map((m) => (
                      <Code key={m}>{m}</Code>
                    ))}
                  </Group>
                </ScrollArea.Autosize>
              </Card>
            )}
            {found.length > 0 && (
              <DataGrid columns={foundColumns} rows={found} keyColumn={bloom ? 'value' : mmdb ? 'ip' : r.key} onRowClick={setSelected} maxHeight="calc(100vh - 420px)" />
            )}
          </Stack>
        )}
      </Grid.Col>
      <RowDrawer row={selected} columns={foundColumns} keyColumn={bloom ? 'value' : mmdb ? 'ip' : r.key} onClose={() => setSelected(null)} />
    </Grid>
  );
}

export default function SearchTab({ r }: { r: ReferentialDetail }) {
  const [mode, setMode] = useState<'facets' | 'list'>(r.kind === 'bloom' ? 'list' : 'facets');
  if (r.kind === 'bloom' || r.kind === 'mmdb') return <ListSearch r={r} />;
  return (
    <Stack gap="md">
      <SegmentedControl
        value={mode}
        onChange={(v) => setMode(v as 'facets' | 'list')}
        data={[
          { value: 'facets', label: 'Multi-criteria search' },
          { value: 'list', label: 'Search by list of values' },
        ]}
        w="fit-content"
      />
      {mode === 'facets' ? <FacetedSearch r={r} /> : <ListSearch r={r} />}
    </Stack>
  );
}
