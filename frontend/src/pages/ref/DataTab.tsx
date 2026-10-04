import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Checkbox,
  CloseButton,
  CopyButton,
  Group,
  Loader,
  Menu,
  Pagination,
  Popover,
  ScrollArea,
  Select,
  Stack,
  Text,
  TextInput,
  Tooltip,
} from '@mantine/core';
import { useDebouncedValue } from '@mantine/hooks';
import {
  IconCheck,
  IconColumns,
  IconDownload,
  IconFilterPlus,
  IconLink,
  IconSearch,
  IconX,
} from '@tabler/icons-react';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { absoluteUrl, api, exportUrl } from '../../api/client';
import type { ReferentialDetail, Row } from '../../api/types';
import { DataGrid, RowDrawer, nextSort } from '../../components/DataGrid';
import { fmtNumber } from '../../lib/format';
import { LargeNotice, fastColumns } from '../../components/LargeNotice';
import { OPERATORS, OPS_BY_KIND, filterParam, readFilters, typeKind } from '../../lib/filters';

const PAGE_SIZES = ['25', '50', '100', '250', '500'];

function AddFilter({ r, onAdd }: { r: ReferentialDetail; onAdd: (column: string, op: string, value: string) => void }) {
  const [opened, setOpened] = useState(false);
  const [column, setColumn] = useState<string | null>(null);
  const [op, setOp] = useState<string | null>('contains');
  const [value, setValue] = useState('');
  const type = r.columns.find((c) => c.name === column)?.type ?? 'VARCHAR';
  const ops = OPS_BY_KIND[typeKind(type)];
  const noValue = op ? OPERATORS[op]?.noValue : false;

  useEffect(() => {
    if (op && !ops.includes(op)) setOp(ops[0]);
  }, [ops, op]);

  const submit = () => {
    if (!column || !op) return;
    onAdd(column, op, noValue ? 'true' : value);
    setValue('');
    setOpened(false);
  };

  return (
    <Popover opened={opened} onChange={setOpened} width={320} position="bottom-start" shadow="md" trapFocus>
      <Popover.Target>
        <Button variant="default" leftSection={<IconFilterPlus size={16} />} onClick={() => setOpened((o) => !o)}>
          Filter
        </Button>
      </Popover.Target>
      <Popover.Dropdown>
        <Stack gap="xs">
          <Select
            label="Column"
            data={r.columns.map((c) => ({
              value: c.name,
              label: `${fastColumns(r).includes(c.name) ? '⚡ ' : ''}${c.name}  ·  ${c.type.toLowerCase()}`,
            }))}
            value={column}
            onChange={setColumn}
            searchable
            comboboxProps={{ withinPortal: false }}
          />
          <Select
            label="Operator"
            data={ops.map((o) => ({ value: o, label: OPERATORS[o].label }))}
            value={op}
            onChange={setOp}
            comboboxProps={{ withinPortal: false }}
            allowDeselect={false}
          />
          {!noValue && (
            <TextInput
              label="Value"
              description={op ? OPERATORS[op]?.hint : undefined}
              placeholder={typeKind(type) === 'temporal' ? 'AAAA-MM-JJ' : undefined}
              value={value}
              onChange={(e) => setValue(e.currentTarget.value)}
              onKeyDown={(e) => e.key === 'Enter' && submit()}
            />
          )}
          <Button onClick={submit} disabled={!column || !op || (!noValue && value === '')}>
            Appliquer
          </Button>
        </Stack>
      </Popover.Dropdown>
    </Popover>
  );
}

function ColumnPicker({ r, selected, onChange }: { r: ReferentialDetail; selected: string[] | null; onChange: (cols: string[] | null) => void }) {
  const all = r.columns.map((c) => c.name);
  const current = selected ?? all;
  const [filter, setFilter] = useState('');
  return (
    <Popover width={280} position="bottom-end" shadow="md">
      <Popover.Target>
        <Button variant="default" leftSection={<IconColumns size={16} />}>
          Columns{selected ? ` (${selected.length}/${all.length})` : ''}
        </Button>
      </Popover.Target>
      <Popover.Dropdown>
        <Stack gap="xs">
          <TextInput size="xs" placeholder="Filter…" value={filter} onChange={(e) => setFilter(e.currentTarget.value)} />
          <Group gap="xs">
            <Button size="compact-xs" variant="subtle" onClick={() => onChange(null)}>
              All
            </Button>
            <Button size="compact-xs" variant="subtle" onClick={() => onChange(r.key ? [r.key] : all.slice(0, 1))}>
              None
            </Button>
          </Group>
          <ScrollArea.Autosize mah={300}>
            <Stack gap={6}>
              {all
                .filter((c) => c.toLowerCase().includes(filter.toLowerCase()))
                .map((c) => (
                  <Checkbox
                    key={c}
                    size="xs"
                    label={c}
                    checked={current.includes(c)}
                    onChange={(e) => {
                      const next = e.currentTarget.checked ? all.filter((x) => x === c || current.includes(x)) : current.filter((x) => x !== c);
                      if (next.length) onChange(next.length === all.length ? null : next);
                    }}
                  />
                ))}
            </Stack>
          </ScrollArea.Autosize>
        </Stack>
      </Popover.Dropdown>
    </Popover>
  );
}

export default function DataTab({ r }: { r: ReferentialDetail }) {
  const [params, setParams] = useSearchParams();
  const [search, setSearch] = useState(params.get('q') ?? '');
  const [debounced] = useDebouncedValue(search, 350);
  const [selected, setSelected] = useState<Row | null>(null);

  const limit = Number(params.get('limit') ?? 50);
  const offset = Number(params.get('offset') ?? 0);
  const sort = params.get('sort');
  const columnsParam = params.get('columns');
  const filters = readFilters(params);

  const update = (fn: (p: URLSearchParams) => void, resetPage = true) => {
    const next = new URLSearchParams(params);
    fn(next);
    if (resetPage) next.delete('offset');
    setParams(next, { replace: true });
  };

  useEffect(() => {
    if ((params.get('q') ?? '') !== debounced) update((p) => (debounced ? p.set('q', debounced) : p.delete('q')));
  }, [debounced]);

  const apiParams = useMemo(() => {
    const p = new URLSearchParams(params);
    p.set('limit', String(limit));
    return p;
  }, [params, limit]);

  const { data, isFetching, error } = useQuery({
    queryKey: ['rows', r.id, r.version, apiParams.toString()],
    queryFn: () => api.rows(r.id, apiParams),
    placeholderData: keepPreviousData,
  });

  const unknownTotal = !!data && data.total == null;
  const total = data?.total ?? 0;
  const page = Math.floor(offset / limit) + 1;
  // Unknown total (large referential, unindexed filter): one more page while the current one is full
  const pages = unknownTotal ? page + ((data?.rows.length ?? 0) === limit ? 1 : 0) : Math.max(1, Math.ceil(total / limit));
  const shownColumns = data?.columns ?? r.columns;
  const apiUrl = absoluteUrl(`/api/referentials/${r.id}/rows?${apiParams}`);

  return (
    <Stack gap="sm">
      <LargeNotice r={r} />
      <Group justify="space-between" gap="sm">
        <Group gap="sm">
          <TextInput
            placeholder={`${r.large ? 'Starts with' : 'Search'}${r.search_columns.length ? ` (${r.search_columns.slice(0, 3).join(', ')}${r.search_columns.length > 3 ? '…' : ''})` : ''}`}
            leftSection={isFetching ? <Loader size={14} /> : <IconSearch size={16} />}
            rightSection={search ? <CloseButton size="sm" onClick={() => setSearch('')} aria-label="Effacer" /> : null}
            value={search}
            onChange={(e) => setSearch(e.currentTarget.value)}
            w={{ base: '100%', sm: 360 }}
          />
          <AddFilter r={r} onAdd={(c, op, v) => update((p) => p.set(filterParam(c, op), v))} />
        </Group>
        <Group gap="sm">
          <ColumnPicker
            r={r}
            selected={columnsParam ? columnsParam.split(',') : null}
            onChange={(cols) => update((p) => (cols ? p.set('columns', cols.join(',')) : p.delete('columns')), false)}
          />
          <Menu position="bottom-end" shadow="md">
            <Menu.Target>
              <Button variant="default" leftSection={<IconDownload size={16} />}>
                Export
              </Button>
            </Menu.Target>
            <Menu.Dropdown>
              <Menu.Label>{filters.length || params.get('q') ? 'Filtered results' : 'Whole referential'} ({fmtNumber(total)} rows)</Menu.Label>
              {[
                ['csv', 'CSV'],
                ['xlsx', 'Excel (.xlsx)'],
                ['json', 'JSON'],
                ['jsonl', 'JSON Lines'],
                ['parquet', 'Parquet'],
              ].map(([f, label]) => (
                <Menu.Item key={f} component="a" href={exportUrl(r.id, params, f)}>
                  {label}
                </Menu.Item>
              ))}
            </Menu.Dropdown>
          </Menu>
          <CopyButton value={apiUrl}>
            {({ copied, copy }) => (
              <Tooltip label={copied ? 'URL copied' : 'Copy the API URL of this view'}>
                <ActionIcon variant="default" size={36} onClick={copy} aria-label="Copy the API URL">
                  {copied ? <IconCheck size={16} /> : <IconLink size={16} />}
                </ActionIcon>
              </Tooltip>
            )}
          </CopyButton>
        </Group>
      </Group>

      {filters.length > 0 && (
        <Group gap={6}>
          {filters.map((f) => (
            <Badge
              key={f.param}
              size="lg"
              variant="light"
              tt="none"
              fw={500}
              rightSection={
                <ActionIcon size="xs" variant="transparent" onClick={() => update((p) => p.delete(f.param))} aria-label="Remove the filter">
                  <IconX size={12} />
                </ActionIcon>
              }
            >
              <b>{f.column}</b> {OPERATORS[f.op]?.symbol ?? f.op} {OPERATORS[f.op]?.noValue ? (f.value === 'false' ? '(no)' : '') : `“${f.value}”`}
            </Badge>
          ))}
          <Button
            size="compact-xs"
            variant="subtle"
            color="gray"
            onClick={() => {
              setSearch('');
              update((p) => {
                for (const f of filters) p.delete(f.param);
                p.delete('q');
              });
            }}
          >
            Clear all
          </Button>
        </Group>
      )}

      {error ? (
        <Alert color="red" title="Invalid query">
          {(error as Error).message}
        </Alert>
      ) : (
        <div style={{ opacity: isFetching ? 0.65 : 1, transition: 'opacity 150ms' }}>
          <DataGrid
            columns={shownColumns}
            rows={data?.rows ?? []}
            sort={sort}
            keyColumn={r.key}
            offset={offset}
            onSort={(c) => update((p) => {
              const s = nextSort(sort, c);
              if (s) p.set('sort', s);
              else p.delete('sort');
            })}
            onRowClick={(row) => setSelected(row)}
          />
        </div>
      )}

      <Group justify="space-between">
        <Text size="sm" c="dimmed" className="tabular">
          {unknownTotal && data.rows.length > 0 ? (
            <>
              Rows {fmtNumber(offset + 1)}–{fmtNumber(offset + data.rows.length)} (total not computed on this volume) · {data.elapsed_ms} ms
            </>
          ) : total > 0 ? (
            <>
              {fmtNumber(offset + 1)}–{fmtNumber(Math.min(offset + limit, total))} of <b>{fmtNumber(total)}</b> rows
              {data && <> · {data.elapsed_ms} ms</>}
            </>
          ) : data ? (
            'No results'
          ) : (
            ''
          )}
        </Text>
        <Group gap="sm">
          <Select
            size="xs"
            w={90}
            data={PAGE_SIZES}
            value={String(limit)}
            onChange={(v) => update((p) => p.set('limit', v ?? '50'))}
            allowDeselect={false}
            aria-label="Rows per page"
          />
          <Pagination
            size="sm"
            total={pages}
            value={page}
            onChange={(pg) => update((p) => p.set('offset', String((pg - 1) * limit)), false)}
            siblings={1}
          />
        </Group>
      </Group>

      <RowDrawer
        row={selected}
        columns={r.columns.filter((c) => selected && c.name in selected)}
        keyColumn={r.key}
        onClose={() => setSelected(null)}
        onFilter={(c, v) => {
          setSelected(null);
          update((p) => p.set(c, v));
        }}
      />
    </Stack>
  );
}
