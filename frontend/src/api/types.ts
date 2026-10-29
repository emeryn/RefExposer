export type Access = 'read' | 'manage';
export type Health = 'ok' | 'stale' | 'error' | 'running' | 'empty' | 'disabled';
export type RunStatus = 'queued' | 'running' | 'success' | 'unchanged' | 'error' | 'rejected' | 'corrupted' | 'cancelled';
export type Role = 'admin' | 'advanced' | 'user';

export interface Column {
  name: string;
  type: string;
}

export interface Changes {
  added: number;
  removed: number;
  modified: number | null;
}

export interface RunLog {
  t: string;
  level: 'info' | 'warn' | 'error';
  msg: string;
}

export interface Run {
  id: string;
  ref_id: string;
  ref_name?: string;
  trigger: 'manual' | 'schedule' | 'startup' | 'upload' | 'inbox' | 'edit' | 'sync';
  user?: string | null;
  force: boolean;
  /** merged referential: data set published alone (new base) */
  replace?: boolean;
  status: RunStatus;
  phase: string;
  queued_at: string;
  started_at: string | null;
  finished_at: string | null;
  duration: number | null;
  message: string | null;
  progress: { done: number; total: number };
  rows?: number | null;
  previous_rows?: number | null;
  changes?: Changes | null;
  version?: number;
  downloaded_bytes?: number;
  quality?: { score: number | null; status: 'ok' | 'warning' | 'failed' };
  logs?: RunLog[];
}

export type QualityCheckType = 'not_null' | 'unique' | 'format' | 'pattern' | 'values' | 'range' | 'length' | 'expression';

export interface QualityRule {
  name?: string | null;
  column?: string | null;
  check: QualityCheckType;
  format?: string | null;
  pattern?: string | null;
  values?: (string | number)[];
  min?: number | string | null;
  max?: number | string | null;
  min_length?: number | null;
  max_length?: number | null;
  expression?: string | null;
  threshold?: number;
  severity?: 'warning' | 'error';
}

export interface QualityCheckResult {
  name: string;
  column: string | null;
  check: QualityCheckType;
  severity: 'warning' | 'error';
  threshold: number;
  automatic: boolean;
  ok: boolean;
  pass_pct: number;
  checked?: number;
  failing?: number;
  examples?: string[];
  error?: string;
  /** Not checked (uniqueness on a large referential) */
  skipped?: boolean;
}

export interface QualityResult {
  score: number | null;
  status: 'ok' | 'warning' | 'failed';
  rows: number | null;
  checks: QualityCheckResult[];
  checked_at: string;
  duration: number;
  recheck?: boolean;
  /** Large referential: rules checked on a sample of blocks of rows */
  sampled?: boolean;
}

export interface QualitySummary {
  score: number | null;
  status: 'ok' | 'warning' | 'failed';
  failed: string[];
  checked_at: string | null;
}

/** Visibility of a referential: company data, or something already published outside. */
export type Visibility = 'internal' | 'public';

/** How a personal data column is served when it is not revealed. */
export type PiiStrategy = 'hide' | 'partial' | 'hash';

/** Masking of the personal data columns of a referential (absent when it has none). */
export interface PiiState {
  columns: string[];
  strategy: PiiStrategy;
  /** What the data is and under which rule, written by whoever configured the masking */
  note: string;
  /** Whether this account may ask for the real values (it still has to ask: `reveal`) */
  can_reveal: boolean;
  /** Configured columns the published version does not have (renamed source, typo) */
  missing: string[];
}

export interface ReferentialSummary {
  id: string;
  name: string;
  description: string;
  category: string;
  tags: string[];
  homepage: string | null;
  license: string | null;
  owner: string | null;
  /** Team answering for the referential, beside its owner */
  owner_team: string | null;
  visibility: Visibility;
  /** Columns holding personal data, masked for everyone by default; null when there are none */
  pii: PiiState | null;
  /** Past versions are kept and can be compared (Time travel tab) */
  time_travel: boolean;
  /** Columns identifying a row when two versions are compared (the key by default) */
  primary_key_columns: string[];
  format: string;
  source_type: 'http' | 'local' | 'internal' | 'sync' | 'git' | 'referential';
  origin: 'file' | 'database' | 'sync';
  config_file: string | null;
  kind: RefKind;
  large: boolean;
  sort_by: string[];
  indexes: string[];
  source_urls: string[];
  key: string | null;
  table: string;
  schedule: string | null;
  enabled: boolean;
  next_run_at: string | null;
  health: Health;
  has_data: boolean;
  status: string | null;
  last_error: string | null;
  version: number | null;
  row_count: number | null;
  column_count: number;
  previous_row_count: number | null;
  changes: Changes | null;
  parquet_size: number | null;
  raw_size: number | null;
  storage_size: number | null;
  last_success_at: string | null;
  last_attempt_at: string | null;
  last_checked_at: string | null;
  data_updated_at: string | null;
  last_run: Run | null;
  current_run: Run | null;
  quality?: QualitySummary | null;
  access: Access | null;
  pinned: { by: string | null; at: string; reason?: string } | null;
  /** Set when the published version was put back from a kept one */
  restored?: { snapshot_id: number; version: number; label: string; saved_at: string | null; at: string; by: string | null } | null;
  /** data encrypted at rest; `encrypted`: state of the published version (until the next publication) */
  confidential: boolean;
  encrypted: boolean;
  manual_import: { origin: 'upload' | 'inbox'; by: string | null; at: string; files: string[]; run: string } | null;
  import_folder: string | null;
  sync_path: string | null;
  /** deltas merged into the published version (settings and state since the base) */
  merge?: MergeState | null;
  /** unified referential: how its source referentials are combined */
  combine?: CombineState | null;
  /** replica of a referential of another RefExposer instance */
  federation?: FederationInfo | null;
}

export type RefKind = 'table' | 'bloom' | 'mmdb' | 'internal' | 'combined';

/** `combine` of a definition: how the referentials of `source.referentials` are combined. */
export interface CombineConfig {
  mode: 'union' | 'enrich';
  /** enrich: column of the first referential holding the value to join on (default: the key) */
  on?: string | null;
  /** enrich: {referential id: its column matching `on`} when the names differ */
  join_on?: Record<string, string>;
  /** {referential id: columns taken from it}; all of them when absent */
  columns?: Record<string, string[]>;
  /** enrich: added columns named <referential>_<column> */
  prefix?: boolean;
  /** union: column recording which referential each row comes from */
  source_column?: string | null;
}

/** Same, as the API reports it on a referential: with the referentials it combines. */
export interface CombineState extends CombineConfig {
  referentials: string[];
}

export interface MergeState {
  enabled: boolean;
  delete_column: string | null;
  base_at?: string | null;
  deltas?: number;
  last_delta_rows?: number;
  last_delta_deleted?: number;
  last_merged_at?: string | null;
}

export interface FederationInfo {
  peer: string;
  remote_id: string;
  remote_url: string | null;
  transform: string | null;
  remote_format: string | null;
  remote_source: string | null;
}

export interface PeerReplica {
  remote_id: string;
  local_id: string | null;
  name: string | null;
  exists: boolean;
  missing: boolean;
  remote_version: number | null;
  replicated_version: number | null;
  remote_rows: number | null;
  rows: number | null;
  status: string | null;
  last_success_at: string | null;
}

export interface SyncReport {
  peer: string;
  status: 'success' | 'warning' | 'error';
  message: string;
  created: string[];
  updated: string[];
  unchanged: string[];
  updates_started: string[];
  conflicts: { id: string; local_id: string; error: string }[];
  errors: { id: string; local_id: string; error: string }[];
  missing: string[];
  started_at: string;
  finished_at?: string;
}

export interface Peer {
  name: string;
  url: string;
  secret: string;
  prefix: string;
  datasets: string[];
  schedule: string;
  enabled: boolean;
  category: string;
  grant_groups: string[];
  last_sync_at: string | null;
  next_sync_at: string | null;
  status: 'success' | 'warning' | 'error' | null;
  message: string | null;
  instance: { url: string | null; refexposer: string; account: string } | null;
  replicas: PeerReplica[];
  last_report: SyncReport | null;
}

export interface PeerDataset {
  id: string;
  name: string;
  category: string;
  kind: string;
  format: string;
  row_count: number | null;
  version: number | null;
  data_updated_at: string | null;
  key: string | null;
  confidential: boolean;
  transform: string | null;
  quality_rules: number;
  federation: FederationInfo | null;
}

export interface ProfileEntry {
  name: string;
  type: string;
  min: string | null;
  max: string | null;
  approx_unique: number | null;
  avg: string | null;
  std: string | null;
  q25: string | null;
  q50: string | null;
  q75: string | null;
  count: number | null;
  null_percentage: number | null;
}

export interface SourceInfo {
  url: string | null;
  final_url?: string;
  files: string[];
  etag?: string | null;
  last_modified?: string | null;
  remote_date?: string | null;
  downloaded_bytes?: number;
  fetched_at?: string;
  checked_at?: string;
  not_modified?: boolean;
  /** unified referential: the source referential and the version its data came from */
  referential?: string;
  name?: string;
  version?: number | null;
  row_count?: number | null;
  data_updated_at?: string | null;
}

export interface BloomInfo {
  capacity: number;
  target_fp_rate: number;
  hash_functions: number;
  bits: number;
  elements: number;
  estimated_fp_rate: number;
  size: number;
}

export interface MmdbInfo {
  database_type: string;
  description: string | null;
  build_date: string;
  ip_version: number;
  languages: string[];
  node_count: number;
  record_size: number;
  format_version: string;
  size: number;
  file_name: string;
}

export interface ReferentialDetail extends ReferentialSummary {
  columns: Column[];
  profile: ProfileEntry[];
  profile_sampled: boolean;
  bloom: BloomInfo | null;
  mmdb: MmdbInfo | null;
  index_views: Record<string, string>;
  key_unique: boolean | null;
  has_previous: boolean;
  sources: SourceInfo[];
  /** unified referentials built from this one (rebuilt after each of its new versions) */
  dependents: string[];
  search_columns: string[];
  max_age: string | null;
  config: Record<string, unknown>;
  config_file: string | null;
}

export type Row = Record<string, unknown>;

export interface RowsResponse {
  columns: Column[];
  rows: Row[];
  total: number | null;
  limit: number;
  offset: number;
  elapsed_ms: number;
}

export interface ColumnStats {
  column: string;
  type: string;
  kind: 'text' | 'number' | 'temporal' | 'boolean' | 'list' | 'struct';
  total: number;
  non_null: number;
  nulls: number;
  distinct: number;
  top: { value: string; count: number }[];
  mostly_unique: boolean;
  histogram: { label: string; count: number; from?: number; to?: number }[] | null;
  sampled?: boolean;
  min?: string;
  max?: string;
  bucket?: string;
}

export interface ChangesResponse {
  kind: 'added' | 'removed' | 'modified';
  available: boolean;
  total: number;
  columns: Column[];
  rows: Row[] | { key: string; changes: { column: string; before: unknown; after: unknown }[] }[];
}

export interface Snapshot {
  id: number;
  version: number;
  created_at: string;
  row_count: number | null;
  size: number | null;
  columns: number;
  schema_hash: string;
  encrypted: boolean;
  /** The file is still on disk (it is removed with the retention policy) */
  available: boolean;
  run_id: string | null;
  /** Saved by hand: it carries a label, and the retention never drops it */
  manual: boolean;
  label: string;
  created_by: string | null;
}

export interface SnapshotListing {
  enabled: boolean;
  max_snapshots: number;
  /** Columns identifying a row for the comparison; empty: rows are compared whole */
  keys: string[];
  latest: { id: 'latest'; version: number | null; row_count: number | null; created_at: string | null; size: number | null };
  snapshots: Snapshot[];
  stored_size: number;
}

export interface DiffSide {
  ref: string;
  version: number | null;
  created_at: string | null;
  row_count: number | null;
  columns: number;
}

export interface DiffSummary {
  kind: 'summary';
  from: DiffSide;
  to: DiffSide;
  keys: string[];
  columns: string[];
  masked: string[];
  revealed: boolean;
  /** False when no key identifies a row: a change then reads as one removal and one addition */
  comparable: boolean;
  added: number;
  removed: number;
  modified: number;
  /** Columns only one of the two versions has */
  schema_changed: string[];
}

export interface DiffRows {
  kind: 'added' | 'removed' | 'modified';
  total: number;
  columns: Column[];
  rows: Row[] | { key: string; changes: { column: string; before: unknown; after: unknown }[] }[];
  note?: string;
}

export type Severity = 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';

export interface WatchdogNotification {
  target: string;
  ok: boolean;
  detail: string;
}

export interface WatchdogRun {
  id: number;
  task_id: number;
  /** Set on the recent-runs list: the name of the watchdog it belongs to */
  name?: string;
  /** The investigation is going on right now: its steps arrive as it works */
  running: boolean;
  started_at: string;
  finished_at: string | null;
  trigger: string;
  status: 'ok' | 'finding' | 'error';
  finding_detected: boolean;
  severity: Severity | null;
  summary: string;
  details_markdown?: string;
  affected_items_count: number | null;
  notifications: WatchdogNotification[];
  error: string | null;
  steps: AgentStep[];
}

export interface WatchdogTask {
  id: number;
  name: string;
  description: string;
  user_id: number;
  username: string;
  referential_ids: string[];
  /** Look at everything the account may read, instead of the list above */
  all_referentials: boolean;
  /** Report on what the latest versions added or changed, not on the whole data */
  only_new_data: boolean;
  trigger_type: 'cron' | 'on_sync';
  schedule: string | null;
  prompt: string;
  template: string | null;
  notify_emails: string[];
  notify_channels: string[];
  min_severity: Severity;
  enabled: boolean;
  created_at: string;
  updated_at: string;
  created_by: string | null;
  next_run_at: string | null;
  running: boolean;
  last_run: WatchdogRun | null;
}

export interface WatchdogTemplate {
  id: string;
  label: string;
  description: string;
  prompt: string;
}

export type AlertKind = 'watchdog' | 'failure' | 'quality' | 'stale' | 'drift';

/** One line of the alert feed: a watchdog finding, or a referential that drifted. */
export interface Alert {
  id: string;
  kind: AlertKind;
  severity: Severity;
  at: string | null;
  title: string;
  summary: string;
  /** Where to go in the interface to act on it */
  link: string;
  referential_id: string | null;
  referential_name: string | null;
  watchdog_id: number | null;
  watchdog_name: string | null;
  /** A watchdog finding carries the report the agent wrote */
  has_report: boolean;
  /** Marked as read by this account, and nothing newer happened since */
  read: boolean;
  /** Deleted from the feed of this account: only listed when the deleted ones are asked for */
  dismissed: boolean;
}

export interface AlertFeed {
  alerts: Alert[];
  /** total, unread, deleted, one per kind and one per severity (lowercase) */
  counts: Record<string, number>;
  /** The kinds this instance reports at all: an administrator can hide some of them */
  kinds: AlertKind[];
}

// --------------------------------------------------------------------------- relational search

/** One kind of identifier the instance recognises, and the columns of the catalogue that hold it. */
export interface EntityType {
  name: string;
  label: string;
  description: string;
  /** Shown first: the identifiers an investigation usually starts from */
  pivot: boolean;
  columns: EntityColumn[];
}

export interface EntityColumn {
  referential_id: string;
  referential_name: string;
  category: string;
  column: string;
  type: string;
  entity: string;
  /** The definition says so, rather than the values of the column */
  declared: boolean;
  /** A lookup here only reads the relevant blocks (sorted or indexed column) */
  fast: boolean;
  /** Why this column cannot be looked up, when it cannot (a referential too large to be read whole) */
  refused: string | null;
}

export interface EntityCatalogue {
  types: EntityType[];
  categories: string[];
  /** A value does not have to be an identifier: `auto` also searches the text of the catalogue */
  match_modes: { name: MatchMode; description: string }[];
  limits: { max_depth: number; max_nodes: number; max_rows_per_lookup: number; budget_seconds: number };
}

/**
 * How a value is looked for. `auto` does both passes, which is what makes an investigation a superset of the
 * global search rather than a narrower mode beside it.
 */
export type MatchMode = 'auto' | 'exact' | 'text';

/** One relational search. The same object is what a saved search stores and replays. */
export interface InvestigationSpec {
  /** A single seed; `values` takes several. One of the two is required. */
  value?: string;
  /** Several seeds: what ties them together comes back in `connections` */
  values?: string[];
  entity?: string | null;
  depth?: number;
  scope?: string[];
  follow?: string[];
  rows_per_lookup?: number;
  max_nodes?: number;
  match?: MatchMode;
}

/** A value of the graph, or a referential that evidences one. */
export interface GraphNode {
  id: string;
  /** `entity`: a value · `query`: the free text that was asked about · `source`: a referential */
  kind: 'entity' | 'query' | 'source';
  entity: string | null;
  value: string;
  label: string;
  category?: string;
  depth: number;
  rows: number;
  referentials: string[];
  seed?: boolean;
  /** Which seed this is, when it is one */
  seed_index?: number;
  /** The seeds this node was reached from: more than one makes it a meeting point */
  seeds: number[];
}

export interface GraphEdge {
  source: string;
  target: string;
  /** `found_in`: the value is in that referential · `same_row`: the two values share one of its rows */
  kind: 'found_in' | 'same_row';
  referential_id: string;
  label: string;
  rows: number;
}

/** The rows of one referential that are the evidence behind an edge. */
export interface InvestigationHit {
  referential_id: string;
  referential_name: string;
  category: string;
  /** The typed column for an exact hit; `text` when the row merely mentions the value */
  column: string;
  /** null for a text hit: no single column is the reason */
  entity: string | null;
  values: string[];
  row_count: number;
  rows: Row[];
  /** `exact`: a typed lookup · `text`: the value is mentioned somewhere in the row */
  how: 'exact' | 'text';
}

/** One seed of an investigation, as it was read. */
export interface InvestigationSeed {
  index: number;
  /** null when the value is not a recognised identifier: it was searched as text */
  entity: string | null;
  value: string;
  id: string;
  candidates: string[];
  type: EntityType | null;
  /** Which passes actually ran for this seed */
  searched: { exact: boolean; text: boolean };
}

/** One step of a chain of evidence between two seeds. */
export interface ConnectionStep {
  id: string;
  label: string;
  kind: GraphNode['kind'];
  entity: string | null;
  /** The referentials whose rows justify this hop (absent on the first step) */
  via?: string[];
}

/**
 * What ties two seeds together: the value both of them reach, and the shortest chain of evidence through it.
 * Only shared *values* count — two seeds merely living in the same referential is not a link.
 */
export interface Connection {
  seeds: [number, number] | number[];
  through: { id: string; label: string; kind: GraphNode['kind']; entity: string | null };
  hops: number;
  path: ConnectionStep[];
  /** How many values tie this pair, of which `through` is the closest */
  meeting_points: number;
}

/** A value several seeds reach. */
export interface SharedNode {
  id: string;
  kind: GraphNode['kind'];
  entity: string | null;
  label: string;
  seeds: number[];
  referentials: string[];
}

export interface Investigation {
  spec: InvestigationSpec & { values: string[] };
  /** The first seed, kept for a caller written before several were possible */
  seed: InvestigationSeed;
  seeds: InvestigationSeed[];
  nodes: GraphNode[];
  edges: GraphEdge[];
  hits: InvestigationHit[];
  connections: Connection[];
  shared: SharedNode[];
  stats: {
    seeds: number;
    connections: number;
    shared: number;
    referentials_searched: number;
    referentials_matched: number;
    columns_searched: number;
    entities: number;
    sources: number;
    edges: number;
    rows: number;
    queries: number;
    truncated: boolean;
  };
  warnings: string[];
  /** Present when the investigation came from replaying a saved search */
  search?: SavedSearch;
}

/** What this account may do with a saved search. */
export type SearchLevel = 'owner' | 'edit' | 'read';

export interface SearchShare {
  id?: number;
  level: 'read' | 'edit';
  user_id: number | null;
  username?: string | null;
  group_id: number | null;
  group?: string | null;
}

export interface SavedSearch {
  id: number;
  name: string;
  description: string;
  spec: InvestigationSpec;
  tags: string[];
  owner_id: number;
  owner: string | null;
  created_at: string | null;
  updated_at: string | null;
  updated_by: string | null;
  last_run_at: string | null;
  level: SearchLevel;
  shared: boolean;
  /** Only served to the owner and to whoever may edit it */
  shares?: SearchShare[];
}

/** Features off by default until they have earned their place (Settings › Experimental features). */
export interface ExperimentalSettings {
  /** Relational search: the Investigate page, its endpoints and its assistant tools */
  relational_search: boolean;
}

/** What counts as an alert here (Settings › Notifications & alerts). */
export interface AlertsSettings {
  hidden_kinds: AlertKind[];
  min_severity: Severity;
  drift_ratio: number;
  drift_min_rows: number;
}

/** An alert with what it takes to act on it (the report, the logs, the rules, the figures). */
export interface AlertDetail {
  alert: Alert;
  run?: WatchdogRun | Run | null;
  watchdog?: { id: number; name: string; description: string; referential_ids: string[]; trigger_type: string; schedule: string | null } | null;
  referential?: ReferentialSummary | null;
  quality?: QualityResult | null;
  figures?: Record<string, unknown>;
}

export interface SqlResult {
  columns: Column[];
  rows: unknown[][];
  row_count: number;
  truncated: boolean;
  elapsed_ms: number;
}

export interface AssistantStatus {
  available: boolean;
  enabled: boolean;
  configured: boolean;
  model: string | null;
  agent: boolean;
  writes: boolean;
  confidential: boolean;
  /** Scheduled watchdogs are on, and this account may use them (administrators and advanced users) */
  watchdogs: boolean;
  max_steps: number;
  max_chars: number;
  /** Referentials a conversation may be narrowed to */
  max_scope: number;
}

/** A saved connector to an inference server. Its secrets never leave the server. */
export interface LlmProfile {
  id: number;
  name: string;
  description: string;
  /** The one the assistant, the agent and the watchdogs use */
  active: boolean;
  provider: 'ollama' | 'openai';
  base_url: string;
  model: string;
  verify_tls: boolean;
  timeout: number;
  temperature: number;
  top_p: number;
  max_tokens: number;
  context_tokens: number;
  stop: string[];
  options: Record<string, unknown>;
  /** Agent mode adapted to a 1B / 3B model: fewer tools, a shorter prompt, one call at a time */
  small_model: boolean;
  /** An API key is stored (its value is never returned) */
  api_key_set: boolean;
  /** Names of the extra headers; their values stay in the database */
  header_names: string[];
  configured: boolean;
  last_test_at: string | null;
  last_test_ok: boolean | null;
  last_test_error: string | null;
  created_at: string;
  updated_at: string;
  created_by: string | null;
  updated_by: string | null;
}

export interface LlmProfileDraft {
  name: string;
  description: string;
  provider: 'ollama' | 'openai';
  base_url: string;
  model: string;
  verify_tls: boolean;
  timeout: number;
  temperature: number;
  top_p: number;
  max_tokens: number;
  context_tokens: number;
  stop: string[];
  options: Record<string, unknown>;
  small_model: boolean;
  /** Empty keeps the stored key; `api_key_clear` removes it */
  api_key?: string;
  api_key_clear?: boolean;
  /** A value equal to the mask keeps the stored one */
  headers?: Record<string, string>;
}

export interface AssistantSettings {
  enabled: boolean;
  provider: 'ollama' | 'openai';
  base_url: string;
  api_key: string;
  api_key_set?: boolean;
  api_key_clear?: boolean;
  headers: Record<string, string>;
  model: string;
  verify_tls: boolean;
  timeout: number;
  temperature: number;
  top_p: number;
  max_tokens: number;
  context_tokens: number;
  stop: string[];
  options: Record<string, unknown>;
  history_messages: number;
  agent_enabled: boolean;
  max_steps: number;
  allow_writes: boolean;
  allow_confidential: boolean;
  /** Agent mode adapted to a small model; carried by the active profile */
  small_model?: boolean;
  /** Which saved profile the connection values come from (read only) */
  profile_id?: number | null;
  profile_name?: string;
  /** Scheduled watchdogs: off by default, and reserved to administrators and advanced users */
  watchdogs_enabled: boolean;
  groups: string[];
  user_ids: number[];
  system_prompt: string;
  retention_days: number;
  rate_per_minute: number;
}

export interface AgentStep {
  tool: string;
  arguments: Record<string, unknown>;
  ok: boolean;
  summary: string;
  /** The first characters of what the tool answered, kept so the exchange can be read back */
  result?: string;
  /** How long the call took */
  ms?: number | null;
}

export type Artifact =
  | { kind: 'sql'; sql: string; title: string; explanation?: string }
  | { kind: 'file'; filename: string; format: string; content: string };

export interface ChatMessage {
  id: number;
  role: 'user' | 'assistant';
  content: string;
  steps: AgentStep[];
  artifacts: Artifact[];
  error: string | null;
  created_at: string;
}

export interface Conversation {
  id: string;
  title: string;
  model: string | null;
  agent: boolean;
  /** Optional filter: the only referentials the assistant looks at in this conversation */
  referential_ids: string[];
  created_at: string;
  updated_at: string;
  messages: number | null;
}

export interface ConversationDetail extends Omit<Conversation, 'messages'> {
  messages: ChatMessage[];
}

export interface SqlReferentialRequest {
  sql: string;
  id: string;
  name: string;
  description?: string;
  category?: string;
  sources?: string[];
  key?: string | null;
  search_columns?: string[];
  schedule?: string | null;
  pull?: boolean;
}

export interface SqlReferentialCreated {
  id: string;
  sources: string[];
  columns: Column[];
  transform: string;
  run: Run | null;
}

export interface SqlTable {
  table: string;
  ref_id: string;
  name: string;
  row_count: number | null;
  columns: Column[];
  indexes?: Record<string, string>;
  /** Columns the console serves masked, and whether this account may ask for them in clear */
  pii?: PiiState | null;
}

export interface SearchResult {
  id: string;
  name: string;
  category: string;
  key?: string | null;
  total: number;
  /** more matches than `total` (counting stops there) */
  total_capped?: boolean;
  columns: Column[];
  rows: Row[];
  elapsed_ms?: number;
  error: string | null;
}

export interface SystemInfo {
  version: string;
  duckdb_version: string;
  started_at: string;
  data_dir: string;
  config_dir: string;
  timezone: string;
  scheduler_enabled: boolean;
  scheduler_running: boolean;
  max_concurrent_jobs: number;
  refresh_on_startup: string;
  excel_support: boolean;
  limits: Record<string, number>;
  import_dir: string | null;
  import_poll_seconds: number;
  import_unknown_folders: string[];
  public_url: string | null;
  sync_dir: string | null;
  sync_poll_seconds: number;
  sync_settle_seconds: number;
  sync_referentials: string[];
  storage_size: number;
  referential_count: number;
  active_runs: Run[];
  scheduled_jobs: { id: string; name: string; next_run_at: string | null }[];
  config_errors: { file: string; error: string }[];
}

export interface Me {
  /** URL of the MCP data server when it is open to this account */
  mcp_url?: string;
  id: number;
  username: string;
  display_name: string | null;
  email: string | null;
  role: Role;
  is_admin: boolean;
  can_create_internal: boolean;
  can_use_sql: boolean;
  is_service: boolean;
  must_change_password: boolean;
  status: UserStatus;
  auth_source: AuthSource;
  groups: string[];
  auth: 'session' | 'token';
  permissions: Record<string, Access>;
  mfa?: MfaStatus;
  pending_requests?: number;
}

export type UserStatus = 'active' | 'pending' | 'rejected';
export type AuthSource = 'local' | 'ldap' | 'oidc' | 'service';

export interface AdminUser {
  id: number;
  username: string;
  display_name: string | null;
  email: string | null;
  role: Role;
  is_active: boolean;
  must_change_password: boolean;
  locked: boolean;
  groups: { id: number; name: string; source: AuthSource }[];
  last_login_at: string | null;
  password_changed_at: string | null;
  created_at: string;
  token_count: number;
  session_count: number;
  auth_source: AuthSource;
  /** service account: API tokens only, created by administrators */
  is_service: boolean;
  mfa_enabled: boolean;
  token_last_used_at: string | null;
  external_id: string | null;
  status: UserStatus;
  approved_at: string | null;
  approved_by: string | null;
  last_sync_at: string | null;
}

export interface AdminGroup {
  id: number;
  name: string;
  description: string | null;
  created_at: string;
  source: AuthSource;
  external_id: string | null;
  members: { id: number; username: string; display_name: string | null }[];
  grant_count: number;
}

export interface GrantInfo {
  id: number;
  /** Stored scope: a referential id, '*', or 'category:<name>'. */
  referential_id: string;
  referential_name: string | null;
  scope: 'referential' | 'category' | 'all';
  category: string | null;
  /** Referentials the right covers as things stand (a category resolves at each request). */
  covers: string[];
  level: Access;
  subject_type: 'user' | 'group';
  user: { id: number; username: string; display_name: string | null } | null;
  group: { id: number; name: string } | null;
  created_at: string;
  created_by: string | null;
}

export interface ApiTokenInfo {
  id: number;
  name: string;
  prefix: string;
  created_at: string;
  expires_at: string | null;
  last_used_at: string | null;
  expired: boolean;
  token?: string;
}

export interface AuditEntry {
  id: number;
  at: string;
  username: string | null;
  action: string;
  target: string | null;
  success: boolean;
  ip: string | null;
  detail: Record<string, unknown> | null;
}

export interface DownloadFormat {
  format: string;
  label: string;
  ready: boolean;
  size: number | null;
  sha256: string | null;
  generated_at: string | null;
  unavailable_reason: string | null;
  prebuilt: boolean;
}

export interface SourceFile {
  name: string;
  size: number;
  url?: string | null;
}

export interface DownloadsInfo {
  version: number | null;
  data_updated_at: string | null;
  row_count: number | null;
  formats: DownloadFormat[];
  sources: SourceFile[];
  has_previous: boolean;
  /** Bloom filter / MaxMind DB: stable URL of the raw file, for tools */
  raw_url?: string;
}

export interface CatalogEntry extends ReferentialSummary {
  formats: DownloadFormat[];
  sources: SourceFile[];
}

export interface SourceConfigDraft {
  type: 'http' | 'local' | 'git' | 'referential';
  /** unified referential: identifiers of the referentials to combine (the first one gives the rows of an enrichment) */
  referentials?: string[];
  urls: string[];
  headers: Record<string, string>;
  /** HTTP Basic "user:password" (e.g. MaxMind account_id:license_key); ${REFEX_SOURCE_*} variables are expanded */
  basic_auth?: string | null;
  extract: string | null;
  /** New attempts when the remote site fails (connection, timeout, HTTP 408 / 429 / 5xx); empty: server default (5) */
  retries?: number | null;
  path?: string | null;
  /** git: HTTPS URL of the repository, branch / tag / commit, access token (private), user name of the token */
  repository?: string | null;
  ref?: string | null;
  token?: string | null;
  username?: string | null;
}

/** Referential definition as edited in the administration UI. */
export interface DefinitionConfig {
  id: string;
  name: string;
  description: string;
  category: string;
  tags: string[];
  homepage: string | null;
  license: string | null;
  owner: string | null;
  owner_team?: string | null;
  visibility?: Visibility;
  /** Columns holding personal data: masked when served, revealed only on request by an allowed account */
  pii?: { columns: string[]; strategy: PiiStrategy; reveal_groups: string[]; reveal_users: number[]; note: string } | null;
  /** Columns identifying a row when two versions are compared (the key by default) */
  primary_key_columns?: string[];
  source: SourceConfigDraft;
  format: string;
  options: Record<string, unknown>;
  transform: string | null;
  key: string | null;
  search_columns: string[];
  schedule: string | null;
  max_age: string | null;
  validation: { min_rows: number; max_drop_pct: number | null; unique_key: boolean };
  quality?: { rules: QualityRule[] };
  enabled: boolean;
  confidential?: boolean;
  downloads: string[];
  storage: {
    sort_by: string[];
    indexes: string[];
    row_group_size: number;
    keep_raw: boolean;
    keep_previous: boolean;
    profile: 'full' | 'sample' | 'none';
    track_changes: boolean;
    check_key: boolean;
    /** Keep each published version so two of them can be compared (Time travel) */
    time_travel?: boolean;
    max_snapshots?: number;
  };
  /** Bloom filter or SQLite database updated by deltas */
  incremental?: { urls: string[]; format?: 'values' | 'bloom' | 'sql' | null; full_every?: string | null; max_fp_rate?: number | null } | null;
  /** tables: each new data set is merged into the published version (deltas) */
  merge?: { enabled: boolean; delete_column?: string | null } | null;
  /** unified referential: how the referentials of source.referentials are combined */
  combine?: CombineConfig | null;
  federation?: FederationInfo | null;
}

export interface Definition {
  id: string;
  origin: 'file' | 'database' | 'sync';
  config_file: string | null;
  kind: RefKind;
  large: boolean;
  sort_by: string[];
  indexes: string[];
  editable: boolean;
  config: DefinitionConfig;
  summary: ReferentialSummary;
  run?: Run | null;
}

export interface PreviewResult {
  files: (SourceFile & { url?: string })[];
  logs: string[];
  detected_format: string;
  format: string;
  sniff: { delimiter: string; quote: string; has_header: boolean; skip_rows: number; column_count: number } | null;
  records_path_candidates: string[];
  applied_options: Record<string, unknown>;
  sampled: boolean;
  columns: Column[];
  rows: unknown[][];
  total_rows: number | null;
  error: string | null;
  sql: string | null;
  /** Source too large to be analysed before the import: the format and its options are set by hand */
  unanalysed?: string | null;
}

export interface UploadResult {
  upload_id: string;
  files: SourceFile[];
}

// Minimal OpenAPI typing used by the generated documentation
export interface OpenApiParam {
  name: string;
  in: 'query' | 'path';
  required?: boolean;
  description?: string;
  example?: unknown;
  schema: { type?: string | string[]; enum?: string[]; default?: unknown; format?: string; maximum?: number };
}

export interface OpenApiOperation {
  tags?: string[];
  operationId: string;
  summary: string;
  description?: string;
  parameters?: OpenApiParam[];
  requestBody?: { content: Record<string, { example?: unknown; schema?: unknown }> };
  responses: Record<string, { description: string; content?: Record<string, { example?: unknown; schema?: unknown }> }>;
  'x-download'?: boolean;
}

export interface OpenApiSpec {
  info: { title: string; version: string; description?: string };
  paths: Record<string, Record<string, OpenApiOperation>>;
  components: { schemas: Record<string, { title?: string; properties?: Record<string, { type?: string | string[]; format?: string; description?: string; examples?: unknown[]; items?: { type?: string } }> }> };
}

export interface Providers {
  local: boolean;
  ldap: { enabled: boolean; label: string };
  oidc: { enabled: boolean; label: string; login_url: string };
  approval_required: boolean;
}

export interface ProxySettings {
  http_proxy: string;
  https_proxy: string;
  no_proxy: string;
  username: string;
  password: string;
  password_set?: boolean;
  password_clear?: boolean;
  verify_tls: boolean;
  ca_bundle: string;
}

export type LogLevel = 'trace' | 'debug' | 'info' | 'warning' | 'error' | 'critical';

export interface SyslogSettings {
  enabled: boolean;
  host: string;
  port: number;
  protocol: 'udp' | 'tcp' | 'tls';
  framing: 'octet-counting' | 'non-transparent';
  facility: string;
  level: LogLevel;
  send_logs: boolean;
  send_audit: boolean;
  app_name: string;
  hostname: string;
  enterprise_id: string;
  timeout: number;
  verify_tls: boolean;
  use_company_ca: boolean;
  ca_bundle: string;
  client_cert: string;
  client_key: string;
  client_key_set?: boolean;
  client_key_clear?: boolean;
}

export interface SyslogStatus {
  queued: number;
  sent: number;
  dropped: number;
  last_error: string | null;
}

export interface LdapSettings {
  enabled: boolean;
  label: string;
  server_url: string;
  start_tls: boolean;
  verify_tls: boolean;
  timeout: number;
  bind_dn: string;
  bind_password: string;
  bind_password_set?: boolean;
  bind_password_clear?: boolean;
  user_base_dn: string;
  user_filter: string;
  username_attr: string;
  display_name_attr: string;
  email_attr: string;
  group_mode: 'memberof' | 'search' | 'none';
  group_base_dn: string;
  group_filter: string;
  group_name_attr: string;
  group_regex: string;
  admin_group: string;
}

export interface OidcSettings {
  enabled: boolean;
  label: string;
  issuer: string;
  discovery_url: string;
  client_id: string;
  client_secret: string;
  client_secret_set?: boolean;
  client_secret_clear?: boolean;
  scopes: string;
  username_claim: string;
  name_claim: string;
  email_claim: string;
  groups_claim: string;
  strip_group_path: boolean;
  group_regex: string;
  admin_group: string;
  verify_tls: boolean;
}

export interface AllSettings {
  proxy: ProxySettings;
  ldap: LdapSettings;
  oidc: OidcSettings;
  branding: BrandingSettings;
  syslog: SyslogSettings;
  syslog_status: SyslogStatus | null;
  mfa: MfaSettings;
  mcp: McpSettings;
  mcp_url: string;
  mcp_user_url: string;
  smtp: SmtpSettings;
  assistant: AssistantSettings;
  assistant_default_prompt: string;
  assistant_parameters: Record<string, unknown>;
  alerts: AlertsSettings;
  alert_kinds: AlertKind[];
  experimental: ExperimentalSettings;
  local_login: boolean;
  log_level: LogLevel;
  public_url: string | null;
  oidc_redirect_uri: string;
  secret_key_source: 'env' | 'file';
  environment_proxy: Record<string, string>;
}

export interface SmtpSettings {
  host: string;
  port: number;
  security: 'starttls' | 'tls' | 'none';
  username: string;
  password: string;
  password_set?: boolean;
  password_clear?: boolean;
  from_address: string;
  verify_tls: boolean;
  timeout: number;
}

export type NotificationEvent = 'failure' | 'recovered' | 'published' | 'quality';

export interface NotificationChannel {
  id: string;
  name: string;
  type: 'email' | 'webhook';
  enabled: boolean;
  events: NotificationEvent[];
  repeat_failures: boolean;
  referentials: string[];
  categories: string[];
  recipients: string[];
  notify_owner: boolean;
  url: string;
  headers: Record<string, string>;
  signing_secret: string;
  template: string;
}

export interface NotificationDelivery {
  at: string;
  channel_id: string;
  channel: string;
  type: 'email' | 'webhook';
  event: NotificationEvent | 'test';
  referential: string;
  ok: boolean;
  attempts: number;
  detail: string;
}

export interface ConfigImportResult {
  export: { format: string; version: number; exported_at?: string; exported_by?: string; instance?: string | null; credentials?: string } | null;
  plan: {
    id: string | null;
    name?: string | null;
    type?: string;
    action: 'create' | 'update' | 'unchanged' | 'skip' | 'conflict' | 'error';
    reason?: string;
    changed?: string[];
    missing_secrets?: string[];
  }[];
  missing_secrets: { name: string; description: string | null; hosts: string[]; used_by: string[] }[];
  missing_groups: string[];
  grants: number;
  applied: boolean;
  created?: string[];
  updated?: string[];
  errors?: { id: string; error: string }[];
  groups_created?: string[];
  grants_changed?: number;
  runs?: number;
}

export interface EndpointReferential {
  referential: string;
  name: string;
  role: 'source' | 'delta' | 'git' | 'enrichment';
  enabled: boolean;
  schedule: string | null;
  calls_per_day: number | null;
  auth: string[];
  secrets: string[];
  health_status: string | null;
  last_error: string | null;
  last_contact: string | null;
  last_download: string | null;
  downloaded_bytes: number | null;
  not_modified: boolean | null;
}

export interface EndpointHost {
  host: string;
  scheme: string;
  urls: { url: string; referentials: EndpointReferential[]; calls_per_day: number; last_contact: string | null; shared: boolean }[];
  url_count: number;
  referentials: string[];
  referential_count: number;
  calls_per_day: number;
  auth: string[];
  secrets: string[];
  roles: string[];
  errors: string[];
  last_contact: string | null;
  downloaded_bytes: number;
}

export interface EndpointInventory {
  hosts: EndpointHost[];
  others: { kind: string; name: string; url: string; host: string; enabled: boolean; detail?: string; secrets?: string[] }[];
  totals: { hosts: number; urls: number; referentials: number; calls_per_day: number; insecure_hosts: number; shared_urls: number };
  local_sources: string[];
}

export interface McpSettings {
  enabled: boolean;
  account_ids: number[];
  read_only: boolean;
  users_enabled: boolean;
  user_groups: string[];
}

/** Secret of the secret manager: the value is write-only, never returned by the API. */
export interface SourceSecret {
  id: number;
  name: string;
  reference: string;
  description: string | null;
  hosts: string[];
  used_by: string[];
  created_at: string;
  created_by: string | null;
  updated_at: string;
  updated_by: string | null;
}

export interface DiscoverySource {
  type: 'git' | 'http';
  repository?: string | null;
  ref?: string | null;
  path?: string | null;
  token?: string | null;
  username?: string | null;
  url?: string | null;
  pattern?: string | null;
  depth?: number;
  headers?: Record<string, string>;
  basic_auth?: string | null;
}

export interface DiscoveryCandidate {
  path: string;
  url: string | null;
  config: Partial<DefinitionConfig> & { id: string; name: string; format: string };
  duplicate_of: string | null;
  selected: boolean;
  analysis: {
    format?: string | null;
    columns?: string[];
    rows?: unknown[][];
    total_rows?: number | null;
    records_path_candidates?: string[];
    error?: string | null;
  } | null;
}

export interface DiscoveryScan {
  repository?: string;
  ref?: string | null;
  commit?: string;
  folder?: string;
  count: number;
  candidates: DiscoveryCandidate[];
  skipped: string[];
  skipped_count: number;
  analysed: boolean;
  truncated: boolean;
}

export interface DiscoveryApplyResult {
  created: string[];
  errors: { id: string; status: number; error: string }[];
  runs: number;
}

export interface TestStep {
  ok: boolean;
  step: string;
  detail?: string;
}

export interface FacetValue {
  value: string;
  count: number;
}

export interface Facet {
  column: string;
  type: string;
  kind: string;
  values: FacetValue[];
  more: boolean;
}

export interface RangeFacet {
  column: string;
  type: string;
  kind: string;
  min: string | null;
  max: string | null;
}

export interface FacetsResponse {
  disabled?: string;
  total: number | null;
  facets: Facet[];
  ranges: RangeFacet[];
  elapsed_ms: number;
}

export interface BatchLookup {
  found: number;
  missing: string[];
  results: Record<string, Record<string, unknown> | null>;
  column: string;
}

export type ColumnType = 'text' | 'integer' | 'number' | 'boolean' | 'date' | 'datetime' | 'list';

export interface ColumnDef {
  name: string;
  type: ColumnType;
  required: boolean;
  description: string;
  auto: boolean;
}

export type InternalRecordRow = Record<string, unknown> & { _key: string; _updated_at: string; _updated_by: string | null };

export interface RecordsResponse {
  total: number;
  columns: ColumnDef[];
  key: string;
  records: InternalRecordRow[];
  can_edit: boolean;
}

export interface InternalDefinition {
  id: string;
  name: string;
  description: string;
  category: string;
  tags: string[];
  owner?: string | null;
  key: string;
  columns: ColumnDef[];
  search_columns: string[];
  confidential?: boolean;
  renames?: Record<string, string>;
}

export interface BulkResult {
  created: number;
  updated: number;
  unchanged: number;
  deleted: number;
  rows_in_file?: number;
}

export interface BrandingSettings {
  title: string;
  subtitle: string;
}

export interface Branding extends BrandingSettings {
  logo_url: string | null;
  public_url: string;
}

export interface TaskParamDef {
  name: string;
  label: string;
  type: 'int' | 'bool' | 'choice' | 'str';
  default: unknown;
  choices: string[] | null;
  min: number | null;
  max: number | null;
  help: string;
}

export interface TaskRun {
  id: string;
  task: string;
  trigger: 'schedule' | 'manual';
  user: string | null;
  started_at: string;
  finished_at?: string;
  duration?: number;
  status: 'success' | 'warning' | 'error';
  message: string;
  details?: Record<string, unknown>;
}

export interface SystemTask {
  id: string;
  name: string;
  category: 'maintenance' | 'integrity' | 'security' | 'backup' | 'system';
  description: string;
  available: boolean;
  enabled: boolean;
  schedule: string | null;
  default_schedule: string | null;
  interval: number | null;
  params: Record<string, unknown>;
  param_defs: TaskParamDef[];
  next_run_at: string | null;
  running: TaskRun | null;
  last_run: TaskRun | null;
  scheduler_running: boolean;
}

export interface BackupFile {
  name: string;
  size: number;
  encrypted: boolean;
  created_at: string;
}

export interface SecurityKey {
  id: number;
  name: string;
  created_at: string;
  last_used_at: string | null;
}

export interface MfaStatus {
  available: boolean;
  /** at least one second factor (authenticator app or security key) */
  enabled: boolean;
  required: boolean;
  totp: boolean;
  enabled_at: string | null;
  webauthn: SecurityKey[];
  /** https or localhost: browsers refuse WebAuthn elsewhere */
  webauthn_available: boolean | null;
  recovery_codes_left: number;
}

export interface WebauthnOptions {
  options: Record<string, unknown>;
  state: string;
}

export interface MfaSettings {
  mode: 'optional' | 'all' | 'groups';
  groups: string[];
}

export interface MfaEnrolment {
  secret: string;
  otpauth_uri: string;
  qr_svg: string;
}

/** Sign-in answer when a second factor is expected */
export interface MfaChallenge {
  mfa: 'verify' | 'setup';
  mfa_token: string;
  username: string;
  methods: { totp: boolean; webauthn: boolean; recovery_code: boolean };
  webauthn_available: boolean;
}

export interface StorageVolume {
  name: string;
  path: string;
  total: number;
  used: number;
  free: number;
  free_pct: number;
  level: 'ok' | 'warning' | 'critical';
}

export interface StorageReferential {
  id: string;
  name: string | null;
  kind: string | null;
  /** data left by a deleted referential */
  orphan: boolean;
  rows: number | null;
  status: string | null;
  total: number;
  categories: Record<string, number>;
  keep_raw: boolean | null;
  keep_previous: boolean | null;
}

export interface StorageReport {
  computed_at: string;
  duration: number;
  partial: boolean;
  reserve: number;
  volumes: StorageVolume[];
  categories: { id: string; label: string; size: number }[];
  referentials: StorageReferential[];
  system: { name: string; size: number }[];
  outside: { name: string; size: number }[];
  largest_files: { path: string; size: number }[];
  data_total: number;
}
