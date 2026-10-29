# Search, downloads and the API

[← Documentation](README.md) · [RefExposer](../README.md)

## Searching a referential

The **Search** tab of each referential has two modes:

- **Multi-criteria search**: full-text search (terms highlighted in the results) combined with facets computed automatically.
  - Columns with few distinct values become checkboxes, with the number of results for each value.
  - Numeric and date columns become ranges.
  - Counters are recomputed at each selection. The search can be shared by link, exported or opened in the explorer.
- **Search by list of values**: paste up to 10,000 values (identifiers, codes…) on the key or another column.
  - The result separates the values found (with their details) from the missing ones.
  - It can be exported to CSV.

The **Global search** (top bar, `Ctrl+K`) queries every accessible referential at once and lists the matches.
[Investigate](investigations.md) is a superset of it: the same text search, plus exact lookups when the value
is a recognised identifier (found whatever each source calls its column), plus the graph of what the matching
rows lead to.

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
Internal referentials: see [Internal referentials](internal-and-unified.md#internal-referentials).

### SQL console security

SQL queries run in an isolated DuckDB connection, specific to the rights of the user:

- a single statement, `SELECT` only (`WITH`, `PIVOT`, `DESCRIBE`, `SUMMARIZE` accepted);
- only the tables of the accessible referentials exist, and only their Parquet files can be read;
- locked configuration (`enable_external_access=false`, `lock_configuration`);
- maximum duration (`REFEX_SQL_TIMEOUT`) and maximum number of returned rows (`REFEX_SQL_MAX_ROWS`);
- every query is recorded in the audit log;
- *Save as referential* goes through the same sandbox before anything is stored, and the stored query reads only the referentials the account may read.

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
