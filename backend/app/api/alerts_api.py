"""The alert feed of the signed-in account (alerts.py)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from .. import alerts
from ..auth import CurrentUser, audit, require_user
from ..db import get_db
from ..service import Service
from .deps import get_service

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("", summary="What needs a look, newest first")
def list_alerts(
    limit: int = Query(50, ge=1, le=alerts.MAX_ALERTS),
    kind: list[str] | None = Query(None, description=f"Restrict to some kinds: {', '.join(alerts.KINDS)}"),
    dismissed: bool = Query(False, description="List what this account deleted instead of its open alerts"),
    service: Service = Depends(get_service),
    user: CurrentUser = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Findings of the watchdogs and drifts of the referentials (failed update, stale data, quality rules
    failing, volume that moved a lot), limited to what this account may see.

    `kinds` says which kinds this instance reports at all: an administrator can hide some of them
    (*Settings › Notifications*), and what is hidden there is not counted either. `deleted` counts what this
    account removed from its feed — those are only listed with `dismissed=true`.
    """
    unknown = sorted(set(kind or []) - set(alerts.KINDS))
    if unknown:
        raise HTTPException(400, f"unknown kind(s): {', '.join(unknown)} (available: {', '.join(alerts.KINDS)})")
    shown = alerts.shown_kinds(db)
    feed = alerts.feed(db, service, user, limit, kind, dismissed=dismissed)
    counts = alerts.counts(feed, shown)
    if not dismissed:  # how many are waiting in the deleted list, so it can be offered without opening it
        counts["deleted"] = len(alerts.feed(db, service, user, alerts.MAX_ALERTS, kind, dismissed=True))
    return {"alerts": feed, "counts": counts, "kinds": list(shown)}


class MarkIn(BaseModel):
    """What to mark. No `ids`: every alert this account can see."""

    model_config = ConfigDict(extra="forbid")

    ids: list[str] | None = Field(None, max_length=alerts.MAX_ALERTS)
    read: bool = True


@router.post("/read", summary="Mark alerts as read, or as unread again")
def mark_read(body: MarkIn, request: Request, service: Service = Depends(get_service),
              user: CurrentUser = Depends(require_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Read is per account: marking one changes nothing for anybody else, and nothing about the referential or
    the watchdog behind it. An alert comes back unread if the thing behind it happens again."""
    changed = alerts.mark(db, service, user, body.ids, body.read)
    audit(db, "alerts.read", user=user, request=request,
          detail={"alerts": len(body.ids) if body.ids is not None else "all", "read": body.read,
                  "changed": changed})
    feed = alerts.feed(db, service, user, alerts.MAX_ALERTS)
    return {"changed": changed, "unread": sum(1 for a in feed if not a["read"])}


class DismissIn(BaseModel):
    """What to delete. No `ids`: every alert of the list concerned."""

    model_config = ConfigDict(extra="forbid")

    ids: list[str] | None = Field(None, max_length=alerts.MAX_ALERTS)
    dismissed: bool = True


@router.post("/dismiss", summary="Delete alerts from this feed, or put deleted ones back")
def dismiss_alerts(body: DismissIn, request: Request, service: Service = Depends(get_service),
                   user: CurrentUser = Depends(require_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Deleting is per account and changes nothing about the referential or the watchdog behind the alert: the
    line leaves this feed and its counts, and comes back if the thing behind it happens again. `dismissed`
    false puts back what was deleted — with no `ids`, all of it."""
    changed = alerts.dismiss(db, service, user, body.ids, body.dismissed)
    audit(db, "alerts.dismiss", user=user, request=request,
          detail={"alerts": len(body.ids) if body.ids is not None else "all",
                  "dismissed": body.dismissed, "changed": changed})
    feed = alerts.feed(db, service, user, alerts.MAX_ALERTS)
    return {"changed": changed, "total": len(feed), "unread": sum(1 for a in feed if not a["read"]),
            "deleted": len(alerts.feed(db, service, user, alerts.MAX_ALERTS, dismissed=True))}


@router.delete("/{alert_id}", summary="Delete one alert from this feed")
def delete_alert(alert_id: str, request: Request, service: Service = Depends(get_service),
                 user: CurrentUser = Depends(require_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Same thing as `POST /alerts/dismiss` for a single alert."""
    if not alerts.dismiss(db, service, user, [alert_id], True):
        raise HTTPException(404, "unknown alert, already deleted, or not one this account may see")
    audit(db, "alerts.dismiss", user=user, request=request, target=f"alert:{alert_id}",
          detail={"alerts": 1, "dismissed": True, "changed": 1})
    return {"ok": True}


@router.get("/{alert_id}", summary="One alert, with what it takes to act on it")
def get_alert(alert_id: str, service: Service = Depends(get_service), user: CurrentUser = Depends(require_user),
              db: Session = Depends(get_db)) -> dict[str, Any]:
    """The report the watchdog wrote, the logs of the failed update, the failing quality rules, or the figures
    of the drift — whichever the alert is about."""
    try:
        return alerts.detail(db, service, user, alert_id)
    except alerts.UnknownAlert as e:
        raise HTTPException(404, "unknown alert, or not one this account may see") from e
