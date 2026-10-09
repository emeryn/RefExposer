"""Secret manager of the sources: create, update, delete. The values are write-only: never returned."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import CurrentUser, audit, require_admin
from ..db import get_db
from ..models import SourceSecret
from ..config import secret_names
from ..secretstore import NAME_RE, SecretError, check_hosts, seal
from ..secretstore import usage as ref_usage
from ..service import Service
from .deps import get_service

router = APIRouter(prefix="/admin/secrets", tags=["secret manager"], dependencies=[Depends(require_admin)])


class SecretCreate(BaseModel):
    name: str = Field(..., description="Name used in the references: ${secret:<name>}")
    value: str = Field(..., min_length=1, max_length=4096, description="Value (write-only: never returned)")
    description: str | None = Field(None, max_length=255)
    hosts: list[str] = Field(default_factory=list, description="Hosts the secret may be sent to (e.g. api.example.com, "
                                                               "*.example.com); any host when empty")


class SecretUpdate(BaseModel):
    value: str | None = Field(None, max_length=4096, description="New value; unchanged when empty")
    description: str | None = Field(None, max_length=255)
    hosts: list[str] | None = None


def usage(service: Service, db: Session) -> dict[str, list[str]]:
    """Secret name -> referentials and notification channels (notification:<name>) using it."""
    from .. import appsettings

    out = ref_usage(service.refs)
    for ch in appsettings.load(db, "notifications").channels:  # type: ignore[attr-defined]
        for name in secret_names([ch.headers, ch.signing_secret]):
            out.setdefault(name, []).append(f"notification:{ch.name}")
    return out


def secret_dict(s: SourceSecret, used: dict[str, list[str]]) -> dict[str, Any]:
    return {
        "id": s.id,
        "name": s.name,
        "reference": "${secret:" + s.name + "}",
        "description": s.description,
        "hosts": s.hosts or [],
        "used_by": sorted(used.get(s.name, [])),
        "created_at": s.created_at,
        "created_by": s.created_by,
        "updated_at": s.updated_at,
        "updated_by": s.updated_by,
    }


def _get(db: Session, name: str) -> SourceSecret:
    s = db.scalar(select(SourceSecret).where(SourceSecret.name == name))
    if not s:
        raise HTTPException(404, f"unknown secret: '{name}'")
    return s


def _hosts(hosts: list[str]) -> list[str]:
    try:
        return check_hosts(hosts)
    except SecretError as e:
        raise HTTPException(422, str(e)) from e


@router.get("", summary="Secrets (names, descriptions, allowed hosts, referentials using them: never the values)")
def list_secrets(db: Session = Depends(get_db), service: Service = Depends(get_service)):
    used = usage(service, db)
    return [secret_dict(s, used) for s in db.scalars(select(SourceSecret).order_by(SourceSecret.name))]


@router.post("", summary="Create a secret", status_code=201)
def create_secret(body: SecretCreate, request: Request, db: Session = Depends(get_db), service: Service = Depends(get_service),
                  admin: CurrentUser = Depends(require_admin)):
    if not NAME_RE.match(body.name):
        raise HTTPException(422, "invalid name: letters, digits, '.', '_' and '-' (64 characters at most)")
    if db.scalar(select(SourceSecret.id).where(SourceSecret.name == body.name)):
        raise HTTPException(409, f"the secret '{body.name}' already exists")
    s = SourceSecret(name=body.name, value=seal(body.name, body.value), description=body.description or None,
                     hosts=_hosts(body.hosts), created_by=admin.username, updated_by=admin.username)
    db.add(s)
    db.flush()
    audit(db, "secret.create", user=admin, target=f"secret:{s.name}", request=request, detail={"hosts": s.hosts})
    return secret_dict(s, usage(service, db))


@router.patch("/{name}", summary="Change the value, description or allowed hosts of a secret")
def update_secret(name: str, body: SecretUpdate, request: Request, db: Session = Depends(get_db),
                  service: Service = Depends(get_service), admin: CurrentUser = Depends(require_admin)):
    s = _get(db, name)
    changes: dict[str, Any] = {}
    if body.value:
        s.value = seal(s.name, body.value)
        changes["value"] = "changed"
    if body.description is not None:
        s.description = body.description or None
        changes["description"] = s.description
    if body.hosts is not None:
        s.hosts = _hosts(body.hosts)
        changes["hosts"] = s.hosts
    s.updated_by = admin.username
    audit(db, "secret.update", user=admin, target=f"secret:{s.name}", request=request, detail=changes)
    return secret_dict(s, usage(service, db))


@router.delete("/{name}", summary="Delete a secret (refused while a referential uses it, unless force)")
def delete_secret(name: str, request: Request, force: bool = Query(False), db: Session = Depends(get_db),
                  service: Service = Depends(get_service), admin: CurrentUser = Depends(require_admin)):
    s = _get(db, name)
    used = usage(service, db).get(s.name, [])
    if used and not force:
        raise HTTPException(409, f"secret used by {', '.join(sorted(used))}: change these referentials first (or force)")
    db.delete(s)
    audit(db, "secret.delete", user=admin, target=f"secret:{name}", request=request, detail={"used_by": used})
    return {"ok": True}
