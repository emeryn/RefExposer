"""Downloads of full referentials and automatically generated API documentation."""

from __future__ import annotations

import json
from email.utils import formatdate, parsedate_to_datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from starlette.background import BackgroundTask
from sqlalchemy.orm import Session

from ..apidoc import SWAGGER_HTML, build_spec
from ..auth import CurrentUser, audit, require_user
from ..config import ReferentialConfig
from ..db import get_db
from ..downloads import DownloadError, DownloadFile
from ..engine import rows_to_records
from ..service import Service
from ..sqlbuild import qi
from ..storage import RefPaths
from .deps import get_ref, get_ref_with_data, get_service, public_base

router = APIRouter()


def _sample(service: Service, ref: ReferentialConfig) -> dict[str, Any] | None:
    """First row with a key value, used as example in the documentation."""
    if not service.has_data(ref.id) or ref.is_artifact:
        return None
    where = f" WHERE {qi(ref.key)} IS NOT NULL" if ref.key else ""
    try:
        cols, rows = service.engine.fetch(f"SELECT * FROM {qi(ref.table)}{where} LIMIT 1")
    except Exception:  # noqa: BLE001
        return None
    return rows_to_records(cols, rows)[0] if rows else None




# --------------------------------------------------------------------------- downloads

@router.get("/downloads", tags=["downloads"], summary="Downloads catalog")
def catalog(service: Service = Depends(get_service), user: CurrentUser = Depends(require_user)):
    out = []
    for ref in service.refs.values():
        if not user.can_read(ref.id) or not service.has_data(ref.id):
            continue
        meta = service.metas.get(ref.id, {})
        out.append({
            **service.summary(ref, user.access(ref.id)),
            "formats": [] if ref.is_artifact else service.downloads.available(ref, meta),
            "sources": _artifact_files(service, ref, meta) if ref.is_artifact else service.downloads.sources(ref, meta),
        })
    return out


@router.get("/referentials/{ref_id}/downloads", tags=["downloads"], summary="Downloadable files of a referential")
def downloads(ref_id: str, service: Service = Depends(get_service), user: CurrentUser = Depends(require_user)):
    ref = get_ref_with_data(service, ref_id, user)
    meta = service.metas.get(ref_id, {})
    if ref.is_artifact:
        paths = RefPaths(service.settings.data_dir, ref_id)
        return {"version": meta.get("version"), "data_updated_at": meta.get("data_updated_at"), "row_count": meta.get("row_count"),
                "formats": [], "sources": _artifact_files(service, ref, meta),
                "raw_url": f"/api/referentials/{ref_id}/raw",
                "has_previous": (paths.previous_mmdb if ref.is_mmdb else paths.previous_bloom).exists()}
    return {
        "version": meta.get("version"),
        "data_updated_at": meta.get("data_updated_at"),
        "row_count": meta.get("row_count"),
        "formats": service.downloads.available(ref, meta),
        "sources": service.downloads.sources(ref, meta),
        "has_previous": (service.settings.data_dir / ref_id / "previous.parquet").exists(),
    }


RAW_DOC = ("Raw file, for tools: the MaxMind DB (.mmdb, original name) or the Bloom filter as published, or the original source "
           "files (zip) of a table. Stable URL; `ETag` / `If-None-Match` and `Last-Modified` / `If-Modified-Since` answer `304` "
           "when the file did not change, `HEAD` gives the headers only. Authentication: `Authorization: Bearer <token>`, or HTTP "
           "Basic with the token as password (`curl -u x:rfx_...`, `wget --user=x --password=rfx_...`).")


@router.api_route(
    "/referentials/{ref_id}/download/{fmt}",
    methods=["GET", "HEAD"],
    tags=["downloads"],
    summary="Download the whole referential",
    description="`fmt`: csv, csv.gz, xlsx, json, jsonl, parquet, `source` (original files), `previous` (previous version) or "
                "`raw` (see /raw). MaxMind DB: `mmdb` (= raw) and `previous`. "
                "The file is generated once per version then served from the cache; its SHA-256 fingerprint is in `X-Checksum-SHA256`.",
)
def download(ref_id: str, fmt: str, request: Request, service: Service = Depends(get_service), user: CurrentUser = Depends(require_user), db: Session = Depends(get_db)):
    ref = get_ref_with_data(service, ref_id, user)
    meta = service.metas.get(ref_id, {})
    try:
        if ref.is_artifact:
            if fmt not in ("raw", "source", "previous", "mmdb" if ref.is_mmdb else "bloom"):
                raise DownloadError(404, f"available formats: raw, previous ({'MaxMind DB' if ref.is_mmdb else 'Bloom filter'} file as published)")
            f = service.downloads.artifact(ref, meta, previous=fmt == "previous")
        elif fmt in ("source", "raw"):
            f: DownloadFile = service.downloads.source_archive(ref, meta)
        elif fmt == "previous":
            f = service.downloads.previous(ref, meta)
        else:
            f = service.downloads.get(ref, meta, fmt)
    except DownloadError as e:
        raise HTTPException(e.status, str(e)) from e
    if f.temporary:  # confidential: clear copy made for this request, deleted once sent
        audit(db, "referential.download", user=user, target=f"ref:{ref_id}", request=request, detail={"format": fmt, "size": f.size})
        return FileResponse(f.path, media_type=f.media_type, filename=f.filename,
                            headers={"X-Checksum-SHA256": f.sha256, "Cache-Control": "no-store"},
                            background=BackgroundTask(f.path.unlink, missing_ok=True))
    etag = f'"{f.sha256}"'
    modified = formatdate(f.path.stat().st_mtime, usegmt=True)
    headers = {"ETag": etag, "X-Checksum-SHA256": f.sha256, "Last-Modified": modified, "Cache-Control": "private, no-cache"}
    if _not_modified(request, etag, f.path.stat().st_mtime):
        return Response(status_code=304, headers=headers)
    if request.method == "HEAD":
        return Response(headers={**headers, "Content-Length": str(f.size), "Content-Type": f.media_type,
                                 "Content-Disposition": f'attachment; filename="{f.filename}"'})
    audit(db, "referential.download", user=user, target=f"ref:{ref_id}", request=request, detail={"format": fmt, "size": f.size})
    return FileResponse(f.path, media_type=f.media_type, filename=f.filename, headers=headers)


@router.api_route("/referentials/{ref_id}/raw", methods=["GET", "HEAD"], tags=["downloads"], summary="Raw file (for tools)", description=RAW_DOC)
def raw(ref_id: str, request: Request, service: Service = Depends(get_service), user: CurrentUser = Depends(require_user), db: Session = Depends(get_db)):
    return download(ref_id, "raw", request, service, user, db)


def _not_modified(request: Request, etag: str, mtime: float) -> bool:
    """Conditional request: If-None-Match wins over If-Modified-Since (RFC 9110)."""
    inm = request.headers.get("if-none-match")
    if inm:
        return etag in [t.strip().removeprefix("W/") for t in inm.split(",")] or inm.strip() == "*"
    ims = request.headers.get("if-modified-since")
    if ims:
        try:
            return int(mtime) <= int(parsedate_to_datetime(ims).timestamp())
        except (TypeError, ValueError):
            return False
    return False


def _artifact_files(service: Service, ref: ReferentialConfig, meta: dict[str, Any]) -> list[dict[str, Any]]:
    paths = RefPaths(service.settings.data_dir, ref.id)
    p = paths.mmdb if ref.is_mmdb else paths.bloom
    if not p.exists():
        return []
    name = (meta.get("mmdb") or {}).get("file_name") if ref.is_mmdb else p.name
    return [{"name": name or p.name, "size": p.stat().st_size, "url": (ref.source.urls or [None])[0], "raw_url": f"/api/referentials/{ref.id}/raw"}]


# --------------------------------------------------------------------------- API documentation

@router.get("/referentials/{ref_id}/openapi.json", tags=["documentation"], summary="Generated OpenAPI specification of a referential")
def ref_openapi(ref_id: str, request: Request, service: Service = Depends(get_service), user: CurrentUser = Depends(require_user)):
    ref = get_ref(service, ref_id, user)
    meta = service.metas.get(ref_id, {})
    return build_spec([(ref, meta, _sample(service, ref))], public_base(request), service.settings.api_max_limit)


@router.get("/catalog/openapi.json", tags=["documentation"], summary="OpenAPI specification of all accessible referentials")
def catalog_openapi(request: Request, service: Service = Depends(get_service), user: CurrentUser = Depends(require_user)):
    entries = [
        (ref, service.metas.get(ref.id, {}), _sample(service, ref))
        for ref in service.refs.values()
        if user.can_read(ref.id) and service.has_data(ref.id)
    ]
    return build_spec(entries, public_base(request), service.settings.api_max_limit)


@router.get("/catalog/docs", tags=["documentation"], summary="Interactive documentation (Swagger) of the referentials", response_class=HTMLResponse)
def catalog_docs(ref: str | None = None):
    """Public page: the specification itself is only served to authenticated users."""
    if ref and not ref.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(400, "invalid identifier")
    spec_url = f"/api/referentials/{ref}/openapi.json" if ref else "/api/catalog/openapi.json"
    title = f"API — {ref}" if ref else "Referentials API"
    return HTMLResponse(SWAGGER_HTML.format(title=title, spec_url=json.dumps(spec_url)))
