"""Scheduled watchdogs: the assistant investigating a fixed set of referentials on its own (watchdog.py).

Who may do what: administrators see and manage every watchdog, an advanced user manages its own. Creating one
needs the assistant (with its agent mode) to be available for the account — a watchdog is the same agent, on a
schedule. The scope is always chosen — a list of referentials, or every one the account may read — and the
account the investigation runs with must be able to read all of it: a watchdog never gets access its owner does
not have.
"""

from __future__ import annotations

from typing import Any, Literal

from apscheduler.triggers.cron import CronTrigger
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import appsettings, watchdog
from ..appsettings import EMAIL_RE
from ..auth import CurrentUser, audit, require_user
from ..db import get_db
from ..llm import agent as agentlib
from ..models import SEVERITIES, User, WatchdogRun, WatchdogTask
from ..service import Service
from .deps import get_service

router = APIRouter(prefix="/watchdogs", tags=["watchdogs"])

MAX_TASKS = 50
MAX_SCOPE = 20


class WatchdogIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=120)
    description: str = Field("", max_length=500)
    # The only referentials the investigation may read. Empty is allowed only with `all_referentials`.
    referential_ids: list[str] = Field(default_factory=list, max_length=MAX_SCOPE)
    # Look at everything instead of a list: the scope becomes what the account below may read, and follows its
    # rights as they change. Confidential referentials stay out unless the assistant settings allow them.
    all_referentials: bool = False
    # Investigate what arrived since the last run, instead of the whole data every time
    only_new_data: bool = False
    trigger_type: Literal["cron", "on_sync"] = "cron"
    schedule: str | None = Field(None, max_length=100)
    prompt: str = Field("", max_length=8000)
    template: str | None = Field(None, max_length=32)
    notify_emails: list[str] = Field(default_factory=list, max_length=50)
    notify_channels: list[str] = Field(default_factory=list, max_length=20)
    min_severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] = "LOW"
    enabled: bool = True
    # Administrators only: the account whose rights the investigation runs with (its author by default)
    user_id: int | None = None

    @field_validator("referential_ids")
    @classmethod
    def _scope(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        return list(dict.fromkeys(r.strip() for r in v if r and r.strip()))

    @model_validator(mode="after")
    def _scope_given(self):
        """A watchdog needs a scope: a list of referentials, or every one its account may read."""
        if not self.all_referentials and not self.referential_ids:
            raise ValueError("choose at least one referential, or let it look at every referential")
        return self

    @field_validator("notify_emails")
    @classmethod
    def _emails(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        out = []
        for address in (a.strip() for a in v):
            if not address:
                continue
            if not EMAIL_RE.match(address):
                raise ValueError(f"invalid e-mail address: {address}")
            if address not in out:
                out.append(address)
        return out

    @field_validator("template")
    @classmethod
    def _template(cls, v: str | None) -> str | None:
        if v and v not in watchdog.TEMPLATES:
            raise ValueError(f"unknown template: {v}")
        return v


class WatchdogPatch(WatchdogIn):
    """Same fields, all optional: a change sends only what it changes."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, min_length=1, max_length=120)
    description: str | None = Field(None, max_length=500)
    referential_ids: list[str] | None = Field(None, max_length=MAX_SCOPE)
    all_referentials: bool | None = None
    only_new_data: bool | None = None
    trigger_type: Literal["cron", "on_sync"] | None = None
    prompt: str | None = Field(None, max_length=8000)
    notify_emails: list[str] | None = None
    notify_channels: list[str] | None = None
    min_severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] | None = None
    enabled: bool | None = None

    @model_validator(mode="after")
    def _scope_given(self):
        """Only what the change sends is checked here; the merged scope is checked in `_check`."""
        if "referential_ids" in self.model_fields_set and not self.referential_ids and not self.all_referentials:
            raise ValueError("choose at least one referential, or let it look at every referential")
        return self


# --------------------------------------------------------------------------- access

def _available(db: Session, user: CurrentUser) -> Any:
    """The assistant settings, if watchdogs are on and this account may use them.

    They are off by default and reserved to administrators and advanced users: for anyone else the whole
    feature answers 403, and the interface does not show the menu.
    """
    cfg = appsettings.load(db, "assistant")
    if not watchdog.enabled(cfg) or not agentlib.allowed(cfg, user):
        raise HTTPException(403, "scheduled watchdogs are disabled, or not available for this account")
    if not watchdog.may_use(user):
        raise HTTPException(403, "watchdogs are reserved to administrators and advanced users")
    return cfg


def _task(db: Session, user: CurrentUser, task_id: int) -> WatchdogTask:
    task = db.get(WatchdogTask, task_id)
    if task is None or (not user.is_admin and task.user_id != user.id):
        raise HTTPException(404, "unknown watchdog")
    return task


def _owner(db: Session, user: CurrentUser, user_id: int | None) -> User:
    """The account the investigation runs with: the author, or the one an administrator chose."""
    if user_id is None or user_id == user.id:
        owner = db.get(User, user.id)
    elif not user.is_admin:
        raise HTTPException(403, "only an administrator can make a watchdog run as another account")
    else:
        owner = db.get(User, user_id)
    if owner is None:
        raise HTTPException(404, "unknown account")
    if owner.auth_source == "service" or not owner.is_active or owner.status != "active":
        raise HTTPException(400, "this account cannot run an investigation (disabled, pending, or a service account)")
    return owner


def _check(db: Session, service: Service, user: CurrentUser, owner: User, data: dict[str, Any]) -> None:
    """Refuse a watchdog that could not run: unknown referential, a scope its account cannot read, an invalid
    crontab, a notification channel that does not exist."""
    scope = data.get("referential_ids") or []
    if not data.get("all_referentials") and not scope:
        raise HTTPException(400, "choose at least one referential, or let it look at every referential")
    unknown = [r for r in scope if r not in service.refs]
    if unknown:
        raise HTTPException(400, f"unknown referential(s): {', '.join(unknown)}")
    # The requester must see what it points the agent at, and so must the account it will run as
    hidden = [r for r in scope if not user.can_read(r)]
    if hidden:
        raise HTTPException(404, f"unknown referential(s): {', '.join(hidden)}")
    as_owner = watchdog.current_user(db, service, owner, None, frozenset())
    unreachable = [r for r in scope if not as_owner.can_read(r)]
    if unreachable:
        raise HTTPException(400, f"the account '{owner.username}' cannot read: {', '.join(unreachable)}")
    if data.get("all_referentials"):
        # Watching everything is watching everything *that account* reads: refuse one that would read nothing
        if not any(as_owner.can_read(r.id) for r in service.refs.values()):
            raise HTTPException(400, f"the account '{owner.username}' may read no referential at all")
    if data.get("trigger_type") == "cron":
        schedule = (data.get("schedule") or "").strip()
        if not schedule:
            raise HTTPException(400, "a scheduled watchdog needs a crontab expression")
        try:
            CronTrigger.from_crontab(schedule)
        except ValueError as e:
            raise HTTPException(400, f"invalid crontab expression: {e}") from e
    ids = {c.id for c in appsettings.load(db, "notifications").channels}
    missing = [c for c in data.get("notify_channels") or [] if c not in ids]
    if missing:
        raise HTTPException(400, "unknown notification channel(s): see Settings › Notifications")
    if data.get("min_severity") and data["min_severity"] not in SEVERITIES:
        raise HTTPException(400, f"severity: one of {', '.join(SEVERITIES)}")


# --------------------------------------------------------------------------- templates and history

@router.get("/templates", summary="Ready-made investigations offered when creating a watchdog")
def templates(db: Session = Depends(get_db), user: CurrentUser = Depends(require_user)) -> list[dict[str, Any]]:
    _available(db, user)
    return [{"id": key, **value} for key, value in watchdog.TEMPLATES.items()]


@router.get("/runs", summary="Latest investigations, across the watchdogs of this account")
def recent_runs(limit: int = 50, db: Session = Depends(get_db), service: Service = Depends(get_service),
                user: CurrentUser = Depends(require_user)) -> list[dict[str, Any]]:
    _available(db, user)
    mine = select(WatchdogTask.id)
    if not user.is_admin:
        mine = mine.where(WatchdogTask.user_id == user.id)
    ids = list(db.scalars(mine))
    if not ids:
        return []
    names = {t.id: t.name for t in db.scalars(select(WatchdogTask).where(WatchdogTask.id.in_(ids)))}
    rows = db.scalars(select(WatchdogRun).where(WatchdogRun.task_id.in_(ids))
                      .order_by(WatchdogRun.id.desc()).limit(max(1, min(limit, 200)))).all()
    return [{**watchdog.run_dict(r, with_details=False), "name": names.get(r.task_id)} for r in rows]


# --------------------------------------------------------------------------- the watchdogs

@router.get("", summary="Watchdogs of this account (every one, for an administrator)")
def list_watchdogs(db: Session = Depends(get_db), service: Service = Depends(get_service),
                   user: CurrentUser = Depends(require_user)) -> list[dict[str, Any]]:
    _available(db, user)
    query = select(WatchdogTask).order_by(WatchdogTask.name)
    if not user.is_admin:
        query = query.where(WatchdogTask.user_id == user.id)
    return [watchdog.task_dict(service, db, t) for t in db.scalars(query)]


@router.post("", summary="Create a watchdog", status_code=201)
def create_watchdog(body: WatchdogIn, request: Request, db: Session = Depends(get_db),
                    service: Service = Depends(get_service),
                    user: CurrentUser = Depends(require_user)) -> dict[str, Any]:
    _available(db, user)
    total = len(list(db.scalars(select(WatchdogTask.id).where(WatchdogTask.user_id == user.id))))
    if total >= MAX_TASKS:
        raise HTTPException(409, f"at most {MAX_TASKS} watchdogs per account")
    owner = _owner(db, user, body.user_id)
    data = body.model_dump(exclude={"user_id"})
    _check(db, service, user, owner, data)
    task = WatchdogTask(user_id=owner.id, created_by=user.username, updated_by=user.username,
                        **{k: v for k, v in data.items()})
    db.add(task)
    db.commit()
    watchdog.schedule_one(service, task)
    audit(db, "watchdog.create", user=user, target=f"watchdog:{task.id}", request=request,
          detail={"name": task.name, "scope": "*" if task.all_referentials else task.referential_ids,
                  "trigger": task.trigger_type, "schedule": task.schedule, "runs_as": owner.username})
    return watchdog.task_dict(service, db, task)


@router.get("/{task_id}", summary="One watchdog")
def get_watchdog(task_id: int, db: Session = Depends(get_db), service: Service = Depends(get_service),
                 user: CurrentUser = Depends(require_user)) -> dict[str, Any]:
    _available(db, user)
    return watchdog.task_dict(service, db, _task(db, user, task_id))


@router.patch("/{task_id}", summary="Change a watchdog")
def update_watchdog(task_id: int, body: WatchdogPatch, request: Request, db: Session = Depends(get_db),
                    service: Service = Depends(get_service),
                    user: CurrentUser = Depends(require_user)) -> dict[str, Any]:
    _available(db, user)
    task = _task(db, user, task_id)
    changes = body.model_dump(exclude_unset=True, exclude={"user_id"})
    owner = _owner(db, user, body.user_id) if body.user_id is not None else task.user
    merged = {
        "referential_ids": changes.get("referential_ids", task.referential_ids),
        "all_referentials": changes.get("all_referentials", task.all_referentials),
        "trigger_type": changes.get("trigger_type", task.trigger_type),
        "schedule": changes.get("schedule", task.schedule) if "schedule" in changes or task.schedule else None,
        "notify_channels": changes.get("notify_channels", task.notify_channels),
        "min_severity": changes.get("min_severity", task.min_severity),
    }
    _check(db, service, user, owner, merged)
    for field, value in changes.items():
        setattr(task, field, value)
    task.user_id, task.updated_by = owner.id, user.username
    db.commit()
    watchdog.schedule_one(service, task)
    audit(db, "watchdog.update", user=user, target=f"watchdog:{task.id}", request=request,
          detail={"changed": sorted(changes), "enabled": task.enabled})
    return watchdog.task_dict(service, db, task)


@router.delete("/{task_id}", summary="Delete a watchdog and its history")
def delete_watchdog(task_id: int, request: Request, db: Session = Depends(get_db),
                    service: Service = Depends(get_service),
                    user: CurrentUser = Depends(require_user)) -> dict[str, Any]:
    _available(db, user)
    task = _task(db, user, task_id)
    name = task.name
    watchdog.unschedule(service, task.id)
    db.delete(task)
    db.commit()
    audit(db, "watchdog.delete", user=user, target=f"watchdog:{task_id}", request=request, detail={"name": name})
    return {"deleted": task_id}


@router.post("/{task_id}/run", summary="Run an investigation now", status_code=202)
def run_watchdog(task_id: int, request: Request, db: Session = Depends(get_db),
                 service: Service = Depends(get_service),
                 user: CurrentUser = Depends(require_user)) -> dict[str, Any]:
    """Starts it in the background and answers at once: an investigation calls the model several times and
    takes as long as it takes. Follow it with the run history (the watchdog reports `running`)."""
    _available(db, user)
    task = _task(db, user, task_id)
    started = watchdog.submit(service, task.id, "manual", user.username)
    if not started:
        raise HTTPException(409, "this watchdog is already running")
    audit(db, "watchdog.start", user=user, target=f"watchdog:{task.id}", request=request, detail={"trigger": "manual"})
    return {"started": True, "id": task.id}


@router.get("/{task_id}/runs", summary="Investigations of a watchdog, newest first")
def watchdog_runs(task_id: int, limit: int = 30, db: Session = Depends(get_db),
                  user: CurrentUser = Depends(require_user)) -> list[dict[str, Any]]:
    _available(db, user)
    task = _task(db, user, task_id)
    rows = db.scalars(select(WatchdogRun).where(WatchdogRun.task_id == task.id)
                      .order_by(WatchdogRun.id.desc()).limit(max(1, min(limit, 200)))).all()
    return [watchdog.run_dict(r) for r in rows]
