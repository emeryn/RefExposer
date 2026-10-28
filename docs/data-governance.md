# Governance, confidentiality and quality

[← Documentation](README.md) · [RefExposer](../README.md)

## Governance: visibility and ownership

Every referential says **what kind of data it holds** and **who answers for it**. Both are shown wherever it
appears — on its card in the dashboard, in its header, in the API — because the two kinds are not handled the
same way, and because a question about the data needs someone to ask.

| Field | What it means |
|---|---|
| `visibility: internal` (the default) | Company data. Not published anywhere else: it is not to be copied into a ticket, a pastebin or a third-party tool without thinking about it. |
| `visibility: public` | Open data or an external feed (CISA KEV, NVD, IANA, a public blocklist…): already published outside, and quoting it is free. |
| `owner` | Who answers for this data: a name or, better, an e-mail address. The **e-mail notification channels** can send the alerts of a referential to its owner (*Also the owner of the referential*). |
| `owner_team` | The team behind that person, for when they move on. |

- **Where it shows**: a filled **Internal** (padlock) or **Public** (globe) badge on each card and in the
  header of the referential, the owner with their initials beside it, and a quick filter
  **All / Internal / Public** in the dashboard. The *Referentials* stat card says how the catalogue splits.
- **Where it is set**: *Description* step of the referential editor, or in YAML:

```yaml
- id: staff-directory
  name: Staff directory
  visibility: internal          # internal (default) · public
  owner: dpo@example.com
  owner_team: Security
```

- `visibility` and the owner are **metadata**: changing them never rebuilds the referential.

## Confidential referentials

A referential marked **confidential** (`confidential: true`, or the switch in its editor) has its data **encrypted at rest**. It is still used normally through the interface, the API and SQL by the people who have access to it. Access rights do not change: confidentiality protects the stored data (disk, volumes, backups, database dumps), not the access.

| Stored data | Protection |
|---|---|
| Published version, previous version, sorted copies (Parquet) | Parquet Modular Encryption, AES-GCM (one key per referential) |
| Rows of an internal referential (PostgreSQL) | AES-256-GCM, bound to the referential |
| Keys of these rows | replaced by a keyed fingerprint (HMAC-SHA256): uniqueness and lookups by key still work |
| Column profile (min, max… in `meta.json`) | AES-256-GCM |
| Downloaded source files, manual imports, the internal export used to publish | deleted once the version is published |
| Downloads (CSV, Excel, Parquet…) | generated in clear for each request, then deleted: never cached |

- **Keys**: they are derived (HKDF-SHA256) from `REFEX_SECRET_KEY`, one per referential and per use.
  - Set `REFEX_SECRET_KEY` in the environment (or a secret manager). Otherwise the generated key is stored in `data/.secret_key`, on the same volume as the data it protects.
  - **Back up the key**: without it, the published versions can be rebuilt from their sources, but the rows of confidential internal referentials are lost.
- **Changing the setting** publishes the referential again:
  - the previous version is removed instead of being kept with the other encryption state;
  - the rows of an internal referential are encrypted or decrypted in the database.
- **Not covered**:
  - data in memory, and data in transit (use HTTPS, see [Expose behind an nginx reverse proxy](deployment.md#expose-behind-an-nginx-reverse-proxy));
  - the temporary files DuckDB may write during very large sorts (`data/.tmp`, deleted after the query);
  - files that a *local* or *sync* source reads from outside RefExposer;
  - SQL queries logged at the `trace` level;
  - Bloom filters and MaxMind databases, which are served as is to tools and cannot be confidential.
- **Searching** an internal referential decrypts its rows in the application, because the database only holds ciphertexts. This stays fast up to a few hundred thousand rows.

## Personal data: column masking

A referential can name the columns that hold **personal data**. Those columns are **masked for everyone**, and
the real values only come out for an account that is entitled to see them **and** asks for them explicitly.

```yaml
- id: staff-directory
  name: Staff directory
  key: id
  pii:
    columns: [email, full_name]
    strategy: hash              # hide · partial · hash
    reveal_groups: [dpo, security]
    reveal_users: []            # account ids, for a named exception
    note: "GDPR record R-14, kept 3 years"
```

| `strategy` | What is served | Good for |
|---|---|---|
| `partial` (the default) | `a***e` — the first and last characters | Recognising a value you already know |
| `hash` | a stable 16-character pseudonym, salted with the instance secret | Counting, grouping and **joining** on the column without the value |
| `hide` | `***` | When even the shape must not leak |

**It is one rule, applied where the data is read.** The DuckDB view of the referential selects the masked
expression instead of the column, so there is nothing for a caller to remember: rows, lookups, exports,
column statistics, the global search, the change view, the generated downloads, the SQL console, the MCP
servers and the assistant are all masked, and so is anything added later.

**Revealing takes two things**, and both are needed:

1. **the right** — the account is named in `pii.reveal_users`, belongs to one of `pii.reveal_groups`, or is an
   administrator;
2. **the request** — `reveal=true` on the call (the *Show personal data* switch in the Data tab, the SQL
   console and the diff view). Every such request is written to the **audit log** (`pii.reveal`, with the
   columns and the path).

So a script, a cron job, a download link or a question to the assistant **never** gets personal data by
accident: masked is the default, not a mode to turn on.

- **The assistant and the MCP servers can never reveal.** They act for a model or a tool, which cannot give
  the consent, so the switch is simply not available to them — whatever the prompt asks, and even when the
  account behind them is an administrator.
- **Generated downloads** (CSV, Excel, Parquet…) are produced from the masked view and cached, so one user's
  right never leaks into a file served to another. The **source files** and the **previous version**, which
  are served straight from disk, are refused to an account that may not reveal; use
  `GET /api/referentials/{id}/export?reveal=true` instead.
- **A unified referential reads its sources masked**: personal data does not spread into a derived
  referential by being combined, and the masked values are what lands in its own Parquet file. To keep the
  real values downstream, mask them on the unified referential rather than on its source. Changing `pii`
  rebuilds the referentials built from it.
- **Masking is not encryption**: the published Parquet file still holds the real values. When they must not be
  readable on disk either, mark the referential **confidential** as well — the two combine.
- **A masked column is served as text**, whatever it held: a masked number is not a number any more, and
  saying so is better than serving a plausible wrong value.
- **A configured column the data does not have** (a renamed source, a typo) is reported as `missing` in the
  interface and in the API instead of silently taking the masking away.
- **One consequence worth knowing**: with `hide` or `partial`, two rows differing only in a masked column read
  as identical in the change and diff views. Use `hash` when that matters — it keeps them distinct.

API: `pii` on `GET /api/referentials` and `/api/referentials/{id}` (columns, strategy, note, `can_reveal`,
`missing`), `reveal=true` on `/rows`, `/export`, `/lookup`, `/changes`, `/diff`, `/columns/{c}/stats`, and
`{"reveal": true}` on `POST /api/sql`.

## Data quality

Each referential can declare **quality rules**, checked on every new version in one scan of the data:

```yaml
quality:
  rules:
    - {column: ip, check: format, format: ipv4, threshold: 99.9}        # 99.9 % of the rows at least
    - {column: sha256, check: format, format: sha256, severity: error}  # error: the version is rejected
    - {column: cve, check: unique}
    - {column: severity, check: values, values: [low, medium, high, critical]}
    - {column: score, check: range, min: 0, max: 10}
    - {column: label, check: length, max_length: 255}
    - {column: code, check: pattern, pattern: "[A-Z]{3}-[0-9]+"}
    - {check: expression, expression: "first_seen <= last_seen", name: "consistent dates"}
```

| Check | A row passes when |
|---|---|
| `not_null` | the value is present (not NULL, not blank) |
| `unique` | its value appears only once |
| `format` | the value is a valid `ipv4`, `ipv6`, `ip`, `cidr`, `domain`, `email`, `url`, `md5`, `sha1`, `sha256`, `sha512`, `cve`, `uuid`, `mac`, `asn` or `country` (ISO 3166 alpha-2) |
| `pattern` | the whole value matches the regular expression (RE2) |
| `values` | the value is among the allowed ones |
| `range` | the value is between `min` and `max` (numbers, or ISO dates / text) |
| `length` | the value has between `min_length` and `max_length` characters |
| `expression` | the SQL condition is true |

- **Empty values** only fail `not_null`: the other checks ignore them. The completeness of the **key** is checked automatically.
- **Threshold** (100 % by default): the share of rows that must pass. **Severity**: `warning` publishes the version and marks it; `error` rejects it, and the published version is kept (a *quality gate*, like `validation`). Internal referentials are never rejected, only marked.
- **Score**: the mean of the pass rates (0–100). **Status**: *ok*, *warning* (a warning rule is not met) or *failed*.
- **Where**: the **Quality** tab of the referential (score, history of the last versions, pass rate of each rule, failing rows and up to 5 failing values; never shown for a confidential referential); a badge and the *Quality issues* filter in **Administration › Referentials**; `quality` in the API answers (`GET /api/referentials/<id>/quality`).
- **Changing the rules** does not download anything again: the published version is checked again in the background (or with **Check again**, `POST /api/referentials/<id>/quality/check`).
- **Notifications**: the `quality` event warns when a version is published with rules not met while the previous one met them. A failing `error` rule rejects the version, which is a `failure`.
- The rules are edited in the referential editor (step *Description*). They take one scan of the data: on billions of rows, keep them few.

## Kept versions: time travel, saved states and restore

A version of a referential is kept in two ways, and both end up in the same place — the **Versions & diff**
tab, where they are compared, restored or deleted:

- **time travel**, turned on per referential, keeps **every** published version on its own and drops the
  oldest ones beyond `max_snapshots`;
- **saving a state** keeps the version published right now, on demand, with a label. It needs the manage
  right, works whether time travel is on or not, and is **never dropped automatically**.

```yaml
- id: external-scan
  name: External scan
  key: asset
  primary_key_columns: [asset, port]   # what identifies a row (default: key)
  storage:
    time_travel: true
    max_snapshots: 30                  # the oldest ones are dropped beyond this
```

- **What it costs**: a kept version is a **hard link** on the published Parquet file, so it takes no extra
  space until the version it shares its blocks with is replaced. It shows up as *Past versions kept* in
  **System › Disk space** (at its full size, which overstates what it really holds).
- **Where**: the **Versions & diff** tab of the referential — two version pickers, the
  `+X added, −Y removed, ~Z modified` summary, the rows of each category, and the before/after of every
  changed column. The list below it shows what is kept, with **Save the current state**, and per line the
  label, who saved it, **restore** and **delete**.
- **How a row is identified**: `primary_key_columns`, failing that the `key` of the referential, failing both
  the whole row — and then a change reads as one removal and one addition, which the interface says plainly.
- **A schema change** does not break the comparison: only the columns both versions have are compared, and the
  others are listed.
- **Masking applies**: a diff is masked like everything else, and `reveal=true` works the same way.
- **The assistant can use it**: the tools `list_referential_snapshots` and `diff_referential_snapshots` are
  how it answers "what is new since Monday", instead of guessing from two queries. They are also in the
  [MCP data server](api.md#mcp-data-server-every-user).

This is not the same thing as the **Changes** tab, which always compares the published version with the one
before it (`storage.keep_previous`). Time travel goes further back, and compares any two versions.

### Restoring a kept version

**Restore** publishes the data of a kept version again. It is the way back from a source that published
nonsense, a transformation that turned out wrong, or a delta that should not have been merged.

- It is **published as a new version**, not rewound: the version number moves forward, the history keeps
  every step, and the *Changes* tab shows what the restore changed. The file is hard-linked, so restoring a
  version does not consume it and it can be restored again.
- Everything the published version is made of is **rebuilt** from that file: the sorted copies (`sort_by`,
  `indexes`), the row count, the columns, the column profile and the **quality rules**, which are checked
  again. The generated downloads of the replaced version are dropped.
- The state published right now is **saved first**, with the label *Before restoring version N*: a restore
  made by mistake is one more restore away.
- The referential is then **frozen** (below), because the next scheduled update would read the source again
  and undo the restore. The banner says so and resuming the updates is one click.
- It is refused when the version was published with the other confidentiality setting (one is encrypted and
  the other is not), or when it does not have the sort or index columns configured today.

### Freezing a referential

A frozen referential keeps its published version: the **schedule**, the **check at startup** and the **sync
folder** leave it alone, and so do the rebuilds of the unified referentials made from it. An update started by
hand is refused while it is frozen — freezing protects against what happens on its own, and resuming is
explicit.

Freeze it from the menu beside **Update** (*Freeze this version*), by ticking *Freeze this version* on a
manual import, or by restoring a version. The banner on the referential says who froze it, when and why, with
**Resume automatic updates** next to it.

API: `GET /api/referentials/{id}/snapshots`,
`POST /api/referentials/{id}/snapshots` (`{"label": "before the migration"}`, manage right),
`GET /api/referentials/{id}/diff?from={id}&to=latest&kind=summary|added|removed|modified`,
`POST /api/referentials/{id}/snapshots/{snapshot_id}/restore` (manage right),
`DELETE /api/referentials/{id}/snapshots/{snapshot_id}` (manage right),
`POST /api/referentials/{id}/pin` (`{"reason": "audit in progress"}`) and `DELETE /api/referentials/{id}/pin`.
