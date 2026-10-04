"""Automatic OpenAPI documentation of each referential, generated from its actual schema.

Every referential gets typed parameters (one filter per column), a typed row schema with
examples taken from the data, and documented lookup, export and download endpoints.
"""

from __future__ import annotations

import re
from typing import Any

from . import __version__
from .config import ReferentialConfig
from .downloads import EXCEL_MAX_ROWS, FORMATS as DOWNLOAD_FORMATS
from .sqlbuild import OPERATORS, type_kind

FILTER_OPS = {
    "text": ["contains", "in"],
    "number": ["gte", "lte", "in"],
    "temporal": ["gte", "lte"],
    "boolean": [],
    "list": ["contains"],
    "struct": ["contains"],
}

OPS_DOC = "\n".join(f"| `__{k}` | {v} |" for k, v in OPERATORS.items())

AUTH_DOC = """**Authentication**: personal API token (created from *My account* in the interface) in the
`Authorization: Bearer rfx_…` header, or the browser session (cookie) for calls made from the interface."""

FILTER_DOC = f"""**Filters**: `column=value` (equality) or `column__operator=value`, combinable (logical AND).
Only the most common filters are listed below; every operator works on every column:
| Suffix | Meaning |
| Suffixe | Signification |
|---|---|
{OPS_DOC}
"""


def column_schema(ctype: str) -> dict[str, Any]:
    t = ctype.upper()
    kind = type_kind(t)
    if kind == "list":
        inner = re.sub(r"\[\d*\]$", "", ctype)
        return {"type": "array", "items": column_schema(inner)}
    if kind == "struct":
        return {"type": "object"}
    if kind == "number":
        return {"type": "integer"} if "INT" in t else {"type": "number"}
    if kind == "boolean":
        return {"type": "boolean"}
    if kind == "temporal":
        if t == "DATE":
            return {"type": "string", "format": "date"}
        if t.startswith("TIMESTAMP"):
            return {"type": "string", "format": "date-time"}
        return {"type": "string"}
    return {"type": "string"}


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    return {**schema, "type": [schema["type"], "null"]}


def _ident(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", s)


def _profile_text(p: dict[str, Any] | None) -> str:
    if not p:
        return ""
    parts = []
    if p.get("null_percentage") is not None:
        parts.append(f"{100 - p['null_percentage']:.0f}% filled")
    if p.get("approx_unique") is not None:
        parts.append(f"≈ {p['approx_unique']:,} distinct values")
    if p.get("min") is not None and p.get("max") is not None and len(str(p["min"])) < 40:
        parts.append(f"from `{p['min']}` to `{p['max']}`")
    return " · ".join(parts)


def _param(name: str, schema: dict[str, Any], description: str, example: Any = None, where: str = "query", required: bool = False) -> dict[str, Any]:
    p: dict[str, Any] = {"name": name, "in": where, "required": required, "schema": schema, "description": description}
    if example not in (None, ""):
        p["example"] = example
    return p


def _query_params(ref: ReferentialConfig, columns: list[dict[str, str]], profile: dict[str, dict], sample: dict[str, Any]) -> list[dict[str, Any]]:
    names = [c["name"] for c in columns]
    search_cols = ref.search_columns or [c["name"] for c in columns if type_kind(c["type"]) == "text"][:5]
    params = [
        _param("q", {"type": "string"}, f"Full-text search (case insensitive) in: {', '.join(search_cols)}."),
        _param("sort", {"type": "string"}, f"Sort: column name, prefixed with `-` for descending order, several separated by commas. Columns: {', '.join(names)}.",
               f"-{names[0]}" if names else None),
        _param("columns", {"type": "string"}, "Columns to return, comma separated (all by default).",
               ",".join(names[:3]) if names else None),
    ]
    for c in columns:
        name, ctype = c["name"], c["type"]
        kind = type_kind(ctype)
        base = column_schema(ctype)
        scalar = base["items"] if base.get("type") == "array" else base
        example = sample.get(name)
        if isinstance(example, list):
            example = example[0] if example else None
        if isinstance(example, (dict, list)):
            example = None
        info = _profile_text(profile.get(name))
        key = " (key)" if name == ref.key else ""
        params.append(_param(name, {"type": "string"} if kind in ("struct",) else scalar,
                             f"Equality on `{name}`{key} — type `{ctype.lower()}`{f' — {info}' if info else ''}.", example))
        for op in FILTER_OPS[kind]:
            schema = {"type": "string"} if op in ("in", "contains") else scalar
            ex = example if op != "in" else (f"{example},…" if example is not None else None)
            params.append(_param(f"{name}__{op}", schema, f"`{name}` {OPERATORS[op]}.", ex))
    return params


def build_bloom_paths(ref: ReferentialConfig, meta: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    base = f"/api/referentials/{ref.id}"
    info = meta.get("bloom") or {}
    rec = {"type": "object", "properties": {
        "value": {"type": "string"}, "present": {"type": "boolean"},
        "probabilistic": {"type": "boolean", "description": "Bloom: “present” may be a false positive"},
        "confirmed": {"type": "boolean", "description": "Presence confirmed by the online API (enrich=true)"},
        "details": {"type": "object"},
    }}
    fp = info.get("estimated_fp_rate")
    desc = (f"Bloom filter of {info.get('elements', 0):,} elements"
            + (f", estimated false positive rate {fp:.1e}" if fp else "")
            + ". “Absent” answer: certain; “present”: probable.")
    enrich = bool(ref.options.get("enrich_bulk_url"))
    paths = {
        f"{base}/lookup/{{value}}": {"get": {
            "tags": [ref.name], "operationId": f"{_ident(ref.table)}_lookup", "summary": "Test a value",
            "description": desc + (" `enrich=true` queries the online API to confirm and detail the value (the value is then sent to that service)." if enrich else ""),
            "parameters": [_param("value", {"type": "string"}, "Value to test", None, where="path", required=True)]
                          + ([_param("enrich", {"type": "boolean", "default": False}, "Complete with the online API")] if enrich else []),
            "responses": {"200": {"description": "Present (probably)", "content": {"application/json": {"schema": rec}}},
                          "404": {"description": "Absent (certain)"}, "401": {"description": "Authentication required"}},
        }},
        f"{base}/lookup": {"post": {
            "tags": [ref.name], "operationId": f"{_ident(ref.table)}_lookup_batch", "summary": "Test a list of values",
            "description": desc,
            "requestBody": {"required": True, "content": {"application/json": {"schema": {"type": "object", "required": ["values"], "properties": {
                "values": {"type": "array", "items": {"type": "string"}, "maxItems": 10000}, "enrich": {"type": "boolean", "default": False}}}}}},
            "responses": {"200": {"description": "Result per value (null when absent)"}},
        }},
        f"{base}/download/{{format}}": {"get": {
            "tags": [ref.name], "operationId": f"{_ident(ref.table)}_download", "summary": "Download the filter (DCSO format)",
            "parameters": [_param("format", {"type": "string", "enum": ["source", "previous"]}, "Version", "source", where="path", required=True)],
            "responses": {"200": {"description": ".bloom file"}}, "x-download": True,
        }},
    }
    return paths, {}, {"name": ref.name, "description": f"{ref.description}\n\n{desc}".strip()}


def build_mmdb_paths(ref: ReferentialConfig, meta: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    base = f"/api/referentials/{ref.id}"
    info = meta.get("mmdb") or {}
    file_name = info.get("file_name") or f"{ref.id}.mmdb"
    desc = (f"MaxMind DB `{info.get('database_type', '?')}` (IPv{info.get('ip_version', '?')}, built {str(info.get('build_date', '?'))[:10]}). "
            "A lookup returns the record of the network containing the address, with `ip` and `network` (CIDR).")
    rec = {"type": "object", "properties": {"ip": {"type": "string"}, "network": {"type": "string", "example": "81.2.69.0/24"}},
           "additionalProperties": True}
    flat = _param("flat", {"type": "boolean", "default": False}, "Dotted keys (country.iso_code, city.name...), English names only")
    raw_doc = ("Raw `.mmdb` file, as published, for tools (Logstash, Suricata, Zeek, Graylog, nginx geoip2...). Stable URL; "
               "`ETag` / `If-None-Match` and `Last-Modified` / `If-Modified-Since` answer `304` when unchanged. "
               "Authentication: `Authorization: Bearer <token>` or HTTP Basic with the token as password.")
    paths = {
        f"{base}/lookup/{{ip}}": {"get": {
            "tags": [ref.name], "operationId": f"{_ident(ref.table)}_lookup", "summary": "Look up an IP address",
            "description": desc,
            "parameters": [_param("ip", {"type": "string"}, "IPv4 or IPv6 address", "81.2.69.142", where="path", required=True), flat],
            "responses": {"200": {"description": "Record of the network", "content": {"application/json": {"schema": rec}}},
                          "404": {"description": "Address in no network of the database"}, "400": {"description": "Not an IP address"},
                          "401": {"description": "Authentication required"}},
        }},
        f"{base}/lookup": {"post": {
            "tags": [ref.name], "operationId": f"{_ident(ref.table)}_lookup_batch", "summary": "Look up a list of IP addresses",
            "description": desc,
            "requestBody": {"required": True, "content": {"application/json": {"schema": {"type": "object", "required": ["values"], "properties": {
                "values": {"type": "array", "items": {"type": "string"}, "maxItems": 10000}, "flat": {"type": "boolean", "default": False}}}}}},
            "responses": {"200": {"description": "Record per address (null when in no network, or not an address)"}},
        }},
        f"{base}/raw": {"get": {
            "tags": [ref.name], "operationId": f"{_ident(ref.table)}_raw", "summary": f"Download the raw database ({file_name})",
            "description": raw_doc,
            "responses": {"200": {"description": f"{file_name}"}, "304": {"description": "Not modified"}}, "x-download": True,
        }},
        f"{base}/download/{{format}}": {"get": {
            "tags": [ref.name], "operationId": f"{_ident(ref.table)}_download", "summary": "Download the current or the previous database",
            "parameters": [_param("format", {"type": "string", "enum": ["mmdb", "previous"]}, "Version", "mmdb", where="path", required=True)],
            "responses": {"200": {"description": ".mmdb file"}}, "x-download": True,
        }},
    }
    return paths, {}, {"name": ref.name, "description": f"{ref.description}\n\n{desc}".strip()}


def build_paths(ref: ReferentialConfig, meta: dict[str, Any], sample: dict[str, Any] | None, max_limit: int) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Return (paths, component schemas, tag) for one referential."""
    if ref.is_bloom:
        return build_bloom_paths(ref, meta)
    if ref.is_mmdb:
        return build_mmdb_paths(ref, meta)
    sample = sample or {}
    columns = meta.get("columns") or []
    profile = {p["name"]: p for p in meta.get("profile") or []}
    rows = meta.get("row_count") or 0
    rows_txt = f"{rows:,}"
    tag = ref.name
    base = f"/api/referentials/{ref.id}"
    row_name = f"{_ident(ref.table)}_row"
    row_ref = {"$ref": f"#/components/schemas/{row_name}"}
    opid = _ident(ref.table)

    props = {}
    for c in columns:
        s = _nullable(column_schema(c["type"]))
        info = _profile_text(profile.get(c["name"]))
        s["description"] = f"{c['type'].lower()}{' — referential key' if c['name'] == ref.key else ''}{f' — {info}' if info else ''}"
        if c["name"] in sample and sample[c["name"]] is not None:
            s["examples"] = [sample[c["name"]]]
        props[c["name"]] = s
    schemas = {
        row_name: {"type": "object", "title": f"Row — {ref.name}", "properties": props},
        f"{_ident(ref.table)}_page": {
            "type": "object",
            "title": f"Result page — {ref.name}",
            "properties": {
                "columns": {"type": "array", "items": {"type": "object", "properties": {"name": {"type": "string"}, "type": {"type": "string"}}}},
                "rows": {"type": "array", "items": row_ref},
                "total": {"type": ["integer", "null"], "description": "Total number of rows matching the filters"},
                "limit": {"type": "integer"},
                "offset": {"type": "integer"},
                "elapsed_ms": {"type": "number"},
            },
        },
    }
    page_ref = {"$ref": f"#/components/schemas/{_ident(ref.table)}_page"}
    errors = {
        "400": {"description": "Invalid request (unknown column or operator, value incompatible with the type)"},
        "401": {"description": "Authentication required"},
        "404": {"description": "Unknown or inaccessible referential"},
    }
    qparams = _query_params(ref, columns, profile, sample)
    page_params = [
        _param("limit", {"type": "integer", "default": 50, "minimum": 0, "maximum": max_limit}, f"Number of rows (max {max_limit})."),
        _param("offset", {"type": "integer", "default": 0, "minimum": 0}, "Offset for pagination."),
        _param("count", {"type": "boolean", "default": True}, "Compute the total (`false` is faster)."),
    ]
    rows_example = {"columns": [{"name": c["name"], "type": c["type"]} for c in columns], "rows": [sample] if sample else [],
                    "total": meta.get("row_count"), "limit": 50, "offset": 0, "elapsed_ms": 12.3}

    paths: dict[str, Any] = {}
    paths[base] = {"get": {
        "tags": [tag], "operationId": f"{opid}_metadata", "summary": "Status and metadata",
        "description": "Version, row count, schema, column profile, last update, schedule.",
        "responses": {"200": {"description": "Referential metadata"}, **errors},
    }}
    paths[f"{base}/rows"] = {"get": {
        "tags": [tag], "operationId": f"{opid}_rows", "summary": "Query the data",
        "description": f"Search, filters, sorting and pagination over the {rows_txt} rows of the referential.\n\n{FILTER_DOC}",
        "parameters": qparams + page_params,
        "responses": {"200": {"description": "Result page", "content": {"application/json": {"schema": page_ref, "example": rows_example}}}, **errors},
    }}
    if ref.key:
        key_col = next((c for c in columns if c["name"] == ref.key), {"type": "VARCHAR"})
        key_example = sample.get(ref.key)
        paths[f"{base}/lookup/{{value}}"] = {"get": {
            "tags": [tag], "operationId": f"{opid}_lookup", "summary": f"Exact lookup by key ({ref.key})",
            "description": f"Returns the row whose `{ref.key}` equals the given value (404 when absent). `all=true` returns every matching row.",
            "parameters": [
                _param("value", {"type": "string"}, f"Value of `{ref.key}` ({key_col['type'].lower()})", key_example, where="path", required=True),
                _param("all", {"type": "boolean", "default": False}, "Return every match (list)."),
            ],
            "responses": {"200": {"description": "Row found", "content": {"application/json": {"schema": row_ref, "example": sample or None}}},
                          **errors, "404": {"description": "No row for this key"}},
        }}
        paths[f"{base}/lookup"] = {"post": {
            "tags": [tag], "operationId": f"{opid}_lookup_batch", "summary": "Batch enrichment",
            "description": f"Looks up to 10,000 values of `{ref.key}` in one request.",
            "requestBody": {"required": True, "content": {"application/json": {
                "schema": {"type": "object", "required": ["values"], "properties": {"values": {"type": "array", "items": {"type": "string"}, "maxItems": 10000}}},
                "example": {"values": [str(key_example) if key_example is not None else "VALUE", "UNKNOWN"]},
            }}},
            "responses": {"200": {"description": "Result per value (null when absent)", "content": {"application/json": {
                "schema": {"type": "object", "properties": {
                    "found": {"type": "integer"}, "missing": {"type": "array", "items": {"type": "string"}},
                    "results": {"type": "object", "additionalProperties": {"anyOf": [row_ref, {"type": "null"}]}},
                }},
            }}}, **errors},
        }}
    dl_formats = [f for f in DOWNLOAD_FORMATS if not (f == "xlsx" and rows > EXCEL_MAX_ROWS)]
    paths[f"{base}/download/{{format}}"] = {"get": {
        "tags": [tag], "operationId": f"{opid}_download", "summary": "Download the whole referential",
        "description": "Complete file of the published version, generated once per version then served from the cache. "
                       "The fingerprint is given in the `X-Checksum-SHA256` header (and `ETag`). "
                       "`source` returns the original files, `previous` the previous version (Parquet).",
        "parameters": [_param("format", {"type": "string", "enum": dl_formats + ["source", "previous"]}, "File format", "csv", where="path", required=True)],
        "responses": {"200": {"description": "File", "content": {DOWNLOAD_FORMATS[f][2]: {"schema": {"type": "string", "format": "binary"}} for f in dl_formats}},
                      **errors, "413": {"description": "Too many rows for the requested format (Excel)"}},
        "x-download": True,
    }}
    paths[f"{base}/export"] = {"get": {
        "tags": [tag], "operationId": f"{opid}_export", "summary": "Export a selection",
        "description": f"Export of the rows matching the filters (same parameters as `/rows`).\n\n{FILTER_DOC}",
        "parameters": [_param("format", {"type": "string", "enum": ["csv", "xlsx", "json", "jsonl", "parquet"], "default": "csv"}, "Format", "csv")]
                      + qparams + [_param("limit", {"type": "integer", "minimum": 1}, "Maximum number of exported rows.")],
        "responses": {"200": {"description": "File", "content": {"text/csv": {"schema": {"type": "string", "format": "binary"}}}}, **errors},
        "x-download": True,
    }}
    if columns:
        paths[f"{base}/columns/{{column}}/stats"] = {"get": {
            "tags": [tag], "operationId": f"{opid}_column_stats", "summary": "Column statistics",
            "description": "Fill rate, cardinality, most frequent values and distribution.",
            "parameters": [_param("column", {"type": "string", "enum": [c["name"] for c in columns]}, "Column", columns[0]["name"], where="path", required=True)],
            "responses": {"200": {"description": "Statistiques"}, **errors},
        }}
    if ref.key:
        paths[f"{base}/changes"] = {"get": {
            "tags": [tag], "operationId": f"{opid}_changes", "summary": "Changes since the previous version",
            "parameters": [
                _param("kind", {"type": "string", "enum": ["added", "removed", "modified"], "default": "added"}, "Kind of change", "added"),
                _param("limit", {"type": "integer", "default": 50, "maximum": 1000}, "Number of rows"),
                _param("offset", {"type": "integer", "default": 0}, "Offset"),
            ],
            "responses": {"200": {"description": "Rows added, removed or modified"}, **errors},
        }}

    if ref.is_internal:
        paths.update(_internal_paths(ref, base, row_ref, sample, errors))

    desc = ref.description or ""
    facts = [f"{rows_txt} rows", f"{len(columns)} columns"]
    if meta.get("version"):
        facts.append(f"version {meta['version']}")
    if ref.key:
        facts.append(f"key `{ref.key}`")
    tag_obj = {"name": tag, "description": f"{desc}\n\n{' · '.join(facts)} · SQL table `{ref.table}`".strip()}
    if ref.homepage:
        tag_obj["externalDocs"] = {"url": ref.homepage, "description": "Source"}
    return paths, schemas, tag_obj


_JSON_TYPES = {"text": "string", "integer": "integer", "number": "number", "boolean": "boolean", "date": "string",
               "datetime": "string", "list": "array"}


def _internal_paths(ref: ReferentialConfig, base: str, row_ref: dict[str, Any], sample: dict[str, Any], errors: dict[str, Any]) -> dict[str, Any]:
    """Write endpoints of an internal referential (fed from RefExposer: editor or API)."""
    props: dict[str, Any] = {}
    for c in ref.columns:
        s: dict[str, Any] = {"type": _JSON_TYPES[c.type]}
        if c.type == "date":
            s["format"] = "date"
        if c.type == "datetime":
            s["format"] = "date-time"
        if c.type == "list":
            s["items"] = {"type": "string"}
        if c.description:
            s["description"] = c.description
        props[c.name] = s
    required = [c.name for c in ref.columns if c.required or (c.name == ref.key and not c.auto)]
    record = {"type": "object", "properties": props, "required": required, "additionalProperties": False}
    example = {c.name: sample.get(c.name) for c in ref.columns} if sample else None
    tag = ref.name
    op = _ident(ref.table)
    w_errors = {**errors, "400": {"description": "Invalid row (unknown column, missing required value, wrong type)"},
                "403": {"description": "The manage right on this referential is required"}}
    note = ("Internal referential: rows are managed in RefExposer. Every change is published as a new version "
            "(a few seconds later) and is recorded in the audit log.")
    key_param = _param("key", {"type": "string"}, f"Value of the key `{ref.key}`", sample.get(ref.key) if sample else None, where="path", required=True)
    return {
        f"{base}/records": {
            "get": {"tags": [tag], "operationId": f"{op}_records", "summary": "Editable rows (current state)",
                    "parameters": [_param("q", {"type": "string"}, "Text search"), _param("limit", {"type": "integer", "default": 50}, "Rows"),
                                   _param("offset", {"type": "integer", "default": 0}, "Offset")],
                    "responses": {"200": {"description": "Rows"}, **errors}},
            "post": {"tags": [tag], "operationId": f"{op}_record_create", "summary": "Add a row (or several with {\"records\": [...]})",
                     "description": note, "parameters": [_param("upsert", {"type": "boolean", "default": False}, "Update the row if the key exists")],
                     "requestBody": {"required": True, "content": {"application/json": {"schema": record, **({"example": example} if example else {})}}},
                     "responses": {"201": {"description": "Created row"}, **w_errors, "409": {"description": "Key already used"}}},
        },
        f"{base}/records/{{key}}": {
            "get": {"tags": [tag], "operationId": f"{op}_record_get", "summary": "Read a row", "parameters": [key_param],
                    "responses": {"200": {"description": "Row"}, **errors}},
            "put": {"tags": [tag], "operationId": f"{op}_record_put", "summary": "Create or replace a row (a different key in the body renames it)",
                    "description": note, "parameters": [key_param],
                    "requestBody": {"required": True, "content": {"application/json": {"schema": record}}},
                    "responses": {"200": {"description": "Row"}, **w_errors}},
            "patch": {"tags": [tag], "operationId": f"{op}_record_patch", "summary": "Update some columns of a row",
                      "description": note, "parameters": [key_param],
                      "requestBody": {"required": True, "content": {"application/json": {"schema": {**record, "required": []}}}},
                      "responses": {"200": {"description": "Row"}, **w_errors}},
            "delete": {"tags": [tag], "operationId": f"{op}_record_delete", "summary": "Delete a row", "parameters": [key_param],
                       "responses": {"200": {"description": "Deleted"}, **w_errors}},
        },
        f"{base}/records/_bulk": {"post": {
            "tags": [tag], "operationId": f"{op}_records_bulk", "summary": "Synchronise many rows at once",
            "description": note + " `replace_all: true` deletes every row absent from `upsert` (full synchronisation from another system).",
            "requestBody": {"required": True, "content": {"application/json": {"schema": {"type": "object", "properties": {
                "upsert": {"type": "array", "items": record}, "delete": {"type": "array", "items": {"type": "string"}},
                "replace_all": {"type": "boolean", "default": False}}}}}},
            "responses": {"200": {"description": "Counts of created / updated / unchanged / deleted rows"}, **w_errors},
        }},
    }


def build_spec(entries: list[tuple[ReferentialConfig, dict[str, Any], dict[str, Any] | None]], base_url: str, max_limit: int, title: str | None = None) -> dict[str, Any]:
    paths: dict[str, Any] = {}
    schemas: dict[str, Any] = {}
    tags = []
    for ref, meta, sample in entries:
        p, s, t = build_paths(ref, meta, sample, max_limit)
        paths.update(p)
        schemas.update(s)
        tags.append(t)
    if len(entries) == 1:
        ref, meta, _ = entries[0]
        title = title or f"API — {ref.name}"
        version = f"{__version__}+v{meta.get('version') or 0}"
        description = f"{ref.description}\n\n{AUTH_DOC}"
    else:
        title = title or "RefExposer — Referentials API"
        version = __version__
        description = f"Documentation generated automatically from the schema of each accessible referential.\n\n{AUTH_DOC}"
    return {
        "openapi": "3.1.0",
        "info": {"title": title, "version": version, "description": description},
        "servers": [{"url": base_url.rstrip("/") or "/"}],
        "tags": tags,
        "paths": paths,
        "components": {
            "schemas": schemas,
            "securitySchemes": {
                "bearer": {"type": "http", "scheme": "bearer", "description": "Personal API token (rfx_…)"},
                "session": {"type": "apiKey", "in": "cookie", "name": "refex_session", "description": "Browser session"},
            },
        },
        "security": [{"bearer": []}, {"session": []}],
    }


SWAGGER_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css" />
  <style>body {{ margin: 0; }} .topbar {{ display: none; }}</style>
</head>
<body>
  <div id="swagger-ui"></div>
  <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
  <script>
    window.ui = SwaggerUIBundle({{
      url: {spec_url},
      dom_id: '#swagger-ui',
      deepLinking: true,
      persistAuthorization: true,
      displayRequestDuration: true,
      docExpansion: 'list',
      defaultModelsExpandDepth: 0,
      filter: true,
      tryItOutEnabled: true,
      // Session (cookie) calls must carry the anti-CSRF header expected by the backend
      requestInterceptor: (req) => {{ req.headers['X-Requested-With'] = 'RefExposer'; return req; }},
    }});
  </script>
</body>
</html>"""
