import { Alert, Anchor, Badge, Button, Card, Group, Loader, Stack, Text, TextInput, Title } from '@mantine/core';
import { IconArrowRight, IconSearch } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api } from '../api/client';
import type { Row, SearchResult } from '../api/types';
import { EmptyState } from '../components/Common';
import { DataGrid, RowDrawer } from '../components/DataGrid';
import { fmtNumber } from '../lib/format';

function ResultCard({ res, q }: { res: SearchResult; q: string }) {
  const [selected, setSelected] = useState<Row | null>(null);
  return (
    <Card padding="md">
      <Group justify="space-between" mb="sm">
        <Group gap="sm">
          <Anchor component={Link} to={`/r/${res.id}`} fw={700} c="inherit">
            {res.name}
          </Anchor>
          <Badge variant="light" color="grape" size="sm">
            {res.category}
          </Badge>
          <Badge variant="light" size="sm">
            {fmtNumber(res.total)}
            {res.total_capped ? '+' : ''} result{res.total > 1 ? 's' : ''}
          </Badge>
          {res.elapsed_ms != null && (
            <Text size="xs" c="dimmed">
              {res.elapsed_ms} ms
            </Text>
          )}
        </Group>
        {res.total > res.rows.length && (
          <Button
            component={Link}
            to={`/r/${res.id}?q=${encodeURIComponent(q)}`}
            size="xs"
            variant="subtle"
            rightSection={<IconArrowRight size={14} />}
          >
            See the {res.total_capped ? 'more than ' : ''}
            {fmtNumber(res.total)} results
          </Button>
        )}
      </Group>
      <DataGrid columns={res.columns} rows={res.rows} keyColumn={res.key} onRowClick={setSelected} maxHeight={320} />
      <RowDrawer row={selected} columns={res.columns} keyColumn={res.key} onClose={() => setSelected(null)} />
    </Card>
  );
}

export default function SearchPage() {
  const [params, setParams] = useSearchParams();
  const q = params.get('q') ?? '';
  const [value, setValue] = useState(q);
  useEffect(() => setValue(q), [q]);

  const { data, isFetching, error } = useQuery({
    queryKey: ['search', q],
    queryFn: () => api.search(q, 5),
    enabled: q.length >= 2,
  });
  const hits = data?.results.filter((r) => r.total > 0) ?? [];
  const misses = data?.results.filter((r) => r.total === 0) ?? [];

  return (
    <Stack gap="lg">
      <div>
        <Title order={2}>Global search</Title>
        <Text c="dimmed" size="sm">
          Search a value (identifier, label, description…) in all referentials at once.
        </Text>
      </div>
      <TextInput
        size="lg"
        radius="xl"
        placeholder="Ex. CVE-2021-44228, log4j, France, Inception…"
        leftSection={isFetching ? <Loader size={18} /> : <IconSearch size={20} />}
        value={value}
        onChange={(e) => setValue(e.currentTarget.value)}
        onKeyDown={(e) => e.key === 'Enter' && value.trim().length >= 2 && setParams({ q: value.trim() })}
        autoFocus
      />
      {error && <Alert color="red">{(error as Error).message}</Alert>}
      {!q && (
        <EmptyState icon={<IconSearch size={28} />} title="What are you looking for?">
          The search uses the search columns declared by each referential (or its key). Exact matches on
          the key come first.
        </EmptyState>
      )}
      {data && hits.length === 0 && (
        <EmptyState icon={<IconSearch size={28} />} title={`No result for “${q}”`}>
          {data.results.length} referential(s) queried.
        </EmptyState>
      )}
      {hits.map((res) => (
        <ResultCard key={res.id} res={res} q={q} />
      ))}
      {data && misses.length > 0 && hits.length > 0 && (
        <Text size="sm" c="dimmed">
          No result: {misses.map((m) => m.name).join(', ')}
        </Text>
      )}
    </Stack>
  );
}
