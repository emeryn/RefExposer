# Formats, options and transformations

[← Documentation](README.md) · [RefExposer](../README.md)

## Formats and options

`options` are passed as is to the matching DuckDB reader:

| Format | Reader | Useful options |
|---|---|---|
| `csv`, `tsv` | `read_csv` | `delim`, `header`, `skip`, `nullstr`, `quote`, `all_varchar`, `ignore_errors`, `null_padding`, `dateformat`, `columns`… |
| `json` | `read_json` | `records_path: a.b` (nested array of records), `records_path: "*"` or `a.*` (object whose values are the records, key in `records_key`, default `_key`), `maximum_depth`, `columns`… |
| `jsonl` | `read_json` (newline-delimited) | same |
| `txt` | one line = one record | `column` (column name, default `value`), `comment` (default `#`), `skip_empty` |
| `parquet` | `read_parquet` | |
| `xlsx` | `read_xlsx` (excel extension) | `sheet` (the analysis lists them), `range` (`A1:F500`), `header`, `all_varchar`, `ignore_errors`, `stop_at_empty`, `empty_as_varchar` |
| `xml` | converted to JSON Lines, then `read_json` | `records_path`: name of the repeated record element (default: the likeliest one, frequent and rich) |
| `bloom` | DCSO Bloom filter (published as is) | `normalize`, `pattern`, `enrich_bulk_url`, `enrich_key` |
| `mmdb` | MaxMind DB (published as is, IP lookups) | |
| `sqlite` | SQLite database, attached read-only (sqlite extension) | `table`: table or view to read; `nsrl`: NIST NSRL set, publications and deltas found automatically (see [SQLite databases](sources.md#sqlite-databases-nist-nsrl-and-sql-deltas)) |

### Excel workbooks

A column of an Excel sheet takes the type of its **first value**: a column of numbers holding one piece of text further
down (a note in a “comment” column) cannot be read at all, and the workbook declares no type for the column itself.
RefExposer then reads **every column as text**, keeps `all_varchar: true` in the definition so the next updates read the
file the same way, and says so in the preview and in the run log. Cast the columns that really are numbers or dates in
the transformation:

```yaml
format: xlsx
options:
  sheet: Inventory          # the analysis lists the sheets of the workbook
  all_varchar: true
transform: |
  SELECT * REPLACE (TRY_CAST(score AS DOUBLE) AS score, TRY_CAST(opened AS DATE) AS opened) FROM {source}
```

`ignore_errors: true` is the other way out — cells that do not fit become empty — but it loses data silently.
`stop_at_empty: false` keeps reading past a blank row in the middle of the sheet.

## SQL transformations

The `transform` field is a DuckDB query producing the final data set. Available placeholders:

| Placeholder | Value |
|---|---|
| `{source}` | reader over all the files (format and options applied) |
| `{source0}`, `{source1}`… | reader over the files of the 1st, 2nd… URL |
| `{files}`, `{files0}`… | literal list of paths (to call a reader yourself) |

Example of a join between two files (IMDb):

```yaml
transform: |
  SELECT b.tconst, b.primaryTitle AS title, r.averageRating AS rating
  FROM {source0} b LEFT JOIN {source1} r USING (tconst)
```

See `config/cybref.yml.sample` for many real examples (nested JSON, XML, header-less TSV, transformations).

## JSON and XML shapes

The analysis step suggests how to read a document and shows the result live:

- **Array of records inside a document** (`{"vulnerabilities": [...]}`): `records_path: vulnerabilities`.
- **Object whose values are the records** (`{"1945182": [{...}], "1945136": [{...}]}`, `{"0001": {"name": ...}}`): `records_path: "*"` (or `executables.*` when nested). Each value, or each item of a value that is a list, becomes a row, and the object key goes to the `records_key` column (`_key` by default).
- **XML** (`format: xml`): every repeated record element becomes a row; attributes and child elements become columns, repeated children become lists, and mixed XHTML content becomes plain text. `records_path` names the element (e.g. `Weakness` for the MITRE CWE catalog).
- **Nested keys that differ only by case** (`EventId` / `EventID`), which DuckDB's schema detection refuses: the analysis retries with `maximum_depth` and keeps that option, so deeper levels are stored as JSON.
- **Several large JSON documents** (e.g. every yearly NVD feed, several GB once unpacked): records are extracted one document at a time to JSON Lines before being read together, which keeps memory bounded by the largest file. All 25 NVD years (398,466 CVEs) import in about 3.5 minutes.

`config/cybref.yml.sample` defines the 59 referentials of [github.com/emeryn/cybref](https://github.com/emeryn/cybref) (threat intelligence, living-off-the-land projects, cloud IP ranges, OUI and USB ids, NVD, CPE, CWE…), all checked with a full import. Rename it to `.yml`, or copy the entries you need, then reload the configuration.
