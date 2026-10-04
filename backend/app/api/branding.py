"""Branding: title, subtitle and company logo (public, shown before sign-in), edited by administrators."""

from __future__ import annotations

import base64
import hashlib

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from sqlalchemy.orm import Session

from .. import appsettings
from ..auth import CurrentUser, audit, require_admin
from ..db import get_db
from ..models import AppSetting
from .deps import public_base

router = APIRouter(tags=["branding"])

LOGO_KEY = "branding_logo"
LOGO_MAX_BYTES = 512 * 1024
LOGO_TYPES = {
    "image/png": b"\x89PNG",
    "image/jpeg": b"\xff\xd8\xff",
    "image/webp": b"RIFF",
    "image/svg+xml": None,
}


def _logo(db: Session) -> AppSetting | None:
    row = db.get(AppSetting, LOGO_KEY)
    return row if row and row.value.get("data") else None


@router.get("/branding", summary="Application name, logo and public URL (public)")
def branding(request: Request, db: Session = Depends(get_db)):
    cfg = appsettings.load(db, "branding")
    logo = _logo(db)
    return {
        "title": cfg.title,
        "subtitle": cfg.subtitle,
        "logo_url": f"/api/branding/logo?v={logo.value['sha']}" if logo else None,
        "public_url": public_base(request),
    }


@router.get("/branding/logo", summary="Company logo (public)", response_class=Response)
def branding_logo(db: Session = Depends(get_db)):
    logo = _logo(db)
    if not logo:
        raise HTTPException(404, "no logo")
    return Response(
        base64.b64decode(logo.value["data"]),
        media_type=logo.value["type"],
        headers={
            "Cache-Control": "public, max-age=86400",
            # An uploaded SVG is only ever displayed as an image: no script, even when opened directly
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/admin/settings/branding/logo", summary="Upload the company logo (PNG, JPEG, WebP or SVG, 512 KB max.)")
async def upload_logo(request: Request, file: UploadFile = File(...), db: Session = Depends(get_db), admin: CurrentUser = Depends(require_admin)):
    data = await file.read(LOGO_MAX_BYTES + 1)
    if len(data) > LOGO_MAX_BYTES:
        raise HTTPException(413, "the logo must not exceed 512 KB")
    ctype = (file.content_type or "").split(";")[0].strip().lower()
    if ctype not in LOGO_TYPES:
        raise HTTPException(400, "unsupported image: use PNG, JPEG, WebP or SVG")
    magic = LOGO_TYPES[ctype]
    head = data.lstrip()[:512].lower()
    if (magic and not data.startswith(magic)) or (magic is None and b"<svg" not in head and not head.startswith(b"<?xml")):
        raise HTTPException(400, f"the file is not a valid {ctype.split('/')[1].split('+')[0].upper()} image")
    value = {"data": base64.b64encode(data).decode(), "type": ctype, "sha": hashlib.sha256(data).hexdigest()[:16], "name": file.filename}
    row = db.get(AppSetting, LOGO_KEY)
    if row:
        row.value, row.updated_by = value, admin.username
    else:
        db.add(AppSetting(key=LOGO_KEY, value=value, updated_by=admin.username))
    audit(db, "settings.branding_logo", user=admin, target="settings:branding", request=request, detail={"type": ctype, "bytes": len(data)})
    return {"logo_url": f"/api/branding/logo?v={value['sha']}"}


@router.delete("/admin/settings/branding/logo", summary="Remove the company logo")
def delete_logo(request: Request, db: Session = Depends(get_db), admin: CurrentUser = Depends(require_admin)):
    row = db.get(AppSetting, LOGO_KEY)
    if row:
        db.delete(row)
    audit(db, "settings.branding_logo", user=admin, target="settings:branding", request=request, detail={"removed": True})
    return {"logo_url": None}
