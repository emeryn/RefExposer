# Changelog

Notable changes to RefExposer. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project uses [semantic versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Governance metadata** on every referential: `visibility` (`internal` or `public`) and an owner with their
  team, shown on every card and page, with a quick filter in the dashboard.
- **Personal data masking**: `pii.columns` are masked by the view the referential is read through, so rows,
  lookups, exports, statistics, search, diffs, generated downloads, the SQL console, the MCP servers and the
  assistant are all masked. Revealing them needs the right *and* an explicit request, which is audited. Three
  strategies: `partial`, `hash` (stable pseudonym) and `hide`.
- **Time travel**: `storage.time_travel` keeps each published version as a hard link on its Parquet file and
  adds a *Versions & diff* tab comparing any two of them — rows added, removed and modified with their before
  and after. Agent and MCP tools `list_referential_snapshots` and `diff_referential_snapshots`.
- **Saved states and restore**: the version published now can be **saved on demand** with a label, whether
  time travel is on or not; such a state is never dropped by the retention, is compared with the published
  version like any other, and can be **restored**. A restore publishes that data again as a *new* version
  (sorted copies, profile and quality rules recomputed), saves the state it replaces first so it can itself be
  undone, and freezes the referential so the next scheduled update does not undo it.
- **Freezing a referential** at any time (*Freeze this version*, beside *Update*), not only through a manual
  import: the schedule, the check at startup and the sync folder leave the published version alone, and an
  update started by hand is refused until the updates are resumed.
- **Watchdogs** (off by default, administrators and advanced users): the assistant investigating a fixed set of
  referentials on a crontab or after a synchronization, returning a structured verdict and alerting only on a
  finding. Three ready-made investigations (shadow IT, external exposure drift, orphan accounts).
- **Alerts**: one list of what needs a look — watchdog findings, failed updates, stale data, quality rules
  failing, a volume that drifted — with the report, the log, the rules or the figures behind one click.
  Alerts are **marked as read per account** (an alert comes back unread when the thing behind it happens
  again) and can be **deleted from one account's feed** — out of the list and out of its counts, listed in a
  *Deleted* tab that puts any of it back, and coming back on its own if the thing behind it happens again.
  An administrator sets **what counts as an alert** in *Settings › Notifications & alerts*: the kinds
  reported, the severity below which nothing is, and the volume change that makes a drift.
- A watchdog can watch **every referential its account may read** instead of a fixed list, and can be asked to
  investigate **only what is new**: its question then carries the published version of each referential in
  scope and what its last version changed, and it is told to ask for the changes (`get_changes`,
  `diff_referential_snapshots`) rather than read everything again.
- The **trace of an agent** keeps what each tool answered and how long it took, written to a watchdog run **as
  it happens**: an investigation can be followed while it works and read back once it is over, in the
  watchdogs page and behind an alert, as in the chat.
- An **optional referential filter per conversation** of the assistant: the agent is restricted to the
  referentials chosen for that conversation instead of exploring the whole catalogue — and a referential it
  creates for you joins the filter, so narrowing a conversation never stops the work.
- **Service accounts can hold the advanced role**: an integration that writes then queries with SQL and creates
  referentials (an internal one, or a query kept as one), getting the manage right on what it creates. It can
  never administrate.
- **Saved connectors to an inference server**: several ways of reaching a model side by side (server, API,
  model, generation parameters), switched with one click, with a connection test that works on a connector
  before it is in use and a duplicate that carries its secrets. The API key and the header values are kept in
  one AES-256-GCM blob bound to the connector and are never returned by any endpoint. The connection of an
  instance configured earlier becomes a connector named *Default* at the next start.
- **Small-model mode** on a connector (1B, 3B): agent mode then offers five read-only tools instead of
  twenty-five, honours one tool call per step, concludes within four, caps results at 2 000 characters and row
  limits at 10, uses a short prompt, and runs a tool call the model wrote as text instead of calling properly
  (only for a tool it was offered).
- Open-source release files: Apache 2.0 licence, `NOTICE`, contribution guide, code of conduct, security
  policy, issue and pull request templates, and CI on GitHub Actions.
- **Building from company repositories** ([`enterprise/`](enterprise/README.md)): the base images, the Python
  index, the npm registry, the Debian and Alpine mirrors and the DuckDB extensions are all build arguments
  with public defaults. Scripts mirror the base images and vendor the dependencies, and a build with nothing
  reachable at all (`--network none`) is documented and verified.

### Fixed

- **Searching or filtering a column whose type is not character data** (a `UUID` key, a `BLOB`) no longer fails
  with *No function matches the given name and argument types `lower(UUID)`*: such a column reads as text in
  the interface but is not `VARCHAR` to DuckDB, so every text operation now casts it explicitly — and the
  prefix search of a large referential, which compares the column with a literal, only uses columns that
  really are character data.
- The **Alerts badge in the menu** counted every alert instead of the unread ones, so marking them read left
  it there; it now says the same thing as the bell in the header.
- The **referential filter of a conversation** could not be picked with the mouse: its dropdown was rendered in
  a portal, so a click on an option counted as a click outside the popover and closed it before the pick
  registered (the keyboard worked). It now opens inside the popover.

### Changed

- A unified referential now reads its sources **masked**, so personal data does not spread into a derived
  referential by being combined.
- The configuration export no longer carries `pii.reveal_users`: account identifiers belong to one instance.
- A federated replica receives the masked data of its peer and says so in its description.
- The disk report has a category of its own for the versions kept by time travel.

### Security

- **The address of a federation peer is no longer concatenated** with the catalog path: an address carrying a
  query or a fragment (`https://peer/#`) made the call ask for the root of the host instead of the catalog —
  with the API token of the peer's service account in the header. The address is now rebuilt from its parts,
  and one carrying `?` or `#` is refused when the peer is saved. The host was, and stays, checked by
  `network.validate_outbound()` before every call, with redirects not followed.
- **A rejected settings value is no longer written to the log.** A pydantic validation message quotes the
  input it refused, and the input there may be a decrypted secret: only the field and the kind of error are
  logged now. `appsettings.SECRETS` is renamed `SECRET_FIELDS`, which is what it holds — the *names* of the
  encrypted fields.
- The frontend is built on **`nginx-unprivileged:*-alpine-slim`**: 21 OS packages instead of 71, without the
  dynamic modules it never used (xslt, image-filter, njs) nor `curl`. `libxml2`, `libcurl` and `brotli` are
  not in the image at all, which clears eight advisories a registry scanner was reporting — three of them with
  no fix published. The image scans clean.
- `scripts/scan.sh` now lists, per package, the vulnerabilities with **no fix published** (they cannot gate a
  build, and were invisible before), reports MEDIUM as well, and gained a `published` target that scans the
  images **as they are in the registry** — the comparison that explains a registry report differing from a
  local build.

- **Redirects are no longer followed** on the calls whose address a request supplies and
  `network.validate_outbound()` has just checked (the proxy test, the federation catalog). Validating an
  address was worth nothing if a `301` could then send the request elsewhere — and the federation call carries
  the API token of a service account on the peer. The sources still follow redirects.
- A **bulk discovery scan** validates the folder it is given, and the address that actually answered, like the
  other calls a request can point somewhere.
- `reveal` is a reserved query parameter, so it can never be read as a column filter.
- The scope of a watchdog travels HMAC-signed with every tool call, and `CurrentUser.level()` refuses anything
  outside it — a scheduled agent cannot drift onto another referential.

## [1.0.0]

First version: referentials declared from the interface or in YAML, downloaded from HTTP, Git, files, an
upload, an import folder, a sync folder or another instance; normalized to Parquet, validated, checked against
quality rules and published. Explorer with filters and facets, generated REST API per referential, cached
downloads, read-only SQL over DuckDB, internal and unified referentials, deltas, Bloom filters and MaxMind
databases, large-volume support, confidential referentials encrypted at rest, local / LDAP / OpenID Connect
sign-in with approval, roles and per-referential rights, API tokens and service accounts, two-factor
authentication, secret manager, notifications, audit log, syslog forwarding, system tasks and backups,
in-application assistant and MCP servers.

[Unreleased]: https://github.com/emeryn/RefExposer/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/emeryn/RefExposer/releases/tag/v1.0.0
