"""Bulk discovery: scan a Git repository or an HTTP folder, then create the referentials chosen."""

from __future__ import annotations

from typing import Any, Literal

from apscheduler.triggers.cron import CronTrigger
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth import CurrentUser, audit, require_admin
from ..db import get_db
from ..discovery import DiscoveryError, scan
from ..service import Service
from .admin_referentials import store_definition
from .deps import get_service

router = APIRouter(prefix="/admin/discovery", tags=["bulk discovery"], dependencies=[Depends(require_admin)])


class DiscoverySource(BaseModel):
    type: Literal["git", "http"]
    # git
    repository: str | None = None
    ref: str | None = None
    path: str | None = Field(None, description="Git: glob of the files (e.g. output/*); every file when empty")
    token: str | None = Field(None, description="Git: ${secret:<name>} reference of the access token")
    username: str | None = None
    # http
    url: str | None = Field(None, description="HTTP: URL of the folder (HTML directory listing)")
    pattern: str | None = Field(None, description="HTTP: glob of the files, relative to the folder")
    depth: int = Field(0, ge=0, le=5, description="HTTP: levels of sub-folders explored")
    headers: dict[str, str] = Field(default_factory=dict)
    basic_auth: str | None = None


class ScanRequest(BaseModel):
    source: DiscoverySource
    analyse: bool = Field(True, description="Fetch and analyse each file (format, record path, columns)")
    category: str | None = None
    tags: list[str] = Field(default_factory=list)
    schedule: str | None = Field(None, description="Cron expression given to every definition (e.g. 0 5 * * *)")


class ApplyRequest(BaseModel):
    configs: list[dict[str, Any]] = Field(..., max_length=1000)
    pull: bool = Field(True, description="Start the imports at once")
    grant_group_ids: list[int] = Field(default_factory=list)


@router.post("/scan", summary="List the data files of a Git repository or an HTTP folder and propose definitions")
def scan_source(body: ScanRequest, service: Service = Depends(get_service)):
    if body.schedule:
        try:
            CronTrigger.from_crontab(body.schedule)
        except ValueError as e:
            raise HTTPException(422, f"invalid schedule: {e}") from e
    try:
        return scan(service.settings, service.refs, body.source.model_dump(exclude_none=True), body.analyse,
                    body.category, body.tags, body.schedule)
    except DiscoveryError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/apply", summary="Create the referentials chosen among the candidates")
def apply(body: ApplyRequest, request: Request, db: Session = Depends(get_db), service: Service = Depends(get_service),
          admin: CurrentUser = Depends(require_admin)):
    created, errors = [], []
    for config in body.configs:
        ref_id = str(config.get("id") or "")
        try:
            created.append(store_definition(db, service, admin, request, config, body.grant_group_ids, origin="discovery"))
        except HTTPException as e:
            db.rollback()
            errors.append({"id": ref_id, "status": e.status_code, "error": e.detail})
    if created:
        service.reload()
    runs = 0
    if body.pull:
        for ref_id in created:
            if ref_id in service.refs and service.refs[ref_id].enabled and service.submit(ref_id, "manual", False, admin.username):
                runs += 1
    audit(db, "discovery.apply", user=admin, request=request, detail={"created": created, "errors": len(errors)})
    return {"created": created, "errors": errors, "runs": runs}
