# Operations

[← Documentation](README.md) · [RefExposer](../README.md)

## Secret manager

API keys, license keys, tokens and passwords of the sources are stored in **Administration › Secrets**, and the definitions only hold a reference:

```yaml
source:
  urls: [https://api.vendor.example/v2/feed.csv]
  headers:
    Authorization: "Bearer ${secret:vendor-api}"
# or: basic_auth: "123456:${secret:maxmind-key}"   ·   Git: token: "${secret:gitlab-token}"
```

- **Write-only**: values are encrypted at rest (AES-256-GCM, key derived from `REFEX_SECRET_KEY`, bound to the name of the secret) and never returned, neither by the interface nor by the API nor by the MCP server. The list shows the name, the description, the allowed hosts and the referentials using each secret.
- **Where**: header values, `basic_auth` and the Git `token`, the places that are never displayed. A reference in a URL or a path is refused. In the editor, the key button of these fields picks a secret.
- **Allowed hosts** (`api.vendor.example`, `*.vendor.example`): the secret is only sent to these hosts. A source pointed at another server (by mistake, or by an agent misled by a prompt) fails with an explanation and sends nothing. Restrict every secret that can be.
- **Checked**: a definition referencing an unknown secret is refused, and a secret still in use is only deleted when forced. A new value takes effect at the next update, without editing the definitions.
- **Literal credentials** still written in a definition are masked in the administration API (`********`). Sent back unchanged, a masked value keeps the stored one; a new value replaces it.
- `${REFEX_SOURCE_…}` environment variables remain supported. Secrets are part of the configuration backups, still encrypted: restore them with the same `REFEX_SECRET_KEY`.

## Branding

**Administration › Settings › Appearance** sets the **title** and **subtitle** shown in the header, on the sign-in page and in the browser tab, and the **company logo** (PNG, JPEG, WebP or SVG, 512 KB maximum) shown instead of the RefExposer icon. The logo is served at `/api/branding/logo` (public, as the sign-in page needs it; an SVG cannot run scripts).

## Synchronization between instances

A RefExposer instance can **replicate the referentials of another one** (a central instance feeding regional or isolated ones, production feeding pre-production…), with their definitions: name, description, category, key, search columns, **quality rules**, **SQL transformation**, indexes, generated downloads, confidentiality.

**On the instance that shares (the peer)**: create a **service account**, give it (or its group) the **read** right on the referentials to share, and create an API token for it. Nothing else: it exposes `GET /api/federation/catalog`, which lists what this account may read.

**On the replica**: **Administration › Synchronized instances › Add a peer**: URL of the peer, token, then **Test the connection** to choose the referentials (or all of them, including the ones shared later), the prefix of the local identifiers (`prod-` gives `prod-iocs`), the schedule and the groups given the read right on the replicas. Each synchronization:

1. reads the catalog of the peer and creates or updates the definition of each replica;
2. starts the update of the replicas whose published version changed over there. The replica downloads the **published file of the peer** (Parquet, Bloom filter or MaxMind DB) with a conditional request. The SQL transformation already ran upstream and stays visible on the replica, and the quality rules are checked again here.

- The token is kept in the **secret manager** (`peer-<name>`), restricted to the host of the peer: it is never sent elsewhere.
- Replicas are **read-only** here (their definition follows the peer); they show a *peer* badge.
- A referential **no longer shared** (removed, or right withdrawn) stays on the replica with its last data, marked *missing over there*. Removing the peer keeps the replicas (no longer updated), or deletes them with their data and the token.
- An identifier already used on the replica is reported as a conflict: give the peer a prefix.

```bash
curl -X PUT -H "$H" -H "Content-Type: application/json" "http://replica:8080/api/admin/federation/peers/prod" \
  -d '{"url": "https://refexposer.example.com", "token": "rfx_…", "prefix": "prod-", "schedule": "0 * * * *"}'
curl -X POST -H "$H" "http://replica:8080/api/admin/federation/peers/prod/sync"
```

## System tasks

**Administration › Tasks** lists the scheduled background tasks. For each task you can:

- enable or disable it;
- change its schedule (cron) and its parameters;
- run it now (**Run now**);
- see its last runs (status, duration, message, details).

Every run that does something is written to the audit log (`task.run`), so it is also forwarded to syslog when that is configured. Warnings and errors show up in the logs.

| Family | Task | Default |
|---|---|---|
| Maintenance | **Purge expired sessions** | hourly |
| | **Audit log retention**: deletes the entries older than N days (365) | disabled: check your obligations first |
| | **Disk cleanup**: leftover temporary files (uploads, previews, exports); folders of deleted referentials are reported, or deleted | daily |
| Integrity and monitoring | **Integrity check**: every published version is readable and matches what was published (size, row count, indexes, MaxMind DB, Bloom filters) | daily |
| | **Stale referentials**: data older than `max_age`, or last update failed | daily |
| | **Source availability**: the URLs of the HTTP sources answer, through the configured proxy | daily |
| | **TLS certificate expiry**: certificate of `REFEX_PUBLIC_URL` (or of a given host), warning N days before (30) | daily |
| Security policy | **Inactive accounts**: reported, or disabled, after N days without sign-in (90); never the last administrator, never service accounts | disabled |
| | **Expired API tokens**: deleted N days after their expiry (30) | daily |
| | **Pending access requests**: reminder in the audit log and syslog | weekdays |
| | **LDAP resynchronisation**: groups, name and e-mail of the LDAP accounts updated from the directory; accounts that left it are reported, or disabled | daily (when LDAP is enabled) |
| Backup | **Configuration backup**: definitions, accounts, rights, settings, internal rows (see below) | daily, 14 kept |
| | **Full backup**: the configuration **and** the data of `data/`, on the server and / or in an S3 bucket (see below) | disabled, weekly when enabled |
| | **Remote storage**: sends to the bucket what it is missing and frees the coldest local copies over the cache limit (see [Where a referential's data lives](referentials.md#where-a-referentials-data-lives)) | hourly (when remote storage is enabled) |
| | **Antivirus**: the local scanner answers, how old its signature database is, and the retention of the quarantine (see [Checking the files that come in](#checking-the-files-that-come-in)) | daily (when the antivirus scan is enabled) |
| Folder scans | **Import folder** and **sync folder** scans | `REFEX_IMPORT_POLL_SECONDS` / `REFEX_SYNC_POLL_SECONDS` |

### Configuration backups

The **Configuration backup** task writes a dated file to `data/.backups/`. It contains:

- the referential definitions;
- accounts and groups, rights, API tokens and security keys;
- settings and the rows of internal referentials;
- a copy of the YAML files of `config/`, for reference.

The file is **encrypted** with a key derived from `REFEX_SECRET_KEY`, so it can only be restored with the same key. From the **Tasks** page, a backup can be downloaded, deleted or **restored** (typing `RESTORE` confirms it); a downloaded backup can be restored too. A restore replaces the configuration in one transaction and signs everybody out. The published data, the history and the audit log are kept.

It does not replace the backup of the PostgreSQL volume and of `data/` — for that, see the **full backups** below.

### Full backups (configuration **and** data)

The **Full backup** task writes **everything** into one archive, in `data/.backups/full/`:

- every table of the database: accounts, groups, rights, API tokens, settings, referential definitions, the rows
  of internal referentials, saved searches, watchdogs and the audit log;
- the YAML definitions of `config/`;
- `data/`: the published versions (Parquet, Bloom filters, MaxMind DBs), their indexes, the metadata, the run
  history and the SQLite bases kept for deltas.

Left out on purpose: the temporary folders, the downloads generated from a published version (`exports/`,
rebuilt on demand), the previous backups, and the downloaded source files (`raw/`) unless you ask for them —
they come from their source again, and are routinely the largest thing on the disk.

> **The archive never carries the encryption key** (`data/.secret_key`, or `REFEX_SECRET_KEY`). An archive
> carrying its own key would protect nothing. Keep that key somewhere else: without it an encrypted archive
> cannot be read, and a restored instance cannot decrypt its secrets or its confidential referentials.

**How often** it runs is the schedule of the task, on the **Tasks** page — weekly by default, and the task is
**off until you enable it**, so you choose a cadence that suits the size of your instance. **Where** it goes and
how many copies are kept is **Settings › Backup**.

The archive is a streamed `tar.gz`, so an instance of several hundred GB is never held in memory, and it is
encrypted (AES-256-GCM) **chunk by chunk**: a truncated, reordered or altered archive is refused rather than
half-restored. The free space is watched while it is written.

#### Copy to an S3 bucket

A backup kept on the same disk as the data it protects is only half a backup. In **Settings › Backup**, turn on
the copy to an **S3 bucket** (Amazon S3 or any compatible store — see
[S3 buckets](sources.md#s3-buckets-amazon-s3-minio-ceph-scaleway-ovh)) and give the endpoint, the bucket, a
prefix and, unless the bucket accepts anonymous writes, an access key. Permissions needed on the prefix:
`s3:ListBucket`, `s3:PutObject` and `s3:DeleteObject` (to apply the retention), plus `s3:GetObject` to restore
from the bucket. A store with a certificate from an internal authority is trusted once its root certificate is
in *Settings › Network & proxy › TLS certificates*.

Each backup is sent after it is written, and the oldest ones over the retention are deleted from the bucket. If
the bucket cannot be reached, the run ends as a **warning**: the archive is still on the server, only its
off-site copy failed. **Delete the local file once the bucket has it** leaves nothing on the server.

#### Restoring

On the **Tasks** page, the **Full backups** card lists the archives on the server and, when the copy is enabled,
those in the bucket (which can be brought back onto the server and restored from there). A file downloaded
earlier can be uploaded and restored too. Typing `RESTORE EVERYTHING` confirms it.

A restore replaces the database, the definitions and the published data, then picks the new configuration up
without a restart; everybody is signed out. By default a referential created *since* the backup keeps its data
on disk; tick **Also delete the data of referentials the backup does not carry** to make the instance match the
archive exactly. Each referential folder is swapped in place once it is complete, so a restore interrupted
halfway leaves readable data behind.

A backup made on an **older schema version** is refused with an explanation: restore it on an instance running
the version it was made with, which brings the schema up to date by itself.

#### Restoring onto a second instance (another encryption key)

A backup restored onto an instance whose `REFEX_SECRET_KEY` is not the one it was made with would land secrets
nobody can read: the database comes back, and everything inside it that was encrypted stays encrypted with the
other key. So that case is handled on purpose.

Tick **This backup comes from another instance** in the restore window and give that instance's key — its
`REFEX_SECRET_KEY`, or the content of its `data/.secret_key` file, copied as it is. The archive is opened with
it, and once everything is in place it is all **re-encrypted with the local key**:

- the secret fields of the settings (LDAP bind password, OIDC client secret, SMTP and proxy passwords, syslog
  client key, assistant API key, the S3 keys);
- the TOTP secrets of the accounts;
- the secrets of the secret manager and of the LLM connectors;
- the rows of the confidential internal referentials, **including the keyed fingerprint of their key**, so
  lookups by key keep working;
- the Parquet files of the confidential referentials, rewritten with the new key, and their column profiles.

The key is used for that restore only: it is never stored, never logged and never written to the audit trail,
which records how many values were re-encrypted and nothing else. What is *not* re-encrypted: the archives
already on the server, which keep the key they were written with, so hold on to the old key as long as you may
want to restore them. Pseudonyms of masked columns change with the key, since they are derived from it — they
are computed when data is served, so nothing is rewritten, but a pseudonym noted before no longer matches.

The archive says which key wrote it (a one-way fingerprint, never the key), so:

- the list of backups marks the ones made elsewhere **another key**, before you try anything;
- a key that is not the one the archive was made with is refused **before anything is touched**;
- an unencrypted archive from another instance is refused too, rather than restored into unreadable secrets.

Re-keying can be run again safely: whatever already reads with the new key is left alone, so a restore
interrupted halfway is simply restored again.

## Checking the files that come in

Two features, **off by default**, each turned on on its own in **Settings › Experimental** and configured just
under its switch. They are independent: either can be used without the other. Neither ever reaches the
network — no cloud service, no file sent to a third party, nothing downloaded at scan time — so both work the
same in an air-gapped network.

> **What this protects, honestly.** RefExposer never *executes* a source file: DuckDB parses it. These checks
> protect the people who download the exports built from that data, and they answer the obligation many
> organisations have to scan whatever enters them. What keeps the server standing is elsewhere: the rootless
> container, the disk guard, the resource limits and the SSRF guard.

### Antivirus scan

Every source lands its files in `data/<id>/raw/` before the import reads them, so one scan covers downloads,
S3 objects, Git files, uploads and the import folder. Two backends:

- **A ClamAV daemon** (`clamd`), over a TCP or Unix socket, spoken to with its own protocol. Give it the data
  volume and it reads each file **by path**: nothing crosses the socket, which is the only workable way for a
  file of several GB. Without the volume, the file is streamed to it (`INSTREAM`), which the daemon's own
  `StreamMaxLength` bounds — 25 MB by default.
- **A command** of this machine: `clamscan`, a corporate agent, a YARA binary. Called as `<command> <file>`,
  read with ClamAV's convention — 0 clean, 1 infected, anything else an error.

Beside the application, the daemon is one service of the compose file:

```yaml
  clamav:
    image: clamav/clamav:1.4
    restart: unless-stopped
    volumes:
      - refexposer-data:/data:ro        # the same volume, read-only: it only has to read
      - clamav-signatures:/var/lib/clamav
```

Then, in **Settings › Experimental › Antivirus**: address `clamav:3310`, *the daemon shares the data volume*
on.

**A file that was skipped is never called clean.** This is the point of the feature having four verdicts
instead of two. ClamAV stops at its own `MaxFileSize` and `MaxScanSize` (100 MB by default), so a 40 GB feed
would otherwise come back "clean" having never been read, and an instance would believe itself protected.
Such a file is reported *not scanned*, and what that means for the update is yours to set: **refuse it**
(the default) or **report it and carry on**. The same choice exists for a scanner that cannot be reached,
which is a different question and deserves its own answer.

**Signature freshness is reported, never fetched.** In an air-gapped network `freshclam` needs a local mirror,
and a scanner whose database is six months old is a comfort, not a control. The **Antivirus** task (Tasks
page) says how old the database is, warns past the threshold you set, and applies the retention of the
quarantine. **Test the scanner** sends the standard EICAR test file — the one file every scanner agrees to
report, and which is not malware — so the whole path is proven end to end rather than assumed.

A refused file is moved to `data/.quarantine/<referential>/<run>/` with a note saying what matched, and the
update is **rejected**: in this application that means the version already published stays published, so a
poisoned feed never replaces good data. The quarantine is listed under its own heading in Experimental, it is
**never carried into a backup**, and the task deletes what is older than the retention.

### Structural checks, and safe spreadsheet exports

No daemon and no signature database: these answer what a data file is allowed to **be**, which is a narrow
enough question to answer properly — and they keep working the day a scanner stops being updated.

On the way in, three questions about each file, each with its own policy (*refuse the update* or *report it*):

- **Is it an executable?** A feed that answers with a Windows, Linux or macOS binary is not a feed, whatever
  its extension says. Read from the first bytes, through gzip when the source is left compressed.
- **Does it carry macros?** An `.xlsm`, or an Office file holding `vbaProject.bin`. Nothing in a referential
  needs a macro.
- **Is it what it claims to be?** A file declared CSV whose first bytes are those of a PDF, of an old Office
  document or of a database. (An HTML error page answered with an HTTP 200 is already refused by the import
  itself, which says so in its own words: there is no second check for it.)

On the way out, one question about the exports:

- **Will a spreadsheet treat a value as a formula?** A cell beginning with `=`, `+`, `-`, `@`, a tab or a
  carriage return is executed by Excel and LibreOffice when the file is opened. Partner data is
  attacker-controlled text, and it is handed to analysts as CSV and XLSX: that is a path from *hostile data in*
  to *code running on a colleague's machine*, and no antivirus sees it, because the file is a valid CSV.
  Such a value is prefixed with a single quote, which spreadsheets read as "this is text".

  This **changes the exported value** of such a cell, which is why it is a choice and not a default. A plain
  number is left alone (`-5`, `+12.5`): a negative figure is not an attack, and quoting it would corrupt the
  data to no purpose. The Parquet, JSON and JSON Lines exports are untouched — nothing executes them. A
  generated download already on disk is rebuilt when the setting changes, so a file is never served under the
  other rule.

Already covered elsewhere, and not repeated here: a path escaping its archive (zip-slip), an archive member
that is not a regular file, a decompression bomb (the disk guard), an empty or whitespace-only source, a Git
LFS pointer, and an HTML error page.

## Logging

### Level of the container logs

`REFEX_LOG_LEVEL` sets what `docker compose logs backend` shows: `trace`, `debug`, `info` (default), `warning`, `error` or `critical`. It applies to the application, to the web server (access log at `info`) and to the libraries. `trace` also shows the generated SQL queries and the HTTP / scheduler details. The audit trail is printed at the `info` level. Restart the backend after a change (`docker compose up -d backend`).

### Syslog forwarding (RFC 5424)

**Administration › Settings › Logging** sends the logs and the audit trail to a syslog collector (rsyslog, syslog-ng, a SIEM…):

- **Format**: RFC 5424 (`<PRI>1 TIMESTAMP HOSTNAME APP-NAME PROCID MSGID [STRUCTURED-DATA] BOM MSG`), timestamps in UTC with microseconds, UTF-8 message.
- **Transports**:
  - **UDP** (RFC 5426);
  - **TCP** (RFC 6587, octet counting by default, or a line feed for legacy collectors);
  - **TLS** (RFC 5425, TLS 1.2 minimum, octet counting).
- **TLS trust**: the collector certificate is verified against public CAs, the **root CA** pasted in the form, and optionally the company CAs of *Network & proxy*. A client certificate and key can be given for mutual TLS.
- **Audit trail** (sign-ins, rights, settings, exports, SQL queries…): sent with severity *notice*, or *warning* for a failure. The MSGID is the action, and the fields are structured data:
  `[audit@32473 user="alice" action="auth.login" target="-" success="true" ip="10.0.0.5"]`, with the details as JSON in the message.
- **Application logs**: sent from a chosen minimum level, with `[log@32473 logger="app.ingest" level="ERROR"]`. Every message also carries `[origin software="RefExposer" swVersion="…"]`.
- Facility, APP-NAME, HOSTNAME and enterprise number are configurable. 32473 is the number reserved for documentation; set your organization's number if it has one.
- **Delivery**: messages leave from a background queue (10,000 messages), so the application never waits for the collector. An unreachable collector is retried with a growing delay, up to 60 s; when the queue is full, the oldest messages are dropped. The page shows the sent, waiting and dropped counts. The **Test** button checks name resolution, the connection, the TLS handshake (with the certificate and its issuer) and sends a test message.

rsyslog collector receiving RFC 5424 over TLS (port 6514):

```
global(DefaultNetstreamDriver="ossl"
       DefaultNetstreamDriverCAFile="/etc/rsyslog.d/ca.pem"
       DefaultNetstreamDriverCertFile="/etc/rsyslog.d/collector.pem"
       DefaultNetstreamDriverKeyFile="/etc/rsyslog.d/collector.key")
module(load="imtcp" StreamDriver.Name="ossl" StreamDriver.Mode="1" StreamDriver.AuthMode="anon")  # "x509/certvalid" for mutual TLS
input(type="imtcp" port="6514" ruleset="refexposer")

template(name="rfc5424" type="string" string="%rawmsg%\n")
ruleset(name="refexposer") {
  action(type="omfile" file="/var/log/refexposer.log" template="rfc5424")
}
```
