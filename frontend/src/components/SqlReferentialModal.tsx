import {
  Alert,
  Badge,
  Button,
  Code,
  Group,
  Modal,
  MultiSelect,
  Select,
  Stack,
  Text,
  TextInput,
  Textarea,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconCheck, IconDatabasePlus } from '@tabler/icons-react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../api/client';
import type { Column, SqlTable } from '../api/types';

const SCHEDULES = [
  { value: '', label: 'When a source publishes a new version' },
  { value: '0 * * * *', label: 'Every hour' },
  { value: '0 5 * * *', label: 'Every day at 5:00' },
  { value: '0 5 * * 1', label: 'Every Monday at 5:00' },
];

/** Identifier suggested from the name: lower case, words joined by hyphens. */
function slug(name: string): string {
  return name
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 63);
}

/** Referentials the query reads, guessed from the identifiers it contains (the server decides for real). */
export function readsFrom(sql: string, tables: SqlTable[]): SqlTable[] {
  const identifiers = new Set(sql.match(/[A-Za-z_][A-Za-z0-9_]*/g) ?? []);
  return tables.filter((t) =>
    [t.table, ...Object.values(t.indexes ?? {})].some((name) => identifiers.has(name)),
  );
}

export function SqlReferentialModal({
  sql,
  columns,
  tables,
  opened,
  onClose,
}: {
  sql: string;
  columns: Column[];
  tables: SqlTable[];
  opened: boolean;
  onClose: () => void;
}) {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [name, setName] = useState('');
  const [id, setId] = useState('');
  const [description, setDescription] = useState('');
  const [category, setCategory] = useState('SQL');
  const [key, setKey] = useState<string | null>(null);
  const [searchColumns, setSearchColumns] = useState<string[]>([]);
  const [schedule, setSchedule] = useState('');
  const [touchedId, setTouchedId] = useState(false);

  const sources = useMemo(() => readsFrom(sql, tables), [sql, tables]);
  const names = useMemo(() => columns.map((c) => c.name), [columns]);

  useEffect(() => {
    if (!touchedId) setId(slug(name));
  }, [name, touchedId]);

  const create = useMutation({
    mutationFn: () =>
      api.sqlReferential({
        sql,
        id,
        name: name.trim(),
        description: description.trim(),
        category: category.trim() || 'SQL',
        key,
        search_columns: searchColumns,
        schedule: schedule || null,
        pull: true,
      }),
    onSuccess: (created) => {
      qc.invalidateQueries({ queryKey: ['referentials'] });
      notifications.show({
        color: 'teal',
        icon: <IconCheck size={16} />,
        title: `Referential “${created.id}” created`,
        message: `Built from ${created.sources.join(', ')}. The first version is being produced.`,
      });
      onClose();
      navigate(`/r/${created.id}`);
    },
  });

  return (
    <Modal opened={opened} onClose={onClose} title="New referential from this query" size="lg">
      <Stack gap="sm">
        <Alert color="blue" variant="light">
          <Text size="sm">
            The query is stored, not its rows: the referential is rebuilt from the published data of{' '}
            {sources.length ? (
              sources.map((s, i) => (
                <span key={s.ref_id}>
                  {i > 0 && ', '}
                  <Code>{s.table}</Code>
                </span>
              ))
            ) : (
              <b>no referential</b>
            )}{' '}
            every time one of them publishes a new version.
          </Text>
        </Alert>

        {!sources.length && (
          <Alert color="orange" variant="light">
            This query names no referential of the left panel. Only a query reading referentials can become one.
          </Alert>
        )}

        <TextInput
          label="Name"
          placeholder="Vulnerabilities exploited and likely to be"
          required
          value={name}
          onChange={(e) => setName(e.currentTarget.value)}
          data-autofocus
        />
        <TextInput
          label="Identifier"
          description="Used in the URLs and the API. Lower case, digits, hyphens."
          required
          value={id}
          error={id && !/^[a-z0-9][a-z0-9_-]{0,62}$/.test(id) ? 'Lower case letters, digits, - and _ only' : null}
          onChange={(e) => {
            setTouchedId(true);
            setId(e.currentTarget.value);
          }}
        />
        <Textarea
          label="Description"
          placeholder="What this referential holds and what it is for."
          autosize
          minRows={2}
          value={description}
          onChange={(e) => setDescription(e.currentTarget.value)}
        />
        <Group grow align="flex-start">
          <TextInput label="Category" value={category} onChange={(e) => setCategory(e.currentTarget.value)} />
          <Select
            label="Key"
            description="Column identifying a row (lookups)"
            placeholder="None"
            clearable
            searchable
            data={names}
            value={key}
            onChange={setKey}
          />
        </Group>
        <MultiSelect
          label="Searchable columns"
          description="Columns the full-text search looks into; the key when empty."
          placeholder={searchColumns.length ? undefined : 'None'}
          data={names}
          value={searchColumns}
          onChange={setSearchColumns}
          clearable
        />
        <Select
          label="Update"
          description="A unified referential follows its sources: a schedule is only needed to rebuild it anyway."
          data={SCHEDULES}
          value={schedule}
          onChange={(v) => setSchedule(v ?? '')}
          allowDeselect={false}
        />

        <Group gap="xs">
          <Text size="xs" c="dimmed">
            Columns produced:
          </Text>
          {columns.slice(0, 12).map((c) => (
            <Badge key={c.name} size="xs" variant="default">
              {c.name}
            </Badge>
          ))}
          {columns.length > 12 && (
            <Text size="xs" c="dimmed">
              and {columns.length - 12} more
            </Text>
          )}
        </Group>

        {create.error && (
          <Alert color="red" title="The referential was not created">
            <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>
              {(create.error as Error).message}
            </Text>
          </Alert>
        )}

        <Group justify="flex-end" mt="xs">
          <Button variant="default" onClick={onClose}>
            Cancel
          </Button>
          <Button
            leftSection={<IconDatabasePlus size={16} />}
            loading={create.isPending}
            disabled={!name.trim() || !id || !/^[a-z0-9][a-z0-9_-]{0,62}$/.test(id) || !sources.length}
            onClick={() => create.mutate()}
          >
            Create and build
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
