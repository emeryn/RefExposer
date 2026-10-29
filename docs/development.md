# Development and contributing

[← Documentation](README.md) · [RefExposer](../README.md)

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

Issues and pull requests are welcome. [CONTRIBUTING.md](../CONTRIBUTING.md) says what you need to run the
project, what a change is expected to look like, and what CI checks. Everyone taking part follows the
[Code of Conduct](../CODE_OF_CONDUCT.md). Found a vulnerability? [SECURITY.md](../SECURITY.md) — please do not open
a public issue.

Changes worth knowing about are in [CHANGELOG.md](../CHANGELOG.md).

## Branches, images and releasing

Two branches, and the images follow them:

| What you do | What CI does | What is published |
|---|---|---|
| open a pull request | the whole CI | nothing |
| push to `dev` | the whole CI, then the images | `dev`, and `dev-<sha>` to come back to one precise build |
| merge into `main` | the whole CI | **nothing** — a release is a deliberate act |
| push a tag `v1.2.0` | the whole CI, then the images | `1.2.0`, `1.2` and `latest` |

Each image goes to **two registries from one build**, so both carry the same digest:

- **Docker Hub** — `emeryn/refexposer-backend`, `emeryn/refexposer-frontend`, the names
  [the deployment](deployment.md) already expects;
- **GitHub Packages** — `ghcr.io/emeryn/refexposer-backend`, `ghcr.io/emeryn/refexposer-frontend`, next to
  the code. Nothing to configure: the run's own token pushes there, with `packages: write`.

Either one can be pulled; they are the same images. A GitHub package is **private when Actions first creates
it**, so each of the two has to be made public once, in its *Package settings*.

`dev` moves under your feet, so a deployment that has to stay put pins `dev-<sha>`. A pre-release tag
(`v1.2.0-rc1`) publishes `1.2.0-rc1` and deliberately leaves `latest` alone.

### Releasing

There is **nothing to edit in the source**. The version the application reports is stamped into the image
from the tag (`REFEX_BUILD_VERSION`, read by `backend/app/__init__.py`), so the tag cannot disagree with what
`/api/system` answers:

```bash
# on dev: close the CHANGELOG — [Unreleased] becomes [1.2.0] with its date
git switch main && git merge --no-ff dev && git push     # CI only, no image
git tag -a v1.2.0 -m "1.2.0" && git push origin v1.2.0   # this publishes
```

A build that passes no version — a source checkout, `make build`, `docker compose build` — reports
`0.0.0+dev` rather than claiming a release it is not.

The two workflows: [`ci.yml`](../.github/workflows/ci.yml) holds the checks, and
[`publish.yml`](../.github/workflows/publish.yml) calls it as its gate before pushing anything, so the tests
guarding a release can never drift from the tests guarding a pull request. `dev` is absent from `ci.yml`'s own
triggers on purpose: `publish.yml` owns that branch, and triggering in both would make two runs share a
concurrency group and cancel each other.

Docker Hub needs `DOCKERHUB_USERNAME` (an Actions **variable**) and `DOCKERHUB_TOKEN` (an Actions **secret**,
a Docker Hub token with *Read & Write*). The username is better as a variable than a secret: as a secret it is
masked in the logs and every image name reads `***/refexposer-backend`. GitHub Packages needs nothing.

### The Docker Hub page of each image

A release also replaces the **description** of both Docker Hub repositories with `README.md`:
`scripts/dockerhub-description.sh <backend|frontend>` renders it with every relative link made absolute
(documents to `/blob/`, pictures to `raw.githubusercontent.com`, or they would lead nowhere off GitHub), under
a few lines saying which half of the pair the image is and how to pull it from either registry.

`--print` renders it to standard output and changes nothing, which is how to check it without credentials:

```bash
scripts/dockerhub-description.sh frontend --print | less
```

A description belongs to a repository rather than to a tag, so it is written on a release only. GitHub
Packages takes its page from the repository on its own, with nothing to push.

### Base image advisories

The two Dockerfiles apply the security updates of their base image at build time (`OS_UPDATE=1`, on by
default), because a base image tag is rebuilt far less often than advisories are published. A scanner
reporting a flaw in the **published** image is usually reporting an image built before the fix existed:
rebuilding is what fixes it, and `make scan` says whether it did. `enterprise/README.md` covers the opposite
case, a build with nothing to download.

## Licence

RefExposer is released under the **[Apache License 2.0](../LICENSE)**: use it, run it, change it, redistribute
it, build something commercial on it. Keep the licence and the [NOTICE](../NOTICE) with it, and say which files
you changed. There is no warranty of any kind.

Third-party code copied into this repository is listed in [NOTICE](../NOTICE); everything else is a declared
dependency with its own licence, pinned in `backend/requirements.txt` and `frontend/package-lock.json`.

Source files carry no licence header — `LICENSE` covers the whole repository.

## Ideas for later

- SAML, single logout (RP-initiated logout) with Keycloak
- Notify administrators (e-mail, Teams) of new access requests
- Row-level rights inside a referential (columns are covered by the masking of personal data)
- Prometheus metrics
- Applications doing this from inside other tools (SIEM, orchestrators, threat-intelligence platforms):
  written and built, documentation and release to come
