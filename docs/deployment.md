# Deployment

[← Documentation](README.md) · [RefExposer](../README.md)

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

*Coming from Docker on the same server?* Copy `data/` and `config/`, then give them to the account: `sudo chown -R refex: ~/refexposer`. Move the database with a `pg_dump` (see [Stop, update, back up](install.md#stop-update-back-up)).

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

Every other variable of [Configuration](configuration.md#configuration-environment-variables) can be added to `refexposer.env`. Keep a copy of `REFEX_SECRET_KEY` outside the server.

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
- `Tmpfs=/tmp` is **required** on the frontend, not merely hardening: the container runs read-only and writes
  its generated nginx configuration there (see *When the backend restarts* below).
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

## When the backend restarts

An update, a crash, a rebooted machine: the backend goes away for a while and has to come back **without
anybody pressing F5** in the open tabs. Two things make that work, and both matter.

**The frontend container re-resolves the address of the backend.** `nginx` resolves a name in `proxy_pass`
once, when it loads its configuration, and keeps the answer for as long as it runs. A backend container that
is recreated often comes back on another address — and `nginx` would then answer `502` to everything until
*it* were restarted too, which no amount of retrying in the browser can get past. So the address is resolved
per request instead, against the DNS of the container runtime:

- the configuration is generated when the container starts, from `/etc/nginx/templates/default.conf.template`
  into **`/tmp/nginx-conf`**, because the container runs with a read-only root filesystem and `/tmp` is its
  one writable place. A deployment that drops `Tmpfs=/tmp` (Podman) or the `tmpfs` entry (Compose) leaves
  nginx with no site at all;
- the nameservers come from the container's own `/etc/resolv.conf`, so nothing names an address: Docker's
  embedded DNS and rootless Podman's `aardvark-dns` both work as they are;
- `REFEX_BACKEND` (default `backend:8000`) says where the backend is, should it not be a container of the
  same stack.

**The interface reconnects on its own.** A request that gets no answer — or a `502`/`503`/`504` carrying no
JSON, which is the proxy answering for a backend that is not there — switches the interface to *not
answering*: it says so over the pages, keeps showing what was last read rather than emptying itself, and
polls the public `GET /api/health` with a backoff. When that answers, everything on screen is refetched and
the session carries on. Nothing is lost, and nobody has to reload. A session that **expired** during the
outage is a different thing and is treated as one: the sign-in page comes back saying so, and signing in
returns to the page that was open.

What to check when a tab stays on *the server is not answering*:

```bash
curl -fsS http://127.0.0.1:8080/api/health          # through the frontend: {"status":"ok"}
docker compose ps                                   # or: systemctl --user status refexposer-backend
docker compose logs --tail 50 backend
```

A `502` from the first command with a healthy backend means the frontend container is older than this
mechanism: recreate it (`docker compose up -d --force-recreate frontend`).

## Public URL

`REFEX_PUBLIC_URL` (e.g. `https://refexposer.my-company.com`) is the address users and tools use to reach RefExposer. It is set once and used for:

- the **OpenID Connect callback** (`<public URL>/api/auth/oidc/callback`, to declare in Keycloak);
- the **generated API documentation** (`servers` of the OpenAPI specifications, Swagger “Try it out”);
- the **URLs and commands shown in the interface** (download links, `curl` examples, help pages);
- **`Secure` cookies** when it starts with `https://`;
- the **domain of the security keys and passkeys** (WebAuthn): they only work at this address.

On a server, it is the HTTPS address of the reverse proxy (see [Expose behind an nginx reverse proxy](#expose-behind-an-nginx-reverse-proxy)).

When it is empty, the address is derived from each request.
