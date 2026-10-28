# Architecture

[← Documentation](README.md) · [RefExposer](../README.md)

## What it does, end to end

```
        WHERE DATA COMES FROM                    WHAT REFEXPOSER DOES                    WHO USES IT
  ─────────────────────────────────   ──────────────────────────────────────────   ──────────────────────────

  HTTP / HTTPS  URL, API, archive ┐
  Git           repository        │   ┌───────────── one referential ───────────┐
  Sync folder   pushed files      ├──▶│                                         │
  Import folder dropped files     │   │  acquire ─▶ read ─▶ transform (SQL)      │
  Upload        from the browser  │   │     │          │           │             │
  Internal      rows typed / API  │   │     │     csv tsv txt      │             │
  Peer instance replicated        │   │     │     json jsonl       ▼             │
  Referentials  union / enrich  ──┘   │     │     xml xlsx     validate          │   ┌─ interface: browse,
                                      │     │     parquet      min rows,         │   │  search, facets, SQL
  ┌────────────────────────────────┐  │     │     sqlite       max drop,         │   │
  │ a referential = a definition   │  │     │     bloom mmdb   unique key,       ├──▶├─ REST API: /rows,
  │ + its data + its history       │  │     │                  quality rules     │   │  /lookup, /export,
  │ + its rights                   │  │     │                      │             │   │  /download, /raw
  └────────────────────────────────┘  │     │                      ▼             │   │
                                      │     │                  publish           │   ├─ MCP server (agents)
  every run is recorded: who,         │     │                  version N+1,      │   │
  why, how long, how many rows,       │     │                  diff vs N,        │   └─ downloads: whole
  what changed, its log               │     └──── keep ──────▶  column profile   │      referential, per
                                      └─────────────────────────────────────────┘      version, cached
                                                     │                   │
                                            data/<id>/ (files)    PostgreSQL (metadata)
```

- A **referential** is one data set: where it comes from, how it is read, how it is checked, who may read it, and every
  version it has had. Nothing is served until a version passed its checks; a failed update leaves the previous version online.
- The **published form is always the same** whatever the source — a Parquet file read by DuckDB — which is why every
  referential offers the same filters, search, SQL, downloads and API, from 10 rows to billions.
- Binary referentials (**Bloom filter**, **MaxMind DB**) are published as they are: no table, but membership tests and IP
  lookups through the API, and the raw file for tools.
- Only the **backend** reads the data. The browser and the API clients never see the files.

## Components

```
┌──────────────────────────┐        ┌─────────────────────────────────────────────┐
│ frontend (nginx :8080)   │ /api → │ backend (FastAPI :8000)                     │
│ React + Mantine (SPA)    │        │  ├─ authentication, rights, audit           │
└──────────────────────────┘        │  ├─ scheduler: updates and system tasks     │
          ▲ published :8080         │  ├─ pipeline: download → checks → unpack    │
                                    │  │   → SQL transformation → validation      │
                                    │  │   → diff → profile → publication         │
                                    │  ├─ import and sync folder scans            │
                                    │  └─ DuckDB query engine (Parquet views)     │
                                    └──────┬────────────────────────┬─────────────┘
                                           │                        │
            ./config/*.yml, ./data/<id>/, ./import/        db (PostgreSQL 17)
            (definitions, referentials, manual imports)    accounts, rights, sessions, audit,
                                                           definitions, internal rows
```

```
backend/              FastAPI API + DuckDB engine (Python 3.12)
  app/config.py         model and loading of the definitions (YAML and database)
  app/ingest.py         update pipeline (download, source checks, transformation, validation, publication)
  app/gitsource.py      Git repository sources (shallow, partial, sparse fetch)
  app/preview.py        source analysis (download, format detection, preview), uploads
  app/downloads.py      downloadable full files, cache per version, raw files
  app/apidoc.py         OpenAPI generation per referential
  app/service.py        job queue, scheduling, status/freshness, manual imports, import folder
  app/tasks.py          system tasks (maintenance, integrity, security policy, backups)
  app/backup.py         configuration backups and restore
  app/engine.py         DuckDB views, SQL sandbox, exports
  app/sqlbuild.py       safe SQL generation (filters, search, readers)
  app/bloom.py          DCSO Bloom filter reader
  app/geoip.py          MaxMind DB reader (IP lookups)
  app/convert.py        JSON objects of records and XML to JSON Lines, split of large multi-document JSON
  app/sync.py           referentials discovered in the sync folder
  app/auth.py           sessions, tokens, roles, rights per referential, audit
  app/mfa.py            second factor: TOTP, WebAuthn, recovery codes
  app/ldapauth.py       LDAP / Active Directory authentication
  app/oidc.py           OpenID Connect (authorization code + PKCE)
  app/security.py       Argon2id / SHA3-256 hashing, password policy
  app/crypto.py         AES-256-GCM encryption, key derivation (secrets, confidential referentials)
  app/records_codec.py  storage form of the rows of internal referentials (encrypted when confidential)
  app/network.py        outgoing HTTP: proxy and company certificate authorities
  app/appsettings.py    settings edited from the interface (stored in the database)
  app/syslog.py         RFC 5424 syslog connector; app/logsetup.py: log levels
  app/models.py         relational schema (SQLAlchemy)
  app/migrations/       Alembic migrations (applied at startup)
  app/api/              REST routes
  requirements.in       declared dependencies (version ranges): the file to edit
  requirements.txt      generated by scripts/lock.sh: pinned versions and file hashes, installed by the images
  tests/                pytest tests (pipeline, API, sources, security, authentication, second factor, tasks…)
frontend/             React 19 + Mantine 8 + TanStack Query, served by nginx
config/               referential definitions (YAML files, optional)
data/                 published referentials, history, backups (created automatically)
import/               import folder: manual versions of existing referentials
sync/                 sync folder: every file or folder pushed there is a referential
enterprise/            building the images from company repositories (internal registry, PyPI, npm, mirrors)
examples/              example configurations and scripts (CybrefV2 definitions, API scripts)
scripts/               lock.sh (pin the Python dependencies), scan.sh (supply-chain scan), sbom.sh (SBOM)
Makefile               shortcuts: up, down, logs, build, test, typecheck, scan, sbom (make help)
```

## Layout of `data/`

```
data/<id>/
  raw/                         downloaded source files (unpacked; deleted when keep_raw: false)
  current.parquet              served version (current.mmdb / current.bloom for MaxMind DB and Bloom filters)
  previous.parquet             previous version (used to compute the changes)
  current.by_<column>.parquet  sorted copies ("indexes", large referentials)
  exports/                     downloadable files generated for the current version
  meta.json                    status: schema, profile, sources, last run, manual import, frozen version…
  runs.jsonl                   update history (with logs)
data/.backups/                 configuration backups (system task)
data/.system/                  history of the system tasks
data/.tmp/                     uploads, previews, exports in progress
data/.secret_key               generated encryption key, when REFEX_SECRET_KEY is not set
```

## Where the data is really stored

Two places hold everything, and both must be backed up: the **`data/` volume** (the data sets themselves, as files) and
the **PostgreSQL database** (accounts, rights, settings and the rows of internal referentials). Nothing is kept in the
frontend container, and no data is stored in the browser beyond the session cookie and display preferences.

| What | Where exactly | Encrypted at rest |
|---|---|---|
| Published version of a referential, previous version, sorted copies | `data/<id>/current.parquet`, `previous.parquet`, `current.by_<column>.parquet` (or `current.bloom` / `current.mmdb`) | only if the referential is **confidential**: Parquet Modular Encryption, AES-GCM, one key per referential |
| Downloaded source files | `data/<id>/raw/` (deleted after the build when `keep_raw: false`, when the referential is confidential, or above `REFEX_KEEP_RAW_MAX`) | no |
| Generated downloads (CSV, Excel, JSON, Parquet) | `data/<id>/exports/`, one set per version | no — a confidential referential generates them per request and never keeps them |
| Status, schema, column profile, source and run summary | `data/<id>/meta.json` | the column profile (which holds values: min, max, most frequent) is encrypted for a confidential referential |
| Update history and logs | `data/<id>/runs.jsonl` (capped by `REFEX_KEEP_RUNS`) | no |
| Manual imports kept as the current source | `data/<id>/manual/<date>/` | no (not kept for a confidential referential) |
| **Rows of an internal referential** | PostgreSQL table `internal_records`, one row per record — **not** in `data/` | AES-256-GCM per row, bound to the referential, when it is confidential; the key column is then replaced by a keyed fingerprint (HMAC-SHA256) |
| Published form of an internal referential | `data/<id>/current.parquet`, rebuilt from the database a few seconds after each edit | as above, if confidential |
| Definitions created from the interface | PostgreSQL table `referential_definitions` | no (credentials inside them are `${secret:…}` references, never values) |
| Definitions written by hand | `config/*.yml`, read-only in the interface | no |
| Accounts, groups, rights, sessions, API tokens | PostgreSQL | passwords: Argon2id · session and API tokens: SHA3-256 fingerprints (the token itself is never stored) |
| Secrets of the secret manager, LDAP bind password, OIDC client secret, proxy password, peer tokens | PostgreSQL | AES-256-GCM with a key derived from `REFEX_SECRET_KEY` |
| Audit log | PostgreSQL (retention from the settings), and optionally sent to syslog | no |
| Settings edited in the interface, branding, notification channels | PostgreSQL | secret fields only |
| Configuration backups | `data/.backups/` (definitions, settings, rights — never the data sets) | secrets inside them stay encrypted |
| System task history | `data/.system/` | no |
| Uploads, previews, exports in progress, DuckDB spill files of large sorts | `data/.tmp/` (cleaned on a TTL and after each query) | no |
| Encryption key, when `REFEX_SECRET_KEY` is not set | `data/.secret_key`, mode 600 | — it **is** the key: set `REFEX_SECRET_KEY` instead so the key does not sit on the volume it protects |

- **Keys** are never stored next to their ciphertext when `REFEX_SECRET_KEY` is set: each use derives its own key
  (HKDF-SHA256) from that one secret — one per referential for Parquet, one for internal rows, one for their key
  fingerprints, one for the settings. Back it up: without it the published versions can be rebuilt from their sources,
  but the rows of confidential internal referentials are lost.
- **Volumes**: `./data` and `./import` are mounted into the backend container (`/data`, `/import`); `./config` and
  `./sync` as well. The database keeps its own volume. See [Containers](install.md#containers).
- Details of the confidential mode, and what it does not cover, are in
  [Confidential referentials](data-governance.md#confidential-referentials).

## Update cycle

1. **Download** in streaming, with conditional requests (`ETag` / `If-Modified-Since`): an unchanged source answers `304` and is not downloaded again. Files are first written to a staging folder. A Git source is only fetched when its branch or tag points to a new commit.
2. **Source checks**: an empty file, a file containing only whitespace, an HTML/text error page instead of data (`404: Not Found`, maintenance page…), a JSON/Parquet/Excel file without the expected signature, or an unreadable archive ends the run as **Corrupted**. The current version is kept and the error is shown on the referential and the dashboard.
3. **Unpacking** of `.gz`, `.zip`, `.tar(.gz)` archives.
4. **Fingerprint** SHA-256 of the files and of the configuration: if nothing changed, the published version is kept as is (status *Unchanged*).
5. **Transformation** DuckDB to Parquet (zstd), with an optional SQL query defined in the configuration. A result with no rows is also treated as a corrupted source.
6. **Validation**: minimum number of rows, maximum drop tolerated, key uniqueness. An invalid version is **rejected** and the previous one stays online.
7. **Differences** with the previous version (additions, removals, changes per key).
8. **Profile** of the columns: fill rate, cardinality, min/max, quantiles.
9. **Atomic publication**: the file is replaced, then the view is refreshed.

Freshness: a referential is marked *Stale* when its last successful update is older than `max_age`. Without `max_age`, the threshold is two intervals of its cron.
