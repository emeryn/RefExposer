import {
  Accordion,
  Alert,
  Badge,
  Button,
  Card,
  Grid,
  Group,
  Kbd,
  Menu,
  ScrollArea,
  Stack,
  Text,
  Title,
  Tooltip,
  UnstyledButton,
  useComputedColorScheme,
} from '@mantine/core';
import { sql as sqlLang, PostgreSQL } from '@codemirror/lang-sql';
import CodeMirror from '@uiw/react-codemirror';
import { IconBook, IconDownload, IconHistory, IconPlayerPlay, IconTable } from '@tabler/icons-react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api/client';
import type { Column, Row, SqlResult } from '../api/types';
import { DataGrid, RowDrawer } from '../components/DataGrid';
import { fmtNumber } from '../lib/format';

const HISTORY_KEY = 'refexposer-sql-history';
const DRAFT_KEY = 'refexposer-sql-draft';

function load<T>(key: string, fallback: T): T {
  try {
    const v = localStorage.getItem(key);
    return v ? (JSON.parse(v) as T) : fallback;
  } catch {
    return fallback;
  }
}
function save(key: string, value: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* storage unavailable */
  }
}

const EXAMPLES: { title: string; tables: string[]; sql: string }[] = [
  {
    title: 'Most likely exploited vulnerabilities (KEV × EPSS)',
    tables: ['cisa_kev', 'epss'],
    sql: `SELECT k.cveID, k.vendorProject, k.product, k.dateAdded,
       e.epss, e.percentile
FROM cisa_kev k
JOIN epss e ON e.cve = k.cveID
ORDER BY e.epss DESC
LIMIT 50`,
  },
  {
    title: 'Most frequent CWEs in the KEV catalog',
    tables: ['cisa_kev', 'cwe'],
    sql: `SELECT c AS cwe_id, w.Name, count(*) AS vulnerabilites
FROM (SELECT unnest(cwes) AS c FROM cisa_kev)
LEFT JOIN cwe w ON w.cwe_id = c
GROUP BY ALL
ORDER BY vulnerabilites DESC
LIMIT 20`,
  },
  {
    title: 'Recent critical NVD CVEs not yet in KEV',
    tables: ['nvd_cve', 'cisa_kev'],
    sql: `SELECT n.cve_id, n.published, n.cvss3_score, n.description
FROM nvd_cve n
ANTI JOIN cisa_kev k ON k.cveID = n.cve_id
WHERE n.cvss3_severity = 'CRITICAL'
ORDER BY n.published DESC
LIMIT 100`,
  },
  {
    title: 'CVE breakdown by severity and month',
    tables: ['nvd_cve'],
    sql: `PIVOT (
  SELECT strftime(published, '%Y-%m') AS mois, coalesce(cvss3_severity, 'N/A') AS severite
  FROM nvd_cve
) ON severite USING count(*)
ORDER BY mois DESC`,
  },
  {
    title: 'Best rated movies (IMDb, > 100,000 votes)',
    tables: ['imdb_titles'],
    sql: `SELECT title, start_year, runtime_minutes, genres, rating, votes
FROM imdb_titles
WHERE title_type = 'movie' AND votes > 100000
ORDER BY rating DESC, votes DESC
LIMIT 50`,
  },
  {
    title: 'Average IMDb rating by genre',
    tables: ['imdb_titles'],
    sql: `SELECT genre, count(*) AS films, round(avg(rating), 2) AS note_moyenne
FROM (SELECT unnest(genres) AS genre, rating FROM imdb_titles
      WHERE title_type = 'movie' AND votes >= 1000)
GROUP BY genre
ORDER BY note_moyenne DESC`,
  },
  {
    title: 'Countries by region and sub-region',
    tables: ['iso_countries'],
    sql: `SELECT region, "sub-region", count(*) AS pays, string_agg("alpha-2", ', ' ORDER BY "alpha-2") AS codes
FROM iso_countries
GROUP BY ALL
ORDER BY region, pays DESC`,
  },
  {
    title: 'Internationalized TLDs (IDN)',
    tables: ['iana_tlds'],
    sql: `SELECT tld FROM iana_tlds WHERE idn ORDER BY tld`,
  },
];

function toCsv(columns: Column[], rows: unknown[][]): string {
  const esc = (v: unknown) => {
    if (v == null) return '';
    const s = typeof v === 'object' ? JSON.stringify(v) : String(v);
    return /[",\n;]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return [columns.map((c) => esc(c.name)).join(','), ...rows.map((r) => r.map(esc).join(','))].join('\n');
}

function download(content: string, filename: string, type: string) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

/** Make column names unique so that rows can be represented as records. */
function asRecords(result: SqlResult): { columns: Column[]; rows: Row[] } {
  const seen = new Map<string, number>();
  const columns = result.columns.map((c) => {
    const n = seen.get(c.name) ?? 0;
    seen.set(c.name, n + 1);
    return n ? { ...c, name: `${c.name}_${n + 1}` } : c;
  });
  const rows = result.rows.map((r) => Object.fromEntries(columns.map((c, i) => [c.name, r[i]])));
  return { columns, rows };
}

export default function SqlConsole() {
  const scheme = useComputedColorScheme('light');
  const { data: tables } = useQuery({ queryKey: ['sql-schema'], queryFn: api.sqlSchema });
  const [code, setCode] = useState<string>(() => load(DRAFT_KEY, ''));
  const [history, setHistory] = useState<string[]>(() => load(HISTORY_KEY, []));
  const [selected, setSelected] = useState<Row | null>(null);

  const available = useMemo(() => new Set(tables?.map((t) => t.table)), [tables]);
  const examples = EXAMPLES.filter((e) => e.tables.every((t) => available.has(t)));
  const initial = !code && tables?.length ? `SELECT *\nFROM ${tables[0].table}\nLIMIT 100` : code;

  const run = useMutation({
    mutationFn: (sql: string) => api.sql(sql),
    onSuccess: (_, sql) => {
      const next = [sql, ...history.filter((h) => h !== sql)].slice(0, 25);
      setHistory(next);
      save(HISTORY_KEY, next);
    },
  });

  const extensions = useMemo(() => {
    const schema = Object.fromEntries(
      (tables ?? []).flatMap((t) => [
        [t.table, t.columns.map((c) => c.name)],
        ...Object.values(t.indexes ?? {}).map((v) => [v, t.columns.map((c) => c.name)]),
      ]),
    );
    return [sqlLang({ dialect: PostgreSQL, schema, upperCaseKeywords: true })];
  }, [tables]);

  const execute = () => {
    const sql = (code || initial).trim();
    if (sql) run.mutate(sql);
  };
  const setEditor = (sql: string) => {
    setCode(sql);
    save(DRAFT_KEY, sql);
  };

  const result = run.data ? asRecords(run.data) : null;

  return (
    <Stack gap="md">
      <Group justify="space-between" align="flex-end">
        <div>
          <Title order={2}>Console SQL</Title>
          <Text c="dimmed" size="sm">
            Read-only queries (DuckDB dialect) over all referentials, joins included.
          </Text>
        </div>
        <Group gap="xs">
          <Menu position="bottom-end" width={420} shadow="md">
            <Menu.Target>
              <Button variant="default" leftSection={<IconBook size={16} />} disabled={!examples.length}>
                Exemples
              </Button>
            </Menu.Target>
            <Menu.Dropdown>
              {examples.map((e) => (
                <Menu.Item key={e.title} onClick={() => setEditor(e.sql)}>
                  {e.title}
                </Menu.Item>
              ))}
            </Menu.Dropdown>
          </Menu>
          <Menu position="bottom-end" width={480} shadow="md">
            <Menu.Target>
              <Button variant="default" leftSection={<IconHistory size={16} />} disabled={!history.length}>
                Historique
              </Button>
            </Menu.Target>
            <Menu.Dropdown>
              <ScrollArea.Autosize mah={400}>
                {history.map((h, i) => (
                  <Menu.Item key={i} onClick={() => setEditor(h)}>
                    <Text size="xs" ff="monospace" lineClamp={2}>
                      {h}
                    </Text>
                  </Menu.Item>
                ))}
              </ScrollArea.Autosize>
            </Menu.Dropdown>
          </Menu>
        </Group>
      </Group>

      <Grid gutter="md">
        <Grid.Col span={{ base: 12, md: 3 }}>
          <Card padding="xs" h="100%">
            <Text size="xs" fw={700} c="dimmed" tt="uppercase" px={6} py={4}>
              Tables
            </Text>
            <ScrollArea.Autosize mah={420}>
              <Accordion variant="filled" chevronPosition="left" multiple>
                {tables?.map((t) => (
                  <Accordion.Item key={t.table} value={t.table}>
                    <Accordion.Control py={0} icon={<IconTable size={14} />}>
                      <Group justify="space-between" wrap="nowrap" gap={4}>
                        <Tooltip label={t.name}>
                          <Text size="sm" ff="monospace" truncate>
                            {t.table}
                          </Text>
                        </Tooltip>
                        <Badge size="xs" variant="default">
                          {fmtNumber(t.row_count)}
                        </Badge>
                      </Group>
                    </Accordion.Control>
                    <Accordion.Panel>
                      <Stack gap={1}>
                        <UnstyledButton onClick={() => setEditor(`SELECT *\nFROM ${t.table}\nLIMIT 100`)}>
                          <Text size="xs" c="indigo">
                            ▸ SELECT * FROM {t.table}
                          </Text>
                        </UnstyledButton>
                        <UnstyledButton onClick={() => setEditor(`SUMMARIZE ${t.table}`)}>
                          <Text size="xs" c="indigo">
                            ▸ SUMMARIZE {t.table}
                          </Text>
                        </UnstyledButton>
                        {Object.entries(t.indexes ?? {}).map(([col, view]) => (
                          <UnstyledButton key={view} onClick={() => setEditor(`SELECT *\nFROM ${view}\nWHERE ${col} = ''\nLIMIT 100`)}>
                            <Text size="xs" c="indigo">
                              ⚡ {view} (sorted on {col})
                            </Text>
                          </UnstyledButton>
                        ))}
                        {t.columns.map((c) => (
                          <Group key={c.name} justify="space-between" wrap="nowrap" gap={4}>
                            <Text size="xs" ff="monospace" truncate>
                              {c.name}
                            </Text>
                            <Text size="10px" c="dimmed" ff="monospace" truncate maw={90}>
                              {c.type.toLowerCase()}
                            </Text>
                          </Group>
                        ))}
                      </Stack>
                    </Accordion.Panel>
                  </Accordion.Item>
                ))}
              </Accordion>
            </ScrollArea.Autosize>
          </Card>
        </Grid.Col>
        <Grid.Col span={{ base: 12, md: 9 }}>
          <Card padding={0} style={{ overflow: 'hidden' }}>
            <div
              onKeyDownCapture={(e) => {
                if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
                  e.preventDefault();
                  execute();
                }
              }}
            >
              <CodeMirror
                value={initial}
                onChange={setEditor}
                height="260px"
                theme={scheme === 'dark' ? 'dark' : 'light'}
                extensions={extensions}
                basicSetup={{ highlightActiveLine: true, autocompletion: true }}
              />
            </div>
            <Group justify="space-between" px="sm" py={8} style={{ borderTop: '1px solid var(--mantine-color-default-border)' }}>
              <Text size="xs" c="dimmed">
                <Kbd size="xs">Ctrl</Kbd> + <Kbd size="xs">Enter</Kbd> to run · <Kbd size="xs">Ctrl</Kbd> + <Kbd size="xs">Espace</Kbd>{' '}
                for autocompletion
              </Text>
              <Button leftSection={<IconPlayerPlay size={16} />} onClick={execute} loading={run.isPending}>
                Run
              </Button>
            </Group>
          </Card>
        </Grid.Col>
      </Grid>

      {run.error && (
        <Alert color="red" title="Error">
          <Text size="sm" ff="monospace" style={{ whiteSpace: 'pre-wrap' }}>
            {(run.error as Error).message}
          </Text>
        </Alert>
      )}

      {run.data && result && (
        <Stack gap="xs">
          <Group justify="space-between">
            <Text size="sm" c="dimmed" className="tabular">
              {fmtNumber(run.data.row_count)} row{run.data.row_count > 1 ? 's' : ''} · {run.data.elapsed_ms} ms
              {run.data.truncated && (
                <Badge ml="xs" color="orange" variant="light" size="sm">
                  truncated result
                </Badge>
              )}
            </Text>
            <Group gap="xs">
              <Button
                size="xs"
                variant="default"
                leftSection={<IconDownload size={14} />}
                onClick={() => download(toCsv(run.data!.columns, run.data!.rows), 'requete.csv', 'text/csv')}
              >
                CSV
              </Button>
              <Button
                size="xs"
                variant="default"
                leftSection={<IconDownload size={14} />}
                onClick={() => download(JSON.stringify(result.rows, null, 2), 'requete.json', 'application/json')}
              >
                JSON
              </Button>
            </Group>
          </Group>
          <DataGrid columns={result.columns} rows={result.rows} onRowClick={setSelected} />
          <RowDrawer row={selected} columns={result.columns} onClose={() => setSelected(null)} />
        </Stack>
      )}

      {!tables?.length && (
        <Alert color="gray">
          No table available: import at least one referential from the <Link to="/">dashboard</Link>.
        </Alert>
      )}
    </Stack>
  );
}
