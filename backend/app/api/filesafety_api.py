"""File safety (administrators): test the local scanner, and look at what was refused.

Two experimental features, turned on in Settings › Experimental and configured there:

- the **antivirus scan** of the files a source brings in (antivirus.py);
- the **structural checks** on those files, and the neutralising of the values a spreadsheet would execute in
  the CSV and Excel exports (filechecks.py).

These routes are the operational side of the first one: is the scanner answering, how old is its signature
database, and what has it refused. Nothing here reaches the network: the scanner is a daemon or a binary of
this machine, and the signature date is read from what it answers, never fetched.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .. import antivirus, appsettings
from ..auth import CurrentUser, audit, require_admin
from ..db import get_db
from ..service import Service
from .deps import get_service

router = APIRouter(prefix="/admin/file-safety", tags=["file safety"], dependencies=[Depends(require_admin)])


def _enabled(db: Session) -> tuple[bool, bool]:
    """(the antivirus scan is on, the structural checks are on)."""
    experimental: Any = appsettings.load(db, "experimental")
    return bool(getattr(experimental, "antivirus_scan", False)), bool(getattr(experimental, "file_checks", False))


@router.get("", summary="State of the two file-safety features")
def state(service: Service = Depends(get_service), db: Session = Depends(get_db)):
    antivirus_on, checks_on = _enabled(db)
    cfg: Any = appsettings.load(db, "antivirus")
    return {
        "antivirus_scan": antivirus_on,
        "file_checks": checks_on,
        "backend": cfg.backend,
        "scanner": antivirus.scanner_of(cfg) if antivirus_on else None,
        "quarantine": len(antivirus.quarantined(service.settings.data_dir)),
    }


@router.post("/antivirus/test", summary="Is the scanner answering, and how old is its database?")
def test_scanner(body: dict[str, Any] = Body(default_factory=dict), service: Service = Depends(get_service),
                 db: Session = Depends(get_db)):
    """Tests the saved settings, so what is tested is what an update will use. Nothing is downloaded: the age
    of the signature database is read from the scanner's own answer."""
    cfg: Any = appsettings.load(db, "antivirus")
    try:
        info = antivirus.check(service.settings, cfg)
    except antivirus.AntivirusError as e:
        raise HTTPException(502, str(e)) from e
    if body.get("eicar"):
        # The EICAR test string: the one file every scanner agrees to report, and which is not malware. It
        # proves the whole path end to end — our side, the socket, and the scanner's verdict.
        probe = service.settings.tmp_dir / "antivirus-probe"
        probe.mkdir(parents=True, exist_ok=True)
        path = probe / "eicar.com.txt"
        path.write_bytes(antivirus.EICAR)
        try:
            verdict = antivirus.scan_one(service.settings, cfg, path)
            info["probe"] = verdict.as_dict()
            info["detects_eicar"] = verdict.status == "infected"
        except antivirus.AntivirusError as e:
            raise HTTPException(502, str(e)) from e
        finally:
            path.unlink(missing_ok=True)
    return info


@router.get("/quarantine", summary="Files refused by the checks, newest first")
def list_quarantine(service: Service = Depends(get_service)):
    return {"entries": antivirus.quarantined(service.settings.data_dir),
            "folder": str(antivirus.quarantine_dir(service.settings.data_dir))}


@router.delete("/quarantine", summary="Empty the quarantine")
def empty_quarantine(request: Request, service: Service = Depends(get_service),
                     admin: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    root = antivirus.quarantine_dir(service.settings.data_dir)
    count = len(antivirus.quarantined(service.settings.data_dir))
    shutil.rmtree(root, ignore_errors=True)
    audit(db, "quarantine.empty", user=admin, target="quarantine", request=request, detail={"entries": count})
    return {"deleted": count}


@router.delete("/quarantine/{folder:path}", summary="Delete one quarantined set of files")
def delete_quarantined(folder: str, request: Request, service: Service = Depends(get_service),
                       admin: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    root = antivirus.quarantine_dir(service.settings.data_dir).resolve()
    target = (root / folder).resolve()
    if not target.is_relative_to(root) or not target.is_dir():
        raise HTTPException(404, "unknown quarantine entry")
    shutil.rmtree(target, ignore_errors=True)
    # Leave no empty referential folder behind
    parent = target.parent
    if parent != root and parent.is_dir() and not any(parent.iterdir()):
        parent.rmdir()
    audit(db, "quarantine.delete", user=admin, target=f"quarantine:{folder}", request=request)
    return {"ok": True}


@router.post("/checks/probe", summary="What the structural checks say about a given file")
def probe_checks(body: dict[str, Any] = Body(...), service: Service = Depends(get_service),
                 db: Session = Depends(get_db)):
    """Run the structural checks on a file of an upload, to see what they would say. Reads nothing else:
    `upload_id` and `name` are those of a file this instance holds in its upload folder."""
    from .. import filechecks
    from ..preview import uploads_dir

    upload_id, name = str(body.get("upload_id") or ""), str(body.get("name") or "")
    base = (uploads_dir(service.settings) / upload_id).resolve()
    path = (base / name).resolve()
    if not upload_id or not path.is_relative_to(base) or not path.is_file():
        raise HTTPException(404, "unknown uploaded file")
    cfg: Any = appsettings.load(db, "checks")
    findings = filechecks.inspect([path], str(body.get("format") or "") or None, cfg)
    return findings.as_dict()


def quarantine_path(data_dir: Path, folder: str) -> Path:  # pragma: no cover - used by the tests
    return antivirus.quarantine_dir(data_dir) / folder
