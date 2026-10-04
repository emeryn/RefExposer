import { Anchor, Badge, Code, Group, Highlight, Text } from '@mantine/core';
import { fmtDecimal, fmtNumber } from '../lib/format';

const URL_RE = /^https?:\/\/\S+$/;

export function isNumericType(type: string) {
  return /^(TINYINT|SMALLINT|INTEGER|BIGINT|HUGEINT|U\w*INT|FLOAT|DOUBLE|DECIMAL)/i.test(type);
}

/** Compact rendering used in grid cells. */
export function CellValue({ value, name, highlight }: { value: unknown; name?: string; highlight?: string }) {
  if (value === null || value === undefined) return <span className="null">∅</span>;
  if (typeof value === 'boolean')
    return (
      <Badge size="xs" variant="light" color={value ? 'teal' : 'gray'}>
        {value ? 'yes' : 'no'}
      </Badge>
    );
  if (typeof value === 'number') {
    // Identifiers and years are displayed verbatim (no thousands separator)
    const raw = name && /(year|^id$|_id$|code|number|num)/i.test(name);
    if (raw) return <>{String(value)}</>;
    return <>{Number.isInteger(value) ? fmtNumber(value) : fmtDecimal(value)}</>;
  }
  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="null">[ ]</span>;
    const shown = value.slice(0, 3);
    return (
      <Group gap={4} wrap="nowrap">
        {shown.map((v, i) => (
          <Badge key={i} size="xs" variant="default" radius="sm" tt="none" style={{ maxWidth: 180 }}>
            {typeof v === 'object' ? JSON.stringify(v) : String(v)}
          </Badge>
        ))}
        {value.length > 3 && (
          <Text size="xs" c="dimmed">
            +{value.length - 3}
          </Text>
        )}
      </Group>
    );
  }
  if (typeof value === 'object') return <Code fz={11}>{JSON.stringify(value)}</Code>;
  const s = String(value);
  if (s === '') return <span className="null">(empty)</span>;
  if (highlight && s.toLowerCase().includes(highlight.toLowerCase()))
    return (
      <Highlight component="span" highlight={highlight} inherit>
        {s}
      </Highlight>
    );
  return <>{s}</>;
}

/** Full rendering used in the row detail drawer. */
export function FullValue({ value, name }: { value: unknown; name?: string }) {
  if (value === null || value === undefined) return <span className="null">∅ (null)</span>;
  if (Array.isArray(value)) {
    if (!value.length) return <span className="null">empty list</span>;
    return (
      <Group gap={4}>
        {value.map((v, i) =>
          typeof v === 'string' && URL_RE.test(v) ? (
            <Anchor key={i} href={v} target="_blank" size="sm" style={{ wordBreak: 'break-all', display: 'block', width: '100%' }}>
              {v}
            </Anchor>
          ) : (
            <Badge key={i} variant="default" radius="sm" tt="none" size="md">
              {typeof v === 'object' ? JSON.stringify(v) : String(v)}
            </Badge>
          ),
        )}
      </Group>
    );
  }
  if (typeof value === 'object')
    return (
      <Code block fz={12}>
        {JSON.stringify(value, null, 2)}
      </Code>
    );
  if (typeof value === 'string' && URL_RE.test(value))
    return (
      <Anchor href={value} target="_blank" size="sm" style={{ wordBreak: 'break-all' }}>
        {value}
      </Anchor>
    );
  return (
    <Text size="sm" style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
      <CellValue value={value} name={name} />
    </Text>
  );
}
