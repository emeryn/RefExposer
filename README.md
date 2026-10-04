<p align="center"><img src="frontend/public/logo.svg" width="96" alt="RefExposer logo"></p>

# RefExposer

One tool to **expose, monitor and query heterogeneous referentials** (CSV, TSV, JSON, JSON Lines, TXT, XML, Parquet, Excel, Bloom filters, MaxMind DB / GeoIP), whether they are downloaded from the Internet, dropped as files, or maintained directly in the application.

Each referential is declared from the administration interface (URL, Git repository or uploaded file, with a live preview) or in a YAML file. RefExposer downloads it, normalizes it to Parquet, checks the new version and publishes it. It is then available in four ways:

- **a web interface**: status dashboard, explorer with filters, column profiles, differences between versions, history, SQL console (administrators and advanced users), global search, and built-in user and administrator guides (**Help** menu);
- **a REST API documented automatically**: an OpenAPI specification is generated for every referential from its schema (one typed filter per column, real examples). It covers filters, search, single and batch lookups, and exports;
- **downloads** of the whole referential (CSV, CSV.gz, Excel, JSON, JSONL, Parquet, original source files), cached per version with a SHA-256 fingerprint;
- **read-only SQL** (DuckDB), joins between referentials included.

Beyond downloaded sources:

- **Internal referentials** are created and edited inside RefExposer (row editor, file loads, write API), for the lists your teams maintain themselves.
- **Sync folder**: files pushed into a folder (scp / rsync over ssh, every week for instance) become referentials by themselves, imported again when they change.
- **Manual imports**: a version can be uploaded from the referential page or dropped in an import folder, for example when the source is unreachable or only sent by e-mail.
- **Corrupted sources are refused**: an empty file, an HTML error page or a broken archive never replaces the current version; the error is shown on the referential.

Access is protected:

- **Identity sources**: local accounts, an **LDAP directory / Active Directory** or **OpenID Connect** (Keycloak…), with group synchronization.
- **Approval**: an administrator approves every new access.
- **Roles** (administrator, advanced user, user), **rights per referential**, API tokens, **service accounts** for tools (API only) and an audit log.
- **Storage**: accounts, settings, definitions and internal rows in PostgreSQL. **Confidential referentials** are encrypted at rest (data files, internal rows and their keys).

The security mechanisms are summarized in [Enterprise security](#enterprise-security).

An **HTTP/HTTPS proxy**, company certificate authorities and the **branding** (title, subtitle, company logo) are configured from the interface. The URL of the application is set once with `REFEX_PUBLIC_URL`.

RefExposer starts empty: add referentials from the interface, with YAML files in `config/`, or by pushing files into `sync/`.

---

## Getting started

### 1. Requirements

- **Docker Engine 24+** with **Docker Compose v2.24+** (`docker compose version`), or **Docker Desktop** on Windows or macOS.
- About 2 GB of RAM and a few GB of disk. The space depends on the referentials: each one is stored as compressed Parquet.
- Outgoing HTTPS access to the sources (directly or through a proxy, see [Configuration](#configuration-environment-variables)).

### 2. Get the project

```bash
git clone <repository url> refexposer
cd refexposer
```

### 3. Folders and permissions

The project folder already contains the folders mounted in the containers:

| Folder | Content | Access from the containers |
|---|---|---|
| `config/` | referential definitions in YAML (optional) | read-only |
| `data/` | published versions, sources, history, configuration backups | read / write |
| `import/` | manual imports (`import/<referential id>/`) | read / write |
| `sync/` | files pushed to become referentials (scp, rsync…) | read-only |

**Nothing to prepare by hand.** If a folder is missing, Docker creates it. At every start, the short `init-permissions` service gives `data/` and `import/` to the account running the backend (uid / gid **10001**), then stops. This includes the files left by an older version or copied as root.

Only on Linux, and only in these cases:

- **Files you put in `config/` or `sync/`** must be readable by everybody, which is the usual default (644 for files, 755 for folders). After an `rsync` or `scp` with restrictive rights, run `chmod -R o+rX sync config`.
- **SELinux** (RHEL, Rocky, Fedora): add `:z` to the volumes in a `docker-compose.override.yml`, e.g. `./data:/data:z`.
- **You prefer `data/` and `import/` to belong to your own account** (to read them without `sudo`): set `REFEX_UID` and `REFEX_GID` in `.env` to the values of `id -u` and `id -g`. The image is then built for this account.

### 4. Configuration

```bash
cp .env.example .env
chmod 600 .env
```

Edit `.env` and set at least:

| Variable | Value |
|---|---|
| `REFEX_PUBLIC_URL` | address typed by the users, e.g. `http://localhost:8080` or `https://refexposer.my-company.com` (see [Public URL](#public-url)) |
| `POSTGRES_PASSWORD` | database password: `openssl rand -hex 24` (letters and digits only) |
| `REFEX_SECRET_KEY` | encryption key of the secrets and confidential data: `openssl rand -base64 48`. **Keep a copy outside the server.** |
| `REFEX_ADMIN_USERNAME` / `REFEX_ADMIN_PASSWORD` | initial administrator; the password must be changed at first sign-in |

Without a `.env`, the defaults of `docker-compose.yml` apply: `admin` / `ChangeMe-Now-2026!`, and a secret key generated in `data/.secret_key`. This is fine for a test, never for real data.

`POSTGRES_PASSWORD` and the initial administrator are only used at the **first** start: change them before it.

### 5. Start

```bash
docker compose up -d --build
docker compose ps
```

The first build takes a few minutes. `docker compose ps` should then show:

- `db`, `backend` and `frontend` **healthy**;
- `init-permissions` **Exited (0)**, which is normal: it only runs at startup.

The database schema is created automatically. If the backend does not start, read `docker compose logs backend`: the problems found at startup (folder not writable, secret key too short…) are explained there with the fix.

### 6. First sign-in

Open **http://localhost:8080** (or your `REFEX_PUBLIC_URL`) and sign in with the initial administrator. **A new password is required at first sign-in.**

Then, as needed:

- **Administration › Settings**:
  - outgoing proxy and company certificate authorities;
  - LDAP directory or OpenID Connect;
  - two-factor authentication policy;
  - syslog forwarding;
  - title and logo.
- **Administration › Users**: accounts, groups, rights per referential, service accounts for tools.
- **Administration › Tasks**: maintenance and backup schedules.

### 7. First referential

Any of these:

- **Administration › Referentials › New referential**: from a URL, a Git repository or an uploaded file, with a live preview;
- drop a CSV file into `sync/`: it becomes a referential by itself within a minute;
- copy `config/cybref.yml.sample` to `config/cybref.yml`: sample cybersecurity referentials (CISA KEV, EPSS, CWE…).

| URL | Content |
|---|---|
| http://localhost:8080 | Web interface |
| http://localhost:8080/help | User and administrator guides |
| http://localhost:8080/api/catalog/docs | Generated documentation of the referentials (Swagger, `?ref=<id>` for one) |
| http://localhost:8080/api/docs | Full technical API documentation (Swagger) |
| http://localhost:8080/api/redoc | API documentation (ReDoc) |

**On a server** (domain name, HTTPS, security headers, rate limiting): see [Expose behind an nginx reverse proxy](#expose-behind-an-nginx-reverse-proxy).

### Stop, update, back up

- **Stop**: `docker compose down`. The referentials stay in `./data`, the database in the `db-data` Docker volume. `docker compose down -v` **deletes the database**.
- **Update**: `git pull`, then `docker compose up -d --build`. Database migrations and folder permissions are handled at startup, including when coming from a version whose containers ran as root.
- **Back up**: the `db-data` volume (`docker compose exec -T db pg_dump -U refexposer refexposer > refexposer.sql`), the `data/` folder, and `REFEX_SECRET_KEY` kept apart. The configuration is also backed up daily in `data/.backups` (see [System tasks](#system-tasks)).

### Containers

The containers do not run as root:

| Container | Account | Port inside the container |
|---|---|---|
| backend | `refex` (uid / gid **10001**, or `REFEX_UID` / `REFEX_GID`) | 8000 |
| frontend | `nginx` (uid 101, official `nginx-unprivileged` image) | 8080 |
| db | `postgres` (the official image drops its privileges) | 5432 |
| init-permissions | root, only the `CHOWN`, `DAC_OVERRIDE` and `FOWNER` capabilities, no network; runs once at startup, then stops | — |

They also have:

- a **read-only root filesystem**: only the volumes and an in-memory `/tmp` are writable;
- **no Linux capability** (`cap_drop: ALL`) and `no-new-privileges`.

The backend code belongs to root, so the application cannot modify it.

Outside `docker compose` (Kubernetes, another orchestrator…), `data/` and `import/` must belong to the account of the backend: run `chown -R 10001:10001` on them, or use an init container like `init-permissions`. Otherwise the backend stops at startup and names the paths it cannot write.

---

## Expose behind an nginx reverse proxy

On a server, put RefExposer behind a reverse proxy that serves it on a domain name with HTTPS: it terminates TLS, adds the security headers and limits the request rate. The example below uses nginx installed on the host, in front of the `frontend` container.

**1. Only the local proxy reaches the interface.** In `.env`:

```bash
FRONTEND_PORT=127.0.0.1:8080              # published on the loopback only
REFEX_PUBLIC_URL=https://refexposer.example.com
```

`REFEX_PUBLIC_URL` must be the address typed by the users. It gives `Secure` cookies, the OpenID Connect callback, the URLs of the generated documentation and the domain of the security keys (WebAuthn). Then apply it: `docker compose up -d`.

**2. Certificate**: from your company PKI, or Let's Encrypt (`certbot certonly --webroot -w /var/www/letsencrypt -d refexposer.example.com`). An ECDSA P-384 key is a good choice.

**3. nginx site** (e.g. `/etc/nginx/conf.d/refexposer.conf`):

```nginx
# Rate limits per client IP: sign-in and second factor, API
limit_req_zone $binary_remote_addr zone=refex_login:10m rate=10r/m;
limit_req_zone $binary_remote_addr zone=refex_api:10m rate=50r/s;
limit_req_status 429;

# Content-Security-Policy: strict for the interface, nothing to run for the API, CDN assets for the API docs
map $uri $refex_csp {
    ~^/api/(docs|redoc|catalog/docs)  "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; font-src 'self' data: https://fonts.gstatic.com; img-src 'self' data: https://fastapi.tiangolo.com https://cdn.jsdelivr.net; worker-src blob:; connect-src 'self'; frame-ancestors 'none'";
    ~^/api/                           "default-src 'none'; frame-ancestors 'none'";
    default                           "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'";
}

upstream refexposer {
    server 127.0.0.1:8080;
    keepalive 16;
}

server {
    listen 80;
    listen [::]:80;
    server_name refexposer.example.com;
    location /.well-known/acme-challenge/ { root /var/www/letsencrypt; }
    location / { return 301 https://$host$request_uri; }
}

server {
    listen 443 ssl;
    listen [::]:443 ssl;
    http2 on;
    server_name refexposer.example.com;

    ssl_certificate     /etc/letsencrypt/live/refexposer.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/refexposer.example.com/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    # Hybrid post-quantum key exchange first (nginx linked with OpenSSL 3.5+, see `nginx -V`; remove it otherwise)
    ssl_ecdh_curve X25519MLKEM768:X25519:prime256v1:secp384r1;
    ssl_prefer_server_ciphers off;
    ssl_session_cache shared:refexposer:10m;
    ssl_session_timeout 1d;
    ssl_session_tickets off;

    server_tokens off;
    client_max_body_size 2g;                      # uploads of source files

    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains" always;
    add_header Content-Security-Policy $refex_csp always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-Frame-Options "DENY" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header Permissions-Policy "camera=(), microphone=(), geolocation=(), payment=(), usb=()" always;
    add_header Cross-Origin-Opener-Policy "same-origin" always;

    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header X-Forwarded-For $remote_addr;   # replaced, not appended: the client cannot forge its IP

    location = /api/auth/login { limit_req zone=refex_login burst=5 nodelay;  proxy_pass http://refexposer; }
    location /api/auth/mfa/    { limit_req zone=refex_login burst=10 nodelay; proxy_pass http://refexposer; }

    location /api/ {
        limit_req zone=refex_api burst=200 nodelay;
        proxy_pass http://refexposer;
        proxy_read_timeout 600s;                  # long exports and imports
        proxy_request_buffering off;              # uploads streamed to the backend
        proxy_buffering off;                      # downloads streamed to the client
    }

    location / { proxy_pass http://refexposer; }
}
```

The headers are only declared at the `server` level: in nginx, a `location` that declares its own `add_header` loses all of them. Then run `nginx -t && systemctl reload nginx`.

**4. Check**:

```bash
curl -sI https://refexposer.example.com/ | grep -iE "strict-transport|content-security"
openssl s_client -connect refexposer.example.com:443 -groups X25519MLKEM768 </dev/null 2>/dev/null | grep -i "negotiated\|group"
```

- **nginx in a container** (e.g. on a shared Docker host): attach it to the `refexposer_default` network and use `server frontend:8080;` as upstream. Remove the published port of `frontend` in a `docker-compose.override.yml`.
- **Another proxy** (Traefik, HAProxy, Apache, a load balancer): the same rules apply. Forward to the `frontend` port, pass `Host` and `X-Forwarded-Proto: https`, and *replace* `X-Forwarded-For` with the client address. Allow 2 GB uploads and 10-minute reads, and do not buffer `/api/`.

---

## Architecture

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
  tests/                pytest tests (pipeline, API, sources, security, authentication, second factor, tasks…)
frontend/             React 19 + Mantine 8 + TanStack Query, served by nginx
config/               referential definitions (YAML files, optional)
data/                 published referentials, history, backups (created automatically)
import/               import folder: manual versions of existing referentials
sync/                 sync folder: every file or folder pushed there is a referential
example/n8n/          n8n workflow using the API
```

### Layout of `data/`

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

### Update cycle

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

---

## Adding a referential

### From the interface (recommended)

**Administration › Referentials › New referential** opens a 4-step wizard:

1. **Source**: one or more URLs (with optional authentication or HTTP headers, e.g. an API token), a Git repository, or a file uploaded by drag and drop (CSV, TSV, TXT, JSON, JSONL, XML, Parquet, Excel, Bloom filter, MaxMind DB, possibly `.gz` or `.zip`, 2 GB maximum).
2. **Reading**: the source is downloaded and analysed.
   - The format is detected automatically, as well as the delimiter and the header of CSV files. For a nested JSON document, the lists of records are found and suggested.
   - Reading options and an optional SQL transformation are set here, with a preview of the first rows, the types and the row count.
3. **Description**: name, identifier, category, key, search columns, schedule, validation rules, large-volume storage, pre-generated download formats and groups with read access.
4. **Publication**: save, then import immediately if the option is ticked.

These definitions are stored in the database. They can be changed later with **Configure** (referential page or administration list), which can also **replace the uploaded file** and then triggers a new version. **View as YAML** exports the definition to version it in `config/`.

> Administrators are trusted users. A source or an SQL transformation makes the server access the network and files, like a configuration file would. Only give the admin role to people who need it.

### With a YAML file

Add a `config/<name>.yml` file (or complete an existing one), then click **System › Reload the configuration**, or call `POST /api/system/reload`. No restart is needed.

```yaml
referentials:
  - id: cisa-kev                    # [a-z0-9_-], used in URLs; SQL table name: cisa_kev
    name: CISA – Known Exploited Vulnerabilities
    description: Catalog of actively exploited vulnerabilities.
    category: Cybersecurity         # grouping in the interface
    tags: [cve, vulnerabilities]
    homepage: https://www.cisa.gov/known-exploited-vulnerabilities-catalog
    license: Public domain
    owner: CISA

    source:
      type: http                    # http (default) | git | local (see below)
      urls:                         # one or more URLs
        - https://www.cisa.gov/sites/default/files/csv/known_exploited_vulnerabilities.csv
      headers: {}                   # extra HTTP headers (e.g. API token)
      extract: "*.csv"              # files to keep from an archive

    format: csv                     # csv | tsv | json | jsonl | txt | xml | parquet | xlsx | bloom | mmdb
    options: {}                     # DuckDB reader options (see below)
    transform: |                    # optional SQL (see below)
      SELECT * REPLACE (CAST(dateAdded AS DATE) AS dateAdded) FROM {source}

    key: cveID                      # key: lookup, differences between versions
    search_columns: [cveID, product] # full-text search columns (default: text columns)
    schedule: "15 */4 * * *"        # cron (REFEX_TIMEZONE time zone); absent = manual
    max_age: 1d                     # staleness threshold (30m, 12h, 2d, 1w)
    enabled: true
    downloads: [csv.gz, xlsx]       # downloads pre-generated after each update
    validation:
      min_rows: 1000                # minimum number of rows expected
      max_drop_pct: 10              # rejects a version that loses more than 10% of its rows
      unique_key: true              # rejects a version whose key is not unique
```

### Formats and options

`options` are passed as is to the matching DuckDB reader:

| Format | Reader | Useful options |
|---|---|---|
| `csv`, `tsv` | `read_csv` | `delim`, `header`, `skip`, `nullstr`, `quote`, `all_varchar`, `ignore_errors`, `null_padding`, `dateformat`, `columns`… |
| `json` | `read_json` | `records_path: a.b` (nested array of records), `records_path: "*"` or `a.*` (object whose values are the records, key in `records_key`, default `_key`), `maximum_depth`, `columns`… |
| `jsonl` | `read_json` (newline-delimited) | same |
| `txt` | one line = one record | `column` (column name, default `value`), `comment` (default `#`), `skip_empty` |
| `parquet` | `read_parquet` | |
| `xlsx` | `read_xlsx` (excel extension) | `sheet`, `range`, `header`, `all_varchar` |
| `xml` | converted to JSON Lines, then `read_json` | `records_path`: name of the repeated record element (default: the likeliest one, frequent and rich) |
| `bloom` | DCSO Bloom filter (published as is) | `normalize`, `pattern`, `enrich_bulk_url`, `enrich_key` |
| `mmdb` | MaxMind DB (published as is, IP lookups) | |

### SQL transformations

The `transform` field is a DuckDB query producing the final data set. Available placeholders:

| Placeholder | Value |
|---|---|
| `{source}` | reader over all the files (format and options applied) |
| `{source0}`, `{source1}`… | reader over the files of the 1st, 2nd… URL |
| `{files}`, `{files0}`… | literal list of paths (to call a reader yourself) |

Example of a join between two files (IMDb):

```yaml
transform: |
  SELECT b.tconst, b.primaryTitle AS title, r.averageRating AS rating
  FROM {source0} b LEFT JOIN {source1} r USING (tconst)
```

See `config/cybref.yml.sample` for many real examples (nested JSON, XML, header-less TSV, transformations).

### JSON and XML shapes

The analysis step suggests how to read a document and shows the result live:

- **Array of records inside a document** (`{"vulnerabilities": [...]}`): `records_path: vulnerabilities`.
- **Object whose values are the records** (`{"1945182": [{...}], "1945136": [{...}]}`, `{"0001": {"name": ...}}`): `records_path: "*"` (or `executables.*` when nested). Each value, or each item of a value that is a list, becomes a row, and the object key goes to the `records_key` column (`_key` by default).
- **XML** (`format: xml`): every repeated record element becomes a row; attributes and child elements become columns, repeated children become lists, and mixed XHTML content becomes plain text. `records_path` names the element (e.g. `Weakness` for the MITRE CWE catalog).
- **Nested keys that differ only by case** (`EventId` / `EventID`), which DuckDB's schema detection refuses: the analysis retries with `maximum_depth` and keeps that option, so deeper levels are stored as JSON.
- **Several large JSON documents** (e.g. every yearly NVD feed, several GB once unpacked): records are extracted one document at a time to JSON Lines before being read together, which keeps memory bounded by the largest file. All 25 NVD years (398,466 CVEs) import in about 3.5 minutes.

`config/cybref.yml.sample` defines the 59 referentials of [github.com/emeryn/cybref](https://github.com/emeryn/cybref) (threat intelligence, living-off-the-land projects, cloud IP ranges, OUI and USB ids, NVD, CPE, CWE…), all checked with a full import. Rename it to `.yml`, or copy the entries you need, then reload the configuration.

### Bloom filters (e.g. CIRCL hashlookup)

Some referentials are not published as a table but as a **Bloom filter**. This is the case of [CIRCL hashlookup](https://circl.lu/services/hashlookup/): about 1 GB for roughly 418 million SHA-1 hashes of known files (NIST NSRL, Linux distributions, Windows…), updated monthly.

The `bloom` format (DCSO format) downloads them like any HTTP source and publishes them as is. They are then queried by value:

- **offline**: no hash is sent to a third party;
- **meaning of the answers**: “absent” is certain, “present” is probable (the false positive rate is shown: about 1e-4 for hashlookup, 1.5e-4 measured on 20,000 random values);
- **where**: single lookup, list search (10,000 values), global search and documented API;
- **optional enrichment**: present values can be confirmed and detailed (file name, product, NSRL source…) with the online bulk API (`enrich_bulk_url`). It is off by default, and only the values present in the filter are sent.

The full hashlookup database is not published as a download: the Bloom filter is the way CIRCL offers to query it offline.

```yaml
format: bloom
options:
  normalize: upper                  # case of the values in the filter
  pattern: "[0-9A-F]{40}"           # accepted values (SHA-1)
  enrich_bulk_url: https://hashlookup.circl.lu/bulk/sha1
  enrich_key: SHA-1
```

### Git repositories (GitHub, GitLab, Gitea…)

A referential can come from **files of a Git repository**. It works with GitHub, GitLab, Gitea / Forgejo, Bitbucket or any Git server over HTTPS. In the editor, choose **Git repository** as the source. Give:

- the URL of the repository;
- the files to read (a glob relative to the repository root);
- optionally a branch, tag or commit (the default branch when empty);
- for a private repository, an access token.

- **Only what is needed is downloaded**: one commit (`--depth 1`), the matching files only (sparse, partial fetch). A large repository costs little.
- **No useless update**: `git ls-remote` runs first. While the branch or tag still points to the published commit, nothing is fetched and the run ends as *unchanged*. The published commit is shown in the sources of the referential.
- **Private repositories**: the token is sent as HTTP Basic credentials, with the user name `oauth2` by default (accepted by GitHub, GitLab and Gitea; it can be changed). The token never goes in the URL nor on a command line, is masked in every message, and is not returned by the API. Keep it out of the definition with a `${REFEX_SOURCE_…}` variable of `.env`.

| Server | Token | Read permission |
|---|---|---|
| GitHub | fine-grained personal access token (or classic token) | *Contents: Read-only* on the repository |
| GitLab | project, group or personal access token | `read_repository` |
| Gitea / Forgejo | access token | `repository: read` |

- **Network**: the proxy and the company certificate authorities of the network settings apply, as for downloads. Only `https` is allowed by default (`REFEX_GIT_PROTOCOLS`): no `ssh`, `file` or `ext` transports, no submodules, no hooks, no credential prompts. A fetch is stopped after `REFEX_GIT_TIMEOUT` seconds (900).
- **Limits**: files stored with **Git LFS** are refused with an explanation, because the repository only holds pointers. Publish such a file as a release asset and use its URL instead. The fetched files are deleted after the import when `storage.keep_raw: false` or for a confidential referential.

```yaml
- id: internal-ranges
  name: Internal IP ranges
  source:
    type: git
    repository: https://gitlab.example.com/network/ipam-exports.git
    ref: main                         # branch, tag or commit (default branch when omitted)
    path: exports/ranges-*.csv        # glob relative to the repository root
    token: "${REFEX_SOURCE_GITLAB_TOKEN}"   # private repository only
  format: csv
  key: cidr
  schedule: "0 * * * *"               # hourly check: nothing fetched while the commit does not change
```

### MaxMind DB / GeoIP (GeoLite2, GeoIP2, DB-IP, IPinfo…)

The `mmdb` format handles databases in the **MaxMind DB** format (`.mmdb`): GeoLite2 / GeoIP2 City, Country and ASN, and the `.mmdb` editions of DB-IP or IPinfo. They are published **as is**, so tools can download the raw file and use it directly. RefExposer also answers IP lookups.

- **Raw file for tools**: Logstash, Suricata, Zeek, Graylog, nginx `geoip2`, Fluentd… download it at a stable URL, `GET /api/referentials/<id>/raw`:
  - the file keeps its original name (`GeoLite2-City.mmdb`);
  - `ETag` / `If-None-Match` and `Last-Modified` / `If-Modified-Since` answer `304` when nothing changed, and `HEAD` returns the headers only;
  - authentication uses an API token (preferably from a [service account](#service-accounts)), either `Authorization: Bearer` or HTTP Basic with the token as password, for tools that only know `user:password`.
- **Lookups**: the API returns the record of the network containing an address, with `network` (CIDR). The UI and the global search accept an IPv4 or IPv6 address.
  - `GET /api/referentials/<id>/lookup/81.2.69.142` returns one address; `POST .../lookup` takes up to 10,000 addresses.
  - `flat=true` gives dotted keys (`country.iso_code`, `city.name`…), for tables and CSV.
- **Safe updates**: a file that cannot be read never replaces the published database. An update is also refused when:
  - the database type changes, for example a City URL that suddenly serves ASN;
  - the database is older than the one already published.

  In both cases, *force* accepts it. The previous version stays downloadable (`/download/previous`).
- **MaxMind archives**: the `.tar.gz` holds the database plus `COPYRIGHT.txt` and `LICENSE.txt`. The `.mmdb` member is picked automatically.
- **Credentials**: MaxMind downloads use HTTP Basic (`account_id:license_key`). Keep the key out of the definition with a `${REFEX_SOURCE_…}` variable from `.env`. Only variables with this prefix are expanded, so other secrets can never be sent to a source.

```yaml
- id: geolite2-city
  name: GeoLite2 City
  category: Network
  license: GeoLite2 End User License Agreement (attribution required)
  source:
    urls: [https://download.maxmind.com/geoip/databases/GeoLite2-City/download?suffix=tar.gz]
    basic_auth: "${REFEX_SOURCE_MAXMIND_ACCOUNT}:${REFEX_SOURCE_MAXMIND_KEY}"
  format: mmdb
  schedule: "0 7 * * 3,6"          # MaxMind publishes GeoLite2 on Tuesdays and Fridays
```

Fetching it from a tool, downloading only when it changed:

```bash
# curl: -z compares with the local file (If-Modified-Since)
curl -fsS -u "x:$REFEX_TOKEN" -z GeoLite2-City.mmdb -o GeoLite2-City.mmdb \
  https://refexposer.example.com/api/referentials/geolite2-city/raw
# wget: -N (timestamping), Basic credentials after the challenge
wget -N --user=x --password="$REFEX_TOKEN" https://refexposer.example.com/api/referentials/geolite2-city/raw -O GeoLite2-City.mmdb
```

### Large volumes (passive DNS, hundreds of millions of rows and more)

The `storage` block adapts storage to very large referentials:

```yaml
storage:
  sort_by: [rrname]                 # data written sorted: fast lookups on rrname
  indexes: [rdata, rrname_rev]      # extra sorted copies: fast lookups on these columns
  keep_raw: false                   # deletes the downloaded files after import (304 still handled)
  keep_previous: false              # no previous version (disk space)
  profile: sample                   # column profile on a sample
```

**Principle**: for each block of rows of the Parquet file, DuckDB keeps the minimum and maximum value of every column. On sorted data, an exact, prefix or range lookup only reads the few relevant blocks instead of the whole file. Each index is a full copy sorted on another column. The engine picks the right copy from the filter automatically, and these copies are visible in the SQL console (`pdns__by_rdata`…).

**Automatic guardrails** above `REFEX_LARGE_ROWS` rows (50 million by default):

- sampled profile; key uniqueness check and change tracking disabled;
- text search turned into a **prefix** search on the indexed columns (case sensitive);
- facets disabled, column statistics on a sample;
- global sort limited to the sort column;
- total not computed when the filter is not on an indexed column;
- downloads limited to Parquet;
- every API query is stopped after `REFEX_QUERY_TIMEOUT` seconds (60 by default).

To find every sub-domain of a domain (`*.example.com`), add a reversed column in the transformation (`reverse(rrname) AS rrname_rev`), index it, and search the reversed form by prefix (`rrname_rev__startswith=moc.elpmaxe.`).

**Benchmark** (1 billion synthetic passive DNS rows, 30 GB of raw Parquet, a single Docker host):

| Step | Result |
|---|---|
| Import | 65 min in total: sort 10 min, `rdata` index 10 min, `rrname_rev` index 14 min, profile 1 min; peak RAM ~25 GB, ~0.6 GB idle |
| Storage | current 31 GB + `by_rdata` 34 GB + `by_rrname_rev` 30 GB (+ raw 29 GB unless `keep_raw: false`) |
| Exact, prefix, index and suffix lookups, `q=` search, first page | 120–270 ms |
| Batch lookup of 1,000 values | ~2.5 s |
| SQL `LIKE 'prefix%'` on the sort column | ~110 ms |
| Same lookups without sorting (full scan) | 4.7–7.8 s |

For 200 GB of raw passive DNS (about 2.5 to 3 billion rows), expect a 3 to 4 hour import, about 300 GB of disk with two indexes, and lookups that stay around 200–300 ms. Plan enough RAM for the sort (or set `REFEX_DUCKDB_MEMORY_LIMIT`, DuckDB then spills to disk) and fast local storage for `data/`.

> A column whose name is also an API parameter (`count`, `sort`, `limit`, `offset`, `q`, `columns`) is filtered with the explicit operator: `?count__eq=3`. The interface does this automatically.

### Local referentials

With `source.type: local`, put the files in `data/<id>/` (e.g. `data/my-ref/raw/export.csv`) and give the pattern in `path`. The scheduler detects changes from the file fingerprints, so another tool can feed RefExposer by simply writing files.

---

## Internal referentials

An internal referential lives in RefExposer itself: there is no remote source. It is shown with an **internal** badge and is browsed, searched, documented, downloaded and queried in SQL like any other referential.

- **Creation**: administrators and **advanced users** use **New internal referential** (dashboard, menu, or Administration › Referentials). They give a name, then the columns (name, type: text, integer, decimal number, yes/no, date, date and time, list of texts; required; description) and the **key** column. An *automatic* key is generated when a row is added without it (UUID for text, next number for integers). An advanced user gets the manage right on the referentials they create.
- **Editing**: the **Edit** tab lists the rows with a search; clicking a row opens a form generated from the schema. **Load from a file** (CSV, Excel, JSON, Parquet) adds/updates rows in bulk or replaces them all. Every change is validated against the column types and published as a new version once the edits settle (`REFEX_INTERNAL_PUBLISH_DELAY`, 2 s by default: a burst of edits gives one version); who changed what is in the audit log.
- **Schema changes**: the **Columns** button changes the columns; existing rows are migrated (renames keep the values, removed columns are dropped) and the change is refused if a row does not fit a new type or a new required column.
- **API**: tools can write rows with the same rights (manage right on the referential). The exact endpoints and the row format are documented in the API tab of each internal referential.

```bash
# Add or replace a row by its key (here the key is "cidr"; encode "/" as %2F)
curl -X PUT -H "$H" -H "Content-Type: application/json" \
  -d '{"cidr": "10.12.0.0/16", "site": "Lyon", "owner": "network"}' \
  "http://localhost:8080/api/referentials/internal-ranges/records/10.12.0.0%2F16"

# Change some columns only
curl -X PATCH -H "$H" -H "Content-Type: application/json" -d '{"owner": "secops"}' \
  "http://localhost:8080/api/referentials/internal-ranges/records/10.12.0.0%2F16"

# Synchronize many rows at once (up to 50,000): upsert + delete,
# or "replace_all": true to drop every row absent from "upsert"
curl -X POST -H "$H" -H "Content-Type: application/json" \
  -d '{"upsert": [{"cidr": "10.12.0.0/16", "site": "Lyon"}], "delete": ["10.9.0.0/16"]}' \
  "http://localhost:8080/api/referentials/internal-ranges/records/_bulk"

# Load a file (mode=upsert or replace)
curl -X POST -H "$H" -F file=@ranges.xlsx -F mode=upsert \
  "http://localhost:8080/api/referentials/internal-ranges/records/_import"
```

Other routes: `GET /api/referentials/{id}/records` (with `q`, `limit`, `offset`), `GET|DELETE /records/{key}`, `POST /records` (one row or `{"records": [...]}`), and `POST|PUT|DELETE /api/internal-referentials[/{id}]` to create, change the columns of, or delete an internal referential.

---

## Confidential referentials

A referential marked **confidential** (`confidential: true`, or the switch in its editor) has its data **encrypted at rest**. It is still used normally through the interface, the API and SQL by the people who have access to it. Access rights do not change: confidentiality protects the stored data (disk, volumes, backups, database dumps), not the access.

| Stored data | Protection |
|---|---|
| Published version, previous version, sorted copies (Parquet) | Parquet Modular Encryption, AES-GCM (one key per referential) |
| Rows of an internal referential (PostgreSQL) | AES-256-GCM, bound to the referential |
| Keys of these rows | replaced by a keyed fingerprint (HMAC-SHA256): uniqueness and lookups by key still work |
| Column profile (min, max… in `meta.json`) | AES-256-GCM |
| Downloaded source files, manual imports, the internal export used to publish | deleted once the version is published |
| Downloads (CSV, Excel, Parquet…) | generated in clear for each request, then deleted: never cached |

- **Keys**: they are derived (HKDF-SHA256) from `REFEX_SECRET_KEY`, one per referential and per use.
  - Set `REFEX_SECRET_KEY` in the environment (or a secret manager). Otherwise the generated key is stored in `data/.secret_key`, on the same volume as the data it protects.
  - **Back up the key**: without it, the published versions can be rebuilt from their sources, but the rows of confidential internal referentials are lost.
- **Changing the setting** publishes the referential again:
  - the previous version is removed instead of being kept with the other encryption state;
  - the rows of an internal referential are encrypted or decrypted in the database.
- **Not covered**:
  - data in memory, and data in transit (use HTTPS, see [Expose behind an nginx reverse proxy](#expose-behind-an-nginx-reverse-proxy));
  - the temporary files DuckDB may write during very large sorts (`data/.tmp`, deleted after the query);
  - files that a *local* or *sync* source reads from outside RefExposer;
  - SQL queries logged at the `trace` level;
  - Bloom filters and MaxMind databases, which are served as is to tools and cannot be confidential.
- **Searching** an internal referential decrypts its rows in the application, because the database only holds ciphertexts. This stays fast up to a few hundred thousand rows.

## Sync folder

The simplest way to publish files produced elsewhere: push them into `sync/` (mounted read-only as `/sync` in the backend) and RefExposer does the rest. **Every file or folder of the sync folder is a referential**, created at the next scan (every `REFEX_SYNC_POLL_SECONDS`, 60 s) and imported again whenever its files change.

| In the sync folder | Referential |
|---|---|
| `sync/epss_scores.csv` | `epss-scores`, from one file |
| `sync/threatfox/*.json` | `threatfox`, from every data file of the folder (same format) |
| `sync/threatfox/refexposer.yml` | optional overrides of the generated definition |

```yaml
# sync/threatfox/refexposer.yml — every key is optional
name: ThreatFox IOCs
description: Recent IOCs from abuse.ch
category: Threat intelligence
key: ioc_id
options:
  records_path: "*"
  records_key: ioc_id
validation:
  min_rows: 1000
```

Weekly push from another server, e.g. in a cron:

```bash
rsync -av --delay-updates --delete exports/ refexposer@refexposer.example.com:/opt/refexposer/sync/
```

- **Formats** come from the extensions (CSV, TSV, TXT/LIST, JSON, JSON Lines, XML, Parquet, Excel, Bloom); `.gz` and `.zip` files are unpacked; the records of a JSON document are found by themselves. Everything else is set in `refexposer.yml` (same keys as a YAML definition, except `source` and `schedule`).
- **Copies in progress are never read**: hidden files (rsync temporary files), `*.part` and `*.tmp` are ignored, and an import starts only once the files have been untouched for `REFEX_SYNC_SETTLE_SECONDS` (30 s). `--delay-updates` makes rsync switch all files at the end of the transfer.
- **Same checks as any source**: an empty file or an error page is refused and the published version is kept; unchanged content does not create a new version.
- **Removal**: removing a file or folder removes the referential at the next scan (its data stays in `data/` and comes back with the files).
- **Conflicts and errors** (identifier already used by a YAML or interface referential, invalid `refexposer.yml`) are shown on the System page.
- **Access**: only administrators see a new synchronized referential; grant access from its Access tab as for any referential.
- The ssh account only needs write access to the `sync/` folder of the host (`chown`/`chmod` it for that account); the backend only reads it.

The sync folder defines referentials. The **import folder** (`import/<referential id>/`, below) is different: it replaces one version of an existing referential, for example when its remote source is down.

## Public URL

`REFEX_PUBLIC_URL` (e.g. `https://refexposer.my-company.com`) is the address users and tools use to reach RefExposer. It is set once and used for:

- the **OpenID Connect callback** (`<public URL>/api/auth/oidc/callback`, to declare in Keycloak);
- the **generated API documentation** (`servers` of the OpenAPI specifications, Swagger “Try it out”);
- the **URLs and commands shown in the interface** (download links, `curl` examples, help pages);
- **`Secure` cookies** when it starts with `https://`;
- the **domain of the security keys and passkeys** (WebAuthn): they only work at this address.

On a server, it is the HTTPS address of the reverse proxy (see [Expose behind an nginx reverse proxy](#expose-behind-an-nginx-reverse-proxy)).

When it is empty, the address is derived from each request.

## Branding

**Administration › Settings › Appearance** sets the **title** and **subtitle** shown in the header, on the sign-in page and in the browser tab, and the **company logo** (PNG, JPEG, WebP or SVG, 512 KB maximum) shown instead of the RefExposer icon. The logo is served at `/api/branding/logo` (public, as the sign-in page needs it; an SVG cannot run scripts).

## Manual imports

Sometimes the remote source is unreachable, wrong, or the file arrives another way. Users with the **manage** right can then provide the new version themselves; the import is clearly shown to every user (banner on the referential, *manual import* badge, trigger in the history).

- **Upload**: the **Import** button of the referential page accepts one or more files (compressed `.gz`/`.zip` accepted). For a referential combining several URLs, send one file per source, named like the remote file.
- **Import folder**: files copied into `import/<referential id>/` (mounted as `/import` in the backend, `REFEX_IMPORT_DIR`) are imported automatically once their copy is finished (checked every `REFEX_IMPORT_POLL_SECONDS` seconds). They then move to `.done/<date>/`, or to `.failed/<date>/` with an `ERROR.txt` file explaining the refusal. Each referential folder contains a `README.txt`; unknown folders are listed on the System page. For large files, copy under a temporary `*.part` name, then rename.
- **Same checks**: manual files go through the same pipeline as a download (corrupted source checks, format, validation rules). If they are refused, the current version is kept.
- **Freeze**: ticking **Freeze this version** when uploading suspends the scheduled updates of the remote source, until someone clicks **Resume automatic updates** (`DELETE /api/referentials/{id}/pin`). Without it, the next scheduled update replaces the manual version with the remote source again.

```bash
curl -X POST -H "$H" -F files=@known_exploited_vulnerabilities.csv -F pin=true \
  "http://localhost:8080/api/referentials/cisa-kev/import"
```

---

## Users and rights

### Roles

| Role | Can… |
|---|---|
| **Administrator** | everything: all referentials, users, groups, rights, settings, audit log, System page, configuration reload |
| **Advanced user** | the referentials granted to them, plus the SQL console and creating internal referentials (they get the manage right on the ones they create) |
| **User** | only the referentials granted to them, directly or through their groups (no SQL console) |

### Rights per referential

A right links a **user or a group** to **a referential** (or `*` = all referentials) with a level:

| Level | Allows |
|---|---|
| **Read** | see the status, explore, search, lookup, export, download, query in SQL (administrators and advanced users) |
| **Manage** | read + start, force or cancel an update, manual imports, editing internal referentials |

The effective right is the highest of the direct rights and those of the groups. A referential that is not granted is **invisible**: absent from the lists, the global search and the SQL console, with a `404` answer when it is requested directly.

Rights are managed in the interface (**Administration › Users / Groups**, or the **Access** tab of a referential) or with `/api/admin/grants`.

### Service accounts

For tools and scripts (SIEM, enrichment, CI…), an administrator creates a **service account** in **Administration › Users › New service account** (or `POST /api/admin/service-accounts`):

- it **never signs in to the interface** (no password, no session): it only calls the API with **tokens created by an administrator** from its page (`POST /api/admin/users/{id}/tokens`), shown once and revocable at any time;
- it lists and queries the referentials it is granted, **directly or through its groups**, exactly like a user (role *user*: no SQL console, no administration);
- it cannot create or revoke its own tokens; disabling the account cuts all its tokens at once. The last use of its tokens is shown in the user list.

```bash
curl -H "Authorization: Bearer rfx_…" "http://localhost:8080/api/referentials/cisa-kev/lookup/CVE-2021-44228"
```

### LDAP directory, OpenID Connect and access approval

**Administration › Settings** connects:

- **an LDAP directory or Active Directory**.
  - Service account, user search base and filter (OpenLDAP and AD presets provided), attributes.
  - Groups read from `memberOf` or by a group search, filtered by a regular expression.
  - LDAPS or StartTLS.
  - The username typed on the sign-in page is first looked up among the local accounts, then in the directory.
- **an OpenID Connect provider (Keycloak…)**.
  - *Authorization code* flow with PKCE, state and nonce. The signature of the id_token is checked against the JWKS.
  - Claims are configurable, including `realm_access.roles` for Keycloak roles.
  - The **redirect URL** to declare in Keycloak is shown in the page.
  - An *internal discovery URL* is used when the backend does not reach Keycloak through the same address as the browsers.

Each section has a **Test** button giving a step-by-step diagnosis. The directory can also be tested with a given account: the DN found, the attributes and the groups are then shown.

**Mandatory approval**: the first sign-in of a person through LDAP or OIDC creates a **pending** account.

- They see a waiting page that refreshes by itself. Administrators see the request (badge in the menu, notice on the dashboard and on the Users page).
- **Approve**: the admin chooses the role and optional local groups.
- **Refuse**: sign-in is then refused; the decision can be reversed.

**Groups**: directory or OIDC groups are created automatically in RefExposer, and their members are synchronized at each sign-in.

- They receive rights on referentials like a local group.
- Their members cannot be edited by hand. An external user can however be added to extra local groups.
- An optional **administrators group** gives the admin role to its members, still after approval.

External accounts have no password in RefExposer. A local account with the same username takes precedence.

**Network**: the HTTP/HTTPS proxy (with credentials and exceptions) and the extra certificate authorities (PEM, e.g. a TLS-inspecting proxy) are used for downloads and OIDC calls. The CAs are also used for LDAPS. Without a proxy set in the interface, the `HTTP_PROXY` / `HTTPS_PROXY` variables of the container apply.

**Secrets**: the LDAP service account password, the OIDC client secret and the proxy password are encrypted in the database with **AES-256-GCM**.

- The key is derived from `REFEX_SECRET_KEY` (at least 32 characters), or generated in `data/.secret_key` when the variable is absent: **back up this file**.
- These secrets are never sent back to the interface. A field left empty keeps the saved value.

### Two-factor authentication

**Local and LDAP accounts** can confirm their sign-in after the password with a second factor, from **My account › Two-factor authentication**. Two methods are available, and an account can use both:

- **Authenticator app** (TOTP, RFC 6238): Google Authenticator, Microsoft Authenticator, FreeOTP, 1Password… The account scans a QR code, then types a first code to confirm.
- **Security keys and passkeys** (WebAuthn / FIDO2): YubiKey, Windows Hello, Touch ID, a phone… An account can register several. Browsers only allow them over HTTPS (or on `localhost`), at the address set in `REFEX_PUBLIC_URL`.

Each account also gets **10 one-time recovery codes** with its first second factor. They are shown once and can be regenerated.

**Policy** (**Administration › Settings › Two-factor**):

- **optional** (default), **required for some groups**, or **required for everybody**;
- when it is required, an account without a second factor enrols one at its next sign-in;
- if a phone or key is lost, **Administration › Users** resets every second factor of the account.

Not concerned:

- OpenID Connect accounts: the identity provider handles their second factor;
- service accounts: they only use API tokens;
- API tokens in general.

The feature is off when single sign-on is the only way to sign in (`REFEX_LOCAL_LOGIN=false` and no LDAP directory).

Security:

- The session is only opened after the second factor: the password step returns a short-lived (5 min), encrypted challenge.
- A TOTP code is accepted once (replay protection), with one 30-second step of tolerance.
- The signature counter of security keys is checked, which detects cloned keys.
- Wrong codes count towards the account lockout, like wrong passwords.
- TOTP secrets are encrypted at rest; recovery codes are stored as fingerprints.

### Single sign-on only

`REFEX_LOCAL_LOGIN=false` disables username / password sign-in for **local accounts** in the interface:

- the sign-in page only offers the configured single sign-on: the OpenID Connect button, and the form when the LDAP directory is enabled;
- sessions already opened by local accounts are no longer accepted;
- API tokens keep working, including those of local accounts and of [service accounts](#service-accounts).

Configure single sign-on first, and make sure an administrator can sign in with it (administrators group of the directory or of OpenID Connect, or approval of the account), then set `REFEX_LOCAL_LOGIN=false` and restart the backend. If nobody can sign in any more, set it back to `true` and restart. The backend logs a warning at startup when local sign-in is disabled and no single sign-on is enabled.

### Initial administrator and recovery

At startup, if **no active administrator** exists, the `REFEX_ADMIN_USERNAME` / `REFEX_ADMIN_PASSWORD` account is created. Its password must be changed at first sign-in (`REFEX_ADMIN_MUST_CHANGE_PASSWORD=true`). If `REFEX_ADMIN_PASSWORD` is empty, a random password is generated and printed in the backend logs.

Lost access: set `REFEX_ADMIN_RESET_PASSWORD=true` with a new `REFEX_ADMIN_PASSWORD`, restart the backend (`docker compose up -d backend`), then set the variable back to `false`. The account is reactivated and unlocked, and its sessions are closed.

### Security

- **Passwords** hashed with **Argon2id** (RFC 9106: 64 MiB, 3 iterations, parallelism 4, 256-bit digest). The parameters can be tuned (`REFEX_ARGON2_*`) and hashes are upgraded at the next sign-in.
  - There is no standardized “post-quantum” password hashing algorithm. Post-quantum algorithms (ML-KEM, ML-DSA) are for key exchange and signatures.
  - Against a hash, a quantum computer only brings Grover's quadratic speed-up. A 256-bit digest keeps 128 bits of security in this model, and the memory cost of Argon2id strongly penalizes this kind of massively parallel attack.
- **Password policy**: 12 characters minimum (`REFEX_PASSWORD_MIN_LENGTH`), 3 kinds of characters, not containing the username.
- **Lockout** after 10 failures for 15 min (`REFEX_LOGIN_MAX_ATTEMPTS`, `REFEX_LOGIN_LOCKOUT_MINUTES`). The response time is the same whether the username exists or not.
- **Server-side sessions**: `HttpOnly`, `SameSite=Lax` cookie limited to `/api`, sliding expiration (`REFEX_SESSION_TTL_HOURS`). Tokens are stored as SHA3-256 fingerprints. Changing requests require the `X-Requested-With: RefExposer` header (CSRF protection). Changing a password closes the other sessions.
- **Personal API tokens** (`rfx_…`, 256 random bits), shown once, stored as SHA3-256, with optional expiration (a maximum can be enforced with `REFEX_API_TOKEN_MAX_DAYS`). They carry exactly the rights of their owner.
- **Sandboxed SQL console**: each set of rights has its own DuckDB connection, which only sees the allowed tables. Only the Parquet files of these tables can be read (`allowed_paths`), which also blocks bypasses such as `read_parquet('/data/…')`.
- **Audit log**: sign-ins (successful or not, with the source: local, LDAP, OIDC), access requests, approvals and refusals, settings changes, password changes, tokens, administration of accounts, rights and referentials, updates, manual imports, internal row edits, exports, downloads and SQL queries. The user, IP and details are recorded.
- Source credentials (`source.headers`, `basic_auth`, Git `token`) are never returned to the users by the API, and can reference `${REFEX_SOURCE_…}` environment variables instead of being stored in the definitions.
- Behind HTTPS, cookies are `Secure` as soon as `REFEX_PUBLIC_URL` starts with `https://`.

---

## Searching a referential

The **Search** tab of each referential has two modes:

- **Multi-criteria search**: full-text search (terms highlighted in the results) combined with facets computed automatically.
  - Columns with few distinct values become checkboxes, with the number of results for each value.
  - Numeric and date columns become ranges.
  - Counters are recomputed at each selection. The search can be shared by link, exported or opened in the explorer.
- **Search by list of values**: paste up to 10,000 values (identifiers, codes…) on the key or another column.
  - The result separates the values found (with their details) from the missing ones.
  - It can be exported to CSV.

The **Global search** (top bar, `Ctrl+K`) queries every accessible referential at once.

---

## Downloading a referential

The **Download** button of each referential and the **Downloads** page offer the complete published version:

| Format | Stable link |
|---|---|
| CSV, compressed CSV, Excel, JSON, JSON Lines, Parquet | `/api/referentials/<id>/download/{csv,csv.gz,xlsx,json,jsonl,parquet}` |
| Original source files (zip when there are several) | `/api/referentials/<id>/download/source` |
| Previous version (Parquet) | `/api/referentials/<id>/download/previous` |
| Raw file of a MaxMind DB or Bloom filter, for tools | `/api/referentials/<id>/raw` |

- Each file is generated **once per version**, at the first request or right after the update when it is listed in `downloads:`. It is then served from the cache. Files of older versions are deleted.
- The SHA-256 fingerprint is returned in `X-Checksum-SHA256` and `ETag`. `If-None-Match` answers `304` when the file has not changed, which makes script synchronization easy.
- Excel is limited to 1,048,575 rows: beyond that, use CSV or Parquet. Large referentials are offered as Parquet only.
- For a confidential referential, every download is generated for the request and deleted afterwards (nothing is cached in clear).

```bash
curl -OJ -H "Authorization: Bearer $REFEX_TOKEN" http://localhost:8080/api/referentials/cisa-kev/download/csv
```

To export a **selection** (with filters), use the *Export* menu of the explorer or `/api/referentials/<id>/export`.

---

## Using the API

> An n8n workflow (list, query, lookup, download) is provided in [`example/n8n`](example/n8n/README.md).

Each referential has **documentation generated automatically** from its actual schema. It is regenerated with every new version:

- the **API** tab of the referential: endpoints, parameters (one typed filter per column), exact row format, examples taken from the data, and a **Try it** button that runs the request and gives the matching `curl` command;
- **Swagger**: `/api/catalog/docs?ref=<id>` for one referential, `/api/catalog/docs` for all accessible ones;
- the **OpenAPI 3.1 specification** to import into Postman, Insomnia or a client generator: `/api/referentials/<id>/openapi.json`, or `/api/catalog/openapi.json` for all. It only describes the referentials the user can access.

Every route except `/api/health` and the Swagger page itself requires authentication. For scripts, create an **API token** from **My account** (top-right menu) and pass it in the `Authorization` header:

```bash
export REFEX_TOKEN=rfx_xxxxxxxxxxxxxxxx
H="Authorization: Bearer $REFEX_TOKEN"
```

The filters of the web explorer match the API parameters exactly. The 🔗 button of the explorer copies the URL of the current view, and the **API** tab of each referential gives ready-to-use examples.

```bash
# List / filter / sort / paginate
curl -H "$H" "http://localhost:8080/api/referentials/imdb-titles/rows?title_type=movie&votes__gt=100000&sort=-rating&limit=20"

# Full-text search, selected columns
curl -H "$H" "http://localhost:8080/api/referentials/cisa-kev/rows?q=log4j&columns=cveID,product"

# Lookup by key
curl -H "$H" "http://localhost:8080/api/referentials/cisa-kev/lookup/CVE-2021-44228"

# Batch enrichment (up to 10,000 keys)
curl -X POST -H "$H" "http://localhost:8080/api/referentials/epss/lookup" \
  -H "Content-Type: application/json" -d '{"values": ["CVE-2021-44228", "CVE-2014-0160"]}'

# Export (csv, xlsx, json, jsonl, parquet), with the same filters
curl -H "$H" -o kev.xlsx "http://localhost:8080/api/referentials/cisa-kev/export?format=xlsx&vendorProject=Microsoft"

# Read-only SQL on the accessible referentials (administrators and advanced users)
curl -X POST -H "$H" "http://localhost:8080/api/sql" -H "Content-Type: application/json" \
  -d '{"sql": "SELECT k.cveID, e.epss FROM cisa_kev k JOIN epss e ON e.cve = k.cveID ORDER BY e.epss DESC LIMIT 10"}'

# Search across referentials
curl -H "$H" "http://localhost:8080/api/search?q=CVE-2021-44228"
```

In Swagger (`/api/docs`), the **Authorize** button accepts an API token.

**Filter syntax**: `column=value` (equality) or `column__operator=value`. For `in` / `nin`, the value is a comma-separated list, or a JSON array (`["a, b", "c"]`) when the values contain commas. Operators: `eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `contains`, `ncontains`, `startswith`, `endswith`, `in`, `nin`, `isnull` (`true`/`false`), `regex`. Filters are combined with AND. On a list column (e.g. `genres`), `eq`/`contains` apply to the items.

Reserved parameters: `q`, `sort` (`col`, `-col`, several separated by commas), `columns`, `limit` (max `REFEX_API_MAX_LIMIT`), `offset`, `count`. A column with one of these names is filtered with an explicit operator (`count__eq=3`).

Other routes: `GET /api/referentials` (status of the accessible referentials), `GET /api/referentials/{id}` (schema, profile, configuration), `/runs`, `/changes?kind=added|removed|modified`, `/columns/{col}/stats`, `POST /refresh?force=true`, `DELETE /run` (cancel), `POST /import` and `DELETE /pin` (manual imports), `GET /api/activity`, `GET /api/health`.

Authentication: `GET /api/auth/providers`, `GET /api/auth/oidc/login`, `GET /api/auth/oidc/callback`, `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/me` (profile and effective rights), `POST /api/auth/password`, `GET|POST|DELETE /api/auth/tokens`, `/api/auth/mfa/…` (second factor: sign-in step, TOTP, security keys, recovery codes).
Administration (admin role): `/api/admin/settings` (`proxy`, `ldap`, `oidc`, `syslog`, `mfa`, `branding`, and the `/test` routes), `POST /api/admin/users/{id}/approve|reject`, `/api/admin/service-accounts`, `/api/admin/users/{id}/tokens`, `DELETE /api/admin/users/{id}/mfa`, `/api/admin/referentials` (definitions, `/preview`, `/uploads`, `/{id}/upload`, `/{id}/yaml`), `/api/admin/users`, `/api/admin/groups`, `/api/admin/grants`, `/api/admin/audit`, `/api/admin/tasks` (`/{id}/run`, `/{id}/runs`), `/api/admin/backups` (download, `/restore`), `GET /api/system`, `POST /api/system/reload`.
Search: `GET /api/referentials/{id}/facets` (same filters as `/rows`), `POST /api/referentials/{id}/lookup` with an optional `column`.
Downloads: `GET /api/downloads` (catalog), `GET /api/referentials/{id}/downloads`, `GET /api/referentials/{id}/download/{format}`.
Internal referentials: see [Internal referentials](#internal-referentials).

### SQL console security

SQL queries run in an isolated DuckDB connection, specific to the rights of the user:

- a single statement, `SELECT` only (`WITH`, `PIVOT`, `DESCRIBE`, `SUMMARIZE` accepted);
- only the tables of the accessible referentials exist, and only their Parquet files can be read;
- locked configuration (`enable_external_access=false`, `lock_configuration`);
- maximum duration (`REFEX_SQL_TIMEOUT`) and maximum number of returned rows (`REFEX_SQL_MAX_ROWS`);
- every query is recorded in the audit log.

---

## System tasks

**Administration › Tasks** lists the scheduled background tasks. For each task you can:

- enable or disable it;
- change its schedule (cron) and its parameters;
- run it now (**Run now**);
- see its last runs (status, duration, message, details).

Every run that does something is written to the audit log (`task.run`), so it is also forwarded to syslog when that is configured. Warnings and errors show up in the logs.

| Family | Task | Default |
|---|---|---|
| Maintenance | **Purge expired sessions** | hourly |
| | **Audit log retention**: deletes the entries older than N days (365) | disabled: check your obligations first |
| | **Disk cleanup**: leftover temporary files (uploads, previews, exports); folders of deleted referentials are reported, or deleted | daily |
| Integrity and monitoring | **Integrity check**: every published version is readable and matches what was published (size, row count, indexes, MaxMind DB, Bloom filters) | daily |
| | **Stale referentials**: data older than `max_age`, or last update failed | daily |
| | **Source availability**: the URLs of the HTTP sources answer, through the configured proxy | daily |
| | **TLS certificate expiry**: certificate of `REFEX_PUBLIC_URL` (or of a given host), warning N days before (30) | daily |
| Security policy | **Inactive accounts**: reported, or disabled, after N days without sign-in (90); never the last administrator, never service accounts | disabled |
| | **Expired API tokens**: deleted N days after their expiry (30) | daily |
| | **Pending access requests**: reminder in the audit log and syslog | weekdays |
| | **LDAP resynchronisation**: groups, name and e-mail of the LDAP accounts updated from the directory; accounts that left it are reported, or disabled | daily (when LDAP is enabled) |
| Backup | **Configuration backup** (see below) | daily, 14 kept |
| Folder scans | **Import folder** and **sync folder** scans | `REFEX_IMPORT_POLL_SECONDS` / `REFEX_SYNC_POLL_SECONDS` |

### Configuration backups

The **Configuration backup** task writes a dated file to `data/.backups/`. It contains:

- the referential definitions;
- accounts and groups, rights, API tokens and security keys;
- settings and the rows of internal referentials;
- a copy of the YAML files of `config/`, for reference.

The file is **encrypted** with a key derived from `REFEX_SECRET_KEY`, so it can only be restored with the same key. From the **Tasks** page, a backup can be downloaded, deleted or **restored** (typing `RESTORE` confirms it); a downloaded backup can be restored too. A restore replaces the configuration in one transaction and signs everybody out. The published data, the history and the audit log are kept.

It does not replace the backup of the PostgreSQL volume and of `data/`.

## Logging

### Level of the container logs

`REFEX_LOG_LEVEL` sets what `docker compose logs backend` shows: `trace`, `debug`, `info` (default), `warning`, `error` or `critical`. It applies to the application, to the web server (access log at `info`) and to the libraries. `trace` also shows the generated SQL queries and the HTTP / scheduler details. The audit trail is printed at the `info` level. Restart the backend after a change (`docker compose up -d backend`).

### Syslog forwarding (RFC 5424)

**Administration › Settings › Logging** sends the logs and the audit trail to a syslog collector (rsyslog, syslog-ng, a SIEM…):

- **Format**: RFC 5424 (`<PRI>1 TIMESTAMP HOSTNAME APP-NAME PROCID MSGID [STRUCTURED-DATA] BOM MSG`), timestamps in UTC with microseconds, UTF-8 message.
- **Transports**:
  - **UDP** (RFC 5426);
  - **TCP** (RFC 6587, octet counting by default, or a line feed for legacy collectors);
  - **TLS** (RFC 5425, TLS 1.2 minimum, octet counting).
- **TLS trust**: the collector certificate is verified against public CAs, the **root CA** pasted in the form, and optionally the company CAs of *Network & proxy*. A client certificate and key can be given for mutual TLS.
- **Audit trail** (sign-ins, rights, settings, exports, SQL queries…): sent with severity *notice*, or *warning* for a failure. The MSGID is the action, and the fields are structured data:
  `[audit@32473 user="alice" action="auth.login" target="-" success="true" ip="10.0.0.5"]`, with the details as JSON in the message.
- **Application logs**: sent from a chosen minimum level, with `[log@32473 logger="app.ingest" level="ERROR"]`. Every message also carries `[origin software="RefExposer" swVersion="…"]`.
- Facility, APP-NAME, HOSTNAME and enterprise number are configurable. 32473 is the number reserved for documentation; set your organization's number if it has one.
- **Delivery**: messages leave from a background queue (10,000 messages), so the application never waits for the collector. An unreachable collector is retried with a growing delay, up to 60 s; when the queue is full, the oldest messages are dropped. The page shows the sent, waiting and dropped counts. The **Test** button checks name resolution, the connection, the TLS handshake (with the certificate and its issuer) and sends a test message.

rsyslog collector receiving RFC 5424 over TLS (port 6514):

```
global(DefaultNetstreamDriver="ossl"
       DefaultNetstreamDriverCAFile="/etc/rsyslog.d/ca.pem"
       DefaultNetstreamDriverCertFile="/etc/rsyslog.d/collector.pem"
       DefaultNetstreamDriverKeyFile="/etc/rsyslog.d/collector.key")
module(load="imtcp" StreamDriver.Name="ossl" StreamDriver.Mode="1" StreamDriver.AuthMode="anon")  # "x509/certvalid" for mutual TLS
input(type="imtcp" port="6514" ruleset="refexposer")

template(name="rfc5424" type="string" string="%rawmsg%\n")
ruleset(name="refexposer") {
  action(type="omfile" file="/var/log/refexposer.log" template="rfc5424")
}
```

## Enterprise security

This section lists the security mechanisms in place, by area. The linked sections give the details.

### Identity and access

| Area | Mechanism |
|---|---|
| Identity sources | Local accounts (can be disabled for **single sign-on only**, `REFEX_LOCAL_LOGIN=false`), **LDAP / Active Directory** and **OpenID Connect**. LDAP uses LDAPS or StartTLS; the certificate is checked against the company CAs and user input is escaped in the LDAP filters. OpenID Connect uses the authorization code flow with **PKCE (S256)**, `state` and `nonce`. The ID token signature, issuer, audience and expiry are checked, only asymmetric algorithms are accepted, and the return path after sign-in is restricted to the application (no open redirect). See [LDAP directory, OpenID Connect and access approval](#ldap-directory-openid-connect-and-access-approval). |
| Approval | Every account coming from LDAP or OpenID Connect is created **pending**. Nobody gets in until an administrator approves the account. |
| Least privilege | Three roles: administrator, advanced user, user. Rights are given **per referential** (read / manage), to users or groups. A referential that is not granted is invisible: it is missing from the lists and the search, and answers `404`, like an unknown one. See [Users and rights](#users-and-rights). |
| SQL console | Reserved to administrators and advanced users. Each set of rights gets its own **sandbox**: only the granted tables, read-only, a single `SELECT` per query, no external access, a locked configuration and a time limit. See [SQL console security](#sql-console-security). |
| Service accounts | Accounts for tools, which **cannot sign in to the interface**. They only use API tokens created by an administrator, and they cannot create their own. See [Service accounts](#service-accounts). |
| API tokens | 256 random bits, shown once and stored as **SHA3-256** fingerprints. They can expire (a maximum can be enforced with `REFEX_API_TOKEN_MAX_DAYS`), are revocable at any time, and carry exactly the rights of their owner. They are sent with `Authorization: Bearer`, or with HTTP Basic (token as password) for tools that only support it. |
| Two-factor authentication | Authenticator app (**TOTP**, RFC 6238) and **security keys / passkeys** (**WebAuthn / FIDO2**) for local and LDAP accounts. It is optional, or required for everybody or for some groups. TOTP codes cannot be replayed, the signature counter of keys is checked, recovery codes are one-time, and the session opens only after the second factor. See [Two-factor authentication](#two-factor-authentication). |
| Passwords | **Argon2id** (RFC 9106). Policy: 12 characters minimum, 3 kinds of characters, not containing the username. Lockout after repeated failures, and the same response time whether the username exists or not. The initial administrator must change the password at first sign-in. See [Security](#security). |
| Sessions | Stored server side. The cookie is `HttpOnly`, `SameSite=Lax`, limited to `/api`, and `Secure` behind HTTPS. Expiration is sliding. **CSRF** protection: changing requests need the `X-Requested-With` header. Changing a password closes the other sessions. |

### Data protection

| Area | Mechanism |
|---|---|
| In transit | HTTPS terminated by the reverse proxy: the [nginx example](#expose-behind-an-nginx-reverse-proxy) gives TLS 1.2/1.3 with the hybrid post-quantum key exchange **X25519MLKEM768** (OpenSSL 3.5+), HSTS and an HTTP → HTTPS redirect. Connections to sources, Git servers, LDAP and syslog verify certificates against public CAs and the company CAs. |
| Secrets in the database | LDAP, OpenID Connect and proxy secrets and the syslog client key are encrypted with **AES-256-GCM**, and are never sent back by the API. |
| Keys | Derived with **HKDF-SHA256** from `REFEX_SECRET_KEY`, one key per use and per referential. The backend refuses to start with a key shorter than 32 characters. |
| Confidential referentials | Data **encrypted at rest**: Parquet files (AES-GCM), internal rows (AES-256-GCM), keys of these rows (HMAC fingerprint) and column profiles. Source files and generated downloads are never kept on disk. See [Confidential referentials](#confidential-referentials). |
| Source credentials | API keys, license keys and Git tokens (HTTP headers, `basic_auth`, `token`) can reference `${REFEX_SOURCE_…}` environment variables, so they stay out of the definitions. Only this prefix is expanded, so other secrets can never be sent to a source. Credentials are masked in the API answers. |

### Integrity of the published data

| Area | Mechanism |
|---|---|
| Corrupted sources | An empty file, an HTML error page, a broken archive or an unreadable file is refused: the current version is kept and the error is shown on the referential. |
| Validation | Optional checks on the minimum row count, the maximum drop compared with the previous version, and the uniqueness of the key. A MaxMind DB is refused if its type changes or if it is older than the published one. |
| Safe publication | New files are built in a staging area, then swapped in atomically. The previous version is kept, and a version can be frozen. |
| Archives | Paths escaping the destination folder (*zip slip*) are refused. |
| Downloads | **SHA-256** fingerprint of every file (`X-Checksum-SHA256`, `ETag`), stable URLs. |

### Operations

| Area | Mechanism |
|---|---|
| Scheduled checks | Integrity of the published data, stale referentials, source availability and certificate expiry. Warnings go to the audit log and syslog. See [System tasks](#system-tasks). |
| Account hygiene | Inactive accounts (reported or disabled), expired API tokens, reminders of pending access requests, LDAP resynchronisation. |
| Configuration backups | Daily, encrypted (AES-256-GCM), with retention, and restorable from the interface. |

### Traceability

| Area | Mechanism |
|---|---|
| Audit log | Records, with user, IP and details:<br>• sign-ins (successful or not, with their source: local, LDAP or OIDC);<br>• access approvals and refusals;<br>• administration of accounts, groups and rights;<br>• settings changes, tokens;<br>• referential administration, updates, manual imports, row edits;<br>• exports, downloads and SQL queries.<br>The keys of confidential referentials are never written to it. |
| SIEM forwarding | Logs and audit trail in **syslog RFC 5424**, over UDP, TCP or **TLS**. TLS supports a private root CA and mutual TLS. Audit events are sent as structured data. See [Syslog forwarding](#syslog-forwarding-rfc-5424). |
| Log level | `REFEX_LOG_LEVEL`, from `trace` to `critical`. |

### Exposure

- **Containers without privileges**: no long-running container runs as root. They have a read-only root filesystem, no Linux capabilities and `no-new-privileges`, and the application code cannot be modified by the account running it. See [Containers](#containers).
- **Single entry point**: only the `frontend` container publishes a port; the backend and PostgreSQL stay on the internal Docker network. Behind a reverse proxy, bind it to the loopback (`FRONTEND_PORT=127.0.0.1:8080`).
- **Reverse proxy** ([nginx example](#expose-behind-an-nginx-reverse-proxy)):
  - a strict **Content-Security-Policy**, HSTS, `X-Frame-Options: DENY`, `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy`;
  - **rate limiting** on the sign-in and the second factor (10 requests per minute per IP) and on the API;
  - client IP forwarded without trusting the client (`X-Forwarded-For` replaced), so the audit log records the real address.

### Left to the operator

These points depend on the hosting and are not handled by RefExposer:

- **Backups**: the PostgreSQL volume, the `data/` folder (configuration backups included, `data/.backups`), and **`REFEX_SECRET_KEY`**. Back up the key separately from the data: without it, encrypted secrets and the rows of confidential internal referentials are lost.
- **Key storage**: set `REFEX_SECRET_KEY` from the environment or a secret manager. Without it, the generated key is stored in `data/.secret_key`, next to the data it protects.
- **Images**: keep the base images up to date (`docker compose build --pull`). Outside `docker compose`, give `data/` and `import/` to the account running the backend (see [Containers](#containers)).
- **Administrators are trusted**: a source URL or an SQL transformation makes the server reach the network and read files, like a configuration file would. Only give the administrator role to people who need it.
- **Reverse proxy**: HTTPS, the security headers and rate limiting are provided by the reverse proxy in front of RefExposer (see [Expose behind an nginx reverse proxy](#expose-behind-an-nginx-reverse-proxy)).
- **Memory and temporary files**: data is in clear in memory while it is used, and DuckDB may write temporary files during very large sorts (`data/.tmp`).
- **Monitoring**: `GET /api/health` (no authentication) for liveness probes. The audit trail can be forwarded to a SIEM.

## Configuration (environment variables)

Put them in a `.env` file at the root (see `.env.example`):

| Variable | Default | Role |
|---|---|---|
| `REFEX_PUBLIC_URL` | `http://localhost:8080` | URL used to reach the application (see [Public URL](#public-url)) |
| `FRONTEND_PORT` | `8080` | published port of the interface; `127.0.0.1:8080` to accept the local reverse proxy only |
| `POSTGRES_PASSWORD` | `refexposer` | database password (avoid `@ : / ? #`) |
| `REFEX_DATABASE_URL` | compose PostgreSQL | SQLAlchemy URL; without a value outside compose: SQLite in `data/refexposer.db` |
| `REFEX_ADMIN_USERNAME` | `admin` | initial administrator |
| `REFEX_ADMIN_PASSWORD` | `ChangeMe-Now-2026!` | its password (empty = generated and printed in the logs) |
| `REFEX_ADMIN_EMAIL` | | its e-mail |
| `REFEX_ADMIN_MUST_CHANGE_PASSWORD` | `true` | requires a change at first sign-in |
| `REFEX_ADMIN_RESET_PASSWORD` | `false` | recovery: resets this account at startup |
| `REFEX_LOCAL_LOGIN` | `true` | `false`: no username / password sign-in for local accounts in the interface (single sign-on only, see [Single sign-on only](#single-sign-on-only)) |
| `REFEX_UID` / `REFEX_GID` | `10001` | account running the backend, set when the image is built (owner of `./data` and `./import`, see [Containers](#containers)) |
| `REFEX_SESSION_TTL_HOURS` | `12` | inactivity before a session expires |
| `REFEX_COOKIE_SECURE` | `false` | forces `Secure` cookies (automatic when `REFEX_PUBLIC_URL` is https) |
| `REFEX_PASSWORD_MIN_LENGTH` | `12` | minimum password length |
| `REFEX_LOGIN_MAX_ATTEMPTS` / `REFEX_LOGIN_LOCKOUT_MINUTES` | `10` / `15` | lockout after failures |
| `REFEX_API_TOKEN_MAX_DAYS` | unlimited | maximum lifetime of API tokens |
| `REFEX_ARGON2_TIME_COST` / `_MEMORY_KIB` / `_PARALLELISM` | `3` / `65536` / `4` | Argon2id parameters |
| `REFEX_SECRET_KEY` | generated in `data/.secret_key` | encryption key of the secrets stored in the database (LDAP, OIDC, proxy), 32 characters minimum |
| `REFEX_GIT_PROTOCOLS` | `https` | transports allowed for Git sources (comma separated; `https,http` for an internal server without TLS) |
| `REFEX_GIT_TIMEOUT` | `900` | maximum duration of a Git fetch (s) |
| `REFEX_LOG_LEVEL` | `info` | container logs: `trace`, `debug`, `info`, `warning`, `error`, `critical` (see [Logging](#logging)) |
| `REFEX_LARGE_ROWS` | `50000000` | “large volume” threshold (automatic guardrails) |
| `REFEX_FACETS_MAX_ROWS` | `REFEX_LARGE_ROWS` | search facets are disabled above this row count |
| `REFEX_INTERNAL_PUBLISH_DELAY` | `2` | seconds without edits before an internal referential is published (0 = at once) |
| `REFEX_QUERY_TIMEOUT` | `60` | maximum duration of an API query on large referentials (s) |
| `REFEX_IMPORT_DIR` | `/import` in compose | import folder (empty = disabled; uploads still work) |
| `REFEX_IMPORT_POLL_SECONDS` | `20` | how often the import folder is checked |
| `REFEX_SYNC_DIR` | `/sync` in compose | sync folder (empty = disabled) |
| `REFEX_SYNC_POLL_SECONDS` | `60` | how often the sync folder is checked |
| `REFEX_SYNC_SETTLE_SECONDS` | `30` | a file must be untouched this long before it is imported (copy finished) |
| `REFEX_REFRESH_ON_STARTUP` | `missing` | `missing` (referentials without data), `all`, `none` |
| `REFEX_SCHEDULER_ENABLED` | `true` | enables scheduled updates |
| `REFEX_TIMEZONE` | `Europe/Paris` | time zone of the cron expressions |
| `REFEX_MAX_CONCURRENT_JOBS` | `2` | concurrent updates |
| `REFEX_HTTP_VERIFY` | `true` | `true`, `false` or path of a CA bundle (company TLS proxy) |
| `HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY` | | outgoing proxy (downloads and build) |
| `REFEX_DUCKDB_MEMORY_LIMIT` | 80% of the RAM | e.g. `4GB` |
| `REFEX_SQL_TIMEOUT` | `30` | max. duration of an SQL query (s) |
| `REFEX_SQL_MAX_ROWS` | `10000` | max. rows returned by the console |
| `REFEX_API_MAX_LIMIT` | `1000` | max. rows per API page |
| `REFEX_EXPORT_MAX_ROWS` | `5000000` | max. rows of an export |
| `REFEX_KEEP_RUNS` | `200` | history depth per referential |

To reach the backend directly (port 8000), uncomment the `ports` section of the `backend` service in `docker-compose.yml`.

**Company certificate**: mount the CA bundle in the backend container (e.g. `./certs/ca.pem:/certs/ca.pem:ro`) and set `REFEX_HTTP_VERIFY=/certs/ca.pem`, or paste it in **Administration › Settings › Network**.

---

## Development

```bash
# Backend tests (pipeline, API, filters, SQL sandbox, authentication, rights, sources, sync, internal referentials)
docker build --target test -t refexposer-backend:test backend && docker run --rm refexposer-backend:test

# Backend locally (Python 3.12+)
cd backend && pip install -r requirements-dev.txt
REFEX_DATA_DIR=../data REFEX_CONFIG_DIR=../config REFEX_ADMIN_PASSWORD='Dev-Passw0rd!' uvicorn app.main:app --reload

# New migration after changing app/models.py
cd backend && alembic revision --autogenerate -m "description"

# Frontend locally (Node 22+), proxied to http://localhost:8000
cd frontend && npm install && npm run dev
```

## Ideas for later

- SAML, single logout (RP-initiated logout) with Keycloak
- Notify administrators (e-mail, Teams) of new access requests
- Row- or column-level rights inside a referential
- Notifications (webhook, e-mail, Teams) on failures, corrupted sources or new versions
- Keep N historical versions and “as of” queries
- Prometheus metrics
