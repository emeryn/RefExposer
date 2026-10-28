# Configuration (environment variables)

[← Documentation](README.md) · [RefExposer](../README.md)

Put them in a `.env` file at the root (see `.env.example`):

| Variable | Default | Role |
|---|---|---|
| `REFEX_PUBLIC_URL` | `http://localhost:8080` | URL used to reach the application (see [Public URL](deployment.md#public-url)) |
| `FRONTEND_PORT` | `8080` | published port of the interface; `127.0.0.1:8080` to accept the local reverse proxy only |
| `POSTGRES_PASSWORD` | `refexposer` | database password (avoid `@ : / ? #`) |
| `REFEX_DATABASE_URL` | compose PostgreSQL | SQLAlchemy URL; without a value outside compose: SQLite in `data/refexposer.db` |
| `REFEX_ADMIN_USERNAME` | `admin` | initial administrator |
| `REFEX_ADMIN_PASSWORD` | `ChangeMe-Now-2026!` | its password (empty = generated and printed in the logs) |
| `REFEX_ADMIN_EMAIL` | | its e-mail |
| `REFEX_ADMIN_MUST_CHANGE_PASSWORD` | `true` | requires a change at first sign-in |
| `REFEX_ADMIN_RESET_PASSWORD` | `false` | recovery: resets this account at startup |
| `REFEX_LOCAL_LOGIN` | `true` | `false`: no username / password sign-in for local accounts in the interface (single sign-on only, see [Single sign-on only](users-and-rights.md#single-sign-on-only)) |
| `REFEX_UID` / `REFEX_GID` | `10001` | account running the backend, set when the image is built (owner of `./data` and `./import`, see [Containers](install.md#containers)) |
| `REFEX_SESSION_TTL_HOURS` | `12` | inactivity before a session expires |
| `REFEX_COOKIE_SECURE` | `false` | forces `Secure` cookies (automatic when `REFEX_PUBLIC_URL` is https) |
| `REFEX_PASSWORD_MIN_LENGTH` | `12` | minimum password length |
| `REFEX_LOGIN_MAX_ATTEMPTS` / `REFEX_LOGIN_LOCKOUT_MINUTES` | `10` / `15` | lockout after failures |
| `REFEX_API_TOKEN_MAX_DAYS` | unlimited | maximum lifetime of API tokens |
| `REFEX_ARGON2_TIME_COST` / `_MEMORY_KIB` / `_PARALLELISM` | `3` / `65536` / `4` | Argon2id parameters |
| `REFEX_SECRET_KEY` | generated in `data/.secret_key` | encryption key of the secrets stored in the database (LDAP, OIDC, proxy), 32 characters minimum |
| `REFEX_GIT_PROTOCOLS` | `https` | transports allowed for Git sources (comma separated; `https,http` for an internal server without TLS) |
| `REFEX_GIT_TIMEOUT` | `900` | maximum duration of a Git fetch (s) |
| `REFEX_LOG_LEVEL` | `info` | container logs: `trace`, `debug`, `info`, `warning`, `error`, `critical` (see [Logging](operations.md#logging)) |
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
| `REFEX_DOWNLOAD_RETRIES` | `5` | new attempts of a download when the remote site fails (see [Downloads and retries](referentials.md#downloads-and-retries)) |
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
