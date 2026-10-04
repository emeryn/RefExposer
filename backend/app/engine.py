"""DuckDB query engine. Each referential with data is exposed as a view over its parquet file.

Two in-memory databases are kept:
- `main`: used by the API (generated, validated queries) and exports;
- `sandbox(tables)`: used by the SQL console, one per permission profile. Only the parquet
  files of the visible tables are readable, external access is disabled, the configuration
  is locked and only single SELECT statements are accepted.
"""

from __future__ import annotations

import datetime as dt
import decimal
import logging
import math
import threading
import uuid
from pathlib import Path
from typing import Any

import duckdb

from .logsetup import TRACE
from .sqlbuild import lit, parquet_reader, qi

log = logging.getLogger(__name__)


class SqlError(Exception):
    pass


class SqlTimeout(SqlError):
    pass


def _jsonable(v: Any) -> Any:
    if v is None or isinstance(v, (str, bool, int)):
        return v
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if isinstance(v, decimal.Decimal):
        f = float(v)
        return f if math.isfinite(f) else None
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        return v.isoformat()
    if isinstance(v, dt.timedelta):
        return str(v)
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, (bytes, bytearray, memoryview)):
        return bytes(v).hex()
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    return str(v)


def rows_to_records(columns: list[str], rows: list[tuple]) -> list[dict[str, Any]]:
    return [{c: _jsonable(v) for c, v in zip(columns, row)} for row in rows]


def rows_to_lists(rows: list[tuple]) -> list[list[Any]]:
    return [[_jsonable(v) for v in row] for row in rows]


class Engine:
    def __init__(self, data_dir: Path, memory_limit: str | None = None, threads: int | None = None, query_timeout: float | None = None):
        self.data_dir = data_dir.resolve()
        self.query_timeout = query_timeout
        # table -> {column: view name} for the sorted copies ("indexes")
        self.index_views: dict[str, dict[str, str]] = {}
        self.tmp_dir = self.data_dir / ".tmp"
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.views: dict[str, Path] = {}
        # Encrypted tables (confidential referentials): view -> (key name, key)
        self.keys: dict[str, tuple[str, str]] = {}

        self.main = duckdb.connect()
        self._configure(self.main, memory_limit, threads)
        self.excel = self._try_load(self.main, "excel")

        self._memory_limit, self._threads = memory_limit, threads
        # One sandbox connection per set of visible tables (i.e. per permission profile)
        self._sandboxes: dict[frozenset[str], duckdb.DuckDBPyConnection] = {}

    def _configure(self, con: duckdb.DuckDBPyConnection, memory_limit: str | None, threads: int | None, temp: str = "duckdb") -> None:
        con.execute(f"SET temp_directory = {lit(str(self.tmp_dir / temp))}")
        if memory_limit:
            con.execute(f"SET memory_limit = {lit(memory_limit)}")
        if threads:
            con.execute(f"SET threads = {int(threads)}")

    @staticmethod
    def _try_load(con: duckdb.DuckDBPyConnection, ext: str) -> bool:
        try:
            con.execute(f"LOAD {ext}")
            return True
        except duckdb.Error:
            try:
                con.execute(f"INSTALL {ext}; LOAD {ext}")
                return True
            except duckdb.Error as e:
                log.warning("DuckDB extension %s unavailable: %s", ext, e)
                return False

    # ------------------------------------------------------------------ views
    @staticmethod
    def _view_sql(table: str, parquet: Path, key: tuple[str, str] | None = None) -> str:
        return f"CREATE OR REPLACE VIEW {qi(table)} AS SELECT * FROM {parquet_reader(str(parquet.resolve()), key[0] if key else None)}"

    @staticmethod
    def add_key(con: duckdb.DuckDBPyConnection, key: tuple[str, str]) -> None:
        con.execute(f"PRAGMA add_parquet_key({lit(key[0])}, {lit(key[1])})")

    @staticmethod
    def index_view(table: str, view_suffix: str) -> str:
        return f"{table}__by_{view_suffix}"

    def register(self, table: str, parquet: Path, indexes: dict[str, tuple[str, Path]] | None = None,
                 key: tuple[str, str] | None = None) -> None:
        """Expose `table`, plus one view per sorted copy: indexes = {column: (view suffix, path)}.
        `key`: the files are encrypted (confidential referential), read with this key."""
        with self._lock:
            if key:
                self.add_key(self.main, key)
            self.main.execute(self._view_sql(table, parquet, key))
            new_idx = {}
            for col, (suffix, path) in (indexes or {}).items():
                view = self.index_view(table, suffix)
                self.main.execute(self._view_sql(view, path, key))
                self.views[view] = path
                new_idx[col] = view
            changed_key = self.keys.get(table) != key
            for view in [table, *new_idx.values()]:
                if key:
                    self.keys[view] = key
                else:
                    self.keys.pop(view, None)
            for col, view in self.index_views.get(table, {}).items():
                if view not in new_idx.values():
                    self.main.execute(f"DROP VIEW IF EXISTS {qi(view)}")
                    self.views.pop(view, None)
            changed = self.views.get(table) != parquet or new_idx != self.index_views.get(table, {}) or changed_key
            self.views[table] = parquet
            self.index_views[table] = new_idx
            if changed:
                self._drop_sandboxes()

    def unregister(self, table: str) -> None:
        with self._lock:
            for view in [table, *self.index_views.pop(table, {}).values()]:
                self.main.execute(f"DROP VIEW IF EXISTS {qi(view)}")
                self.views.pop(view, None)
                self.keys.pop(view, None)
            self._drop_sandboxes()

    def _drop_sandboxes(self) -> None:
        for con in self._sandboxes.values():
            try:
                con.close()
            except duckdb.Error:
                pass
        self._sandboxes.clear()

    def sandbox(self, tables: frozenset[str]) -> duckdb.DuckDBPyConnection:
        """Locked-down connection exposing only `tables`. Only their parquet files are readable
        (`allowed_paths`), so other referentials cannot be reached through read_parquet()."""
        with self._lock:
            # Sorted copies of a visible table are visible too
            tables = frozenset(
                v for t in tables if t in self.views for v in (t, *self.index_views.get(t, {}).values())
            )
            con = self._sandboxes.get(tables)
            if con is not None:
                return con
            con = duckdb.connect()
            self._configure(con, self._memory_limit, self._threads, temp="duckdb-sandbox")
            paths = []
            for t in sorted(tables):
                if t in self.keys:  # before the configuration is locked
                    self.add_key(con, self.keys[t])
                con.execute(self._view_sql(t, self.views[t], self.keys.get(t)))
                paths.append(str(self.views[t].resolve()))
            con.execute("SET allowed_directories = []")
            if paths:
                con.execute(f"SET allowed_paths = {lit(paths)}")
            con.execute("SET enable_external_access = false")
            con.execute("SET lock_configuration = true")
            if len(self._sandboxes) >= 64:
                self._drop_sandboxes()
            self._sandboxes[tables] = con
            return con

    def describe(self, table: str) -> list[dict[str, str]]:
        rows = self.cursor().execute(f"DESCRIBE {qi(table)}").fetchall()
        return [{"name": r[0], "type": r[1]} for r in rows]

    # ------------------------------------------------------------------ queries
    def cursor(self) -> duckdb.DuckDBPyConnection:
        return self.main.cursor()

    def _guarded(self, cur: duckdb.DuckDBPyConnection, fn):
        """Run fn() and interrupt it after query_timeout seconds."""
        if not self.query_timeout:
            return fn()
        timed_out = threading.Event()

        def _interrupt() -> None:
            timed_out.set()
            cur.interrupt()

        timer = threading.Timer(self.query_timeout, _interrupt)
        timer.start()
        try:
            return fn()
        except duckdb.Error as e:
            if timed_out.is_set():
                raise SqlTimeout(f"query stopped after {self.query_timeout:.0f}s: narrow the filters (indexed columns)") from e
            raise
        finally:
            timer.cancel()

    def fetch(self, sql: str) -> tuple[list[str], list[tuple]]:
        log.log(TRACE, "SQL: %s", sql)
        cur = self.cursor()
        try:
            def run():
                res = cur.execute(sql)
                cols = [d[0] for d in res.description] if res.description else []
                return cols, res.fetchall()
            return self._guarded(cur, run)
        finally:
            cur.close()

    def scalar(self, sql: str) -> Any:
        log.log(TRACE, "SQL: %s", sql)
        cur = self.cursor()
        try:
            row = self._guarded(cur, lambda: cur.execute(sql).fetchone())
            return row[0] if row else None
        finally:
            cur.close()

    def run_sandboxed(self, sql: str, max_rows: int, timeout: float, tables: frozenset[str]) -> dict[str, Any]:
        """Execute a user supplied read-only query in a sandbox exposing only `tables`."""
        sql = sql.strip().rstrip(";").strip()
        if not sql:
            raise SqlError("empty query")
        sandbox = self.sandbox(tables)
        try:
            statements = sandbox.extract_statements(sql)
        except duckdb.Error as e:
            raise SqlError(str(e)) from e
        if len(statements) != 1:
            raise SqlError("only one query at a time is allowed")
        if statements[0].type != duckdb.StatementType.SELECT:
            raise SqlError("only read queries (SELECT, WITH, DESCRIBE, SUMMARIZE...) are allowed")

        cur = sandbox.cursor()
        timed_out = threading.Event()

        def _interrupt() -> None:
            timed_out.set()
            cur.interrupt()

        timer = threading.Timer(timeout, _interrupt)
        start = dt.datetime.now()
        timer.start()
        try:
            res = cur.execute(sql)
            columns = [d[0] for d in res.description] if res.description else []
            types = [str(d[1]) for d in res.description] if res.description else []
            rows = res.fetchmany(max_rows + 1)
        except duckdb.Error as e:
            if timed_out.is_set():
                raise SqlTimeout(f"timeout exceeded ({timeout:.0f}s)") from e
            raise SqlError(str(e)) from e
        finally:
            timer.cancel()
            cur.close()
        truncated = len(rows) > max_rows
        rows = rows[:max_rows]
        return {
            "columns": [{"name": c, "type": t} for c, t in zip(columns, types)],
            "rows": rows_to_lists(rows),
            "row_count": len(rows),
            "truncated": truncated,
            "elapsed_ms": round((dt.datetime.now() - start).total_seconds() * 1000, 1),
        }

    def export(self, sql: str, fmt: str) -> Path:
        """Write the result of `sql` to a temporary file and return its path."""
        exports = self.tmp_dir / "exports"
        exports.mkdir(parents=True, exist_ok=True)
        ext = {"csv": "csv", "json": "json", "jsonl": "jsonl", "parquet": "parquet", "xlsx": "xlsx"}[fmt]
        target = exports / f"{uuid.uuid4().hex}.{ext}"
        options = {
            "csv": "FORMAT csv, HEADER true",
            "json": "FORMAT json, ARRAY true",
            "jsonl": "FORMAT json",
            "parquet": "FORMAT parquet, COMPRESSION zstd",
            "xlsx": "FORMAT xlsx, HEADER true",
        }[fmt]
        if fmt == "xlsx" and not self.excel:
            raise SqlError("Excel export unavailable (DuckDB 'excel' extension not loaded)")
        cur = self.cursor()
        try:
            cur.execute(f"COPY ({sql}) TO {lit(str(target))} ({options})")
        finally:
            cur.close()
        return target
