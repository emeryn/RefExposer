import {
  Alert,
  Badge,
  Button,
  Card,
  Drawer,
  FileInput,
  Group,
  Loader,
  Modal,
  NumberInput,
  Pagination,
  SegmentedControl,
  Stack,
  TagsInput,
  Text,
  TextInput,
} from '@mantine/core';
import { useDebouncedValue } from '@mantine/hooks';
import { notifications } from '@mantine/notifications';
import { IconFileImport, IconPlus, IconSearch, IconTrash } from '@tabler/icons-react';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { api } from '../../api/client';
import type { ColumnDef, InternalRecordRow, ReferentialDetail, Row } from '../../api/types';
import { DataGrid } from '../../components/DataGrid';
import { fmtDate, fmtNumber, fmtRelative } from '../../lib/format';

const PAGE = 50;
const DUCK: Record<string, string> = {
  text: 'VARCHAR', integer: 'BIGINT', number: 'DOUBLE', boolean: 'BOOLEAN', date: 'DATE', datetime: 'TIMESTAMP', list: 'VARCHAR[]',
};

function FieldInput({ col, value, onChange, keyCol }: { col: ColumnDef; value: unknown; onChange: (v: unknown) => void; keyCol: boolean }) {
  const label = (
    <Group gap={6}>
      <span>{col.name}</span>
      {keyCol && (
        <Badge size="xs" variant="light" color="yellow">
          key
        </Badge>
      )}
      <Text span size="xs" c="dimmed">
        {col.type}
      </Text>
    </Group>
  );
  const required = col.required || (keyCol && !col.auto);
  const description = col.description || (keyCol && col.auto ? 'Generated automatically when left empty' : undefined);
  switch (col.type) {
    case 'integer':
    case 'number':
      return (
        <NumberInput label={label} description={description} required={required} value={(value as number) ?? ''} decimalScale={col.type === 'integer' ? 0 : undefined}
          onChange={(v) => onChange(v === '' ? null : v)} />
      );
    case 'boolean':
      return (
        <SegmentedControl
          fullWidth
          value={value === true ? 'true' : value === false ? 'false' : ''}
          onChange={(v) => onChange(v === '' ? null : v === 'true')}
          data={[{ value: '', label: 'Not set' }, { value: 'true', label: 'Yes' }, { value: 'false', label: 'No' }]}
          aria-label={col.name}
        />
      );
    case 'date':
      return <TextInput type="date" label={label} description={description} required={required} value={(value as string) ?? ''} onChange={(e) => onChange(e.currentTarget.value || null)} />;
    case 'datetime':
      return (
        <TextInput type="datetime-local" label={label} description={description} required={required} value={value ? String(value).slice(0, 16) : ''}
          onChange={(e) => onChange(e.currentTarget.value || null)} />
      );
    case 'list':
      return <TagsInput label={label} description={description ?? 'Press Enter after each value'} required={required} value={(value as string[]) ?? []} onChange={onChange} />;
    default:
      return <TextInput label={label} description={description} required={required} value={(value as string) ?? ''} onChange={(e) => onChange(e.currentTarget.value)} />;
  }
}

function RecordDrawer({
  r,
  columns,
  keyCol,
  record,
  onClose,
}: {
  r: ReferentialDetail;
  columns: ColumnDef[];
  keyCol: string;
  record: InternalRecordRow | 'new' | null;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const [values, setValues] = useState<Record<string, unknown>>({});
  const isNew = record === 'new';
  useEffect(() => {
    if (record === 'new') setValues({});
    else if (record) setValues(Object.fromEntries(columns.map((c) => [c.name, record[c.name]])));
  }, [record, columns]);
  const done = (msg: string) => {
    notifications.show({ message: msg, color: 'teal' });
    qc.invalidateQueries({ queryKey: ['records', r.id] });
    qc.invalidateQueries({ queryKey: ['referential', r.id] });
    onClose();
  };
  const onError = (e: Error) => notifications.show({ title: 'Not saved', message: e.message, color: 'red', autoClose: 8000 });
  const save = useMutation({
    mutationFn: () => (isNew ? api.createRecord(r.id, values) : api.replaceRecord(r.id, (record as InternalRecordRow)._key, values)),
    onSuccess: () => done(isNew ? 'Row added' : 'Row saved'),
    onError,
  });
  const remove = useMutation({ mutationFn: () => api.deleteRecord(r.id, (record as InternalRecordRow)._key), onSuccess: () => done('Row deleted'), onError });
  return (
    <Drawer opened={record != null} onClose={onClose} position="right" size="lg" title={<Text fw={700}>{isNew ? 'New row' : `Edit “${(record as InternalRecordRow | null)?._key ?? ''}”`}</Text>}>
      <Stack>
        {columns.map((c) => (
          <FieldInput key={c.name} col={c} keyCol={c.name === keyCol} value={values[c.name]} onChange={(v) => setValues((s) => ({ ...s, [c.name]: v }))} />
        ))}
        {!isNew && record && (
          <Text size="xs" c="dimmed">
            Last change {fmtRelative((record as InternalRecordRow)._updated_at)} by {(record as InternalRecordRow)._updated_by ?? '—'} (
            {fmtDate((record as InternalRecordRow)._updated_at)})
          </Text>
        )}
        <Group justify="space-between">
          {!isNew ? (
            <Button variant="light" color="red" leftSection={<IconTrash size={16} />} onClick={() => remove.mutate()} loading={remove.isPending}>
              Delete
            </Button>
          ) : (
            <span />
          )}
          <Group>
            <Button variant="default" onClick={onClose}>
              Cancel
            </Button>
            <Button onClick={() => save.mutate()} loading={save.isPending}>
              {isNew ? 'Add' : 'Save'}
            </Button>
          </Group>
        </Group>
      </Stack>
    </Drawer>
  );
}

function ImportRecords({ r, opened, onClose }: { r: ReferentialDetail; opened: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [mode, setMode] = useState<'upsert' | 'replace'>('upsert');
  const run = useMutation({
    mutationFn: () => api.importRecords(r.id, file!, mode),
    onSuccess: (res) => {
      notifications.show({
        title: 'Rows imported',
        message: `${res.created} created, ${res.updated} updated, ${res.unchanged} unchanged, ${res.deleted} deleted`,
        color: 'teal',
      });
      qc.invalidateQueries({ queryKey: ['records', r.id] });
      onClose();
    },
    onError: (e: Error) => notifications.show({ title: 'Import refused', message: e.message, color: 'red', autoClose: 10000 }),
  });
  return (
    <Modal opened={opened} onClose={onClose} title={<Text fw={700}>Load rows from a file</Text>}>
      <Stack>
        <Text size="sm" c="dimmed">
          CSV, Excel, JSON or Parquet file whose column names match the referential columns ({r.columns.map((c) => c.name).join(', ')}).
          Other columns are ignored, each row is checked against the column types.
        </Text>
        <FileInput label="File" placeholder="Choose a file" value={file} onChange={setFile} accept=".csv,.tsv,.txt,.xlsx,.json,.jsonl,.parquet,.gz" />
        <SegmentedControl
          value={mode}
          onChange={(v) => setMode(v as 'upsert' | 'replace')}
          data={[
            { value: 'upsert', label: 'Add / update' },
            { value: 'replace', label: 'Replace all rows' },
          ]}
        />
        {mode === 'replace' && (
          <Alert color="orange" p="xs">
            Rows absent from the file will be deleted.
          </Alert>
        )}
        <Button onClick={() => run.mutate()} disabled={!file} loading={run.isPending}>
          Import
        </Button>
      </Stack>
    </Modal>
  );
}

/** Row editor of an internal referential. */
export default function EditTab({ r }: { r: ReferentialDetail }) {
  const [search, setSearch] = useState('');
  const [q] = useDebouncedValue(search, 300);
  const [page, setPage] = useState(1);
  const [editing, setEditing] = useState<InternalRecordRow | 'new' | null>(null);
  const [importing, setImporting] = useState(false);
  const params = new URLSearchParams({ limit: String(PAGE), offset: String((page - 1) * PAGE) });
  if (q) params.set('q', q);
  const { data, isLoading, isFetching } = useQuery({
    queryKey: ['records', r.id, params.toString()],
    queryFn: () => api.records(r.id, params),
    placeholderData: keepPreviousData,
  });
  if (isLoading || !data) return <Loader />;
  const columns = data.columns.map((c) => ({ name: c.name, type: DUCK[c.type] }));
  const rows = data.records as unknown as Row[];

  return (
    <Stack gap="sm">
      <Card padding="sm" bg="var(--mantine-color-grape-light)">
        <Text size="sm">
          <b>Internal referential</b> — its rows are managed here and through the API (see the API tab). Every change is published as a
          new version a few seconds later and appears in the history; who changed what is kept in the audit log.
          {!data.can_edit && ' You can read this referential but not edit it (manage right required).'}
        </Text>
      </Card>
      <Group justify="space-between">
        <TextInput
          placeholder="Search rows…"
          leftSection={isFetching ? <Loader size={14} /> : <IconSearch size={16} />}
          value={search}
          onChange={(e) => {
            setSearch(e.currentTarget.value);
            setPage(1);
          }}
          w={320}
        />
        {data.can_edit && (
          <Group gap="xs">
            <Button variant="default" leftSection={<IconFileImport size={16} />} onClick={() => setImporting(true)}>
              Load from a file
            </Button>
            <Button leftSection={<IconPlus size={16} />} onClick={() => setEditing('new')}>
              Add a row
            </Button>
          </Group>
        )}
      </Group>
      {data.total === 0 && !q ? (
        <Alert color="gray">This referential has no rows yet{data.can_edit ? ': add one or load a file.' : '.'}</Alert>
      ) : (
        <DataGrid
          columns={columns}
          rows={rows}
          keyColumn={data.key}
          offset={(page - 1) * PAGE}
          onRowClick={data.can_edit ? (row) => setEditing(row as unknown as InternalRecordRow) : undefined}
        />
      )}
      <Group justify="space-between">
        <Text size="sm" c="dimmed">
          {fmtNumber(data.total)} row{data.total === 1 ? '' : 's'}
          {data.can_edit && data.total > 0 && ' · click a row to edit it'}
        </Text>
        {data.total > PAGE && <Pagination size="sm" total={Math.ceil(data.total / PAGE)} value={page} onChange={setPage} />}
      </Group>
      <RecordDrawer r={r} columns={data.columns} keyCol={data.key} record={editing} onClose={() => setEditing(null)} />
      <ImportRecords r={r} opened={importing} onClose={() => setImporting(false)} />
    </Stack>
  );
}
