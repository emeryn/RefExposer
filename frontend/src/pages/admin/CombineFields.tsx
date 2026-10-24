import { Alert, Autocomplete, Badge, Code, Group, MultiSelect, SegmentedControl, Stack, Switch, Text, TextInput } from '@mantine/core';
import { IconArrowUp } from '@tabler/icons-react';
import type { CombineConfig, DefinitionConfig } from '../../api/types';
import { fmtNumber } from '../../lib/format';

export type CombineCandidate = { id: string; name: string; confidential: boolean; row_count: number | null; columns: string[] };

type Props = {
  draft: DefinitionConfig;
  set: <K extends keyof DefinitionConfig>(k: K, v: DefinitionConfig[K]) => void;
  referentials: CombineCandidate[];
  /** sources: which referentials are combined (Source step) · settings: how they are combined (Reading step) */
  part: 'sources' | 'settings';
};

const EMPTY: CombineConfig = { mode: 'union' };

/** Fields of a unified referential: the referentials it is built from, and how they are combined. */
export default function CombineFields({ draft, set, referentials, part }: Props) {
  const combine = draft.combine ?? EMPTY;
  const ids = (draft.source.referentials ?? []).filter(Boolean);
  const enrich = combine.mode === 'enrich';
  const byId = new Map(referentials.map((r) => [r.id, r]));
  const columnsOf = (id: string) => byId.get(id)?.columns ?? [];

  const setCombine = (patch: Partial<CombineConfig>) => set('combine', { ...combine, ...patch });
  // The referentials live in the source; `combine` only says how they are put together
  const setIds = (next: string[]) => set('source', { ...draft.source, referentials: next });
  const promote = (id: string) => setIds([id, ...ids.filter((x) => x !== id)]);
  const setColumns = (id: string, columns: string[]) =>
    setCombine({ columns: { ...(combine.columns ?? {}), [id]: columns } });

  const options = referentials
    .filter((r) => r.id !== draft.id)
    .map((r) => ({
      value: r.id,
      label: `${r.name} (${r.id})${r.row_count ? ` · ${fmtNumber(r.row_count)} rows` : ''}${r.confidential ? ' · confidential' : ''}`,
    }));
  const base = ids[0];
  const confidentialSource = ids.find((id) => byId.get(id)?.confidential);

  if (part === 'sources') {
    return (
      <Stack gap="sm">
        <Text size="sm" c="dimmed">
          Build one referential from the published data of several others: put their rows together (<b>union</b>, e.g. the same
          kind of list maintained by two teams), or add the columns of the others to the rows of the first one
          (<b>enrichment</b>). It is rebuilt automatically whenever one of its sources publishes a new version.
        </Text>
        <SegmentedControl
          value={combine.mode}
          onChange={(v) => setCombine({ mode: v as CombineConfig['mode'] })}
          data={[
            { value: 'union', label: 'Union (add the rows)' },
            { value: 'enrich', label: 'Enrichment (add the columns)' },
          ]}
          w="fit-content"
        />
        <MultiSelect
          label="Referentials"
          description={enrich ? 'The first one gives the rows; the others add their columns.' : 'Their rows are put together; columns are matched by name.'}
          placeholder={options.length ? 'Choose the referentials' : 'No referential published yet'}
          data={options}
          value={ids}
          onChange={setIds}
          searchable
          required
        />
        {ids.length > 0 && (
          <Group gap={6}>
            {ids.map((id, i) => (
              <Badge
                key={id}
                variant={enrich && i === 0 ? 'filled' : 'light'}
                tt="none"
                size="lg"
                rightSection={
                  enrich && i > 0 ? (
                    <IconArrowUp size={12} style={{ cursor: 'pointer' }} onClick={() => promote(id)} aria-label={`Use ${id} as the base`} />
                  ) : undefined
                }
              >
                {i + 1}. {id}
                {enrich && i === 0 ? ' (rows)' : ''}
              </Badge>
            ))}
            {enrich && ids.length > 1 && (
              <Text size="xs" c="dimmed">
                Use the arrow to make a referential the one that gives the rows.
              </Text>
            )}
          </Group>
        )}
        {confidentialSource && !draft.confidential && (
          <Alert color="orange" p="sm">
            <Text size="sm">
              <Code>{confidentialSource}</Code> is confidential: tick <b>Confidential</b> at the Description step, otherwise the
              referential is refused (its data would be published unencrypted).
            </Text>
          </Alert>
        )}
      </Stack>
    );
  }

  return (
    <Stack gap="sm">
      <Text size="sm" fw={500}>
        {enrich ? 'Enrichment' : 'Union'} of {ids.join(', ') || 'no referential yet'}
      </Text>
      {enrich ? (
        <>
          <Autocomplete
            label="Join column"
            description={`Value shared by the referentials. Empty: the key of this referential${draft.key ? ` (${draft.key})` : ''}.`}
            placeholder={draft.key || 'e.g. hostname'}
            data={columnsOf(base)}
            value={combine.on ?? ''}
            onChange={(v) => setCombine({ on: v || null })}
          />
          {ids.slice(1).map((id) => (
            <Group key={id} align="flex-end" gap="xs" wrap="nowrap">
              <Autocomplete
                size="xs"
                style={{ flex: 1 }}
                label={`Column of ${id} to join on`}
                description="Only when it is not named like the join column."
                placeholder={combine.on || draft.key || 'same name'}
                data={columnsOf(id)}
                value={combine.join_on?.[id] ?? ''}
                onChange={(v) => setCombine({ join_on: { ...(combine.join_on ?? {}), [id]: v } })}
              />
              <MultiSelect
                size="xs"
                style={{ flex: 1 }}
                label={`Columns taken from ${id}`}
                description="All of them when empty."
                data={columnsOf(id)}
                value={combine.columns?.[id] ?? []}
                onChange={(v) => setColumns(id, v)}
                searchable
                clearable
              />
            </Group>
          ))}
          <Switch
            size="xs"
            label="Name the added columns <referential>_<column>"
            description="Switch it off to keep their own names; two referentials must then not share a column name."
            checked={combine.prefix !== false}
            onChange={(e) => setCombine({ prefix: e.currentTarget.checked })}
          />
        </>
      ) : (
        <>
          <TextInput
            label="Column recording the source (optional)"
            description="Added to every row with the identifier of the referential it comes from."
            placeholder="_source"
            value={combine.source_column ?? ''}
            onChange={(e) => setCombine({ source_column: e.currentTarget.value || null })}
            w={320}
          />
          {ids.map((id) => (
            <MultiSelect
              key={id}
              size="xs"
              label={`Columns taken from ${id}`}
              description="All of them when empty."
              data={columnsOf(id)}
              value={combine.columns?.[id] ?? []}
              onChange={(v) => setColumns(id, v)}
              searchable
              clearable
            />
          ))}
          <Text size="xs" c="dimmed">
            Identical rows are kept as they are: set a key and <b>Deltas</b> is not involved here — to keep one row per key, add a
            SQL transformation such as <Code>{'SELECT DISTINCT ON (code) * FROM {source}'}</Code>.
          </Text>
        </>
      )}
    </Stack>
  );
}
