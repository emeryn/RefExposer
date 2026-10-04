import { ActionIcon, Badge, Box, Button, Divider, Drawer, Group, ScrollArea, Stack, Text, Tooltip } from '@mantine/core';
import { IconArrowDown, IconArrowUp, IconArrowsSort, IconFilter, IconKey } from '@tabler/icons-react';
import type { Column, Row } from '../api/types';
import { CellValue, FullValue, isNumericType } from './CellValue';
import { CopyIcon } from './Common';

export function parseSort(sort: string | null | undefined): { column: string; desc: boolean } | null {
  if (!sort) return null;
  const first = sort.split(',')[0];
  return { column: first.replace(/^[-+]/, ''), desc: first.startsWith('-') };
}

/** Cycle: none -> asc -> desc -> none */
export function nextSort(current: string | null | undefined, column: string): string | null {
  const s = parseSort(current);
  if (!s || s.column !== column) return column;
  if (!s.desc) return `-${column}`;
  return null;
}

export function DataGrid({
  columns,
  rows,
  sort,
  onSort,
  onRowClick,
  offset = 0,
  keyColumn,
  maxHeight,
  highlight,
}: {
  columns: Column[];
  rows: Row[];
  sort?: string | null;
  onSort?: (column: string) => void;
  onRowClick?: (row: Row, index: number) => void;
  offset?: number;
  keyColumn?: string | null;
  maxHeight?: number | string;
  highlight?: string;
}) {
  const s = parseSort(sort);
  return (
    <div className="grid-wrap" style={maxHeight ? { maxHeight, minHeight: 0 } : undefined}>
      <table className="grid">
        <thead>
          <tr>
            <th className="rownum">#</th>
            {columns.map((c) => {
              const active = s?.column === c.name;
              const Icon = active ? (s!.desc ? IconArrowDown : IconArrowUp) : IconArrowsSort;
              return (
                <th
                  key={c.name}
                  className={onSort ? 'sortable' : undefined}
                  onClick={onSort ? () => onSort(c.name) : undefined}
                  title={`${c.name} · ${c.type}`}
                >
                  <Group gap={4} wrap="nowrap">
                    {keyColumn === c.name && <IconKey size={12} color="var(--mantine-color-yellow-6)" />}
                    <span>{c.name}</span>
                    {onSort && <Icon size={13} style={{ opacity: active ? 1 : 0.3 }} />}
                  </Group>
                  <Text fz={10} c="dimmed" fw={400} ff="monospace">
                    {c.type.toLowerCase()}
                  </Text>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} onClick={onRowClick ? () => onRowClick(row, i) : undefined}>
              <td className="rownum">{offset + i + 1}</td>
              {columns.map((c) => (
                <td key={c.name} className={isNumericType(c.type) ? 'num' : undefined}>
                  <CellValue value={row[c.name]} name={c.name} highlight={highlight || undefined} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function RowDrawer({
  row,
  columns,
  title,
  keyColumn,
  onClose,
  onFilter,
}: {
  row: Row | null;
  columns: Column[];
  title?: string;
  keyColumn?: string | null;
  onClose: () => void;
  onFilter?: (column: string, value: string) => void;
}) {
  const heading = row && keyColumn && row[keyColumn] != null ? String(row[keyColumn]) : title ?? 'Details';
  return (
    <Drawer
      opened={row != null}
      onClose={onClose}
      position="right"
      size="xl"
      title={
        <Group gap="xs">
          <Text fw={700}>{heading}</Text>
          {row && <CopyIcon value={JSON.stringify(row, null, 2)} label="Copy as JSON" />}
        </Group>
      }
      scrollAreaComponent={ScrollArea.Autosize}
    >
      {row && (
        <Stack gap={0}>
          {columns.map((c, i) => {
            const v = row[c.name];
            const filterable = onFilter && v != null && typeof v !== 'object';
            return (
              <Box key={c.name}>
                {i > 0 && <Divider my={8} />}
                <Group justify="space-between" align="flex-start" wrap="nowrap" gap="xs">
                  <Group gap={6} mb={4}>
                    <Text size="xs" fw={700} c="dimmed">
                      {c.name}
                    </Text>
                    <Badge size="xs" variant="outline" color="gray" tt="lowercase" ff="monospace">
                      {c.type}
                    </Badge>
                  </Group>
                  <Group gap={2} wrap="nowrap">
                    {v != null && <CopyIcon value={typeof v === 'object' ? JSON.stringify(v) : String(v)} />}
                    {filterable && (
                      <Tooltip label="Filter on this value">
                        <ActionIcon size="sm" variant="subtle" color="gray" onClick={() => onFilter!(c.name, String(v))}>
                          <IconFilter size={14} />
                        </ActionIcon>
                      </Tooltip>
                    )}
                  </Group>
                </Group>
                <FullValue value={v} name={c.name} />
              </Box>
            );
          })}
          <Button variant="default" mt="lg" onClick={onClose}>
            Close
          </Button>
        </Stack>
      )}
    </Drawer>
  );
}
