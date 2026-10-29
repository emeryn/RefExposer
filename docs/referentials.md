# Declaring a referential

[← Documentation](README.md) · [RefExposer](../README.md)

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

Credentials of a private repository or folder must be `${secret:…}` references (see [Secret manager](operations.md#secret-manager)), because they are copied into every definition. API: `POST /api/admin/discovery/scan`, then `POST /api/admin/discovery/apply`.

### Export and import (from one environment to another)

**Administration › Referentials › Export** downloads the configuration of the referentials to set up another instance (test, then production; a new server):

- **Content**: the definitions (created in the interface, internal referentials without their rows, YAML files of `config/`; the referentials of the sync folder are left out), the **rights** given to groups (by name) and users (by user name), the groups they use, and the **secrets they need: names, descriptions and allowed hosts, never the values**.
- **Literal credentials** still written in definitions are masked by default. *Included in clear* puts them in the file: keep such a file like a password, or better, move them to the secret manager.
- **Format**: YAML (or JSON) in the format of the files of `config/`. The file can also be dropped into `config/` as is: the extra keys are ignored.

**Import** (same page) works in two steps:

1. **Analyse**: nothing changes yet. For each referential, the plan says *create*, *update* (with the changed fields), *unchanged*, *skip* (already defined, when keeping the existing ones), *conflict* (defined in a YAML file or the sync folder of this instance) or *error* (invalid definition, unknown secret, masked credential with nothing to keep). The secrets to create first and the missing groups are listed.
2. **Import** the selected referentials, with their rights. Missing groups are created on request (add their members afterwards, or let LDAP / OpenID Connect synchronize them), and the imports can be started at once. A masked credential keeps the value already stored on the target.

API: `GET /api/admin/config/export?ids=&grants=&credentials=masked|included&format=yaml|json`, `POST /api/admin/config/import` (`apply: false` for the plan). MCP tools: `export_configuration`, `import_configuration`.

**Ready-made example**: [`examples/cybref/cybref-referentials.yaml`](../examples/cybref/cybref-referentials.yaml) imports 124 cybersecurity referentials of [CybrefV2](https://github.com/emeryn/CybrefV2) (threat intelligence, vulnerabilities, ATT&CK, IP ranges, registries…), with keys, quality rules and merged rolling feeds. See its [README](../examples/cybref/README.md).

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
    entities:                       # relational search: corrects what a column holds (investigations.md).
      cweID: cwe                    # Recognised from the values on its own; declare one only for a column a
      notes: ""                     # pattern gets wrong, or "" to keep a column out of it entirely.
    schedule: "15 */4 * * *"        # cron (REFEX_TIMEZONE time zone); absent = manual
    max_age: 1d                     # staleness threshold (30m, 12h, 2d, 1w)
    enabled: true
    downloads: [csv.gz, xlsx]       # downloads pre-generated after each update
    validation:
      min_rows: 1000                # minimum number of rows expected
      max_drop_pct: 10              # rejects a version that loses more than 10% of its rows
      unique_key: true              # rejects a version whose key is not unique
```

### Local referentials

With `source.type: local`, put the files in `data/<id>/` (e.g. `data/my-ref/raw/export.csv`) and give the pattern in `path`. The scheduler detects changes from the file fingerprints, so another tool can feed RefExposer by simply writing files.

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

## Manual imports

Sometimes the remote source is unreachable, wrong, or the file arrives another way. Users with the **manage** right can then provide the new version themselves; the import is clearly shown to every user (banner on the referential, *manual import* badge, trigger in the history).

- **Upload**: the **Import** button of the referential page accepts one or more files (compressed `.gz`/`.zip` accepted). For a referential combining several URLs, send one file per source, named like the remote file.
- **Import folder**: files copied into `import/<referential id>/` (mounted as `/import` in the backend, `REFEX_IMPORT_DIR`) are imported automatically once their copy is finished (checked every `REFEX_IMPORT_POLL_SECONDS` seconds). They then move to `.done/<date>/`, or to `.failed/<date>/` with an `ERROR.txt` file explaining the refusal. Each referential folder contains a `README.txt`; unknown folders are listed on the System page. For large files, copy under a temporary `*.part` name, then rename.
- **Same checks**: manual files go through the same pipeline as a download (corrupted source checks, format, validation rules). If they are refused, the current version is kept.
- **Freeze**: ticking **Freeze this version** when uploading suspends the automatic updates until someone clicks **Resume automatic updates** (`DELETE /api/referentials/{id}/pin`). Without it, the next scheduled update replaces the manual version with the remote source again. A referential can also be frozen at any time, without importing anything, from the menu beside **Update** — see [Freezing a referential](data-governance.md#freezing-a-referential).

```bash
curl -X POST -H "$H" -F files=@known_exploited_vulnerabilities.csv -F pin=true \
  "http://localhost:8080/api/referentials/cisa-kev/import"
```

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

## Downloads and retries

A remote site that fails does not end the update at once. As `curl --retry` does, the download is tried again on a connection error, a timeout, a connection cut, or an HTTP `408`, `425`, `429`, `500`, `502`, `503` or `504` answer. It waits 1, 2, 4… seconds (up to 60 s), or the time asked by the server (`Retry-After`, up to 5 min). Other errors (`401`, `403`, `404`…) are not tried again. A transfer whose response carries a strong validator (`ETag` or date) **is resumed where it stopped** (HTTP `Range`), otherwise it starts again from zero. **Only the failures in a row count**: an attempt that brought data (a cut after 14 GB, as HuggingFace does on long transfers) starts the count again, so a large file always ends up downloaded. Each attempt is in the run log (*Download of … failed (HTTP 503): started again in 2s, attempt 3/6*).

5 new attempts by default (`REFEX_DOWNLOAD_RETRIES`), or per source:

```yaml
source:
  urls: [https://flaky.example.org/feed.csv]
  retries: 10                       # 0: no new attempt
```

In the interface: **Source › Advanced options › New attempts when the site fails**.
