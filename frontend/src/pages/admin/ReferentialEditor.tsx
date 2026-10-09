import {
  Accordion,
  ActionIcon,
  Alert,
  Anchor,
  Autocomplete,
  Badge,
  Button,
  Card,
  Checkbox,
  Code,
  Grid,
  Group,
  JsonInput,
  Loader,
  MultiSelect,
  NumberInput,
  SegmentedControl,
  Select,
  SimpleGrid,
  Stack,
  Stepper,
  Switch,
  TagsInput,
  Text,
  Textarea,
  TextInput,
  Title,
  useComputedColorScheme,
} from '@mantine/core';
import { Dropzone } from '@mantine/dropzone';
import '@mantine/dropzone/styles.css';
import { notifications } from '@mantine/notifications';
import { sql as sqlLang, PostgreSQL } from '@codemirror/lang-sql';
import CodeMirror from '@uiw/react-codemirror';
import {
  IconAlertTriangle,
  IconArrowLeft,
  IconCheck,
  IconCloudDownload,
  IconFile,
  IconPlus,
  IconRefresh,
  IconTrash,
  IconUpload,
  IconWand,
} from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api } from '../../api/client';
import type { DefinitionConfig, PreviewResult, Row, UploadResult } from '../../api/types';
import { DataGrid } from '../../components/DataGrid';
import { SecretInput } from '../../components/SecretInput';
import { describeCron, fmtBytes, fmtNumber } from '../../lib/format';

type Mode = 'http' | 'upload' | 'local' | 'git';

const EMPTY: DefinitionConfig = {
  id: '',
  name: '',
  description: '',
  category: '',
  tags: [],
  homepage: null,
  license: null,
  owner: null,
  source: { type: 'http', urls: [''], headers: {}, extract: null },
  format: '',
  options: {},
  transform: null,
  key: null,
  search_columns: [],
  schedule: '0 6 * * *',
  max_age: null,
  validation: { min_rows: 1, max_drop_pct: null, unique_key: false },
  enabled: true,
  downloads: [],
  storage: {
    sort_by: [],
    indexes: [],
    row_group_size: 122880,
    keep_raw: true,
    keep_previous: true,
    profile: 'full',
    track_changes: true,
    check_key: true,
  },
};

const FORMATS = [
  { value: 'csv', label: 'CSV' },
  { value: 'tsv', label: 'TSV (tabs)' },
  { value: 'json', label: 'JSON' },
  { value: 'jsonl', label: 'JSON Lines' },
  { value: 'txt', label: 'Text (one value per line)' },
  { value: 'parquet', label: 'Parquet' },
  { value: 'xlsx', label: 'Excel (.xlsx)' },
  { value: 'xml', label: 'XML (repeated record elements)' },
  { value: 'bloom', label: 'Bloom filter (DCSO format, e.g. CIRCL hashlookup)' },
  { value: 'mmdb', label: 'MaxMind DB (.mmdb: GeoIP2 / GeoLite2, DB-IP, IPinfo…)' },
  { value: 'sqlite', label: 'SQLite database (e.g. NIST NSRL)' },
];

const SCHEDULES = [
  { value: '', label: 'Manual only' },
  { value: '0 * * * *', label: 'Every hour' },
  { value: '0 */6 * * *', label: 'Every 6 hours' },
  { value: '0 6 * * *', label: 'Every day at 6:00' },
  { value: '0 6 * * 1', label: 'Every Monday at 6:00' },
  { value: '0 6 1 * *', label: 'On the 1st of the month at 6:00' },
  { value: 'custom', label: 'Custom (cron)…' },
];

const DOWNLOAD_FORMATS = [
  { value: 'csv', label: 'CSV' },
  { value: 'csv.gz', label: 'Compressed CSV' },
  { value: 'xlsx', label: 'Excel' },
  { value: 'json', label: 'JSON' },
  { value: 'jsonl', label: 'JSON Lines' },
];

function slugify(s: string) {
  return s
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 63);
}

function humanize(filename: string) {
  const base = filename.split('/').pop()!.replace(/\.(csv|tsv|json|jsonl|ndjson|txt|parquet|xlsx|gz|zip)$/gi, '').replace(/\.(csv|tsv|json)$/i, '');
  const s = base.replace(/[_\-.]+/g, ' ').trim();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** Remove empty values before sending the definition. */
function cleanConfig(c: DefinitionConfig, mode: Mode): Partial<DefinitionConfig> {
  const source =
    mode === 'http'
      ? {
          type: 'http' as const,
          urls: c.source.urls.map((u) => u.trim()).filter(Boolean),
          headers: c.source.headers,
          basic_auth: c.source.basic_auth || null,
          extract: c.source.extract || null,
        }
      : mode === 'git'
        ? {
            type: 'git' as const,
            urls: [],
            headers: {},
            extract: null,
            repository: (c.source.repository ?? '').trim(),
            ref: c.source.ref?.trim() || null,
            path: c.source.path?.trim() || null,
            token: c.source.token || null,
            username: c.source.username?.trim() || null,
          }
        : { type: 'local' as const, urls: [], headers: {}, extract: null, path: c.source.path || 'raw/**/*' };
  return {
    ...c,
    homepage: c.homepage || null,
    license: c.license || null,
    owner: c.owner || null,
    transform: c.transform?.trim() ? c.transform : null,
    schedule: c.schedule || null,
    max_age: c.max_age || null,
    category: c.category || 'General',
    source,
    // Deltas: Bloom filters and SQLite databases downloaded from a URL only
    incremental:
      c.incremental && mode === 'http' && (c.format === 'bloom' || c.format === 'sqlite')
        ? { ...c.incremental, urls: c.incremental.urls.map((u) => u.trim()).filter(Boolean) }
        : null,
  };
}

function OptionField({ label, description, children }: { label: string; description?: string; children: ReactNode }) {
  return (
    <div>
      <Text size="sm" fw={500}>
        {label}
      </Text>
      {description && (
        <Text size="xs" c="dimmed" mb={4}>
          {description}
        </Text>
      )}
      {children}
    </div>
  );
}

export default function ReferentialEditor() {
  const { id } = useParams();
  const editing = !!id;
  const navigate = useNavigate();
  const qc = useQueryClient();
  const scheme = useComputedColorScheme('light');

  const { data: existing, isLoading: loadingExisting } = useQuery({ queryKey: ['definition', id], queryFn: () => api.definition(id!), enabled: editing });
  const { data: meta } = useQuery({ queryKey: ['definition-meta'], queryFn: api.definitionMeta });

  const [step, setStep] = useState(0);
  const [draft, setDraft] = useState<DefinitionConfig>(EMPTY);
  const [mode, setMode] = useState<Mode>('http');
  const [upload, setUpload] = useState<UploadResult | null>(null);
  const [uploading, setUploading] = useState(false);
  const [preview, setPreview] = useState<PreviewResult | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [optionsText, setOptionsText] = useState('{}');
  const [idTouched, setIdTouched] = useState(false);
  const [customCron, setCustomCron] = useState(false);
  const [pull, setPull] = useState(true);
  const [groupIds, setGroupIds] = useState<string[]>([]);
  const [headers, setHeaders] = useState<{ k: string; v: string }[]>([]);

  useEffect(() => {
    if (!existing) return;
    const c = {
      ...EMPTY,
      ...existing.config,
      source: { ...EMPTY.source, ...existing.config.source },
      storage: { ...EMPTY.storage, ...existing.config.storage },
    };
    if (!c.source.urls.length) c.source.urls = [''];
    setDraft(c);
    setMode(c.source.type === 'local' ? 'local' : c.source.type === 'git' ? 'git' : 'http');
    setOptionsText(JSON.stringify(c.options ?? {}, null, 2));
    setHeaders(Object.entries(c.source.headers ?? {}).map(([k, v]) => ({ k, v: String(v) })));
    setIdTouched(true);
    setCustomCron(!!c.schedule && !SCHEDULES.some((s) => s.value === c.schedule));
  }, [existing]);

  const set = <K extends keyof DefinitionConfig>(k: K, v: DefinitionConfig[K]) => setDraft((d) => ({ ...d, [k]: v }));
  const setOptions = (opts: Record<string, unknown>) => {
    set('options', opts);
    setOptionsText(JSON.stringify(opts, null, 2));
  };
  const setOption = (k: string, v: unknown) => {
    const next = { ...draft.options };
    if (v === undefined || v === null || v === '') delete next[k];
    else next[k] = v;
    setOptions(next);
  };

  const previewSource = () => {
    if (mode === 'upload') return { type: 'upload', upload_id: upload?.upload_id };
    if (mode === 'local') return { type: 'local', path: draft.source.path };
    if (mode === 'git')
      return {
        type: 'git',
        repository: (draft.source.repository ?? '').trim(),
        ref: draft.source.ref?.trim() || null,
        path: draft.source.path?.trim() || null,
        token: draft.source.token || null,
        username: draft.source.username?.trim() || null,
      };
    return {
      type: 'http',
      urls: draft.source.urls.map((u) => u.trim()).filter(Boolean),
      headers: Object.fromEntries(headers.filter((h) => h.k.trim()).map((h) => [h.k.trim(), h.v])),
      basic_auth: draft.source.basic_auth || null,
      extract: draft.source.extract || null,
    };
  };

  const runPreview = async (opts: { refresh?: boolean; format?: string | null; next?: boolean } = {}) => {
    setPreviewing(true);
    setPreviewError(null);
    try {
      const res = await api.previewSource({
        source: previewSource(),
        format: opts.format === undefined ? draft.format || null : opts.format,
        options: draft.options,
        transform: draft.transform?.trim() ? draft.transform : null,
        // Editing: masked credentials (never shown again) are replaced by the stored values
        referential_id: id ?? null,
        refresh: !!opts.refresh,
      });
      setPreview(res);
      const applied = res.applied_options ?? {};
      if (Object.keys(applied).length) {
        // Options the analysis had to add to read the source (e.g. maximum_depth): keep them in the definition
        setDraft((d) => ({ ...d, options: { ...d.options, ...applied } }));
        setOptionsText(JSON.stringify({ ...draft.options, ...applied }, null, 2));
      }
      setDraft((d) => {
        const next = { ...d, format: res.format ?? d.format };
        if (!editing && !d.name && res.files[0]) next.name = humanize(res.files[0].name);
        if (!editing && !idTouched && next.name) next.id = slugify(next.name);
        return next;
      });
      if (opts.next) setStep(1);
    } catch (e) {
      setPreviewError((e as Error).message);
    } finally {
      setPreviewing(false);
    }
  };

  const onDrop = async (files: File[]) => {
    setUploading(true);
    setPreview(null);
    try {
      setUpload(await api.uploadFiles(files));
    } catch (e) {
      notifications.show({ title: 'Upload failed', message: (e as Error).message, color: 'red' });
    } finally {
      setUploading(false);
    }
  };

  const save = useMutation({
    mutationFn: () => {
      const config = cleanConfig({ ...draft, source: { ...draft.source, headers: Object.fromEntries(headers.filter((h) => h.k.trim()).map((h) => [h.k.trim(), h.v])) } }, mode);
      const upload_id = mode === 'upload' ? upload?.upload_id ?? null : null;
      return editing
        ? api.updateDefinition(id!, { config, upload_id, pull })
        : api.createDefinition({ config, upload_id, pull, grant_group_ids: groupIds.map(Number) });
    },
    onSuccess: (d) => {
      notifications.show({
        title: editing ? 'Referential updated' : 'Referential created',
        message: d.run ? 'Import started' : d.config.name,
        color: 'teal',
      });
      qc.invalidateQueries();
      navigate(`/r/${d.id}`);
    },
    onError: (e: Error) => notifications.show({ title: 'Cannot save', message: e.message, color: 'red', autoClose: 10000 }),
  });

  const columns = preview?.columns ?? [];
  const columnOptions = columns.map((c) => ({ value: c.name, label: `${c.name} · ${c.type.toLowerCase()}` }));
  const previewRows = useMemo(
    () => (preview?.rows ?? []).map((r) => Object.fromEntries(columns.map((c, i) => [c.name, r[i]])) as Row),
    [preview, columns],
  );
  const sourceReady =
    mode === 'http'
      ? draft.source.urls.some((u) => /^https?:\/\/.+/.test(u.trim()))
      : mode === 'git'
        ? /^https?:\/\/.+/.test((draft.source.repository ?? '').trim()) && !!draft.source.path?.trim()
        : mode === 'upload'
          ? !!upload
          : true;
  const previewOk = !!preview && !preview.error;
  const keyCandidates = columns.filter((c) => /(^id$|_id$|code|key|cle|clé|^cve|^iso)/i.test(c.name)).map((c) => c.name);

  if (editing && loadingExisting) return <Loader />;
  if (editing && existing?.kind === 'internal')
    return (
      <Alert color="grape" title="Internal referential">
        This referential is managed in RefExposer: edit its{' '}
        <Anchor component={Link} to={`/internal/${existing.id}/schema`}>
          columns
        </Anchor>{' '}
        or its{' '}
        <Anchor component={Link} to={`/r/${existing.id}/edit`}>
          rows
        </Anchor>
        .
      </Alert>
    );
  if (editing && existing && !existing.editable)
    return (
      <Alert color="orange" title="Referential defined in a YAML file">
        This referential is defined in <Code>config/{existing.config_file}</Code>: edit that file then reload the configuration.
      </Alert>
    );

  const fmt = draft.format;
  return (
    <Stack gap="lg">
      <Anchor component={Link} to="/admin/referentials" size="sm" c="dimmed">
        <IconArrowLeft size={14} style={{ verticalAlign: -2 }} /> Referentials
      </Anchor>
      <div>
        <Title order={2}>{editing ? `Configure “${existing?.config.name ?? id}”` : 'New referential'}</Title>
        <Text c="dimmed" size="sm">
          {editing ? 'Changes apply at the next update.' : 'Give a source, check how it is read, describe it, then publish.'}
        </Text>
      </div>

      <Stepper active={step} onStepClick={setStep} allowNextStepsSelect={editing || previewOk} size="sm">
        {/* ------------------------------------------------------------ 1. Source */}
        <Stepper.Step label="Source" description="URL or file">
          <Card padding="lg" mt="md">
            <Stack>
              <SegmentedControl
                value={mode}
                onChange={(v) => {
                  setMode(v as Mode);
                  setPreview(null);
                }}
                data={[
                  { value: 'http', label: 'From a URL' },
                  { value: 'git', label: 'Git repository' },
                  { value: 'upload', label: editing ? 'Replace with a file' : 'Upload a file' },
                  ...(editing && existing?.config.source.type === 'local' ? [{ value: 'local', label: 'Current files' }] : []),
                ]}
                w="fit-content"
              />
              {mode === 'http' && (
                <Stack gap="xs">
                  <Text size="sm" c="dimmed">
                    The file is downloaded at each update (conditional requests: nothing is downloaded again if it has not changed).
                    <Code>.gz</Code>, <Code>.zip</Code> and <Code>.tar.gz</Code> archives are unpacked automatically. Several URLs
                    are combined (or joined with an SQL transformation).
                  </Text>
                  {draft.source.urls.map((u, i) => (
                    <Group key={i} gap="xs" wrap="nowrap">
                      <TextInput
                        style={{ flex: 1 }}
                        placeholder="https://example.org/data.csv"
                        value={u}
                        leftSection={<Text size="xs" c="dimmed">{i}</Text>}
                        onChange={(e) => {
                          const v = e.currentTarget.value;
                          set('source', { ...draft.source, urls: draft.source.urls.map((x, j) => (j === i ? v : x)) });
                        }}
                      />
                      {draft.source.urls.length > 1 && (
                        <ActionIcon variant="subtle" color="red" onClick={() => set('source', { ...draft.source, urls: draft.source.urls.filter((_, j) => j !== i) })}>
                          <IconTrash size={16} />
                        </ActionIcon>
                      )}
                    </Group>
                  ))}
                  <Button size="compact-sm" variant="subtle" w="fit-content" leftSection={<IconPlus size={14} />} onClick={() => set('source', { ...draft.source, urls: [...draft.source.urls, ''] })}>
                    Add a URL
                  </Button>
                  <Accordion variant="contained">
                    <Accordion.Item value="adv">
                      <Accordion.Control>
                        <Text size="sm">Advanced options (authentication, HTTP headers, archive filter)</Text>
                      </Accordion.Control>
                      <Accordion.Panel>
                        <Stack gap="xs">
                          <SecretInput
                            size="xs"
                            label="HTTP Basic authentication (user:password)"
                            description={
                              <>
                                E.g. MaxMind <Code>account_id:{'${secret:maxmind-key}'}</Code>: pick the password in the secret manager
                                (key button), it never appears in the definition.
                              </>
                            }
                            placeholder={'123456:${secret:maxmind-key}'}
                            keepPrefix
                            value={draft.source.basic_auth ?? ''}
                            onChange={(v) => set('source', { ...draft.source, basic_auth: v || null })}
                          />
                          <Text size="xs" c="dimmed">
                            Headers sent with the request (e.g. <Code>Authorization</Code> for a protected API). Use a secret of the
                            secret manager (key button): the definition only holds its <Code>{'${secret:…}'}</Code> reference. Literal
                            values are masked by the API.
                          </Text>
                          {headers.map((h, i) => (
                            <Group key={i} gap="xs" align="flex-start">
                              <TextInput size="xs" placeholder="Header" value={h.k} onChange={(e) => { const v = e.currentTarget.value; setHeaders((s) => s.map((x, j) => (j === i ? { ...x, k: v } : x))); }} w={200} />
                              <SecretInput size="xs" placeholder={'Value, e.g. Bearer ${secret:vendor-api}'} value={h.v} onChange={(v) => setHeaders((s) => s.map((x, j) => (j === i ? { ...x, v } : x)))} style={{ flex: 1 }} />
                              <ActionIcon size="sm" variant="subtle" color="red" onClick={() => setHeaders((s) => s.filter((_, j) => j !== i))}>
                                <IconTrash size={14} />
                              </ActionIcon>
                            </Group>
                          ))}
                          <Button size="compact-xs" variant="subtle" w="fit-content" onClick={() => setHeaders((s) => [...s, { k: '', v: '' }])}>
                            + Header
                          </Button>
                          <TextInput
                            size="xs"
                            label="Files to keep from an archive (glob)"
                            placeholder="*.csv"
                            value={draft.source.extract ?? ''}
                            onChange={(e) => set('source', { ...draft.source, extract: e.currentTarget.value || null })}
                            w={300}
                          />
                        </Stack>
                      </Accordion.Panel>
                    </Accordion.Item>
                  </Accordion>
                </Stack>
              )}
              {mode === 'upload' && (
                <Stack gap="xs">
                  <Dropzone onDrop={onDrop} loading={uploading} multiple maxSize={2 * 1024 ** 3}>
                    <Group justify="center" gap="lg" mih={140} style={{ pointerEvents: 'none' }}>
                      <Dropzone.Accept>
                        <IconUpload size={42} color="var(--mantine-color-indigo-6)" />
                      </Dropzone.Accept>
                      <Dropzone.Idle>
                        <IconFile size={42} color="var(--mantine-color-dimmed)" />
                      </Dropzone.Idle>
                      <div>
                        <Text size="lg" inline>
                          Drop a file here or click to choose
                        </Text>
                        <Text size="sm" c="dimmed" inline mt={7} display="block">
                          CSV, TSV, TXT, JSON, JSONL, Parquet, Excel — possibly compressed (.gz, .zip). 2 GB maximum.
                        </Text>
                      </div>
                    </Group>
                  </Dropzone>
                  {upload && (
                    <Alert color="teal" icon={<IconCheck size={16} />} p="xs">
                      {upload.files.map((f) => (
                        <Text key={f.name} size="sm">
                          {f.name} — {fmtBytes(f.size)}
                        </Text>
                      ))}
                    </Alert>
                  )}
                  {editing && (
                    <Text size="xs" c="dimmed">
                      When saved, these files replace the current source and a new version is imported.
                    </Text>
                  )}
                </Stack>
              )}
              {mode === 'git' && (
                <Stack gap="xs">
                  <Text size="sm" c="dimmed">
                    Files of a repository on GitHub, GitLab, Gitea or any Git server over HTTPS. Only the matching files are
                    downloaded, and nothing is fetched again while the branch or tag still points to the same commit.
                  </Text>
                  <TextInput
                    label="Repository (HTTPS)"
                    placeholder="https://github.com/org/project.git"
                    value={draft.source.repository ?? ''}
                    onChange={(e) => set('source', { ...draft.source, repository: e.currentTarget.value })}
                    required
                  />
                  <SimpleGrid cols={{ base: 1, sm: 2 }}>
                    <TextInput
                      label="Files (path in the repository)"
                      description="Glob, e.g. data/countries.csv or exports/*.json"
                      placeholder="data/*.csv"
                      value={draft.source.path ?? ''}
                      onChange={(e) => set('source', { ...draft.source, path: e.currentTarget.value })}
                      required
                    />
                    <TextInput
                      label="Branch, tag or commit"
                      description="Default branch when empty"
                      placeholder="main"
                      value={draft.source.ref ?? ''}
                      onChange={(e) => set('source', { ...draft.source, ref: e.currentTarget.value })}
                    />
                  </SimpleGrid>
                  <SimpleGrid cols={{ base: 1, sm: 2 }}>
                    <SecretInput
                      label="Access token (private repository)"
                      description="Read access is enough. Pick it in the secret manager (key button): only its reference is stored."
                      placeholder={'${secret:git-token}'}
                      value={draft.source.token ?? ''}
                      onChange={(v) => set('source', { ...draft.source, token: v })}
                    />
                    <TextInput
                      label="User name of the token"
                      description="oauth2 by default (GitHub, GitLab and Gitea accept it)"
                      placeholder="oauth2"
                      value={draft.source.username ?? ''}
                      onChange={(e) => set('source', { ...draft.source, username: e.currentTarget.value })}
                    />
                  </SimpleGrid>
                  <Text size="xs" c="dimmed">
                    Tokens: GitHub fine-grained token with <i>Contents: read</i>, GitLab project / personal token with{' '}
                    <i>read_repository</i>, Gitea token with <i>repository: read</i>. Git LFS files are not supported.
                  </Text>
                </Stack>
              )}
              {mode === 'local' && (
                <Text size="sm" c="dimmed">
                  Files currently stored for this referential (pattern <Code>{draft.source.path}</Code>).
                </Text>
              )}
              {previewError && (
                <Alert color="red" icon={<IconAlertTriangle size={16} />}>
                  {previewError}
                </Alert>
              )}
              <Group>
                <Button leftSection={<IconCloudDownload size={16} />} onClick={() => runPreview({ next: true, format: editing ? draft.format : null })} loading={previewing} disabled={!sourceReady}>
                  Analyse the source
                </Button>
                {previewing && (mode === 'http' || mode === 'git') && (
                  <Text size="sm" c="dimmed">
                    Downloading… (may take a while for a large file)
                  </Text>
                )}
              </Group>
            </Stack>
          </Card>
        </Stepper.Step>

        {/* ------------------------------------------------------------ 2. Reading & preview */}
        <Stepper.Step label="Reading" description="Format, options, preview">
          <Grid mt="md" gutter="md">
            <Grid.Col span={{ base: 12, lg: 4 }}>
              <Card padding="lg">
                <Stack gap="sm">
                  <Select
                    label="Format"
                    description={preview ? `Detected: ${preview.detected_format}` : undefined}
                    data={FORMATS}
                    value={fmt || null}
                    onChange={(v) => {
                      set('format', v ?? '');
                      runPreview({ format: v });
                    }}
                    allowDeselect={false}
                  />
                  {(fmt === 'csv' || fmt === 'tsv') && (
                    <>
                      {preview?.sniff && (
                        <Text size="xs" c="dimmed">
                          Detected: delimiter <Code>{preview.sniff.delimiter === '\t' ? '\\t' : preview.sniff.delimiter}</Code>, header{' '}
                          {preview.sniff.has_header ? 'yes' : 'no'}, {preview.sniff.column_count} columns
                          {preview.sniff.skip_rows ? `, ${preview.sniff.skip_rows} row(s) skipped` : ''}.
                        </Text>
                      )}
                      <SimpleGrid cols={2} spacing="xs">
                        <TextInput size="xs" label="Delimiter" placeholder="auto" value={String(draft.options.delim ?? '')} onChange={(e) => setOption('delim', e.currentTarget.value.replace('\\t', '\t'))} />
                        <Select
                          size="xs"
                          label="Header row"
                          data={[{ value: '', label: 'auto' }, { value: 'true', label: 'yes' }, { value: 'false', label: 'no' }]}
                          value={draft.options.header === undefined ? '' : String(draft.options.header)}
                          onChange={(v) => setOption('header', v === '' || v == null ? undefined : v === 'true')}
                        />
                        <NumberInput size="xs" label="Rows to skip" min={0} value={(draft.options.skip as number) ?? ''} onChange={(v) => setOption('skip', v === '' ? undefined : Number(v))} />
                        <TextInput size="xs" label="Null value" placeholder="e.g. \N, NA" value={String(draft.options.nullstr ?? '')} onChange={(e) => setOption('nullstr', e.currentTarget.value)} />
                        <Select
                          size="xs"
                          label="Decimal separator"
                          data={[{ value: '', label: 'dot (.)' }, { value: ',', label: 'comma (,)' }]}
                          value={String(draft.options.decimal_separator ?? '')}
                          onChange={(v) => setOption('decimal_separator', v || undefined)}
                        />
                        <TextInput size="xs" label="Quote" placeholder='auto (")' value={String(draft.options.quote ?? '')} onChange={(e) => setOption('quote', e.currentTarget.value)} />
                      </SimpleGrid>
                      <Switch size="xs" label="Read everything as text (keeps leading zeros)" checked={!!draft.options.all_varchar} onChange={(e) => setOption('all_varchar', e.currentTarget.checked || undefined)} />
                      <Switch size="xs" label="Ignore invalid rows" checked={!!draft.options.ignore_errors} onChange={(e) => setOption('ignore_errors', e.currentTarget.checked || undefined)} />
                      <Switch size="xs" label="Pad rows that are too short" checked={!!draft.options.null_padding} onChange={(e) => setOption('null_padding', e.currentTarget.checked || undefined)} />
                    </>
                  )}
                  {(fmt === 'json' || fmt === 'xml') && (
                    <OptionField
                      label={fmt === 'xml' ? 'Record element' : 'Records path'}
                      description={
                        fmt === 'xml'
                          ? 'Name of the repeated element holding one record (detected automatically when empty).'
                          : 'Array of objects to read in a nested JSON document (e.g. data.items). Use * for an object whose values are the records (e.g. * or executables.*): the key goes to the _key column.'
                      }
                    >
                      <Autocomplete
                        size="xs"
                        data={preview?.records_path_candidates ?? []}
                        value={String(draft.options.records_path ?? '')}
                        onChange={(v) => setOption('records_path', v)}
                        placeholder={preview?.records_path_candidates.length ? `suggestion: ${preview.records_path_candidates[0]}` : fmt === 'xml' ? 'automatic' : 'document root'}
                      />
                    </OptionField>
                  )}
                  {fmt === 'txt' && (
                    <SimpleGrid cols={2} spacing="xs">
                      <TextInput size="xs" label="Column name" placeholder="value" value={String(draft.options.column ?? '')} onChange={(e) => setOption('column', e.currentTarget.value)} />
                      <TextInput size="xs" label="Comment prefix" placeholder="#" value={String(draft.options.comment ?? '')} onChange={(e) => setOption('comment', e.currentTarget.value)} />
                    </SimpleGrid>
                  )}
                  {fmt === 'bloom' && (
                    <>
                      <Text size="xs" c="dimmed">
                        The filter is published as is: it tells, offline, whether a value (file hash…) belongs to the set. "Absent" is
                        certain, "present" is probable.
                      </Text>
                      <SimpleGrid cols={2} spacing="xs">
                        <Select
                          size="xs"
                          label="Value case"
                          data={[
                            { value: 'upper', label: 'UPPERCASE (hashlookup)' },
                            { value: 'lower', label: 'lowercase' },
                            { value: 'none', label: 'as is' },
                          ]}
                          value={String(draft.options.normalize ?? 'upper')}
                          onChange={(v) => setOption('normalize', v ?? 'upper')}
                          allowDeselect={false}
                        />
                        <TextInput
                          size="xs"
                          label="Expected format (regex)"
                          placeholder="[0-9A-F]{40}"
                          value={String(draft.options.pattern ?? '')}
                          onChange={(e) => setOption('pattern', e.currentTarget.value)}
                        />
                      </SimpleGrid>
                      <TextInput
                        size="xs"
                        label="Bulk enrichment API (optional)"
                        description="POST {hashes: [...]} → list of objects; only the values present in the filter are sent."
                        placeholder="https://hashlookup.circl.lu/bulk/sha1"
                        value={String(draft.options.enrich_bulk_url ?? '')}
                        onChange={(e) => setOption('enrich_bulk_url', e.currentTarget.value)}
                      />
                      <TextInput
                        size="xs"
                        label="Field matching the value"
                        placeholder="SHA-1"
                        value={String(draft.options.enrich_key ?? '')}
                        onChange={(e) => setOption('enrich_key', e.currentTarget.value)}
                      />
                    </>
                  )}
                  {fmt === 'mmdb' && (
                    <Text size="xs" c="dimmed">
                      The database is published as is: tools fetch the raw <Code>.mmdb</Code> file at a stable URL (<Code>/raw</Code>), and the
                      API answers IP lookups. In a MaxMind archive (.tar.gz), the <Code>.mmdb</Code> member is picked automatically; set the
                      archive filter if it holds several databases. An update is refused if the database type changes or if it is older than
                      the published one (unless forced).
                    </Text>
                  )}
                  {fmt === 'sqlite' && (
                    <OptionField
                      label="Table or view"
                      description="Table or view of the database to publish (e.g. FILE for the NIST NSRL). Use the SQL transformation to filter or rename columns."
                    >
                      <Autocomplete
                        size="xs"
                        data={preview?.records_path_candidates ?? []}
                        value={String(draft.options.table ?? '')}
                        onChange={(v) => setOption('table', v)}
                        placeholder={preview?.records_path_candidates.length ? `e.g. ${preview.records_path_candidates[0]}` : 'FILE'}
                      />
                    </OptionField>
                  )}
                  {(fmt === 'sqlite' || fmt === 'bloom') && mode === 'http' && (
                    <OptionField
                      label="Deltas (optional, one URL per line)"
                      description={
                        fmt === 'sqlite'
                          ? 'SQL files (or archives holding one) applied once, in this order, to the full database of the source URL, which is kept. Add each new delta at the end of the list; deltas can also be imported by hand.'
                          : 'Files of values (one per line) added to the published filter when they change; the full filter is only downloaded again when it changes.'
                      }
                    >
                      <Textarea
                        size="xs"
                        autosize
                        minRows={2}
                        placeholder={fmt === 'sqlite' ? 'https://…/RDS_2026.06.1_modern_minimal_delta.zip' : 'https://…/delta.txt.gz'}
                        value={(draft.incremental?.urls ?? []).join('\n')}
                        onChange={(e) => {
                          const urls = e.currentTarget.value.split('\n').map((u) => u.trim());
                          const kept = urls.filter(Boolean);
                          // Keep an empty last line while typing; a SQLite database keeps the block even without URL
                          set('incremental', kept.length || fmt === 'sqlite' ? { ...(draft.incremental ?? {}), urls: urls.length > kept.length && urls[urls.length - 1] === '' ? [...kept, ''] : kept } : null);
                        }}
                        onBlur={() => {
                          const kept = (draft.incremental?.urls ?? []).filter(Boolean);
                          set('incremental', kept.length || (fmt === 'sqlite' && draft.incremental) ? { ...(draft.incremental ?? {}), urls: kept } : null);
                        }}
                      />
                    </OptionField>
                  )}
                  {fmt === 'xlsx' && (
                    <SimpleGrid cols={2} spacing="xs">
                      <TextInput size="xs" label="Sheet" placeholder="first" value={String(draft.options.sheet ?? '')} onChange={(e) => setOption('sheet', e.currentTarget.value)} />
                      <TextInput size="xs" label="Range" placeholder="e.g. A1:F500" value={String(draft.options.range ?? '')} onChange={(e) => setOption('range', e.currentTarget.value)} />
                    </SimpleGrid>
                  )}
                  <JsonInput
                    size="xs"
                    label="All reader options (JSON)"
                    description="Passed as is to the DuckDB reader (read_csv, read_json…)."
                    value={optionsText}
                    onChange={setOptionsText}
                    onBlur={() => {
                      try {
                        set('options', JSON.parse(optionsText || '{}'));
                      } catch {
                        /* invalid JSON is reported by the component */
                      }
                    }}
                    validationError="Invalid JSON"
                    autosize
                    minRows={2}
                    formatOnBlur
                  />
                  {fmt !== 'bloom' && fmt !== 'mmdb' && (
                  <OptionField
                    label="SQL transformation (optional)"
                    description="DuckDB query producing the final dataset. {source} = reader over all files, {source0}, {source1}… = files of each URL."
                  >
                    <Card withBorder padding={0}>
                      <CodeMirror
                        value={draft.transform ?? ''}
                        onChange={(v) => set('transform', v)}
                        height="140px"
                        theme={scheme === 'dark' ? 'dark' : 'light'}
                        extensions={[sqlLang({ dialect: PostgreSQL, upperCaseKeywords: true })]}
                        placeholder="SELECT * REPLACE (CAST(date AS DATE) AS date) FROM {source}"
                        basicSetup={{ lineNumbers: false, foldGutter: false }}
                      />
                    </Card>
                  </OptionField>
                  )}
                  <Group>
                    <Button leftSection={<IconRefresh size={16} />} onClick={() => runPreview()} loading={previewing}>
                      Refresh the preview
                    </Button>
                    {mode === 'http' && (
                      <Button variant="subtle" size="xs" onClick={() => runPreview({ refresh: true })} disabled={previewing}>
                        Download the source again
                      </Button>
                    )}
                  </Group>
                </Stack>
              </Card>
            </Grid.Col>
            <Grid.Col span={{ base: 12, lg: 8 }}>
              <Stack gap="sm">
                {previewError && <Alert color="red">{previewError}</Alert>}
                {!preview && !previewing && <Alert color="gray">Analyse the source to see the preview.</Alert>}
                {previewing && <Loader />}
                {preview && (
                  <>
                    {preview.error ? (
                      <Alert color="red" icon={<IconAlertTriangle size={16} />} title="Cannot read with these settings">
                        <Text size="sm" ff="monospace" style={{ whiteSpace: 'pre-wrap' }}>
                          {preview.error}
                        </Text>
                      </Alert>
                    ) : preview.unanalysed ? (
                      <Alert color="orange" icon={<IconAlertTriangle size={16} />} title="Source not analysed">
                        <Text size="sm">{preview.unanalysed}</Text>
                        <Text size="xs" c="dimmed" mt={4}>
                          Choose the format (and, for a SQLite database, the table) on the left, then continue: the settings are checked
                          by the import, which runs in the background and can be followed in the History tab.
                        </Text>
                      </Alert>
                    ) : (
                      <Alert color="teal" icon={<IconCheck size={16} />} p="sm">
                        <Text size="sm">
                          {preview.total_rows != null ? <b>{fmtNumber(preview.total_rows)} rows</b> : 'Row count computed at import (large source)'} ·{' '}
                          {columns.length} columns · format <b>{preview.format}</b> · {preview.files.length} file(s):{' '}
                          {preview.files.map((f) => `${f.name} (${fmtBytes(f.size)})`).join(', ')}
                        </Text>
                        {preview.sampled && (
                          <Text size="xs" c="dimmed" mt={4}>
                            Large source: the preview uses the beginning of the file only. The import downloads and reads the whole file
                            in the background (compressed CSV / TSV / TXT / JSON Lines files are read without being decompressed on
                            disk). For hundreds of millions of rows, set the large volume options at the Description step.
                          </Text>
                        )}
                      </Alert>
                    )}
                    {(fmt === 'json' || fmt === 'xml') && preview.records_path_candidates.length > 0 && !draft.options.records_path && (
                      <Alert color="indigo" icon={<IconWand size={16} />} p="sm">
                        <Group gap="xs">
                          <Text size="sm">{fmt === 'xml' ? 'Repeated elements (the first one is used by default):' : 'This JSON contains collections of records:'}</Text>
                          {preview.records_path_candidates.map((c) => (
                            <Button key={c} size="compact-xs" variant="light" onClick={() => setOption('records_path', c)}>
                              read “{c}”
                            </Button>
                          ))}
                        </Group>
                      </Alert>
                    )}
                    {columns.length > 0 && (
                      <Group gap={4}>
                        {columns.map((c) => (
                          <Badge key={c.name} variant="default" tt="none" size="sm">
                            {c.name} <Text span c="dimmed" size="xs">{c.type.toLowerCase()}</Text>
                          </Badge>
                        ))}
                      </Group>
                    )}
                    {previewRows.length > 0 && <DataGrid columns={columns} rows={previewRows} maxHeight={460} />}
                    {preview.logs.length > 0 && (
                      <Text size="xs" c="dimmed">
                        {preview.logs.join(' · ')}
                      </Text>
                    )}
                  </>
                )}
              </Stack>
            </Grid.Col>
          </Grid>
        </Stepper.Step>

        {/* ------------------------------------------------------------ 3. Description */}
        <Stepper.Step label="Description" description="Metadata, key, schedule">
          <SimpleGrid cols={{ base: 1, lg: 2 }} mt="md">
            <Card padding="lg">
              <Stack gap="sm">
                <Title order={5}>Identification</Title>
                <TextInput
                  label="Name"
                  required
                  value={draft.name}
                  onChange={(e) => {
                    const v = e.currentTarget.value;
                    setDraft((d) => ({ ...d, name: v, id: !editing && !idTouched ? slugify(v) : d.id }));
                  }}
                />
                <TextInput
                  label="Identifier"
                  description={`Used in URLs and as the SQL table “${draft.id.replace(/-/g, '_') || '…'}”. Cannot be changed later.`}
                  required
                  disabled={editing}
                  value={draft.id}
                  onChange={(e) => {
                    setIdTouched(true);
                    set('id', e.currentTarget.value.toLowerCase());
                  }}
                  error={draft.id && !/^[a-z0-9][a-z0-9_-]{0,62}$/.test(draft.id) ? 'lowercase letters, digits, - and _' : undefined}
                />
                <Textarea label="Description" autosize minRows={2} value={draft.description} onChange={(e) => set('description', e.currentTarget.value)} />
                <SimpleGrid cols={2} spacing="sm">
                  <Autocomplete label="Category" data={meta?.categories ?? []} value={draft.category} onChange={(v) => set('category', v)} placeholder="General" />
                  <TagsInput label="Tags" value={draft.tags} onChange={(v) => set('tags', v)} />
                  <TextInput label="Producer" value={draft.owner ?? ''} onChange={(e) => set('owner', e.currentTarget.value)} />
                  <TextInput label="License" value={draft.license ?? ''} onChange={(e) => set('license', e.currentTarget.value)} />
                </SimpleGrid>
                <TextInput label="Reference website" placeholder="https://…" value={draft.homepage ?? ''} onChange={(e) => set('homepage', e.currentTarget.value)} />
              </Stack>
            </Card>
            <Stack>
              <Card padding="lg">
                <Stack gap="sm">
                  <Title order={5}>Querying</Title>
                  <Select
                    label="Key"
                    description={`Column identifying a row: exact lookup (/lookup), tracking of additions/removals/changes.${keyCandidates.length ? ` Suggestion: ${keyCandidates.join(', ')}` : ''}`}
                    data={columnOptions}
                    value={draft.key}
                    onChange={(v) => set('key', v)}
                    searchable
                    clearable
                    placeholder={columns.length ? 'None' : 'Analyse the source first'}
                  />
                  <MultiSelect
                    label="Full-text search columns"
                    description="Default: all text columns."
                    data={columnOptions}
                    value={draft.search_columns}
                    onChange={(v) => set('search_columns', v)}
                    searchable
                    clearable
                  />
                  {fmt !== 'bloom' && fmt !== 'mmdb' && (
                    <Accordion variant="contained">
                      <Accordion.Item value="storage">
                        <Accordion.Control>
                          <Text size="sm" fw={500}>
                            Large volume (sorting, indexes, storage)
                          </Text>
                        </Accordion.Control>
                        <Accordion.Panel>
                          <Stack gap="xs">
                            <Text size="xs" c="dimmed">
                              For hundreds of millions of rows (passive DNS…): the data is written sorted, and sorted copies ("indexes")
                              make exact, prefix and range searches on these columns nearly instant.
                            </Text>
                            <MultiSelect
                              size="xs"
                              label="Sort on"
                              description="The first column gets fast lookups."
                              data={columnOptions}
                              value={draft.storage.sort_by}
                              onChange={(v) => set('storage', { ...draft.storage, sort_by: v })}
                              searchable
                            />
                            <MultiSelect
                              size="xs"
                              label="Indexes (additional sorted copies)"
                              description="Each index takes about the size of the referential."
                              data={columnOptions}
                              value={draft.storage.indexes}
                              onChange={(v) => set('storage', { ...draft.storage, indexes: v })}
                              searchable
                            />
                            <Select
                              size="xs"
                              label="Column profile"
                              data={[
                                { value: 'full', label: 'Full (automatic sample above the large volume threshold)' },
                                { value: 'sample', label: 'On a sample of one million rows' },
                                { value: 'none', label: 'None' },
                              ]}
                              value={draft.storage.profile}
                              onChange={(v) => set('storage', { ...draft.storage, profile: (v ?? 'full') as DefinitionConfig['storage']['profile'] })}
                              allowDeselect={false}
                            />
                            <Switch size="xs" label="Keep the source files after import" checked={draft.storage.keep_raw} onChange={(e) => set('storage', { ...draft.storage, keep_raw: e.currentTarget.checked })} />
                            <Switch size="xs" label="Keep the previous version (change tracking)" checked={draft.storage.keep_previous} onChange={(e) => set('storage', { ...draft.storage, keep_previous: e.currentTarget.checked })} />
                          </Stack>
                        </Accordion.Panel>
                      </Accordion.Item>
                    </Accordion>
                  )}
                  {fmt !== 'bloom' && fmt !== 'mmdb' && (
                  <MultiSelect
                    label="Pre-generated downloads"
                    description="Files produced right after each update (other formats are generated on first request)."
                    data={DOWNLOAD_FORMATS}
                    value={draft.downloads}
                    onChange={(v) => set('downloads', v)}
                  />
                  )}
                </Stack>
              </Card>
              <Card padding="lg">
                <Stack gap="sm">
                  <Title order={5}>Updates</Title>
                  <Select
                    label="Schedule"
                    data={SCHEDULES}
                    value={customCron ? 'custom' : draft.schedule ?? ''}
                    onChange={(v) => {
                      if (v === 'custom') setCustomCron(true);
                      else {
                        setCustomCron(false);
                        set('schedule', v || null);
                      }
                    }}
                    allowDeselect={false}
                  />
                  {customCron && (
                    <TextInput
                      label="Cron expression"
                      description={draft.schedule ? describeCron(draft.schedule) : 'minute hour day month day-of-week'}
                      placeholder="30 */4 * * *"
                      value={draft.schedule ?? ''}
                      onChange={(e) => set('schedule', e.currentTarget.value)}
                    />
                  )}
                  <TextInput label="Stale after" placeholder="e.g. 2d, 12h (default: 2 schedule intervals)" value={draft.max_age ?? ''} onChange={(e) => set('max_age', e.currentTarget.value)} />
                  <SimpleGrid cols={2} spacing="sm">
                    <NumberInput label="Minimum rows" min={0} value={draft.validation.min_rows} onChange={(v) => set('validation', { ...draft.validation, min_rows: Number(v) || 0 })} />
                    <NumberInput
                      label="Max. drop tolerated (%)"
                      min={0}
                      max={100}
                      placeholder="unlimited"
                      value={draft.validation.max_drop_pct ?? ''}
                      onChange={(v) => set('validation', { ...draft.validation, max_drop_pct: v === '' ? null : Number(v) })}
                    />
                  </SimpleGrid>
                  <Switch label="Reject a version whose key is not unique" checked={draft.validation.unique_key} onChange={(e) => set('validation', { ...draft.validation, unique_key: e.currentTarget.checked })} />
                  <Switch label="Referential enabled" checked={draft.enabled} onChange={(e) => set('enabled', e.currentTarget.checked)} />
                  {fmt !== 'bloom' && fmt !== 'mmdb' && (
                    <Switch
                      label="Confidential (encrypted at rest)"
                      description="Encrypted at rest (AES-256-GCM): published files, previous version, internal rows and their keys in the database, column profile. Source files and generated downloads are not kept on disk. Transparent for users with access. Changing it publishes the referential again."
                      checked={!!draft.confidential}
                      onChange={(e) => set('confidential', e.currentTarget.checked)}
                    />
                  )}
                </Stack>
              </Card>
              {!editing && (
                <Card padding="lg">
                  <Title order={5} mb="xs">
                    Access
                  </Title>
                  <MultiSelect
                    label="Groups with read access"
                    description="Administrators always have access. Rights can be changed later (Access tab)."
                    data={(meta?.groups ?? []).map((g) => ({ value: String(g.id), label: g.name }))}
                    value={groupIds}
                    onChange={setGroupIds}
                    placeholder={meta?.groups.length ? 'None' : 'No group defined'}
                  />
                </Card>
              )}
            </Stack>
          </SimpleGrid>
        </Stepper.Step>

        {/* ------------------------------------------------------------ 4. Summary */}
        <Stepper.Step label="Publish" description="Check and save">
          <Card padding="lg" mt="md" maw={900}>
            <Stack>
              <Title order={5}>Summary</Title>
              <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="xs">
                <Text size="sm">Name: <b>{draft.name || '—'}</b></Text>
                <Text size="sm">Identifier: <Code>{draft.id || '—'}</Code></Text>
                <Text size="sm">Source: {mode === 'http' ? draft.source.urls.filter(Boolean).join(', ') : mode === 'git' ? `${draft.source.repository} · ${draft.source.path}${draft.source.ref ? ` @ ${draft.source.ref}` : ''}` : mode === 'upload' ? upload?.files.map((f) => f.name).join(', ') : 'current files'}</Text>
                <Text size="sm">Format: {draft.format || '—'}</Text>
                <Text size="sm">Key: {draft.key ?? 'none'}</Text>
                <Text size="sm">Schedule: {describeCron(draft.schedule)}</Text>
                <Text size="sm">Preview: {preview?.total_rows != null ? `${fmtNumber(preview.total_rows)} rows` : '—'}, {columns.length} columns</Text>
              </SimpleGrid>
              {!draft.name || !draft.id || !draft.format ? (
                <Alert color="orange">The name, identifier and format are required.</Alert>
              ) : preview?.error ? (
                <Alert color="orange">The preview failed: the import will probably fail. Fix the reading settings in step 2.</Alert>
              ) : null}
              <Checkbox label={editing ? 'Run an update after saving' : 'Start the import immediately'} checked={pull} onChange={(e) => setPull(e.currentTarget.checked)} />
              <Group>
                <Button size="md" leftSection={<IconCheck size={18} />} onClick={() => save.mutate()} loading={save.isPending} disabled={!draft.name || !draft.id || !draft.format || (mode === 'upload' && !upload)}>
                  {editing ? 'Save' : 'Create the referential'}
                </Button>
              </Group>
            </Stack>
          </Card>
        </Stepper.Step>
      </Stepper>

      <Group justify="space-between">
        <Button variant="default" onClick={() => setStep((s) => Math.max(0, s - 1))} disabled={step === 0}>
          Previous
        </Button>
        {step < 3 && (
          <Button onClick={() => (step === 0 ? runPreview({ next: true, format: editing ? draft.format : null }) : setStep((s) => s + 1))} disabled={step === 0 ? !sourceReady : step === 1 && !previewOk && !editing} loading={step === 0 && previewing}>
            Next
          </Button>
        )}
      </Group>
    </Stack>
  );
}
