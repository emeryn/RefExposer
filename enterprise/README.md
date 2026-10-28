# Building RefExposer from company repositories

RefExposer builds from the public Internet by default: Docker Hub for the base images, PyPI for the Python
packages, the npm registry for the frontend, the Debian and Alpine mirrors for the security updates of the base
images. On a company network that is usually not allowed, and on a disconnected one it is not possible.

Everything the build reaches is a **build argument with a public default**. Point those arguments at your own
repositories and nothing else changes: same `Dockerfile`, same lock files, same hashes, same images.

```bash
cp enterprise/.env.example .env.build        # fill in your repository addresses
docker compose --env-file .env.build build   # or: make build
```

---

## 1. What you need

Four kinds of repository. Most companies already run all of them behind one Nexus or Artifactory.

| What | Why | Example of an internal address |
|---|---|---|
| **Container registry** (a Docker Hub proxy, or images you mirror yourself) | the four base images | `registry.example.com/dockerhub` |
| **Python index** (PyPI proxy) | the backend dependencies | `https://nexus.example.com/repository/pypi/simple` |
| **npm registry** (npmjs proxy) | the frontend dependencies | `https://nexus.example.com/repository/npm/` |
| **Debian and Alpine mirrors** | the security updates applied to the base images | `https://mirror.example.com/debian`, `https://mirror.example.com/alpine` |

The four base images to make available:

| Image | Used by |
|---|---|
| `python:3.12-slim` | backend (application and tests) |
| `node:22-alpine` | frontend build |
| `nginxinc/nginx-unprivileged:1.31-alpine-slim` | frontend, serving the built assets |
| `postgres:17-alpine` | the database at runtime (not a build dependency) |

`enterprise/mirror-images.sh` copies those four into your registry in one command — see
[§4](#4-mirror-the-base-images).

**Also worth knowing before you start:**

- **The versions are pinned and hash-checked.** `backend/requirements.txt` carries every version *and* every
  file hash (`--require-hashes`), and `frontend/package-lock.json` does the same for npm. Your index must serve
  **those exact files**: a proxy of the public repositories does, a curated index that only holds "approved"
  versions will fail the build rather than install something else. That is the intended behaviour — see
  [§7](#7-when-your-index-does-not-have-everything).
- **TLS interception**: if your repositories are behind a TLS-inspecting proxy, the cleanest fix is a base image
  that already trusts the company authority ([§5](#5-tls-interception-and-the-company-authority)).
- **The backend image needs `git`** (Git repository sources) and **three DuckDB extensions** (`excel`,
  `httpfs`, `sqlite`), which the build installs from `extensions.duckdb.org`. Both are covered below.
- **Nothing is downloaded at runtime.** The built images install nothing when they start: the DuckDB extensions
  are baked in, and the production image has no `pip` at all. The only outgoing connections a running instance
  makes are the referential sources you configure, and they go through the proxy set in *Administration ›
  Settings*.

---

## 2. What to change

Nothing in the `Dockerfile`s or in `docker-compose.yml`. Set the variables below — in a file you pass with
`--env-file`, or exported in the shell, or in your CI.

| Variable | Default | What it does |
|---|---|---|
| `PYTHON_IMAGE` | `python:3.12-slim` | base image of the backend |
| `NODE_IMAGE` | `node:22-alpine` | base image building the frontend |
| `NGINX_IMAGE` | `nginxinc/nginx-unprivileged:1.31-alpine-slim` | base image serving the frontend |
| `POSTGRES_IMAGE` | `postgres:17-alpine` | database image used at runtime |
| `PIP_INDEX_URL` | `https://pypi.org/simple` | Python index |
| `PIP_EXTRA_ARGS` | *(empty)* | anything else pip needs: `--trusted-host`, `--cert`, `--find-links`, `--no-index` |
| `NPM_REGISTRY` | `https://registry.npmjs.org/` | npm registry |
| `NPM_EXTRA_ARGS` | *(empty)* | anything else npm needs: `--ca`, `--strict-ssl=false` |
| `APT_MIRROR` | *(empty)* | replaces `deb.debian.org` and `security.debian.org` in the backend image |
| `APK_MIRROR` | *(empty)* | replaces `dl-cdn.alpinelinux.org` in the frontend image |
| `OS_UPDATE` | `1` | `0` skips the `apt`/`apk` step entirely: the base image already carries the security updates **and `git`** |
| `DUCKDB_EXTENSION_REPOSITORY` | *(empty)* | where the three DuckDB extensions are downloaded from, if you host them |
| `HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY` | *(empty)* | outgoing proxy **during the build**; they already existed |

`enterprise/.env.example` is that list, ready to fill in.

---

## 3. A complete example

A company running one Nexus at `nexus.example.com`, with a Docker Hub proxy on port 8443, reachable without a
proxy. Put this in `.env.build`:

```properties
PYTHON_IMAGE=nexus.example.com:8443/python:3.12-slim
NODE_IMAGE=nexus.example.com:8443/node:22-alpine
NGINX_IMAGE=nexus.example.com:8443/nginxinc/nginx-unprivileged:1.31-alpine-slim
POSTGRES_IMAGE=nexus.example.com:8443/postgres:17-alpine

PIP_INDEX_URL=https://nexus.example.com/repository/pypi-proxy/simple
NPM_REGISTRY=https://nexus.example.com/repository/npm-proxy/

APT_MIRROR=https://nexus.example.com/repository/debian
APK_MIRROR=https://nexus.example.com/repository/alpine
```

Then:

```bash
docker login nexus.example.com:8443                  # if the registry asks for it
docker compose --env-file .env.build build --pull
docker compose --env-file .env.build up -d
```

The `.env.build` file only drives the **build**. The runtime settings stay in `.env` (see the main
[Getting started](../docs/install.md#4-configuration)); `--env-file` can be given twice if you prefer to keep them apart:
`docker compose --env-file .env --env-file .env.build …`.

---

## 4. Mirror the base images

If you have no Docker Hub proxy, copy the four images into your registry once:

```bash
# Reads the image list from the compose file and the Dockerfiles, so it cannot drift from them
enterprise/mirror-images.sh --registry registry.example.com/refexposer --dry-run
enterprise/mirror-images.sh --registry registry.example.com/refexposer
```

It prints the `.env.build` lines to use afterwards. On a disconnected network, run it in two steps on either
side of the gap:

```bash
# On a machine with Internet access
enterprise/mirror-images.sh --save bundle/base-images.tar

# Transfer bundle/base-images.tar, then on the build machine
enterprise/mirror-images.sh --load bundle/base-images.tar --registry registry.example.com/refexposer
```

---

## 5. TLS interception and the company authority

If your repositories are reached through a proxy that re-signs TLS, the build has to trust the company
authority. Two ways, in order of preference:

**Publish base images that already trust it.** One `Dockerfile` per base image, built once, pushed to your
registry, and then every RefExposer build just works — pip, npm, apt and apk included.

```dockerfile
# registry.example.com/base/python:3.12-slim
FROM python:3.12-slim
COPY company-root-ca.crt /usr/local/share/ca-certificates/
RUN update-ca-certificates
ENV REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
```

**Or tell each tool where the certificate is**, if the certificate is already somewhere the build can read:

```properties
PIP_EXTRA_ARGS=--cert /etc/ssl/certs/company-root-ca.crt
NPM_EXTRA_ARGS=--ca-file /etc/ssl/certs/company-root-ca.crt
```

Do **not** reach for `--trusted-host` or `--strict-ssl=false` as a first move: they turn the check off instead
of pointing it at the right authority, and the hash check in `requirements.txt` is then your only guarantee
that what arrived is what was reviewed.

> The authority a *running* instance uses to reach its sources is a different setting: *Administration ›
> Settings › Network & proxy*, which also covers LDAPS, OpenID Connect and the inference server. Nothing of
> that belongs in the image.

---

## 6. Fully disconnected: nothing reachable at build time

When the build machine reaches neither the Internet nor an internal repository, four things have to come to it.
The sequence below is the one that was run to verify this folder; the resulting image builds with
`--network none` and works offline.

### On a machine that has access

```bash
enterprise/vendor-python.sh      # backend/wheels/            — the pinned wheels, hashes checked
enterprise/vendor-duckdb.sh      # backend/duckdb-extensions/ — excel, httpfs, sqlite
cd frontend && npm ci && npm run build && cd ..   # frontend/dist/ — the built assets
```

Both folders are part of the build context, empty in the repository and ignored by git; the build picks them
up on its own. You also need a **base image that carries `git` and the security updates**, because `apt` will
not be reachable either:

```dockerfile
# registry.example.com/refexposer/python:3.12-slim — built once, where apt works
FROM python:3.12-slim
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
# COPY company-root-ca.crt /usr/local/share/ca-certificates/ && update-ca-certificates   (see section 5)
```

Mirror that one and the nginx one with `enterprise/mirror-images.sh --save` / `--load`
([§4](#4-mirror-the-base-images)).

### Transfer, then build with no network

```bash
docker build --target runtime --network none \
  --build-arg PYTHON_IMAGE=registry.example.com/refexposer/python:3.12-slim \
  --build-arg OS_UPDATE=0 \
  --build-arg PIP_EXTRA_ARGS="--no-index --find-links /app/wheels" \
  -t refexposer-backend:latest ./backend

docker buildx build -f enterprise/frontend-prebuilt.Dockerfile --network none \
  --build-context dist=frontend/dist \
  --build-arg NGINX_IMAGE=registry.example.com/refexposer/nginxinc/nginx-unprivileged:1.31-alpine-slim \
  --build-arg OS_UPDATE=0 \
  -t refexposer-frontend:latest ./frontend
```

What each argument is for:

| Argument | Why |
|---|---|
| `OS_UPDATE=0` | skips `apt-get`/`apk`, which have no mirror to reach. The build then **fails on purpose** if the base image has no `git`, rather than producing an image that cannot read a Git source. |
| `PIP_EXTRA_ARGS=--no-index --find-links /app/wheels` | installs from the vendored wheels. The hashes of `requirements.txt` are still enforced: `vendor-python.sh` downloaded with `--require-hashes`, so nothing but the reviewed files is in there. |
| `--build-context dist=frontend/dist` | npm has no comfortable offline story, so the assets are built where a registry exists. `frontend-prebuilt.Dockerfile` is the serving half of the normal image — same nginx configuration, same unprivileged account, same health check. |

Both vendored folders are removed from the images after use, so they cost nothing in the result.

### Check it

```bash
docker run --rm --network none --entrypoint python refexposer-backend:latest -c \
  "import duckdb; c = duckdb.connect(); [c.execute(f'LOAD {e}') for e in ('excel','httpfs','sqlite')]; \
   import app.main; print('extensions loaded and the application imports, offline')"
docker run --rm --entrypoint sh refexposer-backend:latest -c "command -v git && (command -v pip || echo 'no pip: expected')"
```

## 7. When your index does not have everything

`--require-hashes` means the build installs **exactly** the files recorded in `backend/requirements.txt`, or
nothing. A curated index that holds different versions will stop the build with
`THESE PACKAGES DO NOT MATCH THE HASHES`. That is the guarantee working, not a bug.

Two honest ways out:

- **Have the index proxy the public one** for the packages RefExposer needs. The list is
  `backend/requirements.in` (21 direct dependencies) and what they pull in.
- **Re-lock against your index**: change `requirements.in` if a version has to differ, then
  `PIP_INDEX_URL=… make lock`. The hashes are recomputed from what *your* index serves, and the result is a
  lock file that belongs to your organisation. Keep it in your fork — and keep reading the upstream changes,
  because a pinned dependency that nobody updates is a frozen vulnerability.

The same applies to npm: `package-lock.json` is authoritative, and `npm ci` fails rather than resolving
something else.

---

## 8. Check what you built

```bash
# The pinned versions really are the ones in the image
docker run --rm --entrypoint python refexposer-backend:latest -c \
  "import importlib.metadata as m; print(m.version('fastapi'), m.version('duckdb'), m.version('sqlalchemy'))"

# Nothing installs anything at runtime: no package manager in the production image
docker run --rm --entrypoint sh refexposer-backend:latest -c \
  "command -v pip apt-get apk || echo 'no package manager: expected'"

# No secret, no .env and no base image from the Internet left in a layer
docker history --no-trunc refexposer-backend:latest | grep -iE "\.env|token|password" || echo clean

# Supply-chain scan and bill of materials of what you built
make scan
make sbom
```

The test suite runs against the image you built, offline:

```bash
make test
```

---

## 9. If something fails

| What you see | What it means |
|---|---|
| `failed to resolve source metadata for docker.io/library/python` | a `*_IMAGE` variable is still public, or the registry needs `docker login`. `docker compose config` prints the values actually used. |
| `THESE PACKAGES DO NOT MATCH THE HASHES` | your index serves other files than the lock file records. See [§7](#7-when-your-index-does-not-have-everything). |
| `CERTIFICATE_VERIFY_FAILED` during pip or npm | TLS interception without the company authority. See [§5](#5-tls-interception-and-the-company-authority). |
| `Could not fetch URL …: There was a problem confirming the ssl certificate` | same cause, pip's wording. |
| `npm ERR! code E401` / `E403` | the npm registry wants credentials: put them in `~/.npmrc` on the build machine and pass `NPM_EXTRA_ARGS=--userconfig /root/.npmrc`, or use a token in the registry URL. Never commit either. |
| `Temporary failure resolving 'deb.debian.org'` | `APT_MIRROR` is not set, or the proxy variables are missing from the build. With nothing to reach, use `OS_UPDATE=0` and a base image that carries the updates and `git`. |
| `git is missing: the base image must carry it when OS_UPDATE=0` | exactly that: add `git` to your base image. The build refuses rather than ship an image that cannot read a Git source. |
| `Failed to download extension "excel"` | the DuckDB extensions are fetched from `extensions.duckdb.org`. Run `enterprise/vendor-duckdb.sh` on a machine that reaches it, or set `DUCKDB_EXTENSION_REPOSITORY` to your own copy. |
| `ERROR: unable to select packages` (Alpine) | `APK_MIRROR` points at something that is not an Alpine mirror root (it must hold `v3.24/main`). |
| The build works, `docker compose up` pulls from Docker Hub | `POSTGRES_IMAGE` is a **runtime** image: set it in the environment you deploy with, not only in the build one. |
