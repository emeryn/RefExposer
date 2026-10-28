# Internal and unified referentials

[← Documentation](README.md) · [RefExposer](../README.md)

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
[`examples/scripts/refex-internal.py`](../examples/scripts/refex-internal.py) and its
[README](../examples/scripts/README.md) — the same commands are in the application help, under *Internal referentials*.

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

**Save as referential**, next to the exports of the [SQL console](api.md#sql-console-security), turns the query that has
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
