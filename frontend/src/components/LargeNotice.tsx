import { Alert, Badge, Group, Text } from '@mantine/core';
import { IconBolt } from '@tabler/icons-react';
import type { ReferentialDetail } from '../api/types';
import { fmtNumber } from '../lib/format';

/** Columns on which lookups are fast (physical sort order, sorted copies). */
export function fastColumns(r: ReferentialDetail): string[] {
  return [...new Set([...(r.sort_by ?? []).slice(0, 1), ...(r.indexes ?? [])])];
}

export function LargeNotice({ r }: { r: ReferentialDetail }) {
  if (!r.large) return null;
  const fast = fastColumns(r);
  return (
    <Alert color="indigo" variant="light" icon={<IconBolt size={18} />} p="sm">
      <Group gap={6}>
        <Text size="sm">
          Large referential ({fmtNumber(r.row_count)} rows). Instant lookups on
        </Text>
        {fast.length ? (
          fast.map((c) => (
            <Badge key={c} variant="filled" size="sm" tt="none" leftSection={<IconBolt size={10} />}>
              {c}
            </Badge>
          ))
        ) : (
          <Text size="sm" fw={600}>
            no column (configure storage.sort_by / indexes)
          </Text>
        )}
      </Group>
      <Text size="xs" c="dimmed" mt={4}>
        Fast operators: equals, one of, starts with (case-sensitive), ranges. Filters on other columns work but read the whole
        volume (time limited) and the total is then not computed. The text search becomes a prefix search.
      </Text>
    </Alert>
  );
}
