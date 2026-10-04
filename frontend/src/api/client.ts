import type {
  AdminGroup,
  AdminUser,
  AllSettings,
  ApiTokenInfo,
  AuditEntry,
  BackupFile,
  BatchLookup,
  Branding,
  BulkResult,
  CatalogEntry,
  ChangesResponse,
  ColumnStats,
  Definition,
  DefinitionConfig,
  DownloadsInfo,
  FacetsResponse,
  GrantInfo,
  InternalDefinition,
  InternalRecordRow,
  Me,
  MfaChallenge,
  MfaEnrolment,
  MfaStatus,
  OpenApiSpec,
  PreviewResult,
  Providers,
  RecordsResponse,
  ReferentialDetail,
  ReferentialSummary,
  RowsResponse,
  Run,
  SearchResult,
  SqlResult,
  SqlTable,
  SystemInfo,
  SystemTask,
  TaskRun,
  TestStep,
  UploadResult,
  WebauthnOptions,
} from './types';

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

/** Called on any 401 so that the application can go back to the login page. */
let onUnauthorized: () => void = () => {};
export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    credentials: 'same-origin',
    headers: {
      Accept: 'application/json',
      // Required by the backend on cookie-authenticated mutations (CSRF protection)
      'X-Requested-With': 'RefExposer',
      ...(init?.body && !(init.body instanceof FormData) ? { 'Content-Type': 'application/json' } : {}),
      ...init?.headers,
    },
  });
  if (res.status === 401 && !path.startsWith('/api/auth/login')) onUnauthorized();
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (typeof body.detail === 'string') message = body.detail;
      else if (Array.isArray(body.detail)) message = body.detail.map((d: { msg: string }) => d.msg).join(', ');
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, message);
  }
  return res.json() as Promise<T>;
}

const enc = encodeURIComponent;
const json = (method: string, body?: unknown): RequestInit => ({ method, body: body === undefined ? undefined : JSON.stringify(body) });

export const api = {
  referentials: () => request<ReferentialSummary[]>('/api/referentials'),
  referential: (id: string) => request<ReferentialDetail>(`/api/referentials/${enc(id)}`),
  runs: (id: string, limit = 50) => request<Run[]>(`/api/referentials/${enc(id)}/runs?limit=${limit}`),
  refresh: (id: string, force = false) => request<Run>(`/api/referentials/${enc(id)}/refresh?force=${force}`, json('POST')),
  cancel: (id: string) => request<{ cancelled: boolean }>(`/api/referentials/${enc(id)}/run`, json('DELETE')),
  rows: (id: string, params: URLSearchParams) => request<RowsResponse>(`/api/referentials/${enc(id)}/rows?${params}`),
  columnStats: (id: string, column: string) =>
    request<ColumnStats>(`/api/referentials/${enc(id)}/columns/${enc(column)}/stats`),
  changes: (id: string, kind: string, limit: number, offset: number) =>
    request<ChangesResponse>(`/api/referentials/${enc(id)}/changes?kind=${kind}&limit=${limit}&offset=${offset}`),
  search: (q: string, limit = 5) => request<{ q: string; results: SearchResult[] }>(`/api/search?q=${enc(q)}&limit=${limit}`),
  sql: (sql: string) => request<SqlResult>('/api/sql', json('POST', { sql })),
  sqlSchema: () => request<SqlTable[]>('/api/sql/schema'),
  system: () => request<SystemInfo>('/api/system'),
  reload: () =>
    request<{ loaded: number; errors: { file: string; error: string }[]; removed: string[] }>('/api/system/reload', json('POST')),
  activity: (limit = 30) => request<Run[]>(`/api/activity?limit=${limit}`),

  // Authentication & account
  providers: () => request<Providers>('/api/auth/providers'),
  branding: () => request<Branding>('/api/branding'),
  me: () => request<Me>('/api/auth/me'),
  login: (username: string, password: string) => request<Me | MfaChallenge>('/api/auth/login', json('POST', { username, password })),
  mfaVerify: (mfa_token: string, code: string) => request<Me>('/api/auth/mfa/verify', json('POST', { mfa_token, code })),
  mfaSetup: (mfa_token: string) => request<MfaEnrolment>('/api/auth/mfa/setup', json('POST', { mfa_token })),
  mfaSetupConfirm: (mfa_token: string, code: string) =>
    request<Me & { recovery_codes: string[] }>('/api/auth/mfa/setup/confirm', json('POST', { mfa_token, code })),
  mfaStatus: () => request<MfaStatus>('/api/auth/mfa'),
  mfaWebauthnOptions: (mfa_token: string) => request<WebauthnOptions>('/api/auth/mfa/webauthn/options', json('POST', { mfa_token })),
  mfaWebauthnVerify: (mfa_token: string, state: string, credential: object) =>
    request<Me>('/api/auth/mfa/webauthn/verify', json('POST', { mfa_token, state, credential })),
  mfaSetupWebauthnOptions: (mfa_token: string) => request<WebauthnOptions>('/api/auth/mfa/setup/webauthn/options', json('POST', { mfa_token })),
  mfaSetupWebauthnConfirm: (mfa_token: string, state: string, credential: object, name: string) =>
    request<Me & { recovery_codes: string[] }>('/api/auth/mfa/setup/webauthn/confirm', json('POST', { mfa_token, state, credential, name })),
  mfaWebauthnRegisterOptions: () => request<WebauthnOptions>('/api/auth/mfa/webauthn/register/options', json('POST')),
  mfaWebauthnRegister: (state: string, credential: object, name: string) =>
    request<MfaStatus & { recovery_codes: string[] }>('/api/auth/mfa/webauthn/register', json('POST', { state, credential, name })),
  mfaWebauthnDelete: (id: number) => request<MfaStatus>(`/api/auth/mfa/webauthn/${id}`, json('DELETE')),
  mfaEnroll: () => request<MfaEnrolment>('/api/auth/mfa/enroll', json('POST')),
  mfaEnable: (code: string) => request<MfaStatus & { recovery_codes: string[] }>('/api/auth/mfa/enable', json('POST', { code })),
  mfaDisable: (code: string) => request<MfaStatus>('/api/auth/mfa/disable', json('POST', { code })),
  mfaRecoveryCodes: (code: string) => request<MfaStatus & { recovery_codes: string[] }>('/api/auth/mfa/recovery-codes', json('POST', { code })),
  logout: () => request<{ ok: boolean }>('/api/auth/logout', json('POST')),
  changePassword: (current_password: string, new_password: string) =>
    request<{ ok: boolean }>('/api/auth/password', json('POST', { current_password, new_password })),
  passwordPolicy: () => request<{ min_length: number; min_classes: number }>('/api/auth/password-policy'),
  tokens: () => request<ApiTokenInfo[]>('/api/auth/tokens'),
  createToken: (name: string, expires_in_days: number | null) =>
    request<ApiTokenInfo>('/api/auth/tokens', json('POST', { name, expires_in_days })),
  deleteToken: (id: number) => request<{ ok: boolean }>(`/api/auth/tokens/${id}`, json('DELETE')),

  // Administration
  users: () => request<AdminUser[]>('/api/admin/users'),
  approveUser: (id: number, body: { role?: string; group_ids?: number[] }) => request<AdminUser>(`/api/admin/users/${id}/approve`, json('POST', body)),
  rejectUser: (id: number, reason?: string) => request<AdminUser>(`/api/admin/users/${id}/reject`, json('POST', { reason })),
  settings: () => request<AllSettings>('/api/admin/settings'),
  saveSettings: (section: string, body: object) => request<Record<string, unknown>>(`/api/admin/settings/${section}`, json('PUT', body)),
  testProxy: (config: object, url: string) =>
    request<{ ok: boolean; status: number | null; elapsed_ms: number; route: string; error: string | null }>('/api/admin/settings/proxy/test', json('POST', { config, url })),
  testLdap: (config: object, username?: string, password?: string) =>
    request<{ ok: boolean; steps: TestStep[]; identity?: { dn: string; username: string; display_name: string | null; email: string | null; groups: string[]; admin: boolean } | null }>(
      '/api/admin/settings/ldap/test',
      json('POST', { config, username, password }),
    ),
  testSyslog: (config: object) => request<{ ok: boolean; steps: TestStep[] }>('/api/admin/settings/syslog/test', json('POST', { config })),
  testOidc: (config: object) =>
    request<{ ok: boolean; steps: TestStep[]; redirect_uri: string; endpoints: Record<string, string | null> }>('/api/admin/settings/oidc/test', json('POST', { config })),
  createUser: (body: Record<string, unknown>) => request<AdminUser>('/api/admin/users', json('POST', body)),
  tasks: () => request<SystemTask[]>('/api/admin/tasks'),
  updateTask: (id: string, body: { enabled?: boolean; schedule?: string; params?: Record<string, unknown> }) =>
    request<SystemTask>(`/api/admin/tasks/${id}`, json('PUT', body)),
  runTask: (id: string) => request<SystemTask>(`/api/admin/tasks/${id}/run`, json('POST')),
  taskRuns: (id: string) => request<TaskRun[]>(`/api/admin/tasks/${id}/runs`),
  backups: () => request<BackupFile[]>('/api/admin/backups'),
  deleteBackup: (name: string) => request<{ ok: boolean }>(`/api/admin/backups/${encodeURIComponent(name)}`, json('DELETE')),
  restoreBackup: (name: string, confirm: string) =>
    request<{ restored: Record<string, number> }>(`/api/admin/backups/${encodeURIComponent(name)}/restore`, json('POST', { confirm })),
  restoreBackupUpload: (file: File, confirm: string) => {
    const form = new FormData();
    form.append('file', file);
    form.append('confirm', confirm);
    return request<{ restored: Record<string, number> }>('/api/admin/backups/restore-upload', { method: 'POST', body: form, headers: {} });
  },
  createServiceAccount: (body: { username: string; display_name: string | null; email: string | null; group_ids: number[] }) =>
    request<AdminUser>('/api/admin/service-accounts', json('POST', body)),
  userTokens: (id: number) => request<ApiTokenInfo[]>(`/api/admin/users/${id}/tokens`),
  createUserToken: (id: number, name: string, expires_in_days: number | null) =>
    request<ApiTokenInfo>(`/api/admin/users/${id}/tokens`, json('POST', { name, expires_in_days })),
  deleteUserToken: (id: number, tokenId: number) => request<{ ok: boolean }>(`/api/admin/users/${id}/tokens/${tokenId}`, json('DELETE')),
  updateUser: (id: number, body: Record<string, unknown>) => request<AdminUser>(`/api/admin/users/${id}`, json('PATCH', body)),
  deleteUser: (id: number) => request<{ ok: boolean }>(`/api/admin/users/${id}`, json('DELETE')),
  resetUserMfa: (id: number) => request<AdminUser>(`/api/admin/users/${id}/mfa`, json('DELETE')),
  revokeUserSessions: (id: number) => request<{ ok: boolean }>(`/api/admin/users/${id}/sessions`, json('DELETE')),
  groups: () => request<AdminGroup[]>('/api/admin/groups'),
  createGroup: (body: Record<string, unknown>) => request<AdminGroup>('/api/admin/groups', json('POST', body)),
  updateGroup: (id: number, body: Record<string, unknown>) => request<AdminGroup>(`/api/admin/groups/${id}`, json('PATCH', body)),
  deleteGroup: (id: number) => request<{ ok: boolean }>(`/api/admin/groups/${id}`, json('DELETE')),
  grants: (params: { referential_id?: string; user_id?: number; group_id?: number }) => {
    const p = new URLSearchParams(Object.entries(params).filter(([, v]) => v != null).map(([k, v]) => [k, String(v)]));
    return request<GrantInfo[]>(`/api/admin/grants?${p}`);
  },
  setGrant: (body: { referential_id: string; user_id?: number; group_id?: number; level: string }) =>
    request<GrantInfo>('/api/admin/grants', json('POST', body)),
  deleteGrant: (id: number) => request<{ ok: boolean }>(`/api/admin/grants/${id}`, json('DELETE')),
  audit: (params: URLSearchParams) => request<{ total: number; rows: AuditEntry[] }>(`/api/admin/audit?${params}`),

  // Referential definitions (administration)
  definitions: () => request<Definition[]>('/api/admin/referentials'),
  definition: (id: string) => request<Definition>(`/api/admin/referentials/${enc(id)}`),
  definitionYaml: async (id: string) => {
    const res = await fetch(`/api/admin/referentials/${enc(id)}/yaml`, { credentials: 'same-origin' });
    if (!res.ok) throw new ApiError(res.status, res.statusText);
    return res.text();
  },
  definitionMeta: () =>
    request<{ formats: string[]; categories: string[]; groups: { id: number; name: string }[] }>('/api/admin/referentials/_meta'),
  previewSource: (body: Record<string, unknown>) => request<PreviewResult>('/api/admin/referentials/preview', json('POST', body)),
  createDefinition: (body: { config: Partial<DefinitionConfig>; upload_id?: string | null; pull: boolean; grant_group_ids: number[] }) =>
    request<Definition>('/api/admin/referentials', json('POST', body)),
  updateDefinition: (id: string, body: { config: Partial<DefinitionConfig>; upload_id?: string | null; pull: boolean }) =>
    request<Definition>(`/api/admin/referentials/${enc(id)}`, json('PUT', body)),
  deleteDefinition: (id: string, purge: boolean) => request<{ ok: boolean }>(`/api/admin/referentials/${enc(id)}?purge=${purge}`, json('DELETE')),
  uploadFiles: (files: File[], refId?: string) => {
    const form = new FormData();
    files.forEach((f) => form.append('files', f));
    const path = refId ? `/api/admin/referentials/${enc(refId)}/upload` : '/api/admin/referentials/uploads';
    return request<UploadResult & { run?: Run }>(path, { method: 'POST', body: form, headers: {} });
  },

  // Manual import, frozen versions
  importFiles: (id: string, files: File[], pin: boolean) => {
    const form = new FormData();
    files.forEach((f) => form.append('files', f));
    form.append('pin', String(pin));
    return request<Run>(`/api/referentials/${enc(id)}/import`, { method: 'POST', body: form });
  },
  uploadLogo: (file: File) => {
    const form = new FormData();
    form.append('file', file);
    return request<{ logo_url: string }>('/api/admin/settings/branding/logo', { method: 'POST', body: form });
  },
  deleteLogo: () => request<{ logo_url: null }>('/api/admin/settings/branding/logo', json('DELETE')),
  unpin: (id: string) => request<{ pinned: boolean }>(`/api/referentials/${enc(id)}/pin`, json('DELETE')),

  // Internal referentials
  records: (id: string, params: URLSearchParams) => request<RecordsResponse>(`/api/referentials/${enc(id)}/records?${params}`),
  createRecord: (id: string, record: Record<string, unknown>) =>
    request<InternalRecordRow>(`/api/referentials/${enc(id)}/records`, json('POST', record)),
  replaceRecord: (id: string, key: string, record: Record<string, unknown>) =>
    request<InternalRecordRow>(`/api/referentials/${enc(id)}/records/${enc(key)}`, json('PUT', record)),
  deleteRecord: (id: string, key: string) => request<{ deleted: string }>(`/api/referentials/${enc(id)}/records/${enc(key)}`, json('DELETE')),
  importRecords: (id: string, file: File, mode: 'upsert' | 'replace') => {
    const form = new FormData();
    form.append('file', file);
    form.append('mode', mode);
    return request<BulkResult>(`/api/referentials/${enc(id)}/records/_import`, { method: 'POST', body: form });
  },
  createInternal: (body: InternalDefinition) => request<ReferentialDetail>('/api/internal-referentials', json('POST', body)),
  updateInternal: (id: string, body: InternalDefinition) => request<ReferentialDetail>(`/api/internal-referentials/${enc(id)}`, json('PUT', body)),
  deleteInternal: (id: string) => request<{ ok: boolean }>(`/api/internal-referentials/${enc(id)}`, json('DELETE')),

  // Downloads & generated documentation
  downloads: (id: string) => request<DownloadsInfo>(`/api/referentials/${enc(id)}/downloads`),
  facets: (id: string, params: URLSearchParams) => request<FacetsResponse>(`/api/referentials/${enc(id)}/facets?${params}`),
  lookupBatch: (id: string, values: string[], column?: string | null, enrich = false, flat = false) =>
    request<BatchLookup>(`/api/referentials/${enc(id)}/lookup`, json('POST', { values, column: column || null, enrich, flat })),
  downloadCatalog: () => request<CatalogEntry[]>('/api/downloads'),
  openapi: (id: string) => request<OpenApiSpec>(`/api/referentials/${enc(id)}/openapi.json`),
};

export function downloadUrl(id: string, format: string): string {
  return `/api/referentials/${enc(id)}/download/${enc(format)}`;
}

export function exportUrl(id: string, params: URLSearchParams, format: string): string {
  const p = new URLSearchParams(params);
  p.delete('limit');
  p.delete('offset');
  p.set('format', format);
  return `/api/referentials/${enc(id)}/export?${p}`;
}

// Public URL of the application (REFEX_PUBLIC_URL), used in the URLs and commands shown to users
let publicBase = '';

export function setPublicBase(url: string | null | undefined) {
  publicBase = (url ?? '').replace(/\/+$/, '');
}

export function absoluteUrl(path: string): string {
  return `${publicBase || window.location.origin}${path}`;
}
