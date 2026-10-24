"""Scheduled watchdogs: the assistant investigating on its own, on a narrow scope.

A watchdog is a question asked again and again — on a crontab, or right after one of its referentials
publishes a new version — over a set of referentials chosen explicitly. It runs the same agent as the chat,
with three differences that matter:

- **Scope.** The referentials are listed in the task, and nothing else is reachable: the signed scope travels
  with every tool call and `CurrentUser.level()` returns 0 for anything outside it, so the model cannot drift
  onto another source even if it asks.
- **Rights.** It runs with the rights of the account it belongs to, through a session of its own opened for
  the run and closed after it. It never writes: the tools that change something are off for a watchdog,
  whatever the assistant settings allow in the chat.
- **Answer.** The model must return a structured verdict (`finding_detected`, `severity`, `summary`,
  `details_markdown`, `affected_items_count`). Recipients are told **only** when something was found, at or
  above the severity the task asks for — an investigation that finds nothing is recorded and stays quiet.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import appsettings
from .auth import CurrentUser, audit, load_grants
from .models import SEVERITIES, AuthSession, User, WatchdogRun, WatchdogTask, now
from .security import new_session_token
from .storage import utcnow

log = logging.getLogger(__name__)

JOB_PREFIX = "watchdog:"
PROMPT = Path(__file__).parent / "llm" / "prompts" / "watchdog.md"
MAX_DETAILS = 100_000
# A run holds a session open for at most this long, then the session expires on its own
SESSION_MINUTES = 60
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="watchdog")
_running: set[int] = set()
_lock = threading.Lock()


# --------------------------------------------------------------------------- templates offered in the interface

TEMPLATES: dict[str, dict[str, str]] = {
    "shadow_it": {
        "label": "Shadow IT hunt (CMDB vs infrastructure / network)",
        "description": "Assets alive on the network or the hypervisor that the CMDB does not know, or that "
                       "carry no security agent.",
        "prompt": "Compare the referentials in scope. Identify every machine or IP address active on the "
                  "infrastructure or on the network that is not listed in the official CMDB referential, or "
                  "that has no active EDR agent. List the gaps you find with their details.",
    },
    "easm_drift": {
        "label": "External exposure drift (EASM)",
        "description": "New exposed assets, unexpected open ports, unknown certificates or sub-domains.",
        "prompt": "Analyse the external scan and inventory referentials. Use the difference with the last "
                  "snapshot when it is available to identify any new sub-domain, newly exposed IP address or "
                  "sensitive service that was not there before, or that is not validated in the official "
                  "inventory.",
    },
    "iam_orphans": {
        "label": "IAM audit & orphan accounts",
        "description": "Leavers and movers crossed with the accounts still active in the directories and the "
                       "applications.",
        "prompt": "Correlate the users / HR referential with the active account referentials (AD, VPN, "
                  "applications). Identify every account still active that belongs to an inactive person, "
                  "someone who has left, or someone who should no longer hold that access.",
    },
    "custom": {
        "label": "Free (write your own question)",
        "description": "Your own correlation, over the referentials you choose.",
        "prompt": "",
    },
}


def default_prompt() -> str:
    return PROMPT.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- scheduling

def job_id(task_id: int) -> str:
    return f"{JOB_PREFIX}{task_id}"


def enabled(cfg: Any) -> bool:
    """Whether watchdogs run at all: they are off by default, and need the agent mode of the assistant."""
    return bool(cfg.enabled and cfg.agent_enabled and cfg.watchdogs_enabled)


def may_use(user: CurrentUser) -> bool:
    """Reserved to administrators and advanced users: a watchdog reads on a schedule, with nobody watching."""
    return user.is_admin or user.role == "advanced"


def schedule_all(service: Any) -> None:
    """(Re)install the crontab jobs of every enabled watchdog, or remove them all when the feature is off.

    Called at startup and whenever the assistant settings change, so turning watchdogs off stops the
    schedules at once instead of at the next restart.
    """
    for job in service.scheduler.get_jobs():
        if job.id.startswith(JOB_PREFIX):
            job.remove()
    from .db import session_factory

    try:
        with session_factory()() as db:
            if not enabled(appsettings.load(db, "assistant")):
                return
            tasks = list(db.scalars(select(WatchdogTask).where(WatchdogTask.enabled.is_(True))))
    except Exception:  # noqa: BLE001 (the database is not ready yet)
        log.exception("Cannot load the watchdogs")
        return
    for task in tasks:
        schedule_one(service, task)


def schedule_one(service: Any, task: WatchdogTask) -> None:
    unschedule(service, task.id)
    if not (task.enabled and task.trigger_type == "cron" and task.schedule):
        return
    try:
        trigger = CronTrigger.from_crontab(task.schedule, timezone=service.tz)
    except ValueError as e:
        log.error("Watchdog '%s': invalid schedule %r (%s)", task.name, task.schedule, e)
        return
    service.scheduler.add_job(submit, trigger, args=[service, task.id, "cron"], id=job_id(task.id),
                              name=f"Watchdog: {task.name}", coalesce=True, max_instances=1,
                              misfire_grace_time=3600, replace_existing=True)


def unschedule(service: Any, task_id: int) -> None:
    job = service.scheduler.get_job(job_id(task_id))
    if job is not None:
        job.remove()


def next_run(service: Any, task_id: int):
    job = service.scheduler.get_job(job_id(task_id))
    return getattr(job, "next_run_time", None) if job else None


def on_sync(service: Any, ref_id: str) -> None:
    """A referential just published a new version: start the watchdogs waiting for it."""
    from .db import session_factory

    try:
        with session_factory()() as db:
            if not enabled(appsettings.load(db, "assistant")):
                return
            tasks = [t.id for t in db.scalars(
                select(WatchdogTask).where(WatchdogTask.enabled.is_(True), WatchdogTask.trigger_type == "on_sync"))
                if ref_id in (t.referential_ids or [])]
    except Exception:  # noqa: BLE001
        log.exception("Cannot read the watchdogs after the update of %s", ref_id)
        return
    for task_id in tasks:
        submit(service, task_id, f"on_sync:{ref_id}")


def submit(service: Any, task_id: int, trigger: str, username: str | None = None) -> bool:
    """Start an investigation in the background, unless that watchdog is already running."""
    with _lock:
        if task_id in _running:
            log.info("Watchdog %s is already running: this trigger (%s) is skipped", task_id, trigger)
            return False
        _running.add(task_id)

    def job() -> None:
        try:
            execute(service, task_id, trigger, username)
        except Exception:  # noqa: BLE001
            log.exception("Watchdog %s failed", task_id)
        finally:
            with _lock:
                _running.discard(task_id)

    _pool.submit(job)
    return True


def is_running(task_id: int) -> bool:
    with _lock:
        return task_id in _running


# --------------------------------------------------------------------------- the verdict

def parse_verdict(text: str) -> tuple[dict[str, Any], str | None]:
    """Read the JSON object the watchdog prompt asks for. Returns (verdict, error).

    A model that answered in prose instead is **not** read as "nothing found": the run is recorded as an
    error, with its answer kept, so that silence is never mistaken for an all-clear.
    """
    raw = (text or "").strip()
    data = _first_object(raw)
    if data is None:
        return ({"finding_detected": False, "severity": "LOW", "summary": raw.splitlines()[0][:2000] if raw else "",
                 "details_markdown": raw[:MAX_DETAILS], "affected_items_count": None},
                "the watchdog did not answer with the expected JSON verdict")
    severity = str(data.get("severity") or "LOW").upper()
    count = data.get("affected_items_count")
    return ({
        "finding_detected": bool(data.get("finding_detected")),
        "severity": severity if severity in SEVERITIES else "LOW",
        "summary": str(data.get("summary") or "")[:2000],
        "details_markdown": str(data.get("details_markdown") or "")[:MAX_DETAILS],
        "affected_items_count": int(count) if isinstance(count, (int, float)) and not isinstance(count, bool) else None,
    }, None)


def _first_object(text: str) -> dict[str, Any] | None:
    """The first complete JSON object of the text, braces counted so a nested one does not cut it short."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE | re.MULTILINE)
    start = text.find("{")
    while start != -1:
        depth, in_string, escaped = 0, False, False
        for i in range(start, len(text)):
            c = text[i]
            if in_string:
                if escaped:
                    escaped = False
                elif c == "\\":
                    escaped = True
                elif c == '"':
                    in_string = False
                continue
            if c == '"':
                in_string = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        value = json.loads(text[start:i + 1])
                    except ValueError:
                        break
                    return value if isinstance(value, dict) else None
        start = text.find("{", start + 1)
    return None


def severity_reaches(severity: str, minimum: str) -> bool:
    order = {s: i for i, s in enumerate(SEVERITIES)}
    return order.get(severity, 0) >= order.get(minimum, 0)


# --------------------------------------------------------------------------- one investigation

class WatchdogError(RuntimeError):
    """The investigation cannot be run (assistant off, scope empty, account unusable)."""


def _session_for(db: Session, user: User) -> tuple[str, int]:
    """Open a short-lived session for the owner of the watchdog: the tool calls authenticate with it, so they
    go through exactly the same checks as a click in the interface."""
    import datetime as dt

    token, digest = new_session_token()
    sess = AuthSession(token_hash=digest, user_id=user.id, expires_at=now() + dt.timedelta(minutes=SESSION_MINUTES),
                       last_seen_at=now(), ip="watchdog", user_agent="RefExposer watchdog")
    db.add(sess)
    db.commit()
    return token, sess.id


def _close_session(db: Session, session_id: int) -> None:
    sess = db.get(AuthSession, session_id)
    if sess is not None:
        db.delete(sess)
        db.commit()


def current_user(db: Session, service: Any, user: User, scope: frozenset[str], denied: frozenset[str]) -> CurrentUser:
    """The account as the agent sees it: its own rights, narrowed to the scope of the watchdog."""
    categories = {r.id: r.category for r in service.refs.values()}
    return CurrentUser(
        id=user.id, username=user.username, display_name=user.display_name, email=user.email, role=user.role,
        must_change_password=False, groups=sorted(g.name for g in user.groups),
        grants={} if user.role == "admin" else load_grants(db, user, categories),
        denied=denied, allowed=scope, via="agent", status=user.status, auth_source=user.auth_source,
    )


def question(task: WatchdogTask) -> str:
    scope = ", ".join(task.referential_ids or [])
    prompt = (task.prompt or "").strip() or "Report anything unusual in these referentials."
    return f"Referentials in scope: {scope}.\n\n{prompt}"


def execute(service: Any, task_id: int, trigger: str = "manual", username: str | None = None) -> dict[str, Any]:
    """Run one investigation and record it. Returns the run as the API returns it."""
    from .db import session_factory
    from .llm import agent as agentlib
    from .llm.provider import LLMError, build

    with session_factory()() as db:
        task = db.get(WatchdogTask, task_id)
        if task is None:
            raise WatchdogError(f"unknown watchdog: {task_id}")
        owner = task.user
        cfg = appsettings.load(db, "assistant")
        scope = frozenset(task.referential_ids or [])
        run = WatchdogRun(task_id=task.id, trigger=trigger[:80], status="error", started_at=utcnow())
        db.add(run)
        db.commit()
        notifications: list[dict[str, Any]] = []
        session_id = None
        try:
            if not scope:
                raise WatchdogError("this watchdog has no referential in its scope")
            if not enabled(cfg):
                raise WatchdogError("scheduled watchdogs are disabled (Settings › Assistant)")
            if not cfg.configured:
                raise WatchdogError("the assistant has no inference server or no model yet")
            if not may_use(current_user(db, service, owner, frozenset(), frozenset())):
                raise WatchdogError(f"the account '{owner.username}' may not run a watchdog "
                                    "(administrators and advanced users only)")
            if not (owner.is_active and owner.status == "active") or owner.auth_source == "service":
                raise WatchdogError(f"the account '{owner.username}' cannot run an investigation")
            denied = frozenset() if cfg.allow_confidential else agentlib.confidential_ids(service)
            current = current_user(db, service, owner, scope, denied)
            if not agentlib.allowed(cfg, current):
                raise WatchdogError(f"the account '{owner.username}' is not allowed to use the assistant")
            unreachable = sorted(r for r in scope if not current.can_read(r))
            if unreachable:
                raise WatchdogError("the account cannot read: " + ", ".join(unreachable))
            provider = build(cfg, service.settings, appsettings.load(db, "proxy"))
            token, session_id = _session_for(db, owner)
            # A watchdog only looks: the tools that change something are off whatever the settings allow
            read_only = cfg.model_copy(update={"allow_writes": False})
            caller = agentlib.Caller(service.app, service, {"cookie": f"refex_session={token}"}, None, scope)
            worker = agentlib.Agent(caller, read_only, provider, current, agent_mode=True,
                                    system_prompt=default_prompt())
            final: dict[str, Any] = {}
            for event in worker.run([{"role": "user", "content": question(task)}]):
                if event.get("type") == "done":
                    final = event
            run.steps = final.get("steps") or None
            if final.get("error"):
                raise WatchdogError(str(final["error"]))
            verdict, bad = parse_verdict(final.get("content") or "")
            run.finding_detected = verdict["finding_detected"]
            run.severity = verdict["severity"]
            run.summary = verdict["summary"]
            run.details_markdown = verdict["details_markdown"]
            run.affected_items_count = verdict["affected_items_count"]
            if bad:
                run.status, run.error = "error", bad
            else:
                run.status = "finding" if run.finding_detected else "ok"
                if run.finding_detected and severity_reaches(run.severity, task.min_severity):
                    notifications = notify(service, db, task, run)
        except (WatchdogError, LLMError) as e:
            run.status, run.error = "error", str(e)[:2000]
            log.warning("Watchdog '%s': %s", task.name, e)
        except Exception as e:  # noqa: BLE001
            run.status, run.error = "error", f"{type(e).__name__}: {e}"[:2000]
            log.exception("Watchdog '%s' failed", task.name)
        finally:
            if session_id is not None:
                _close_session(db, session_id)
        run.finished_at = utcnow()
        run.notifications = notifications or None
        db.commit()
        audit(db, "watchdog.run", username=username or "system", target=f"watchdog:{task.id}",
              success=run.status != "error",
              detail={"trigger": trigger, "status": run.status, "severity": run.severity,
                      "items": run.affected_items_count, "notified": len(notifications)})
        _prune_runs(db, task.id)
        return run_dict(run)


KEEP_RUNS = 100


def _prune_runs(db: Session, task_id: int) -> None:
    old = list(db.scalars(select(WatchdogRun).where(WatchdogRun.task_id == task_id)
                          .order_by(WatchdogRun.id.desc()).limit(100_000).offset(KEEP_RUNS)))
    for run in old:
        db.delete(run)
    if old:
        db.commit()


# --------------------------------------------------------------------------- telling the recipients

def notify(service: Any, db: Session, task: WatchdogTask, run: WatchdogRun) -> list[dict[str, Any]]:
    """Tell the recipients of a watchdog that it found something. Never called otherwise."""
    from . import notify as notifier
    from .network import client

    out: list[dict[str, Any]] = []
    emails = list(task.notify_emails or [])
    channel_ids = set(task.notify_channels or [])
    channels = [c for c in appsettings.load(db, "notifications").channels if c.id in channel_ids]  # type: ignore[attr-defined]
    smtp = appsettings.load(db, "smtp")
    ca = appsettings.load(db, "proxy").ca_bundle  # type: ignore[attr-defined]
    data = payload(service, task, run)
    subject = f"[RefExposer] {run.severity} — {task.name}: {run.summary or 'finding detected'}"[:200]
    body = mail_body(service, task, run)

    for channel in channels:
        if channel.type == "email":
            emails += [r for r in channel.recipients if r not in emails]
            continue
        try:
            with client(service.settings, timeout=15) as c:
                status = notifier.post_webhook(channel, data, c)
            out.append({"target": f"webhook:{channel.name}", "ok": True, "detail": f"HTTP {status}"})
        except Exception as e:  # noqa: BLE001
            out.append({"target": f"webhook:{channel.name}", "ok": False, "detail": f"{type(e).__name__}: {e}"[:300]})
    if emails:
        try:
            notifier.send_mail(smtp, emails, subject, body, ca)  # type: ignore[arg-type]
            out.append({"target": ", ".join(emails), "ok": True, "detail": "e-mail sent"})
        except Exception as e:  # noqa: BLE001
            out.append({"target": ", ".join(emails), "ok": False, "detail": f"{type(e).__name__}: {e}"[:300]})
    for entry in out:
        if not entry["ok"]:
            log.warning("Watchdog '%s': %s not notified (%s)", task.name, entry["target"], entry["detail"])
    return out


def payload(service: Any, task: WatchdogTask, run: WatchdogRun) -> dict[str, Any]:
    """Body of a webhook notification. `event` and `referential` keep the shape the other notifications use,
    so an existing channel template keeps working."""
    base = service.settings.public_url
    url = f"{base.rstrip('/')}/watchdogs/{task.id}" if base else None
    return {
        "event": "watchdog",
        "text": f"🐕 RefExposer watchdog “{task.name}”: {run.severity} — {run.summary}"
                + (f" ({run.affected_items_count} item(s))" if run.affected_items_count else "")
                + (f" {url}" if url else ""),
        "referential": {"id": ", ".join(task.referential_ids or []), "name": task.name,
                        "category": "watchdog", "owner": task.user.email, "url": url},
        "watchdog": {
            "id": task.id, "name": task.name, "trigger": run.trigger,
            "referentials": task.referential_ids or [],
            "finding_detected": run.finding_detected, "severity": run.severity, "summary": run.summary,
            "details_markdown": run.details_markdown, "affected_items_count": run.affected_items_count,
        },
        "run": {"id": run.id, "status": run.status, "message": run.summary,
                "started_at": run.started_at, "finished_at": run.finished_at},
        "previous_status": None,
        "quality": None,
        "instance": base,
        "sent_at": run.finished_at or utcnow(),
    }


def mail_body(service: Any, task: WatchdogTask, run: WatchdogRun) -> str:
    base = service.settings.public_url
    lines = [
        f"Watchdog: {task.name}",
        f"Severity: {run.severity}",
        f"Started by: {run.trigger}",
        f"Referentials: {', '.join(task.referential_ids or []) or '—'}",
        "",
        run.summary or "A finding was detected.",
        "",
    ]
    if run.affected_items_count is not None:
        lines.append(f"Items concerned: {run.affected_items_count}")
        lines.append("")
    if run.details_markdown:
        lines += [run.details_markdown[:20_000], ""]
    if base:
        lines.append(f"{base.rstrip('/')}/watchdogs/{task.id}")
    lines += ["", "This message was sent because this watchdog found something. It stays quiet otherwise."]
    return "\n".join(lines)


# --------------------------------------------------------------------------- what the API returns

def run_dict(run: WatchdogRun, with_details: bool = True) -> dict[str, Any]:
    out = {
        "id": run.id,
        "task_id": run.task_id,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "trigger": run.trigger,
        "status": run.status,
        "finding_detected": run.finding_detected,
        "severity": run.severity,
        "summary": run.summary,
        "affected_items_count": run.affected_items_count,
        "notifications": run.notifications or [],
        "error": run.error,
        "steps": run.steps or [],
    }
    if with_details:
        out["details_markdown"] = run.details_markdown
    return out


def task_dict(service: Any, db: Session, task: WatchdogTask) -> dict[str, Any]:
    last = db.scalars(select(WatchdogRun).where(WatchdogRun.task_id == task.id)
                      .order_by(WatchdogRun.id.desc()).limit(1)).first()
    nxt = next_run(service, task.id)
    return {
        "id": task.id,
        "name": task.name,
        "description": task.description,
        "user_id": task.user_id,
        "username": task.user.username,
        "referential_ids": task.referential_ids or [],
        "trigger_type": task.trigger_type,
        "schedule": task.schedule,
        "prompt": task.prompt,
        "template": task.template,
        "notify_emails": task.notify_emails or [],
        "notify_channels": task.notify_channels or [],
        "min_severity": task.min_severity,
        "enabled": task.enabled,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "created_by": task.created_by,
        "next_run_at": nxt.isoformat() if nxt else None,
        "running": is_running(task.id),
        "last_run": run_dict(last, with_details=False) if last else None,
    }
