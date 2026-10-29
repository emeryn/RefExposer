import {
  ActionIcon,
  Alert as Callout,
  Anchor,
  Badge,
  Button,
  Card,
  Drawer,
  Group,
  Loader,
  Modal,
  MultiSelect,
  NumberInput,
  Select,
  ScrollArea,
  SimpleGrid,
  Stack,
  Table,
  Tabs,
  TagsInput,
  Text,
  TextInput,
  Textarea,
  Title,
  Tooltip,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import {
  IconAlertTriangle,
  IconBookmark,
  IconDeviceFloppy,
  IconGraph,
  IconPlayerPlay,
  IconSearch,
  IconLink,
  IconPlus,
  IconShare,
  IconTable,
  IconTrash,
  IconUsers,
  IconX,
} from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api } from '../api/client';
import type {
  Connection,
  GraphNode,
  Investigation,
  InvestigationSpec,
  MatchMode,
  SavedSearch,
  SearchShare,
} from '../api/types';
import { EmptyState, StatCard } from '../components/Common';
import { DataGrid } from '../components/DataGrid';
import { RelationGraph } from '../components/RelationGraph';
import { fmtDate, fmtNumber, fmtRelative } from '../lib/format';

/** The spec the page starts from, and what the URL carries so an investigation can be linked to. */
const EMPTY: InvestigationSpec = {
  values: [],
  entity: null,
  depth: 1,
  scope: [],
  follow: [],
  rows_per_lookup: 5,
  match: 'auto',
};

function specFromUrl(params: URLSearchParams): InvestigationSpec {
  const list = (key: string) => (params.get(key) ?? '').split(',').filter(Boolean);
  // `value` is the single-seed spelling of a link made before several were possible
  const single = params.get('value');
  return {
    values: list('values').length ? list('values') : single ? [single] : [],
    entity: params.get('entity') || null,
    depth: Number(params.get('depth')) || 1,
    scope: list('scope'),
    follow: list('follow'),
    rows_per_lookup: Number(params.get('rows')) || 5,
    match: (params.get('match') as MatchMode) || 'auto',
  };
}

function urlFromSpec(spec: InvestigationSpec): Record<string, string> {
  const out: Record<string, string> = { values: (spec.values ?? []).join(',') };
  if (spec.entity) out.entity = spec.entity;
  if (spec.depth && spec.depth !== 1) out.depth = String(spec.depth);
  if (spec.scope?.length) out.scope = spec.scope.join(',');
  if (spec.follow?.length) out.follow = spec.follow.join(',');
  if (spec.rows_per_lookup && spec.rows_per_lookup !== 5) out.rows = String(spec.rows_per_lookup);
  if (spec.match && spec.match !== 'auto') out.match = spec.match;
  return out;
}

/**
 * What one node of the graph is, and where it was found: the rows, the relations, and the ways on from here.
 *
 * Written as plain content rather than a drawer, because it is shown in two places — a drawer beside the
 * page, and the right-hand pane of the enlarged graph, where a drawer would cover the very picture it is
 * describing.
 */
function NodeDetails({
  node,
  result,
  onExpand,
  expanding,
}: {
  node: GraphNode;
  result: Investigation;
  /** Grow the graph from a value, in place */
  onExpand: (value: string, entity: string | null, from: GraphNode) => void;
  expanding: string | null;
}) {
  const [row, setRow] = useState<Record<string, unknown> | null>(null);
  const hits = result.hits.filter((h) =>
    node.kind === 'source' ? h.referential_id === node.value : node.referentials.includes(h.referential_id),
  );
  const relations = result.edges.filter(
    (e) => e.kind === 'same_row' && (e.source === node.id || e.target === node.id),
  );
  return (
    <Stack gap="md">
        <Group gap="xs">
          <Badge variant="filled">
            {node.kind === 'source' ? 'Referential' : node.kind === 'query' ? 'Text searched' : node.entity}
          </Badge>
          {node.seed && <Badge color="red">What you searched</Badge>}
          <Badge variant="light">hop {node.depth}</Badge>
          {node.kind === 'source' ? (
            <Anchor component={Link} to={`/r/${node.value}`} size="sm">
              Open the referential
            </Anchor>
          ) : (
            <>
              <Button
                size="xs"
                leftSection={<IconPlus size={14} />}
                onClick={() => onExpand(node.value, node.entity, node)}
                loading={expanding === node.value}
              >
                Expand from here
              </Button>
              <Button
                size="xs"
                variant="light"
                component={Link}
                to={`/investigate?values=${encodeURIComponent(node.value)}&depth=2`}
                leftSection={<IconSearch size={14} />}
              >
                Investigate it on its own
              </Button>
            </>
          )}
        </Group>
        {node.kind !== 'source' && (
          <Text size="xs" c="dimmed">
            <b>Expand from here</b> grows this graph with what that value touches, keeping everything already on
            screen. Investigating it on its own starts a new question.
          </Text>
        )}
        {node.kind !== 'source' && (
          <Text size="sm" style={{ wordBreak: 'break-all' }}>
            <Text span c="dimmed">
              value:
            </Text>{' '}
            <b>{node.value}</b>
          </Text>
        )}

        {!!relations.length && (
          <div>
            <Text size="sm" fw={600} mb={4}>
              Shares a row with
            </Text>
            <Group gap={6}>
              {relations.map((e, i) => {
                const otherId = e.source === node.id ? e.target : e.source;
                const other = result.nodes.find((n) => n.id === otherId);
                return (
                  <Tooltip key={i} label={`${e.referential_id} · ${e.label}`}>
                    <Badge variant="light" color="blue">
                      {other?.label ?? otherId}
                    </Badge>
                  </Tooltip>
                );
              })}
            </Group>
          </div>
        )}

        {hits.map((hit) => (
          <Card key={`${hit.referential_id}:${hit.column}`} withBorder padding="sm">
            <Group justify="space-between" mb="xs">
              <Group gap="xs">
                <Anchor component={Link} to={`/r/${hit.referential_id}`} fw={600} size="sm">
                  {hit.referential_name}
                </Anchor>
                <Badge size="sm" variant="light" color="grape">
                  {hit.category}
                </Badge>
                <Tooltip
                  label={
                    hit.how === 'exact'
                      ? `Exact match on ${hit.column}, a ${hit.entity} column`
                      : 'The value is mentioned somewhere in these rows (text search)'
                  }
                >
                  <Badge size="sm" variant="outline" color={hit.how === 'exact' ? 'teal' : 'gray'}>
                    {hit.how === 'exact' ? hit.column : 'mentions it'}
                  </Badge>
                </Tooltip>
              </Group>
              <Text size="xs" c="dimmed">
                {fmtNumber(hit.row_count)} row(s)
              </Text>
            </Group>
            <DataGrid
              columns={Object.keys(hit.rows[0] ?? {}).map((c) => ({ name: c, type: '' }))}
              rows={hit.rows}
              keyColumn={hit.column}
              onRowClick={setRow}
              maxHeight={240}
              highlight={node.kind === 'source' ? undefined : node.value}
            />
            <Text size="xs" c="dimmed" mt={6}>
              Click a row to pick a value from it and carry on from there.
            </Text>
          </Card>
        ))}

        {/* The row that was clicked, every value of it expandable: this is how you follow something the
            investigation did not type as an identifier — a product name, a ticket, a label */}
        {row && (
          <Card withBorder padding="sm" bg="var(--mantine-color-default-hover)">
            <Group justify="space-between" mb="xs">
              <Text size="sm" fw={600}>
                Carry on from a value of this row
              </Text>
              <ActionIcon variant="subtle" onClick={() => setRow(null)} aria-label="Close">
                <IconX size={16} />
              </ActionIcon>
            </Group>
            <Stack gap={6}>
              {Object.entries(row)
                .filter(([, value]) => value !== null && value !== undefined && String(value).trim() !== '')
                .map(([column, value]) => (
                  <Group key={column} gap="xs" wrap="nowrap" justify="space-between">
                    <div style={{ minWidth: 0 }}>
                      <Text size="xs" c="dimmed">
                        {column}
                      </Text>
                      <Text size="sm" style={{ wordBreak: 'break-all' }}>
                        {String(value)}
                      </Text>
                    </div>
                    <Button
                      size="compact-xs"
                      variant="light"
                      leftSection={<IconPlus size={12} />}
                      onClick={() => onExpand(String(value), null, node)}
                      loading={expanding === String(value)}
                      style={{ flexShrink: 0 }}
                    >
                      Expand
                    </Button>
                  </Group>
                ))}
            </Stack>
          </Card>
        )}
    </Stack>
  );
}

/** The details beside the page, in a drawer. */
function NodePanel({
  node,
  result,
  onClose,
  onExpand,
  expanding,
}: {
  node: GraphNode;
  result: Investigation;
  onClose: () => void;
  onExpand: (value: string, entity: string | null, from: GraphNode) => void;
  expanding: string | null;
}) {
  return (
    <Drawer opened onClose={onClose} position="right" size="xl" title={node.label}>
      <NodeDetails node={node} result={result} onExpand={onExpand} expanding={expanding} />
    </Drawer>
  );
}

/**
 * Merge an expansion into the graph on screen, instead of replacing it.
 *
 * Node identifiers are deterministic (`<entity>:<value>`, `source:<id>`), so merging is a union by id and a
 * value already on screen keeps its place rather than appearing twice. Two things are deliberately *not*
 * taken from the expansion:
 *
 * - **depth**, for a node already there: its distance from the original question is what the picture shows,
 *   and an expansion three clicks later would otherwise renumber it. A genuinely new node is placed one hop
 *   past the node it was expanded from.
 * - **provenance** (`seeds`): a node found by expanding from somewhere inherits that place's seeds, so the
 *   meeting points stay the ones the seeds actually reach and an expansion cannot invent a link.
 */
function merge(current: Investigation, added: Investigation, from: GraphNode): Investigation {
  const byId = new Map(current.nodes.map((n) => [n.id, n]));
  const inherited = from.seeds ?? [];
  for (const node of added.nodes) {
    const existing = byId.get(node.id);
    if (existing) {
      // Keep its place and its provenance; take what the expansion genuinely adds
      existing.rows = Math.max(existing.rows, node.rows);
      existing.referentials = [...new Set([...existing.referentials, ...node.referentials])];
      continue;
    }
    if (node.id === from.id) continue;
    byId.set(node.id, {
      ...node,
      depth: from.depth + Math.max(1, node.depth),
      seeds: [...new Set(inherited)].sort((a, b) => a - b),
      seed: false,
      seed_index: undefined,
    });
  }
  const key = (e: { source: string; target: string; kind: string; referential_id: string; label: string }) =>
    `${e.source}|${e.target}|${e.kind}|${e.referential_id}|${e.label}`;
  const edges = new Map(current.edges.map((e) => [key(e), e]));
  for (const edge of added.edges) {
    if (!byId.has(edge.source) || !byId.has(edge.target)) continue;
    const found = edges.get(key(edge));
    if (found) found.rows = Math.max(found.rows, edge.rows);
    else edges.set(key(edge), edge);
  }
  // Hits are keyed by referential and column, like the backend keys them
  const hits = new Map(current.hits.map((h) => [`${h.referential_id}:${h.column}`, h]));
  for (const hit of added.hits) {
    const id = `${hit.referential_id}:${hit.column}`;
    const found = hits.get(id);
    if (!found) {
      hits.set(id, hit);
      continue;
    }
    found.row_count = Math.max(found.row_count, hit.row_count);
    found.values = [...new Set([...found.values, ...hit.values])];
    const seen = new Set(found.rows.map((r) => JSON.stringify(r)));
    for (const row of hit.rows) {
      if (!seen.has(JSON.stringify(row))) found.rows.push(row);
    }
  }
  const nodes = [...byId.values()];
  return {
    ...current,
    nodes,
    edges: [...edges.values()],
    hits: [...hits.values()].sort((a, b) => b.row_count - a.row_count),
    stats: {
      ...current.stats,
      entities: nodes.filter((n) => n.kind === 'entity').length,
      sources: nodes.filter((n) => n.kind === 'source').length,
      edges: edges.size,
      rows: [...hits.values()].reduce((total, h) => total + h.row_count, 0),
    },
    warnings: [...new Set([...current.warnings, ...added.warnings])],
  };
}

/** Values several seeds reach, recomputed after a merge: an expansion can turn up a new one. */
function meetingPoints(result: Investigation): string[] {
  if (result.stats.seeds < 2) return [];
  return result.nodes.filter((n) => n.kind === 'entity' && (n.seeds ?? []).length > 1).map((n) => n.id);
}

/**
 * What ties the seeds together. A chain of evidence reads seed → shared value → seed, and every hop names the
 * referential whose rows justify it, so a link is never asserted without something to read behind it.
 */
function Connections({
  result,
  onOpen,
}: {
  result: Investigation;
  onOpen: (node: GraphNode | null) => void;
}) {
  const label = (index: number) => result.seeds[index]?.value ?? `seed ${index + 1}`;
  if (!result.connections.length) {
    return (
      <EmptyState icon={<IconLink size={28} />} title="No link found between these values">
        A link runs through a value they <b>both</b> reach — a technique, a product, an address — not through
        merely living in the same referential, which would be true of almost any pair. Two values only meet
        where their expansions overlap, so raising the depth is usually what finds one.
      </EmptyState>
    );
  }
  return (
    <Stack gap="sm">
      {result.connections.map((c: Connection, i) => (
        <Card key={i} withBorder padding="sm">
          <Group justify="space-between" mb={6} wrap="wrap" gap="xs">
            <Group gap="xs" wrap="wrap">
              <Badge variant="light">{label(c.seeds[0])}</Badge>
              <IconLink size={14} />
              <Badge variant="light">{label(c.seeds[1])}</Badge>
              <Text size="sm">
                meet on{' '}
                <Text span fw={700}>
                  {c.through.label}
                </Text>
                {c.through.entity ? (
                  <Text span c="dimmed">
                    {' '}
                    ({c.through.entity})
                  </Text>
                ) : null}
              </Text>
            </Group>
            <Group gap="xs">
              <Badge size="sm" variant="outline">
                {c.hops} hop{c.hops > 1 ? 's' : ''}
              </Badge>
              {c.meeting_points > 1 && (
                <Tooltip label="Other values tie this pair too; the shortest chain is shown">
                  <Badge size="sm" variant="light" color="gray">
                    {c.meeting_points} meeting points
                  </Badge>
                </Tooltip>
              )}
            </Group>
          </Group>
          {/* The chain itself: each step is clickable, so the rows behind a hop are one click away */}
          <Group gap={4} wrap="wrap">
            {c.path.map((step, j) => (
              <Group gap={4} key={step.id} wrap="nowrap">
                {j > 0 && (
                  <Tooltip label={step.via?.length ? `shares a row in ${step.via.join(', ')}` : 'shares a row'}>
                    <Text size="xs" c="dimmed">
                      →{step.via?.length ? ` ${step.via.join('/')} →` : ''}
                    </Text>
                  </Tooltip>
                )}
                <Badge
                  variant={step.id === c.through.id ? 'filled' : 'default'}
                  color={step.id === c.through.id ? 'yellow' : undefined}
                  style={{ cursor: 'pointer', textTransform: 'none' }}
                  onClick={() => onOpen(result.nodes.find((n) => n.id === step.id) ?? null)}
                >
                  {step.label}
                </Badge>
              </Group>
            ))}
          </Group>
        </Card>
      ))}
      {result.shared.length > 1 && (
        <div>
          <Text size="xs" c="dimmed" mb={4}>
            Every value several of them reach
          </Text>
          <Group gap={6}>
            {result.shared.map((node) => (
              <Tooltip key={node.id} label={`reached from ${node.seeds.map(label).join(' and ')}`}>
                <Badge
                  variant="light"
                  color="yellow"
                  style={{ cursor: 'pointer', textTransform: 'none' }}
                  onClick={() => onOpen(result.nodes.find((n) => n.id === node.id) ?? null)}
                >
                  {node.label}
                </Badge>
              </Tooltip>
            ))}
          </Group>
        </div>
      )}
    </Stack>
  );
}

/** Who a saved search is shared with. Sharing gives the question, never the data. */
function ShareModal({ search, onClose }: { search: SavedSearch; onClose: () => void }) {
  const qc = useQueryClient();
  const { data: targets, isLoading } = useQuery({ queryKey: ['share-targets'], queryFn: api.shareTargets });
  // Kept as {subject id: level} per kind, which is what the two pickers below edit
  const [users, setUsers] = useState<Record<string, 'read' | 'edit'>>(() =>
    Object.fromEntries((search.shares ?? []).filter((s) => s.user_id !== null).map((s) => [String(s.user_id), s.level])),
  );
  const [groups, setGroups] = useState<Record<string, 'read' | 'edit'>>(() =>
    Object.fromEntries((search.shares ?? []).filter((s) => s.group_id !== null).map((s) => [String(s.group_id), s.level])),
  );

  const save = useMutation({
    mutationFn: () => {
      const shares: SearchShare[] = [
        ...Object.entries(users).map(([id, level]) => ({ user_id: Number(id), group_id: null, level })),
        ...Object.entries(groups).map(([id, level]) => ({ user_id: null, group_id: Number(id), level })),
      ];
      return api.shareSearch(search.id, shares);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['searches'] });
      notifications.show({ title: 'Sharing updated', message: search.name, color: 'teal' });
      onClose();
    },
    onError: (e: Error) => notifications.show({ title: 'Failed', message: e.message, color: 'red' }),
  });

  /** Picking a subject keeps the level it already had; a new one starts at `read`. */
  const pick = (
    current: Record<string, 'read' | 'edit'>,
    set: (next: Record<string, 'read' | 'edit'>) => void,
  ) => (ids: string[]) => set(Object.fromEntries(ids.map((id) => [id, current[id] ?? 'read'])));

  const Levels = ({
    current,
    set,
    label,
  }: {
    current: Record<string, 'read' | 'edit'>;
    set: (next: Record<string, 'read' | 'edit'>) => void;
    label: string;
  }) => {
    const ids = Object.keys(current);
    if (!ids.length) return null;
    const every = (level: 'read' | 'edit') => set(Object.fromEntries(ids.map((id) => [id, level])));
    const mixed = new Set(Object.values(current)).size > 1;
    return (
      <Select
        label={label}
        data={[
          { value: 'read', label: 'Replay it' },
          { value: 'edit', label: 'Replay and change it' },
        ]}
        value={mixed ? null : (Object.values(current)[0] ?? 'read')}
        placeholder={mixed ? 'mixed — pick one to set them all' : undefined}
        allowDeselect={false}
        comboboxProps={{ withinPortal: false }}
        onChange={(level) => level && every(level as 'read' | 'edit')}
      />
    );
  };

  return (
    <Modal opened onClose={onClose} title={`Share “${search.name}”`} size="lg">
      <Stack gap="md">
        <Callout color="gray" variant="light" icon={<IconUsers size={16} />}>
          You are sharing the <b>question</b>, not its answer. Whoever replays it reads the catalogue with their
          own rights, so they see the referentials they may read — never the ones only you can.
        </Callout>
        {isLoading && <Loader size="sm" />}
        {targets?.scoped_to_groups && (
          <Text size="xs" c="dimmed">
            You can share with <b>your groups and the people in them</b>. Signing in does not give everyone the
            list of accounts, so someone outside your groups has to be reached through a group you share.
          </Text>
        )}
        <MultiSelect
          label="Accounts"
          placeholder={Object.keys(users).length ? undefined : 'nobody yet'}
          searchable
          clearable
          comboboxProps={{ withinPortal: false }}
          data={(targets?.users ?? []).map((u) => ({
            value: String(u.id),
            label: u.display_name ? `${u.username} (${u.display_name})` : u.username,
          }))}
          value={Object.keys(users)}
          onChange={pick(users, setUsers)}
        />
        <Levels current={users} set={setUsers} label="What those accounts may do" />
        <MultiSelect
          label="Groups"
          placeholder={Object.keys(groups).length ? undefined : 'no group yet'}
          searchable
          clearable
          comboboxProps={{ withinPortal: false }}
          data={(targets?.groups ?? []).map((g) => ({ value: String(g.id), label: g.name }))}
          value={Object.keys(groups)}
          onChange={pick(groups, setGroups)}
        />
        <Levels current={groups} set={setGroups} label="What those groups may do" />
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={() => save.mutate()} loading={save.isPending}>
            Save the sharing
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}

/** The searches this account keeps, and the ones colleagues shared with it. */
function SavedList({ onOpen }: { onOpen: (search: SavedSearch) => void }) {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ['searches'], queryFn: () => api.searches() });
  const [sharing, setSharing] = useState<SavedSearch | null>(null);
  const remove = useMutation({
    mutationFn: (id: number) => api.deleteSearch(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['searches'] });
      notifications.show({ title: 'Deleted', message: 'The saved search is gone', color: 'orange' });
    },
    onError: (e: Error) => notifications.show({ title: 'Failed', message: e.message, color: 'red' }),
  });

  if (isLoading) return <Loader />;
  const searches = data?.searches ?? [];
  if (!searches.length) {
    return (
      <EmptyState icon={<IconBookmark size={28} />} title="No saved search">
        Run an investigation and keep it: a saved search holds the question, so replaying it later reads the
        catalogue as it is then. You can share one with an account or a group.
      </EmptyState>
    );
  }
  return (
    <>
      <Stack gap={6}>
        {searches.map((s) => (
          <Card key={s.id} withBorder padding="sm">
            <Group justify="space-between" wrap="nowrap">
              <div style={{ minWidth: 0 }}>
                <Group gap="xs" wrap="nowrap">
                  <Text fw={600} size="sm" truncate>
                    {s.name}
                  </Text>
                  <Badge size="sm" variant="light">
                    {s.spec.entity ?? 'auto'}
                  </Badge>
                  {s.level !== 'owner' && (
                    <Tooltip label={`Shared by ${s.owner} — you may ${s.level === 'edit' ? 'change it' : 'replay it'}`}>
                      <Badge size="sm" color="grape" variant="light">
                        from {s.owner}
                      </Badge>
                    </Tooltip>
                  )}
                  {s.shared && s.level === 'owner' && (
                    <Badge size="sm" color="teal" variant="light">
                      shared
                    </Badge>
                  )}
                </Group>
                <Text size="xs" c="dimmed" lineClamp={1}>
                  {(s.spec.values?.length ? s.spec.values : [s.spec.value]).filter(Boolean).join(' + ')} · depth{' '}
                  {s.spec.depth ?? 1}
                  {s.description ? ` — ${s.description}` : ''}
                </Text>
                <Text size="xs" c="dimmed">
                  {s.last_run_at ? `last replayed ${fmtRelative(s.last_run_at)}` : 'never replayed'} · updated{' '}
                  {fmtDate(s.updated_at)}
                </Text>
              </div>
              <Group gap={4} wrap="nowrap">
                <Tooltip label="Replay it with your rights">
                  <ActionIcon variant="light" onClick={() => onOpen(s)} aria-label="Replay">
                    <IconPlayerPlay size={16} />
                  </ActionIcon>
                </Tooltip>
                {s.level !== 'read' && (
                  <Tooltip label="Share it">
                    <ActionIcon variant="subtle" onClick={() => setSharing(s)} aria-label="Share">
                      <IconShare size={16} />
                    </ActionIcon>
                  </Tooltip>
                )}
                {s.level === 'owner' && (
                  <Tooltip label="Delete it">
                    <ActionIcon variant="subtle" color="red" onClick={() => remove.mutate(s.id)} aria-label="Delete">
                      <IconTrash size={16} />
                    </ActionIcon>
                  </Tooltip>
                )}
              </Group>
            </Group>
          </Card>
        ))}
      </Stack>
      {sharing && <ShareModal search={sharing} onClose={() => setSharing(null)} />}
    </>
  );
}

function SaveModal({ spec, onClose }: { spec: InvestigationSpec; onClose: () => void }) {
  const qc = useQueryClient();
  const [name, setName] = useState((spec.values ?? []).join(' + ').slice(0, 120));
  const [description, setDescription] = useState('');
  const save = useMutation({
    mutationFn: () => api.createSearch({ name: name.trim(), description: description.trim(), spec, tags: [] }),
    onSuccess: (saved) => {
      qc.invalidateQueries({ queryKey: ['searches'] });
      notifications.show({ title: 'Saved', message: `“${saved.name}” — share it from the Saved tab`, color: 'teal' });
      onClose();
    },
    onError: (e: Error) => notifications.show({ title: 'Failed', message: e.message, color: 'red' }),
  });
  return (
    <Modal opened onClose={onClose} title="Keep this investigation">
      <Stack gap="sm">
        <TextInput label="Name" value={name} onChange={(e) => setName(e.currentTarget.value)} maxLength={120} />
        <Textarea
          label="What it is for"
          description="Optional — what a colleague needs to know to use it"
          value={description}
          onChange={(e) => setDescription(e.currentTarget.value)}
          maxLength={1000}
          autosize
          minRows={2}
        />
        <Text size="xs" c="dimmed">
          What is kept is the question — <b>{(spec.values ?? []).join(', ')}</b>, depth {spec.depth ?? 1} — not
          the result: replaying it reads the catalogue as it is then.
        </Text>
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={() => save.mutate()} loading={save.isPending} disabled={!name.trim()}>
            Save
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}

export default function InvestigatePage() {
  const [params, setParams] = useSearchParams();
  const [spec, setSpec] = useState<InvestigationSpec>(() => specFromUrl(params));
  const [result, setResult] = useState<Investigation | null>(null);
  const [node, setNode] = useState<GraphNode | null>(null);
  const [saving, setSaving] = useState(false);
  const [tab, setTab] = useState<string | null>('graph');
  // Expansions made since the last search, and the meeting points they turned up. The chains between seeds
  // are computed by the backend, so a link found by expanding is reported and a fresh search offered rather
  // than a chain being worked out here from a different rule.
  const [expansions, setExpansions] = useState(0);
  const [newlyShared, setNewlyShared] = useState<string[]>([]);
  const [full, setFull] = useState(false);

  const { data: catalogue } = useQuery({ queryKey: ['entity-catalogue'], queryFn: api.entityCatalogue });
  const { data: refs } = useQuery({ queryKey: ['referentials'], queryFn: api.referentials });

  const run = useMutation({
    mutationFn: (body: InvestigationSpec) => api.investigate(body),
    onSuccess: (answer) => {
      setResult(answer);
      setNode(null);
      setExpansions(0);
      setNewlyShared([]);
      // With several values, what ties them together is what was asked for
      setTab(answer.connections.length ? 'links' : 'graph');
    },
    onError: (e: Error) => notifications.show({ title: 'Cannot investigate that', message: e.message, color: 'orange' }),
  });
  /**
   * Grow the graph from a value already on screen, in place.
   *
   * A value whose type the graph knows is looked up exactly (`match: exact`), which is the cheap, precise
   * path. A value picked out of a row is anything at all, so it takes the ordinary `auto` — including the
   * text pass, which is what makes following a product name or a ticket possible.
   */
  const expand = useMutation({
    mutationFn: ({ value, entity }: { value: string; entity: string | null; from: GraphNode }) =>
      api.investigate({
        values: [value],
        ...(entity ? { entity, match: 'exact' as MatchMode } : { match: 'auto' as MatchMode }),
        depth: 1,
        scope: spec.scope,
        follow: spec.follow,
        rows_per_lookup: spec.rows_per_lookup,
      }),
    onSuccess: (added, { value, from }) => {
      setResult((current) => {
        if (!current) return added;
        const before = new Set(meetingPoints(current));
        const next = merge(current, added, from);
        const fresh = meetingPoints(next).filter((id) => !before.has(id));
        setExpansions((n) => n + 1);
        setNewlyShared((ids) => [...new Set([...ids, ...fresh])]);
        const grew = next.nodes.length - current.nodes.length;
        notifications.show({
          title: grew ? `${grew} new node(s)` : 'Nothing new',
          message: grew
            ? `Expanded from ${value}${fresh.length ? ` — ${fresh.length} new shared value(s)` : ''}`
            : `${value} leads only to what is already on screen`,
          color: grew ? 'teal' : 'gray',
        });
        return next;
      });
    },
    onError: (e: Error) => notifications.show({ title: 'Cannot expand', message: e.message, color: 'orange' }),
  });
  const doExpand = (value: string, entity: string | null, from: GraphNode) =>
    expand.mutate({ value, entity, from });

  const replay = useMutation({
    mutationFn: (id: number) => api.runSearch(id),
    onSuccess: (answer) => {
      setResult(answer);
      setSpec({ ...EMPTY, ...answer.spec, values: answer.spec.values });
      setParams(urlFromSpec({ ...answer.spec, values: answer.spec.values }), { replace: true });
      setNode(null);
      setExpansions(0);
      setNewlyShared([]);
      setTab(answer.connections.length ? 'links' : 'graph');
    },
    onError: (e: Error) => notifications.show({ title: 'Failed', message: e.message, color: 'red' }),
  });

  // A value in the URL runs on arrival, so an investigation can be linked to and reopened
  useEffect(() => {
    const fromUrl = specFromUrl(params);
    if (fromUrl.values?.length && !result && !run.isPending) {
      setSpec(fromUrl);
      run.mutate(fromUrl);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const submit = () => {
    if (!(spec.values ?? []).length) return;
    setParams(urlFromSpec(spec), { replace: true });
    run.mutate(spec);
  };

  const entityOptions = useMemo(
    () =>
      (catalogue?.types ?? [])
        .filter((t) => t.columns.length)
        .map((t) => ({ value: t.name, label: `${t.label} — ${t.columns.length} column(s)` })),
    [catalogue],
  );
  const scopeOptions = useMemo(() => {
    const categories = [...new Set((refs ?? []).map((r) => r.category))].sort();
    return [
      { group: 'Categories', items: categories.map((c) => ({ value: `category:${c}`, label: `${c} (category)` })) },
      {
        group: 'Referentials',
        items: (refs ?? []).map((r) => ({ value: r.id, label: `${r.name} (${r.id})` })),
      },
    ];
  }, [refs]);

  const stats = result?.stats;

  return (
    <Stack gap="lg">
      <div>
        <Title order={2}>Investigate</Title>
        <Text c="dimmed" size="sm">
          Start from anything — a CVE, an address, a network, a domain, a hash, a CPE, or plain text like a
          vendor or a product — and follow it across the catalogue. An identifier is recognised for what it is
          and looked up wherever that kind of value lives, whatever each source calls the column; anything else
          is searched as text, and the identifiers in the matching rows are the way into the graph. Add{' '}
          <b>several values</b> and the question becomes what ties them together: the value they both reach,
          and the shortest chain of evidence through it. Every relation says which row it comes from.
        </Text>
      </div>

      <Card withBorder padding="md">
        <Stack gap="sm">
          <Group align="flex-end" gap="sm" wrap="wrap">
            <TagsInput
              label="Values"
              description="An identifier or plain text. Add several to find what ties them together."
              placeholder={spec.values?.length ? 'another value…' : 'CVE-2024-3400, 10.0.0.0/8, Palo Alto…'}
              value={spec.values ?? []}
              onChange={(values) => setSpec({ ...spec, values })}
              clearable
              maxTags={8}
              style={{ flex: 1, minWidth: 300 }}
              leftSection={<IconSearch size={16} />}
            />
            <Select
              label="Read it as"
              description="Only to override what was recognised"
              placeholder="automatic"
              data={entityOptions}
              value={spec.entity ?? null}
              onChange={(entity) => setSpec({ ...spec, entity })}
              clearable
              searchable
              w={210}
            />
            <NumberInput
              label="Depth"
              description="Hops to follow"
              min={1}
              max={catalogue?.limits.max_depth ?? 3}
              value={spec.depth ?? 1}
              onChange={(v) => setSpec({ ...spec, depth: Number(v) || 1 })}
              w={110}
            />
            <Button
              leftSection={<IconSearch size={16} />}
              onClick={submit}
              loading={run.isPending || replay.isPending}
              disabled={!(spec.values ?? []).length}
            >
              Investigate
            </Button>
          </Group>
          <Group gap="sm" wrap="wrap" align="flex-end">
            <MultiSelect
              label="Only these referentials"
              placeholder={spec.scope?.length ? undefined : 'the whole catalogue you may read'}
              data={scopeOptions}
              value={spec.scope ?? []}
              onChange={(scope) => setSpec({ ...spec, scope })}
              searchable
              clearable
              style={{ flex: 1, minWidth: 260 }}
            />
            <MultiSelect
              label="Only follow these kinds"
              placeholder={spec.follow?.length ? undefined : 'every kind'}
              data={entityOptions}
              value={spec.follow ?? []}
              onChange={(follow) => setSpec({ ...spec, follow })}
              searchable
              clearable
              style={{ flex: 1, minWidth: 220 }}
            />
            <Select
              label="How to look"
              data={[
                { value: 'auto', label: 'Exact where possible, and text' },
                { value: 'exact', label: 'Exact lookups only' },
                { value: 'text', label: 'Text search only' },
              ]}
              value={spec.match ?? 'auto'}
              onChange={(match) => setSpec({ ...spec, match: (match as MatchMode) ?? 'auto' })}
              allowDeselect={false}
              w={230}
            />
            <NumberInput
              label="Rows per lookup"
              min={1}
              max={catalogue?.limits.max_rows_per_lookup ?? 50}
              value={spec.rows_per_lookup ?? 5}
              onChange={(v) => setSpec({ ...spec, rows_per_lookup: Number(v) || 5 })}
              w={140}
            />
          </Group>
        </Stack>
      </Card>

      {(run.isPending || replay.isPending) && <Loader />}

      {result && stats && (
        <>
          <SimpleGrid cols={{ base: 2, sm: 4 }}>
            <StatCard
              label="Referentials matched"
              value={`${stats.referentials_matched} / ${stats.referentials_searched}`}
              icon={<IconTable size={20} />}
              color={stats.referentials_matched ? 'teal' : 'gray'}
            />
            <StatCard label="Rows of evidence" value={fmtNumber(stats.rows)} icon={<IconTable size={20} />} color="blue" />
            <StatCard label="Values found" value={stats.entities} icon={<IconGraph size={20} />} color="grape" />
            {stats.seeds > 1 ? (
              <StatCard
                label="Links between your values"
                value={stats.connections}
                hint={stats.shared ? `${stats.shared} shared value(s)` : undefined}
                icon={<IconLink size={20} />}
                color={stats.connections ? 'yellow' : 'gray'}
              />
            ) : (
              <StatCard label="Relations" value={stats.edges} icon={<IconGraph size={20} />} color="indigo" />
            )}
          </SimpleGrid>

          {expansions > 0 && (
            <Callout
              color={newlyShared.length ? 'yellow' : 'gray'}
              variant="light"
              icon={<IconPlus size={18} />}
              title={`Grown by hand: ${expansions} expansion${expansions > 1 ? 's' : ''}`}
            >
              <Group justify="space-between" wrap="wrap" gap="sm">
                <Text size="sm">
                  {newlyShared.length
                    ? `Expanding turned up ${newlyShared.length} value(s) that several of your seeds reach. The
                       chains between them are worked out at search time, so search again to read them.`
                    : `What you expanded is in the graph and in the evidence. The links between your values
                       are the ones found at search time.`}
                </Text>
                {newlyShared.length > 0 && (
                  <Button size="xs" variant="light" onClick={submit} loading={run.isPending}>
                    Search again
                  </Button>
                )}
              </Group>
            </Callout>
          )}

          {!!result.warnings.length && (
            <Callout color="orange" variant="light" icon={<IconAlertTriangle size={18} />} title="Partial answer">
              <Stack gap={2}>
                {result.warnings.map((w, i) => (
                  <Text key={i} size="sm">
                    {w}
                  </Text>
                ))}
              </Stack>
            </Callout>
          )}

          <Group justify="space-between" wrap="wrap" gap="sm">
            <Group gap="xs" wrap="wrap">
              {result.seeds.map((seed) => (
                <Group gap={4} key={seed.index} wrap="nowrap">
                  <Badge size="lg" variant="filled" color={seed.entity ? undefined : 'dark'}>
                    {seed.entity ?? 'text'}
                  </Badge>
                  <Text fw={700}>{seed.value}</Text>
                </Group>
              ))}
              {result.seeds.some((seed) => !seed.entity) && (
                <Text size="xs" c="dimmed">
                  what is not an identifier was searched as text
                </Text>
              )}
              {result.search && (
                <Badge variant="light" color="grape">
                  from “{result.search.name}”
                </Badge>
              )}
            </Group>
            <Button variant="default" leftSection={<IconDeviceFloppy size={16} />} onClick={() => setSaving(true)}>
              Keep this search
            </Button>
          </Group>

          {stats.referentials_matched === 0 ? (
            <EmptyState icon={<IconSearch size={28} />} title="Nothing in your catalogue carries that value">
              {stats.referentials_searched} referential(s) were searched
              {result.seed.searched.exact && result.seed.entity ? ` for ${result.seed.entity} values` : ''}
              {result.seed.searched.exact && result.seed.searched.text ? ' and as text' : ''}
              {!result.seed.searched.exact ? ' as text' : ''}. The value may be absent, or held in a referential
              you may not read.
            </EmptyState>
          ) : (
            <Tabs value={tab} onChange={setTab}>
              <Tabs.List>
                <Tabs.Tab value="graph" leftSection={<IconGraph size={14} />}>
                  Relations ({stats.edges})
                </Tabs.Tab>
                {stats.seeds > 1 && (
                  <Tabs.Tab
                    value="links"
                    leftSection={<IconLink size={14} />}
                    rightSection={
                      stats.connections ? (
                        <Badge size="xs" color="yellow">
                          {stats.connections}
                        </Badge>
                      ) : null
                    }
                  >
                    Between your values
                  </Tabs.Tab>
                )}
                <Tabs.Tab value="evidence" leftSection={<IconTable size={14} />}>
                  Evidence ({result.hits.length})
                </Tabs.Tab>
              </Tabs.List>
              <Tabs.Panel value="graph" pt="md">
                <RelationGraph
                  nodes={result.nodes}
                  edges={result.edges}
                  selected={node?.id ?? null}
                  onSelect={setNode}
                  seedCount={stats.seeds}
                  onFullscreen={setFull}
                />
              </Tabs.Panel>
              {stats.seeds > 1 && (
                <Tabs.Panel value="links" pt="md">
                  <Connections result={result} onOpen={setNode} />
                </Tabs.Panel>
              )}
              <Tabs.Panel value="evidence" pt="md">
                <Table fz="sm" withTableBorder highlightOnHover>
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th>Referential</Table.Th>
                      <Table.Th>Category</Table.Th>
                      <Table.Th>Matched on</Table.Th>
                      <Table.Th>How</Table.Th>
                      <Table.Th>Values matched</Table.Th>
                      <Table.Th ta="right">Rows</Table.Th>
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {result.hits.map((hit) => (
                      <Table.Tr
                        key={`${hit.referential_id}:${hit.column}`}
                        style={{ cursor: 'pointer' }}
                        onClick={() =>
                          setNode(result.nodes.find((n) => n.kind === 'source' && n.value === hit.referential_id) ?? null)
                        }
                      >
                        <Table.Td>
                          <Anchor component={Link} to={`/r/${hit.referential_id}`} onClick={(e) => e.stopPropagation()}>
                            {hit.referential_name}
                          </Anchor>
                        </Table.Td>
                        <Table.Td>{hit.category}</Table.Td>
                        <Table.Td>
                          <Badge size="sm" variant="outline">
                            {hit.how === 'exact' ? hit.column : 'any text column'}
                          </Badge>
                        </Table.Td>
                        <Table.Td>
                          <Badge size="sm" variant="light" color={hit.how === 'exact' ? 'teal' : 'gray'}>
                            {hit.how === 'exact' ? `exact ${hit.entity}` : 'mentions it'}
                          </Badge>
                        </Table.Td>
                        <Table.Td>
                          <Text size="xs" lineClamp={1}>
                            {hit.values.join(', ')}
                          </Text>
                        </Table.Td>
                        <Table.Td ta="right" className="tabular">
                          {fmtNumber(hit.row_count)}
                        </Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              </Tabs.Panel>
            </Tabs>
          )}
        </>
      )}

      <Card withBorder padding="md">
        <Group justify="space-between" mb="sm">
          <Group gap="xs">
            <IconBookmark size={18} />
            <Text fw={600}>Saved searches</Text>
          </Group>
          <Text size="xs" c="dimmed">
            Yours, and the ones shared with you
          </Text>
        </Group>
        <SavedList onOpen={(s) => replay.mutate(s.id)} />
      </Card>

      {/* Enlarged: the graph takes the window and the values of whatever is selected sit beside it, rather
          than in a drawer that would cover the picture being described */}
      {result && (
        <Modal
          opened={full}
          onClose={() => setFull(false)}
          fullScreen
          padding="md"
          title={`Relations — ${result.seeds.map((seed) => seed.value).join(' + ')}`}
          styles={{ body: { height: 'calc(100dvh - 60px)', paddingBottom: 0 } }}
        >
          <Group align="stretch" gap="md" wrap="nowrap" h="100%">
            <div style={{ flex: 1, minWidth: 0, display: 'flex' }}>
              <RelationGraph
                nodes={result.nodes}
                edges={result.edges}
                selected={node?.id ?? null}
                onSelect={setNode}
                seedCount={stats?.seeds ?? 1}
                fullscreen
                onFullscreen={setFull}
              />
            </div>
            <div
              style={{
                width: 460,
                flexShrink: 0,
                borderLeft: '1px solid var(--mantine-color-default-border)',
                paddingLeft: 'var(--mantine-spacing-md)',
                display: 'flex',
                flexDirection: 'column',
                minHeight: 0,
              }}
            >
              {node ? (
                <>
                  <Group justify="space-between" mb="xs" wrap="nowrap">
                    <Text fw={700} truncate>
                      {node.label}
                    </Text>
                    <ActionIcon variant="subtle" onClick={() => setNode(null)} aria-label="Clear the selection">
                      <IconX size={16} />
                    </ActionIcon>
                  </Group>
                  <ScrollArea style={{ flex: 1 }} offsetScrollbars>
                    <NodeDetails
                      node={node}
                      result={result}
                      onExpand={doExpand}
                      expanding={expand.isPending ? (expand.variables?.value ?? null) : null}
                    />
                  </ScrollArea>
                </>
              ) : (
                <EmptyState icon={<IconGraph size={28} />} title="Pick a node">
                  Click anything in the graph to read the rows behind it here, and to carry on from one of their
                  values without leaving this view.
                </EmptyState>
              )}
            </div>
          </Group>
        </Modal>
      )}

      {/* Beside the page, a drawer is right: there is nothing underneath it worth seeing */}
      {node && result && !full && (
        <NodePanel
          node={node}
          result={result}
          onClose={() => setNode(null)}
          onExpand={doExpand}
          expanding={expand.isPending ? (expand.variables?.value ?? null) : null}
        />
      )}
      {saving && <SaveModal spec={spec} onClose={() => setSaving(false)} />}
    </Stack>
  );
}
