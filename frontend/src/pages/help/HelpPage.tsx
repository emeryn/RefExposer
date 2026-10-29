import { Alert, Anchor, Badge, Code, Grid, Group, List, NavLink, Paper, Stack, Table, Text, Title } from '@mantine/core';
import { IconBook, IconShieldCog } from '@tabler/icons-react';
import type { ReactNode } from 'react';
import { Link, useParams } from 'react-router-dom';
import { absoluteUrl } from '../../api/client';
import type { Me } from '../../api/types';
import { useAuth } from '../../auth/AuthContext';
import { HEALTH, RUN_STATUS } from '../../components/Status';
/** One page of a guide: the user guide, the administrator guide, or a guide added by a release. */
export type Section = { id: string; title: string; admin?: boolean; visible?: (me: Me) => boolean; body: (me: Me) => ReactNode };

// The guide for the applications for other tools lives in IntegrationsHelp.tsx, which is not part of the
// published repository yet: import its INTEGRATIONS here and put them in this list to show the group.
const PLUGINS: Section[] = [];

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
            <b>Unified</b> <Badge size="xs" variant="light" color="teal">unified</Badge> — built from several other referentials:
            their rows put together, or the rows of one enriched with the columns of the others. It is rebuilt whenever one of
            them is updated; its <b>Source</b> tab lists them with the version used.
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
              ['Versions & diff', 'Versions kept: save the state published right now, compare any two of them (added, removed, modified, with the before and after of each column), restore one, or delete one.'],
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
    id: 'governance',
    title: 'Visibility, owner and personal data',
    body: () => (
      <>
        <P>
          Three things on a referential tell you how to handle the data in it. They are on every card in the{' '}
          <Anchor component={Link} to="/">dashboard</Anchor> and in the header of the referential.
        </P>
        <H>Internal or public</H>
        <P>
          A violet <b>Internal</b> badge (padlock) means company data: it is not published anywhere else, so think
          before copying it into a ticket, a chat or an outside tool. A teal <b>Public</b> badge (globe) means open
          data or an external feed — already public, quote it freely. The dashboard has an{' '}
          <b>All / Internal / Public</b> filter.
        </P>
        <H>Who answers for it</H>
        <P>
          The owner is shown with their initials, with their team on hover. That is who to ask when a value looks
          wrong, when a column needs explaining, or when you need access.
        </P>
        <H>Masked columns</H>
        <P>
          An orange <b>N masked</b> badge means the referential holds personal data. Those columns come back masked
          everywhere — in the table, in exports, in downloads, in the SQL console and to the assistant — either as{' '}
          <Code>***</Code>, as a first and last character, or as a pseudonym that is always the same for the same
          value (so you can still count and group on it). Hover the badge to see which columns, how, and why.
        </P>
        <P>
          If you are allowed to see the real values, a <b>Show personal data</b> switch appears in the Data tab, in
          the SQL console and in the diff view. It is off every time: turning it on is a deliberate act, and each
          request is recorded in the audit log. If the switch is not there, ask the owner of the referential — the
          assistant cannot lift the masking for you, whatever you ask it.
        </P>
      </>
    ),
  },
  {
    id: 'alerts',
    title: 'Alerts',
    body: () => (
      <>
        <P>
          The bell in the header and the <Anchor component={Link} to="/alerts">Alerts</Anchor> page list what
          needs a look, newest first. The badge counts what you have not read yet.
        </P>
        <Table fz="sm" maw={860} mb="sm" withTableBorder>
          <Table.Tbody>
            {[
              ['Watchdog', 'A scheduled investigation found something, or could not finish. Only its owner sees it.'],
              ['Update', 'The last update of a referential failed, was rejected by its validation, or found a corrupted source.'],
              ['Quality', 'The published version no longer passes its quality rules.'],
              ['Stale', 'No new version within the expected delay: the source may have stopped publishing.'],
              ['Drift', 'The last version changed the number of rows a lot. A drop counts for more than growth.'],
            ].map(([k, v]) => (
              <Table.Tr key={k}>
                <Table.Td w={120} fw={600}>
                  {k}
                </Table.Td>
                <Table.Td>{v}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
        <P>
          Click an entry to see what it takes to act on it: the <b>report the watchdog wrote</b> with the items
          it found, the <b>log of the failed update</b>, the <b>rules that are failing</b> and by how much, or
          the <b>figures</b> of the drift — plus a link straight to the referential.
        </P>
        <H>Read, deleted, and back again</H>
        <P>
          Opening an alert marks it as <b>read</b>, and the envelope on each line marks it read or unread again.
          The badge on the bell and in the menu counts what you have <b>not read</b>, so it clears itself as you
          go through the list — and says the same thing on every browser you use, because read is kept on the
          server. It is yours alone: it changes nothing for your colleagues, and nothing about the referential
          or the watchdog behind it.
        </P>
        <P>
          To get a line out of the way for good, <b>delete</b> it: the bin on a line, or the button that takes
          everything currently shown. A deleted alert leaves your feed and its counts — again, yours only. The{' '}
          <b>Deleted</b> tab lists what you removed and puts any of it back, so nothing is ever lost.
        </P>
        <P>
          An alert you have read, or deleted, <b>comes back</b> if the thing behind it happens again — the same
          referential failing a second time is news.
        </P>
        <P>
          The list itself is built from the current state, not from a mailbox: an alert disappears on its own
          once the thing behind it is fixed, and there is nothing to clean up. You only ever see alerts about
          referentials you may read, and an administrator decides which kinds are reported here at all — so a
          kind missing from the list above is one that was turned off.
        </P>
        <P>
          This is the view inside the application. Getting the same events by e-mail or in a chat channel is a
          separate setting an administrator configures per channel.
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
        <H>Searching a large referential</H>
        <P>
          On hundreds of millions of rows, a search over everything would read the whole file, so only the <b>sorted and indexed</b>{' '}
          columns of the referential are searched, and only on the <b>beginning</b> of the values (“starts with”, case-sensitive).
          Those columns are shown in the header of the referential and in its <b>Source</b> tab. What works, in order of comfort:
        </P>
        <List size="sm" spacing={4} maw={860} mb="sm">
          <List.Item>
            the text search and <b>Search by list</b> on a sorted or indexed column — instant, even for 10,000 values at once;
          </List.Item>
          <List.Item>
            a column filter in the <b>Data</b> tab with <i>equals</i> or <i>starts with</i> on one of those columns;
          </List.Item>
          <List.Item>
            a filter on any other column: the rows come back, but without an exact total, and it is refused on the very largest
            referentials with the list of the columns that would work;
          </List.Item>
          <List.Item>
            the <b>SQL console</b>, which can also read the sorted copies directly (the extra tables marked ⚡).
          </List.Item>
        </List>
        <Alert color="gray" maw={860} p="sm">
          <Text size="sm">
            If a search on such a referential answers that it is not available, the column you need is neither sorted nor indexed:
            ask an administrator to add it (<i>Large volume › Sort on / Indexes</i> in its definition). It takes a rebuild, and
            each index takes about the size of the data.
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
        <H>Keep a query as a referential</H>
        <P>
          <b>Save as referential</b>, next to the CSV and JSON exports, turns the query that has just run into a referential of its
          own. The <b>query is stored, not its rows</b>: the referential is rebuilt from the published data of the referentials it
          reads, every time one of them publishes a new version. It then behaves like any other referential — explorer, filters,
          lookups, downloads, API, access rights, quality rules.
        </P>
        <P>
          Give it a name, an identifier and, when a column identifies a row, a key (which enables{' '}
          <Code>/lookup/{'{value}'}</Code>). No schedule is needed: a referential built from others follows them. Only a query reading
          the tables of the side panel can become one, and only the referentials you may read are available to it.
        </P>
      </>
    ),
  },
  {
    id: 'assistant',
    title: 'Assistant',
    body: () => (
      <>
        <P>
          The <Anchor component={Link} to="/chat">Assistant</Anchor> answers questions about the referentials from a
          model hosted on your own infrastructure. In <b>agent mode</b> it does the looking up itself: it lists the
          referentials, queries them, runs SQL and reports what it found.
        </P>
        <H>It has exactly your rights</H>
        <P>
          Everything it does goes through the API <b>with your session</b>: a referential you cannot read does not
          exist for it either, and it can only change what you could change yourself from the interface. Nothing is
          elevated. Confidential referentials are out of its reach unless an administrator allowed them.
        </P>
        <H>Pointing it at a few referentials</H>
        <P>
          The button at the top of the page narrows a conversation to the referentials it is about. It is
          optional, and it is there for speed: with a catalogue of a hundred referentials, the agent spends its
          first steps working out where to look. A conversation keeps its own filter, and a referential it
          <b> creates</b> for you joins it — narrowing a conversation never stops the work.
        </P>
        <H>What it can hand you</H>
        <List size="sm" spacing={4} maw={860} mb="sm">
          <List.Item>
            <b>Rows</b> in the answer, as a table, with the total.
          </List.Item>
          <List.Item>
            <b>A query</b> to open in the <Anchor component={Link} to="/sql">SQL console</Anchor> with one click:
            useful when you want to adjust it, or keep it as a referential.
          </List.Item>
          <List.Item>
            <b>A file</b> to download (JSON, CSV) when the result is meant to be passed on.
          </List.Item>
          <List.Item>
            <b>A referential of its own</b> from a query, when you ask for a unified referential: the query is
            stored, not its rows, so it follows the referentials it reads.
          </List.Item>
        </List>
        <H>Conversations</H>
        <P>
          A conversation is kept and is yours alone: ask several questions in a row, come back to it later. It lasts
          until you delete it — one conversation, or all of them, from the menu at the top right. Each tool call is
          shown with its result, so you can see where an answer comes from.
        </P>
        <H>Which model answers</H>
        <P>
          An administrator saves one or more <b>connectors</b> and picks the one in use; you see the result, not
          the plumbing. When the connector says the model is a <b>small</b> one (1B or 3B), the assistant works
          with five read-only tools and answers questions over one or two referentials — it will not write a
          referential, hand you a file or build a query for you. Ask an administrator to switch to a bigger
          model for that.
        </P>
        <P>
          Answers come from a language model: check anything that matters. The assistant is told never to invent a
          referential, a column or a number, and a row it reports can always be checked in the explorer.
        </P>
      </>
    ),
  },
  {
    id: 'watchdogs',
    title: 'Watchdogs',
    visible: (me) => me.is_admin || me.role === 'advanced',
    body: () => (
      <>
        <P>
          A <Anchor component={Link} to="/watchdogs">watchdog</Anchor> is the assistant asking itself the same
          question again and again, over referentials you choose, and telling you <b>only when it finds
          something</b>. Nothing arrives when there is nothing to report.
        </P>
        <P>
          They are off until an administrator turns them on, and they are reserved to administrators and
          advanced users. Findings also show up in <Anchor component={Link} to="/alerts">Alerts</Anchor>, with
          the report attached.
        </P>
        <H>What you set up</H>
        <List size="sm" spacing={4} maw={860} mb="sm">
          <List.Item>
            <b>The referentials</b> it may read. Only those: the agent cannot reach anything else, however the
            question is worded. Or <b>every referential</b> its account may read, when the question is about the
            catalogue as a whole — its reach then follows the rights of that account.
          </List.Item>
          <List.Item>
            <b>When</b> it runs: on a schedule, or right after one of those referentials publishes a new version.
          </List.Item>
          <List.Item>
            <b>Only what is new</b>, if you want it. The question then carries the published version of each
            referential and what its last version changed, and the agent is asked about the rows added or
            changed since its previous run instead of the whole data. It pairs naturally with “after a
            synchronization”.
          </List.Item>
          <List.Item>
            <b>The investigation</b> itself. Three examples are pre-filled for you: shadow IT against the CMDB,
            external exposure drift, and orphan accounts crossed with the HR referential.
          </List.Item>
          <List.Item>
            <b>Who is told</b>, and from which severity upwards. A finding below it is recorded without waking
            anyone.
          </List.Item>
        </List>
        <H>What it can and cannot do</H>
        <P>
          It runs with the rights of its own account and <b>never writes</b>: it looks, correlates and reports. An
          answer that does not come back as a proper verdict is recorded as a <b>failure</b>, never as an
          all-clear — so a quiet watchdog really means nothing was found.
        </P>
        <H>Following them</H>
        <P>
          A watchdog that is working says so, and <b>its executions open by themselves</b>: the steps appear as it
          goes, so you can see which referential it is reading and what came back. <b>Run now</b> starts one on the
          spot; it takes a moment, since the model is called several times.
        </P>
        <P>
          Once it is over, the same list is how you check its work: the verdict with its severity, the report the
          agent wrote, every query it went through — click one to see what the tool answered and how long it took —
          and whether each alert was delivered. A run with no end date and nothing running is one the service
          interrupted; start it again.
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
        <H>Complete example: create a referential and fill it from a script</H>
        <P>
          Everything below uses one token (<Anchor component={Link} to="/account">My account › API tokens</Anchor>, or a{' '}
          <b>service account</b> for an unattended script). Creating a referential needs the administrator or advanced-user role;
          writing rows needs the <b>manage</b> right on it.
        </P>
        <Shell>{`export REFEX_TOKEN=rfx_…
export REFEX_URL=${origin()}
H="Authorization: Bearer $REFEX_TOKEN"
J="Content-Type: application/json"

# 1. Create the referential: its columns, and the key that identifies a row
curl -X POST -H "$H" -H "$J" "$REFEX_URL/api/internal-referentials" -d '{
  "id": "sites", "name": "Sites", "category": "Internal", "key": "code",
  "search_columns": ["label", "owner"],
  "columns": [
    {"name": "code",    "type": "text",    "required": true},
    {"name": "label",   "type": "text"},
    {"name": "owner",   "type": "text"},
    {"name": "people",  "type": "integer"},
    {"name": "opened",  "type": "date"},
    {"name": "active",  "type": "boolean"},
    {"name": "tags",    "type": "list"},
    {"name": "comment", "type": "text"}
  ]}'

# 2. One row at a time
#    POST   = add (409 if the key exists; add ?upsert=true to update it instead)
curl -X POST -H "$H" -H "$J" "$REFEX_URL/api/referentials/sites/records" \\
     -d '{"code": "LYO", "label": "Lyon", "owner": "network", "people": 120,
          "opened": "2019-04-01", "active": true, "tags": ["prod", "eu"]}'
#    PATCH  = change only these columns          PUT = replace the whole row
curl -X PATCH -H "$H" -H "$J" "$REFEX_URL/api/referentials/sites/records/LYO" -d '{"owner": "secops"}'
curl -X PUT   -H "$H" -H "$J" "$REFEX_URL/api/referentials/sites/records/LYO" -d '{"label": "Lyon Part-Dieu"}'
curl -X DELETE -H "$H" "$REFEX_URL/api/referentials/sites/records/LYO"
#    A key with / or # is URL-encoded: .../records/10.12.0.0%2F16

# 3. Many rows at once: upsert (matched on the key), delete, or full synchronisation
curl -X POST -H "$H" -H "$J" "$REFEX_URL/api/referentials/sites/records/_bulk" -d '{
  "upsert": [{"code": "NTE", "label": "Nantes", "people": 40},
             {"code": "BDX", "label": "Bordeaux", "people": 25}],
  "delete": ["LYO"]}'
# replace_all: the rows sent become the whole content, every other row is deleted
curl -X POST -H "$H" -H "$J" "$REFEX_URL/api/referentials/sites/records/_bulk" \\
     -d '{"upsert": [{"code": "NTE", "label": "Nantes"}], "replace_all": true}'

# 4. Or load a file directly (CSV, TSV, Excel, JSON, JSON Lines, Parquet)
curl -X POST -H "$H" -F file=@sites.csv -F mode=upsert \\
     "$REFEX_URL/api/referentials/sites/records/_import"   # mode=replace: the file becomes the whole content`}</Shell>
        <P>
          Answers: a single-row call returns the row as stored (with its generated key, if any); a bulk call returns{' '}
          <Code>{'{"created": n, "updated": n, "unchanged": n, "deleted": n}'}</Code>. A batch is <b>all or nothing</b>: one value
          that does not fit its column type is reported with the row number and nothing is written. At most 50,000 changes per
          request, so split larger files. Each change publishes a new version a few seconds later; a burst of calls gives one
          version.
        </P>
        <Table fz="sm" maw={860} mb="sm" withTableBorder>
          <Table.Thead>
            <Table.Tr>
              <Table.Th w={110}>Column type</Table.Th>
              <Table.Th>Accepted values</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {[
              ['text', 'any text; a number is turned into its text form'],
              ['integer', 'whole number, or its text form ("120")'],
              ['number', 'decimal number; a comma is accepted as the decimal separator'],
              ['boolean', 'true / false, yes / no, 1 / 0, oui / non, x'],
              ['date', 'YYYY-MM-DD (the date part of a timestamp is kept)'],
              ['datetime', 'ISO 8601, e.g. 2026-04-01T09:30:00Z'],
              ['list', 'JSON array ["a", "b"], or comma separated values in a CSV cell'],
            ].map(([k, v]) => (
              <Table.Tr key={k}>
                <Table.Td>
                  <Code>{k}</Code>
                </Table.Td>
                <Table.Td>{v}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
        <P>
          An empty value is stored as empty, except on a <b>required</b> column and on the key, where the row is refused — unless
          the key column is <i>generated</i> (<Code>auto</Code>), which gives a UUID to a text key and the next number to an
          integer key.
        </P>
        <H>The same thing as a ready-made script</H>
        <P>
          <Code>examples/scripts/refex-internal.py</Code> (standard library only) wraps exactly these calls:
        </P>
        <Shell>{`export REFEX_TOKEN=rfx_…   REFEX_URL=${origin()}

./refex-internal.py create sites "Sites" --key code \\
    --column code:text:required --column label:text --column owner:text \\
    --column people:integer --column opened:date --column active:boolean --column tags:list
./refex-internal.py add    sites code=LYO label=Lyon owner=network people=120 active=true tags=prod,eu
./refex-internal.py patch  sites LYO owner=secops
./refex-internal.py load   sites sites.csv      # add / update in batches of 10,000 rows
./refex-internal.py sync   sites sites.csv      # same, and delete every row absent from the file
./refex-internal.py rows   sites --limit 20`}</Shell>
        <P>
          <Code>sites.csv</Code> is an ordinary CSV whose header holds the column names; columns the referential does not have are
          left out with a warning. The exact endpoints, with the schema of this referential, are in its <b>API</b> tab.
        </P>
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
              <Table.Td>The referentials they are granted, the SQL console, and the creation of referentials — internal, or a console query kept as one (they manage the ones they create).</Table.Td>
            </Table.Tr>
            <Table.Tr>
              <Table.Td fw={600}>User</Table.Td>
              <Table.Td>Only the referentials they are granted, directly or through their groups.</Table.Td>
            </Table.Tr>
          </Table.Tbody>
        </Table>
        <P>
          Rights are given to a user or a group at two levels: <b>Read</b> (browse, search, API, downloads, SQL for advanced users)
          and <b>Manage</b> (read + run or cancel updates, manual imports, editing internal referentials). Manage them from the
          Access tab of a referential, or from a user or group page.
        </P>
        <P>
          The scope of a right is <b>one referential</b>, <b>a whole category</b>, or <i>all referentials</i>. A right on a
          category covers the referentials it holds <b>now and later</b>: handing a team a dozen referentials is one row, and one
          added to the category afterwards is covered without touching the rights. The other way round too — moving a referential
          to another category changes who may read it, so a category is a permission boundary and not only a way to sort the list.
          On the Access tab of a referential, a right that comes from its category is shown marked <Code>category</Code> and is
          edited from the user or the group it was given to.
        </P>
        <H>System tasks</H>
        <P>
          <Anchor component={Link} to="/system/tasks">System › Tasks</Anchor> lists the scheduled background tasks:
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
          every sign-in, change and export is recorded in the <Anchor component={Link} to="/system/audit">audit log</Anchor>.
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
        <H>Excel workbooks</H>
        <P>
          The analysis lists the <b>sheets</b> of the workbook: choose the one to read (the first one by default), and a{' '}
          <b>range</b> (<Code>A1:F500</Code>) when the data does not start at the first cell.
        </P>
        <P>
          A column takes the type of its <b>first value</b>: a column of numbers holding one piece of text further down (a note in
          a “comment” column) cannot be read, and Excel gives no type for the column itself. RefExposer then reads{' '}
          <b>every column as text</b> by itself, keeps the option <Code>all_varchar</Code> in the definition — so the next updates
          read the file the same way — and says so in the preview and in the run log. Cast the columns that really are numbers or
          dates with a SQL transformation:
        </P>
        <Shell>{`SELECT * REPLACE (
         TRY_CAST(score AS DOUBLE) AS score,      -- NULL instead of an error on a bad cell
         TRY_CAST(day AS DATE)     AS day
       ) FROM {source}`}</Shell>
        <P>
          Other options: <b>Ignore cells that do not fit their column</b> (<Code>ignore_errors</Code>, they become empty — it
          loses data, so prefer reading as text), <b>Stop at the first empty row</b> (on by default, switch it off when the sheet
          has blank rows in the middle) and the header row, when the first row holds data rather than column names.
        </P>
        <H>SQLite databases and SQL deltas (NIST NSRL)</H>
        <P>
          <b>NIST NSRL</b>: paste the URL of the NIST download page as the source. The NSRL mode is detected: choose the set in{' '}
          <b>NIST NSRL publication</b> (e.g. <i>Modern, minimal</i>). The latest full database is downloaded once, then each quarterly
          delta (SQL file) is found on the page and replayed into it, in release order: nothing to list or maintain. A delta the database
          already holds is skipped, and a delta made for another release is refused without changing anything. The RDS 2.x conversion
          described by NIST is built in: tables <Code>NSRLFile</Code> (SHA-1, MD5, CRC32, FileName, FileSize, ProductCode…),{' '}
          <Code>NSRLProd</Code>, <Code>NSRLMfg</Code> and <Code>NSRLOS</Code>. A last column (<Code>SourceFile</Code>, or{' '}
          <Code>source_file</Code> in <Code>FILE</Code>, <Code>PKG</Code>…) gives the publication each row comes from: the full
          database or the delta that added it.
        </P>
        <P>
          Other SQLite databases: choose the table or view to publish (e.g. <Code>FILE</Code> for the NSRL). When the publisher
          provides SQL deltas, list their URLs in the <b>Deltas</b> field, oldest first: the full database is kept and each delta is
          applied once, in this order, in a transaction (a broken delta changes nothing). Add each new delta at the end of the list, or
          import it by hand with the <b>Import</b> button (<Code>.sql</Code> or the publisher's zip). A source of tens of GB is not
          analysed by the preview: set the format and the table by hand, the import runs in the background. For the NSRL, prefer the{' '}
          <i>minimal</i> database (distinct hashes, a few times smaller).
        </P>
        <H>Deltas merged into a table</H>
        <P>
          With <b>Deltas › Merge each new data set into the published data</b> in the definition, a table accumulates instead of being
          replaced: each download of the URL, import or file dropped in the import folder is merged. With a key, a row of the delta
          replaces the published row with the same key; without key, rows are added (identical rows kept once). An optional deletion
          column removes the rows flagged <Code>true</Code>. To start again from a full data set: <b>Import › New base</b>, or{' '}
          <b>Update › Replace with the source</b>.
        </P>
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
        <H>Choosing the columns when the source cannot be analysed</H>
        <P>
          A source of tens of GB is not downloaded by the preview, so the editor has no column list to offer. The{' '}
          <b>key</b>, the <b>search columns</b>, <b>Sort on</b> and the <b>indexes</b> are plain text fields: type the column names as
          they appear in the file (the header of a CSV, the fields of a JSON record, the columns of the Parquet file or of the SQLite
          table). The import checks them: an unknown sort or index column is refused with its name, a missing search column is only a
          warning in the log, and the columns actually produced are listed in the <b>Schema</b> tab afterwards.
        </P>
        <Alert color="gray" maw={860} p="sm" mb="sm">
          <Text size="sm">
            Above <Code>REFEX_LARGE_ROWS</Code> rows, the text search and the sort only work on the <b>sorted and indexed</b>{' '}
            columns, because anything else would read the whole file. A large referential with none of them can be browsed page by
            page and filtered with the <Code>startswith</Code> / <Code>eq</Code> operators, but not searched: that is why the sort
            column is the first thing to set on a passive DNS referential. When the search columns are left empty, the search uses
            every sorted and indexed text column.
          </Text>
        </Alert>
        <Shell>{`# Passive DNS: lookups on the name and on the address, both nearly instant
storage:
  sort_by: [rrname]       # the data is written in this order
  indexes: [rdata]        # a second copy, sorted on rdata
search_columns: [rrname, rdata]

# After the import, any of these works in milliseconds:
GET /api/referentials/pdns/rows?rrname=www.example.com
GET /api/referentials/pdns/rows?rrname__startswith=www.exam
GET /api/referentials/pdns/rows?q=www.exam          # prefix search on rrname and rdata
GET /api/referentials/pdns/lookup/www.example.com
POST /api/referentials/pdns/lookup  {"values": [...], "column": "rdata"}`}</Shell>
        <P>
          A filter on a column that is neither sorted nor indexed is still answered (without an exact total), unless the published
          file is above <Code>REFEX_FULL_SCAN_MAX</Code>: it is then refused at once, with the columns that would work, instead of
          running until the timeout.
        </P>
        <P>
          Measured on 1 billion passive DNS rows (30 GB of Parquet): import in about 65 minutes with two indexes, lookups and first pages
          in 120–270 ms, batches of 1,000 values in about 2.5 s, against 5–8 s for a full scan without sorting. Above{' '}
          <Code>REFEX_LARGE_ROWS</Code> (50 million rows by default), RefExposer samples the profile, disables facets and change
          tracking, offers Parquet downloads only and stops queries after <Code>REFEX_QUERY_TIMEOUT</Code> seconds.
        </P>
        <P>
          Sources of tens of GB: a <Code>.parquet.gz</Code> is decompressed while it is downloaded (the compressed file never sits on the
          disk), and a barely compressed Parquet is rewritten with zstd when the disk can hold the copy. An update never fills the disk:
          it keeps <Code>REFEX_MIN_FREE_DISK</Code> free (5 GB) and ends as <i>rejected</i> with the figures instead, the published version
          staying online. When space is short, the previous version is removed first.
        </P>
      </>
    ),
  },
  {
    id: 'admin-governance',
    title: 'Governance, personal data and history',
    admin: true,
    body: () => (
      <>
        <P>
          Three blocks of the <b>Description</b> step of the referential editor decide how its data is handled. They
          cost nothing to set and save a lot of questions later.
        </P>
        <H>Visibility and owner</H>
        <P>
          <b>Internal</b> (the default) or <b>Public</b>, plus who answers for the data and their team. It is shown
          on every card and page, filterable in the dashboard, and an e-mail notification channel can send the
          alerts of a referential to its owner. None of it rebuilds the referential.
        </P>
        <H>Personal data</H>
        <P>
          Name the columns that hold it. They are then masked <b>everywhere</b>, because the masking is applied by
          the DuckDB view the referential is read through: the table, the exports, the generated downloads, the
          column statistics, the search, the diffs, the SQL console, the MCP servers and the assistant. There is no
          caller to remember it, and anything added later is covered too.
        </P>
        <Table fz="sm" maw={860} mb="sm" withTableBorder>
          <Table.Tbody>
            {[
              ['Partial', 'a***e — the first and last characters. Enough to recognise a value you already know.'],
              ['Pseudonym', 'A stable 16-character hash, salted with the instance secret: rows can still be counted, grouped and joined on the column without the value. Pick this when the column is used in a join, or when a change in it must still show up in the diffs.'],
              ['Hidden', '*** — when even the shape must not leak.'],
            ].map(([k, v]) => (
              <Table.Tr key={k}>
                <Table.Td w={150} fw={600}>
                  {k}
                </Table.Td>
                <Table.Td>{v}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
        <P>
          Seeing the real values takes the <b>right</b> (the groups you choose, or an administrator) <b>and</b> an
          explicit request from the person (<Code>reveal=true</Code>, the switch in the interface), which is written
          to the audit log as <Code>pii.reveal</Code>. The assistant and the MCP servers can never reveal: they act
          for a model or a tool, which cannot give the consent.
        </P>
        <Alert color="orange" variant="light" mb="sm" maw={860}>
          <Text size="sm">
            Masking is <b>not</b> encryption: the published Parquet file still holds the real values, and the source
            files and the previous version are refused to anyone who may not reveal them. When the data must not be
            readable on disk either, tick <b>Confidential</b> as well — the two combine. A unified referential reads
            its sources <b>masked</b>, so personal data does not spread into a derived referential.
          </Text>
        </Alert>
        <H>Keeping versions, and going back to one</H>
        <P>
          The <b>Versions &amp; diff</b> tab of a referential holds what is kept of it. Two things end up there:
          <b> time travel</b>, which keeps every published version on its own and drops the oldest ones beyond the
          retention, and <b>Save the current state</b>, which keeps the version published right now with a label —
          what you do before a migration, a change of source, or anything you may want to undo. A kept version is a
          hard link on the Parquet file, so it costs no space until that version is replaced, and a state you saved
          is never dropped automatically.
        </P>
        <P>
          Any two of them are compared there: added, removed and modified rows, with the before and after of each
          column. Set <b>the columns identifying a row</b> (the key by default) so a modified row is told from a new
          one — without them, a change reads as one removal and one addition. The assistant reads these differences
          too, which is how it answers "what is new since Monday".
        </P>
        <P>
          <b>Restore</b> publishes the data of a kept version again. It is published as a <b>new version</b>, not
          rewound: the history keeps every step, the sorted copies, the profile and the quality rules are computed
          again, and the state published right now is saved first — so a restore made by mistake is one more restore
          away. The referential is then <b>frozen</b>, because the next scheduled update would read the source again
          and undo it.
        </P>
        <H>Freezing a referential</H>
        <P>
          <b>Freeze this version</b>, in the menu beside <b>Update</b>, keeps the published version as it is: the
          schedule, the check at startup and the sync folder leave it alone, and an update started by hand is
          refused while it lasts. Use it while you investigate something, or to hold a version that must not move.
          The banner on the referential says who froze it and why, with <b>Resume automatic updates</b> next to it.
        </P>
        <H>Connectors to a model, and small models</H>
        <P>
          <b>Settings › Assistant</b> holds as many <b>connectors</b> as you like — a server, an API, a model,
          generation parameters — and one is in use. Test one before switching to it, duplicate one to try
          another model on the same gateway (the copy carries the key), and switch with one click: the chat,
          the agent and the watchdogs follow immediately. API keys and header values are stored encrypted and
          never come back out of the server, so the form edits a connector it cannot read.
        </P>
        <P>
          Tick <b>This is a small model</b> for a 1B or 3B: agent mode then offers five read-only tools instead
          of twenty-five, runs one tool call at a time, concludes within four steps, keeps results and row
          limits small, uses a short prompt, and runs a tool call the model wrote as text. It answers questions
          over one or two referentials; it will not write anything. That is a property of the model, which is
          why it lives on the connector and not in the settings.
        </P>
        <H>Watchdogs</H>
        <P>
          A <Anchor component={Link} to="/watchdogs">watchdog</Anchor> runs the assistant on a schedule over a scope
          you choose and alerts only on a finding. It needs the agent mode of the assistant. It is confined to the
          referentials it names, runs with the rights of its own account through a session opened for the run, and
          never writes — whatever <b>Allow writes</b> permits in the chat. Administrators and advanced users can
          create one.
        </P>
      </>
    ),
  },
  {
    id: 'admin-combine',
    title: 'Unified referentials',
    admin: true,
    body: () => (
      <>
        <P>
          A <b>unified referential</b> is built from the published data of other referentials instead of a source of its own:{' '}
          <i>New referential › Other referentials</i>. Two shapes:
        </P>
        <List size="sm" spacing={6} maw={860} mb="sm">
          <List.Item>
            <b>Union</b> — the rows of every referential, one after the other, columns matched by name (a column one of them does
            not have is empty for its rows). The case of one list maintained by your team and the same kind of list replicated
            from another instance or another team: one referential covers both. An optional column records which referential each
            row comes from.
          </List.Item>
          <List.Item>
            <b>Enrichment</b> — the first referential gives the rows, the others add their columns, joined on a shared value (a
            left join: a row without a match keeps empty values). Choose the join column, which column of each other referential
            matches it when the names differ, and which columns to bring in. Added columns are named{' '}
            <Code>&lt;referential&gt;_&lt;column&gt;</Code> unless you switch the prefix off.
          </List.Item>
        </List>
        <P>
          Each source is read from its published version, so it has already passed its own validation and quality rules. The
          unified referential is <b>rebuilt automatically</b> whenever one of them publishes a new version (trigger{' '}
          <i>Source referential updated</i>), and it is skipped when nothing changed. It has its own key, search columns,
          indexes, quality rules, downloads and rights, and can be queried in SQL like any other. A <b>SQL transformation</b>{' '}
          applies on top of the combination: <Code>{'{source}'}</Code> is the combined result, <Code>{'{source0}'}</Code>,{' '}
          <Code>{'{source1}'}</Code>… each referential on its own.
        </P>
        <Shell>{`# Union of the lists of two teams, keeping where each row comes from
source:
  type: referential
  referentials: [team1-assets, team2-assets]
combine:
  mode: union
  source_column: _team

# Assets enriched with their owner and the criticality of their site
source:
  type: referential
  referentials: [assets, owners, sites]
combine:
  mode: enrich
  on: hostname                      # column of 'assets' holding the value to join on
  join_on: {sites: site_code}       # 'sites' matches it with its own column
  columns: {owners: [owner, team]}  # only these columns are added
  prefix: true                      # -> owners_owner, owners_team, sites_criticality…
key: hostname
transform: SELECT * EXCLUDE (sites_internal_note) FROM {source}`}</Shell>
        <List size="sm" spacing={4} maw={860} mb="sm">
          <List.Item>
            A source must have been <b>published at least once</b>: its columns and its file are what the build reads.
          </List.Item>
          <List.Item>
            A <b>confidential</b> source can only be combined into a confidential referential — otherwise its data would be
            published unencrypted, and the definition is refused.
          </List.Item>
          <List.Item>
            A referential cannot read itself, directly or through another unified referential, and a referential used by one
            cannot be deleted until the unified referential is changed or deleted (the <b>Source</b> tab lists both directions).
          </List.Item>
          <List.Item>
            Bloom filters and MaxMind DB files have no rows to combine. A unified referential has no reading options, no deltas
            and no source files of its own: it is rebuilt from its sources every time.
          </List.Item>
          <List.Item>
            Its own <b>schedule</b> is still useful as a safety net (e.g. daily) when a source is updated by a path that does not
            publish a new version, and it is skipped when the versions it read have not changed.
          </List.Item>
        </List>
      </>
    ),
  },
  {
    id: 'admin-peers',
    title: 'Synchronized instances',
    admin: true,
    body: () => (
      <>
        <P>
          An instance can replicate the referentials of another RefExposer instance, with their definitions (description, key, quality
          rules, SQL transformation, indexes, downloads). <b>On the instance that shares</b>: create a service account, give its group the
          read right on the referentials to share, and an API token. <b>Here</b>: <b>Administration › Referentials › Synchronized instances › Add a peer</b>,
          with its URL and the token, then choose the referentials (or all of them) and a prefix for the local identifiers.
        </P>
        <P>
          Each synchronization (on the schedule of the peer, or <b>Synchronize now</b>) creates or updates the replicas, then downloads the
          published data of those whose version changed over there. Replicas are read-only here; a referential no longer shared stays with
          its last data, marked <i>missing over there</i>. The token stays in the secret manager, only sent to the host of the peer.
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
            <b>Audit</b> — the <Anchor component={Link} to="/system/audit">audit log</Anchor> records sign-ins, administration, updates,
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
  const shown = (list: Section[]) => list.filter((s) => !s.visible || s.visible(me));
  const all = shown([...USER, ...PLUGINS, ...(me.is_admin ? ADMIN : [])]);
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
          {shown(USER).map(link)}
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
