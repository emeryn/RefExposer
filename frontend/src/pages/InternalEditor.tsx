import {
  ActionIcon,
  Alert,
  Anchor,
  Button,
  Card,
  Checkbox,
  Group,
  Loader,
  Modal,
  MultiSelect,
  Radio,
  Select,
  Stack,
  Switch,
  Table,
  TagsInput,
  Text,
  Textarea,
  TextInput,
  Title,
  Tooltip,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconArrowLeft, IconDatabasePlus, IconPlus, IconTrash } from '@tabler/icons-react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api } from '../api/client';
import { useReferential } from '../api/hooks';
import type { ColumnDef, ColumnType, InternalDefinition } from '../api/types';
import { useAuth } from '../auth/AuthContext';

const TYPES: { value: ColumnType; label: string }[] = [
  { value: 'text', label: 'Text' },
  { value: 'integer', label: 'Integer' },
  { value: 'number', label: 'Decimal number' },
  { value: 'boolean', label: 'Yes / no' },
  { value: 'date', label: 'Date' },
  { value: 'datetime', label: 'Date and time' },
  { value: 'list', label: 'List of texts' },
];
const ID_RE = /^[a-z0-9][a-z0-9_-]{1,62}$/;
const COL_RE = /^[A-Za-z_][A-Za-z0-9_]*$/;

type EditableColumn = ColumnDef & { uid: number; original?: string };
let uid = 0;
const blank = (name = ''): EditableColumn => ({ uid: ++uid, name, type: 'text', required: false, description: '', auto: false });

function slugify(s: string) {
  return s
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 63);
}

/** Creation and schema editing of an internal referential (admins and advanced users). */
export default function InternalEditor() {
  const { id } = useParams();
  const editing = !!id;
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { me } = useAuth();
  const { data: existing, isLoading } = useReferential(id ?? '', { enabled: editing });

  const [name, setName] = useState('');
  const [refId, setRefId] = useState('');
  const [idTouched, setIdTouched] = useState(false);
  const [description, setDescription] = useState('');
  const [category, setCategory] = useState('Internal');
  const [tags, setTags] = useState<string[]>([]);
  const [owner, setOwner] = useState('');
  const [columns, setColumns] = useState<EditableColumn[]>(() => [{ ...blank('id'), auto: true }, blank('name')]);
  const [key, setKey] = useState('id');
  const [searchColumns, setSearchColumns] = useState<string[]>([]);
  const [confidential, setConfidential] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const loaded = useRef(false);
  useEffect(() => {
    // Load the definition once: later background refetches must not erase the edits in progress
    if (!existing || loaded.current) return;
    loaded.current = true;
    const cfg = existing.config as unknown as InternalDefinition;
    setName(existing.name);
    setRefId(existing.id);
    setDescription(existing.description);
    setCategory(existing.category);
    setTags(existing.tags);
    setOwner(existing.owner ?? '');
    setColumns((cfg.columns ?? []).map((c) => ({ ...c, uid: ++uid, original: c.name })));
    setKey(cfg.key);
    setSearchColumns(cfg.search_columns ?? []);
    setConfidential(!!cfg.confidential);
  }, [existing]);

  const names = columns.map((c) => c.name.trim()).filter(Boolean);
  const problems = useMemo(() => {
    const p: string[] = [];
    if (!name.trim()) p.push('Give the referential a name.');
    if (!editing && !ID_RE.test(refId)) p.push('The identifier must be 2 to 63 lowercase letters, digits, - or _.');
    if (!columns.length) p.push('Add at least one column.');
    columns.forEach((c) => c.name && !COL_RE.test(c.name) && p.push(`Column “${c.name}”: letters, digits and _ only, not starting with a digit.`));
    if (columns.some((c) => !c.name.trim())) p.push('Every column needs a name.');
    if (new Set(names.map((n) => n.toLowerCase())).size !== names.length) p.push('Two columns have the same name.');
    if (!names.includes(key)) p.push('Choose the key column.');
    const k = columns.find((c) => c.name === key);
    if (k?.auto && k.type !== 'text' && k.type !== 'integer') p.push('An automatic key must be text or integer.');
    return p;
  }, [name, refId, columns, key, names, editing]);

  const update = (u: number, patch: Partial<EditableColumn>) => {
    setColumns((cs) => cs.map((c) => (c.uid === u ? { ...c, ...patch } : c)));
    if (patch.name !== undefined) {
      const old = columns.find((c) => c.uid === u)?.name;
      if (old === key) setKey(patch.name);
      setSearchColumns((s) => s.map((x) => (x === old ? patch.name! : x)));
    }
  };

  const body = (): InternalDefinition => ({
    id: refId,
    name: name.trim(),
    description,
    category: category || 'Internal',
    tags,
    owner: owner || null,
    key,
    columns: columns.map(({ name, type, required, description, auto }) => ({ name: name.trim(), type, required, description, auto })),
    search_columns: searchColumns.filter((s) => names.includes(s)),
    confidential,
    renames: Object.fromEntries(columns.filter((c) => c.original && c.original !== c.name.trim()).map((c) => [c.original!, c.name.trim()])),
  });

  const done = (rid: string, msg: string) => {
    notifications.show({ message: msg, color: 'teal' });
    qc.invalidateQueries({ queryKey: ['referentials'] });
    qc.invalidateQueries({ queryKey: ['referential', rid] });
    qc.invalidateQueries({ queryKey: ['records', rid] });
    qc.invalidateQueries({ queryKey: ['me'] });
  };
  const onError = (e: Error) => notifications.show({ title: 'Not saved', message: e.message, color: 'red', autoClose: 10000 });
  const save = useMutation({
    mutationFn: () => (editing ? api.updateInternal(id!, body()) : api.createInternal(body())),
    onSuccess: (r) => {
      done(r.id, editing ? 'Columns updated' : 'Internal referential created');
      navigate(`/r/${r.id}/edit`);
    },
    onError,
  });
  const remove = useMutation({
    mutationFn: () => api.deleteInternal(id!),
    onSuccess: () => {
      done(id!, 'Referential deleted');
      navigate('/');
    },
    onError,
  });

  if (!me?.can_create_internal)
    return <Alert color="orange">Creating internal referentials is reserved to administrators and advanced users.</Alert>;
  if (editing && isLoading) return <Loader />;
  if (editing && existing && existing.kind !== 'internal') return <Alert color="orange">This referential is not an internal referential.</Alert>;

  return (
    <Stack gap="md" maw={1100}>
      <Anchor component={Link} to={editing ? `/r/${id}/edit` : '/'} size="sm" c="dimmed">
        <IconArrowLeft size={14} style={{ verticalAlign: -2 }} /> {editing ? existing?.name : 'Dashboard'}
      </Anchor>
      <div>
        <Title order={2}>{editing ? 'Columns and description' : 'New internal referential'}</Title>
        <Text c="dimmed" size="sm" maw={820}>
          An internal referential lives inside RefExposer: there is no remote source, its rows are typed in the editor, loaded from a
          file or written through the API. It is queried, searched, downloaded and documented like any other referential.
        </Text>
      </div>

      <Card padding="lg">
        <Stack>
          <Group grow align="flex-start">
            <TextInput
              label="Name"
              required
              value={name}
              onChange={(e) => {
                setName(e.currentTarget.value);
                if (!editing && !idTouched) setRefId(slugify(e.currentTarget.value));
              }}
              placeholder="Internal IP ranges"
            />
            <TextInput
              label="Identifier"
              description={editing ? 'Cannot be changed' : 'Used in URLs, the API and as the SQL table name'}
              required
              disabled={editing}
              value={refId}
              onChange={(e) => {
                setRefId(e.currentTarget.value);
                setIdTouched(true);
              }}
              error={refId && !editing && !ID_RE.test(refId) ? 'lowercase letters, digits, - and _' : undefined}
            />
          </Group>
          <Textarea label="Description" autosize minRows={2} value={description} onChange={(e) => setDescription(e.currentTarget.value)} />
          <Group grow align="flex-start">
            <TextInput label="Category" value={category} onChange={(e) => setCategory(e.currentTarget.value)} />
            <TagsInput label="Tags" value={tags} onChange={setTags} />
            <TextInput label="Owner" placeholder="Team in charge" value={owner} onChange={(e) => setOwner(e.currentTarget.value)} />
          </Group>
        </Stack>
      </Card>

      <Card padding="lg">
        <Group justify="space-between" mb="sm">
          <div>
            <Title order={5}>Columns</Title>
            <Text size="xs" c="dimmed">
              The key identifies each row (unique). An automatic key is generated when a row is added without it.
            </Text>
          </div>
          <Button variant="light" leftSection={<IconPlus size={16} />} onClick={() => setColumns((c) => [...c, blank()])}>
            Add a column
          </Button>
        </Group>
        <Table verticalSpacing={6}>
          <Table.Thead>
            <Table.Tr>
              <Table.Th w={60}>Key</Table.Th>
              <Table.Th>Name</Table.Th>
              <Table.Th w={170}>Type</Table.Th>
              <Table.Th w={90}>Required</Table.Th>
              <Table.Th w={90}>
                <Tooltip label="Key only: generated when empty (UUID for text, next number for integer)">
                  <span>Automatic</span>
                </Tooltip>
              </Table.Th>
              <Table.Th>Description</Table.Th>
              <Table.Th w={40} />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {columns.map((c) => (
              <Table.Tr key={c.uid}>
                <Table.Td>
                  <Radio checked={!!c.name && key === c.name} onChange={() => setKey(c.name)} disabled={!c.name} aria-label="Key column" />
                </Table.Td>
                <Table.Td>
                  <TextInput size="xs" value={c.name} onChange={(e) => update(c.uid, { name: e.currentTarget.value })} ff="monospace"
                    description={c.original && c.original !== c.name ? `renamed from ${c.original}` : undefined} />
                </Table.Td>
                <Table.Td>
                  <Select size="xs" data={TYPES} value={c.type} allowDeselect={false} onChange={(v) => update(c.uid, { type: v as ColumnType })} />
                </Table.Td>
                <Table.Td>
                  <Checkbox checked={c.required} onChange={(e) => update(c.uid, { required: e.currentTarget.checked })} aria-label="Required" />
                </Table.Td>
                <Table.Td>
                  <Checkbox checked={c.auto} disabled={key !== c.name} onChange={(e) => update(c.uid, { auto: e.currentTarget.checked })} aria-label="Automatic" />
                </Table.Td>
                <Table.Td>
                  <TextInput size="xs" value={c.description} onChange={(e) => update(c.uid, { description: e.currentTarget.value })} />
                </Table.Td>
                <Table.Td>
                  <ActionIcon variant="subtle" color="red" onClick={() => setColumns((cs) => cs.filter((x) => x.uid !== c.uid))} aria-label="Remove column">
                    <IconTrash size={16} />
                  </ActionIcon>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
        <MultiSelect
          mt="md"
          label="Searched columns"
          description="Columns used by the full-text search (all text columns when empty)"
          data={names}
          value={searchColumns.filter((s) => names.includes(s))}
          onChange={setSearchColumns}
          maw={600}
        />
        <Switch
          mt="md"
          label="Confidential (encrypted at rest)"
          description="Encrypted at rest (AES-256-GCM): published files, previous version, internal rows and their keys in the database, column profile. Source files and generated downloads are not kept on disk. Transparent for users with access. Existing rows are encrypted or decrypted when it changes."
          checked={confidential}
          onChange={(e) => setConfidential(e.currentTarget.checked)}
          maw={700}
        />
        {editing && (
          <Alert color="gray" mt="md" p="xs">
            Existing rows are migrated: renamed columns keep their values, removed columns are dropped, and the change is refused if a
            row does not fit a new type or a new required column.
          </Alert>
        )}
      </Card>

      {problems.length > 0 && (
        <Alert color="orange" p="sm">
          {problems.map((p) => (
            <Text key={p} size="sm">
              {p}
            </Text>
          ))}
        </Alert>
      )}
      <Group justify="space-between">
        {editing ? (
          <Button variant="light" color="red" leftSection={<IconTrash size={16} />} onClick={() => setConfirmDelete(true)}>
            Delete this referential
          </Button>
        ) : (
          <span />
        )}
        <Button leftSection={<IconDatabasePlus size={16} />} disabled={problems.length > 0} loading={save.isPending} onClick={() => save.mutate()}>
          {editing ? 'Save' : 'Create'}
        </Button>
      </Group>
      <Modal opened={confirmDelete} onClose={() => setConfirmDelete(false)} title={<Text fw={700}>Delete “{existing?.name}”?</Text>}>
        <Text size="sm">All its rows, versions and access rights are deleted. This cannot be undone.</Text>
        <Group justify="flex-end" mt="md">
          <Button variant="default" onClick={() => setConfirmDelete(false)}>
            Cancel
          </Button>
          <Button color="red" loading={remove.isPending} onClick={() => remove.mutate()}>
            Delete
          </Button>
        </Group>
      </Modal>
    </Stack>
  );
}
