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
| Backup | **Configuration backup** (see below) | daily, 14 kept |
| Folder scans | **Import folder** and **sync folder** scans | `REFEX_IMPORT_POLL_SECONDS` / `REFEX_SYNC_POLL_SECONDS` |

### Configuration backups

The **Configuration backup** task writes a dated file to `data/.backups/`. It contains:

- the referential definitions;
- accounts and groups, rights, API tokens and security keys;
- settings and the rows of internal referentials;
- a copy of the YAML files of `config/`, for reference.

The file is **encrypted** with a key derived from `REFEX_SECRET_KEY`, so it can only be restored with the same key. From the **Tasks** page, a backup can be downloaded, deleted or **restored** (typing `RESTORE` confirms it); a downloaded backup can be restored too. A restore replaces the configuration in one transaction and signs everybody out. The published data, the history and the audit log are kept.

It does not replace the backup of the PostgreSQL volume and of `data/`.

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
