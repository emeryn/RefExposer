import { Alert, Anchor, Badge, Code, Grid, Group, List, NavLink, Paper, Stack, Table, Text, Title } from '@mantine/core';
import { IconBook, IconShieldCog } from '@tabler/icons-react';
import type { ReactNode } from 'react';
import { Link, useParams } from 'react-router-dom';
import { absoluteUrl } from '../../api/client';
import { useAuth } from '../../auth/AuthContext';
import { HEALTH, RUN_STATUS } from '../../components/Status';
import type { Me } from '../../api/types';

type Section = { id: string; title: string; admin?: boolean; visible?: (me: Me) => boolean; body: (me: Me) => ReactNode };

const H = ({ children }: { children: ReactNode }) => (
  <Title order={4} mt="lg" mb={6}>
    {children}
  </Title>
);
const P = ({ children }: { children: ReactNode }) => (
  <Text size="sm" mb="sm" maw={860} style={{ lineHeight: 1.6 }}>
    {children}
  </Text>
);
const Shell = ({ children }: { children: ReactNode }) => (
  <Code block mb="sm" maw={860}>
    {children}
  </Code>
);
// Base URL of the examples: REFEX_PUBLIC_URL when set, the browser address otherwise
const origin = () => absoluteUrl('');

// ------------------------------------------------------------------ user guide

const USER: Section[] = [
  {
    id: 'start',
    title: 'Getting started',
    body: () => (
      <>
        <P>
          RefExposer gathers reference data sets (“referentials”) in one place: vulnerability catalogues, country codes, IP ranges,
          lists maintained by your teams… Each referential is downloaded from its source on a schedule, checked, converted and
          published as a new <b>version</b>. You can browse it, search it, query it with SQL, call it from scripts through the API, or
          download it.
        </P>
        <H>The dashboard</H>
        <P>
          The <Anchor component={Link} to="/">dashboard</Anchor> shows every referential you can access, with its health, its size and
          when its data last changed. Use the filters to find the ones that need attention. The left menu lists them by category, with a
          coloured dot for their health.
        </P>
        <H>Health of a referential</H>
        <Table fz="sm" maw={860} mb="sm" withTableBorder>
          <Table.Tbody>
            {Object.entries(HEALTH).map(([k, v]) => (
              <Table.Tr key={k}>
                <Table.Td w={140}>
                  <Badge variant="light" color={v.color}>
                    {v.label}
                  </Badge>
                </Table.Td>
                <Table.Td>{HEALTH_HELP[k] ?? ''}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
        <H>Kinds of referentials</H>
        <List size="sm" spacing={4} maw={860} mb="sm">
          <List.Item>
            <b>Downloaded</b> — fetched from a URL (CSV, JSON, Excel, Parquet…) on a schedule. Most referentials are of this kind.
          </List.Item>
          <List.Item>
            <b>Internal</b> <Badge size="xs" variant="light" color="grape">internal</Badge> — created and maintained inside RefExposer:
            no remote source, rows are edited in the application or written through the API.
          </List.Item>
          <List.Item>
            <b>Confidential</b> <Badge size="xs" variant="light" color="red">confidential</Badge> — its data is encrypted at rest (files,
            database rows and keys); you use it normally if you have access. Its downloads are generated for each request and never
            kept.
          </List.Item>
          <List.Item>
            <b>Bloom filter</b> — a compact, offline membership test (e.g. CIRCL hashlookup, hundreds of millions of file hashes). It tells
            whether a value is <i>certainly absent</i> or <i>probably present</i>; there are no rows to browse.
          </List.Item>
          <List.Item>
            <b>MaxMind DB</b> — a GeoIP / ASN database (<Code>.mmdb</Code>: GeoLite2, GeoIP2, DB-IP…). Search tab and global search: paste
            IP addresses to get their country, city, network or AS. Tools download the raw file from the link of the Download menu
            (<Code>/raw</Code>, with an API token), only when it changed.
          </List.Item>
          <List.Item>
            <b>Large volume</b> — hundreds of millions of rows (e.g. passive DNS). Lookups on the sorted and indexed columns stay fast;
            some features (exact totals, facets, change tracking, CSV/Excel downloads) are limited.
          </List.Item>
        </List>
      </>
    ),
  },
  {
    id: 'explore',
    title: 'Exploring a referential',
    body: () => (
      <>
        <P>Open a referential to see its header (rows, version, schedule, storage, key) and its tabs:</P>
        <Table fz="sm" maw={860} mb="sm" withTableBorder>
          <Table.Tbody>
            {[
              ['Data', 'Paginated table with sorting, column filters (equals, contains, starts with, greater than, empty…), column selection and quick text search. Click a row to see all its values. The filters are reflected in the URL: copy it to share the view, or copy the equivalent API URL.'],
              ['Edit', 'Internal referentials only: add, change and delete rows, or load them from a file.'],
              ['Search', 'Multi-criteria search with facets (most frequent values per column, ranges for numbers and dates), and search by a list of values: paste up to 10,000 values to know which ones exist.'],
              ['Schema & profile', 'Columns, types, fill rate, distinct values, min/max. Click a column for its distribution and most frequent values.'],
              ['Changes', 'Rows added, removed and modified by the last version (requires a key).'],
              ['History', 'Every update: when, why (schedule, manual, upload, import folder, edit), how long, how many rows, and its log.'],
              ['API', 'Documentation generated from the actual schema, with a request builder you can run in the page.'],
              ['Source & configuration', 'Where the data comes from, the validation rules and the definition.'],
            ].map(([k, v]) => (
              <Table.Tr key={k}>
                <Table.Td w={170} fw={600}>
                  {k}
                </Table.Td>
                <Table.Td>{v}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
        <H>Where does the current version come from?</H>
        <P>
          A banner under the header tells you when the data does not come from the usual automatic source: a <b>manual import</b> (a
          file uploaded by someone, or dropped in the import folder), a <b>frozen version</b> (automatic updates suspended), or an
          <b> internal referential</b>. When the remote source is <b>corrupted</b> (empty file, error page instead of data, unreadable
          archive), the update is refused, the current version stays online and an orange alert explains what was wrong.
        </P>
      </>
    ),
  },
  {
    id: 'search',
    title: 'Searching',
    body: () => (
      <>
        <P>
          <b>Global search</b> (top bar, <Code>Ctrl+K</Code>) looks for a value in all your referentials at once: identifiers, labels,
          descriptions… Exact matches on a referential key come first.
        </P>
        <P>
          Inside a referential, the <b>Search</b> tab combines a text search with facets: click values to filter, set ranges on numbers
          and dates, then open the result in the explorer or export it.
        </P>
        <P>
          <b>Search by list</b> answers “which of these values are known?”: paste values (one per line), choose the column (the key by
          default) and get the matches with their details, the missing values, and a CSV export. On a Bloom filter, the test runs offline
          and only probable matches can optionally be confirmed with the online API.
        </P>
        <Alert color="gray" maw={860} p="sm">
          <Text size="sm">
            On large referentials, the text search matches the <b>beginning</b> of the values (“starts with”) on the sorted and indexed
            columns, which keeps it fast on billions of rows. Use filters or the API for other patterns.
          </Text>
        </Alert>
      </>
    ),
  },
  {
    id: 'sql',
    title: 'SQL console',
    visible: (me) => me.can_use_sql,
    body: () => (
      <>
        <P>
          The <Anchor component={Link} to="/sql">SQL console</Anchor> runs read-only queries (DuckDB dialect) over every referential
          you can access, joins included. Each referential is a table whose name is shown in its header (e.g.{' '}
          <Code>cisa_kev</Code>). The side panel lists tables and columns; <Code>Ctrl+Space</Code> completes names and{' '}
          <Code>Ctrl+Enter</Code> runs the query. Results can be exported.
        </P>
        <Shell>{`SELECT k.cveID, k.vendorProject, e.epss
FROM cisa_kev k JOIN epss e ON e.cve = k.cveID
ORDER BY e.epss DESC LIMIT 20`}</Shell>
        <P>
          Large referentials also expose their indexes as extra tables (shown with ⚡ in the side panel): querying the copy sorted on the
          column you filter on is much faster.
        </P>
      </>
    ),
  },
  {
    id: 'api',
    title: 'API & tokens',
    body: () => (
      <>
        <P>
          Everything in the interface is available through a REST API documented with OpenAPI: open the <b>API</b> tab of a referential,
          or the <Anchor href="/api/docs" target="_blank">full documentation</Anchor>. Scripts authenticate with a personal token created
          in <Anchor component={Link} to="/account">My account</Anchor>; a token has exactly your rights.
        </P>
        <Shell>{`export REFEX_TOKEN=rfx_…
# Rows, with filters (column=value, column__contains=…, column__gte=…), sort, pagination
curl -H "Authorization: Bearer $REFEX_TOKEN" "${origin()}/api/referentials/cisa-kev/rows?vendorProject=Microsoft&limit=10"
# One row by its key
curl -H "Authorization: Bearer $REFEX_TOKEN" "${origin()}/api/referentials/cisa-kev/lookup/CVE-2021-44228"
# Many values at once
curl -H "Authorization: Bearer $REFEX_TOKEN" -H "Content-Type: application/json" \\
     -d '{"values": ["CVE-2021-44228", "CVE-2014-0160"]}' "${origin()}/api/referentials/cisa-kev/lookup"`}</Shell>
        <P>
          A column whose name is also an API parameter (<Code>count</Code>, <Code>sort</Code>, <Code>limit</Code>…) is filtered with the
          explicit operator: <Code>?count__eq=3</Code>.
        </P>
      </>
    ),
  },
  {
    id: 'downloads',
    title: 'Downloads',
    body: () => (
      <P>
        The <Anchor component={Link} to="/downloads">Downloads</Anchor> page offers each referential in its latest version as CSV,
        compressed CSV, Excel, JSON, JSON Lines or Parquet, and the original source files. Files are generated once per version, so the
        links are stable and can be used by your tools with an API token. Large referentials are offered as Parquet only; filtered
        exports are available from the Data tab.
      </P>
    ),
  },
  {
    id: 'import',
    title: 'Manual imports',
    body: () => (
      <>
        <P>
          With the <b>manage</b> right on a referential, the <b>Import</b> button of its page lets you upload files that become the new
          version instead of the usual source — useful when the source is unreachable, wrong, or only available by e-mail. The files go
          through the same checks as a normal update: if they are empty, corrupted or fail the validation rules, they are refused and the
          current version stays online.
        </P>
        <P>
          Tick <b>Freeze this version</b> to suspend the scheduled updates, otherwise the next scheduled update will replace your files
          with the remote source again. A frozen referential shows a banner with a <b>Resume automatic updates</b> button.
        </P>
        <P>
          Operators can also drop files in the <b>import folder</b> of the server (one sub-folder per referential): they are imported
          automatically, then moved to <Code>.done/</Code>, or to <Code>.failed/</Code> with an <Code>ERROR.txt</Code> file explaining
          the refusal.
        </P>
      </>
    ),
  },
  {
    id: 'internal',
    title: 'Internal referentials',
    body: (me) => (
      <>
        <P>
          An internal referential lives in RefExposer itself: there is no remote source, its rows are typed in the application or written
          by your tools through the API. It is browsed, searched, documented, downloaded and queried in SQL like any other referential,
          and it shows an <Badge size="xs" variant="light" color="grape">internal</Badge> badge.
        </P>
        <H>Creating one</H>
        <P>
          Administrators and <b>advanced users</b> can create one from{' '}
          {me.can_create_internal ? <Anchor component={Link} to="/internal/new">New internal referential</Anchor> : 'New internal referential'}: give it a
          name, then define its columns (name, type, required) and choose the <b>key</b> column that identifies each row. An automatic key
          is generated when a row is added without it (a UUID for text, the next number for integers). Advanced users get the manage
          right on the referentials they create; give access to others from the Access tab (administrators).
        </P>
        <H>Editing rows</H>
        <P>
          The <b>Edit</b> tab lists the rows; click one to change or delete it, use <b>Add a row</b>, or <b>Load from a file</b> (CSV,
          Excel, JSON) to add/update rows in bulk or replace them all. Each change is checked against the column types and published as a
          new version a few seconds later. The <b>Columns</b> button changes the schema; existing rows are migrated (renames keep the
          values) and the change is refused if a row does not fit.
        </P>
        <H>Feeding it through the API</H>
        <Shell>{`# Add or replace a row (by key)
curl -X PUT -H "Authorization: Bearer $REFEX_TOKEN" -H "Content-Type: application/json" \\
     -d '{"cidr": "10.12.0.0/16", "site": "Lyon", "owner": "network"}' \\
     "${origin()}/api/referentials/internal-ranges/records/10.12.0.0%2F16"
# Change some columns only
curl -X PATCH ... -d '{"owner": "secops"}' ".../records/10.12.0.0%2F16"
# Synchronise many rows at once (upsert + delete, or upsert and drop every other row)
curl -X POST ... -d '{"upsert": [{...}, {...}], "delete": ["10.9.0.0/16"]}' ".../records/_bulk"
curl -X POST ... -d '{"upsert": [{...}, {...}], "replace_all": true}' ".../records/_bulk"`}</Shell>
        <P>The exact endpoints and the row format are documented in the API tab of each internal referential.</P>
      </>
    ),
  },
  {
    id: 'account',
    title: 'Your account',
    body: () => (
      <P>
        <Anchor component={Link} to="/account">My account</Anchor> shows your role, your groups and the referentials you can access, lets
        you change your password (local accounts) and manage your API tokens. Accounts from the company directory (LDAP) or single
        sign-on (OpenID Connect) are approved once by an administrator; their groups are synchronized at each sign-in. The interface
        theme (light, dark, system) is chosen with the sun/moon button of the top bar.
      </P>
    ),
  },
];

const HEALTH_HELP: Record<string, string> = {
  ok: 'The latest update succeeded and the data is fresh.',
  stale: 'The data is older than expected for its schedule: the source may be stuck, or updates are failing.',
  error: 'The last update failed (download error, corrupted source, rejected by validation). The previous version is still served.',
  running: 'An update is in progress.',
  empty: 'The referential has never been loaded.',
  disabled: 'Automatic updates are disabled.',
};

// ------------------------------------------------------------------ admin guide

const ADMIN: Section[] = [
  {
    id: 'admin-access',
    title: 'Users, roles and rights',
    admin: true,
    body: () => (
      <>
        <Table fz="sm" maw={860} mb="sm" withTableBorder>
          <Table.Tbody>
            <Table.Tr>
              <Table.Td w={150} fw={600}>
                Administrator
              </Table.Td>
              <Table.Td>Everything: all referentials, users, groups, rights, settings, system, audit log.</Table.Td>
            </Table.Tr>
            <Table.Tr>
              <Table.Td fw={600}>Advanced user</Table.Td>
              <Table.Td>The referentials they are granted, the SQL console, and the creation of internal referentials (they manage the ones they create).</Table.Td>
            </Table.Tr>
            <Table.Tr>
              <Table.Td fw={600}>User</Table.Td>
              <Table.Td>Only the referentials they are granted, directly or through their groups.</Table.Td>
            </Table.Tr>
          </Table.Tbody>
        </Table>
        <P>
          Rights are given per referential (or on <i>all referentials</i>) to a user or a group, at two levels: <b>Read</b> (browse,
          search, API, downloads, SQL for advanced users) and <b>Manage</b> (read + run or cancel updates, manual imports, editing internal referentials).
          Manage them from the Access tab of a referential, or from a user or group page.
        </P>
        <H>System tasks</H>
        <P>
          <Anchor component={Link} to="/admin/tasks">Tasks</Anchor> lists the scheduled background tasks:
          maintenance (sessions, audit retention, disk cleanup), integrity and monitoring (published data, stale referentials,
          sources, TLS certificate), security policy (inactive accounts, expired tokens, pending requests, LDAP resynchronisation)
          and configuration backups. Enable them, change their schedule and parameters, run them now and read their history. Backups
          are restored from the same page.
        </P>
        <H>Two-factor authentication</H>
        <P>
          Local and LDAP accounts add an authenticator app or a security key / passkey from My account. Require it for everybody or
          for some groups in Settings › Two-factor; reset it from Users when a phone or a key is lost. OpenID Connect accounts get
          their second factor from the identity provider.
        </P>
        <H>Service accounts</H>
        <P>
          For tools and scripts (SIEM, enrichment, CI…), create a <b>service account</b> in{' '}
          <Anchor component={Link} to="/admin/users">Users</Anchor> › New service account. It never signs in to the interface: it only
          calls the API with tokens that administrators create from its page (shown once, revocable at any time). It lists and queries
          the referentials it is granted, directly or through its groups, exactly like a user; it has no SQL console and cannot create
          its own tokens. Disabling the account cuts all its tokens at once.
        </P>
        <H>Approving accounts</H>
        <P>
          Local accounts are created by administrators. Accounts coming from LDAP or OpenID Connect are created at their first sign-in in
          a <b>pending</b> state: nobody gets in without an administrator approving them in{' '}
          <Anchor component={Link} to="/admin/users">Users</Anchor> (a badge shows the pending requests). When approving, choose the role
          and optional local groups; synchronized groups are already applied. A refused account cannot sign in until it is approved.
        </P>
        <P>
          The first administrator is created from <Code>REFEX_ADMIN_USERNAME</Code> / <Code>REFEX_ADMIN_PASSWORD</Code> and must change
          the password at first sign-in. Passwords are hashed with Argon2id; session and API tokens are stored as SHA3-256 fingerprints;
          every sign-in, change and export is recorded in the <Anchor component={Link} to="/admin/audit">audit log</Anchor>.
        </P>
      </>
    ),
  },
  {
    id: 'admin-idp',
    title: 'LDAP & OpenID Connect',
    admin: true,
    body: () => (
      <>
        <P>
          Configure them in <Anchor component={Link} to="/admin/settings/ldap">Settings</Anchor>; secrets are encrypted in the database
          (AES-256-GCM with <Code>REFEX_SECRET_KEY</Code>). Each tab has a test button that checks the values of the form before saving.
        </P>
        <List size="sm" spacing={6} maw={860} mb="sm">
          <List.Item>
            <b>LDAP / Active Directory</b> — a read-only service account looks up the user, then RefExposer binds as the user to check
            the password. Groups come from <Code>memberOf</Code> or a group search; a regex keeps only the relevant ones. Presets fill the
            usual OpenLDAP and Active Directory attributes.
          </List.Item>
          <List.Item>
            <b>OpenID Connect (Keycloak…)</b> — authorization code flow with PKCE. Create a confidential client, allow the redirect URL
            shown in the settings, and add a “Group Membership” mapper so that the token carries a <Code>groups</Code> claim.
          </List.Item>
          <List.Item>
            An optional <b>administrators group</b> gives the admin role to its members — still after approval.
          </List.Item>
        </List>
      </>
    ),
  },
  {
    id: 'admin-refs',
    title: 'Adding referentials',
    admin: true,
    body: () => (
      <>
        <P>
          <Anchor component={Link} to="/admin/referentials/new">New referential</Anchor> walks through four steps: the source (one or
          more URLs, or an uploaded file), the reading (format detected automatically, options, an optional SQL transformation, live
          preview), the description (name, key, search columns, schedule, validation rules, access) and the publication.
        </P>
        <P>
          Referentials can also be defined as YAML files in the <Code>config/</Code> folder, which is convenient to version them: use{' '}
          <i>View as YAML</i> on an existing referential as a starting point, then <i>Reload the configuration</i> from the{' '}
          <Anchor component={Link} to="/system">System</Anchor> page. YAML referentials are read-only in the interface.
        </P>
        <H>JSON and XML documents</H>
        <List size="sm" spacing={4} maw={860} mb="sm">
          <List.Item>
            Array of records inside a document (<Code>{'{"vulnerabilities": [...]}'}</Code>): records path <Code>vulnerabilities</Code>.
          </List.Item>
          <List.Item>
            Object whose values are the records (<Code>{'{"1945182": {...}, "1945136": {...}}'}</Code>): records path <Code>*</Code>, or{' '}
            <Code>executables.*</Code> when nested. The object key goes to the <Code>_key</Code> column (option <Code>records_key</Code>).
          </List.Item>
          <List.Item>
            XML: each repeated record element becomes a row (attributes and children become columns). The record element is
            detected, or set in the records path (e.g. <Code>Weakness</Code> for the CWE catalog).
          </List.Item>
          <List.Item>
            The analysis suggests these paths as buttons, and adds <Code>maximum_depth</Code> by itself when nested keys differ only by
            case. Several large documents (all NVD years…) are read one document at a time to keep memory bounded.
          </List.Item>
        </List>
        <H>Validation and corrupted sources</H>
        <P>
          A new version is published only if it passes the checks: the downloaded file must not be empty, an HTML error page or an
          unreadable archive (the run ends as <b>Corrupted</b>), and the result must respect the rules of the referential (minimum rows,
          maximum drop compared with the previous version, unique key). Otherwise the current version stays online and the error is shown
          on the referential and the dashboard.
        </P>
        <Table fz="sm" maw={860} mb="sm" withTableBorder>
          <Table.Tbody>
            {Object.entries(RUN_STATUS).map(([k, v]) => (
              <Table.Tr key={k}>
                <Table.Td w={140}>
                  <Badge variant="light" color={v.color}>
                    {v.label}
                  </Badge>
                </Table.Td>
                <Table.Td>{RUN_HELP[k] ?? ''}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </>
    ),
  },
  {
    id: 'admin-large',
    title: 'Large volumes',
    admin: true,
    body: () => (
      <>
        <P>
          Referentials of hundreds of millions or billions of rows (passive DNS…) are supported. In the <i>Large volume</i> section of
          the definition, choose <b>Sort on</b> (the column most lookups use) and <b>Indexes</b> (extra sorted copies for other columns,
          each about the size of the data). Exact, prefix and range lookups on these columns then read only the relevant blocks.
        </P>
        <P>
          Measured on 1 billion passive DNS rows (30 GB of Parquet): import in about 65 minutes with two indexes, lookups and first pages
          in 120–270 ms, batches of 1,000 values in about 2.5 s, against 5–8 s for a full scan without sorting. Above{' '}
          <Code>REFEX_LARGE_ROWS</Code> (50 million rows by default), RefExposer samples the profile, disables facets and change
          tracking, offers Parquet downloads only and stops queries after <Code>REFEX_QUERY_TIMEOUT</Code> seconds.
        </P>
      </>
    ),
  },
  {
    id: 'admin-sync',
    title: 'Sync folder',
    admin: true,
    body: () => (
      <>
        <P>
          The sync folder is the simplest way to publish files produced elsewhere: push them (scp, rsync over ssh, a shared folder…)
          and RefExposer does the rest. There is nothing to declare: <b>every file or folder of the sync folder is a referential</b>,
          created at the next scan and imported again whenever its files change.
        </P>
        <Table fz="sm" maw={860} mb="sm" withTableBorder>
          <Table.Tbody>
            <Table.Tr>
              <Table.Td w={260}>
                <Code>sync/epss_scores.csv</Code>
              </Table.Td>
              <Table.Td>referential <Code>epss-scores</Code> (one file)</Table.Td>
            </Table.Tr>
            <Table.Tr>
              <Table.Td>
                <Code>sync/threatfox/*.json</Code>
              </Table.Td>
              <Table.Td>referential <Code>threatfox</Code>, made of every data file of the folder</Table.Td>
            </Table.Tr>
            <Table.Tr>
              <Table.Td>
                <Code>sync/threatfox/refexposer.yml</Code>
              </Table.Td>
              <Table.Td>optional: name, description, category, key, format, options, transform, validation…</Table.Td>
            </Table.Tr>
          </Table.Tbody>
        </Table>
        <Shell>{`# Weekly push from another server (cron)
rsync -av --delay-updates --delete exports/ refexposer@refexposer.example.com:/opt/refexposer/sync/

# sync/threatfox/refexposer.yml
name: ThreatFox IOCs
category: Threat intelligence
key: ioc_id
options:
  records_path: "*"
  records_key: ioc_id`}</Shell>
        <List size="sm" spacing={4} maw={860} mb="sm">
          <List.Item>
            The format comes from the extension (CSV, TSV, TXT/LIST, JSON, JSON Lines, XML, Parquet, Excel, Bloom), compressed files
            (<Code>.gz</Code>, <Code>.zip</Code>) are accepted, and the records of a JSON document are found by themselves.
          </List.Item>
          <List.Item>
            An import starts once the files have stopped changing: hidden files (rsync temporary files), <Code>*.part</Code> and{' '}
            <Code>*.tmp</Code> are ignored, so a copy in progress is never read. <Code>--delay-updates</Code> makes rsync switch all
            the files at the end of the transfer.
          </List.Item>
          <List.Item>
            The usual checks apply: an empty file or an error page never replaces the published version. Unchanged content does not
            create a new version.
          </List.Item>
          <List.Item>
            Removing a file or folder removes the referential (its data is kept and comes back with the files). A name already used
            by a YAML or interface referential is reported on the System page. Rights are given as for any referential (Access
            tab): only administrators see a new synchronized referential at first.
          </List.Item>
        </List>
      </>
    ),
  },
  {
    id: 'admin-import',
    title: 'Import folder',
    admin: true,
    body: () => (
      <>
        <P>
          Mount a folder in the backend container and set <Code>REFEX_IMPORT_DIR</Code> to let operators drop files instead of uploading
          them. RefExposer creates one sub-folder per referential with a <Code>README.txt</Code>, checks the folder every{' '}
          <Code>REFEX_IMPORT_POLL_SECONDS</Code> seconds, waits until the files are completely copied, then imports them as a new
          version.
        </P>
        <Shell>{`# docker-compose.yml (backend)
volumes:
  - ./import:/import
environment:
  REFEX_IMPORT_DIR: /import

cp nvd-2026.json.gz import/nvd-cve/
# → import/nvd-cve/.done/<date>/…   or   import/nvd-cve/.failed/<date>/ERROR.txt`}</Shell>
        <P>
          An import from the folder does not freeze the version: the next scheduled update replaces it with the remote source again.
          To keep it, upload the files from the referential page with “Freeze this version” ticked. Folders that match no referential are listed on the System page.
        </P>
      </>
    ),
  },
  {
    id: 'admin-ops',
    title: 'Settings & operations',
    admin: true,
    body: () => (
      <>
        <List size="sm" spacing={6} maw={860} mb="sm">
          <List.Item>
            <b>Proxy</b> — <Anchor component={Link} to="/admin/settings/network">Settings › Network</Anchor>: HTTP and HTTPS proxies,
            exceptions, credentials, extra certificate authorities (TLS-inspecting proxies). Without it, the container environment
            variables apply.
          </List.Item>
          <List.Item>
            <b>Appearance</b> — <Anchor component={Link} to="/admin/settings/appearance">Settings › Appearance</Anchor>: title, subtitle
            and company logo shown in the header, on the sign-in page and in the browser tab.
          </List.Item>
          <List.Item>
            <b>Public URL</b> — <Code>REFEX_PUBLIC_URL</Code> (e.g. <Code>https://refexposer.my-company.com</Code>) is set once and used for
            the OpenID Connect callback, the generated API documentation, the URLs shown to users, secure cookies and, in production,
            the nginx host name.
          </List.Item>
          <List.Item>
            <b>System</b> — <Anchor component={Link} to="/system">System</Anchor> shows the service, the running updates, the schedule,
            the limits and the import folder, and reloads the YAML configuration.
          </List.Item>
          <List.Item>
            <b>Audit</b> — the <Anchor component={Link} to="/admin/audit">audit log</Anchor> records sign-ins, administration, updates,
            imports, row edits, exports and SQL queries.
          </List.Item>
          <List.Item>
            <b>Production</b> — expose RefExposer behind a reverse proxy serving HTTPS (the README gives an nginx configuration with
            security headers, rate limiting and hybrid post-quantum TLS). Set a long random <Code>REFEX_SECRET_KEY</Code> and keep it: it decrypts the
            secrets stored in the database.
          </List.Item>
          <List.Item>
            <b>Data</b> — everything lives in the <Code>data/</Code> volume (Parquet versions, sources, download cache) and the PostgreSQL
            database (users, rights, definitions, internal rows, audit). Back up both.
          </List.Item>
        </List>
      </>
    ),
  },
];

const RUN_HELP: Record<string, string> = {
  queued: 'Waiting for a free slot (concurrent updates are limited).',
  running: 'Downloading, reading, checking or publishing.',
  success: 'A new version has been published.',
  unchanged: 'The source has not changed (HTTP 304 or identical content): nothing to publish.',
  error: 'Technical failure (network, unreadable format, SQL error). The current version is kept.',
  rejected: 'The data was read but breaks a validation rule. The current version is kept.',
  corrupted: 'The remote file is empty, an error page or a broken archive. The current version is kept.',
  cancelled: 'Stopped by a user.',
};

export default function HelpPage() {
  const { section } = useParams();
  const { me } = useAuth();
  if (!me) return null;
  const all = [...USER, ...(me.is_admin ? ADMIN : [])].filter((s) => !s.visible || s.visible(me));
  const current = all.find((s) => s.id === section) ?? all[0];
  const link = (s: Section) => (
    <NavLink key={s.id} component={Link} to={`/help/${s.id}`} label={s.title} active={s.id === current.id} style={{ borderRadius: 8 }} />
  );
  return (
    <Grid gutter="lg">
      <Grid.Col span={{ base: 12, md: 3 }}>
        <Paper withBorder p="xs" radius="md" pos="sticky" top={76}>
          <Group gap={6} px="sm" py={6}>
            <IconBook size={16} />
            <Text size="xs" fw={700} tt="uppercase" c="dimmed">
              User guide
            </Text>
          </Group>
          {USER.map(link)}
          {me.is_admin && (
            <>
              <Group gap={6} px="sm" py={6} mt="sm">
                <IconShieldCog size={16} />
                <Text size="xs" fw={700} tt="uppercase" c="dimmed">
                  Administrator guide
                </Text>
              </Group>
              {ADMIN.map(link)}
            </>
          )}
        </Paper>
      </Grid.Col>
      <Grid.Col span={{ base: 12, md: 9 }}>
        <Stack gap={0}>
          <Text size="xs" c="dimmed" tt="uppercase" fw={700}>
            {current.admin ? 'Administrator guide' : 'User guide'}
          </Text>
          <Title order={2} mb="md">
            {current.title}
          </Title>
          {current.body(me)}
        </Stack>
      </Grid.Col>
    </Grid>
  );
}
