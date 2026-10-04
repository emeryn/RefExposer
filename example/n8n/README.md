# n8n example: list, query and download referentials

[`refexposer-workflow.json`](refexposer-workflow.json) is an n8n workflow that calls the RefExposer API with an API token:

1. **List referentials** — `GET /api/referentials`. It returns the referentials the token can read, as one item each.
2. **Keep the chosen referential** — keeps the referential named in **Config**, if it is granted to the token and published.
3. **Action** — runs one of three branches:
   - `rows`: **Query rows** — `GET /api/referentials/{id}/rows` with column filters, a text search and a limit. **One item per row** then turns each row into an n8n item.
   - `lookup`: **Lookup by key** — `GET /api/referentials/{id}/lookup/{value}`. It returns the row whose key is the value, or a 404 when the value is absent.
   - `download`: **Download the referential** — `GET /api/referentials/{id}/download/{format}`. It returns the whole file as binary data in the `data` property.

```
Start → Config → List referentials → Keep the chosen referential → Action ─┬─ rows     → Query rows → One item per row
                                                                           ├─ lookup   → Lookup by key
                                                                           └─ download → Download the referential
```

## Setup

### 1. Create an API token

For an automation, use a **service account** rather than a personal token:

1. In RefExposer, go to **Administration › Users › New service account**. For example, name it `n8n` with the purpose "n8n workflows".
2. Give it access to the referentials it needs, directly or through a group. The **Read** right is enough for this workflow.
3. Open the account's page. Under **API tokens**, choose **New token** and copy the `rfx_…` value. It is only shown once.

### 2. Import the workflow into n8n

1. In n8n, go to **Workflows › Import from File** and select `refexposer-workflow.json`.
2. Create the credential. Open **List referentials**, then go to **Credential › Create new › Header Auth**:
   - **Name**: `Authorization`
   - **Value**: `Bearer rfx_…` (your token)
3. Select this same credential in the three other HTTP nodes: **Query rows**, **Lookup by key** and **Download the referential**.

### 3. Set the parameters in the **Config** node

| Field | Example | Use |
|---|---|---|
| `baseUrl` | `http://localhost:8080` | URL of RefExposer (`REFEX_PUBLIC_URL`), without a trailing `/` |
| `referential` | `cisa-kev` | id of the referential |
| `action` | `rows`, `lookup` or `download` | branch to run |
| `search` | `log4j` | `rows`: full-text search (`q`), can be empty |
| `filters` | `knownRansomwareCampaignUse=Known&dateAdded__gte=2024-01-01` | `rows`: column filters, written as a query string |
| `limit` | `100` | `rows`: number of rows (capped by `REFEX_API_MAX_LIMIT`, 1000 by default) |
| `lookupValue` | `CVE-2021-44228` | `lookup`: value of the key |
| `downloadFormat` | `csv.gz` | `download`: `csv`, `csv.gz`, `xlsx`, `json`, `jsonl`, `parquet` or `source` (original files) |

The example values match `cisa-kev` from [`config/cybref.yml.sample`](../../config/cybref.yml.sample). Replace them with your own referential and columns. The **API** tab of each referential in RefExposer lists its columns and generates ready-to-use URLs.

Run the workflow with **Execute workflow**.

## Filters

Filters use `column=value` or `column__operator=value`. You can combine several with `&`.

| Operator | Meaning |
|---|---|
| (none) / `eq`, `ne` | equal, different |
| `gt`, `gte`, `lt`, `lte` | comparisons (numbers, dates, text) |
| `contains`, `ncontains`, `startswith`, `endswith` | text, case-insensitive |
| `in`, `nin` | among a comma-separated list, or a JSON array |
| `isnull` | empty (`true`) or not (`false`) |
| `regex` | regular expression |

Other parameters of `/rows`:
- `sort=column` or `sort=-column` (descending)
- `columns=a,b` (only these columns)
- `offset=` (pagination: loop while `offset < total`)
- `count=false` (no total, faster)

## Going further

- **Enrichment of many values**: `POST /api/referentials/{id}/lookup` with `{"values": ["CVE-…", "CVE-…"]}`, up to 10,000 values in one call. It returns the found rows and the missing values. In n8n, this is an HTTP Request node in `POST` mode with a JSON body.
- **Filtered export** instead of the whole file: `GET /api/referentials/{id}/export?format=csv&<filters>`.
- **Download only when the data changed**: the `X-Checksum-SHA256` header of the download, the `version` field from the list, and `If-None-Match` (answered with `304`) avoid fetching the same version twice.
- **Search every referential at once**: `GET /api/search?q=…`.
- **Save the downloaded file**: add a *Read/Write Files from Disk*, *S3*, *SFTP*… node after **Download the referential**, on the binary property `data`.
- **Schedule**: replace **Start** with a *Schedule Trigger*. Referentials are refreshed on their own schedule, shown in the list (`next_run_at`).

The complete API is documented in RefExposer under `/api/docs`. The documentation generated for each referential, with its typed filters, is under `/api/catalog/docs`.
