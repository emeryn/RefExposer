"""Administration of referential definitions: create, configure, preview and pull sources from the UI."""

from __future__ import annotations

import shutil
from typing import Any, Literal

import yaml
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..auth import CurrentUser, audit, require_admin
from ..config import FORMATS, validate_definition
from ..db import get_db
from ..models import Grant, Group, ReferentialDefinition
from ..preview import PreviewError, adopt_upload, preview, store_upload
from ..secretstore import SecretError, check_references, mask_config, mask_source, unmask_source
from ..service import Service
from ..storage import RefPaths
from .deps import get_service

router = APIRouter(prefix="/admin/referentials", tags=["referential administration"], dependencies=[Depends(require_admin)])


class PreviewSource(BaseModel):
    type: Literal["http", "upload", "local", "git"] = "http"
    repository: str | None = None
    ref: str | None = None
    token: str | None = None
    username: str | None = None
    urls: list[str] = Field(default_factory=list)
    headers: dict[str, str] = Field(default_factory=dict)
    basic_auth: str | None = None
    extract: str | None = None
    upload_id: str | None = None
    path: str | None = None


class PreviewRequest(BaseModel):
    source: PreviewSource
    format: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)
    transform: str | None = None
    referential_id: str | None = Field(None, description="For a local source: existing referential")
    refresh: bool = Field(False, description="Download again instead of using the cached copy")


class DefinitionIn(BaseModel):
    config: dict[str, Any]
    upload_id: str | None = Field(None, description="Uploaded file to use as the source (local referential)")
    pull: bool = Field(True, description="Start the import immediately")
    grant_group_ids: list[int] = Field(default_factory=list, description="Groups getting the read right (creation)")


def _clean(config: dict[str, Any]) -> dict[str, Any]:
    """Drop empty values so that stored definitions stay readable."""
    out = {}
    for k, v in config.items():
        if v is None or v == "" or v == [] or v == {}:
            continue
        out[k] = _clean(v) if isinstance(v, dict) and k not in ("options", "headers") else v
    return out


def _definition_dict(service: Service, ref_id: str) -> dict[str, Any]:
    ref = service.refs.get(ref_id)
    if not ref:
        raise HTTPException(404, f"unknown referential: '{ref_id}'")
    return {
        "id": ref.id,
        "origin": ref.origin,
        "config_file": ref.config_file,
        "editable": ref.origin == "database",
        # Literal credentials are masked (sent back unchanged, they keep their value); ${secret:...} references are shown
        "config": mask_config(ref.model_dump(mode="json", exclude_defaults=False)),
        "summary": service.summary(ref, "manage"),
    }


@router.get("", summary="All definitions (YAML files and interface)")
def list_definitions(service: Service = Depends(get_service)):
    return [_definition_dict(service, r) for r in service.refs]


@router.get("/_meta", summary="Values for the form (formats, categories, groups)")
def form_options(db: Session = Depends(get_db), service: Service = Depends(get_service)):
    return {
        "formats": list(FORMATS),
        "categories": sorted({r.category for r in service.refs.values()}),
        "groups": [{"id": g.id, "name": g.name} for g in db.scalars(select(Group).order_by(Group.name))],
    }


@router.get("/{ref_id}", summary="Referential definition")
def get_definition(ref_id: str, service: Service = Depends(get_service)):
    return _definition_dict(service, ref_id)


@router.get("/{ref_id}/yaml", summary="Definition as YAML", response_class=PlainTextResponse)
def get_yaml(ref_id: str, service: Service = Depends(get_service)):
    ref = service.refs.get(ref_id)
    if not ref:
        raise HTTPException(404, f"unknown referential: '{ref_id}'")
    config = mask_config(_clean(ref.model_dump(mode="json")))
    return yaml.safe_dump({"referentials": [config]}, allow_unicode=True, sort_keys=False, width=110)


@router.post("/uploads", summary="Upload one or more files (csv, xlsx, json, zip, gz…)")
def upload(files: list[UploadFile] = File(...), service: Service = Depends(get_service)):
    try:
        return store_upload(service.settings, [(f.filename or "file", f.file) for f in files])
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"upload failed: {e}") from e


@router.post("/preview", summary="Analyse a source: download, format detection, preview")
def run_preview(body: PreviewRequest, service: Service = Depends(get_service)):
    ref_root = RefPaths(service.settings.data_dir, body.referential_id).root if body.referential_id else None
    if body.format and body.format not in FORMATS:
        raise HTTPException(400, f"unknown format: {body.format}")
    source = body.source.model_dump()
    stored = service.refs.get(body.referential_id) if body.referential_id else None
    try:  # masked credentials of the edited referential: its stored values
        source = unmask_source(source, stored.source.model_dump() if stored else None)
    except SecretError as e:
        raise HTTPException(400, str(e)) from e
    try:
        return preview(service.settings, source, body.format, body.options, body.transform or None, ref_root, body.refresh)
    except PreviewError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"{type(e).__name__}: {e}") from e


def _validate(db: Session, config: dict[str, Any], ref_id: str | None = None, stored: dict[str, Any] | None = None) -> dict[str, Any]:
    data = {**config}
    if ref_id:
        data["id"] = ref_id
    try:
        if data.get("source"):
            data["source"] = unmask_source(data["source"], (stored or {}).get("source"))
        check_references(db, data)
        ref = validate_definition(data)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return _clean(ref.model_dump(mode="json", exclude={"id"}))


def _adopt(service: Service, ref_id: str, upload_id: str, config: dict[str, Any]) -> dict[str, Any]:
    raw = RefPaths(service.settings.data_dir, ref_id).raw
    try:
        adopt_upload(service.settings, upload_id, raw)
    except PreviewError as e:
        raise HTTPException(400, str(e)) from e
    return {**config, "source": {"type": "local", "path": "raw/**/*"}}


def store_definition(db: Session, service: Service, admin: CurrentUser, request: Request, raw_config: dict[str, Any],
                     grant_group_ids: list[int], upload_id: str | None = None, origin: str | None = None) -> str:
    """Validate and store a new definition (with its read grants); the caller reloads the service. Returns the id."""
    ref_id = str(raw_config.get("id") or "").strip()
    config = {k: v for k, v in raw_config.items() if k != "id"}
    if upload_id:
        config["source"] = {"type": "local", "path": "raw/**/*"}
    config = _validate(db, config, ref_id)
    if ref_id in service.refs or db.get(ReferentialDefinition, ref_id):
        raise HTTPException(409, f"identifier '{ref_id}' is already used")
    if RefPaths(service.settings.data_dir, ref_id).root.exists() and not upload_id and config.get("source", {}).get("type") != "local":
        # Leftover data from a deleted referential would be silently reused. Local sources are
        # kept: their files may have been dropped beforehand by another tool.
        shutil.rmtree(RefPaths(service.settings.data_dir, ref_id).root, ignore_errors=True)
    if upload_id:
        config = _adopt(service, ref_id, upload_id, config)
    db.add(ReferentialDefinition(id=ref_id, config=config, created_by=admin.username, updated_by=admin.username))
    for gid in grant_group_ids:
        if db.get(Group, gid):
            db.add(Grant(referential_id=ref_id, group_id=gid, level="read", created_by=admin.username))
    detail = {"source": mask_source(config.get("source")), "format": config.get("format")}
    if origin:
        detail["origin"] = origin
    audit(db, "referential.create", user=admin, target=f"ref:{ref_id}", request=request, detail=detail)
    return ref_id


@router.post("", summary="Create a referential", status_code=201)
def create_definition(body: DefinitionIn, request: Request, db: Session = Depends(get_db), service: Service = Depends(get_service), admin: CurrentUser = Depends(require_admin)):
    ref_id = store_definition(db, service, admin, request, body.config, body.grant_group_ids, body.upload_id)
    service.reload()
    run = service.submit(ref_id, "manual", False, admin.username).to_dict() if body.pull and service.refs[ref_id].enabled else None
    return {**_definition_dict(service, ref_id), "run": run}


@router.put("/{ref_id}", summary="Update a referential created from the interface")
def update_definition(ref_id: str, body: DefinitionIn, request: Request, db: Session = Depends(get_db), service: Service = Depends(get_service), admin: CurrentUser = Depends(require_admin)):
    d = db.get(ReferentialDefinition, ref_id)
    if not d:
        ref = service.refs.get(ref_id)
        if ref and ref.origin == "file":
            raise HTTPException(409, f"referential defined in config/{ref.config_file}: edit the YAML file")
        if ref and ref.origin == "sync":
            raise HTTPException(409, f"referential synchronized from {ref.config_file}: change its files or its refexposer.yml")
        raise HTTPException(404, f"unknown referential: '{ref_id}'")
    if ref_id in service.active:
        raise HTTPException(409, "an update is running, retry when it is finished")
    config = {k: v for k, v in body.config.items() if k != "id"}
    if body.upload_id:
        config["source"] = {"type": "local", "path": "raw/**/*"}
    config = _validate(db, config, ref_id, d.config)
    if body.upload_id:
        config = _adopt(service, ref_id, body.upload_id, config)
    d.config = config
    d.updated_by = admin.username
    audit(db, "referential.update", user=admin, target=f"ref:{ref_id}", request=request,
          detail={"upload": bool(body.upload_id)})
    service.reload()
    run = service.submit(ref_id, "manual", False, admin.username).to_dict() if body.pull and service.refs[ref_id].enabled else None
    return {**_definition_dict(service, ref_id), "run": run}


@router.delete("/{ref_id}", summary="Delete a referential created from the interface")
def delete_definition(
    ref_id: str,
    request: Request,
    purge: bool = Query(True, description="Also delete the downloaded data"),
    db: Session = Depends(get_db),
    service: Service = Depends(get_service),
    admin: CurrentUser = Depends(require_admin),
):
    d = db.get(ReferentialDefinition, ref_id)
    if not d:
        ref = service.refs.get(ref_id)
        if ref and ref.origin == "file":
            raise HTTPException(409, f"referential defined in config/{ref.config_file}: remove it from the YAML file")
        if ref and ref.origin == "sync":
            raise HTTPException(409, f"referential synchronized from {ref.config_file}: remove it from the sync folder")
        raise HTTPException(404, f"unknown referential: '{ref_id}'")
    if ref_id in service.active:
        raise HTTPException(409, "an update is running: cancel it before deleting the referential")
    db.delete(d)
    db.execute(delete(Grant).where(Grant.referential_id == ref_id))
    audit(db, "referential.delete", user=admin, target=f"ref:{ref_id}", request=request, detail={"purge": purge})
    service.reload()
    if purge:
        shutil.rmtree(RefPaths(service.settings.data_dir, ref_id).root, ignore_errors=True)
    return {"ok": True}


@router.post("/{ref_id}/upload", summary="Replace the files of a local referential and update it")
def replace_files(ref_id: str, request: Request, files: list[UploadFile] = File(...), db: Session = Depends(get_db), service: Service = Depends(get_service), admin: CurrentUser = Depends(require_admin)):
    ref = service.refs.get(ref_id)
    if not ref:
        raise HTTPException(404, f"unknown referential: '{ref_id}'")
    if ref.source.type != "local":
        raise HTTPException(400, "only referentials with a local source (uploaded files) accept an upload")
    if ref_id in service.active:
        raise HTTPException(409, "an update is running")
    stored = store_upload(service.settings, [(f.filename or "file", f.file) for f in files])
    adopt_upload(service.settings, stored["upload_id"], RefPaths(service.settings.data_dir, ref_id).raw)
    audit(db, "referential.upload", user=admin, target=f"ref:{ref_id}", request=request, detail={"files": [f["name"] for f in stored["files"]]})
    return {"files": stored["files"], "run": service.submit(ref_id, "manual", False, admin.username).to_dict()}
