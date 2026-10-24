#!/usr/bin/env python3
"""Create an internal referential in RefExposer and feed it, row by row or in bulk.

An internal referential has no remote source: its rows live in RefExposer and are written through this API.
Only the standard library is used.

    export REFEX_URL=https://refexposer.example.com
    export REFEX_TOKEN=rfx_...                 # service account token, with the manage right

    refex-internal.py create  sites "Sites" --key code \\
                              --column code:text:required --column label:text --column owner:text \\
                              --column people:integer --column opened:date --column active:boolean \\
                              --column tags:list --column comment:text
    refex-internal.py columns sites                       # schema of the referential
    refex-internal.py add     sites code=LYO label=Lyon owner=network people=120 active=true tags=prod,eu
    refex-internal.py set     sites LYO label="Lyon Part-Dieu" owner=network   # create or replace the whole row
    refex-internal.py patch   sites LYO owner=secops                           # change some columns only
    refex-internal.py load    sites sites.csv             # add / update from a CSV, JSON or JSON Lines file
    refex-internal.py sync    sites sites.csv             # same, and delete every row absent from the file
    refex-internal.py rows    sites --limit 20
    refex-internal.py delete  sites LYO

Column types: text, integer, number, boolean, date (YYYY-MM-DD), datetime (ISO 8601), list (comma separated).
`--column name:type:required` makes a value mandatory. `--key` must name one of the columns; add `:auto` to a text
key (e.g. `--column id:text:auto`) to have RefExposer generate a UUID when a row is added without it.
"""

import argparse
import csv
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("REFEX_URL", "http://localhost:8080").rstrip("/")
TOKEN = os.environ.get("REFEX_TOKEN", "")
# The API accepts 50,000 changes per request; smaller batches keep each request short
BATCH = 10_000
TYPES = ("text", "integer", "number", "boolean", "date", "datetime", "list")


class ApiError(RuntimeError):
    pass


def request(method, path, body=None):
    """One API call. Returns the parsed JSON answer (None when the answer is empty)."""
    if not TOKEN:
        raise ApiError("set REFEX_TOKEN to an API token with the manage right (service account recommended)")
    data, headers = None, {"Authorization": f"Bearer {TOKEN}", "Accept": "application/json"}
    if body is not None:
        data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        try:
            detail = json.loads(detail).get("detail", detail)
        except ValueError:
            pass
        raise ApiError(f"HTTP {e.code} on {method} {path}: {detail}") from e
    except urllib.error.URLError as e:
        raise ApiError(f"cannot reach {BASE}: {e.reason}") from e
    return json.loads(raw) if raw else None


def quote(value):
    return urllib.parse.quote(str(value), safe="")


# --------------------------------------------------------------------------- rows


def parse_values(pairs):
    """`col=value` arguments into a row. A list column takes comma separated values."""
    row = {}
    for pair in pairs:
        if "=" not in pair:
            raise ApiError(f"expected column=value, got '{pair}'")
        name, value = pair.split("=", 1)
        row[name.strip()] = value
    return row


def read_file(path):
    """Rows of a .csv, .tsv, .json (array of objects, or {\"records\": [...]}) or .jsonl file."""
    suffix = os.path.splitext(path)[1].lower()
    if suffix in (".csv", ".tsv"):
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f, delimiter="\t" if suffix == ".tsv" else ",")
            if not reader.fieldnames:
                raise ApiError(f"{path}: no header row")
            # A trailing empty column (line ending with a separator) would be an unknown column
            return [{k: v for k, v in row.items() if k} for row in reader]
    with open(path, encoding="utf-8") as f:
        if suffix in (".jsonl", ".ndjson"):
            return [json.loads(line) for line in f if line.strip()]
        doc = json.load(f)
    rows = doc.get("records", doc.get("rows")) if isinstance(doc, dict) else doc
    if not isinstance(rows, list):
        raise ApiError(f"{path}: expected an array of objects, or an object with a 'records' array")
    return rows


def keep_known(rows, columns):
    """Drop the fields the referential does not have (an extra column is refused by the API)."""
    known = {c["name"] for c in columns}
    extra = sorted({k for row in rows for k in row} - known)
    if extra:
        print(f"ignored column(s): {', '.join(extra)}", file=sys.stderr)
    return [{k: v for k, v in row.items() if k in known} for row in rows]


# --------------------------------------------------------------------------- commands


def create(args):
    columns = []
    for spec in args.column:
        parts = spec.split(":")
        name, kind, flags = parts[0], (parts[1] if len(parts) > 1 and parts[1] else "text"), parts[2:]
        if kind not in TYPES:
            raise ApiError(f"column '{name}': unknown type '{kind}' (one of {', '.join(TYPES)})")
        columns.append({"name": name, "type": kind, "required": "required" in flags, "auto": "auto" in flags})
    if not columns:
        raise ApiError("give at least one --column name:type")
    key = args.key or columns[0]["name"]
    if key not in [c["name"] for c in columns]:
        raise ApiError(f"the key '{key}' must be one of the columns")
    body = {"id": args.id, "name": args.name, "key": key, "columns": columns,
            "description": args.description or "", "category": args.category,
            "confidential": args.confidential, "search_columns": args.search or []}
    out = request("POST", "/api/internal-referentials", body)
    print(f"created '{out['id']}': key {key}, columns {', '.join(c['name'] for c in columns)}")


def columns_cmd(args):
    out = request("GET", f"/api/referentials/{quote(args.id)}/records?limit=1")
    print(f"key: {out['key']}")
    for c in out["columns"]:
        flags = [f for f, on in (("required", c["required"]), ("generated", c.get("auto"))) if on]
        print(f"  {c['name']}: {c['type']}" + (f" ({', '.join(flags)})" if flags else ""))
    print(f"{out['total']} row(s)")


def add(args):
    row = request("POST", f"/api/referentials/{quote(args.id)}/records?upsert={str(args.upsert).lower()}",
                  parse_values(args.values))
    print(json.dumps(row, indent=2, ensure_ascii=False))


def set_row(args):
    row = request("PUT", f"/api/referentials/{quote(args.id)}/records/{quote(args.key)}", parse_values(args.values))
    print(json.dumps(row, indent=2, ensure_ascii=False))


def patch(args):
    row = request("PATCH", f"/api/referentials/{quote(args.id)}/records/{quote(args.key)}", parse_values(args.values))
    print(json.dumps(row, indent=2, ensure_ascii=False))


def delete(args):
    request("DELETE", f"/api/referentials/{quote(args.id)}/records/{quote(args.key)}")
    print(f"deleted {args.key}")


def load(args):
    """Bulk upsert. `--replace` deletes every row absent from the file (full synchronisation)."""
    schema = request("GET", f"/api/referentials/{quote(args.id)}/records?limit=1")
    rows = keep_known(read_file(args.file), schema["columns"])
    if not rows:
        raise ApiError(f"{args.file}: no row")
    totals = {"created": 0, "updated": 0, "unchanged": 0, "deleted": 0}
    for start in range(0, len(rows), BATCH):
        chunk = rows[start:start + BATCH]
        # replace_all is only sent with the first batch: the next ones add to it
        body = {"upsert": chunk, "replace_all": args.replace and start == 0}
        result = request("POST", f"/api/referentials/{quote(args.id)}/records/_bulk", body)
        for k in totals:
            totals[k] += result.get(k, 0)
        print(f"{min(start + BATCH, len(rows)):,}/{len(rows):,} rows sent", file=sys.stderr)
    print(", ".join(f"{v:,} {k}" for k, v in totals.items()))


def rows_cmd(args):
    out = request("GET", f"/api/referentials/{quote(args.id)}/records?limit={int(args.limit)}"
                         + (f"&q={quote(args.search)}" if args.search else ""))
    for row in out["records"]:
        print(json.dumps({k: v for k, v in row.items() if not k.startswith("_")}, ensure_ascii=False))
    print(f"{len(out['records'])} of {out['total']} row(s)", file=sys.stderr)


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("create", help="create an internal referential")
    p.add_argument("id")
    p.add_argument("name")
    p.add_argument("--column", action="append", default=[], metavar="NAME:TYPE[:required|:auto]")
    p.add_argument("--key", help="column identifying a row (default: the first one)")
    p.add_argument("--description", default="")
    p.add_argument("--category", default="Internal")
    p.add_argument("--search", action="append", help="column searched by the full-text search (repeatable)")
    p.add_argument("--confidential", action="store_true", help="rows encrypted at rest")
    p.set_defaults(func=create)

    p = sub.add_parser("columns", help="show the schema and the number of rows")
    p.add_argument("id")
    p.set_defaults(func=columns_cmd)

    p = sub.add_parser("add", help="add one row (fails if the key already exists)")
    p.add_argument("id")
    p.add_argument("values", nargs="+", metavar="column=value")
    p.add_argument("--upsert", action="store_true", help="update the row instead of failing")
    p.set_defaults(func=add)

    p = sub.add_parser("set", help="create or replace a row by its key")
    p.add_argument("id")
    p.add_argument("key")
    p.add_argument("values", nargs="*", metavar="column=value")
    p.set_defaults(func=set_row)

    p = sub.add_parser("patch", help="change some columns of a row")
    p.add_argument("id")
    p.add_argument("key")
    p.add_argument("values", nargs="+", metavar="column=value")
    p.set_defaults(func=patch)

    p = sub.add_parser("delete", help="delete a row by its key")
    p.add_argument("id")
    p.add_argument("key")
    p.set_defaults(func=delete)

    p = sub.add_parser("load", help="add / update rows from a CSV, JSON or JSON Lines file")
    p.add_argument("id")
    p.add_argument("file")
    p.add_argument("--replace", action="store_true", help="delete every row absent from the file")
    p.set_defaults(func=load)

    p = sub.add_parser("sync", help="same as load --replace")
    p.add_argument("id")
    p.add_argument("file")
    p.set_defaults(func=load, replace=True)

    p = sub.add_parser("rows", help="show the rows")
    p.add_argument("id")
    p.add_argument("--limit", default=50)
    p.add_argument("--search", help="text searched in the rows")
    p.set_defaults(func=rows_cmd)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except ApiError as e:
        sys.exit(str(e))
    return 0


if __name__ == "__main__":
    main()
