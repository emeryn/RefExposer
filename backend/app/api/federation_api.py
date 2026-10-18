"""Synchronization between RefExposer instances (see federation.py).

- /federation/catalog: on the instance sharing its referentials, what the calling account (a service account of the
  replica) may read, with the definitions and the published versions.
- /admin/federation: on the replica, the peers (URL, token, chosen referentials, schedule), their state, a test of the
  connection and a synchronization on demand.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import appsettings, federation
from ..auth import CurrentUser, audit, require_admin, require_user
from ..db import get_db
from ..federation import FederationError, Peer
from ..models import ReferentialDefinition, SourceSecret
from ..secretstore import seal
from ..service import Service
from .deps import get_service, public_base

router = APIRouter(tags=["federation"])
admin_router = APIRouter(prefix="/admin/federation", tags=["federation"], dependencies=[Depends(require_admin)])


@router.get("/federation/catalog", summary="Referentials this account may replicate (federation)")
def catalog(request: Request, service: Service = Depends(get_service), user: CurrentUser = Depends(require_user)):
    """For another RefExposer instance: the readable referentials with data, their definitions (quality rules, SQL
    transformation, key, indexes...) and published versions. Called with the API token of a service account."""
    return federation.build_catalog(service, user, public_base(request))


class PeerIn(BaseModel):
    url: str
    token: str | None = Field(None, description="API token of the service account of the peer (kept when empty)")
    prefix: str = ""
    datasets: list[str] = Field(default_factory=list)
    schedule: str = "0 * * * *"
    enabled: bool = True
    category: str = ""
    grant_groups: list[str] = Field(default_factory=list)


class CatalogRequest(BaseModel):
    url: str | None = None
    token: str | None = None
    peer: str | None = Field(None, description="Use the URL and token of this peer")


def _secret_name(name: str) -> str:
    return f"peer-{name}"


def _peer_dict(service: Service, peer: Peer, state: dict[str, Any]) -> dict[str, Any]:
    s = state.get(peer.name) or {}
    replicas = []
    for rid, d in sorted((s.get("datasets") or {}).items()):
        lid = d.get("local_id")
        meta = service.metas.get(lid, {}) if lid else {}
        replicas.append({"remote_id": rid, "local_id": lid, "name": d.get("name"), "exists": lid in service.refs,
                         "missing": d.get("missing", False), "remote_version": d.get("remote_version"),
                         "replicated_version": d.get("version"), "remote_rows": d.get("remote_rows"),
                         "rows": meta.get("row_count"), "status": meta.get("status"), "last_success_at": meta.get("last_success_at")})
    nxt = service.scheduler.get_job(f"peer:{peer.name}") if service.scheduler.running else None
    return {**peer.model_dump(), "last_sync_at": s.get("last_sync_at"), "status": s.get("status"), "message": s.get("message"),
            "instance": s.get("instance"), "replicas": replicas, "last_report": s.get("last_report"),
            "next_sync_at": nxt.next_run_time.isoformat() if nxt and nxt.next_run_time else None}


@admin_router.get("", summary="Peers (other RefExposer instances replicated here) and their state")
def list_peers(service: Service = Depends(get_service)):
    state = federation.load_state(service)
    return {"peers": [_peer_dict(service, p, state) for p in federation.load_settings().peers]}


@admin_router.post("/catalog", summary="Test a connection: referentials shared by a peer")
def peer_catalog(body: CatalogRequest, service: Service = Depends(get_service)):
    try:
        if body.peer and not body.token:
            peer = federation.get_peer(body.peer)
            url, token = body.url or peer.url, federation.peer_token(peer)
        else:
            url, token = body.url, body.token
        if not url or not token:
            raise FederationError("give the URL of the peer and the API token of its service account")
        doc = federation.fetch_catalog(service, url, token)
    except FederationError as e:
        raise HTTPException(400, str(e)) from e
    return {"instance": doc.get("instance"), "datasets": [
        {k: d.get(k) for k in ("id", "name", "category", "kind", "format", "row_count", "version", "data_updated_at", "key",
                               "confidential", "transform", "federation")}
        | {"quality_rules": len((d.get("quality") or {}).get("rules") or [])} for d in doc.get("datasets") or []]}


@admin_router.put("/peers/{name}", summary="Add or change a peer")
def save_peer(name: str, body: PeerIn, request: Request, db: Session = Depends(get_db), service: Service = Depends(get_service),
              admin: CurrentUser = Depends(require_admin)):
    settings = federation.load_settings()
    others = [p for p in settings.peers if p.name != name]
    current = next((p for p in settings.peers if p.name == name), None)
    try:
        peer = Peer(name=name, secret=_secret_name(name), **body.model_dump(exclude={"token"}))
    except ValidationError as e:
        raise HTTPException(422, "; ".join(f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors())) from e
    secret = db.scalar(select(SourceSecret).where(SourceSecret.name == peer.secret))
    if body.token:
        token = body.token.strip()
        try:  # the token is checked before anything is stored
            federation.fetch_catalog(service, peer.url, token)
        except FederationError as e:
            raise HTTPException(400, str(e)) from e
        if secret is None:
            secret = SourceSecret(name=peer.secret, created_by=admin.username)
            db.add(secret)
        secret.value = seal(peer.secret, token)
        secret.description = f"API token of the RefExposer peer '{name}' (federation)"
        secret.hosts = [peer.host]  # only ever sent to the peer
        secret.updated_by = admin.username
    elif secret is None:
        raise HTTPException(422, "give the API token of the service account of the peer")
    elif current and current.host != peer.host:
        raise HTTPException(422, "the address of the peer changed: give its API token again")
    settings = federation.FederationSettings(peers=sorted([*others, peer], key=lambda p: p.name))
    appsettings.save(db, "federation", settings, admin.username)
    audit(db, "federation.peer.save", user=admin, target=f"peer:{name}", request=request,
          detail={"url": peer.url, "datasets": peer.datasets or "all", "token_changed": bool(body.token), "created": current is None})
    service._schedule_all()
    return _peer_dict(service, peer, federation.load_state(service))


@admin_router.delete("/peers/{name}", summary="Remove a peer: its replicas stay (no longer synchronized), or are deleted with remove_replicas")
def delete_peer(name: str, request: Request, remove_replicas: bool = False, db: Session = Depends(get_db),
                service: Service = Depends(get_service), admin: CurrentUser = Depends(require_admin)):
    import shutil

    from sqlalchemy import delete

    from ..models import Grant
    from ..storage import RefPaths

    settings = federation.load_settings()
    if not any(p.name == name for p in settings.peers):
        raise HTTPException(404, f"unknown peer: '{name}'")
    removed = []
    for d in db.scalars(select(ReferentialDefinition)):
        if ((d.config or {}).get("federation") or {}).get("peer") != name:
            continue
        if remove_replicas:
            if d.id in service.active:
                raise HTTPException(409, f"an update of '{d.id}' is running: retry when it is finished")
            db.delete(d)
            db.execute(delete(Grant).where(Grant.referential_id == d.id))
            removed.append(d.id)
    secret = db.scalar(select(SourceSecret).where(SourceSecret.name == _secret_name(name)))
    if secret is not None and remove_replicas:
        db.delete(secret)
    appsettings.save(db, "federation", federation.FederationSettings(peers=[p for p in settings.peers if p.name != name]), admin.username)
    audit(db, "federation.peer.delete", user=admin, target=f"peer:{name}", request=request, detail={"replicas_removed": removed})
    federation.forget_peer(service, name)
    service.reload()
    for rid in removed:
        shutil.rmtree(RefPaths(service.settings.data_dir, rid).root, ignore_errors=True)
    return {"ok": True, "replicas_removed": removed}


@admin_router.post("/peers/{name}/sync", summary="Synchronize with a peer now")
def sync_now(name: str, request: Request, db: Session = Depends(get_db), service: Service = Depends(get_service),
             admin: CurrentUser = Depends(require_admin)):
    try:
        report = federation.sync_peer(service, name, admin.username)
    except FederationError as e:
        raise HTTPException(400, str(e)) from e
    audit(db, "federation.peer.sync", user=admin, target=f"peer:{name}", request=request, detail={"status": report["status"]})
    return report
