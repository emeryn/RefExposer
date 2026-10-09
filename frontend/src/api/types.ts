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
  logs?: RunLog[];
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
  format: string;
  source_type: 'http' | 'local' | 'internal' | 'sync';
  origin: 'file' | 'database' | 'sync';
  config_file: string | null;
  kind: 'table' | 'bloom' | 'mmdb' | 'internal';
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
  access: Access | null;
  pinned: { by: string | null; at: string; reason?: string } | null;
  /** data encrypted at rest; `encrypted`: state of the published version (until the next publication) */
  confidential: boolean;
  encrypted: boolean;
  manual_import: { origin: 'upload' | 'inbox'; by: string | null; at: string; files: string[]; run: string } | null;
  import_folder: string | null;
  sync_path: string | null;
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

export interface SqlResult {
  columns: Column[];
  rows: unknown[][];
  row_count: number;
  truncated: boolean;
  elapsed_ms: number;
}

export interface SqlTable {
  table: string;
  ref_id: string;
  name: string;
  row_count: number | null;
  columns: Column[];
  indexes?: Record<string, string>;
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
  referential_id: string;
  referential_name: string | null;
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
  type: 'http' | 'local' | 'git';
  urls: string[];
  headers: Record<string, string>;
  /** HTTP Basic "user:password" (e.g. MaxMind account_id:license_key); ${REFEX_SOURCE_*} variables are expanded */
  basic_auth?: string | null;
  extract: string | null;
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
  source: SourceConfigDraft;
  format: string;
  options: Record<string, unknown>;
  transform: string | null;
  key: string | null;
  search_columns: string[];
  schedule: string | null;
  max_age: string | null;
  validation: { min_rows: number; max_drop_pct: number | null; unique_key: boolean };
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
  };
  /** Bloom filter or SQLite database updated by deltas */
  incremental?: { urls: string[]; format?: 'values' | 'bloom' | 'sql' | null; full_every?: string | null; max_fp_rate?: number | null } | null;
}

export interface Definition {
  id: string;
  origin: 'file' | 'database' | 'sync';
  config_file: string | null;
  kind: 'table' | 'bloom' | 'mmdb' | 'internal';
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
  smtp: SmtpSettings;
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

export type NotificationEvent = 'failure' | 'recovered' | 'published';

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

export interface McpSettings {
  enabled: boolean;
  account_ids: number[];
  read_only: boolean;
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
