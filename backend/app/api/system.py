from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
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


@router.post("/sql", tags=["sql"], summary="Run a read-only SQL query")
def run_sql(
    body: SqlRequest,
    request: Request,
    service: Service = Depends(get_service),
    user: CurrentUser = Depends(require_sql),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Reserved to administrators and advanced users. Only the referentials readable by the user
    are visible (as tables and as files)."""
    max_rows = min(body.max_rows or service.settings.sql_max_rows, service.settings.sql_max_rows)
    try:
        result = service.engine.run_sandboxed(body.sql, max_rows, service.settings.sql_timeout, readable_tables(service, user))
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
        })
    return out


@router.get("/health", tags=["system"], summary="Liveness probe")
def health() -> dict[str, Any]:
    """Public probe (no authentication)."""
    return {"status": "ok"}


@router.get("/system", tags=["system"], summary="Service status")
def system(service: Service = Depends(get_service), _: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    return service.system()


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
