<p align="center"><img src="frontend/public/logo.svg" width="96" alt="RefExposer logo"></p>

# RefExposer

[![CI](https://github.com/emeryn/RefExposer/actions/workflows/ci.yml/badge.svg)](https://github.com/emeryn/RefExposer/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](backend/requirements.in)
[![Node 22](https://img.shields.io/badge/node-22-blue.svg)](frontend/package.json)

One tool to **expose, monitor and query heterogeneous referentials** (CSV, TSV, JSON, JSON Lines, TXT, XML, Parquet, Excel, Bloom filters, MaxMind DB / GeoIP), whether they are downloaded from the Internet, dropped as files, or maintained directly in the application.

Each referential is declared from the administration interface (URL, Git repository or uploaded file, with a live preview) or in a YAML file. RefExposer downloads it, normalizes it to Parquet, checks the new version and publishes it. It is then available in four ways:

- **a web interface**: status dashboard, explorer with filters, column profiles, differences between versions, history, SQL console (administrators and advanced users), global search, and built-in user and administrator guides (**Help** menu);
- **a REST API documented automatically**: an OpenAPI specification is generated for every referential from its schema (one typed filter per column, real examples). It covers filters, search, single and batch lookups, and exports;
- **downloads** of the whole referential (CSV, CSV.gz, Excel, JSON, JSONL, Parquet, original source files), cached per version with a SHA-256 fingerprint;
- **read-only SQL** (DuckDB), joins between referentials included.

Beyond downloaded sources:

- **Internal referentials** are created and edited inside RefExposer (row editor, file loads, write API), for the lists your teams maintain themselves.
- **Unified referentials** combine several referentials into one: their rows put together, the rows of one enriched with the columns of the others, or any query of the SQL console kept as a referential. They are rebuilt whenever a source publishes a new version.
- **Sync folder**: files pushed into a folder (scp / rsync over ssh, every week for instance) become referentials by themselves, imported again when they change.
- **Manual imports**: a version can be uploaded from the referential page or dropped in an import folder, for example when the source is unreachable or only sent by e-mail.
- **Corrupted sources are refused**: an empty file, an HTML error page or a broken archive never replaces the current version; the error is shown on the referential.
- **Data quality**: rules checked on every new version (filled, unique, IP / domain / hash / CVE formats, allowed values, ranges, SQL conditions), a score per version, and a quality gate that rejects a bad version.
- **Notifications**: e-mails and generic webhooks (Slack, Teams, Discord, incident tools…) when a referential fails, recovers or publishes a new version.
- **Governance**: every referential says whether it holds **internal** or **public** data, and who answers for it — shown on every card and page, with a quick filter in the dashboard.
- **Personal data**: the columns holding it are **masked everywhere** (rows, exports, downloads, SQL, MCP, assistant). Seeing the real values takes the right **and** an explicit request from the user, which is audited.
- **Time travel**: a referential can keep each published version (hard links: no extra space) and compare any two of them — rows added, removed and modified with their before and after.
- **Watchdogs** (optional, administrators and advanced users): the assistant investigating on its own, on a schedule or right after a synchronization, over a scope you choose — and telling the recipients only when it finds something.
- **Alerts**: one list of what needs a look — watchdog findings, failed updates, stale data, quality rules failing, a volume that drifted — with the report or the log of each behind one click.

Access is protected:

- **Identity sources**: local accounts, an **LDAP directory / Active Directory** or **OpenID Connect** (Keycloak…), with group synchronization.
- **Approval**: an administrator approves every new access.
- **Roles** (administrator, advanced user, user), **rights per referential**, API tokens, **service accounts** for tools (API only) and an audit log.
- **Secret manager**: API keys, tokens and passwords of the sources are stored encrypted and referenced as `${secret:<name>}`, never returned by the API, optionally restricted to their hosts.
- **Bulk discovery**: every data file of a Git repository (e.g. [cybref](https://github.com/emeryn/cybref)) or of an HTTP folder becomes a referential in a few clicks.
- **In-application assistant** (optional): a chatbot on its own page, backed by a model you host (Ollama or OpenAI-compatible). In agent mode it queries the referentials and acts **through the session of the user**, never with more rights; confidential referentials are kept away from the model unless an administrator allows them.
- **MCP servers** (optional): every user queries the referentials it may read from an AI assistant, with its own API token; an administration server lets an agent drive RefExposer with the token of a service account.
- **Storage**: accounts, settings, definitions and internal rows in PostgreSQL. **Confidential referentials** are encrypted at rest (data files, internal rows and their keys).

The security mechanisms are summarized in [Enterprise security](#enterprise-security).

An **HTTP/HTTPS proxy**, company certificate authorities and the **branding** (title, subtitle, company logo) are configured from the interface. The URL of the application is set once with `REFEX_PUBLIC_URL`.

RefExposer starts empty: add referentials from the interface, with YAML files in `config/`, or by pushing files into `sync/`.

---

## Getting started

### 1. Requirements

- **Docker Engine 24+** with **Docker Compose v2.24+** (`docker compose version`), or **Docker Desktop** on Windows or macOS.
  On a Linux server, **Podman 5+** without root works too, with systemd units: see [Run with Podman (Quadlet, rootless)](#run-with-podman-quadlet-rootless).
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
- **Check the dependencies and the images**: `make scan` (or `sh scripts/scan.sh`). Rebuilding with `--pull` applies the security updates of the base images. See [Supply chain](#supply-chain).

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
- **Podman**: the port is set by `PublishPort=` in `refexposer-frontend.container` (see the next section), not by `FRONTEND_PORT`.

---

## Run with Podman (Quadlet, rootless)

On a Linux server, RefExposer can run with **Podman without root**: the containers are started by your own account, as **systemd user services** written as [Quadlet](https://docs.podman.io/en/latest/markdown/podman-systemd.unit.5.html) files. This replaces `docker compose`, and uses the published images (`docker.io/emeryn/refexposer-backend`, `docker.io/emeryn/refexposer-frontend`): there is nothing to build.

**Requirements**: Podman **5.0+** (`podman --version`: RHEL / Rocky / Alma 9.5+, Fedora 40+, Debian 13, Ubuntu 25.04+), and a dedicated account (here `refex`) with sub-UIDs (`grep refex /etc/subuid`; created by `useradd` on recent distributions).

### 1. Account and folders

As root, once, to keep the services running without an open session:

```bash
useradd -m refex                       # or an existing account
loginctl enable-linger refex
```

Then **as this account** (sign in with `ssh refex@server` or `machinectl shell refex@`, not `su`, so that `systemctl --user` works):

```bash
mkdir -p ~/refexposer/{config,data,import,sync} ~/.config/containers/systemd
```

These folders belong to your account and need nothing more: the backend runs with `UserNS=keep-id`, which maps your account to the `refex` account of the image (uid 10001). The files written by RefExposer belong to you on the host, and you can drop files into `import/` and `sync/` directly. The `init-permissions` service of `docker-compose.yml` is not needed.

*Coming from Docker on the same server?* Copy `data/` and `config/`, then give them to the account: `sudo chown -R refex: ~/refexposer`. Move the database with a `pg_dump` (see [Stop, update, back up](#stop-update-back-up)).

### 2. Configuration

Two environment files, readable by your account only. Podman reads them **literally**: no quotes, no comment at the end of a line (`.env.example` cannot be copied as is).

`~/refexposer/db.env`:

```bash
POSTGRES_DB=refexposer
POSTGRES_USER=refexposer
POSTGRES_PASSWORD=<openssl rand -hex 24>
```

`~/refexposer/refexposer.env`:

```bash
REFEX_PUBLIC_URL=https://refexposer.example.com
REFEX_DATABASE_URL=postgresql+psycopg://refexposer:<same password>@db:5432/refexposer
REFEX_SECRET_KEY=<openssl rand -base64 48>
REFEX_ADMIN_USERNAME=admin
REFEX_ADMIN_PASSWORD=<initial password, changed at first sign-in>
REFEX_COOKIE_SECURE=true
REFEX_IMPORT_DIR=/import
REFEX_SYNC_DIR=/sync
REFEX_TIMEZONE=Europe/Paris
TZ=Europe/Paris
# Outgoing proxy, if any
# HTTP_PROXY=http://proxy.example.com:3128
# HTTPS_PROXY=http://proxy.example.com:3128
# NO_PROXY=localhost,127.0.0.1,backend,frontend,db
```

```bash
chmod 600 ~/refexposer/*.env
```

Every other variable of [Configuration](#configuration-environment-variables) can be added to `refexposer.env`. Keep a copy of `REFEX_SECRET_KEY` outside the server.

### 3. Quadlet files

In `~/.config/containers/systemd/`:

`refexposer.network`:

```ini
[Network]
NetworkName=refexposer
```

`refexposer-db.volume`:

```ini
[Volume]
VolumeName=refexposer-db
```

`refexposer-db.container`:

```ini
[Unit]
Description=RefExposer - PostgreSQL

[Container]
Image=docker.io/library/postgres:17-alpine
ContainerName=refexposer-db
Network=refexposer.network
NetworkAlias=db
EnvironmentFile=%h/refexposer/db.env
Volume=refexposer-db.volume:/var/lib/postgresql/data
HealthCmd=pg_isready -U refexposer -d refexposer
HealthInterval=5s
HealthTimeout=5s
HealthRetries=20
Notify=healthy

[Service]
Restart=always
TimeoutStartSec=600

[Install]
WantedBy=default.target
```

`refexposer-backend.container`:

```ini
[Unit]
Description=RefExposer - backend
Requires=refexposer-db.service
After=refexposer-db.service

[Container]
Image=docker.io/emeryn/refexposer-backend:latest
ContainerName=refexposer-backend
Network=refexposer.network
NetworkAlias=backend
EnvironmentFile=%h/refexposer/refexposer.env
# Your account = the refex account (uid 10001) of the image: data/ and import/ need no chown
UserNS=keep-id:uid=10001,gid=10001
Volume=%h/refexposer/data:/data:Z
Volume=%h/refexposer/import:/import:Z
Volume=%h/refexposer/config:/app/config:ro,z
Volume=%h/refexposer/sync:/sync:ro,z
ReadOnly=true
Tmpfs=/tmp
DropCapability=ALL
NoNewPrivileges=true
HealthCmd=python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"
HealthInterval=30s
HealthTimeout=5s
HealthStartPeriod=20s
HealthRetries=3
Notify=healthy
AutoUpdate=registry

[Service]
Restart=always
# First start: image download and database creation
TimeoutStartSec=900

[Install]
WantedBy=default.target
```

`refexposer-frontend.container`:

```ini
[Unit]
Description=RefExposer - web interface
Requires=refexposer-backend.service
After=refexposer-backend.service

[Container]
Image=docker.io/emeryn/refexposer-frontend:latest
ContainerName=refexposer-frontend
Network=refexposer.network
# Loopback only: the nginx of the host is in front (see "Expose behind an nginx reverse proxy")
PublishPort=127.0.0.1:8080:8080
ReadOnly=true
Tmpfs=/tmp
Tmpfs=/var/cache/nginx
DropCapability=ALL
NoNewPrivileges=true
HealthCmd=wget -qO- http://127.0.0.1:8080/healthz || exit 1
HealthInterval=30s
HealthTimeout=5s
Notify=healthy
AutoUpdate=registry

[Service]
Restart=always
TimeoutStartSec=600

[Install]
WantedBy=default.target
```

- `%h` is the home folder of the account. Adapt the paths if the folders are elsewhere.
- `:Z` / `:z` label the folders for **SELinux** (RHEL, Rocky, Fedora). They are ignored elsewhere.
- Without a reverse proxy on the host, publish `PublishPort=8080:8080` instead. Without root, a port below 1024 cannot be used directly.

### 4. Start

```bash
systemctl --user daemon-reload                       # generates the services from the Quadlet files
systemctl --user start refexposer-frontend            # also starts the network, db and backend
systemctl --user status refexposer-db refexposer-backend refexposer-frontend
```

The first start downloads the images and can take a few minutes: `start` returns once the containers are **healthy**. The services restart at boot (thanks to `enable-linger` and `WantedBy=default.target`); `systemctl --user enable` is neither needed nor possible for Quadlet services.

If nothing is generated, check the files with `/usr/libexec/podman/quadlet -dryrun -user`.

### 5. Operate

| Action | Command |
|---|---|
| Logs | `journalctl --user -u refexposer-backend -f` (or `podman logs -f refexposer-backend`) |
| Stop / start | `systemctl --user stop refexposer-frontend refexposer-backend refexposer-db` / `systemctl --user start refexposer-frontend` |
| Update | `podman auto-update` (pulls the new `latest` images and restarts the changed services; `--dry-run` to only list them) |
| Automatic updates | `systemctl --user enable --now podman-auto-update.timer` (daily) |
| Pin a version | replace `:latest` with a tag (e.g. `:0.2`) in the `.container` files, then `systemctl --user daemon-reload` and restart |
| Back up the database | `podman exec refexposer-db pg_dump -U refexposer refexposer > refexposer.sql` |
| Change a setting | edit `refexposer.env`, then `systemctl --user restart refexposer-backend` |

Back up as with Docker: the database dump, `~/refexposer/data/`, and `REFEX_SECRET_KEY` kept apart. `podman volume rm refexposer-db` **deletes the database**.

---

## Architecture

### What it does, end to end

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

### Components

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
examples/              example configurations and scripts (CybrefV2 definitions, API scripts)
scripts/               lock.sh (pin the Python dependencies), scan.sh (supply-chain scan), sbom.sh (SBOM)
Makefile               shortcuts: up, down, logs, build, test, typecheck, scan, sbom (make help)
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

### Where the data is really stored

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
  `./sync` as well. The database keeps its own volume. See [Containers](#containers).
- Details of the confidential mode, and what it does not cover, are in
  [Confidential referentials](#confidential-referentials).

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

### Bulk discovery (Git repository, HTTP folder)

**Administration › Bulk discovery** creates one referential per data file of a source, for example the 100 files of `output/` in [github.com/emeryn/cybref](https://github.com/emeryn/cybref):

1. **Scan** a Git repository (HTTPS URL, glob of the files such as `output/*`, branch, token) or an HTTP folder (page listing the files: Apache, nginx, Caddy autoindex…; glob, sub-folder levels, authentication). For Git, only the tree of the commit is fetched, not the content: listing a repository takes about a second.
2. Each data file (known extension, possibly `.gz` / `.zip`) becomes a **proposal**: identifier and name from the file name (`-2` when the identifier is taken), Git or HTTP source, format, and the category, tags and schedule given once for all. Files already used by a referential are flagged and left unselected.
3. **Analyse** (optional): the files are fetched (Git: only them, in one sparse fetch; HTTP: sampled) and read like the preview of the editor. The format, the record path of a JSON document (`records_path: prefixes` for `aws_ip_ranges.json`), the columns and the row count are filled in, and unreadable files are shown with their error.
4. Review the list (identifier, name and format can be changed), choose the groups with read access, then **create** the selected referentials and start their imports.

Credentials of a private repository or folder must be `${secret:…}` references (see [Secret manager](#secret-manager)), because they are copied into every definition. API: `POST /api/admin/discovery/scan`, then `POST /api/admin/discovery/apply`.

### Export and import (from one environment to another)

**Administration › Referentials › Export** downloads the configuration of the referentials to set up another instance (test, then production; a new server):

- **Content**: the definitions (created in the interface, internal referentials without their rows, YAML files of `config/`; the referentials of the sync folder are left out), the **rights** given to groups (by name) and users (by user name), the groups they use, and the **secrets they need: names, descriptions and allowed hosts, never the values**.
- **Literal credentials** still written in definitions are masked by default. *Included in clear* puts them in the file: keep such a file like a password, or better, move them to the secret manager.
- **Format**: YAML (or JSON) in the format of the files of `config/`. The file can also be dropped into `config/` as is: the extra keys are ignored.

**Import** (same page) works in two steps:

1. **Analyse**: nothing changes yet. For each referential, the plan says *create*, *update* (with the changed fields), *unchanged*, *skip* (already defined, when keeping the existing ones), *conflict* (defined in a YAML file or the sync folder of this instance) or *error* (invalid definition, unknown secret, masked credential with nothing to keep). The secrets to create first and the missing groups are listed.
2. **Import** the selected referentials, with their rights. Missing groups are created on request (add their members afterwards, or let LDAP / OpenID Connect synchronize them), and the imports can be started at once. A masked credential keeps the value already stored on the target.

API: `GET /api/admin/config/export?ids=&grants=&credentials=masked|included&format=yaml|json`, `POST /api/admin/config/import` (`apply: false` for the plan). MCP tools: `export_configuration`, `import_configuration`.

**Ready-made example**: [`examples/cybref/cybref-referentials.yaml`](examples/cybref/cybref-referentials.yaml) imports 124 cybersecurity referentials of [CybrefV2](https://github.com/emeryn/CybrefV2) (threat intelligence, vulnerabilities, ATT&CK, IP ranges, registries…), with keys, quality rules and merged rolling feeds. See its [README](examples/cybref/README.md).

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
      type: http                    # http (default) | git | local | sync | internal | referential (see below)
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
| `xlsx` | `read_xlsx` (excel extension) | `sheet` (the analysis lists them), `range` (`A1:F500`), `header`, `all_varchar`, `ignore_errors`, `stop_at_empty`, `empty_as_varchar` |
| `xml` | converted to JSON Lines, then `read_json` | `records_path`: name of the repeated record element (default: the likeliest one, frequent and rich) |
| `bloom` | DCSO Bloom filter (published as is) | `normalize`, `pattern`, `enrich_bulk_url`, `enrich_key` |
| `mmdb` | MaxMind DB (published as is, IP lookups) | |
| `sqlite` | SQLite database, attached read-only (sqlite extension) | `table`: table or view to read; `nsrl`: NIST NSRL set, publications and deltas found automatically (see [SQLite databases](#sqlite-databases-nist-nsrl-and-sql-deltas)) |

#### Excel workbooks

A column of an Excel sheet takes the type of its **first value**: a column of numbers holding one piece of text further
down (a note in a “comment” column) cannot be read at all, and the workbook declares no type for the column itself.
RefExposer then reads **every column as text**, keeps `all_varchar: true` in the definition so the next updates read the
file the same way, and says so in the preview and in the run log. Cast the columns that really are numbers or dates in
the transformation:

```yaml
format: xlsx
options:
  sheet: Inventory          # the analysis lists the sheets of the workbook
  all_varchar: true
transform: |
  SELECT * REPLACE (TRY_CAST(score AS DOUBLE) AS score, TRY_CAST(opened AS DATE) AS opened) FROM {source}
```

`ignore_errors: true` is the other way out — cells that do not fit become empty — but it loses data silently.
`stop_at_empty: false` keeps reading past a blank row in the middle of the sheet.

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

#### Incremental updates (deltas)

When the provider also publishes **deltas**, the full filter (often around 1 GB) does not have to be downloaded at every update. The `incremental` block lists the delta files. They are added to the published filter, and the full filter is only downloaded again when the provider publishes a new one.

```yaml
format: bloom
source:
  urls: [https://example.org/hashlookup-full.bloom]   # full filter
schedule: "0 6 * * *"                               # deltas checked every day
incremental:
  urls: [https://example.org/hashlookup-delta.txt.gz]
  format: values        # values: one value per line (# comments, .gz accepted)
                        # bloom: Bloom filter with the same size and hash functions as the full one
  full_every: 30d       # full filter checked at most every 30 days (every update when omitted)
  max_fp_rate: 0.001    # optional, default: 10 times the rate the full filter was built for
```

At each update:

1. **Full filter**: it is checked when `full_every` has elapsed, with a conditional request (`If-None-Match` / `If-Modified-Since`): nothing is downloaded when it did not change. A new full filter replaces the published one, and the deltas are applied again on top of it.
2. **Deltas**: each URL is checked the same way, and only a changed file is downloaded. It is added to a copy of the published filter, so lookups keep working during the update. Its values are normalized like the lookups (`normalize`), and the lines that do not match `pattern` are ignored and counted in the log.
3. **Publication**: the result becomes a new version (the previous one is kept, see `storage.keep_previous`). The run history shows the new elements of each delta, and whether the full filter was downloaded.

Good to know:

- **Applying a delta twice changes nothing**: adding a value that is already in a Bloom filter does not modify it. A delta already contained in a new full filter, or applied again after a failure, is therefore harmless.
- **Deletions**: a Bloom filter cannot remove a value. Values withdrawn by the provider remain present until the next full filter.
- **Saturation**: each delta adds elements to a filter sized for a fixed capacity, so the false positive rate rises. A delta that would push the estimated rate above `max_fp_rate` is **rejected**, and the published version is kept. Wait for a new full filter, or force an update (`POST /api/referentials/<id>/refresh?force=true`) when the provider has published one.
- **Delta as a Bloom filter**: it must have the same size (bits) and number of hash functions as the full filter (the two are combined bit by bit). Otherwise it is rejected with an explanation.
- Deltas use the same headers and credentials as the full filter (`source.headers`, `basic_auth`). `incremental` only applies to a Bloom filter downloaded from a single URL.
- **Forced update**: it downloads everything again, the full filter and the deltas.

### Git repositories (GitHub, GitLab, Gitea…)

A referential can come from **files of a Git repository**. It works with GitHub, GitLab, Gitea / Forgejo, Bitbucket or any Git server over HTTPS. In the editor, choose **Git repository** as the source. Give:

- the URL of the repository;
- the files to read (a glob relative to the repository root);
- optionally a branch, tag or commit (the default branch when empty);
- for a private repository, an access token.

- **Only what is needed is downloaded**: one commit (`--depth 1`), the matching files only (sparse, partial fetch). A large repository costs little.
- **No useless update**: `git ls-remote` runs first. While the branch or tag still points to the published commit, nothing is fetched and the run ends as *unchanged*. The published commit is shown in the sources of the referential.
- **Private repositories**: the token is sent as HTTP Basic credentials, with the user name `oauth2` by default (accepted by GitHub, GitLab and Gitea; it can be changed). The token never goes in the URL nor on a command line, is masked in every message, and is not returned by the API. Keep it out of the definition with a secret of the [secret manager](#secret-manager) (`token: "${secret:gitlab-token}"`) or a `${REFEX_SOURCE_…}` variable of `.env`.

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

### SQLite databases (NIST NSRL) and SQL deltas

The `sqlite` format reads one **table or view** of a SQLite database (`options.table`), possibly inside an archive with other files (readme, schema…), which are ignored. The database is attached read-only, so views keep their column types. The usual SQL transformation applies on top (`{source}` is the chosen table).

#### NIST NSRL: nothing to maintain

For the [NIST NSRL](https://www.nist.gov/itl/csd/secure-systems-and-applications/national-software-reference-library-nsrl/nsrl-download-0), give the **set** in `options.nsrl`. RefExposer reads the NIST download page, downloads the latest full database of the set once, then **replays each quarterly delta into it** (each delta is a SQL file of `INSERT` / `DELETE` statements). No URL to list: a new delta published by NIST is found and applied at the next update.

```yaml
id: nsrl-modern
name: NIST NSRL (modern, minimal)
format: sqlite
source:
  urls: []                     # optional: the NIST download page by default
options:
  nsrl: modern_minimal         # modern, legacy, android, ios, with or without _minimal
  table: NSRLFile              # default: RDS 2.x conversion (see below)
key: SHA-1
storage:
  sort_by: [SHA-1]             # hundreds of millions of rows: fast lookups by hash
  indexes: [MD5]
  keep_raw: false
  track_changes: false
schedule: "0 6 * * *"
```

In the interface: **New referential**, paste the URL of the NIST download page. The NSRL mode is detected (set *modern, minimal* by default, can be changed in *NIST NSRL publication*). The preview lists what will be downloaded, without downloading it (tens of GB). If the URL of a publication (`RDS_2026.03.1_modern_minimal.zip`) is given in NSRL mode, the download page is read instead: the latest publications are always used.

**Publication of each row**: in NSRL mode, a last column gives the file each row comes from: the full database (`RDS_2026.03.1_modern_minimal.db`) or the quarterly delta that added it (`RDS_2026.09.1_modern_minimal_delta.sql`). It is `SourceFile` in the RDS 2.x tables below and `source_file` in the RDSv3 tables `FILE`, `PKG`, `OS` and `MFG`. RefExposer records it in the kept database (table `REFEX_ORIGIN`) while it applies each delta. A row that a delta inserts again (already in the database) is skipped and keeps its publication.

**Conversion to RDS 2.x**: NIST no longer publishes the `NSRL*.txt` text files, only a SQLite database, and [describes how to convert it](https://s3.amazonaws.com/rds.nsrl.nist.gov/RDS/RDSv3_Docs/RDSv3_to_RDSv2_text_files_LINUX-OSX.pdf). This conversion is built in. Choose one of these tables:

| Table | Columns |
|---|---|
| `NSRLFile` | `SHA-1`, `MD5`, `CRC32`, `FileName`, `FileSize`, `ProductCode`, `OpSystemCode` (`0`), `SpecialCode` (empty), `SourceFile` (NSRL mode) |
| `NSRLProd` | `ProductCode`, `ProductName`, `ProductVersion`, `OpSystemCode`, `MfgCode`, `Language`, `ApplicationType`, `SourceFile` (NSRL mode) |
| `NSRLMfg` | `MfgCode`, `MfgName`, `SourceFile` (NSRL mode) |
| `NSRLOS` | `OpSystemCode`, `OpSystemName`, `OpSystemVersion`, `MfgCode`, `SourceFile` (NSRL mode) |

The CSV download of `NSRLFile` gives the former `NSRLFile.txt`. The RDSv3 tables (`FILE` with `sha256`, `PKG`, `OS`, `MFG`, `DISTINCT_HASH`) can also be published as they are. Create one referential per table you need.

How the deltas are chained: a NIST delta only applies to the **previous release**. Each one ends with `DELETE FROM VERSION WHERE version='<previous>'` then `INSERT INTO VERSION … VALUES('<new>', …)`. RefExposer reads the release of the kept database (`VERSION` table) and:

- applies the deltas **newer** than the database, in order. The delta published with a full database (e.g. `2026.03.1`) is already in that database: it is not applied again.
- **skips** a delta the database already holds (imported twice, listed by mistake).
- **refuses** a delta made for another release (a gap in the chain), without changing anything: *delta … updates release 2026.06.1 to 2026.09.1, but the database is at release 2026.03.1*. When NIST publishes a newer full database, the full database is loaded again.

The kept database is never downloaded again while the chain holds: after the first import (one full database of several GB), each update only downloads the new delta (tens to hundreds of MB).

Orders of magnitude, measured on the *iOS minimal* set (Docker Desktop, data on a Windows disk):

| Step | Measure |
|---|---|
| Full database 2026.03.1 | 5.9 GB zip downloaded (~15 MB/s), **38 GB** once extracted and kept |
| Deltas 2026.06.1 + 2026.09.1 | 0.4 M + 3 M SQL statements, replayed at about 8,500 statements/s |
| `NSRLFile` table (97.5 M rows, sorted on `SHA-1`) | built in 5 min, 2.4 GB of Parquet |
| Next update without a new delta | 1 s (the NIST page is read, nothing is downloaded) |

Each delta is replayed in WAL mode: the changed pages are written once to a `-wal` file (a few GB for a large delta) instead of copying every page to a rollback journal first, which is 4 to 5 times faster on a database of tens of GB. It stays atomic, even when the service is killed. Plan the disk accordingly: the *modern minimal* database takes about three times the space of the *iOS minimal* one.

#### Other SQLite databases with SQL deltas

When another publisher provides **SQL deltas**, declare them in `incremental`. The full database is then **kept** in `data/<id>/base/`, and each delta is applied **once, in the order of the list**:

```yaml
id: nsrl-modern-pinned
name: NIST NSRL (modern, minimal, deltas listed by hand)
format: sqlite
source:
  urls: [https://s3.amazonaws.com/rds.nsrl.nist.gov/RDS/rds_2026.03.1/RDS_2026.03.1_modern_minimal.zip]
options:
  table: FILE                  # FILE (sha256, sha1, md5, crc32, file_name, file_size, package_id)
key: sha1
incremental:
  urls:                        # deltas published after the full database, oldest first
    - https://s3.amazonaws.com/rds.nsrl.nist.gov/RDS/rds_2026.06.1/RDS_2026.06.1_modern_minimal_delta.zip
    - https://s3.amazonaws.com/rds.nsrl.nist.gov/RDS/rds_2026.09.1/RDS_2026.09.1_modern_minimal_delta.zip
storage:
  sort_by: [sha1]
schedule: "0 6 * * *"
```

In the interface: **New referential**, source URL, format **SQLite database**, table, and the deltas in the *Deltas* field. A source too large to be analysed is not downloaded by the preview: choose the format and the table by hand, and the import does the rest in the background.

How it works (both modes):

- **First import**: the full database is downloaded and unpacked (the archive is then deleted), kept, and the deltas are applied on top. The referential is then built from the database (sort, indexes, profile…).
- **New delta** (listed mode): add its URL **at the end** of the list. At the next update, only this delta is downloaded and applied. Nothing new: no download, no new version.
- **Each delta runs in a transaction**: a broken or truncated delta is rejected without changing anything (the database and the published version stay as they were), and can be applied again once fixed. The transaction statements of the file (`BEGIN TRANSACTION` / `COMMIT`) are replaced by RefExposer's own.
- **Deltas imported by hand**: drop the delta (`.sql`, or the NIST zip as downloaded, with its folder) with the **Import** button of the referential or in `import/<id>/`. It is replayed once into the kept database; the same file is refused the second time. A full database (`.db`) imported by hand replaces the kept one. The deltas are then applied on top (listed ones, or in NSRL mode the ones published after it).
- **Full database loaded again** (listed mode) when the source URL changes, when an already applied delta is removed from the list or moved, or on a forced update. A forced update in NSRL mode also loads the latest full database again.
- **Disk space**: the kept database, the archive during the download of the full database, and the published Parquet files. Prefer the **minimal** sets (distinct files only): they are much smaller than the full ones, which also hold paths and metadata.
- An incremental SQLite database cannot be confidential (the database is kept on disk unencrypted).

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

**Naming the columns when the source cannot be analysed**: a source of tens of GB is not downloaded by the preview, so the editor has no column list to offer. The **key**, the **search columns**, `sort_by` and `indexes` are plain text fields: type the names as they appear in the file (header of a CSV, fields of a JSON record, columns of the Parquet file or of the SQLite table). The import checks them — an unknown sort or index column is refused with its name, a missing search column is a warning in the log — and the columns actually produced appear in the **Schema** tab afterwards. Without these columns a large referential can be browsed page by page but **not searched**, because a search over everything would read the whole file; when `search_columns` is left empty, the search uses every sorted and indexed text column.

**Principle**: for each block of rows of the Parquet file, DuckDB keeps the minimum and maximum value of every column. On sorted data, an exact, prefix or range lookup only reads the few relevant blocks instead of the whole file. Each index is a full copy sorted on another column. The engine picks the right copy from the filter automatically, and these copies are visible in the SQL console (`pdns__by_rdata`…).

**Automatic guardrails** above `REFEX_LARGE_ROWS` rows (50 million by default):

- sampled profile; key uniqueness check and change tracking disabled;
- text search turned into a **prefix** search on the indexed columns (case sensitive);
- facets disabled, column statistics on a sample;
- global sort limited to the sort column;
- total not computed when the filter is not on an indexed column;
- downloads limited to Parquet;
- every API query is stopped after `REFEX_QUERY_TIMEOUT` seconds (60 by default).
- above `REFEX_FULL_SCAN_MAX` (20 GB published), a filter or a lookup that could only read the whole file (column neither sorted nor indexed) is **refused at once**, with what to configure: 6 billion rows of passive DNS without sort take about 2 hours per lookup on a workstation disk.

**Parquet sources of tens of GB** (e.g. the passive DNS of [ip.thc.org](https://ip.thc.org/docs/bulk-data-access), 6 billion rows):

- A **single Parquet file** without `transform`, `options` or confidentiality is **published as is**: no rewrite of the rows. A downloaded or imported file is hard-linked (no copy); a file of `sync/` or of a local source is copied. With `sort_by`, the file is published as is when its row groups already follow each other on the first sort column. The footer statistics tell this without a scan. Otherwise it is rewritten sorted, which is the long step.
- An **interrupted download is resumed** where it stopped (HTTP `Range` + `If-Range`), within the run (see [Downloads and retries](#downloads-and-retries)), then by the next run. When the source changed meanwhile, it is downloaded again from the start.
- The fingerprint of a download is computed **while receiving it**; a file above 1 GB of another source is fingerprinted from its size and date. Files are not read a second time.
- The sampled profile reads **blocks of rows** at random instead of the whole file.
- A **`.parquet.gz` (or `.json.gz`, `.db.gz`…) is decompressed while it is downloaded**: the compressed file never sits on the disk next to the decompressed one. For the THC passive DNS (`rdns-sept.parquet.gz`: 53 GB, about 91 GB decompressed) the disk holds 91 GB at the peak instead of 144. A connection cut is resumed (HTTP `Range`) by the attempts of the same run. **After a restart of the service**, the transfer resumes too, from the last *resume point*: many compressors (pigz, Python `gzip.flush()`, the THC dump) regularly write sync points where the decompression can start again from the last 32 KB already written. RefExposer keeps one every 64 MB of compressed data. It checks the regenerated bytes against the ones already on disk, then the CRC32 of the whole file against the gzip trailer. At most about 64 MB is downloaded again instead of 53 GB. A gzip without sync points starts again from zero.
- A large Parquet source **barely compressed** (uncompressed, or a codec saving less than 25 %) is **rewritten with zstd** instead of being published as is, when the disk can hold the copy. The size of the copy is measured on a sample of one million rows first. Uncompressed text such as DNS names and IP addresses typically shrinks 3 to 10 times, and queries read that much less. Otherwise it is published as is, with a warning in the log.
- A source above `REFEX_KEEP_RAW_MAX` (10 GB) that is **not** the published file itself (decompressed, rewritten, transformed) is **not kept** after the build, whatever `keep_raw` says: keeping it would double the disk used, and triple it during the next update. The next check still answers `304 Not Modified` when the remote file did not change.

**The disk is never filled.** The disk of `data/` is shared with the database and the other referentials: an update that fills it takes them down. An update therefore keeps `REFEX_MIN_FREE_DISK` (5 GB) free:

- a download is **not started** when the announced size does not fit, and **stopped** when the free space reaches the reserve while it is written (download, decompression, archive extraction);
- the work files of DuckDB (spill of a sort or an index) are limited to the free space minus the reserve (`max_temp_directory_size`), and the file written by the build is watched the same way;
- when the free space cannot hold a new source and a new version of the sizes of the last ones, the **previous version** (kept for rollback) is removed first;
- a failed or cancelled update leaves nothing in `work/`.

**Administration › Disk space** shows the free space of each volume (data, import, sync, temporary files) and what takes the space in `data/`: per referential and per kind of file (published version, indexes, previous version, kept database, source files kept, interrupted downloads, build in progress, generated downloads), the largest files, and the folders left by deleted referentials. Each line says what can free it (`storage.keep_raw: false`…). This view is for administrators only and is read only: nothing can be deleted from it. It never follows a symbolic link, counts a hard-linked file once, shows paths relative to `data/`, and walks the folders at most once every 5 s (cached 30 s). About 1 s for 140 referentials (`GET /api/system/storage`).

The update then ends as *rejected* with the reason and the figures (*disk almost full while writing the new version (4.8 GB free, reserve 5.0 GB)*); the published version stays online.

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

For 200 GB of raw passive DNS (about 2.5 to 3 billion rows), expect a 3 to 4 hour import, about 300 GB of disk with two indexes, and lookups that stay around 200–300 ms. Plan fast local storage for `data/`: the sort works on disk beyond the memory given to DuckDB.

**Memory and processors.** Above 2 GB of source, the update runs in low memory mode (the order of the rows is not kept, the sort orders them anyway). The run log shows the limits applied (e.g. *DuckDB limited to 9.3 GiB of memory and 8 thread(s)*). Without settings, DuckDB gets a share of the memory of the container and half of its processors: the import of a 75 GB Parquet file then takes longer, working on disk, instead of taking all the memory of the machine until the service is killed. On a workstation (Docker Desktop, WSL), also limit the container (`REFEX_CPUS=6`, `REFEX_MEMORY=16g` in `.env`) so that the host stays usable. On a dedicated server, give more (`REFEX_DUCKDB_MEMORY_LIMIT=48GB`, `REFEX_DUCKDB_THREADS=16`) for faster imports.

**A stop of the service is not a crash.** `docker stop`, a restart or a new image send `SIGTERM`: the running updates are recorded at once as *interrupted by a stop of the service* and **start again by themselves at the next start** (a large download resumes where it stopped, see above). Automatic updates are not suspended.

**An update that kills the service is not started again by itself.** When the service dies during an update without being asked to stop (killed when out of memory, host power cut), the next start finds the update in progress (`data/<id>/running.json`). It records it as failed, with its logs and the phase reached (*update interrupted: the service stopped during the 'transform' phase, probably killed when out of memory*). It then suspends the automatic updates of this referential (startup, schedule, sync folder), and moves the files of the import folder being imported to `.failed/`. The service no longer dies at each restart. Fix the cause (memory), then start an update by hand: once it goes to its end, the automatic updates resume.

> A column whose name is also an API parameter (`count`, `sort`, `limit`, `offset`, `q`, `columns`) is filtered with the explicit operator: `?count__eq=3`. The interface does this automatically.

### Downloads and retries

A remote site that fails does not end the update at once. As `curl --retry` does, the download is tried again on a connection error, a timeout, a connection cut, or an HTTP `408`, `425`, `429`, `500`, `502`, `503` or `504` answer. It waits 1, 2, 4… seconds (up to 60 s), or the time asked by the server (`Retry-After`, up to 5 min). Other errors (`401`, `403`, `404`…) are not tried again. A transfer whose response carries a strong validator (`ETag` or date) **is resumed where it stopped** (HTTP `Range`), otherwise it starts again from zero. **Only the failures in a row count**: an attempt that brought data (a cut after 14 GB, as HuggingFace does on long transfers) starts the count again, so a large file always ends up downloaded. Each attempt is in the run log (*Download of … failed (HTTP 503): started again in 2s, attempt 3/6*).

5 new attempts by default (`REFEX_DOWNLOAD_RETRIES`), or per source:

```yaml
source:
  urls: [https://flaky.example.org/feed.csv]
  retries: 10                       # 0: no new attempt
```

In the interface: **Source › Advanced options › New attempts when the site fails**.

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

**Complete worked example**, from the creation of the referential to a bulk load from a CSV:
[`examples/scripts/refex-internal.py`](examples/scripts/refex-internal.py) and its
[README](examples/scripts/README.md) — the same commands are in the application help, under *Internal referentials*.

---

## Unified referentials

A referential can be built from the **published data of other referentials** instead of a source of its own
(`source.type: referential`, or *New referential › Other referentials*). Two shapes:

- **union** — the rows of every referential, one after the other, columns matched by name (a column one of them does
  not have is empty for its rows). One list maintained by your team plus the same kind of list coming from another
  team or replicated from another instance become one referential.
- **enrich** — the first referential gives the rows, the others add their columns, joined on a shared value
  (left join: a row without a match keeps empty values).

```yaml
referentials:
  # Every team's assets in one place, keeping where each row comes from
  - id: assets-all
    name: Assets (every team)
    key: hostname
    source:
      type: referential
      referentials: [team1-assets, team2-assets]
    combine:
      mode: union
      source_column: _team           # added column holding the id of the source referential
      # columns: {team2-assets: [hostname, ip, owner]}   # optional: only these columns

  # Assets enriched with their owner and the criticality of their site
  - id: assets-enriched
    name: Assets with owner and site
    key: hostname
    source:
      type: referential
      referentials: [assets, owners, sites]   # the first one gives the rows
    combine:
      mode: enrich
      on: hostname                   # column of 'assets' holding the value to join on
      join_on: {sites: site_code}    # 'sites' matches it with a differently named column
      columns: {owners: [owner, team]}
      prefix: true                   # added columns: owners_owner, owners_team, sites_criticality…
    transform: SELECT * EXCLUDE (sites_internal_note) FROM {source}
    schedule: "30 7 * * *"           # safety net: it is rebuilt as soon as a source publishes a new version
```

- It is **rebuilt automatically** whenever one of its sources publishes a new version (trigger *Source referential
  updated*), and the run ends as *Unchanged* when the versions it read have not moved.
- Each source must have been **published at least once**: its columns and its Parquet file are what the build reads.
  The run log and the **Source** tab record which version of each source the data came from, and the source's own
  **Source** tab lists the unified referentials built from it.
- A **confidential** source can only be combined into a confidential referential, otherwise its data would be
  published unencrypted and the definition is refused. A referential cannot read itself, directly or through another
  unified referential, and a referential used by one cannot be deleted before it.
- Everything else behaves like a normal referential: key, search columns, sort and indexes, quality rules, generated
  downloads, rights, SQL table. In the transformation, `{source}` is the combined result and `{source0}`,
  `{source1}`… are the referentials on their own. Bloom filters and MaxMind databases have no rows to combine.

### From the SQL console

**Save as referential**, next to the exports of the [SQL console](#sql-console-security), turns the query that has
just run into a unified referential. The **query is stored, not its rows**: the join written to explore the data
becomes a referential rebuilt whenever one of the referentials it reads publishes a new version.

```bash
curl -X POST -H "$H" -H "Content-Type: application/json" "http://localhost:8080/api/sql/referential" -d '{
  "id": "kev-epss", "name": "Exploited vulnerabilities and their EPSS score", "key": "cveID",
  "sql": "SELECT k.cveID, k.vendorProject, e.epss FROM cisa_kev k JOIN epss e ON e.cve = k.cveID"
}'
```

The referentials the query reads are detected from its table names (`sources` names them explicitly), and the
query is checked in the same sandbox as the console before anything is stored: it must parse, read only those
referentials and be usable as a subquery. The stored definition binds each table name to the published file of its
referential with a CTE, so the query is kept as it was written and stays editable in the referential editor.

Reserved to administrators and advanced users, limited to the referentials the account may read, and audited as
`referential.create`. It starts with **no group grant**: an administrator opens it to the groups that need it. An
advanced user gets the manage right on what it creates, as for an internal referential.

---

## Governance: visibility and ownership

Every referential says **what kind of data it holds** and **who answers for it**. Both are shown wherever it
appears — on its card in the dashboard, in its header, in the API — because the two kinds are not handled the
same way, and because a question about the data needs someone to ask.

| Field | What it means |
|---|---|
| `visibility: internal` (the default) | Company data. Not published anywhere else: it is not to be copied into a ticket, a pastebin or a third-party tool without thinking about it. |
| `visibility: public` | Open data or an external feed (CISA KEV, NVD, IANA, a public blocklist…): already published outside, and quoting it is free. |
| `owner` | Who answers for this data: a name or, better, an e-mail address. The **e-mail notification channels** can send the alerts of a referential to its owner (*Also the owner of the referential*). |
| `owner_team` | The team behind that person, for when they move on. |

- **Where it shows**: a filled **Internal** (padlock) or **Public** (globe) badge on each card and in the
  header of the referential, the owner with their initials beside it, and a quick filter
  **All / Internal / Public** in the dashboard. The *Referentials* stat card says how the catalogue splits.
- **Where it is set**: *Description* step of the referential editor, or in YAML:

```yaml
- id: staff-directory
  name: Staff directory
  visibility: internal          # internal (default) · public
  owner: dpo@example.com
  owner_team: Security
```

- `visibility` and the owner are **metadata**: changing them never rebuilds the referential.

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

## Personal data: column masking

A referential can name the columns that hold **personal data**. Those columns are **masked for everyone**, and
the real values only come out for an account that is entitled to see them **and** asks for them explicitly.

```yaml
- id: staff-directory
  name: Staff directory
  key: id
  pii:
    columns: [email, full_name]
    strategy: hash              # hide · partial · hash
    reveal_groups: [dpo, security]
    reveal_users: []            # account ids, for a named exception
    note: "GDPR record R-14, kept 3 years"
```

| `strategy` | What is served | Good for |
|---|---|---|
| `partial` (the default) | `a***e` — the first and last characters | Recognising a value you already know |
| `hash` | a stable 16-character pseudonym, salted with the instance secret | Counting, grouping and **joining** on the column without the value |
| `hide` | `***` | When even the shape must not leak |

**It is one rule, applied where the data is read.** The DuckDB view of the referential selects the masked
expression instead of the column, so there is nothing for a caller to remember: rows, lookups, exports,
column statistics, the global search, the change view, the generated downloads, the SQL console, the MCP
servers and the assistant are all masked, and so is anything added later.

**Revealing takes two things**, and both are needed:

1. **the right** — the account is named in `pii.reveal_users`, belongs to one of `pii.reveal_groups`, or is an
   administrator;
2. **the request** — `reveal=true` on the call (the *Show personal data* switch in the Data tab, the SQL
   console and the diff view). Every such request is written to the **audit log** (`pii.reveal`, with the
   columns and the path).

So a script, a cron job, a download link or a question to the assistant **never** gets personal data by
accident: masked is the default, not a mode to turn on.

- **The assistant and the MCP servers can never reveal.** They act for a model or a tool, which cannot give
  the consent, so the switch is simply not available to them — whatever the prompt asks, and even when the
  account behind them is an administrator.
- **Generated downloads** (CSV, Excel, Parquet…) are produced from the masked view and cached, so one user's
  right never leaks into a file served to another. The **source files** and the **previous version**, which
  are served straight from disk, are refused to an account that may not reveal; use
  `GET /api/referentials/{id}/export?reveal=true` instead.
- **A unified referential reads its sources masked**: personal data does not spread into a derived
  referential by being combined, and the masked values are what lands in its own Parquet file. To keep the
  real values downstream, mask them on the unified referential rather than on its source. Changing `pii`
  rebuilds the referentials built from it.
- **Masking is not encryption**: the published Parquet file still holds the real values. When they must not be
  readable on disk either, mark the referential **confidential** as well — the two combine.
- **A masked column is served as text**, whatever it held: a masked number is not a number any more, and
  saying so is better than serving a plausible wrong value.
- **A configured column the data does not have** (a renamed source, a typo) is reported as `missing` in the
  interface and in the API instead of silently taking the masking away.
- **One consequence worth knowing**: with `hide` or `partial`, two rows differing only in a masked column read
  as identical in the change and diff views. Use `hash` when that matters — it keeps them distinct.

API: `pii` on `GET /api/referentials` and `/api/referentials/{id}` (columns, strategy, note, `can_reveal`,
`missing`), `reveal=true` on `/rows`, `/export`, `/lookup`, `/changes`, `/diff`, `/columns/{c}/stats`, and
`{"reveal": true}` on `POST /api/sql`.

## Outgoing connections

**Administration › Outgoing connections** lists every URL the referentials contact, **grouped by host**: an API called by several referentials appears once, with all of them.

- **Per host**: referentials, URLs, estimated **calls per day** (from the schedules), authentication used (header names, Basic, Git token, `${secret:…}` names: never the values), last contact and volume downloaded, referentials in error. Hosts reached in plain `http` are flagged.
- **Per URL**: every referential that uses it, its role (source, delta of an incremental update, Git repository, online enrichment of a Bloom filter at lookup time), its schedule and its last contact (`HTTP 304` when unchanged). A URL **downloaded by several referentials** is marked *shared*.
- **Safe to show**: credentials in URLs are masked (`user:password@`, query parameters such as `api_key`, `token`, `secret`…).
- **Other connections of the instance**: notification webhooks, SMTP, syslog, LDAP, OpenID Connect, outgoing proxy.
- **Export CSV**: one line per host (allowlist of a firewall or a proxy), or one line per URL and referential. API: `GET /api/admin/endpoints`, `GET /api/admin/endpoints/export?hosts_only=true`; MCP tool `list_endpoints`.

## Secret manager

API keys, license keys, tokens and passwords of the sources are stored in **Administration › Secrets**, and the definitions only hold a reference:

```yaml
source:
  urls: [https://api.vendor.example/v2/feed.csv]
  headers:
    Authorization: "Bearer ${secret:vendor-api}"
# or: basic_auth: "123456:${secret:maxmind-key}"   ·   Git: token: "${secret:gitlab-token}"
```

- **Write-only**: values are encrypted at rest (AES-256-GCM, key derived from `REFEX_SECRET_KEY`, bound to the name of the secret) and never returned, neither by the interface nor by the API nor by the MCP server. The list shows the name, the description, the allowed hosts and the referentials using each secret.
- **Where**: header values, `basic_auth` and the Git `token`, the places that are never displayed. A reference in a URL or a path is refused. In the editor, the key button of these fields picks a secret.
- **Allowed hosts** (`api.vendor.example`, `*.vendor.example`): the secret is only sent to these hosts. A source pointed at another server (by mistake, or by an agent misled by a prompt) fails with an explanation and sends nothing. Restrict every secret that can be.
- **Checked**: a definition referencing an unknown secret is refused, and a secret still in use is only deleted when forced. A new value takes effect at the next update, without editing the definitions.
- **Literal credentials** still written in a definition are masked in the administration API (`********`). Sent back unchanged, a masked value keeps the stored one; a new value replaces it.
- `${REFEX_SOURCE_…}` environment variables remain supported. Secrets are part of the configuration backups, still encrypted: restore them with the same `REFEX_SECRET_KEY`.

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

## Deltas: merging new data into a referential

By default each new data set **replaces** the published version. With `merge`, a table referential accumulates instead: every new data set is a **delta merged into the published data**. It applies to every way data arrives: the download of the source URL (a feed of the last 48 hours, a daily export of the changes…), the **Import** button or `POST /import` (push), the import folder and the sync folder.

```yaml
id: iocs
format: csv
key: ioc                    # upsert key: a row of the delta replaces the published row with the same ioc
source:
  urls: [https://feeds.example.com/iocs-last-48h.csv]
merge:
  delete_column: _deleted   # optional: rows with _deleted = true remove the published row (column not published)
```

- **With a key**: a row of the delta replaces the published row of the same key (upsert), the others are kept. **Without key**: the rows are added, identical rows kept once.
- **New columns** of a delta are added (empty for the older rows). The same delta received twice changes nothing; an **empty delta** keeps the published version.
- Validation and **quality rules** run on the merged result; the history shows the changes (+added / ~modified / -removed), and the referential page the number of deltas merged since the base (*deltas* badge).
- **New base**: the first data set is the base. To start again from a full data set, import it with **Import › New base** (`-F mode=replace`), or start an update with **Update › Replace with the source** (`POST /refresh?replace=true`).

```bash
# Push a delta (merged), then a full data set that becomes the new base
curl -X POST -H "$H" -F files=@delta.csv "http://localhost:8080/api/referentials/iocs/import"
curl -X POST -H "$H" -F files=@full.csv -F mode=replace "http://localhost:8080/api/referentials/iocs/import"
```

Bloom filters and SQLite databases have their own deltas (`incremental`, see above). Internal referentials are edited row by row.

---

## Time travel: past versions and diffs

Turned on per referential, **time travel** keeps each published version and lets two of them be compared.

```yaml
- id: external-scan
  name: External scan
  key: asset
  primary_key_columns: [asset, port]   # what identifies a row (default: key)
  storage:
    time_travel: true
    max_snapshots: 30                  # the oldest ones are dropped beyond this
```

- **What it costs**: a snapshot is a **hard link** on the published Parquet file, so it takes no extra space
  until the version it shares its blocks with is replaced. It shows up as *Past versions kept* in
  **System › Disk space** (at its full size, which overstates what it really holds).
- **Where**: a **History & diff** tab appears on the referential — two version pickers, the
  `+X added, −Y removed, ~Z modified` summary, the rows of each category, and the before/after of every
  changed column. The list below it shows the versions kept and can delete one.
- **How a row is identified**: `primary_key_columns`, failing that the `key` of the referential, failing both
  the whole row — and then a change reads as one removal and one addition, which the interface says plainly.
- **A schema change** does not break the comparison: only the columns both versions have are compared, and the
  others are listed.
- **Masking applies**: a diff is masked like everything else, and `reveal=true` works the same way.
- **The assistant can use it**: the tools `list_referential_snapshots` and `diff_referential_snapshots` are
  how it answers "what is new since Monday", instead of guessing from two queries. They are also in the
  [MCP data server](#mcp-data-server-every-user).

This is not the same thing as the **Changes** tab, which always compares the published version with the one
before it (`storage.keep_previous`). Time travel goes further back, and compares any two versions.

API: `GET /api/referentials/{id}/snapshots`,
`GET /api/referentials/{id}/diff?from={id}&to=latest&kind=summary|added|removed|modified`,
`DELETE /api/referentials/{id}/snapshots/{snapshot_id}` (manage right).

## Synchronization between instances

A RefExposer instance can **replicate the referentials of another one** (a central instance feeding regional or isolated ones, production feeding pre-production…), with their definitions: name, description, category, key, search columns, **quality rules**, **SQL transformation**, indexes, generated downloads, confidentiality.

**On the instance that shares (the peer)**: create a **service account**, give it (or its group) the **read** right on the referentials to share, and create an API token for it. Nothing else: it exposes `GET /api/federation/catalog`, which lists what this account may read.

**On the replica**: **Administration › Synchronized instances › Add a peer**: URL of the peer, token, then **Test the connection** to choose the referentials (or all of them, including the ones shared later), the prefix of the local identifiers (`prod-` gives `prod-iocs`), the schedule and the groups given the read right on the replicas. Each synchronization:

1. reads the catalog of the peer and creates or updates the definition of each replica;
2. starts the update of the replicas whose published version changed over there. The replica downloads the **published file of the peer** (Parquet, Bloom filter or MaxMind DB) with a conditional request. The SQL transformation already ran upstream and stays visible on the replica, and the quality rules are checked again here.

- The token is kept in the **secret manager** (`peer-<name>`), restricted to the host of the peer: it is never sent elsewhere.
- Replicas are **read-only** here (their definition follows the peer); they show a *peer* badge.
- A referential **no longer shared** (removed, or right withdrawn) stays on the replica with its last data, marked *missing over there*. Removing the peer keeps the replicas (no longer updated), or deletes them with their data and the token.
- An identifier already used on the replica is reported as a conflict: give the peer a prefix.

```bash
curl -X PUT -H "$H" -H "Content-Type: application/json" "http://replica:8080/api/admin/federation/peers/prod" \
  -d '{"url": "https://refexposer.example.com", "token": "rfx_…", "prefix": "prod-", "schedule": "0 * * * *"}'
curl -X POST -H "$H" "http://replica:8080/api/admin/federation/peers/prod/sync"
```

---

## Users and rights

### Roles

| Role | Can… |
|---|---|
| **Administrator** | everything: all referentials, users, groups, rights, settings, audit log, System page, configuration reload |
| **Advanced user** | the referentials granted to them, plus the SQL console and creating referentials (internal, or a query of the console kept as one: they get the manage right on the ones they create) |
| **User** | only the referentials granted to them, directly or through their groups (no SQL console) |

### Rights per referential

A right links a **user or a group** to a **scope**, with a level:

| Scope | Covers |
|---|---|
| One referential | that referential |
| **A category** | every referential of that category, **now and as they are added to it** |
| `*` | every referential |

| Level | Allows |
|---|---|
| **Read** | see the status, explore, search, lookup, export, download, query in SQL (administrators and advanced users) |
| **Manage** | read + start, force or cancel an update, manual imports, editing internal referentials |

The effective right is the highest of the direct rights and those of the groups. A referential that is not granted is **invisible**: absent from the lists, the global search and the SQL console, with a `404` answer when it is requested directly.

#### Rights on a whole category

Categories group the referentials in the interface; a right can be given on one, which is the way to hand a set
of referentials to a team in one row instead of a dozen:

```bash
curl -X POST -H "$H" -H "Content-Type: application/json" "http://localhost:8080/api/admin/grants"   -d '{"category": "Threat intelligence", "group_id": 3, "level": "read"}'
```

The scope picker of the rights editor (a user's page, a group's page, the **Access** tab of a referential) offers
the three forms in one list: *All referentials*, *By category* (with how many each holds) and *One referential*.

- **Resolved at every request**, against the categories the referentials have at that moment: a referential added
  to the category is covered without touching the rights, and one **moved out of it stops being**. Changing the
  category of a referential therefore changes who may read it — it is a rights change made from the referential
  editor, and it is in the audit log as a `referential.update`.
- A right on a category of a referential appears on its **Access** tab marked `category`, read-only there: it is
  edited from the user or the group it was given to, like a `*` right.
- The **highest** level wins, so a direct `manage` on one referential sits on top of a `read` given on its
  category.
- A category nothing is in yet is accepted: the right waits for its first referential rather than being refused.
- Stored as `category:<name>` in the scope of the grant, carried by the configuration export and by the MCP tool
  `grant_access` (`category` argument).

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
- Source credentials (`source.headers`, `basic_auth`, Git `token`) are never returned by the API (masked for administrators too). They should reference a secret of the [secret manager](#secret-manager) (`${secret:…}`: encrypted, optionally restricted to its hosts) or a `${REFEX_SOURCE_…}` environment variable instead of being stored in the definitions.
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

# The same query kept as a referential, rebuilt whenever a source publishes a new version
curl -X POST -H "$H" "http://localhost:8080/api/sql/referential" -H "Content-Type: application/json" \
  -d '{"id": "kev-epss", "name": "KEV and EPSS", "key": "cveID", "sql": "SELECT k.cveID, e.epss FROM cisa_kev k JOIN epss e ON e.cve = k.cveID"}'

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
- every query is recorded in the audit log;
- *Save as referential* goes through the same sandbox before anything is stored, and the stored query reads only the referentials the account may read.

---

## Data quality

Each referential can declare **quality rules**, checked on every new version in one scan of the data:

```yaml
quality:
  rules:
    - {column: ip, check: format, format: ipv4, threshold: 99.9}        # 99.9 % of the rows at least
    - {column: sha256, check: format, format: sha256, severity: error}  # error: the version is rejected
    - {column: cve, check: unique}
    - {column: severity, check: values, values: [low, medium, high, critical]}
    - {column: score, check: range, min: 0, max: 10}
    - {column: label, check: length, max_length: 255}
    - {column: code, check: pattern, pattern: "[A-Z]{3}-[0-9]+"}
    - {check: expression, expression: "first_seen <= last_seen", name: "consistent dates"}
```

| Check | A row passes when |
|---|---|
| `not_null` | the value is present (not NULL, not blank) |
| `unique` | its value appears only once |
| `format` | the value is a valid `ipv4`, `ipv6`, `ip`, `cidr`, `domain`, `email`, `url`, `md5`, `sha1`, `sha256`, `sha512`, `cve`, `uuid`, `mac`, `asn` or `country` (ISO 3166 alpha-2) |
| `pattern` | the whole value matches the regular expression (RE2) |
| `values` | the value is among the allowed ones |
| `range` | the value is between `min` and `max` (numbers, or ISO dates / text) |
| `length` | the value has between `min_length` and `max_length` characters |
| `expression` | the SQL condition is true |

- **Empty values** only fail `not_null`: the other checks ignore them. The completeness of the **key** is checked automatically.
- **Threshold** (100 % by default): the share of rows that must pass. **Severity**: `warning` publishes the version and marks it; `error` rejects it, and the published version is kept (a *quality gate*, like `validation`). Internal referentials are never rejected, only marked.
- **Score**: the mean of the pass rates (0–100). **Status**: *ok*, *warning* (a warning rule is not met) or *failed*.
- **Where**: the **Quality** tab of the referential (score, history of the last versions, pass rate of each rule, failing rows and up to 5 failing values; never shown for a confidential referential); a badge and the *Quality issues* filter in **Administration › Referentials**; `quality` in the API answers (`GET /api/referentials/<id>/quality`).
- **Changing the rules** does not download anything again: the published version is checked again in the background (or with **Check again**, `POST /api/referentials/<id>/quality/check`).
- **Notifications**: the `quality` event warns when a version is published with rules not met while the previous one met them. A failing `error` rule rejects the version, which is a `failure`.
- The rules are edited in the referential editor (step *Description*). They take one scan of the data: on billions of rows, keep them few.

## Alerts (in the application)

**Alerts** is the list of what needs a look, newest first, reachable from the bell in the header and from the
menu. It gathers two things:

| Kind | Raised when |
|---|---|
| **Watchdog** | A [scheduled investigation](#watchdogs-scheduled-investigations) found something, or could not finish. |
| **Update** | The last update failed, was rejected by the validation, or found a corrupted source. |
| **Quality** | The published version does not pass its [quality rules](#data-quality). |
| **Stale** | No new version within the expected delay (`max_age`, or twice the schedule interval). |
| **Drift** | The last version changed the volume by more than 20 % on a referential that had enough rows for the ratio to mean something. A drop counts for more than growth. |

Clicking an entry opens what it takes to act on it: the **report the watchdog wrote** (its Markdown table of
affected items, the queries it went through, whether each notification was delivered), the **log of the failed
update**, the **failing quality rules** with their pass rates, or the **figures** of the drift — and a link to
the referential or the watchdog.

- **Nothing to acknowledge, nothing to clean up.** The list is built from the state of the referentials and the
  recorded watchdog runs, so an alert disappears on its own once the thing behind it is fixed, and it can never
  disagree with what the rest of the interface shows.
- **Filtered by what you may see**: a referential you cannot read is not there, a watchdog belongs to its owner
  (administrators see every one), and ordinary accounts get no watchdog alerts at all.
- The **badge on the bell** counts what arrived since you last opened the page; that mark is kept in your
  browser, so it is per person and per device, and nothing about it is stored on the server.
- This is the **in-application** view. Pushing the same events out by e-mail or webhook is
  [notification channels](#notifications-e-mail-webhooks), configured per channel; the two are independent.

API: `GET /api/alerts?limit=&kind=` (`watchdog`, `failure`, `quality`, `stale`, `drift`), `GET /api/alerts/{id}`.

## Notifications (e-mail, webhooks)

**Administration › Notifications** sends the events of the referentials to channels:

| Event | When |
|---|---|
| `failure` | an update ends in error, with a corrupted source or a rejected version (the published version is kept) |
| `recovered` | first successful update after a failure |
| `published` | a new version is published |
| `quality` | a new version is published with quality rules not met (severity `warning`), the previous one meeting them |

- **No flood**: a referential failing at every scheduled update is notified once, when it starts failing, then at its recovery. Tick *Every failed update* to be told each time.
- **Filters**: every channel chooses its events and its referentials (ids and / or categories; all when empty).
- **E-mail**: SMTP server in **Settings › E-mail** (STARTTLS, TLS or an internal relay, authentication, company certificate authorities, test button). Recipients, plus optionally the `owner` of the referential when it is an e-mail address. The message gives the error, the state before, a link to the referential and the last lines of the log.
- **Webhook**: `POST` of a JSON document. The generic document holds `event`, `text` (a ready-made sentence, read as is by Slack, Mattermost, Rocket.Chat, Google Chat and the classic Teams incoming webhooks; Teams Workflows expect an Adaptive Card template), `referential` (id, name, category, owner, url), `run` (status, message, trigger, dates, rows) and `previous_status`. A **template** can shape the body for another tool, with `{{placeholders}}` between quotes, e.g. Discord: `{"content": "{{text}}"}`.
- **Credentials and signature**: header values may be `${secret:…}` references (restricted to their hosts, see [Secret manager](#secret-manager)). Literal values are masked by the API. With a signing secret, the body is signed: `X-RefExposer-Signature: sha256=<HMAC-SHA256 of the body>`. `X-RefExposer-Event` gives the event.
- **Reliability**: deliveries run in the background (an update never waits for them), with 3 attempts (after 2 s, then 8 s). The 500 last deliveries and their result are listed on the page (`data/.notifications.jsonl`). The send button of a channel sends a test at once.
- API: `/api/admin/notifications` (channels, `POST /{id}/test`, `GET /deliveries`), `PUT /api/admin/settings/smtp`, `POST /api/admin/settings/smtp/test`.

## In-application assistant

A chatbot on a page of its own (**Assistant** in the menu), backed by **a model you host**: Ollama, or anything
speaking the OpenAI API (vLLM, llama.cpp server, LocalAI, TGI). Disabled by default; the menu entry only appears
once an administrator enabled it in **Settings › Assistant**.

In **agent mode** it uses the application for the person who is chatting: it lists the referentials, queries them,
runs SQL, hands over a query to open in the SQL console, produces a JSON or CSV file, and — when writes are
allowed — starts an update or creates a referential. The same tools as the [MCP servers](#mcp-data-server-every-user),
driven from the chat instead of an external client.

### Rights: the session of the user, never more

Every tool call goes through the **REST API in-process, carrying the authentication of the person chatting**: the
same session, the same per-referential grants, the same validations, the same audit log. There is no elevation
anywhere.

- A user who cannot see referential *B* cannot query it from the chat: the API answers `404`, exactly as it would
  in the interface, and the model is told it has no access.
- An administrator can create a referential from the chat; a reader asking for the same thing gets a refusal.
- Write tools are offered only when **Allow actions that change something** is on, and they still obey the rights
  of the account.

### Confidential referentials

**Confidential referentials may be queried** is off by default. While it is off, nothing of a confidential
referential reaches the model — not its rows, not its columns, not its name:

- the identifiers of every confidential referential are put in the `denied` set of the account for the whole tool
  call, and `CurrentUser.level()` checks that set **before the role**, so an administrator is restricted too;
- every read goes through it: `/rows`, `/lookup`, `/export`, `/download`, `/search`, `/facets`, the catalog, and
  the tables registered in the **SQL sandbox** — a query naming a confidential table finds no such table;
- the agent refuses a tool call naming one before making it, and filters what the administration endpoints list.

It is an authorization rule, not a prompt instruction: a model that tries anyway gets nothing.

### Configuration (Settings › Assistant)

| Setting | What it does |
|---|---|
| API, address | `ollama` (`/api/chat`) or `openai` (`/v1/chat/completions`), and the address of the server |
| API key | Optional: most local servers have none. Sent as `Authorization: Bearer` |
| Extra headers | Any header the gateway in front of the model expects (tenant, routing, an API key under another name) |
| Model | Chosen from the list the server returns, after **Test and list the models** |
| Verify TLS | Stays on: the **certificate authorities of Settings › Network & proxy** are the ones used, so an internal root CA needs nothing more here |
| Temperature, top_p, answer limit, messages kept | Generation, and how much of the conversation goes into the prompt |
| Context window | `num_ctx` for Ollama: 0 leaves it to the server, which is often small — raise it when the agent reads large tool results. The OpenAI API has no such parameter |
| Stop sequences, other options | Anything else the server accepts, as JSON: for Ollama inside the `options` object (`repeat_penalty`, `top_k`, `min_p`, `mirostat`, `seed`, `num_thread`…), for an OpenAI-compatible server at the top level (`repetition_penalty`, `top_k`, `frequency_penalty`…). A key set there wins over the fields above; the keys that make up the request itself (`model`, `messages`, `stream`, `tools`…) are refused. **Sent to the model right now** shows the resulting block |
| Agent mode, steps, writes | Whether it may use the application, how many tool calls per answer, and whether it may change anything |
| Confidential referentials | Above |
| Groups allowed, accounts allowed | Who may use it: these groups and these named accounts. Everyone when both are empty; administrators always; service accounts never (no interface session) |
| System prompt | Visible and editable, with **Load the default** and **Back to default** |
| Questions per minute, retention | Per-account rate limit, and how long conversations are kept (purged by the `assistant_retention` task) |

The browser never calls the inference server: RefExposer is its only client, so the server does not have to be
reachable from the workstations. The address, the API key and the headers are administration settings; the key is
encrypted at rest and never returned to the browser.

### What the user sees

The conversation is kept server-side and **belongs to one account**: someone else's conversation is reported as
unknown. It goes on until the user deletes it (one conversation, or all of them), so several questions in a row
build on each other. Answers stream in as they are written, each tool call shows up as a line (tool, arguments,
result), and the answer is rendered as Markdown built into React elements — no HTML, no remote images, links
with their real target.

API: `GET /api/assistant` (availability), `/api/assistant/conversations` (list, create, delete all),
`/api/assistant/conversations/{id}` (read, rename, delete), `POST /api/assistant/conversations/{id}/messages`
(server-sent events: `token`, `step`, `done`), `POST /api/assistant/test` (administrators).

## Watchdogs: scheduled investigations

A **watchdog** is the assistant asking itself the same question again and again, over a **fixed set of
referentials**, and telling someone **only when it finds something**.

They are **off by default**: an administrator turns them on in *Settings › Assistant* (**Scheduled
watchdogs**), which needs the agent mode. They are also **reserved to administrators and advanced users** —
for anyone else the menu is not there and the whole API answers 403. Turning the switch off removes every
schedule at once, and a trigger that slips through is refused rather than run.

### How one is defined

| Field | What it does |
|---|---|
| Name, description | What this watchdog is for. |
| **Referentials** | The **only** ones it may read. Chosen explicitly — there is no "everything". |
| **Trigger** | `cron` (a crontab expression, or one of the usual schedules) or `on_sync`: right after one of its referentials publishes a new version. |
| **Prompt** | The investigation itself. |
| **Recipients** | E-mail addresses, and/or the [notification channels](#notifications-e-mail-webhooks) already configured. |
| **Severity floor** | A finding below it is recorded, but nobody is woken up. |

### Three things it cannot do

- **Leave its scope.** The list of referentials travels **signed** with every tool call, and
  `CurrentUser.level()` returns 0 for anything outside it. The model can ask for another referential; it does
  not exist for the call.
- **Exceed its account.** It runs with the rights of the account it belongs to, through a session opened for
  the run and closed after it, so every tool call goes through the same checks as a click in the interface.
  Creating a watchdog is refused when that account cannot read a referential of the scope.
- **Write.** The tools that change something are off for a watchdog, whatever *Allow writes* permits in the
  chat.

### What it answers

The model is given a prompt of its own and must return a structured verdict:

```json
{"finding_detected": true, "severity": "HIGH", "summary": "3 machines are not in the CMDB",
 "details_markdown": "| host | ip | seen |\n| --- | --- | --- |\n…", "affected_items_count": 3}
```

An answer that is **not** that JSON is recorded as an **error**, with the text kept — never as "nothing to
report". Silence is never mistaken for an all-clear.

### Ready-made investigations

The editor offers three templates (plus a free one), pre-filling the prompt and the description:

- **Shadow IT hunt (CMDB vs infrastructure / network)** — assets alive on the network or the hypervisor that
  the CMDB does not know, or that carry no EDR agent.
- **External exposure drift (EASM)** — new sub-domains, newly exposed addresses or sensitive services, using
  the diff with the last snapshot when the referential keeps them.
- **IAM audit & orphan accounts** — the HR referential crossed with the active accounts (AD, VPN,
  applications): accounts still open for someone who has left.

### Following them

Findings also land in [Alerts](#alerts-in-the-application), with the report attached. Each watchdog lists its
**executions**: date, what started it (`cron`, `on_sync:<referential>`, `manual`),
*nothing to report* / *finding* with its severity / *failed*, the report the agent wrote, the tool calls it
went through, and whether each notification was delivered. **Run now** starts one on the spot (in the
background: an investigation calls the model several times). The 100 most recent runs of a watchdog are kept.
Every run is audited (`watchdog.run`).

API: `GET|POST /api/watchdogs`, `GET|PATCH|DELETE /api/watchdogs/{id}`, `POST /api/watchdogs/{id}/run`,
`GET /api/watchdogs/{id}/runs`, `GET /api/watchdogs/runs`, `GET /api/watchdogs/templates`.

## MCP data server (every user)

Each user, or service account, can connect an AI assistant (Claude Code, Claude Desktop, any [MCP](https://modelcontextprotocol.io) client) to the referentials **it may read**, with **its own API token** (**My account › API tokens**):

```bash
claude mcp add --transport http refexposer https://refexposer.example.com/api/mcp/user --header "Authorization: Bearer $REFEX_TOKEN"
```

- **Enabled by an administrator** in **Settings › MCP server › Data server**, for every account or for some groups only (administrators always). Disabled by default. The URL is then shown in **My account**.
- **The rights of the token, nothing more**: no elevation. The tools call the REST API with the token, so a referential the account may not read stays invisible (listed nowhere, `404` on a lookup), and SQL is offered to advanced users and administrators only.
- **Reading only**: `whoami`, `list_referentials`, `get_referential` (columns, profile, state), `query_rows` (filters `column__op`, text search, sort), `lookup`, `lookup_many` (up to 10,000 values), `search` (every referential), `column_stats`, `get_changes`, `get_quality`, `list_runs`, plus `run_sql` and `sql_schema` when the role allows it.
- SQL queries are recorded in the audit log, as from the console.

## Administration MCP server

An AI agent (Claude Code, Claude Desktop, any [MCP](https://modelcontextprotocol.io) client) can drive RefExposer: analyse a source, create and update referentials, follow imports, query the data, manage secrets, discover a repository, manage users and rights.

1. **Administration › Users › New service account**, then create its API token.
2. **Administration › Settings › MCP server**: enable the server, choose the service account(s) allowed, optionally **read-only**.
3. Connect the client to `https://<public URL>/api/mcp` (Streamable HTTP) with the token:

```bash
claude mcp add --transport http refexposer https://refexposer.example.com/api/mcp --header "Authorization: Bearer $REFEX_MCP_TOKEN"
```

- **Disabled by default.** It answers only to the API tokens of the chosen service accounts. Any other token, a session cookie or a disabled server is refused.
- **Admin powers through the server only**: the token alone keeps the usual rights of the account on the REST API (role *user*). Through `/api/mcp`, its calls are signed by the server and run as an administrator. Disabling the server, or removing the account from the list, withdraws these powers at once.
- **Same rules as the interface**: every tool calls the REST API in-process, as the service account. Validations and rights apply, and every change is recorded in the audit log with `via: mcp`.
- **Tools**: `list_referentials`, `get_referential`, `get_definition`, `list_runs`, `query_rows`, `lookup`, `search`, `run_sql`, `sql_schema`, `preview_source`, `create_referential`, `update_referential`, `delete_referential`, `refresh_referential`, `cancel_run`, `list_secrets`, `create_secret`, `update_secret`, `delete_secret`, `discovery_scan`, `discovery_apply`, `export_configuration`, `import_configuration`, `list_endpoints`, `list_users`, `list_groups`, `list_grants`, `grant_access`, `get_audit_log`, `list_tasks`, `run_task`, `get_settings`, `reload_configuration`, plus `api_endpoints` and `api_request` for the rest of the API (sign-in and personal tokens excluded).
- **Read-only mode**: only the reading tools are offered (state, data, SQL, preview, scan), and the API refuses any change made through the server.
- **Secrets stay write-only**: an agent can create or replace a secret, never read one back. With allowed hosts, a secret cannot be sent elsewhere, even by an agent.

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
| Least privilege | Three roles: administrator, advanced user, user. Rights are given to users or groups (read / manage) on **one referential, a whole category, or all of them**; a category right is resolved at each request from the current category of each referential. A referential that is not granted is invisible: it is missing from the lists and the search, and answers `404`, like an unknown one. See [Users and rights](#users-and-rights). |
| SQL console | Reserved to administrators and advanced users. Each set of rights gets its own **sandbox**: only the granted tables, read-only, a single `SELECT` per query, no external access, a locked configuration and a time limit. See [SQL console security](#sql-console-security). |
| Watchdogs | Off unless the agent mode is on. A watchdog is confined to the referentials it names: the scope travels **HMAC-signed** with every tool call and `CurrentUser.level()` returns 0 outside it. It runs with the rights of its own account, through a session opened for the run and closed after it, and the tools that change something are off whatever the assistant settings allow. An answer that is not the expected structured verdict is recorded as an error, never as an all-clear. Every run is audited (`watchdog.run`). See [Watchdogs: scheduled investigations](#watchdogs-scheduled-investigations). |
| Assistant (LLM) | Off by default. Every tool call goes through the REST API **with the session of the person chatting**: no elevation, so the model can never read or do what that account could not. Confidential referentials are added to the `denied` set of the account for the whole call, checked **before the role**, which covers the rows, the catalog and the SQL sandbox alike; the agent also refuses a call naming one. The internal header that applies the restriction is HMAC-signed, so a browser cannot forge it. Model output is rendered as React elements (no HTML, no remote images), tool results are passed as data and never as instructions, generation is rate limited per account and every action is audited with `via: agent`. See [In-application assistant](#in-application-assistant). |
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
| Personal data | The columns named in `pii` are **masked by the DuckDB view of the referential**, so every read is masked: rows, lookups, exports, statistics, search, diffs, generated downloads, the SQL console, the MCP servers and the assistant. Revealing them needs the right (`reveal_groups` / `reveal_users`, or administrator) **and** an explicit `reveal=true`, which is audited (`pii.reveal`); the assistant and the MCP servers can never reveal. A unified referential reads its sources masked. See [Personal data: column masking](#personal-data-column-masking). |
| Source credentials | API keys, license keys and Git tokens (HTTP headers, `basic_auth`, `token`) reference the secret manager (`${secret:…}`: encrypted, write-only, optionally restricted to their hosts) or `${REFEX_SOURCE_…}` environment variables, so they stay out of the definitions. Only this prefix of variables is expanded, so other secrets can never be sent to a source. Credentials are masked in the API answers. |

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

### Supply chain

The dependencies, the images and the Dockerfiles are scanned by one command. Docker is the only prerequisite:
every tool runs in a container, nothing is installed on the host.

```bash
make scan          # or: sh scripts/scan.sh         dependencies + Dockerfiles + images
make scan-deps     # or: sh scripts/scan.sh deps    dependencies only, no image needed
make sbom          # or: sh scripts/sbom.sh         CycloneDX bill of materials of the images
make lock          # or: sh scripts/lock.sh         recompile the Python lock files
```

#### Pinned dependencies

Both parts install from a lock file, so two builds of the same commit install the same code:

| Part | Declared in | Installed from | Guarantee |
|---|---|---|---|
| Backend (Python) | `backend/requirements.in` (ranges) | `backend/requirements.txt` — 49 packages pinned with their file hashes | `pip install --require-hashes`: a version or a file that does not match the lock **fails the build** |
| Backend tests | `backend/requirements-dev.in` | `backend/requirements-dev.txt` (56 packages) | same |
| Frontend (JavaScript) | `frontend/package.json` | `frontend/package-lock.json` | `npm ci` |

To change a dependency, edit the `.in` file and run `make lock`; review the diff, then rebuild. `make lock-upgrade`
takes the newest versions the ranges allow. Resolution runs in `python:3.12-slim`, the image the backend is built
from, so the versions and the environment markers match the runtime.

What it runs, and what makes it fail:

| Check | Tool | Fails the scan on |
|---|---|---|
| Python dependencies (`backend/requirements.txt` and `requirements-dev.txt`, pinned) | `pip-audit`, `trivy fs` | any known vulnerability |
| JavaScript dependencies (`frontend/package-lock.json`) | `npm audit --audit-level=high` | high or critical |
| Images built here (backend, frontend): OS and language packages, embedded secrets, misconfiguration | `trivy image` | any **critical**, or a **high that has a fix** |
| Dockerfiles | `trivy config` | high or critical misconfiguration |
| Third-party images (PostgreSQL) | `trivy image` | never: reported only, their content belongs to their publisher |

A vulnerability without a fix upstream does not fail the scan: it would block every build without anything to
apply. Rebuilding with `make build` (which passes `--pull`) is what applies the fixes: both images install the
security updates of their base image at build time, so the result does not depend on when the base tag was last
rebuilt.

#### Last scan

| Target | Result | Date |
|---|---|---|
| `backend/requirements.txt` (49 pinned) — pip-audit, Trivy | No known vulnerabilities | 2026-10-21 |
| `backend/requirements-dev.txt` (56 pinned) — pip-audit, Trivy | No known vulnerabilities | 2026-10-21 |
| `frontend/package-lock.json` — npm audit | 0 vulnerabilities | 2026-10-21 |
| Dockerfiles — Trivy 0.76.0 `config` | 0 misconfigurations | 2026-10-21 |
| `refexposer-backend` image (`sha256:9ac6d97a…`, Debian 13.7, 263 OS packages) | **0 critical, 0 fixable high** (49 high / 84 medium / 126 low without a fix upstream), 0 secrets | 2026-10-21 |
| `refexposer-frontend` image (`sha256:f241d20b…`, Alpine 3.24.2) | **0 vulnerabilities**, 0 secrets | 2026-10-21 |
| `postgres:17-alpine` (third party, `sha256:b0f9560a…`) | 0 in the Alpine packages; 1 critical + 25 high in the bundled `gosu` binary (Go standard library 1.24.6) | 2026-10-21 |

The `gosu` findings come from the official PostgreSQL image: the binary is statically linked against an older Go
standard library and only runs once, at container start, to drop privileges. There is nothing to patch on this
side; it goes away when the publisher rebuilds the image. The Debian findings of the backend image are packages
Debian marks *no fix available* (`fix_deferred` / `affected`), mostly in libraries the application never calls.

Base images, pinned by tag and recorded by digest at the last scan:

| Image | Digest |
|---|---|
| `python:3.12-slim` | `sha256:a6e34c598f24…` |
| `nginxinc/nginx-unprivileged:1.31-alpine` | `sha256:b5047b9b7684…` |
| `postgres:17-alpine` | `sha256:b0f9560a2de0…` |
| `node:22-alpine` (build stage only) | `sha256:0a7108bf6c7b…` |

#### Not covered yet

- **Pinning by digest** and an update bot (Renovate, Dependabot, or a periodic review offline): pinning without
  one freezes the vulnerabilities in place, so the two go together.
- **Signed images**: `cosign` signatures and the SBOM attached as an attestation, verified at deployment.
- **CIS benchmark of the base OS** (`oscap-podman` / `oscap-docker`) and a **DAST** pass (OWASP ZAP baseline)
  against the running stack: not run.
- **Vendored code**: none in this repository — every third-party library is a declared dependency, fetched at
  build time and pinned (see [NOTICE](NOTICE)). A vendored copy would be invisible to the dependency scan, so
  any that appears later is listed there and tracked by hand.

### Code scanning (CodeQL)

GitHub code scanning runs on `main`. Three kinds of finding come out of it, and they are handled differently.

**Fixed in the code**

| Alert | What was done |
|---|---|
| `py/clear-text-logging-sensitive-data` | A settings change is written to the audit log and to syslog through `appsettings.audit_safe()`: the encrypted fields are replaced by `***`, and so are the **values** of the free-form header maps — a gateway key routinely lives in a header. The names are kept, the values never leave the database. |
| `py/polynomial-redos` | `EMAIL_RE` was quadratic on input like `!@!.!.!.…` because the dot was inside the character classes. Rewritten so each part matches in exactly one way, with bounded label lengths, and the recipient of a test e-mail is capped at 320 characters. |
| `py/insecure-protocol` | `ssl.create_default_context()` already refuses TLS 1.0 and 1.1 on this runtime; `minimum_version = TLSv1_2` is now set explicitly, in the certificate-expiry task and in the outgoing HTTP client, so it does not depend on a default. |
| `py/reflective-xss` | The documentation page accepted any identifier `str.isalnum()` liked, which includes letters of other scripts. It is now an ASCII pattern, URL-encoded into the specification link and HTML-escaped into the title: nothing from the query string can become markup. |
| `py/url-redirection` | `oidc.safe_next()` parses the path instead of pattern-matching it and rebuilds it from its parts, so a scheme or a host anywhere in it is dropped. |
| `py/incomplete-url-substring-sanitization` (×2) | Test-only: an assertion searched for a URL inside an exported CSV. It now compares the first column of each line, which is what the test actually means. |

**Hardened, but the alert stands by design** — `py/full-ssrf` on `POST /api/admin/settings/proxy/test` and on the
federation catalog fetch. Both exist in order to call an address an administrator typed; no allowlist can make
that go away, and an administrator can already point a referential source anywhere. `network.validate_outbound()`
now removes what turns such a request into an escalation: http(s) only, no credentials in the address, and any
host resolving to a **link-local address refused** — the cloud metadata services among them, which answer with
the credentials of the instance. Private and loopback addresses stay allowed: an inference server, a syslog
collector or a peer on the internal network is the normal case. Resolution is opportunistic, because a name
reached through a proxy is resolved by the proxy; this is therefore **not** a defence against DNS rebinding.

**Accepted** — `py/stack-trace-exposure` on the syslog test. The endpoint is admin-only and its whole purpose is
to report why a collector cannot be reached, step by step; the text comes from `str(exception)`, not from a
traceback. Dismiss it as intended behaviour rather than hiding the message.

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
| `REFEX_DUCKDB_MEMORY_LIMIT` | automatic | memory of DuckDB, e.g. `8GB`. Empty: each update gets 60 % of the container memory shared between `REFEX_MAX_CONCURRENT_JOBS`, the query engine 25 %; beyond it, DuckDB works on disk |
| `REFEX_DUCKDB_THREADS` | automatic | threads of DuckDB. Empty: half of the container processors for an update |
| `REFEX_DOWNLOAD_RETRIES` | `5` | new attempts of a download when the remote site fails (see [Downloads and retries](#downloads-and-retries)) |
| `REFEX_MIN_FREE_DISK` | `5GB` | disk space an update never uses (download, decompression, DuckDB work files): it stops with a clear error instead of filling the disk shared with the database |
| `REFEX_KEEP_RAW_MAX` | `10GB` | source files above this size are not kept after the build when they are not the published file itself (decompressed, rewritten); `0`: no limit |
| `REFEX_FULL_SCAN_MAX` | `20GB` | above this published size, a filter or a lookup on a column neither sorted nor indexed is refused at once with what to configure, instead of reading the whole file until `REFEX_QUERY_TIMEOUT`; `0`: never |
| `REFEX_CPUS`, `REFEX_MEMORY` | `0` (no limit) | processors and memory of the backend container (docker compose), e.g. `6` and `16g` |
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

# Backend locally (Python 3.12+); --require-hashes installs exactly the locked versions
cd backend && pip install --require-hashes -r requirements-dev.txt

# Add or change a dependency: edit requirements.in (or requirements-dev.in), then recompile the lock
make lock            # or: sh scripts/lock.sh   ·   make lock-upgrade to take the newest allowed versions
REFEX_DATA_DIR=../data REFEX_CONFIG_DIR=../config REFEX_ADMIN_PASSWORD='Dev-Passw0rd!' uvicorn app.main:app --reload

# New migration after changing app/models.py
cd backend && alembic revision --autogenerate -m "description"

# Frontend locally (Node 22+), proxied to http://localhost:8000
cd frontend && npm install && npm run dev
```

## Contributing

Issues and pull requests are welcome. [CONTRIBUTING.md](CONTRIBUTING.md) says what you need to run the
project, what a change is expected to look like, and what CI checks. Everyone taking part follows the
[Code of Conduct](CODE_OF_CONDUCT.md). Found a vulnerability? [SECURITY.md](SECURITY.md) — please do not open
a public issue.

Changes worth knowing about are in [CHANGELOG.md](CHANGELOG.md).

## Licence

RefExposer is released under the **[Apache License 2.0](LICENSE)**: use it, run it, change it, redistribute
it, build something commercial on it. Keep the licence and the [NOTICE](NOTICE) with it, and say which files
you changed. There is no warranty of any kind.

Third-party code copied into this repository is listed in [NOTICE](NOTICE); everything else is a declared
dependency with its own licence, pinned in `backend/requirements.txt` and `frontend/package-lock.json`.

Source files carry no licence header — `LICENSE` covers the whole repository.

## Ideas for later

- SAML, single logout (RP-initiated logout) with Keycloak
- Notify administrators (e-mail, Teams) of new access requests
- Row-level rights inside a referential (columns are covered by the masking of personal data)
- Prometheus metrics
- Applications doing this from inside other tools (SIEM, orchestrators, threat-intelligence platforms):
  written and built, documentation and release to come
