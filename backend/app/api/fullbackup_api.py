"""Full backups: the whole instance in one archive, on the server or in an S3 bucket (administrators).

What backup.py and `/admin/backups` handle is the configuration alone. These routes handle the archive that also
carries data/: see fullbackup.py for what goes in it and what is deliberately left out.

A restore replaces the database, the definitions and the published data. It is irreversible, so it asks for the
same `confirm` word as a configuration restore, and the archive is read and checked in full before anything of
the instance is touched.

`secret_key` carries the encryption key of the instance the archive comes from, which is what restoring onto a
*second* instance needs: the archive is opened with it and everything it holds is re-encrypted with the local
key (see rekey.py). The key is never stored, never logged and never written to the audit trail — only the
one-way fingerprint of a key appears anywhere.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import appsettings, fullbackup
from ..auth import CurrentUser, audit, require_admin
from ..db import get_db
from ..s3 import S3Error
from ..service import Service
from .deps import get_service

router = APIRouter(prefix="/admin/full-backups", tags=["full backups"], dependencies=[Depends(require_admin)])

CONFIRM = "RESTORE EVERYTHING"


class RestoreIn(BaseModel):
    confirm: str = Field(..., description=f"'{CONFIRM}'")
    remove_extra: bool = Field(False, description="Also delete the data of referentials the backup does not carry")
    secret_key: str = Field("", max_length=4096, description="REFEX_SECRET_KEY (or the content of data/.secret_key) "
                                                             "of the instance the backup comes from, when it is not "
                                                             "this one. What it carries is then re-encrypted with "
                                                             "this instance's key.")


def _settings(db: Session) -> Any:
    return appsettings.load(db, "backup")


def _bucket(db: Session) -> tuple[Any, str]:
    cfg = _settings(db)
    if not cfg.s3_enabled:
        raise HTTPException(409, "the copy to a bucket is not enabled (Settings › Backup)")
    return cfg.s3, cfg.prefix


def _check(confirm: str) -> None:
    if confirm != CONFIRM:
        raise HTTPException(400, f"type '{CONFIRM}' to confirm: the database, the definitions and the published data "
                                 "are all replaced by those of the backup")


# --------------------------------------------------------------------------- on the server

@router.get("", summary="Full backups held on the server")
def list_local(service: Service = Depends(get_service), db: Session = Depends(get_db)):
    cfg = _settings(db)
    from ..crypto import key_fingerprint

    return {"backups": fullbackup.list_files(service.settings.data_dir),
            "s3_enabled": cfg.s3_enabled,
            "bucket": cfg.bucket if cfg.s3_enabled else None,
            "confirm_word": CONFIRM,
            # One-way name of this instance's key: what tells an archive apart from one made elsewhere
            "key_fingerprint": key_fingerprint()}


@router.post("/run", summary="Write a full backup now")
def run_now(service: Service = Depends(get_service), admin: CurrentUser = Depends(require_admin)):
    """Starts the `full_backup` task: the archive can take a while, so it runs in the background and its
    progress and result are in the history of that task."""
    try:
        return service.tasks.submit("full_backup", admin.username)
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e


@router.get("/{name}", summary="Download a full backup")
def download(name: str, request: Request, service: Service = Depends(get_service),
             admin: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    try:
        path = fullbackup.path_of(service.settings.data_dir, name)
    except fullbackup.BackupError as e:
        raise HTTPException(404, str(e)) from e
    audit(db, "fullbackup.download", user=admin, target=f"full-backup:{name}", request=request)
    return FileResponse(path, media_type="application/octet-stream", filename=name)


@router.get("/{name}/manifest", summary="What a full backup holds")
def manifest(name: str, service: Service = Depends(get_service)):
    try:
        return fullbackup.read_manifest(fullbackup.path_of(service.settings.data_dir, name))
    except fullbackup.BackupError as e:
        raise HTTPException(400, str(e)) from e


@router.delete("/{name}", summary="Delete a full backup")
def delete(name: str, request: Request, service: Service = Depends(get_service),
           admin: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    try:
        fullbackup.path_of(service.settings.data_dir, name).unlink()
    except fullbackup.BackupError as e:
        raise HTTPException(404, str(e)) from e
    audit(db, "fullbackup.delete", user=admin, target=f"full-backup:{name}", request=request)
    return {"ok": True}


# --------------------------------------------------------------------------- restoring

def _apply(service: Service, db: Session) -> None:
    """Pick up a restored configuration without a restart: caches, proxy, syslog, profiles, then the catalogue."""
    from .. import llmprofiles, network, syslog

    appsettings.clear_cache()
    llmprofiles.bootstrap(db)
    network.set_proxy(appsettings.load(db, "proxy"))
    syslog.install(appsettings.load(db, "syslog"), appsettings.load(db, "proxy"))
    service.reload()
    service.tasks.schedule_all()


def _restore(path: Path, label: str, body: RestoreIn, request: Request, service: Service,
             admin: CurrentUser, db: Session) -> dict[str, Any]:
    _check(body.confirm)
    if service.active:
        raise HTTPException(409, f"an update is running ({', '.join(sorted(service.active))}): wait for it to end, "
                                 "it would write over the restored data")
    try:
        done = fullbackup.restore(db, service.settings, path, remove_extra=body.remove_extra,
                                  source_key=body.secret_key or None)
    except (fullbackup.BackupError, ValueError) as e:
        raise HTTPException(400, str(e)) from e
    _apply(service, db)
    audit(db, "fullbackup.restore", username=admin.username, target=f"full-backup:{label}", request=request,
          detail={"created_at": done.created_at, "app_version": done.app_version, "rows": sum(done.counts.values()),
                  "referentials": len(done.referentials), "removed": done.removed, "remove_extra": body.remove_extra,
                  # The key itself never appears: only how many values it re-encrypted
                  "rekeyed": (done.rekeyed or {}).get("total")})
    return {"restored": done.counts, "created_at": done.created_at, "app_version": done.app_version,
            "referentials": done.referentials, "config_files": done.config_files, "data_files": done.data_files,
            "data_bytes": done.data_bytes, "removed": done.removed, "signed_out": True, "rekeyed": done.rekeyed}


@router.post("/{name}/restore", summary="Restore a full backup (replaces the configuration and the data)")
def restore(name: str, body: RestoreIn, request: Request, service: Service = Depends(get_service),
            admin: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    try:
        path = fullbackup.path_of(service.settings.data_dir, name)
    except fullbackup.BackupError as e:
        raise HTTPException(404, str(e)) from e
    return _restore(path, name, body, request, service, admin, db)


@router.post("/restore-upload", summary="Restore an uploaded full backup")
def restore_upload(request: Request, file: UploadFile = File(...), confirm: str = Form(...),
                   remove_extra: bool = Form(False), secret_key: str = Form(""),
                   service: Service = Depends(get_service),
                   admin: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    """The archive is written to the temporary folder first: it is read from a file, never held in memory."""
    _check(confirm)
    name = Path(file.filename or "upload").name
    if not fullbackup.NAME_RE.match(name):
        raise HTTPException(400, "this is not a RefExposer full backup (expected refexposer-full-<date>.tar.gz[.enc])")
    staging = service.settings.tmp_dir / "uploads" / "full-backups"
    staging.mkdir(parents=True, exist_ok=True)
    path = staging / name
    try:
        with path.open("wb") as f:
            shutil.copyfileobj(file.file, f, 1 << 20)
        return _restore(path, name, RestoreIn(confirm=confirm, remove_extra=remove_extra, secret_key=secret_key),
                        request, service, admin, db)
    finally:
        path.unlink(missing_ok=True)


# --------------------------------------------------------------------------- in the bucket

@router.get("/remote/list", summary="Full backups held in the S3 bucket")
def list_remote(service: Service = Depends(get_service), db: Session = Depends(get_db)):
    cfg, prefix = _bucket(db)
    try:
        return fullbackup.list_remote(cfg, service.settings, prefix)
    except S3Error as e:
        raise HTTPException(502, str(e)) from e


@router.post("/remote/{name}/fetch", summary="Bring a backup of the bucket back onto the server")
def fetch_remote(name: str, request: Request, service: Service = Depends(get_service),
                 admin: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    """Downloaded into data/.backups/full/, from where it is restored like any local backup."""
    cfg, prefix = _bucket(db)
    try:
        path = fullbackup.download_remote(cfg, service.settings, prefix, name)
    except fullbackup.BackupError as e:
        raise HTTPException(400, str(e)) from e
    except S3Error as e:
        raise HTTPException(502, str(e)) from e
    audit(db, "fullbackup.fetch", user=admin, target=f"full-backup:{name}", request=request,
          detail={"bucket": cfg.bucket})
    return {"name": name, "size": path.stat().st_size}


@router.delete("/remote/{name}", summary="Delete a full backup from the bucket")
def delete_remote(name: str, request: Request, service: Service = Depends(get_service),
                  admin: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    cfg, prefix = _bucket(db)
    try:
        fullbackup.delete_remote(cfg, service.settings, prefix, name)
    except fullbackup.BackupError as e:
        raise HTTPException(400, str(e)) from e
    except S3Error as e:
        raise HTTPException(502, str(e)) from e
    audit(db, "fullbackup.delete_remote", user=admin, target=f"full-backup:{name}", request=request,
          detail={"bucket": cfg.bucket})
    return {"ok": True}


@router.post("/remote/test", summary="Test the S3 destination (list, then write and delete a probe object)")
def test_remote(body: dict[str, Any] = Body(default_factory=dict), service: Service = Depends(get_service),
                db: Session = Depends(get_db)):
    """Tests the saved settings. A secret access key left empty in the form keeps the stored one, so the test
    checks what the task will actually use."""
    from ..s3 import S3Client

    cfg = _settings(db)
    if not (cfg.endpoint and cfg.bucket):
        raise HTTPException(422, "configure the endpoint and the bucket first")
    try:
        with S3Client(cfg.s3, service.settings, timeout=30) as client:
            return client.check_access(cfg.prefix, write=bool(body.get("write", True)))
    except S3Error as e:
        raise HTTPException(502, str(e)) from e
