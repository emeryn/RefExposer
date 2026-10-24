from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ..engine import SqlError, SqlTimeout
from ..service import Service
from ..storage import RefPaths, read_runs
from ..auth import CurrentUser, audit, require_admin, require_sql, require_user
from ..db import get_db
from .deps import get_service, readable_tables

router = APIRouter()


class SqlRequest(BaseModel):
    sql: str = Field(..., examples=["SELECT * FROM cisa_kev LIMIT 10"])
    max_rows: int | None = Field(None, ge=1)
    # Personal data columns in clear, for the referentials this account may reveal them on (pii.py). Off by
    # default, so a query — including one the assistant writes — reads masked values.
    reveal: bool = False


@router.post("/sql", tags=["sql"], summary="Run a read-only SQL query")
def run_sql(
    body: SqlRequest,
    request: Request,
    service: Service = Depends(get_service),
    user: CurrentUser = Depends(require_sql),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Reserved to administrators and advanced users. Only the referentials readable by the user
    are visible (as tables and as files), with their personal data columns masked unless `reveal` is set and
    this account is allowed to lift it."""
    from .. import pii

    max_rows = min(body.max_rows or service.settings.sql_max_rows, service.settings.sql_max_rows)
    reveal = pii.revealed(service, user) if body.reveal else frozenset()
    if reveal:
        audit(db, "pii.reveal", user=user, request=request, target="sql",
              detail={"tables": sorted(reveal), "path": "/api/sql"})
    try:
        result = service.engine.run_sandboxed(body.sql, max_rows, service.settings.sql_timeout,
                                              readable_tables(service, user), reveal)
    except SqlTimeout as e:
        audit(db, "sql.query", user=user, request=request, success=False, detail={"sql": body.sql[:2000], "error": str(e)})
        raise HTTPException(408, str(e)) from e
    except SqlError as e:
        audit(db, "sql.query", user=user, request=request, success=False, detail={"sql": body.sql[:2000], "error": str(e)[:500]})
        raise HTTPException(400, str(e)) from e
    audit(db, "sql.query", user=user, request=request, detail={"sql": body.sql[:2000], "rows": result["row_count"]})
    return result


@router.get("/sql/schema", tags=["sql"], summary="Tables available to the SQL console")
def sql_schema(service: Service = Depends(get_service), user: CurrentUser = Depends(require_sql)) -> list[dict[str, Any]]:
    from .. import pii

    out = []
    for ref in service.refs.values():
        if not service.has_data(ref.id) or not user.can_read(ref.id) or ref.is_artifact:
            continue
        meta = service.metas.get(ref.id, {})
        out.append({
            "table": ref.table,
            "ref_id": ref.id,
            "name": ref.name,
            "row_count": meta.get("row_count"),
            "columns": meta.get("columns") or service.engine.describe(ref.table),
            "indexes": service.engine.index_views.get(ref.table, {}),
            # Columns served masked in the console, and whether this account may ask for them in clear
            "pii": pii.state(ref, user, [c["name"] for c in meta.get("columns") or []]),
        })
    return out


class SqlReferentialRequest(BaseModel):
    """A query of the SQL console kept as a referential of its own."""

    model_config = ConfigDict(extra="forbid")

    sql: str = Field(..., min_length=1, max_length=20_000,
                     examples=["SELECT k.cveID, k.vendorProject, e.epss FROM cisa_kev k JOIN epss e ON e.cve = k.cveID"])
    id: str = Field(..., pattern=r"^[a-z0-9][a-z0-9_-]{0,62}$", examples=["kev-epss"])
    name: str = Field(..., min_length=1, max_length=200)
    description: str = Field("", max_length=2000)
    category: str = Field("SQL", min_length=1, max_length=100)
    sources: list[str] | None = Field(
        None, max_length=20,
        description="Referentials the query reads; detected from the table names of the query when omitted")
    key: str | None = Field(None, max_length=200, description="Column identifying a row (lookups, upserts)")
    search_columns: list[str] = Field(default_factory=list, max_length=20)
    schedule: str | None = Field(None, max_length=100,
                                description="Crontab expression. Empty is usually right: the referential is "
                                            "rebuilt whenever one of its sources publishes a new version.")
    pull: bool = Field(True, description="Build the first version immediately")


@router.post("/sql/referential", tags=["sql"], summary="Create a referential from a query", status_code=201)
def create_sql_referential(
    body: SqlReferentialRequest,
    request: Request,
    service: Service = Depends(get_service),
    user: CurrentUser = Depends(require_sql),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Keep the result of a console query as a unified referential (`source.type: referential`).

    The query itself is stored, not its rows: the referential is rebuilt from the published data of the
    referentials it reads, every time one of them publishes a new version. Reserved to administrators and
    advanced users, and limited to the referentials the account may read — the same scope as the console.
    """
    from ..combine import CombineError, transform_from_sql
    from .admin_referentials import store_definition

    tables: dict[str, str] = {}  # table or sorted copy -> referential holding it
    for ref in service.refs.values():
        if not service.has_data(ref.id) or not user.can_read(ref.id) or ref.is_artifact:
            continue
        tables[ref.table] = ref.id
        for view in service.engine.index_views.get(ref.table, {}).values():
            tables[view] = ref.id

    if body.sources is None:
        used = [name for name in tables if _mentions(body.sql, name)]
    else:
        unknown = [r for r in body.sources if r not in set(tables.values())]
        if unknown:
            raise HTTPException(400, f"unknown or unreadable referential(s): {', '.join(unknown)}")
        used = [name for name, ref_id in tables.items() if ref_id in set(body.sources)]
    sources = list(dict.fromkeys(tables[name] for name in used))
    if not sources:
        raise HTTPException(400, "the query names no referential: write the table names the console lists "
                                 "(left panel), not a file or an external source")

    # The query is checked the way the console runs it, against those referentials only: it must parse, read
    # nothing else, and be usable as a subquery, which is how the referential is rebuilt
    probe = frozenset(name for name, ref_id in tables.items() if ref_id in set(sources))
    try:
        result = service.engine.run_sandboxed(f"SELECT * FROM ({body.sql.strip().rstrip(';')}) LIMIT 0", 1,
                                              service.settings.sql_timeout, probe)
    except SqlTimeout as e:
        raise HTTPException(408, str(e)) from e
    except SqlError as e:
        raise HTTPException(400, f"the query cannot become a referential: {e}") from e
    columns = [c["name"] for c in result["columns"]]
    if not columns:
        raise HTTPException(400, "the query returns no column")
    for label, wanted in (("key", [body.key] if body.key else []), ("search_columns", body.search_columns)):
        missing = [c for c in wanted if c not in columns]
        if missing:
            raise HTTPException(400, f"{label}: the query has no column {', '.join(missing)} "
                                     f"(columns: {', '.join(columns)})")

    # Only the names the query actually uses are bound, plus the table of each source so the stored
    # transformation stays readable and editable
    names = [[n for n in dict.fromkeys([service.refs[ref_id].table, *used]) if tables.get(n) == ref_id]
             for ref_id in sources]
    try:
        transform = transform_from_sql(body.sql, names)
    except CombineError as e:
        raise HTTPException(400, str(e)) from e

    config: dict[str, Any] = {
        "id": body.id,
        "name": body.name,
        "description": body.description,
        "category": body.category,
        "format": "parquet",
        "source": {"type": "referential", "referentials": sources},
        "transform": transform,
        "key": body.key,
        "search_columns": body.search_columns,
        "schedule": body.schedule,
        # A referential built from a confidential one is confidential too (combine.check_sources)
        "confidential": any(service.refs[s].confidential for s in sources),
    }
    # The creator manages its referential, as for an internal one; an administrator already sees everything
    ref_id = store_definition(db, service, user, request, config, [], origin="sql",
                              manager_id=None if user.is_admin else user.id)
    service.reload()
    run = service.submit(ref_id, "manual", False, user.username).to_dict() if body.pull else None
    return {"id": ref_id, "sources": sources, "columns": result["columns"], "transform": transform, "run": run}


def _mentions(sql: str, table: str) -> bool:
    """Whether `sql` names this table, ignoring the names that are only part of a longer identifier."""
    return re.search(rf'(?<![A-Za-z0-9_.]){re.escape(table)}(?![A-Za-z0-9_])', sql) is not None


@router.get("/health", tags=["system"], summary="Liveness probe")
def health() -> dict[str, Any]:
    """Public probe (no authentication)."""
    return {"status": "ok"}


@router.get("/system", tags=["system"], summary="Service status")
def system(service: Service = Depends(get_service), _: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    return service.system()


@router.get("/system/storage", tags=["system"], summary="Disk space: free space of the volumes, what takes the space")
def storage(refresh: bool = Query(False, description="Walk the folders again (at most every 5 s; cached 30 s otherwise)"),
            service: Service = Depends(get_service), _: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    """Administrators only, read only: sizes per volume, per referential and per kind of file (published version,
    indexes, previous version, sources kept, interrupted downloads...), the largest files, and folders left by deleted
    referentials. Nothing is deleted from here."""
    from ..diskusage import report

    return report(service, refresh)


@router.post("/system/reload", tags=["system"], summary="Reload the referentials configuration")
def reload(request: Request, service: Service = Depends(get_service), user: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    result = service.reload()
    audit(db, "system.reload", user=user, request=request, detail={"loaded": result["loaded"], "errors": len(result["errors"])})
    return result


@router.get("/activity", tags=["system"], summary="Latest updates across all referentials")
def activity(limit: int = Query(30, ge=1, le=200), service: Service = Depends(get_service), user: CurrentUser = Depends(require_user)) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = [r.to_dict(with_logs=False) for r in service.active.values() if user.can_read(r.ref_id)]
    for ref in service.refs.values():
        if not user.can_read(ref.id):
            continue
        for run in read_runs(RefPaths(service.settings.data_dir, ref.id), limit):
            run.pop("logs", None)
            runs.append(run)
    names = {r.id: r.name for r in service.refs.values()}
    for r in runs:
        r["ref_name"] = names.get(r["ref_id"], r["ref_id"])
    runs.sort(key=lambda r: r.get("started_at") or r.get("queued_at") or "", reverse=True)
    return runs[:limit]
