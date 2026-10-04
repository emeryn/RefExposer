"""System tasks and configuration backups (administrators)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import backup
from ..auth import CurrentUser, audit, require_admin
from ..db import get_db
from ..service import Service
from .deps import get_service

router = APIRouter(prefix="/admin", tags=["system tasks"], dependencies=[Depends(require_admin)])

CONFIRM = "RESTORE"


class TaskUpdate(BaseModel):
    enabled: bool | None = None
    schedule: str | None = None
    params: dict[str, Any] | None = None


def _task(service: Service, task_id: str) -> None:
    if task_id not in service.tasks.defs:
        raise HTTPException(404, f"unknown task: '{task_id}'")


@router.get("/tasks", summary="System tasks")
def list_tasks(service: Service = Depends(get_service)):
    return service.tasks.list()


@router.put("/tasks/{task_id}", summary="Enable / disable a task, change its schedule or parameters")
def update_task(task_id: str, body: TaskUpdate, request: Request, service: Service = Depends(get_service),
                admin: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    _task(service, task_id)
    try:
        out = service.tasks.update(task_id, body.enabled, body.schedule, body.params, admin.username)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    audit(db, "task.update", user=admin, target=f"task:{task_id}", request=request, detail=body.model_dump(exclude_none=True))
    return out


@router.post("/tasks/{task_id}/run", summary="Run a task now")
def run_task(task_id: str, request: Request, service: Service = Depends(get_service),
             admin: CurrentUser = Depends(require_admin)):
    _task(service, task_id)
    if not service.tasks.defs[task_id].available(service.tasks):
        raise HTTPException(409, "this task is not available with the current configuration")
    try:
        return service.tasks.submit(task_id, admin.username)
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e


@router.get("/tasks/{task_id}/runs", summary="History of a task")
def task_runs(task_id: str, limit: int = 50, service: Service = Depends(get_service)):
    _task(service, task_id)
    return service.tasks.history(task_id, min(max(limit, 1), 500))


# --------------------------------------------------------------------------- backups

@router.get("/backups", summary="Configuration backups")
def list_backups(service: Service = Depends(get_service)):
    return backup.list_files(service.settings.data_dir)


@router.get("/backups/{name}", summary="Download a configuration backup")
def download_backup(name: str, request: Request, service: Service = Depends(get_service),
                    admin: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    try:
        path = backup.path_of(service.settings.data_dir, name)
    except backup.BackupError as e:
        raise HTTPException(404, str(e)) from e
    audit(db, "backup.download", user=admin, target=f"backup:{name}", request=request)
    return FileResponse(path, media_type="application/octet-stream", filename=name)


@router.delete("/backups/{name}", summary="Delete a configuration backup")
def delete_backup(name: str, request: Request, service: Service = Depends(get_service),
                  admin: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    try:
        backup.path_of(service.settings.data_dir, name).unlink()
    except backup.BackupError as e:
        raise HTTPException(404, str(e)) from e
    audit(db, "backup.delete", user=admin, target=f"backup:{name}", request=request)
    return {"ok": True}


def _restore(raw: bytes, label: str, request: Request, service: Service, admin: CurrentUser, db: Session) -> dict[str, Any]:
    try:
        data = backup.read(raw)
    except backup.BackupError as e:
        raise HTTPException(400, str(e)) from e
    counts = backup.restore(db, data)
    service.reload()
    for ref in list(service.refs.values()):  # the rows of internal referentials may have changed
        if ref.is_internal:
            service.publish_internal(ref.id, admin.username, delay=0)
    audit(db, "backup.restore", username=admin.username, target=f"backup:{label}", request=request,
          detail={"created_at": data.get("created_at"), "counts": counts})
    return {"restored": counts, "created_at": data.get("created_at"), "config_files": sorted(data.get("config_files", {})),
            "signed_out": True}


@router.post("/backups/{name}/restore", summary="Restore a configuration backup (replaces the current configuration)")
def restore_backup(name: str, request: Request, confirm: str = Body(..., embed=True), service: Service = Depends(get_service),
                   admin: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    if confirm != CONFIRM:
        raise HTTPException(400, f"type {CONFIRM} to confirm: the current accounts, rights, definitions and settings are replaced")
    try:
        raw = backup.path_of(service.settings.data_dir, name).read_bytes()
    except backup.BackupError as e:
        raise HTTPException(404, str(e)) from e
    return _restore(raw, name, request, service, admin, db)


@router.post("/backups/restore-upload", summary="Restore an uploaded configuration backup")
def restore_upload(request: Request, file: UploadFile = File(...), confirm: str = Form(...), service: Service = Depends(get_service),
                   admin: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    if confirm != CONFIRM:
        raise HTTPException(400, f"type {CONFIRM} to confirm: the current accounts, rights, definitions and settings are replaced")
    return _restore(file.file.read(), file.filename or "upload", request, service, admin, db)
