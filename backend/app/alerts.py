"""The alert feed: what needs a look, newest first.

One list gathering what the application already knows, instead of a table of its own: the findings of the
[watchdogs](watchdog.py) and the drifts of the referentials (an update that failed, data that went stale,
quality rules that started failing, a volume that moved a lot in one version). Nothing is written when an
event happens — the feed is built from the state of the referentials and the recorded watchdog runs, so it can
never disagree with what the rest of the interface shows.

Every entry is filtered by what the account may see: a referential it cannot read is not there, and a watchdog
belongs to its owner (administrators see every one).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import CurrentUser
from .models import SEVERITIES, WatchdogRun, WatchdogTask
from .storage import RefPaths, read_runs

KINDS = ("watchdog", "failure", "quality", "stale", "drift")
# A version that changes the volume by more than this, on a referential that had enough rows for the ratio to
# mean something, is reported as a drift: the source may have changed shape, or lost a part of its data
DRIFT_RATIO = 0.2
DRIFT_MIN_ROWS = 100
MAX_ALERTS = 200


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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _rank(severity: str) -> int:
    return SEVERITIES.index(severity) if severity in SEVERITIES else 0


# --------------------------------------------------------------------------- referentials

def _referential_alerts(service: Any, user: CurrentUser) -> list[Alert]:
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

        drift = _drift(meta)
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


def _drift(meta: dict[str, Any]) -> tuple[int, float] | None:
    """(delta, ratio) when the last version moved the volume a lot, else None."""
    before, now = meta.get("previous_row_count"), meta.get("row_count")
    if not isinstance(before, int) or not isinstance(now, int) or before < DRIFT_MIN_ROWS:
        return None
    delta = now - before
    ratio = delta / before
    return (delta, ratio) if abs(ratio) >= DRIFT_RATIO else None


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

def feed(db: Session, service: Any, user: CurrentUser, limit: int = 50,
         kinds: list[str] | None = None) -> list[dict[str, Any]]:
    wanted = set(kinds or KINDS)
    alerts = [a for a in _referential_alerts(service, user) if a.kind in wanted]
    if "watchdog" in wanted:
        alerts += _watchdog_alerts(db, user, min(limit, MAX_ALERTS))
    # Newest first; an entry without a date (never updated) goes last, and severity breaks a tie
    alerts.sort(key=lambda a: (a.at or "", _rank(a.severity)), reverse=True)
    return [a.to_dict() for a in alerts[:min(limit, MAX_ALERTS)]]


def counts(alerts: list[dict[str, Any]]) -> dict[str, int]:
    out = {"total": len(alerts)}
    for kind in KINDS:
        out[kind] = sum(1 for a in alerts if a["kind"] == kind)
    for severity in SEVERITIES:
        out[severity.lower()] = sum(1 for a in alerts if a["severity"] == severity)
    return out


# --------------------------------------------------------------------------- one alert in full

class UnknownAlert(LookupError):
    """No such alert, or it is not one this account may see."""


def detail(db: Session, service: Any, user: CurrentUser, alert_id: str) -> dict[str, Any]:
    """The alert with what it takes to act on it: the report of a watchdog, the logs of a failed update, the
    failing quality rules, the figures of a drift."""
    found = next((a for a in feed(db, service, user, limit=MAX_ALERTS) if a["id"] == alert_id), None)
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
