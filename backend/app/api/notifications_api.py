"""Notification channels (e-mail, webhook) of the referential events, their test and their deliveries."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import ValidationError
from sqlalchemy.orm import Session

from .. import appsettings
from ..appsettings import NotificationChannel, NotificationsSettings
from ..auth import CurrentUser, audit, require_admin
from ..db import get_db
from ..notify import check_template, payload
from ..secretstore import SecretError, check_references, mask_source, unmask_source
from ..service import Service
from .deps import get_service

router = APIRouter(prefix="/admin/notifications", tags=["notifications"], dependencies=[Depends(require_admin)])


def _public(ch: NotificationChannel) -> dict[str, Any]:
    data = ch.model_dump()
    data["headers"] = (mask_source({"headers": ch.headers}) or {}).get("headers", {})  # literal values masked
    return data


def _channels(db: Session) -> list[NotificationChannel]:
    return list(appsettings.load(db, "notifications").channels)  # type: ignore[attr-defined]


def _save(db: Session, channels: list[NotificationChannel], username: str) -> None:
    appsettings.save(db, "notifications", NotificationsSettings(channels=channels), username)


def _validated(db: Session, body: dict[str, Any], stored: NotificationChannel | None, service: Service) -> NotificationChannel:
    data = {**body, "id": stored.id if stored else uuid.uuid4().hex[:12]}
    try:
        data["headers"] = (unmask_source({"headers": data.get("headers") or {}}, {"headers": stored.headers if stored else {}}) or {}).get("headers", {})
        ch = NotificationChannel.model_validate(data).check()
        check_references(db, {"headers": ch.headers, "signing": ch.signing_secret})
        if ch.template:
            check_template(ch.template)
    except ValidationError as e:
        raise HTTPException(422, "; ".join(f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors())) from e
    except (SecretError, ValueError) as e:
        raise HTTPException(422, str(e)) from e
    unknown = [r for r in ch.referentials if r not in service.refs]
    if unknown:
        raise HTTPException(422, f"unknown referential(s): {', '.join(unknown)}")
    return ch


@router.get("", summary="Notification channels (literal header values masked)")
def list_channels(db: Session = Depends(get_db), service: Service = Depends(get_service)):
    return {
        "channels": [_public(c) for c in _channels(db)],
        "smtp_configured": appsettings.load(db, "smtp").configured,  # type: ignore[attr-defined]
        "events": ["failure", "recovered", "published"],
        "categories": sorted({r.category for r in service.refs.values()}),
    }


@router.post("", summary="Create a notification channel", status_code=201)
def create_channel(body: dict[str, Any], request: Request, db: Session = Depends(get_db), service: Service = Depends(get_service),
                   admin: CurrentUser = Depends(require_admin)):
    ch = _validated(db, body, None, service)
    _save(db, [*_channels(db), ch], admin.username)
    audit(db, "notification.create", user=admin, target=f"notification:{ch.id}", request=request,
          detail={"name": ch.name, "type": ch.type, "events": ch.events})
    return _public(ch)


@router.put("/{channel_id}", summary="Update a notification channel")
def update_channel(channel_id: str, body: dict[str, Any], request: Request, db: Session = Depends(get_db),
                   service: Service = Depends(get_service), admin: CurrentUser = Depends(require_admin)):
    channels = _channels(db)
    stored = next((c for c in channels if c.id == channel_id), None)
    if stored is None:
        raise HTTPException(404, "unknown notification channel")
    ch = _validated(db, body, stored, service)
    _save(db, [ch if c.id == channel_id else c for c in channels], admin.username)
    audit(db, "notification.update", user=admin, target=f"notification:{ch.id}", request=request,
          detail={"name": ch.name, "type": ch.type, "events": ch.events, "enabled": ch.enabled})
    return _public(ch)


@router.delete("/{channel_id}", summary="Delete a notification channel")
def delete_channel(channel_id: str, request: Request, db: Session = Depends(get_db), admin: CurrentUser = Depends(require_admin)):
    channels = _channels(db)
    if not any(c.id == channel_id for c in channels):
        raise HTTPException(404, "unknown notification channel")
    _save(db, [c for c in channels if c.id != channel_id], admin.username)
    audit(db, "notification.delete", user=admin, target=f"notification:{channel_id}", request=request)
    return {"ok": True}


@router.post("/{channel_id}/test", summary="Send a test notification now (one attempt)")
def test_channel(channel_id: str, referential_id: str | None = Query(None), db: Session = Depends(get_db),
                 service: Service = Depends(get_service)):
    ch = next((c for c in _channels(db) if c.id == channel_id), None)
    if ch is None:
        raise HTTPException(404, "unknown notification channel")
    ref = service.refs.get(referential_id or "") or next(iter(service.refs.values()), None)
    info = ({"id": ref.id, "name": ref.name, "category": ref.category, "owner": ref.owner} if ref
            else {"id": "example", "name": "Example", "category": "Test", "owner": None})
    run = {"id": "test", "status": "error", "trigger": "test", "message": "Test notification sent from the administration"}
    return service.notifier.send_now(ch, payload("test", info, run, "ok", service.settings.public_url))


@router.get("/deliveries", summary="Last deliveries (success or error)")
def deliveries(limit: int = Query(100, ge=1, le=500), service: Service = Depends(get_service)):
    return service.notifier.deliveries(limit)
