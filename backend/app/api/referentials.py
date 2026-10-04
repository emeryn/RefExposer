from __future__ import annotations

import re
import shutil
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Any, Literal

import duckdb
from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Path as PathParam, Query, Request, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from sqlalchemy.orm import Session

from ..auth import CurrentUser, audit, require_user
from ..config import ReferentialConfig
from ..db import get_db
from ..engine import SqlError, SqlTimeout, rows_to_records
from ..service import Service
from ..sqlbuild import OPERATORS, QueryError, TableQuery, lit, parquet_reader, parse_filters, qi, type_kind
from ..storage import RefPaths
from .deps import get_ref, get_ref_with_data, get_service, require_table
from ..bloom import normalize as bloom_normalize
from ..geoip import MmdbError, looks_like_ip
from ..geoip import flatten as geoip_flatten

router = APIRouter(tags=["referentials"])

_stats_cache: dict[tuple, dict[str, Any]] = {}
_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="query")


class _ResultCache:
    """Results that only depend on the published version (counts, facets): computed once per version.
    Keys include the version and the fingerprint, so a new publication never serves stale results."""

    def __init__(self, size: int = 1024):
        self.size = size
        self._data: OrderedDict[tuple, Any] = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def key(service: Service, ref_id: str, *parts: Any) -> tuple:
        meta = service.metas.get(ref_id, {})
        return (ref_id, meta.get("version"), meta.get("fingerprint"), *parts)

    def get(self, key: tuple, compute):
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                return self._data[key]
        value = compute()
        with self._lock:
            self._data[key] = value
            while len(self._data) > self.size:
                self._data.popitem(last=False)
        return value


_results = _ResultCache()
SEARCH_CAP = 1000  # global search: matches counted per referential


def _columns(service: Service, ref: ReferentialConfig) -> dict[str, str]:
    cols = service.metas.get(ref.id, {}).get("columns")
    if not cols:
        cols = service.engine.describe(ref.table)
    return {c["name"]: c["type"] for c in cols}


def _table_query(service: Service, ref: ReferentialConfig, request: Request, q: str | None, sort: str | None, columns: str | None) -> TableQuery:
    return TableQuery(
        table=ref.table,
        columns=_columns(service, ref),
        search=q,
        search_columns=ref.search_columns,
        filters=parse_filters(request.query_params.multi_items()),
        sort=sort,
        select=[c for c in columns.split(",") if c] if columns else None,
        **_layout(service, ref),
    )


def _layout(service: Service, ref: ReferentialConfig) -> dict[str, Any]:
    return {
        "indexes": service.engine.index_views.get(ref.table, {}),
        "sorted_by": ref.storage.sort_by,
        "large": service.is_large(ref.id),
    }


def _key_source(service: Service, ref: ReferentialConfig) -> tuple[str, str]:
    """(table or sorted copy to read, condition template) for exact lookups on the key."""
    cols = _columns(service, ref)
    view = service.engine.index_views.get(ref.table, {}).get(ref.key) or ref.table
    direct = type_kind(cols.get(ref.key, "VARCHAR")) == "text"
    return view, (qi(ref.key) if direct else f"CAST({qi(ref.key)} AS VARCHAR)")


def _run_query(fn):
    try:
        return fn()
    except QueryError as e:
        raise HTTPException(400, str(e)) from e
    except (duckdb.ConversionException, duckdb.BinderException, duckdb.InvalidInputException) as e:
        raise HTTPException(400, f"invalid request: {e}") from e
    except SqlTimeout as e:
        raise HTTPException(408, str(e)) from e
    except SqlError as e:
        raise HTTPException(400, str(e)) from e


# --------------------------------------------------------------------------- catalogue

@router.get("/referentials", summary="Referentials and their status")
def list_referentials(service: Service = Depends(get_service), user: CurrentUser = Depends(require_user)) -> list[dict[str, Any]]:
    return [service.summary(r, user.access(r.id)) for r in service.refs.values() if user.can_read(r.id)]


@router.get("/referentials/{ref_id}", summary="Referential detail (schema, profile, configuration)")
def get_referential(ref_id: str, service: Service = Depends(get_service), user: CurrentUser = Depends(require_user)) -> dict[str, Any]:
    return service.detail(get_ref(service, ref_id, user), user.access(ref_id))


@router.get("/referentials/{ref_id}/runs", summary="Update history")
def get_runs(ref_id: str, limit: int = Query(50, ge=1, le=500), service: Service = Depends(get_service), user: CurrentUser = Depends(require_user)):
    get_ref(service, ref_id, user)
    return service.runs(ref_id, limit)


@router.post("/referentials/{ref_id}/refresh", summary="Start an update")
def refresh(
    ref_id: str,
    request: Request,
    force: bool = Query(False, description="Rebuild even if the source has not changed"),
    service: Service = Depends(get_service),
    user: CurrentUser = Depends(require_user),
    db: Session = Depends(get_db),
):
    get_ref(service, ref_id, user, "manage")
    if service.is_pinned(ref_id):
        raise HTTPException(409, "version frozen after a manual import: resume automatic updates before starting one")
    run = service.submit(ref_id, "manual", force, user.username)
    audit(db, "referential.refresh", user=user, target=f"ref:{ref_id}", request=request, detail={"force": force, "run": run.id})
    return run.to_dict()


@router.post("/referentials/{ref_id}/import", summary="Manual import: new version from uploaded files")
def manual_import(
    ref_id: str,
    request: Request,
    files: list[UploadFile] = File(...),
    pin: bool = Form(False, description="Freeze this version (suspends automatic updates)"),
    service: Service = Depends(get_service),
    user: CurrentUser = Depends(require_user),
    db: Session = Depends(get_db),
):
    ref = get_ref(service, ref_id, user, "manage")
    if ref.source.type == "internal":
        raise HTTPException(400, "internal referential: feed it with the editor or the records API")
    from ..preview import store_upload
    from ..service import BusyError

    if ref_id in service.active:
        raise HTTPException(409, "an update is already running for this referential")
    stored = store_upload(service.settings, [(f.filename or "file", f.file) for f in files])
    staging = service.settings.tmp_dir / "uploads" / stored["upload_id"]
    target = RefPaths(service.settings.data_dir, ref_id).root / "manual" / stored["upload_id"]
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(staging), str(target))
    try:
        run = service.import_files(ref_id, target, "upload", user.username, pin)
    except BusyError as e:
        shutil.rmtree(target, ignore_errors=True)
        raise HTTPException(409, str(e)) from e
    audit(db, "referential.import", user=user, target=f"ref:{ref_id}", request=request,
          detail={"files": [f["name"] for f in stored["files"]], "pin": pin, "run": run.id})
    return run.to_dict()


@router.delete("/referentials/{ref_id}/pin", summary="Resume automatic updates (frozen version)")
def unpin(ref_id: str, request: Request, service: Service = Depends(get_service), user: CurrentUser = Depends(require_user), db: Session = Depends(get_db)):
    get_ref(service, ref_id, user, "manage")
    service.set_pinned(ref_id, None)
    audit(db, "referential.unpin", user=user, target=f"ref:{ref_id}", request=request)
    return {"pinned": False}


@router.delete("/referentials/{ref_id}/run", summary="Cancel the running update")
def cancel(ref_id: str, request: Request, service: Service = Depends(get_service), user: CurrentUser = Depends(require_user), db: Session = Depends(get_db)):
    get_ref(service, ref_id, user, "manage")
    if not service.cancel(ref_id):
        raise HTTPException(404, "no update running")
    audit(db, "referential.cancel", user=user, target=f"ref:{ref_id}", request=request)
    return {"cancelled": True}


# --------------------------------------------------------------------------- data

@router.get(
    "/referentials/{ref_id}/rows",
    summary="Query the data",
    description=(
        "Filters with `column=value` or `column__operator=value` parameters. "
        f"Operators: {', '.join(f'`{k}` ({v})' for k, v in OPERATORS.items())}. "
        "`q` runs a full-text search, `sort=col,-other` sorts, `columns=a,b` restricts the columns."
    ),
)
def get_rows(
    ref_id: str,
    request: Request,
    q: str | None = None,
    sort: str | None = None,
    columns: str | None = None,
    limit: int = Query(50, ge=0),
    offset: int = Query(0, ge=0),
    count: bool = True,
    service: Service = Depends(get_service),
    user: CurrentUser = Depends(require_user),
):
    ref = require_table(get_ref_with_data(service, ref_id, user))
    limit = min(limit, service.settings.api_max_limit)
    tq = _table_query(service, ref, request, q, sort, columns)
    t0 = time.perf_counter()

    def run():
        where = tq.where()
        if not count or (tq.large and where and not tq.fast_filter):
            # Counting would scan the whole volume: the total is reported as unknown
            return (*service.engine.fetch(tq.select_sql(limit, offset)), None)
        if not where and service.metas.get(ref.id, {}).get("row_count") is not None:
            return (*service.engine.fetch(tq.select_sql(limit, offset)), service.metas[ref.id]["row_count"])
        # Page and total count are computed concurrently (each on its own cursor); the count is kept per version
        page = _pool.submit(service.engine.fetch, tq.select_sql(limit, offset))
        count_sql = tq.count_sql()
        total = _results.get(_results.key(service, ref.id, count_sql), lambda: service.engine.scalar(count_sql))
        return (*page.result(), total)

    cols, rows, total = _run_query(run)
    all_cols = _columns(service, ref)
    return {
        "columns": [{"name": c, "type": all_cols.get(c, "")} for c in cols],
        "rows": rows_to_records(cols, rows),
        "total": total,
        "limit": limit,
        "offset": offset,
        "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
    }


@router.get("/referentials/{ref_id}/export", summary="Export the data (with the same filters as /rows)")
def export(
    ref_id: str,
    request: Request,
    format: Literal["csv", "json", "jsonl", "parquet", "xlsx"] = "csv",
    q: str | None = None,
    sort: str | None = None,
    columns: str | None = None,
    limit: int | None = Query(None, ge=1),
    service: Service = Depends(get_service),
    user: CurrentUser = Depends(require_user),
    db: Session = Depends(get_db),
):
    ref = require_table(get_ref_with_data(service, ref_id, user))
    max_rows = service.settings.export_max_rows
    if format == "xlsx":
        max_rows = min(max_rows, 1_048_575)
    limit = min(limit or max_rows, max_rows)
    tq = _table_query(service, ref, request, q, sort, columns)
    path = _run_query(lambda: service.engine.export(tq.select_sql(limit, 0), format))
    filename = f"{ref.id}-{date.today().isoformat()}.{path.suffix.lstrip('.')}"
    audit(db, "referential.export", user=user, target=f"ref:{ref.id}", request=request,
          detail={"format": format, "query": str(request.query_params)[:1000]})
    media = {
        "csv": "text/csv",
        "json": "application/json",
        "jsonl": "application/x-ndjson",
        "parquet": "application/vnd.apache.parquet",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }[format]
    return FileResponse(path, media_type=media, filename=filename, background=BackgroundTask(path.unlink, missing_ok=True))


@router.get("/referentials/{ref_id}/lookup/{value:path}", summary="Exact lookup by key (IP address for a MaxMind DB)")
def lookup(ref_id: str, value: str, all: bool = False, enrich: bool = False,
           flat: bool = Query(False, description="MaxMind DB: dotted keys (country.iso_code...), English names only"),
           service: Service = Depends(get_service), user: CurrentUser = Depends(require_user)):
    ref = get_ref_with_data(service, ref_id, user)
    if ref.is_mmdb:
        rec = mmdb_lookup(service, ref, [value], flat, strict=True)[value]
        if rec is None:
            raise HTTPException(404, f"no network containing '{value}' in {ref_id}")
        return [rec] if all else rec
    if ref.is_bloom:
        rec = bloom_lookup(service, ref, [value], enrich)[bloom_normalize(value, ref.options.get("normalize", "upper"))]
        if rec is None:
            raise HTTPException(404, f"'{value}' is absent from the filter {ref_id}")
        return [rec] if all else rec
    if not ref.key:
        raise HTTPException(400, f"the referential '{ref_id}' has no key")
    view, cond = _key_source(service, ref)
    sql = f"SELECT * FROM {qi(view)} WHERE {cond} = {lit(value)} LIMIT 100"
    cols, rows = _run_query(lambda: service.engine.fetch(sql))
    if not rows:
        raise HTTPException(404, f"no entry '{value}' in {ref_id}")
    records = rows_to_records(cols, rows)
    return records if all else records[0]


@router.post("/referentials/{ref_id}/lookup", summary="Batch lookup (enrichment)")
def lookup_batch(
    ref_id: str,
    values: list[str] = Body(..., embed=True, max_length=10_000),
    column: str | None = Body(None, embed=True, description="Lookup column (the key by default)"),
    enrich: bool = Body(False, embed=True, description="Bloom filter: complete the present values with the configured online API"),
    flat: bool = Body(False, embed=True, description="MaxMind DB: dotted keys (country.iso_code...), English names only"),
    service: Service = Depends(get_service),
    user: CurrentUser = Depends(require_user),
):
    ref = get_ref_with_data(service, ref_id, user)
    if ref.is_mmdb:
        results = mmdb_lookup(service, ref, [str(v) for v in values], flat)
        missing = [k for k, v in results.items() if v is None]
        return {"found": len(results) - len(missing), "missing": missing, "results": results, "column": "ip"}
    if ref.is_bloom:
        results = bloom_lookup(service, ref, [str(v) for v in values], enrich)
        missing = [k for k, v in results.items() if v is None]
        return {"found": len(results) - len(missing), "missing": missing, "results": results, "column": "value"}
    column = column or ref.key
    if not column:
        raise HTTPException(400, f"the referential '{ref_id}' has no key: give the column")
    if column not in _columns(service, ref):
        raise HTTPException(400, f"unknown column: '{column}'")
    wanted = list(dict.fromkeys(str(v).strip() for v in values if str(v).strip()))
    if not wanted:
        return {"found": 0, "missing": [], "results": {}, "column": column}
    k = qi(column)
    cols = _columns(service, ref)
    view = service.engine.index_views.get(ref.table, {}).get(column) or ref.table
    if type_kind(cols[column]) == "text":
        # Constant IN list: pushed down to the parquet scan (row groups skipped on sorted data)
        sql = f"SELECT {k} AS __key, * FROM {qi(view)} WHERE {k} IN ({', '.join(lit(v) for v in wanted)})"
    else:
        sql = (
            f"SELECT CAST(t.{k} AS VARCHAR) AS __key, t.* FROM {qi(view)} t "
            f"SEMI JOIN (SELECT unnest({lit(wanted)}) AS v) w ON CAST(t.{k} AS VARCHAR) = w.v"
        )
    cols, rows = _run_query(lambda: service.engine.fetch(sql))
    results: dict[str, Any] = {v: None for v in wanted}
    for rec in rows_to_records(cols, rows):
        key = rec.pop("__key")
        if results.get(key) is None:
            results[key] = rec
    missing = [k for k, v in results.items() if v is None]
    return {"found": len(wanted) - len(missing), "missing": missing, "results": results, "column": column}


def mmdb_lookup(service: Service, ref: ReferentialConfig, values: list[str], flat: bool = False, strict: bool = False) -> dict[str, Any]:
    """IP lookups in a MaxMind DB: {ip: record of the network containing it, or None}. Values that are not IP
    addresses are missing (strict: 400)."""
    db = service.mmdb(ref)
    results: dict[str, Any] = {}
    for v in dict.fromkeys(str(x).strip() for x in values if str(x).strip()):
        try:
            rec = db.lookup(v)
        except MmdbError as e:
            if strict:
                raise HTTPException(400, str(e)) from e
            rec = None
        results[v] = geoip_flatten(rec) if rec is not None and flat else rec
    return results


def bloom_lookup(service: Service, ref: ReferentialConfig, values: list[str], enrich: bool) -> dict[str, Any]:
    """Membership tests in a Bloom filter, optionally completed by an online bulk API
    (e.g. CIRCL hashlookup: enrich_bulk_url + enrich_key in the referential options)."""
    opts = ref.options
    mode = opts.get("normalize", "upper")
    pattern = opts.get("pattern")
    bf = service.bloom(ref)
    wanted = list(dict.fromkeys(bloom_normalize(v, mode) for v in values if str(v).strip()))
    results: dict[str, Any] = {}
    for v in wanted:
        if pattern and not re.fullmatch(pattern, v):
            results[v] = None
        else:
            results[v] = {"value": v, "present": True, "probabilistic": True} if bf.check(v) else None
    url, key = opts.get("enrich_bulk_url"), opts.get("enrich_key")
    present = [v for v, r in results.items() if r]
    if enrich and url and key and present:
        from ..network import client

        try:
            with client(service.settings, timeout=30) as c:
                for i in range(0, min(len(present), 5000), 500):
                    r = c.post(url, json={"hashes": present[i:i + 500]})
                    r.raise_for_status()
                    data = r.json()
                    for obj in data if isinstance(data, list) else []:
                        k = bloom_normalize(str(obj.get(key, "")), mode)
                        if results.get(k):
                            results[k].update({"confirmed": True, "details": obj})
            for v in present:
                results[v].setdefault("confirmed", False)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"online enrichment failed: {e}") from e
    return results


def _facet_candidates(ref: ReferentialConfig, meta: dict[str, Any], cols: dict[str, str]) -> tuple[list[str], list[str]]:
    """Columns suited for value facets (low cardinality) and for range filters (numbers, dates)."""
    profile = {p["name"]: p for p in meta.get("profile") or []}
    facets, ranges = [], []
    for name, ctype in cols.items():
        if name == ref.key:
            continue
        kind = type_kind(ctype)
        p = profile.get(name, {})
        distinct = p.get("approx_unique") or 0
        if p.get("null_percentage") is not None and p["null_percentage"] >= 99.9:
            continue
        if kind in ("text", "boolean", "list") and 1 < distinct <= 300:
            facets.append((distinct, name))
        elif kind in ("number", "temporal") and distinct > 1:
            ranges.append(name)
    return [n for _, n in sorted(facets)][:8], ranges[:6]


@router.get("/referentials/{ref_id}/facets", summary="Search facets (values, ranges) for the current filters")
def facets(
    ref_id: str,
    request: Request,
    q: str | None = None,
    facets: str | None = Query(None, description="Facet columns (default: automatic choice)"),
    facet_limit: int = Query(30, ge=1, le=200),
    service: Service = Depends(get_service),
    user: CurrentUser = Depends(require_user),
):
    ref = require_table(get_ref_with_data(service, ref_id, user))
    meta = service.metas.get(ref_id, {})
    limit_rows = service.settings.facets_max_rows or service.settings.large_rows
    if service.is_large(ref_id) or (meta.get("row_count") or 0) > limit_rows:
        return {"total": None, "facets": [], "ranges": [], "elapsed_ms": 0,
                "disabled": f"facets are disabled above {limit_rows:,} rows: use filters on the indexed columns"}
    cols = _columns(service, ref)
    auto_facets, ranges = _facet_candidates(ref, meta, cols)
    facet_cols = [c for c in facets.split(",") if c in cols] if facets else auto_facets
    params = [(k, v) for k, v in request.query_params.multi_items() if k not in ("facets", "facet_limit")]
    tq = TableQuery(table=ref.table, columns=cols, search=q, search_columns=ref.search_columns, filters=parse_filters(params))
    t0 = time.perf_counter()

    def run():
        key = _results.key(service, ref_id, "facets", tq.where(), tuple(facet_cols), tuple(ranges), facet_limit,
                           tuple(f"{f.column}__{f.op}={f.value}" for f in tq.filters or []))
        return _results.get(key, lambda: _compute_facets(service.engine, ref.table, cols, tq, facet_cols, ranges, facet_limit))

    facet_res, range_res, total = _run_query(run)
    return {"total": total, "facets": facet_res, "ranges": range_res, "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1)}


def _compute_facets(eng, table: str, cols: dict[str, str], tq: TableQuery, facet_cols: list[str], ranges: list[str],
                    facet_limit: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """Facets, ranges and total. Columns whose own filters do not change the WHERE clause (the usual case)
    share one scan (GROUPING SETS for the values, one aggregate for the ranges and the total); the others
    get their own query, which ignores their own filters."""
    t = qi(table)
    where = tq.where()

    def facet_entry(col: str, rows: list[tuple]) -> dict[str, Any]:
        return {"column": col, "type": cols[col], "kind": type_kind(cols[col]),
                "values": [{"value": v, "count": n} for v, n in rows[:facet_limit]], "more": len(rows) > facet_limit}

    def range_entry(col: str, mn: Any, mx: Any) -> dict[str, Any]:
        return {"column": col, "type": cols[col], "kind": type_kind(cols[col]),
                "min": None if mn is None else str(mn), "max": None if mx is None else str(mx)}

    def facet(col: str) -> dict[str, Any]:
        c, kind = qi(col), type_kind(cols[col])
        w = tq.where(exclude_column=col)
        if kind == "list":
            sql = (f"SELECT v, count(*) AS n FROM (SELECT CAST(unnest({c}) AS VARCHAR) AS v FROM {t}{w}) "
                   f"WHERE v IS NOT NULL AND v <> '' GROUP BY v ORDER BY n DESC, v LIMIT {facet_limit + 1}")
        else:
            cond = f"{c} IS NOT NULL" + ("" if kind == "boolean" else f" AND CAST({c} AS VARCHAR) <> ''")
            sql = (f"SELECT CAST({c} AS VARCHAR) AS v, count(*) AS n FROM {t}{w}{' AND ' if w else ' WHERE '}{cond} "
                   f"GROUP BY 1 ORDER BY n DESC, v LIMIT {facet_limit + 1}")
        return facet_entry(col, eng.fetch(sql)[1])

    def rng(col: str) -> dict[str, Any]:
        c = qi(col)
        mn, mx = eng.fetch(f"SELECT min({c}), max({c}) FROM {t}{tq.where(exclude_column=col)}")[1][0]
        return range_entry(col, mn, mx)

    def shared_facets(names: list[str]) -> dict[str, dict[str, Any]]:
        if not names:
            return {}
        alias = {n: f"c{i}" for i, n in enumerate(names)}
        proj = ", ".join(f"CAST({qi(n)} AS VARCHAR) AS {alias[n]}" for n in names)
        sets = ", ".join(f"({alias[n]})" for n in names)
        group = "CASE " + " ".join(f"WHEN grouping({alias[n]}) = 0 THEN {i}" for i, n in enumerate(names)) + " END"
        value = "CASE " + " ".join(f"WHEN grouping({alias[n]}) = 0 THEN {alias[n]}" for n in names) + " END"
        sql = (f"SELECT g, v, n FROM (SELECT {group} AS g, {value} AS v, count(*) AS n FROM (SELECT {proj} FROM {t}{where}) "
               f"GROUP BY GROUPING SETS ({sets})) WHERE v IS NOT NULL AND v <> '' "
               f"QUALIFY row_number() OVER (PARTITION BY g ORDER BY n DESC, v) <= {facet_limit + 1} ORDER BY g, n DESC, v")
        by_group: dict[int, list[tuple]] = {i: [] for i in range(len(names))}
        for g, v, n in eng.fetch(sql)[1]:
            by_group[g].append((v, n))
        return {n: facet_entry(n, by_group[i]) for i, n in enumerate(names)}

    def shared_ranges(names: list[str]) -> tuple[dict[str, dict[str, Any]], int]:
        aggs = "".join(f", min({qi(n)}), max({qi(n)})" for n in names)
        row = eng.fetch(f"SELECT count(*){aggs} FROM {t}{where}")[1][0]
        return {n: range_entry(n, row[1 + 2 * i], row[2 + 2 * i]) for i, n in enumerate(names)}, row[0]

    # A column is shared when ignoring its own filters gives the same WHERE clause
    shared_f = [c for c in facet_cols if type_kind(cols[c]) != "list" and tq.where(exclude_column=c) == where]
    shared_r = [c for c in ranges if tq.where(exclude_column=c) == where]
    jobs = {("f", c): _pool.submit(facet, c) for c in facet_cols if c not in shared_f}
    jobs.update({("r", c): _pool.submit(rng, c) for c in ranges if c not in shared_r})
    grouped = _pool.submit(shared_facets, shared_f)
    range_map, total = shared_ranges(shared_r)
    facet_map = grouped.result()
    for (kind, c), job in jobs.items():
        (facet_map if kind == "f" else range_map)[c] = job.result()
    return [facet_map[c] for c in facet_cols], [range_map[c] for c in ranges], total


@router.get("/referentials/{ref_id}/columns/{column:path}/stats", summary="Column statistics")
def column_stats(ref_id: str, column: str = PathParam(...), top: int = Query(20, ge=1, le=200), service: Service = Depends(get_service), user: CurrentUser = Depends(require_user)):
    ref = require_table(get_ref_with_data(service, ref_id, user))
    cols = _columns(service, ref)
    if column not in cols:
        raise HTTPException(404, f"unknown column: '{column}'")
    version = service.metas.get(ref_id, {}).get("version")
    cache_key = (ref_id, version, column, top)
    if cache_key in _stats_cache:
        return _stats_cache[cache_key]

    ctype = cols[column]
    kind = type_kind(ctype)
    c, t = qi(column), qi(ref.table)
    sampled = service.is_large(ref_id)
    if sampled:  # statistics on a ~1 % sample of the row groups
        t = f"(SELECT * FROM {qi(ref.table)} USING SAMPLE 1% (system))"
    eng = service.engine
    total, non_null = eng.fetch(f"SELECT count(*), count({c}) FROM {t}")[1][0]
    distinct = eng.scalar(f"SELECT approx_count_distinct({c}) FROM {t}")
    if kind == "list":
        top_sql = f"SELECT v, count(*) AS n FROM (SELECT CAST(unnest({c}) AS VARCHAR) AS v FROM {t}) WHERE v IS NOT NULL GROUP BY v ORDER BY n DESC, v LIMIT {top}"
    else:
        top_sql = f"SELECT CAST({c} AS VARCHAR) AS v, count(*) AS n FROM {t} WHERE {c} IS NOT NULL GROUP BY 1 ORDER BY n DESC, v LIMIT {top}"
    # Top values are meaningless for (almost) unique columns such as identifiers or timestamps
    mostly_unique = kind != "list" and non_null > 50 and distinct >= 0.9 * non_null
    top_values = [] if mostly_unique else [{"value": v, "count": n} for v, n in eng.fetch(top_sql)[1]]
    if kind != "list" and top_values and top_values[0]["count"] < 0.001 * non_null:
        # Scattered values (e.g. continuous scores): "most frequent" would be noise
        top_values, mostly_unique = [], True

    result: dict[str, Any] = {
        "column": column,
        "type": ctype,
        "kind": kind,
        "total": total,
        "non_null": non_null,
        "nulls": total - non_null,
        "distinct": distinct,
        "top": top_values,
        "mostly_unique": mostly_unique,
        "histogram": None,
        "sampled": sampled,
    }
    if kind in ("number", "temporal") and non_null:
        mn, mx = eng.fetch(f"SELECT min({c}), max({c}) FROM {t}")[1][0]
        result["min"], result["max"] = str(mn), str(mx)
        hist = None
        if kind == "number" and mn != mx:
            width = (float(mx) - float(mn)) / 20
            rows = eng.fetch(
                f"SELECT least(floor((CAST({c} AS DOUBLE) - {float(mn)!r}) / {width!r}), 19) AS b, count(*) "
                f"FROM {t} WHERE {c} IS NOT NULL GROUP BY 1 ORDER BY 1"
            )[1]
            hist = [{"label": f"{float(mn) + b * width:.4g}", "from": float(mn) + b * width, "to": float(mn) + (b + 1) * width, "count": n} for b, n in rows]
        elif kind == "temporal" and (ctype.upper() == "DATE" or ctype.upper().startswith("TIMESTAMP")):
            span = eng.scalar(f"SELECT date_diff('day', CAST(min({c}) AS TIMESTAMP), CAST(max({c}) AS TIMESTAMP)) FROM {t}") or 0
            unit = "year" if span > 365 * 6 else "month" if span > 120 else "week" if span > 35 else "day"
            fmt = {"year": "%Y", "month": "%Y-%m", "week": "%Y-%m-%d", "day": "%Y-%m-%d"}[unit]
            rows = eng.fetch(
                f"SELECT strftime(date_trunc('{unit}', {c}), '{fmt}') AS b, count(*) FROM {t} "
                f"WHERE {c} IS NOT NULL GROUP BY 1 ORDER BY 1"
            )[1]
            hist = [{"label": b, "count": n} for b, n in rows][-200:]
            result["bucket"] = unit
        result["histogram"] = hist
    if len(_stats_cache) > 500:
        _stats_cache.clear()
    _stats_cache[cache_key] = result
    return result


@router.get("/referentials/{ref_id}/changes", summary="Differences with the previous version")
def changes(
    ref_id: str,
    kind: Literal["added", "removed", "modified"] = "added",
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    service: Service = Depends(get_service),
    user: CurrentUser = Depends(require_user),
):
    ref = require_table(get_ref_with_data(service, ref_id, user))
    paths = RefPaths(service.settings.data_dir, ref_id)
    if not ref.key:
        raise HTTPException(400, "change tracking requires a key ('key') in the configuration")
    if not paths.previous.exists():
        return {"kind": kind, "rows": [], "columns": [], "total": 0, "available": False}
    k = qi(ref.key)
    key_name = None
    if service.metas.get(ref_id, {}).get("encrypted"):  # confidential: both versions are encrypted
        from ..crypto import parquet_key

        key_name = parquet_key(ref_id)[0]
    cur, prev = parquet_reader(str(paths.current), key_name), parquet_reader(str(paths.previous), key_name)
    if kind == "added":
        base = f"SELECT c.* FROM {cur} c ANTI JOIN {prev} p ON c.{k} = p.{k}"
    elif kind == "removed":
        base = f"SELECT p.* FROM {prev} p ANTI JOIN {cur} c ON c.{k} = p.{k}"
    else:
        base = (
            f"SELECT c.{k} AS __key, to_json(p) AS __before, to_json(c) AS __after FROM {cur} c JOIN {prev} p ON c.{k} = p.{k} "
            f"WHERE md5(CAST(c AS VARCHAR)) <> md5(CAST(p AS VARCHAR))"
        )
    eng = service.engine
    total = _run_query(lambda: eng.scalar(f"SELECT count(*) FROM ({base})"))
    cols, rows = _run_query(lambda: eng.fetch(f"{base} ORDER BY 1 LIMIT {limit} OFFSET {offset}"))
    if kind != "modified":
        all_cols = _columns(service, ref) if kind == "added" else {}
        return {
            "kind": kind,
            "available": True,
            "total": total,
            "columns": [{"name": c, "type": all_cols.get(c, "")} for c in cols],
            "rows": rows_to_records(cols, rows),
        }
    import orjson

    out = []
    for key, before, after in rows:
        b, a = orjson.loads(before), orjson.loads(after)
        diff = [
            {"column": col, "before": b.get(col), "after": a.get(col)}
            for col in dict.fromkeys([*a.keys(), *b.keys()])
            if b.get(col) != a.get(col)
        ]
        out.append({"key": str(key), "changes": diff})
    return {"kind": kind, "available": True, "total": total, "columns": [], "rows": out}


# --------------------------------------------------------------------------- global search

@router.get("/search", summary="Search across all referentials")
def search(q: str = Query(..., min_length=2), limit: int = Query(5, ge=1, le=50), service: Service = Depends(get_service), user: CurrentUser = Depends(require_user)):
    refs = [r for r in service.refs.values() if service.has_data(r.id) and user.can_read(r.id)]
    needle = q.strip()

    def one(ref: ReferentialConfig) -> dict[str, Any]:
        t0 = time.perf_counter()
        if ref.is_mmdb:
            rows = [r for r in mmdb_lookup(service, ref, [needle], flat=True).values() if r] if looks_like_ip(needle) else []
            columns = list(dict.fromkeys(k for r in rows for k in r))
            return {"id": ref.id, "name": ref.name, "category": ref.category, "key": "ip", "total": len(rows),
                    "columns": [{"name": c, "type": "VARCHAR"} for c in columns],
                    "rows": rows, "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1), "error": None}
        if ref.is_bloom:
            pattern = ref.options.get("pattern")
            if pattern and not re.fullmatch(pattern, needle):
                rows = []
            else:
                rows = [r for r in bloom_lookup(service, ref, [needle], False).values() if r]
            return {"id": ref.id, "name": ref.name, "category": ref.category, "key": "value", "total": len(rows),
                    "columns": [{"name": "value", "type": "VARCHAR"}, {"name": "present", "type": "BOOLEAN"}, {"name": "probabilistic", "type": "BOOLEAN"}],
                    "rows": rows, "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1), "error": None}
        try:
            cols = _columns(service, ref)
            tq = TableQuery(table=ref.table, columns=cols, search=needle, search_columns=ref.search_columns or ([ref.key] if ref.key else None),
                            **_layout(service, ref))
            where = tq.where()
            src = qi(tq.source())
            # Matches are read until SEARCH_CAP is reached, never the whole table: the scan stops early
            # and the exact key matches are put first among these candidates
            candidates = f"SELECT * FROM {src}{where} LIMIT {SEARCH_CAP}"
            order = ""
            if ref.key:
                order = f" ORDER BY CASE WHEN lower(CAST({qi(ref.key)} AS VARCHAR)) = lower({lit(needle)}) THEN 0 ELSE 1 END"
            names, rows = service.engine.fetch(f"SELECT * FROM ({candidates}){order} LIMIT {limit}")
            total = service.engine.scalar(f"SELECT count(*) FROM (SELECT 1 FROM {src}{where} LIMIT {SEARCH_CAP + 1})") if rows else 0
            if ref.key and ref.key in names and ref.key in tq.fast_columns:
                # Exact key match beyond the candidates (e.g. a frequent word): looked up on the indexed or sorted key
                view, cond = _key_source(service, ref)
                variants = sorted({needle, needle.upper(), needle.lower()})
                k = names.index(ref.key)
                if not any(str(r[k]).lower() == needle.lower() for r in rows):
                    exact = service.engine.fetch(f"SELECT * FROM {qi(view)} WHERE {cond} IN ({', '.join(lit(v) for v in variants)}) LIMIT {limit}")[1]
                    if exact:
                        seen = {r[k] for r in exact}
                        rows = (exact + [r for r in rows if r[k] not in seen])[:limit]
                        total = max(total, len(exact))
            return {
                "id": ref.id,
                "name": ref.name,
                "category": ref.category,
                "key": ref.key,
                "total": min(total, SEARCH_CAP),
                "total_capped": total > SEARCH_CAP,
                "columns": [{"name": c, "type": cols.get(c, "")} for c in names],
                "rows": rows_to_records(names, rows),
                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
                "error": None,
            }
        except Exception as e:  # noqa: BLE001
            return {"id": ref.id, "name": ref.name, "category": ref.category, "total": 0, "rows": [], "columns": [], "error": str(e)}

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(one, refs))
    results.sort(key=lambda r: (r["total"] == 0, r["name"]))
    return {"q": needle, "results": results}
