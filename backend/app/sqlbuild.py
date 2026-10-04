"""Safe SQL generation helpers (identifiers, literals, readers and filter queries)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


class QueryError(ValueError):
    """Invalid user query (unknown column, bad operator...)."""


def qi(name: str) -> str:
    """Quote an identifier."""
    return '"' + name.replace('"', '""') + '"'


def lit(value: Any) -> str:
    """Render a Python value as a DuckDB literal. Strings use standard SQL quoting
    (DuckDB does not interpret backslashes in standard strings)."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(lit(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{lit(str(k))}: {lit(v)}" for k, v in value.items()) + "}"
    s = str(value).replace("\x00", "")
    return "'" + s.replace("'", "''") + "'"


# --------------------------------------------------------------------------- readers

def parquet_reader(path: str, key_name: str | None = None) -> str:
    """read_parquet() of one published file; `key_name`: encryption key (confidential referential)."""
    return f"read_parquet({lit(path)}" + (f", encryption_config = {{footer_key: {lit(key_name)}}})" if key_name else ")")


def _opts(options: dict[str, Any]) -> str:
    return "".join(f", {k}={lit(v)}" for k, v in options.items())


def reader_expr(fmt: str, files: list[str], options: dict[str, Any] | None = None) -> str:
    """Return a table expression reading `files` according to the referential format."""
    if not files:
        raise ValueError("no source file")
    options = dict(options or {})
    if fmt in ("json", "xml"):
        from .convert import needs_conversion, needs_split, split_records, to_jsonl

        if needs_conversion(fmt, options.get("records_path")):
            # Objects of records and XML are first converted to JSON Lines (cached), then read like any JSON Lines file
            records_path, records_key = options.pop("records_path", None), options.pop("records_key", None)
            files = [str(to_jsonl(Path(f), fmt, records_path, records_key)) for f in files]
            fmt = "jsonl"
        elif needs_split(fmt, options.get("records_path"), files):
            # Large multi-document sources (e.g. every yearly NVD feed): records extracted one document at a time,
            # each line is {"r": record}, giving the same columns as the in-memory unnest below
            records_path = str(options.pop("records_path"))
            options.pop("records_key", None)
            options.pop("format", None)
            options.pop("maximum_object_size", None)  # one record per line: the default limit applies (large values multiply buffers)
            parts = [str(split_records(Path(f), records_path)) for f in files]
            return (f"(SELECT unnest(r, recursive := true) FROM read_json({lit(parts)}, format='newline_delimited', "
                    f"union_by_name=true{_opts(options)}))")
    paths = lit(files)
    if fmt in ("csv", "tsv"):
        if fmt == "tsv":
            options.setdefault("delim", "\t")
        if len(files) > 1:
            options.setdefault("union_by_name", True)
        return f"read_csv({paths}{_opts(options)})"
    if fmt == "txt":
        comment = options.pop("comment", "#")
        column = options.pop("column", "value")
        skip_empty = options.pop("skip_empty", True)
        conds = []
        if comment:
            conds.append(f"NOT starts_with(ltrim(line), {lit(comment)})")
        if skip_empty:
            conds.append("trim(line) <> ''")
        where = f" WHERE {' AND '.join(conds)}" if conds else ""
        return (
            f"(SELECT trim(line) AS {qi(column)} FROM read_csv({paths}, columns={{'line': 'VARCHAR'}}, "
            f"header=false, delim=chr(31), quote='', escape=''{_opts(options)}){where})"
        )
    if fmt in ("json", "jsonl"):
        records_path = options.pop("records_path", None)
        if fmt == "jsonl":
            options.setdefault("format", "newline_delimited")
        else:
            # A JSON document is one object: allow the size of the largest file (DuckDB sizes its buffers on this
            # limit, so a fixed huge value multiplies memory with threads and files)
            biggest = max((Path(f).stat().st_size for f in files if Path(f).exists()), default=0)
            options.setdefault("maximum_object_size", min(max(16 << 20, biggest + (1 << 20)), (1 << 32) - 1))
        reader = f"read_json({paths}{_opts(options)})"
        if records_path:
            expr = ".".join(qi(p) for p in str(records_path).split("."))
            return f"(SELECT unnest(r, recursive := true) FROM (SELECT unnest({expr}) AS r FROM {reader}))"
        return reader
    if fmt == "parquet":
        options.setdefault("union_by_name", True)
        return f"read_parquet({paths}{_opts(options)})"
    if fmt == "xlsx":
        parts = [f"SELECT * FROM read_xlsx({lit(f)}{_opts(options)})" for f in files]
        return "(" + " UNION ALL BY NAME ".join(parts) + ")"
    raise ValueError(f"unsupported format: {fmt}")


def build_source_sql(fmt: str, groups: list[list[str]], options: dict[str, Any], transform: str | None) -> str:
    """Build the final SELECT producing the dataset.

    Placeholders available in transforms:
      {source}   reader over all files        {files}   list literal of all files
      {sourceN}  reader over files of url N   {filesN}  list literal of files of url N
    """
    all_files = [f for g in groups for f in g]
    if not transform:
        return f"SELECT * FROM {reader_expr(fmt, all_files, options)}"
    sql = transform.strip().rstrip(";")
    for i in reversed(range(len(groups))):  # reversed so {source1} is not eaten by {source10}
        if f"{{source{i}}}" in sql:
            sql = sql.replace(f"{{source{i}}}", reader_expr(fmt, groups[i], options))
        sql = sql.replace(f"{{files{i}}}", lit(groups[i]))
    if "{source}" in sql:
        sql = sql.replace("{source}", reader_expr(fmt, all_files, options))
    sql = sql.replace("{files}", lit(all_files))
    return sql


# --------------------------------------------------------------------------- filtering

OPERATORS: dict[str, str] = {
    "eq": "equal to",
    "ne": "different from",
    "gt": "greater than",
    "gte": "greater than or equal to",
    "lt": "less than",
    "lte": "less than or equal to",
    "contains": "contains",
    "ncontains": "does not contain",
    "startswith": "starts with",
    "endswith": "ends with",
    "in": "among (comma separated list)",
    "nin": "not among (comma separated list)",
    "isnull": "is empty (true/false)",
    "regex": "matches the regular expression",
}

RESERVED_PARAMS = {"q", "sort", "limit", "offset", "columns", "format", "count"}

_NUMERIC = re.compile(r"^(TINYINT|SMALLINT|INTEGER|BIGINT|HUGEINT|UTINYINT|USMALLINT|UINTEGER|UBIGINT|UHUGEINT|FLOAT|DOUBLE|DECIMAL.*)$")
_TEMPORAL = re.compile(r"^(DATE|TIME.*|TIMESTAMP.*|INTERVAL)$")


def type_kind(t: str) -> str:
    t = t.upper()
    if t.endswith("]"):
        return "list"
    if t.startswith(("STRUCT", "MAP", "UNION", "JSON")):
        return "struct"
    if _NUMERIC.match(t):
        return "number"
    if _TEMPORAL.match(t):
        return "temporal"
    if t == "BOOLEAN":
        return "boolean"
    return "text"


@dataclass
class Filter:
    column: str
    op: str
    value: str


def parse_filters(params: Iterable[tuple[str, str]]) -> list[Filter]:
    """Turn query params like `year__gte=2000` or `country=FR` into filters."""
    out = []
    for key, value in params:
        if key in RESERVED_PARAMS:
            continue
        column, op = key, "eq"
        if "__" in key:
            head, tail = key.rsplit("__", 1)
            if tail in OPERATORS:
                column, op = head, tail
        out.append(Filter(column, op, value))
    return out


def split_values(value: str) -> list[str]:
    """Values of `in` / `nin`: a JSON array (values may contain commas) or a comma separated list."""
    v = value.strip()
    if v.startswith("["):
        try:
            data = json.loads(v)
            if isinstance(data, list):
                return [str(x) for x in data if x is not None and str(x) != ""]
        except ValueError:
            pass
    return [x.strip() for x in value.split(",") if x.strip()]


def _truthy(v: str) -> bool:
    return v.strip().lower() in ("1", "true", "yes", "")


PREFIX_END = "\U0010ffff"
FAST_OPS = {"eq", "in", "startswith", "gt", "gte", "lt", "lte"}


def prefix_range(col: str, value: str) -> str:
    """Prefix match written as a range: unlike starts_with(lower(...)), DuckDB can use the
    min/max statistics of each row group to skip data (case-sensitive)."""
    c = qi(col)
    return f"({c} >= {lit(value)} AND {c} < {lit(value + PREFIX_END)})"


def _condition(col: str, ctype: str, op: str, value: str, fast: bool = False) -> str:
    c = qi(col)
    kind = type_kind(ctype)
    if fast and kind == "text" and op == "startswith":
        return prefix_range(col, value)
    text = f"CAST({c} AS VARCHAR)"
    low = f"lower({lit(value)})"

    def typed(v: str) -> str:
        return lit(v) if kind == "text" else f"CAST({lit(v)} AS {ctype})"

    if op == "isnull":
        empty = f"({c} IS NULL OR len({c}) = 0)" if kind == "list" else f"{c} IS NULL"
        return empty if _truthy(value) else f"NOT {empty}"
    if kind == "list":
        any_match = lambda pred: f"len(list_filter({c}, x -> {pred})) > 0"  # noqa: E731
        if op == "eq":
            return any_match(f"lower(CAST(x AS VARCHAR)) = {low}")
        if op == "ne":
            return "NOT " + any_match(f"lower(CAST(x AS VARCHAR)) = {low}")
        if op == "contains":
            return any_match(f"contains(lower(CAST(x AS VARCHAR)), {low})")
        if op == "ncontains":
            return "NOT " + any_match(f"contains(lower(CAST(x AS VARCHAR)), {low})")
        if op in ("in", "nin"):
            values = lit([v.lower() for v in split_values(value)])
            pred = any_match(f"list_contains({values}, lower(CAST(x AS VARCHAR)))")
            return pred if op == "in" else "NOT " + pred
    if kind in ("list", "struct") and op in ("eq", "ne", "gt", "gte", "lt", "lte"):
        c, kind = text, "text"

    if op == "eq":
        if kind == "text":
            return f"{c} = {lit(value)}"
        return f"{c} = {typed(value)}"
    if op == "ne":
        return f"{c} IS DISTINCT FROM {typed(value)}"
    if op in ("gt", "gte", "lt", "lte"):
        sym = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[op]
        return f"{c} {sym} {typed(value)}"
    if op == "contains":
        return f"contains(lower({text}), {low})"
    if op == "ncontains":
        return f"({c} IS NULL OR NOT contains(lower({text}), {low}))"
    if op == "startswith":
        return f"starts_with(lower({text}), {low})"
    if op == "endswith":
        return f"ends_with(lower({text}), {low})"
    if op in ("in", "nin"):
        values = split_values(value)
        if not values:
            raise QueryError(f"empty list for {col}__{op}")
        cond = f"{c} IN ({', '.join(typed(v) for v in values)})"
        return cond if op == "in" else f"NOT ({cond})"
    if op == "regex":
        return f"regexp_matches({text}, {lit(value)})"
    raise QueryError(f"unknown operator: {op}")


@dataclass
class TableQuery:
    table: str
    columns: dict[str, str]  # name -> duckdb type (ordered)
    search: str | None = None
    search_columns: list[str] | None = None
    filters: list[Filter] | None = None
    sort: str | None = None
    select: list[str] | None = None
    # Large referentials: sorted copies {column: view}, physical sort order, guardrails
    indexes: dict[str, str] | None = None
    sorted_by: list[str] | None = None
    large: bool = False

    @property
    def fast_columns(self) -> list[str]:
        """Columns on which lookups only read the relevant row groups."""
        cols = list(self.sorted_by[:1]) if self.sorted_by else []
        return cols + [c for c in (self.indexes or {}) if c not in cols]

    @property
    def fast_filter(self) -> str | None:
        """First filter that can use a sort order (column), if any."""
        for f in self.filters or []:
            if f.column in self.fast_columns and f.op in FAST_OPS:
                return f.column
        if self.search and self.search.strip() and self.large:
            cols = [c for c in (self.search_columns or []) if c in self.fast_columns]
            return cols[0] if cols else None
        return None

    def source(self) -> str:
        """Table to read: the copy sorted on the filtered column when there is one."""
        col = self.fast_filter
        if col and self.indexes and col in self.indexes and col not in (self.sorted_by or [])[:1]:
            return self.indexes[col]
        return self.table

    def _check_column(self, name: str) -> str:
        if name not in self.columns:
            raise QueryError(f"unknown column: '{name}'")
        return name

    def where(self, exclude_column: str | None = None) -> str:
        """WHERE clause; `exclude_column` ignores the filters on one column (facet counts)."""
        conds: list[str] = []
        for f in self.filters or []:
            if exclude_column is not None and f.column == exclude_column:
                continue
            self._check_column(f.column)
            if f.op not in OPERATORS:
                raise QueryError(f"unknown operator: '{f.op}' (available: {', '.join(OPERATORS)})")
            conds.append(_condition(f.column, self.columns[f.column], f.op, f.value, fast=f.column in self.fast_columns))
        if self.search and self.search.strip() and self.large:
            # Full-text "contains" would scan everything: prefix search on sorted columns only
            cols = [c for c in (self.search_columns or []) if c in self.fast_columns and type_kind(self.columns.get(c, "")) == "text"]
            if not cols:
                fast = ", ".join(self.fast_columns) or "none"
                raise QueryError(f"full-text search is not available on this volume: filter on an indexed column ({fast})")
            conds.append("(" + " OR ".join(prefix_range(c, self.search.strip()) for c in cols) + ")")
        elif self.search and self.search.strip():
            cols = [c for c in (self.search_columns or []) if c in self.columns]
            if not cols:
                cols = [c for c, t in self.columns.items() if type_kind(t) in ("text", "list")][:30]
            if not cols:
                cols = list(self.columns)[:30]
            low = f"lower({lit(self.search.strip())})"
            ors = [
                f"contains(lower({qi(c) if type_kind(self.columns[c]) == 'text' else f'CAST({qi(c)} AS VARCHAR)'}), {low})"
                for c in cols
            ]
            conds.append("(" + " OR ".join(ors) + ")")
        return (" WHERE " + " AND ".join(conds)) if conds else ""

    def order_by(self) -> str:
        if not self.sort:
            return ""
        parts = []
        for item in self.sort.split(","):
            item = item.strip()
            if not item:
                continue
            desc = item.startswith("-")
            name = self._check_column(item.lstrip("-+"))
            if self.large and not self.fast_filter and not (not desc and self.sorted_by and name == self.sorted_by[0]):
                raise QueryError("sorting the whole of this volume is not available: filter on an indexed column first")
            parts.append(f"{qi(name)} {'DESC' if desc else 'ASC'} NULLS LAST")
        return (" ORDER BY " + ", ".join(parts)) if parts else ""

    def projection(self) -> str:
        if not self.select:
            return "*"
        return ", ".join(qi(self._check_column(c)) for c in self.select)

    def select_sql(self, limit: int | None = None, offset: int = 0) -> str:
        sql = f"SELECT {self.projection()} FROM {qi(self.source())}{self.where()}{self.order_by()}"
        if limit is not None:
            sql += f" LIMIT {int(limit)} OFFSET {int(offset)}"
        return sql

    def count_sql(self) -> str:
        return f"SELECT count(*) FROM {qi(self.source())}{self.where()}"
