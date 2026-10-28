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
