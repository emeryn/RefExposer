import { ActionIcon, Autocomplete, Badge, Button, Card, Group, NumberInput, Select, SimpleGrid, Stack, TagsInput, Text, TextInput, Tooltip } from '@mantine/core';
import { IconPlus, IconShieldCheck, IconTrash } from '@tabler/icons-react';
import type { QualityCheckType, QualityRule, QualitySummary } from '../api/types';

export const QUALITY_STATUS = {
  ok: { label: 'Quality OK', color: 'teal' },
  warning: { label: 'Quality warning', color: 'orange' },
  failed: { label: 'Quality failed', color: 'red' },
} as const;

/** Score and status of the data quality (nothing when no rule is checked). */
export function QualityBadge({ quality, size = 'sm' }: { quality?: QualitySummary | { score: number | null; status: keyof typeof QUALITY_STATUS } | null; size?: 'xs' | 'sm' | 'md' }) {
  if (!quality || quality.score == null) return null;
  const s = QUALITY_STATUS[quality.status] ?? QUALITY_STATUS.ok;
  const failed = 'failed' in quality ? quality.failed : [];
  return (
    <Tooltip label={failed.length ? `Rules not met: ${failed.join(', ')}` : s.label} multiline w={360} withArrow>
      <Badge size={size} variant="light" color={s.color} leftSection={<IconShieldCheck size={12} />}>
        {Math.round(quality.score * 10) / 10}
      </Badge>
    </Tooltip>
  );
}

const CHECKS: { value: QualityCheckType; label: string }[] = [
  { value: 'not_null', label: 'Filled (not empty)' },
  { value: 'unique', label: 'Unique' },
  { value: 'format', label: 'Known format' },
  { value: 'pattern', label: 'Regular expression' },
  { value: 'values', label: 'Allowed values' },
  { value: 'range', label: 'Range (min / max)' },
  { value: 'length', label: 'Length' },
  { value: 'expression', label: 'SQL condition' },
];

const FORMATS = [
  { value: 'ipv4', label: 'IPv4 address' },
  { value: 'ipv6', label: 'IPv6 address' },
  { value: 'ip', label: 'IP address (v4 or v6)' },
  { value: 'cidr', label: 'Network (CIDR)' },
  { value: 'domain', label: 'Domain name' },
  { value: 'email', label: 'E-mail address' },
  { value: 'url', label: 'URL' },
  { value: 'md5', label: 'MD5' },
  { value: 'sha1', label: 'SHA-1' },
  { value: 'sha256', label: 'SHA-256' },
  { value: 'sha512', label: 'SHA-512' },
  { value: 'cve', label: 'CVE identifier' },
  { value: 'uuid', label: 'UUID' },
  { value: 'mac', label: 'MAC address' },
  { value: 'asn', label: 'AS number' },
  { value: 'country', label: 'Country code (ISO 3166 alpha-2)' },
];

const num = (v: string | number) => (v === '' ? null : Number(v));

/** Editor of the quality rules of a definition. */
export function QualityRulesEditor({ rules, columns, onChange }: { rules: QualityRule[]; columns: string[]; onChange: (rules: QualityRule[]) => void }) {
  const update = (i: number, patch: Partial<QualityRule>) => onChange(rules.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  return (
    <Stack gap="xs">
      <div>
        <Text size="sm" fw={500}>
          Data quality rules
        </Text>
        <Text size="xs" c="dimmed">
          Checked on every new version (score and status in the Quality tab). <b>Warning</b>: the version is published and marked;{' '}
          <b>error</b>: it is rejected and the published one is kept. Empty values only fail the “filled” check. The completeness of the
          key is checked automatically.
        </Text>
      </div>
      {rules.map((r, i) => (
        <Card key={i} withBorder padding="xs">
          <Group gap="xs" align="flex-end" wrap="wrap">
            <Select size="xs" label="Check" data={CHECKS} value={r.check} onChange={(v) => v && update(i, { check: v as QualityCheckType })} w={170} allowDeselect={false} />
            {r.check !== 'expression' && (
              <Autocomplete size="xs" label="Column" data={columns} value={r.column ?? ''} onChange={(v) => update(i, { column: v || null })} w={170} />
            )}
            {r.check === 'format' && (
              <Select size="xs" label="Format" data={FORMATS} value={r.format ?? null} onChange={(v) => update(i, { format: v })} w={200} searchable />
            )}
            {r.check === 'pattern' && (
              <TextInput size="xs" label="Regular expression (whole value)" placeholder="^[A-Z]{3}-\d+$" value={r.pattern ?? ''} onChange={(e) => update(i, { pattern: e.currentTarget.value })} style={{ flex: 1 }} ff="monospace" />
            )}
            {r.check === 'values' && (
              <TagsInput size="xs" label="Allowed values" value={(r.values ?? []).map(String)} onChange={(v) => update(i, { values: v })} style={{ flex: 1 }} />
            )}
            {r.check === 'range' && (
              <>
                <TextInput size="xs" label="Min" placeholder="0 or 2020-01-01" value={r.min ?? ''} onChange={(e) => { const v = e.currentTarget.value; update(i, { min: v === '' ? null : isNaN(Number(v)) ? v : Number(v) }); }} w={130} />
                <TextInput size="xs" label="Max" value={r.max ?? ''} onChange={(e) => { const v = e.currentTarget.value; update(i, { max: v === '' ? null : isNaN(Number(v)) ? v : Number(v) }); }} w={130} />
              </>
            )}
            {r.check === 'length' && (
              <>
                <NumberInput size="xs" label="Min length" min={0} value={r.min_length ?? ''} onChange={(v) => update(i, { min_length: num(v) })} w={100} />
                <NumberInput size="xs" label="Max length" min={0} value={r.max_length ?? ''} onChange={(v) => update(i, { max_length: num(v) })} w={100} />
              </>
            )}
            {r.check === 'expression' && (
              <TextInput size="xs" label="SQL condition true for every row" placeholder="first_seen <= last_seen" value={r.expression ?? ''} onChange={(e) => update(i, { expression: e.currentTarget.value })} style={{ flex: 1 }} ff="monospace" />
            )}
            <NumberInput size="xs" label="Threshold (%)" min={0} max={100} value={r.threshold ?? 100} onChange={(v) => update(i, { threshold: Number(v) || 0 })} w={100} />
            <Select
              size="xs"
              label="Severity"
              data={[
                { value: 'warning', label: 'Warning' },
                { value: 'error', label: 'Error (reject)' },
              ]}
              value={r.severity ?? 'warning'}
              onChange={(v) => update(i, { severity: (v ?? 'warning') as 'warning' | 'error' })}
              w={130}
              allowDeselect={false}
            />
            <ActionIcon variant="subtle" color="red" mb={4} onClick={() => onChange(rules.filter((_, j) => j !== i))} aria-label="Remove the rule">
              <IconTrash size={14} />
            </ActionIcon>
          </Group>
          <TextInput size="xs" mt={6} placeholder="Label (optional)" value={r.name ?? ''} onChange={(e) => update(i, { name: e.currentTarget.value || null })} variant="unstyled" />
        </Card>
      ))}
      <Button size="compact-sm" variant="light" leftSection={<IconPlus size={14} />} w="fit-content" onClick={() => onChange([...rules, { check: 'not_null', column: null, threshold: 100, severity: 'warning' }])}>
        Add a rule
      </Button>
      {rules.length === 0 && (
        <SimpleGrid cols={1}>
          <Text size="xs" c="dimmed">
            Examples: <i>ip</i> is a valid IPv4 address (99.9 %), <i>sha256</i> is a valid SHA-256, <i>cve</i> is filled and unique,
            <i> first_seen &lt;= last_seen</i>.
          </Text>
        </SimpleGrid>
      )}
    </Stack>
  );
}
