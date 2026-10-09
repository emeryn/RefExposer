import {
  Alert,
  Badge,
  Button,
  Card,
  Checkbox,
  Code,
  Divider,
  FileButton,
  Grid,
  Group,
  Image,
  List,
  MultiSelect,
  Select,
  Loader,
  NumberInput,
  PasswordInput,
  SegmentedControl,
  SimpleGrid,
  Stack,
  Switch,
  Tabs,
  Text,
  Textarea,
  TextInput,
  Title,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconBrush, IconCheck, IconFileText, IconKey, IconMail, IconRobot, IconShieldLock, IconNetwork, IconPhotoUp, IconPlugConnected, IconSitemap, IconTrash, IconX } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState, type ReactNode } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api } from '../../api/client';
import { useBranding } from '../../api/hooks';
import type { BrandingSettings, LdapSettings, LogLevel, McpSettings, SmtpSettings, MfaSettings, OidcSettings, ProxySettings, SyslogSettings, SyslogStatus, TestStep } from '../../api/types';
import { BrandHeader } from '../../components/Brand';
import { CodeSnippet, CopyIcon } from '../../components/Common';

const MONO = { input: { fontFamily: 'var(--mantine-font-family-monospace)' } };

function useSave(section: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: object) => api.saveSettings(section, body),
    onSuccess: () => {
      notifications.show({ message: 'Settings saved', color: 'teal' });
      qc.invalidateQueries({ queryKey: ['settings'] });
      qc.invalidateQueries({ queryKey: ['providers'] });
    },
    onError: (e: Error) => notifications.show({ title: 'Cannot save', message: e.message, color: 'red', autoClose: 10000 }),
  });
}

function Steps({ steps }: { steps: TestStep[] }) {
  return (
    <List spacing={4} size="sm" center>
      {steps.map((s, i) => (
        <List.Item key={i} icon={s.ok ? <IconCheck size={16} color="var(--mantine-color-teal-6)" /> : <IconX size={16} color="var(--mantine-color-red-6)" />}>
          <Text size="sm" fw={500} span>
            {s.step}
          </Text>
          {s.detail && (
            <Text size="xs" c="dimmed" style={{ wordBreak: 'break-word' }}>
              {s.detail}
            </Text>
          )}
        </List.Item>
      ))}
    </List>
  );
}

function SecretInput({
  label,
  description,
  value,
  isSet,
  clear,
  onChange,
  onClear,
}: {
  label: string;
  description?: string;
  value: string;
  isSet?: boolean;
  clear?: boolean;
  onChange: (v: string) => void;
  onClear: (v: boolean) => void;
}) {
  return (
    <Stack gap={4}>
      <PasswordInput
        label={label}
        description={description}
        placeholder={isSet && !clear ? '•••••••• (saved, leave empty to keep it)' : ''}
        value={value}
        onChange={(e) => onChange(e.currentTarget.value)}
        disabled={clear}
        autoComplete="new-password"
        rightSectionWidth={36}
      />
      {isSet && <Checkbox size="xs" label="Clear the saved value" checked={!!clear} onChange={(e) => onClear(e.currentTarget.checked)} />}
    </Stack>
  );
}

function Section({ title, children, icon }: { title: string; children: ReactNode; icon?: ReactNode }) {
  return (
    <Card padding="lg">
      <Group gap="xs" mb="sm">
        {icon}
        <Title order={5}>{title}</Title>
      </Group>
      <Stack gap="sm">{children}</Stack>
    </Card>
  );
}

// ------------------------------------------------------------------ proxy

function ProxyTab({ initial, envProxy }: { initial: ProxySettings; envProxy: Record<string, string> }) {
  const [cfg, setCfg] = useState(initial);
  const [url, setUrl] = useState('https://www.cisa.gov/');
  useEffect(() => setCfg(initial), [initial]);
  const save = useSave('proxy');
  const test = useMutation({ mutationFn: () => api.testProxy(cfg, url) });
  const set = <K extends keyof ProxySettings>(k: K, v: ProxySettings[K]) => setCfg((c) => ({ ...c, [k]: v }));

  return (
    <Grid gutter="md">
      <Grid.Col span={{ base: 12, lg: 7 }}>
        <Stack>
          <Section title="Outgoing proxy" icon={<IconNetwork size={18} />}>
            <Text size="sm" c="dimmed">
              Used to download the referentials and to reach the OpenID Connect provider. Without a proxy configured here, the
              <Code>HTTP_PROXY</Code> / <Code>HTTPS_PROXY</Code> environment variables of the container apply.
              {Object.keys(envProxy).length > 0 && (
                <>
                  {' '}
                  Currently: {Object.entries(envProxy).map(([k, v]) => (
                    <Code key={k}>
                      {k}={v}
                    </Code>
                  ))}
                </>
              )}
            </Text>
            <SimpleGrid cols={{ base: 1, sm: 2 }}>
              <TextInput label="Proxy HTTP" placeholder="http://proxy.example.com:3128" value={cfg.http_proxy} onChange={(e) => set('http_proxy', e.currentTarget.value)} />
              <TextInput label="Proxy HTTPS" placeholder="http://proxy.example.com:3128" value={cfg.https_proxy} onChange={(e) => set('https_proxy', e.currentTarget.value)} />
            </SimpleGrid>
            <TextInput
              label="Exceptions (no proxy)"
              description="Comma-separated hosts or domains (e.g. localhost, .intranet, keycloak)"
              value={cfg.no_proxy}
              onChange={(e) => set('no_proxy', e.currentTarget.value)}
            />
            <SimpleGrid cols={{ base: 1, sm: 2 }}>
              <TextInput label="Proxy user (optional)" value={cfg.username} onChange={(e) => set('username', e.currentTarget.value)} autoComplete="off" />
              <SecretInput
                label="Proxy password"
                value={cfg.password}
                isSet={cfg.password_set}
                clear={cfg.password_clear}
                onChange={(v) => set('password', v)}
                onClear={(v) => set('password_clear', v)}
              />
            </SimpleGrid>
          </Section>
          <Section title="TLS certificates">
            <Switch label="Verify server certificates (recommended)" checked={cfg.verify_tls} onChange={(e) => set('verify_tls', e.currentTarget.checked)} />
            <Textarea
              label="Additional certificate authorities (PEM)"
              description="E.g. the root certificate of your company or of a TLS-inspecting proxy. Also used for LDAPS."
              placeholder="-----BEGIN CERTIFICATE-----"
              autosize
              minRows={3}
              maxRows={10}
              styles={MONO}
              value={cfg.ca_bundle}
              onChange={(e) => set('ca_bundle', e.currentTarget.value)}
            />
          </Section>
          <Group>
            <Button onClick={() => save.mutate(cfg)} loading={save.isPending}>
              Save
            </Button>
          </Group>
        </Stack>
      </Grid.Col>
      <Grid.Col span={{ base: 12, lg: 5 }}>
        <Section title="Test access" icon={<IconPlugConnected size={18} />}>
          <Text size="sm" c="dimmed">
            Tests the values of the form (even unsaved).
          </Text>
          <TextInput label="URL" value={url} onChange={(e) => setUrl(e.currentTarget.value)} />
          <Button variant="light" onClick={() => test.mutate()} loading={test.isPending} w="fit-content">
            Test
          </Button>
          {test.data && (
            <Alert color={test.data.ok ? 'teal' : 'red'} icon={test.data.ok ? <IconCheck /> : <IconX />}>
              <Text size="sm">
                {test.data.ok ? `HTTP ${test.data.status} in ${test.data.elapsed_ms} ms` : test.data.error ?? `HTTP ${test.data.status}`}
              </Text>
              <Text size="xs" c="dimmed">
                Route: {test.data.route}
              </Text>
            </Alert>
          )}
          {test.error && <Alert color="red">{(test.error as Error).message}</Alert>}
        </Section>
      </Grid.Col>
    </Grid>
  );
}

// ------------------------------------------------------------------ second factor

function MfaTab({ initial, ssoOnly }: { initial: MfaSettings; ssoOnly: boolean }) {
  const [cfg, setCfg] = useState(initial);
  useEffect(() => setCfg(initial), [initial]);
  const save = useSave('mfa');
  const { data: groups } = useQuery({ queryKey: ['admin-groups'], queryFn: api.groups });
  return (
    <Grid gutter="md">
      <Grid.Col span={{ base: 12, lg: 7 }}>
        <Stack>
          <Section title="Two-factor authentication (TOTP)" icon={<IconShieldLock size={18} />}>
            <Text size="sm" c="dimmed">
              Local and LDAP accounts can add a code of an authenticator app (Google Authenticator, Microsoft Authenticator,
              FreeOTP…) after their password, from <b>My account</b>. OpenID Connect accounts get their second factor from the
              identity provider; service accounts only use API tokens.
            </Text>
            {ssoOnly && (
              <Alert color="gray" p="xs">
                <Text size="xs">
                  Inactive: single sign-on is the only way to sign in (REFEX_LOCAL_LOGIN=false and no LDAP directory).
                </Text>
              </Alert>
            )}
            <SegmentedControl
              value={cfg.mode}
              onChange={(v) => setCfg((c) => ({ ...c, mode: v as MfaSettings['mode'] }))}
              data={[
                { value: 'optional', label: 'Optional' },
                { value: 'groups', label: 'Required for some groups' },
                { value: 'all', label: 'Required for everybody' },
              ]}
            />
            {cfg.mode === 'groups' && (
              <MultiSelect
                label="Groups"
                description="Members of these groups must use a second factor (local and synchronized groups)."
                data={(groups ?? []).map((g) => g.name)}
                value={cfg.groups}
                onChange={(v) => setCfg((c) => ({ ...c, groups: v }))}
                searchable
              />
            )}
            <Text size="xs" c="dimmed">
              When it is required, accounts without a second factor enrol it at their next sign-in (QR code, then recovery codes).
              A lost phone: reset the second factor of the account from Administration › Users.
            </Text>
            <Group>
              <Button onClick={() => save.mutate(cfg)} loading={save.isPending}>
                Save
              </Button>
            </Group>
          </Section>
        </Stack>
      </Grid.Col>
    </Grid>
  );
}

// ------------------------------------------------------------------ e-mail (SMTP)

function SmtpTab({ initial }: { initial: SmtpSettings }) {
  const [cfg, setCfg] = useState(initial);
  useEffect(() => setCfg(initial), [initial]);
  const set = <K extends keyof SmtpSettings>(k: K, v: SmtpSettings[K]) => setCfg((c) => ({ ...c, [k]: v }));
  const save = useSave('smtp');
  const [to, setTo] = useState('');
  const test = useMutation({ mutationFn: () => api.testSmtp(cfg, to) });
  return (
    <Grid gutter="md">
      <Grid.Col span={{ base: 12, lg: 7 }}>
        <Stack>
          <Section title="Outgoing e-mail server (SMTP)" icon={<IconMail size={18} />}>
            <Text size="sm" c="dimmed">
              Used by the e-mail channels of <Link to="/admin/notifications">Administration › Notifications</Link> (failure of a referential,
              recovery, new version).
            </Text>
            <SimpleGrid cols={{ base: 1, sm: 3 }}>
              <TextInput label="Server" placeholder="smtp.example.com" value={cfg.host} onChange={(e) => set('host', e.currentTarget.value)} />
              <NumberInput label="Port" min={1} max={65535} value={cfg.port} onChange={(v) => set('port', Number(v) || 587)} />
              <Select
                label="Security"
                data={[
                  { value: 'starttls', label: 'STARTTLS (587)' },
                  { value: 'tls', label: 'TLS (465)' },
                  { value: 'none', label: 'None (internal relay)' },
                ]}
                value={cfg.security}
                onChange={(v) => {
                  const security = (v ?? 'starttls') as SmtpSettings['security'];
                  setCfg((c) => ({ ...c, security, port: security === 'tls' ? 465 : security === 'starttls' ? 587 : 25 }));
                }}
              />
            </SimpleGrid>
            <TextInput
              label="Sender"
              placeholder="RefExposer <refexposer@example.com>"
              value={cfg.from_address}
              onChange={(e) => set('from_address', e.currentTarget.value)}
            />
            <SimpleGrid cols={{ base: 1, sm: 2 }}>
              <TextInput label="User name" description="Empty: no authentication" value={cfg.username} onChange={(e) => set('username', e.currentTarget.value)} autoComplete="off" />
              <SecretInput
                label="Password"
                value={cfg.password}
                isSet={cfg.password_set}
                clear={cfg.password_clear}
                onChange={(v) => set('password', v)}
                onClear={(v) => set('password_clear', v)}
              />
            </SimpleGrid>
            <Switch
              label="Verify the certificate of the server"
              description="The company certificate authorities of Network & proxy are trusted too"
              checked={cfg.verify_tls}
              onChange={(e) => set('verify_tls', e.currentTarget.checked)}
            />
            <Group>
              <Button onClick={() => save.mutate(cfg)} loading={save.isPending}>
                Save
              </Button>
            </Group>
          </Section>
        </Stack>
      </Grid.Col>
      <Grid.Col span={{ base: 12, lg: 5 }}>
        <Section title="Test" icon={<IconPlugConnected size={18} />}>
          <Text size="sm" c="dimmed">
            Sends a test e-mail with the settings of the form (saved or not).
          </Text>
          <Group align="flex-end">
            <TextInput label="Recipient" placeholder="me@example.com" value={to} onChange={(e) => setTo(e.currentTarget.value)} style={{ flex: 1 }} />
            <Button variant="light" onClick={() => test.mutate()} loading={test.isPending} disabled={!to.trim() || !cfg.host}>
              Send
            </Button>
          </Group>
          {test.data && (
            <Alert color={test.data.ok ? 'teal' : 'red'} p="xs">
              <Text size="sm">{test.data.ok ? `Sent in ${test.data.elapsed_ms} ms` : test.data.error}</Text>
            </Alert>
          )}
          {test.error && (
            <Alert color="red" p="xs">
              <Text size="sm">{(test.error as Error).message}</Text>
            </Alert>
          )}
        </Section>
      </Grid.Col>
    </Grid>
  );
}

// ------------------------------------------------------------------ MCP server

function McpTab({ initial, url }: { initial: McpSettings; url: string }) {
  const [cfg, setCfg] = useState(initial);
  useEffect(() => setCfg(initial), [initial]);
  const save = useSave('mcp');
  const { data: users } = useQuery({ queryKey: ['admin-users'], queryFn: api.users });
  const services = (users ?? []).filter((u) => u.is_service);
  const claude = `claude mcp add --transport http refexposer ${url} --header "Authorization: Bearer $REFEX_MCP_TOKEN"`;
  const json = JSON.stringify(
    { mcpServers: { refexposer: { type: 'http', url, headers: { Authorization: 'Bearer ${REFEX_MCP_TOKEN}' } } } },
    null,
    2,
  );
  return (
    <Grid gutter="md">
      <Grid.Col span={{ base: 12, lg: 7 }}>
        <Section title="Administration MCP server" icon={<IconRobot size={18} />}>
          <Text size="sm" c="dimmed">
            An AI agent (Claude Code, Claude Desktop, any MCP client) drives RefExposer through the Model Context Protocol: analyse
            sources, create and update referentials, follow imports, manage secrets, bulk discovery, users, rights… It authenticates
            with an API token of a <b>service account</b> chosen below. Through this server only, the account acts as an
            administrator: its token alone keeps its usual rights, and disabling the server withdraws them at once. Every action is
            recorded in the audit log with <Code>via: mcp</Code>.
          </Text>
          <Switch label="Enable the MCP server" checked={cfg.enabled} onChange={(e) => setCfg((c) => ({ ...c, enabled: e.currentTarget.checked }))} />
          <MultiSelect
            label="Service accounts allowed"
            description={
              <>
                Create a service account and its API token in <Link to="/admin/users">Users</Link> (service accounts never sign in to the
                interface).
              </>
            }
            data={services.map((u) => ({ value: String(u.id), label: u.display_name ? `${u.username} — ${u.display_name}` : u.username }))}
            value={cfg.account_ids.map(String)}
            onChange={(v) => setCfg((c) => ({ ...c, account_ids: v.map(Number) }))}
            placeholder={services.length ? 'Choose…' : 'No service account yet'}
            searchable
          />
          <Switch
            label="Read-only"
            description="Only the tools that read (state, data, SQL, preview, scan): no creation, change, deletion or update."
            checked={cfg.read_only}
            onChange={(e) => setCfg((c) => ({ ...c, read_only: e.currentTarget.checked }))}
          />
          {cfg.enabled && !cfg.account_ids.length && (
            <Alert color="orange" p="xs">
              <Text size="xs">No service account allowed: nobody can use the server.</Text>
            </Alert>
          )}
          <Group>
            <Button onClick={() => save.mutate(cfg)} loading={save.isPending}>
              Save
            </Button>
          </Group>
        </Section>
      </Grid.Col>
      <Grid.Col span={{ base: 12, lg: 5 }}>
        <Section title="Connect a client" icon={<IconPlugConnected size={18} />}>
          <Group gap={4}>
            <Text size="sm">Server URL (Streamable HTTP):</Text>
            <Code>{url}</Code>
            <CopyIcon value={url} />
          </Group>
          <Text size="xs" c="dimmed">
            Claude Code (the token in the REFEX_MCP_TOKEN variable):
          </Text>
          <CodeSnippet code={claude} />
          <Text size="xs" c="dimmed">
            Clients configured with JSON (.mcp.json, Claude Desktop…):
          </Text>
          <CodeSnippet code={json} />
          <Text size="xs" c="dimmed">
            Secrets stay write-only: the agent can create or replace a secret, never read one back. Restrict each secret to its hosts so
            that no source pointed elsewhere can receive it.
          </Text>
        </Section>
      </Grid.Col>
    </Grid>
  );
}

// ------------------------------------------------------------------ logging (syslog)

const FACILITIES = ['kern', 'user', 'mail', 'daemon', 'auth', 'syslog', 'lpr', 'news', 'uucp', 'cron', 'authpriv', 'ftp', 'ntp', 'security',
  'console', 'solaris-cron', 'local0', 'local1', 'local2', 'local3', 'local4', 'local5', 'local6', 'local7'];
const LEVELS: { value: LogLevel; label: string }[] = [
  { value: 'trace', label: 'Trace (+ SQL queries, HTTP details)' },
  { value: 'debug', label: 'Debug' },
  { value: 'info', label: 'Info' },
  { value: 'warning', label: 'Warning' },
  { value: 'error', label: 'Error' },
  { value: 'critical', label: 'Critical' },
];
const DEFAULT_PORTS = { udp: 514, tcp: 514, tls: 6514 } as const;

function SyslogTab({ initial, status, logLevel, companyCa }: { initial: SyslogSettings; status: SyslogStatus | null; logLevel: LogLevel; companyCa: boolean }) {
  const [cfg, setCfg] = useState(initial);
  useEffect(() => setCfg(initial), [initial]);
  const save = useSave('syslog');
  const test = useMutation({ mutationFn: () => api.testSyslog(cfg) });
  const set = <K extends keyof SyslogSettings>(k: K, v: SyslogSettings[K]) => setCfg((c) => ({ ...c, [k]: v }));
  const setProtocol = (p: SyslogSettings['protocol']) =>
    setCfg((c) => ({ ...c, protocol: p, port: c.port === DEFAULT_PORTS[c.protocol] ? DEFAULT_PORTS[p] : c.port }));

  return (
    <Grid gutter="md">
      <Grid.Col span={{ base: 12, lg: 7 }}>
        <Stack>
          <Section title="Container logs" icon={<IconFileText size={18} />}>
            <Text size="sm">
              Current level: <Badge variant="light">{logLevel}</Badge>
            </Text>
            <Text size="sm" c="dimmed">
              Set with the <Code>REFEX_LOG_LEVEL</Code> variable (<Code>trace</Code>, <Code>debug</Code>, <Code>info</Code>,{' '}
              <Code>warning</Code>, <Code>error</Code>, <Code>critical</Code>) in <Code>.env</Code>, then restart the container.{' '}
              <Code>trace</Code> adds the generated SQL queries and the HTTP details.
            </Text>
          </Section>
          <Section title="Syslog forwarding (RFC 5424)" icon={<IconNetwork size={18} />}>
            <Switch label="Send to a syslog collector" checked={cfg.enabled} onChange={(e) => set('enabled', e.currentTarget.checked)} />
            <Text size="sm" c="dimmed">
              Messages follow RFC 5424 (UTC timestamps with microseconds, structured data), over UDP (RFC 5426), TCP (RFC 6587) or TLS (RFC
              5425). They are sent in the background: an unreachable collector never slows the application down.
            </Text>
            <SimpleGrid cols={{ base: 1, sm: 3 }}>
              <TextInput label="Collector" placeholder="rsyslog.example.com" value={cfg.host} onChange={(e) => set('host', e.currentTarget.value)} />
              <NumberInput label="Port" min={1} max={65535} value={cfg.port} onChange={(v) => set('port', Number(v) || DEFAULT_PORTS[cfg.protocol])} />
              <div>
                <Text size="sm" fw={500} mb={4}>
                  Transport
                </Text>
                <SegmentedControl
                  fullWidth
                  value={cfg.protocol}
                  onChange={(v) => setProtocol(v as SyslogSettings['protocol'])}
                  data={[
                    { value: 'udp', label: 'UDP' },
                    { value: 'tcp', label: 'TCP' },
                    { value: 'tls', label: 'TLS' },
                  ]}
                />
              </div>
            </SimpleGrid>
            {cfg.protocol === 'tcp' && (
              <Select
                label="TCP framing (RFC 6587)"
                value={cfg.framing}
                onChange={(v) => v && set('framing', v as SyslogSettings['framing'])}
                data={[
                  { value: 'octet-counting', label: 'Octet counting (recommended, multi-line messages)' },
                  { value: 'non-transparent', label: 'Line feed after each message (legacy collectors)' },
                ]}
              />
            )}
            <SimpleGrid cols={{ base: 1, sm: 2 }}>
              <Select label="Facility" data={FACILITIES} value={cfg.facility} onChange={(v) => v && set('facility', v)} searchable />
              <Select
                label="Minimum level of the application logs"
                data={LEVELS}
                value={cfg.level}
                onChange={(v) => v && set('level', v as LogLevel)}
                disabled={!cfg.send_logs}
              />
            </SimpleGrid>
            <Group>
              <Switch label="Application logs" checked={cfg.send_logs} onChange={(e) => set('send_logs', e.currentTarget.checked)} />
              <Switch label="Audit trail (structured data)" checked={cfg.send_audit} onChange={(e) => set('send_audit', e.currentTarget.checked)} />
            </Group>
            <SimpleGrid cols={{ base: 1, sm: 3 }}>
              <TextInput label="APP-NAME" value={cfg.app_name} onChange={(e) => set('app_name', e.currentTarget.value)} />
              <TextInput label="HOSTNAME" placeholder="container name" value={cfg.hostname} onChange={(e) => set('hostname', e.currentTarget.value)} />
              <TextInput
                label="Enterprise number"
                description="SD-ID: audit@…, log@…"
                value={cfg.enterprise_id}
                onChange={(e) => set('enterprise_id', e.currentTarget.value)}
              />
            </SimpleGrid>
          </Section>
          {cfg.protocol === 'tls' && (
            <Section title="TLS">
              <Switch label="Verify the collector certificate (recommended)" checked={cfg.verify_tls} onChange={(e) => set('verify_tls', e.currentTarget.checked)} />
              <Textarea
                label="Root CA of the collector (PEM)"
                description="Certificate authority that signed the certificate of the collector (several certificates allowed)."
                placeholder="-----BEGIN CERTIFICATE-----"
                autosize
                minRows={3}
                maxRows={10}
                styles={MONO}
                value={cfg.ca_bundle}
                onChange={(e) => set('ca_bundle', e.currentTarget.value)}
              />
              <Switch
                label="Also trust the company CAs of the network settings"
                description={companyCa ? undefined : 'None configured in Network & proxy'}
                checked={cfg.use_company_ca}
                onChange={(e) => set('use_company_ca', e.currentTarget.checked)}
              />
              <Divider label="Client certificate (mutual TLS, optional)" labelPosition="left" />
              <Textarea
                label="Client certificate (PEM)"
                placeholder="-----BEGIN CERTIFICATE-----"
                autosize
                minRows={2}
                maxRows={8}
                styles={MONO}
                value={cfg.client_cert}
                onChange={(e) => set('client_cert', e.currentTarget.value)}
              />
              <SecretInput
                label="Private key (PEM, unencrypted)"
                value={cfg.client_key}
                isSet={cfg.client_key_set}
                clear={cfg.client_key_clear}
                onChange={(v) => set('client_key', v)}
                onClear={(v) => set('client_key_clear', v)}
              />
            </Section>
          )}
          <Group>
            <Button onClick={() => save.mutate(cfg)} loading={save.isPending}>
              Save
            </Button>
          </Group>
        </Stack>
      </Grid.Col>
      <Grid.Col span={{ base: 12, lg: 5 }}>
        <Stack>
          <Section title="Test" icon={<IconPlugConnected size={18} />}>
            <Text size="sm" c="dimmed">
              Connects with the values of the form (even unsaved) and sends one test message.
            </Text>
            <Button variant="light" onClick={() => test.mutate()} loading={test.isPending} w="fit-content" disabled={!cfg.host}>
              Send a test message
            </Button>
            {test.data && (
              <Alert color={test.data.ok ? 'teal' : 'red'}>
                <Steps steps={test.data.steps} />
              </Alert>
            )}
            {test.error && <Alert color="red">{(test.error as Error).message}</Alert>}
          </Section>
          {status && (
            <Section title="Connector status">
              <Text size="sm">
                {status.sent.toLocaleString()} sent · {status.queued.toLocaleString()} waiting · {status.dropped.toLocaleString()} dropped
              </Text>
              {status.last_error && (
                <Alert color="orange" p="xs">
                  <Text size="xs">Collector unreachable, retrying: {status.last_error}</Text>
                </Alert>
              )}
            </Section>
          )}
          <Section title="Example">
            <Code block fz={11} style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
              {`<${(FACILITIES.indexOf(cfg.facility) * 8 + 5)}>1 2026-10-03T08:15:02.123456Z ${cfg.hostname || 'refex-backend'} ${cfg.app_name || 'refexposer'} 7 auth.login [audit@${cfg.enterprise_id} user="alice" action="auth.login" target="-" success="true" ip="10.0.0.5"][origin software="RefExposer" swVersion="…"] auth.login {"provider":"ldap"}`}
            </Code>
          </Section>
        </Stack>
      </Grid.Col>
    </Grid>
  );
}

// ------------------------------------------------------------------ LDAP

const LDAP_PRESETS: Record<string, Partial<LdapSettings>> = {
  openldap: {
    user_filter: '(&(objectClass=inetOrgPerson)(uid={username}))',
    username_attr: 'uid',
    display_name_attr: 'cn',
    email_attr: 'mail',
    group_mode: 'search',
    group_filter: '(&(objectClass=groupOfNames)(member={user_dn}))',
    group_name_attr: 'cn',
  },
  ad: {
    user_filter: '(&(objectClass=user)(sAMAccountName={username}))',
    username_attr: 'sAMAccountName',
    display_name_attr: 'displayName',
    email_attr: 'mail',
    group_mode: 'memberof',
    group_name_attr: 'cn',
  },
};

function LdapTab({ initial }: { initial: LdapSettings }) {
  const [cfg, setCfg] = useState(initial);
  const [user, setUser] = useState('');
  const [pass, setPass] = useState('');
  useEffect(() => setCfg(initial), [initial]);
  const save = useSave('ldap');
  const test = useMutation({ mutationFn: (withUser: boolean) => api.testLdap(cfg, withUser ? user : undefined, withUser ? pass : undefined) });
  const set = <K extends keyof LdapSettings>(k: K, v: LdapSettings[K]) => setCfg((c) => ({ ...c, [k]: v }));

  return (
    <Grid gutter="md">
      <Grid.Col span={{ base: 12, lg: 7 }}>
        <Stack>
          <Section title="LDAP directory / Active Directory" icon={<IconSitemap size={18} />}>
            <Switch label="Allow sign-in with a directory account" checked={cfg.enabled} onChange={(e) => set('enabled', e.currentTarget.checked)} />
            <Text size="xs" c="dimmed">
              At the first sign-in, a pending account is created: an administrator must approve it. Directory groups are
              synchronized at each sign-in and can be given rights on referentials.
            </Text>
            <Group gap="xs">
              <Text size="sm">Preset:</Text>
              <Button size="compact-xs" variant="default" onClick={() => setCfg((c) => ({ ...c, ...LDAP_PRESETS.openldap }))}>
                OpenLDAP
              </Button>
              <Button size="compact-xs" variant="default" onClick={() => setCfg((c) => ({ ...c, ...LDAP_PRESETS.ad }))}>
                Active Directory
              </Button>
            </Group>
            <SimpleGrid cols={{ base: 1, sm: 2 }}>
              <TextInput label="Label on the sign-in page" value={cfg.label} onChange={(e) => set('label', e.currentTarget.value)} />
              <TextInput label="Server" placeholder="ldaps://ldap.example.com:636" value={cfg.server_url} onChange={(e) => set('server_url', e.currentTarget.value)} />
            </SimpleGrid>
            <Group>
              <Switch size="sm" label="StartTLS" checked={cfg.start_tls} onChange={(e) => set('start_tls', e.currentTarget.checked)} />
              <Switch size="sm" label="Verify the certificate" checked={cfg.verify_tls} onChange={(e) => set('verify_tls', e.currentTarget.checked)} />
              <NumberInput size="xs" label="Timeout (s)" w={100} min={1} max={120} value={cfg.timeout} onChange={(v) => set('timeout', Number(v) || 10)} />
            </Group>
            <Divider label="Service account (user lookup)" labelPosition="left" />
            <SimpleGrid cols={{ base: 1, sm: 2 }}>
              <TextInput label="Account DN" placeholder="cn=refexposer,ou=services,dc=example,dc=com" value={cfg.bind_dn} onChange={(e) => set('bind_dn', e.currentTarget.value)} />
              <SecretInput
                label="Password"
                value={cfg.bind_password}
                isSet={cfg.bind_password_set}
                clear={cfg.bind_password_clear}
                onChange={(v) => set('bind_password', v)}
                onClear={(v) => set('bind_password_clear', v)}
              />
            </SimpleGrid>
            <Divider label="Users" labelPosition="left" />
            <TextInput label="Search base" placeholder="ou=people,dc=example,dc=com" value={cfg.user_base_dn} onChange={(e) => set('user_base_dn', e.currentTarget.value)} />
            <TextInput label="Filter" description="{username} is replaced by the typed username (escaped)." styles={MONO} value={cfg.user_filter} onChange={(e) => set('user_filter', e.currentTarget.value)} />
            <SimpleGrid cols={3}>
              <TextInput label="Username attribute" value={cfg.username_attr} onChange={(e) => set('username_attr', e.currentTarget.value)} />
              <TextInput label="Name attribute" value={cfg.display_name_attr} onChange={(e) => set('display_name_attr', e.currentTarget.value)} />
              <TextInput label="Email attribute" value={cfg.email_attr} onChange={(e) => set('email_attr', e.currentTarget.value)} />
            </SimpleGrid>
            <Divider label="Groups" labelPosition="left" />
            <SegmentedControl
              value={cfg.group_mode}
              onChange={(v) => set('group_mode', v as LdapSettings['group_mode'])}
              data={[
                { value: 'memberof', label: 'memberOf attribute' },
                { value: 'search', label: 'Group search' },
                { value: 'none', label: 'Do not import' },
              ]}
              w="fit-content"
            />
            {cfg.group_mode === 'search' && (
              <>
                <TextInput label="Groups base" placeholder="ou=groups,dc=example,dc=com" value={cfg.group_base_dn} onChange={(e) => set('group_base_dn', e.currentTarget.value)} />
                <SimpleGrid cols={{ base: 1, sm: 2 }}>
                  <TextInput label="Filter" description="{user_dn} = DN of the user" styles={MONO} value={cfg.group_filter} onChange={(e) => set('group_filter', e.currentTarget.value)} />
                  <TextInput label="Group name attribute" value={cfg.group_name_attr} onChange={(e) => set('group_name_attr', e.currentTarget.value)} />
                </SimpleGrid>
              </>
            )}
            {cfg.group_mode !== 'none' && (
              <SimpleGrid cols={{ base: 1, sm: 2 }}>
                <TextInput label="Imported groups (regex, optional)" placeholder="^refexposer-" styles={MONO} value={cfg.group_regex} onChange={(e) => set('group_regex', e.currentTarget.value)} />
                <TextInput
                  label="Administrators group (optional)"
                  description="Its members get the admin role (after approval)."
                  value={cfg.admin_group}
                  onChange={(e) => set('admin_group', e.currentTarget.value)}
                />
              </SimpleGrid>
            )}
          </Section>
          <Group>
            <Button onClick={() => save.mutate(cfg)} loading={save.isPending}>
              Save
            </Button>
          </Group>
        </Stack>
      </Grid.Col>
      <Grid.Col span={{ base: 12, lg: 5 }}>
        <Section title="Diagnostic" icon={<IconPlugConnected size={18} />}>
          <Button variant="light" onClick={() => test.mutate(false)} loading={test.isPending && !test.variables} w="fit-content" disabled={!cfg.server_url}>
            Test the connection
          </Button>
          <Divider label="or test a user" labelPosition="center" />
          <SimpleGrid cols={2}>
            <TextInput size="xs" label="Username" value={user} onChange={(e) => setUser(e.currentTarget.value)} autoComplete="off" />
            <PasswordInput size="xs" label="Password" value={pass} onChange={(e) => setPass(e.currentTarget.value)} autoComplete="new-password" />
          </SimpleGrid>
          <Button variant="light" onClick={() => test.mutate(true)} loading={test.isPending && !!test.variables} w="fit-content" disabled={!user || !cfg.server_url}>
            Test this user
          </Button>
          {test.data && (
            <Alert color={test.data.ok ? 'teal' : 'red'}>
              <Steps steps={test.data.steps} />
              {test.data.identity && (
                <Stack gap={2} mt="xs">
                  <Text size="xs">
                    DN: <Code>{test.data.identity.dn}</Code>
                  </Text>
                  <Text size="xs">
                    {test.data.identity.display_name} · {test.data.identity.email} · username <b>{test.data.identity.username}</b>
                  </Text>
                  <Group gap={4}>
                    {test.data.identity.groups.map((g) => (
                      <Badge key={g} size="xs" variant="outline" tt="none">
                        {g}
                      </Badge>
                    ))}
                    {test.data.identity.admin && (
                      <Badge size="xs" color="red">
                        admin
                      </Badge>
                    )}
                  </Group>
                </Stack>
              )}
            </Alert>
          )}
          {test.error && <Alert color="red">{(test.error as Error).message}</Alert>}
        </Section>
      </Grid.Col>
    </Grid>
  );
}

// ------------------------------------------------------------------ appearance

function BrandingTab({ initial }: { initial: BrandingSettings }) {
  const [cfg, setCfg] = useState(initial);
  useEffect(() => setCfg(initial), [initial]);
  const qc = useQueryClient();
  const { data: branding } = useBranding();
  const save = useSave('branding');
  const refresh = () => qc.invalidateQueries({ queryKey: ['branding'] });
  const onError = (e: Error) => notifications.show({ title: 'Logo refused', message: e.message, color: 'red' });
  const upload = useMutation({ mutationFn: api.uploadLogo, onSuccess: refresh, onError });
  const remove = useMutation({ mutationFn: api.deleteLogo, onSuccess: refresh, onError });

  return (
    <Grid gutter="md">
      <Grid.Col span={{ base: 12, lg: 7 }}>
        <Stack>
          <Section title="Name" icon={<IconBrush size={18} />}>
            <Text size="sm" c="dimmed">
              Shown in the header, on the sign-in page and in the browser tab.
            </Text>
            <TextInput label="Title" required maxLength={60} value={cfg.title} onChange={(e) => setCfg({ ...cfg, title: e.currentTarget.value })} />
            <TextInput
              label="Subtitle"
              description="Leave empty to show the title only"
              maxLength={120}
              value={cfg.subtitle}
              onChange={(e) => setCfg({ ...cfg, subtitle: e.currentTarget.value })}
            />
            <Group>
              <Button onClick={() => save.mutate(cfg, { onSuccess: refresh })} loading={save.isPending} disabled={!cfg.title.trim()}>
                Save
              </Button>
            </Group>
          </Section>
          <Section title="Company logo" icon={<IconPhotoUp size={18} />}>
            <Text size="sm" c="dimmed">
              PNG, JPEG, WebP or SVG, 512 KB maximum. A wide logo is shown up to four times its height; a transparent background
              suits both the light and the dark theme.
            </Text>
            {branding?.logo_url ? (
              <Card withBorder padding="md" w="fit-content">
                <Image src={branding.logo_url} alt="Company logo" h={64} w="auto" fit="contain" />
              </Card>
            ) : (
              <Text size="sm">No logo: the RefExposer icon is used.</Text>
            )}
            <Group>
              <FileButton onChange={(f) => f && upload.mutate(f)} accept="image/png,image/jpeg,image/webp,image/svg+xml">
                {(props) => (
                  <Button {...props} variant="light" leftSection={<IconPhotoUp size={16} />} loading={upload.isPending}>
                    {branding?.logo_url ? 'Replace the logo' : 'Upload a logo'}
                  </Button>
                )}
              </FileButton>
              {branding?.logo_url && (
                <Button variant="subtle" color="red" leftSection={<IconTrash size={16} />} onClick={() => remove.mutate()} loading={remove.isPending}>
                  Remove
                </Button>
              )}
            </Group>
          </Section>
        </Stack>
      </Grid.Col>
      <Grid.Col span={{ base: 12, lg: 5 }}>
        <Section title="Preview">
          <Card withBorder padding="sm">
            <BrandHeader />
          </Card>
          <Text size="xs" c="dimmed">
            The preview shows the saved settings.
          </Text>
        </Section>
      </Grid.Col>
    </Grid>
  );
}

// ------------------------------------------------------------------ OIDC

function OidcTab({ initial, redirectUri, publicUrl }: { initial: OidcSettings; redirectUri: string; publicUrl: string | null }) {
  const [cfg, setCfg] = useState(initial);
  useEffect(() => setCfg(initial), [initial]);
  const save = useSave('oidc');
  const test = useMutation({ mutationFn: () => api.testOidc(cfg) });
  const set = <K extends keyof OidcSettings>(k: K, v: OidcSettings[K]) => setCfg((c) => ({ ...c, [k]: v }));
  const effectiveRedirect = test.data?.redirect_uri ?? redirectUri;

  return (
    <Grid gutter="md">
      <Grid.Col span={{ base: 12, lg: 7 }}>
        <Stack>
          <Section title="OpenID Connect (Keycloak…)" icon={<IconKey size={18} />}>
            <Switch label="Show the OpenID Connect sign-in" checked={cfg.enabled} onChange={(e) => set('enabled', e.currentTarget.checked)} />
            <Text size="xs" c="dimmed">
              Authorization code flow with PKCE. At the first sign-in, an account is created pending approval. Groups (configurable
              claim) are synchronized at each sign-in.
            </Text>
            <SimpleGrid cols={{ base: 1, sm: 2 }}>
              <TextInput label="Button label" value={cfg.label} onChange={(e) => set('label', e.currentTarget.value)} />
              <TextInput label="Issuer" placeholder="https://keycloak.example.com/realms/my-realm" value={cfg.issuer} onChange={(e) => set('issuer', e.currentTarget.value)} />
            </SimpleGrid>
            <TextInput
              label="Internal discovery URL (optional)"
              description="When the backend reaches Keycloak through another address than the browsers (e.g. http://keycloak:8080/realms/my-realm)."
              value={cfg.discovery_url}
              onChange={(e) => set('discovery_url', e.currentTarget.value)}
            />
            <SimpleGrid cols={{ base: 1, sm: 2 }}>
              <TextInput label="Client ID" value={cfg.client_id} onChange={(e) => set('client_id', e.currentTarget.value)} />
              <SecretInput
                label="Client secret"
                description="Confidential client (recommended)"
                value={cfg.client_secret}
                isSet={cfg.client_secret_set}
                clear={cfg.client_secret_clear}
                onChange={(v) => set('client_secret', v)}
                onClear={(v) => set('client_secret_clear', v)}
              />
            </SimpleGrid>
            <TextInput label="Scopes" value={cfg.scopes} onChange={(e) => set('scopes', e.currentTarget.value)} />
            <Divider label="Claim mapping" labelPosition="left" />
            <SimpleGrid cols={3}>
              <TextInput label="Username" value={cfg.username_claim} onChange={(e) => set('username_claim', e.currentTarget.value)} />
              <TextInput label="Name" value={cfg.name_claim} onChange={(e) => set('name_claim', e.currentTarget.value)} />
              <TextInput label="Email" value={cfg.email_claim} onChange={(e) => set('email_claim', e.currentTarget.value)} />
            </SimpleGrid>
            <SimpleGrid cols={{ base: 1, sm: 2 }}>
              <TextInput
                label="Groups claim"
                description="Dotted paths allowed: realm_access.roles, resource_access.<client>.roles"
                value={cfg.groups_claim}
                onChange={(e) => set('groups_claim', e.currentTarget.value)}
              />
              <TextInput label="Imported groups (regex, optional)" styles={MONO} value={cfg.group_regex} onChange={(e) => set('group_regex', e.currentTarget.value)} />
            </SimpleGrid>
            <Switch size="sm" label="Keep only the last segment of Keycloak groups (/team/security → security)" checked={cfg.strip_group_path} onChange={(e) => set('strip_group_path', e.currentTarget.checked)} />
            <SimpleGrid cols={{ base: 1, sm: 2 }}>
              <TextInput label="Administrators group (optional)" description="Its members get the admin role (after approval)." value={cfg.admin_group} onChange={(e) => set('admin_group', e.currentTarget.value)} />
            </SimpleGrid>
            <Switch size="sm" label="Verify the provider certificate" checked={cfg.verify_tls} onChange={(e) => set('verify_tls', e.currentTarget.checked)} />
          </Section>
          <Group>
            <Button onClick={() => save.mutate(cfg)} loading={save.isPending}>
              Save
            </Button>
          </Group>
        </Stack>
      </Grid.Col>
      <Grid.Col span={{ base: 12, lg: 5 }}>
        <Stack>
          <Section title="Keycloak client configuration">
            <Text size="sm">Redirect URL to allow ("Valid redirect URIs"):</Text>
            <Group gap={4} wrap="nowrap">
              <Code style={{ wordBreak: 'break-all' }}>{effectiveRedirect}</Code>
              <CopyIcon value={effectiveRedirect} />
            </Group>
            <Text size="xs" c="dimmed">
              {publicUrl ? (
                <>
                  Built from <Code>REFEX_PUBLIC_URL</Code> ({publicUrl}).
                </>
              ) : (
                <>
                  Built from the address used to open this page: set <Code>REFEX_PUBLIC_URL</Code> to make it explicit.
                </>
              )}
            </Text>
            <List size="xs" spacing={2}>
              <List.Item>Client authentication: on (confidential client), Standard flow: on</List.Item>
              <List.Item>PKCE method: S256</List.Item>
              <List.Item>
                Groups: add a "Group Membership" mapper (claim <Code>groups</Code>) to the client or to a client scope
              </List.Item>
            </List>
          </Section>
          <Section title="Diagnostic" icon={<IconPlugConnected size={18} />}>
            <Button variant="light" onClick={() => test.mutate()} loading={test.isPending} w="fit-content" disabled={!cfg.issuer && !cfg.discovery_url}>
              Test the provider
            </Button>
            {test.data && (
              <Alert color={test.data.ok ? 'teal' : 'red'}>
                <Steps steps={test.data.steps} />
              </Alert>
            )}
            {test.error && <Alert color="red">{(test.error as Error).message}</Alert>}
          </Section>
        </Stack>
      </Grid.Col>
    </Grid>
  );
}

export default function SettingsPage() {
  const { tab = 'network' } = useParams();
  const navigate = useNavigate();
  const { data, isLoading } = useQuery({ queryKey: ['settings'], queryFn: api.settings });
  if (isLoading || !data) return <Loader />;
  return (
    <Stack gap="lg">
      <div>
        <Title order={2}>Settings</Title>
        <Text c="dimmed" size="sm">
          Outgoing network, identity sources and logging. Passwords and secrets are encrypted in the database (AES-256-GCM, key{' '}
          {data.secret_key_source === 'env' ? 'from REFEX_SECRET_KEY' : 'generated in data/.secret_key'}) and are never shown again.
        </Text>
      </div>
      <Tabs value={tab} onChange={(v) => navigate(`/admin/settings/${v}`)} keepMounted={false}>
        <Tabs.List mb="md">
          <Tabs.Tab value="network" leftSection={<IconNetwork size={16} />}>
            Network & proxy
          </Tabs.Tab>
          <Tabs.Tab
            value="ldap"
            leftSection={<IconSitemap size={16} />}
            rightSection={data.ldap.enabled ? <Badge size="xs" color="teal">on</Badge> : null}
          >
            LDAP directory
          </Tabs.Tab>
          <Tabs.Tab value="appearance" leftSection={<IconBrush size={16} />}>
            Appearance
          </Tabs.Tab>
          <Tabs.Tab value="oidc" leftSection={<IconKey size={16} />} rightSection={data.oidc.enabled ? <Badge size="xs" color="teal">on</Badge> : null}>
            OpenID Connect
          </Tabs.Tab>
          <Tabs.Tab value="mfa" leftSection={<IconShieldLock size={16} />} rightSection={data.mfa.mode !== 'optional' ? <Badge size="xs" color="teal">required</Badge> : null}>
            Two-factor
          </Tabs.Tab>
          <Tabs.Tab value="logging" leftSection={<IconFileText size={16} />} rightSection={data.syslog.enabled ? <Badge size="xs" color="teal">syslog</Badge> : null}>
            Logging
          </Tabs.Tab>
          <Tabs.Tab value="smtp" leftSection={<IconMail size={16} />} rightSection={data.smtp.host ? <Badge size="xs" color="teal">on</Badge> : null}>
            E-mail
          </Tabs.Tab>
          <Tabs.Tab value="mcp" leftSection={<IconRobot size={16} />} rightSection={data.mcp.enabled ? <Badge size="xs" color="teal">on</Badge> : null}>
            MCP server
          </Tabs.Tab>
        </Tabs.List>
        <Tabs.Panel value="network">
          <ProxyTab initial={data.proxy} envProxy={data.environment_proxy} />
        </Tabs.Panel>
        <Tabs.Panel value="ldap">
          <LdapTab initial={data.ldap} />
        </Tabs.Panel>
        <Tabs.Panel value="oidc">
          <OidcTab initial={data.oidc} redirectUri={data.oidc_redirect_uri} publicUrl={data.public_url} />
        </Tabs.Panel>
        <Tabs.Panel value="mfa">
          <MfaTab initial={data.mfa} ssoOnly={!data.local_login && !data.ldap.enabled} />
        </Tabs.Panel>
        <Tabs.Panel value="logging">
          <SyslogTab initial={data.syslog} status={data.syslog_status} logLevel={data.log_level} companyCa={!!data.proxy.ca_bundle} />
        </Tabs.Panel>
        <Tabs.Panel value="appearance">
          <BrandingTab initial={data.branding} />
        </Tabs.Panel>
        <Tabs.Panel value="smtp">
          <SmtpTab initial={data.smtp} />
        </Tabs.Panel>
        <Tabs.Panel value="mcp">
          <McpTab initial={data.mcp} url={data.mcp_url} />
        </Tabs.Panel>
      </Tabs>
    </Stack>
  );
}
