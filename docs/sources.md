# Special and large sources

[← Documentation](README.md) · [RefExposer](../README.md)

## Bloom filters (e.g. CIRCL hashlookup)

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

### Incremental updates (deltas)

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

## Git repositories (GitHub, GitLab, Gitea…)

A referential can come from **files of a Git repository**. It works with GitHub, GitLab, Gitea / Forgejo, Bitbucket or any Git server over HTTPS. In the editor, choose **Git repository** as the source. Give:

- the URL of the repository;
- the files to read (a glob relative to the repository root);
- optionally a branch, tag or commit (the default branch when empty);
- for a private repository, an access token.

- **Only what is needed is downloaded**: one commit (`--depth 1`), the matching files only (sparse, partial fetch). A large repository costs little.
- **No useless update**: `git ls-remote` runs first. While the branch or tag still points to the published commit, nothing is fetched and the run ends as *unchanged*. The published commit is shown in the sources of the referential.
- **Private repositories**: the token is sent as HTTP Basic credentials, with the user name `oauth2` by default (accepted by GitHub, GitLab and Gitea; it can be changed). The token never goes in the URL nor on a command line, is masked in every message, and is not returned by the API. Keep it out of the definition with a secret of the [secret manager](operations.md#secret-manager) (`token: "${secret:gitlab-token}"`) or a `${REFEX_SOURCE_…}` variable of `.env`.

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

## MaxMind DB / GeoIP (GeoLite2, GeoIP2, DB-IP, IPinfo…)

The `mmdb` format handles databases in the **MaxMind DB** format (`.mmdb`): GeoLite2 / GeoIP2 City, Country and ASN, and the `.mmdb` editions of DB-IP or IPinfo. They are published **as is**, so tools can download the raw file and use it directly. RefExposer also answers IP lookups.

- **Raw file for tools**: Logstash, Suricata, Zeek, Graylog, nginx `geoip2`, Fluentd… download it at a stable URL, `GET /api/referentials/<id>/raw`:
  - the file keeps its original name (`GeoLite2-City.mmdb`);
  - `ETag` / `If-None-Match` and `Last-Modified` / `If-Modified-Since` answer `304` when nothing changed, and `HEAD` returns the headers only;
  - authentication uses an API token (preferably from a [service account](users-and-rights.md#service-accounts)), either `Authorization: Bearer` or HTTP Basic with the token as password, for tools that only know `user:password`.
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

## SQLite databases (NIST NSRL) and SQL deltas

The `sqlite` format reads one **table or view** of a SQLite database (`options.table`), possibly inside an archive with other files (readme, schema…), which are ignored. The database is attached read-only, so views keep their column types. The usual SQL transformation applies on top (`{source}` is the chosen table).

### NIST NSRL: nothing to maintain

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

### Other SQLite databases with SQL deltas

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

## Large volumes (passive DNS, hundreds of millions of rows and more)

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
- An **interrupted download is resumed** where it stopped (HTTP `Range` + `If-Range`), within the run (see [Downloads and retries](referentials.md#downloads-and-retries)), then by the next run. When the source changed meanwhile, it is downloaded again from the start.
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
