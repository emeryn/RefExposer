"""The alert feed: what needs a look, newest first.

One list gathering what the application already knows, instead of a table of its own: the findings of the
[watchdogs](watchdog.py) and the drifts of the referentials (an update that failed, data that went stale,
quality rules that started failing, a volume that moved a lot in one version). Nothing is written when an
event happens — the feed is built from the state of the referentials and the recorded watchdog runs, so it can
never disagree with what the rest of the interface shows.

Every entry is filtered by what the account may see: a referential it cannot read is not there, and a watchdog
belongs to its owner (administrators see every one). On top of that, an administrator chooses what counts as an
alert at all (`AlertsSettings`): the kinds reported, the severity below which nothing is, and what makes a volume
drift. What is left out there is out of the feed for everyone, so the bell in the header and the page always agree.

**Read** is kept per account (`AlertRead`), not per alert: an alert is a computed line, and acknowledging one only
means this person has seen it. It comes back unread when the thing behind it happens again, because the marker
remembers the date of what was acknowledged.

**Deleting** one goes further: the same marker carries a `dismissed` flag, and a dismissed alert is out of the
feed and out of its counts for that account — nobody else's — until the thing behind it happens again. Nothing
about the referential or the watchdog is touched, and the deleted ones stay listable (`dismissed=True`) so one
can be put back.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from . import appsettings
from .auth import CurrentUser
from .models import SEVERITIES, AlertRead, WatchdogRun, WatchdogTask, now as utcnow
from .storage import RefPaths, read_runs

KINDS = ("watchdog", "failure", "quality", "stale", "drift")
# A version that changes the volume by more than this, on a referential that had enough rows for the ratio to
# mean something, is reported as a drift: the source may have changed shape, or lost a part of its data.
# Both are the defaults of `AlertsSettings`, which is what the feed actually reads.
DRIFT_RATIO = 0.2
DRIFT_MIN_ROWS = 100
MAX_ALERTS = 200
# Read markers of an alert nobody has seen for this long are dropped: the alert itself is long gone
KEEP_READ_DAYS = 180


@dataclass
class Alert:
    """One line of the feed. `id` is enough to fetch its detail, and says where it comes from."""

    id: str
    kind: str
    severity: str
    at: str | None
    title: str
    summary: str
    link: str
    referential_id: str | None = None
    referential_name: str | None = None
    watchdog_id: int | None = None
    watchdog_name: str | None = None
    # A watchdog finding carries the report the agent wrote; the others carry logs or figures
    has_report: bool = False
    # This account has marked it read (and the thing behind it has not happened again since)
    read: bool = False
    # This account has deleted it from its feed: only listed when the deleted ones are asked for
    dismissed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _rank(severity: str) -> int:
    return SEVERITIES.index(severity) if severity in SEVERITIES else 0


# --------------------------------------------------------------------------- referentials

def _referential_alerts(service: Any, user: CurrentUser, cfg: Any) -> list[Alert]:
    out: list[Alert] = []
    for ref in service.refs.values():
        if not user.can_read(ref.id):
            continue
        meta = service.metas.get(ref.id, {})
        name = ref.name
        status = meta.get("status")
        if status in ("error", "rejected", "corrupted"):
            severity = {"error": "HIGH", "rejected": "MEDIUM", "corrupted": "MEDIUM"}[status]
            title = {"error": "Update failed", "rejected": "New version rejected",
                     "corrupted": "Corrupted source, current version kept"}[status]
            out.append(Alert(
                id=f"ref:{ref.id}:failure", kind="failure", severity=severity,
                at=meta.get("last_attempt_at"), title=title,
                summary=str(meta.get("last_error") or "")[:500],
                link=f"/r/{ref.id}/history", referential_id=ref.id, referential_name=name))
        elif service.health(ref, meta) == "stale":
            out.append(Alert(
                id=f"ref:{ref.id}:stale", kind="stale", severity="MEDIUM",
                at=meta.get("data_updated_at"), title="Data is stale",
                summary="No new version within the expected delay: the source may have stopped publishing, or "
                        "the schedule is no longer right.",
                link=f"/r/{ref.id}/history", referential_id=ref.id, referential_name=name))

        quality = meta.get("quality") or {}
        if quality.get("status") in ("warning", "failed"):
            failed = ", ".join(quality.get("failed") or [])
            out.append(Alert(
                id=f"ref:{ref.id}:quality", kind="quality",
                severity="HIGH" if quality["status"] == "failed" else "MEDIUM",
                at=quality.get("checked_at") or meta.get("data_updated_at"),
                title=f"Quality rules failing (score {quality.get('score')}/100)",
                summary=f"Failing rules: {failed}" if failed else "The published version does not pass its quality rules.",
                link=f"/r/{ref.id}/quality", referential_id=ref.id, referential_name=name))

        drift = _drift(meta, cfg)
        if drift is not None:
            delta, ratio = drift
            way = "lost" if delta < 0 else "gained"
            out.append(Alert(
                id=f"ref:{ref.id}:drift", kind="drift", severity="MEDIUM" if delta < 0 else "LOW",
                at=meta.get("data_updated_at"),
                title=f"Volume drift: {way} {abs(ratio) * 100:.0f}% of the rows",
                summary=f"Version {meta.get('version')} holds {meta.get('row_count'):,} rows against "
                        f"{meta.get('previous_row_count'):,} before ({delta:+,}).".replace(",", " "),
                link=f"/r/{ref.id}/changes", referential_id=ref.id, referential_name=name))
    return out


def _drift(meta: dict[str, Any], cfg: Any) -> tuple[int, float] | None:
    """(delta, ratio) when the last version moved the volume more than the settings tolerate, else None."""
    before, now = meta.get("previous_row_count"), meta.get("row_count")
    if not isinstance(before, int) or not isinstance(now, int) or before < cfg.drift_min_rows:
        return None
    delta = now - before
    ratio = delta / before
    return (delta, ratio) if abs(ratio) >= cfg.drift_ratio else None


# --------------------------------------------------------------------------- watchdogs

def _watchdog_alerts(db: Session, user: CurrentUser, limit: int) -> list[Alert]:
    from . import watchdog

    if not watchdog.may_use(user):
        return []
    tasks = select(WatchdogTask)
    if not user.is_admin:
        tasks = tasks.where(WatchdogTask.user_id == user.id)
    names = {t.id: t.name for t in db.scalars(tasks)}
    if not names:
        return []
    runs = db.scalars(
        select(WatchdogRun).where(WatchdogRun.task_id.in_(list(names)),
                                  WatchdogRun.status.in_(("finding", "error")))
        .order_by(WatchdogRun.id.desc()).limit(limit)).all()
    out = []
    for run in runs:
        found = run.status == "finding"
        out.append(Alert(
            id=f"watchdog:{run.id}", kind="watchdog",
            severity=(run.severity or "LOW") if found else "MEDIUM",
            at=run.started_at.isoformat() if run.started_at else None,
            title=f"{names[run.task_id]}: {'finding' if found else 'investigation failed'}",
            summary=(run.summary or "")[:500] if found else (run.error or "The watchdog could not finish.")[:500],
            link="/watchdogs", watchdog_id=run.task_id, watchdog_name=names[run.task_id],
            has_report=found and bool(run.details_markdown)))
    return out


# --------------------------------------------------------------------------- the feed

def shown_kinds(db: Session) -> tuple[str, ...]:
    """The kinds the administrator left on, in the order of KINDS."""
    cfg = appsettings.load(db, "alerts")
    return tuple(k for k in KINDS if cfg.shows(k))  # type: ignore[attr-defined]


def read_markers(db: Session, user: CurrentUser) -> dict[str, tuple[str, bool]]:
    """{alert id: (date it carried when this account marked it, deleted from the feed)}."""
    return {r.alert_id: (r.at or "", bool(r.dismissed))
            for r in db.scalars(select(AlertRead).where(AlertRead.user_id == user.id))}


def feed(db: Session, service: Any, user: CurrentUser, limit: int = 50,
         kinds: list[str] | None = None, dismissed: bool | None = False) -> list[dict[str, Any]]:
    """The feed of this account. `dismissed`: False the alerts it has not deleted (the feed proper), True only
    the deleted ones, None both — what marking and deleting work against."""
    cfg = appsettings.load(db, "alerts")
    wanted = set(kinds or KINDS) & set(shown_kinds(db))
    alerts = [a for a in _referential_alerts(service, user, cfg) if a.kind in wanted]
    if "watchdog" in wanted:
        alerts += _watchdog_alerts(db, user, min(limit, MAX_ALERTS))
    floor = _rank(cfg.min_severity)  # type: ignore[attr-defined]
    alerts = [a for a in alerts if _rank(a.severity) >= floor]
    marked = read_markers(db, user)
    for alert in alerts:
        # Marked, and nothing newer happened since: the date of the alert is the date of what was acknowledged
        at, gone = marked.get(alert.id, ("", False))
        alert.read = alert.id in marked and at >= (alert.at or "")
        alert.dismissed = alert.read and gone
    if dismissed is not None:
        alerts = [a for a in alerts if a.dismissed is dismissed]
    # Newest first; an entry without a date (never updated) goes last, and severity breaks a tie
    alerts.sort(key=lambda a: (a.at or "", _rank(a.severity)), reverse=True)
    return [a.to_dict() for a in alerts[:min(limit, MAX_ALERTS)]]


def counts(alerts: list[dict[str, Any]], kinds: tuple[str, ...] = KINDS) -> dict[str, int]:
    out = {"total": len(alerts), "unread": sum(1 for a in alerts if not a.get("read"))}
    for kind in kinds:
        out[kind] = sum(1 for a in alerts if a["kind"] == kind)
    for severity in SEVERITIES:
        out[severity.lower()] = sum(1 for a in alerts if a["severity"] == severity)
    return out


# --------------------------------------------------------------------------- one alert in full

class UnknownAlert(LookupError):
    """No such alert, or it is not one this account may see."""


def _targets(db: Session, service: Any, user: CurrentUser, ids: list[str] | None,
             dismissed: bool | None) -> tuple[dict[str, str], list[str], dict[str, AlertRead]]:
    """What a marking acts on: the dates of the alerts in scope, the identifiers to touch, and the markers
    already stored for them. Only alerts the account actually sees are in scope, so an identifier is never a
    way to learn that something exists."""
    current = {a["id"]: (a["at"] or "")
               for a in feed(db, service, user, limit=MAX_ALERTS, dismissed=dismissed)}
    wanted = list(current) if ids is None else [i for i in dict.fromkeys(ids) if i in current]
    existing = {r.alert_id: r for r in db.scalars(
        select(AlertRead).where(AlertRead.user_id == user.id, AlertRead.alert_id.in_(wanted or [""])))}
    return current, wanted, existing


def _purge(db: Session, user: CurrentUser) -> None:
    """A marker whose alert went away years ago has nothing left to hide. A deleted one is kept: its alert may
    well still be there, and dropping the marker would put it back in the feed."""
    import datetime as dt

    db.execute(delete(AlertRead)
               .where(AlertRead.user_id == user.id, AlertRead.dismissed.is_(False),
                      AlertRead.read_at < utcnow() - dt.timedelta(days=KEEP_READ_DAYS))
               # Deleted in the database, not evaluated against the rows of the session: those come back from
               # SQLite without a timezone, and comparing them in Python raises
               .execution_options(synchronize_session=False))


def mark(db: Session, service: Any, user: CurrentUser, ids: list[str] | None, read: bool) -> int:
    """Mark alerts of this account read (or unread again). `ids` None: every alert of its feed.

    Returns how many lines changed. Marking one unread also puts it back in the feed if it had been deleted.
    """
    current, wanted, existing = _targets(db, service, user, ids, dismissed=None if ids else False)
    changed = 0
    for alert_id in wanted:
        row = existing.get(alert_id)
        if not read:
            if row is not None:
                db.delete(row)
                changed += 1
            continue
        if row is None:
            db.add(AlertRead(user_id=user.id, alert_id=alert_id, at=current[alert_id][:40]))
            changed += 1
        elif (row.at or "") != current[alert_id]:
            row.at, row.read_at = current[alert_id][:40], utcnow()
            changed += 1
    _purge(db, user)
    db.commit()
    return changed


def dismiss(db: Session, service: Any, user: CurrentUser, ids: list[str] | None, dismissed: bool = True) -> int:
    """Delete alerts from the feed of this account, or put deleted ones back. `ids` None: every alert of the
    list concerned — the feed when deleting, the deleted ones when restoring.

    A deleted alert is read too: it is gone from the list and from its counts, and it comes back on its own
    when the thing behind it happens again. Nothing is changed for anybody else. Returns how many changed.
    """
    current, wanted, existing = _targets(db, service, user, ids, dismissed=not dismissed)
    changed = 0
    for alert_id in wanted:
        row = existing.get(alert_id)
        if row is None:
            if not dismissed:  # nothing stored, so nothing was deleted
                continue
            db.add(AlertRead(user_id=user.id, alert_id=alert_id, at=current[alert_id][:40], dismissed=True))
            changed += 1
            continue
        if bool(row.dismissed) == dismissed and (row.at or "") == current[alert_id]:
            continue
        row.dismissed, row.at, row.read_at = dismissed, current[alert_id][:40], utcnow()
        changed += 1
    _purge(db, user)
    db.commit()
    return changed


def detail(db: Session, service: Any, user: CurrentUser, alert_id: str) -> dict[str, Any]:
    """The alert with what it takes to act on it: the report of a watchdog, the logs of a failed update, the
    failing quality rules, the figures of a drift."""
    # Deleted ones included: their detail stays readable from the list of what this account removed
    found = next((a for a in feed(db, service, user, limit=MAX_ALERTS, dismissed=None) if a["id"] == alert_id), None)
    if found is None:
        raise UnknownAlert(alert_id)
    out: dict[str, Any] = {"alert": found}
    if found["kind"] == "watchdog":
        from . import watchdog

        run = db.get(WatchdogRun, int(alert_id.split(":", 1)[1]))
        if run is None:
            raise UnknownAlert(alert_id)
        out["run"] = watchdog.run_dict(run)
        task = db.get(WatchdogTask, run.task_id)
        out["watchdog"] = {"id": task.id, "name": task.name, "description": task.description,
                           "referential_ids": task.referential_ids or [],
                           "trigger_type": task.trigger_type, "schedule": task.schedule} if task else None
        return out

    ref_id = found["referential_id"]
    ref, meta = service.refs.get(ref_id), service.metas.get(ref_id, {})
    out["referential"] = service.summary(ref, user.access(ref_id), user) if ref else None
    if found["kind"] == "failure":
        runs = read_runs(RefPaths(service.settings.data_dir, ref_id), limit=1)
        out["run"] = runs[0] if runs else None
    elif found["kind"] == "quality":
        out["quality"] = meta.get("quality")
    else:
        out["figures"] = {k: meta.get(k) for k in ("version", "row_count", "previous_row_count", "changes",
                                                   "data_updated_at", "last_checked_at", "parquet_size")}
    return out
