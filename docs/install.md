# Getting started

[← Documentation](README.md) · [RefExposer](../README.md)

## 1. Requirements

- **Docker Engine 24+** with **Docker Compose v2.24+** (`docker compose version`), or **Docker Desktop** on Windows or macOS.
  On a Linux server, **Podman 5+** without root works too, with systemd units: see [Run with Podman (Quadlet, rootless)](deployment.md#run-with-podman-quadlet-rootless).
- About 2 GB of RAM and a few GB of disk. The space depends on the referentials: each one is stored as compressed Parquet.
- Outgoing HTTPS access to the sources (directly or through a proxy, see [Configuration](configuration.md#configuration-environment-variables)).

## 2. Get the project

```bash
git clone <repository url> refexposer
cd refexposer
```

## 3. Folders and permissions

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

## 4. Configuration

```bash
cp .env.example .env
chmod 600 .env
```

Edit `.env` and set at least:

| Variable | Value |
|---|---|
| `REFEX_PUBLIC_URL` | address typed by the users, e.g. `http://localhost:8080` or `https://refexposer.my-company.com` (see [Public URL](deployment.md#public-url)) |
| `POSTGRES_PASSWORD` | database password: `openssl rand -hex 24` (letters and digits only) |
| `REFEX_SECRET_KEY` | encryption key of the secrets and confidential data: `openssl rand -base64 48`. **Keep a copy outside the server.** |
| `REFEX_ADMIN_USERNAME` / `REFEX_ADMIN_PASSWORD` | initial administrator; the password must be changed at first sign-in |

Without a `.env`, the defaults of `docker-compose.yml` apply: `admin` / `ChangeMe-Now-2026!`, and a secret key generated in `data/.secret_key`. This is fine for a test, never for real data.

`POSTGRES_PASSWORD` and the initial administrator are only used at the **first** start: change them before it.

## 5. Start

```bash
docker compose up -d --build
docker compose ps
```

The first build takes a few minutes. `docker compose ps` should then show:

- `db`, `backend` and `frontend` **healthy**;
- `init-permissions` **Exited (0)**, which is normal: it only runs at startup.

The database schema is created automatically. If the backend does not start, read `docker compose logs backend`: the problems found at startup (folder not writable, secret key too short…) are explained there with the fix.

## 6. First sign-in

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

## 7. First referential

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

**On a server** (domain name, HTTPS, security headers, rate limiting): see [Expose behind an nginx reverse proxy](deployment.md#expose-behind-an-nginx-reverse-proxy).

## Stop, update, back up

- **Stop**: `docker compose down`. The referentials stay in `./data`, the database in the `db-data` Docker volume. `docker compose down -v` **deletes the database**.
- **Update**: `git pull`, then `docker compose up -d --build`. Database migrations and folder permissions are handled at startup, including when coming from a version whose containers ran as root.
- **Back up**: the `db-data` volume (`docker compose exec -T db pg_dump -U refexposer refexposer > refexposer.sql`), the `data/` folder, and `REFEX_SECRET_KEY` kept apart. The configuration is also backed up daily in `data/.backups` (see [System tasks](operations.md#system-tasks)).
- **Check the dependencies and the images**: `make scan` (or `sh scripts/scan.sh`). Rebuilding with `--pull` applies the security updates of the base images. See [Supply chain](security.md#supply-chain).

## Building from company repositories

The build reaches Docker Hub, PyPI, the npm registry and the Debian / Alpine mirrors. Each of those is a
**build argument with a public default**, so pointing them at your own repositories changes nothing else —
same `Dockerfile`, same lock files, same hashes:

```bash
cp enterprise/.env.example .env.build        # your registry, index, npm registry, mirrors
docker compose --env-file .env.build build --pull
```

[`enterprise/`](../enterprise/README.md) has the whole method: what you need, what to set, how to mirror the four
base images, TLS interception and the company authority, and a build with **nothing reachable at all** (the
wheels, the DuckDB extensions and the frontend assets brought in, then `docker build --network none`).

## Containers

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
