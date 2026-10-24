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
  adds a *History & diff* tab comparing any two of them — rows added, removed and modified with their before
  and after. Agent and MCP tools `list_referential_snapshots` and `diff_referential_snapshots`.
- **Watchdogs** (off by default, administrators and advanced users): the assistant investigating a fixed set of
  referentials on a crontab or after a synchronization, returning a structured verdict and alerting only on a
  finding. Three ready-made investigations (shadow IT, external exposure drift, orphan accounts).
- **Alerts**: one list of what needs a look — watchdog findings, failed updates, stale data, quality rules
  failing, a volume that drifted — with the report, the log, the rules or the figures behind one click.
- Open-source release files: Apache 2.0 licence, `NOTICE`, contribution guide, code of conduct, security
  policy, issue and pull request templates, and CI on GitHub Actions.

### Changed

- A unified referential now reads its sources **masked**, so personal data does not spread into a derived
  referential by being combined.
- The configuration export no longer carries `pii.reveal_users`: account identifiers belong to one instance.
- A federated replica receives the masked data of its peer and says so in its description.
- The disk report has a category of its own for the versions kept by time travel.

### Security

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
