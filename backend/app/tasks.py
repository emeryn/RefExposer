"""System tasks: maintenance, integrity and monitoring, security policy and configuration backups.

Each task has a default schedule (cron) that administrators can change, enable or disable, parameters, a
"run now" action and a history (data/.system/tasks.jsonl). Every run that does something is written to the
audit log, and therefore forwarded to syslog when it is configured.
"""

from __future__ import annotations

import json
import logging
import shutil
import socket
import ssl
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Literal
from urllib.parse import urlsplit

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from .storage import dir_size, iso, utcnow

if TYPE_CHECKING:
    from .service import Service

log = logging.getLogger(__name__)

Category = Literal["maintenance", "integrity", "security", "backup", "system"]
Status = Literal["success", "warning", "error"]
KEEP_RUNS = 1000


@dataclass
class Param:
    name: str
    label: str
    type: Literal["int", "bool", "choice", "str"]
    default: Any
    choices: list[str] | None = None
    min: int | None = None
    max: int | None = None
    help: str = ""

    def coerce(self, value: Any) -> Any:
        if self.type == "int":
            v = int(value)
            if (self.min is not None and v < self.min) or (self.max is not None and v > self.max):
                raise ValueError(f"{self.name}: between {self.min} and {self.max}")
            return v
        if self.type == "bool":
            if isinstance(value, bool):
                return value
            return str(value).strip().lower() in ("1", "true", "yes", "on")
        if self.type == "choice":
            if value not in (self.choices or []):
                raise ValueError(f"{self.name}: one of {', '.join(self.choices or [])}")
            return value
        return str(value)


@dataclass
class Result:
    status: Status = "success"
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    changed: bool = True  # quiet tasks (folder scans) only keep the runs that did something


@dataclass
class TaskDef:
    id: str
    name: str
    category: Category
    description: str
    func: Callable[["TaskManager", dict[str, Any]], Result]
    schedule: str | None = None  # default cron
    interval: int | None = None  # fixed interval in seconds (folder scans, set by environment variables)
    enabled: bool = True
    params: list[Param] = field(default_factory=list)
    quiet: bool = False
    available: Callable[["TaskManager"], bool] = lambda m: True


class TaskManager:
    def __init__(self, service: "Service"):
        self.service = service
        self.settings = service.settings
        self.defs: dict[str, TaskDef] = {d.id: d for d in _definitions()}
        self.defs["scan_import"].interval = self.settings.import_poll_seconds
        self.defs["scan_sync"].interval = self.settings.sync_poll_seconds
        self.last: dict[str, dict[str, Any]] = {}
        self.running: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="task")
        self.history_file = self.settings.data_dir / ".system" / "tasks.jsonl"
        for run in self.history(limit=KEEP_RUNS):  # last run of each task, after a restart
            self.last.setdefault(run["task"], run)

    # ------------------------------------------------------------------ configuration
    def _stored(self) -> dict[str, dict[str, Any]]:
        from . import appsettings
        from .db import session_factory

        with session_factory()() as db:
            return dict(appsettings.load(db, "tasks").tasks)

    def config(self, task_id: str, stored: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
        d = self.defs[task_id]
        cfg = (stored if stored is not None else self._stored()).get(task_id, {})
        params = {p.name: p.default for p in d.params}
        for p in d.params:
            if p.name in (cfg.get("params") or {}):
                try:
                    params[p.name] = p.coerce(cfg["params"][p.name])
                except (TypeError, ValueError):
                    pass
        return {"enabled": cfg.get("enabled", d.enabled), "schedule": cfg.get("schedule") or d.schedule, "params": params}

    def update(self, task_id: str, enabled: bool | None, schedule: str | None, params: dict[str, Any] | None, user: str | None) -> dict[str, Any]:
        from . import appsettings
        from .db import session_factory

        d = self.defs[task_id]
        if schedule is not None:
            if d.interval:
                raise ValueError("the interval of this task is set by an environment variable")
            CronTrigger.from_crontab(schedule)  # raises ValueError when invalid
        with session_factory()() as db:
            current = appsettings.load(db, "tasks")
            tasks = {k: dict(v) for k, v in current.tasks.items()}
            entry = tasks.setdefault(task_id, {})
            if enabled is not None:
                entry["enabled"] = enabled
            if schedule is not None:
                entry["schedule"] = schedule
            if params is not None:
                known = {p.name: p for p in d.params}
                unknown = set(params) - set(known)
                if unknown:
                    raise ValueError(f"unknown parameter(s): {', '.join(sorted(unknown))}")
                entry["params"] = {**(entry.get("params") or {}), **{k: known[k].coerce(v) for k, v in params.items()}}
            appsettings.save(db, "tasks", appsettings.TasksSettings(tasks=tasks), user)
        self.schedule_one(task_id)
        return self.describe(task_id)

    # ------------------------------------------------------------------ scheduling
    def schedule_all(self) -> None:
        stored = self._stored()
        for task_id in self.defs:
            self.schedule_one(task_id, stored)

    def schedule_one(self, task_id: str, stored: dict[str, dict[str, Any]] | None = None) -> None:
        sched = self.service.scheduler
        if not sched.running:
            return
        d, job_id = self.defs[task_id], f"task:{task_id}"
        cfg = self.config(task_id, stored)
        if sched.get_job(job_id):
            sched.remove_job(job_id)
        if not cfg["enabled"] or not d.available(self):
            return
        trigger = IntervalTrigger(seconds=d.interval) if d.interval else CronTrigger.from_crontab(cfg["schedule"], timezone=self.service.tz)
        sched.add_job(self.run, trigger, args=[task_id, "schedule"], id=job_id, name=d.name,
                      max_instances=1, coalesce=True, misfire_grace_time=3600, replace_existing=True)

    def next_run(self, task_id: str) -> datetime | None:
        sched = self.service.scheduler
        job = sched.get_job(f"task:{task_id}") if sched.running else None
        return job.next_run_time if job else None

    # ------------------------------------------------------------------ execution
    def submit(self, task_id: str, user: str | None) -> dict[str, Any]:
        """Run now, in the background (one run at a time per task)."""
        if task_id in self.running:
            raise RuntimeError("this task is already running")
        self._pool.submit(self.run, task_id, "manual", user)
        return self.describe(task_id)

    def run(self, task_id: str, trigger: str = "schedule", user: str | None = None) -> dict[str, Any] | None:
        d = self.defs[task_id]
        with self._lock:
            if task_id in self.running:
                return None
            run = {"id": uuid.uuid4().hex[:12], "task": task_id, "trigger": trigger, "user": user, "started_at": iso(utcnow())}
            self.running[task_id] = run
        t0 = time.monotonic()
        try:
            result = d.func(self, self.config(task_id)["params"])
        except Exception as e:  # noqa: BLE001
            log.exception("Task %s failed", task_id)
            result = Result("error", f"{type(e).__name__}: {e}")
        run.update({"finished_at": iso(utcnow()), "duration": round(time.monotonic() - t0, 2), "status": result.status,
                    "message": result.message, "details": result.details})
        with self._lock:
            self.running.pop(task_id, None)
            if result.changed or result.status != "success" or trigger == "manual" or not d.quiet:
                self.last[task_id] = run
        if d.quiet and not result.changed and result.status == "success" and trigger != "manual":
            return run
        self._record(run)
        level = {"success": logging.INFO, "warning": logging.WARNING, "error": logging.ERROR}[result.status]
        log.log(level, "Task %s: %s", task_id, result.message)
        return run

    def _record(self, run: dict[str, Any]) -> None:
        from .auth import audit
        from .db import session_factory

        self.history_file.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            with self.history_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(run, default=str) + "\n")
            lines = self.history_file.read_text(encoding="utf-8").splitlines()
            if len(lines) > KEEP_RUNS * 1.2:
                self.history_file.write_text("\n".join(lines[-KEEP_RUNS:]) + "\n", encoding="utf-8")
        try:
            with session_factory()() as db:
                audit(db, "task.run", username=run["user"] or "system", target=f"task:{run['task']}",
                      success=run["status"] != "error", detail={"trigger": run["trigger"], "status": run["status"], "message": run["message"][:500]})
        except Exception:  # noqa: BLE001
            log.exception("cannot write the audit entry of task %s", run["task"])

    def history(self, task_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        try:
            lines = self.history_file.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out = []
        for line in reversed(lines):
            try:
                run = json.loads(line)
            except ValueError:
                continue
            if task_id is None or run.get("task") == task_id:
                out.append(run)
                if len(out) >= limit:
                    break
        return out

    def describe(self, task_id: str, stored: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
        d = self.defs[task_id]
        cfg = self.config(task_id, stored)
        nxt = self.next_run(task_id)
        return {
            "id": d.id, "name": d.name, "category": d.category, "description": d.description,
            "available": d.available(self), "enabled": cfg["enabled"], "schedule": None if d.interval else cfg["schedule"],
            "default_schedule": d.schedule, "interval": d.interval, "params": cfg["params"],
            "param_defs": [{"name": p.name, "label": p.label, "type": p.type, "default": p.default, "choices": p.choices,
                            "min": p.min, "max": p.max, "help": p.help} for p in d.params],
            "next_run_at": iso(nxt) if nxt else None, "running": self.running.get(task_id), "last_run": self.last.get(task_id),
            "scheduler_running": self.service.scheduler.running,
        }

    def list(self) -> list[dict[str, Any]]:
        stored = self._stored()
        return [self.describe(t, stored) for t in self.defs]

    def stop(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------------ helpers for the tasks
    def db(self):
        from .db import session_factory

        return session_factory()()


# =========================================================================== tasks

def _purge_sessions(m: TaskManager, p: dict[str, Any]) -> Result:
    from .auth import purge_expired_sessions

    with m.db() as db:
        n = purge_expired_sessions(db)
    return Result(message=f"{n} expired session(s) removed", details={"removed": n}, changed=n > 0)


def _audit_retention(m: TaskManager, p: dict[str, Any]) -> Result:
    from sqlalchemy import delete, func, select

    from .models import AuditLog

    limit = datetime.now(timezone.utc) - timedelta(days=p["days"])
    with m.db() as db:
        n = db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.at < limit)) or 0
        db.execute(delete(AuditLog).where(AuditLog.at < limit))
        db.commit()
    return Result(message=f"{n} audit entr{'y' if n == 1 else 'ies'} older than {p['days']} days removed", details={"removed": n})


def _older_than(path: Path, seconds: float) -> bool:
    try:
        return time.time() - path.stat().st_mtime > seconds
    except OSError:
        return False


def _disk_cleanup(m: TaskManager, p: dict[str, Any]) -> Result:
    data = m.settings.data_dir
    freed, removed = 0, []
    # Temporary files left behind (interrupted uploads, previews, exports)
    tmp = m.settings.tmp_dir
    for sub in ("uploads", "preview", "exports"):
        folder = tmp / sub
        for entry in (folder.iterdir() if folder.is_dir() else []):
            if _older_than(entry, p["tmp_hours"] * 3600):
                size = dir_size(entry) if entry.is_dir() else entry.stat().st_size
                shutil.rmtree(entry, ignore_errors=True) if entry.is_dir() else entry.unlink(missing_ok=True)
                freed += size
                removed.append(f".tmp/{sub}/{entry.name}")
    # Folders of referentials that no longer exist
    known = set(m.service.refs)
    orphans = [d for d in data.iterdir() if d.is_dir() and not d.name.startswith(".") and d.name not in known
               and ((d / "meta.json").exists() or (d / "raw").is_dir())]
    orphan_info = [{"folder": d.name, "size": dir_size(d)} for d in orphans]
    if p["orphans"] == "delete":
        for d in orphans:
            freed += dir_size(d)
            shutil.rmtree(d, ignore_errors=True)
            removed.append(d.name)
    status: Status = "warning" if orphans and p["orphans"] == "report" else "success"
    msg = f"{_human(freed)} freed ({len(removed)} item(s))"
    if orphans:
        names = ", ".join(o["folder"] for o in orphan_info[:10])
        msg += f"; {len(orphans)} folder(s) of deleted referentials {'removed' if p['orphans'] == 'delete' else 'found'}: {names}"
    return Result(status, msg, {"freed_bytes": freed, "removed": removed[:200], "orphans": orphan_info})


def _scan_import(m: TaskManager, p: dict[str, Any]) -> Result:
    started = m.service.scan_import_dir()
    return Result(message=f"import started: {', '.join(started)}" if started else "nothing to import",
                  details={"started": started}, changed=bool(started))


def _scan_sync(m: TaskManager, p: dict[str, Any]) -> Result:
    started = m.service.scan_sync()
    return Result(message=f"import started: {', '.join(started)}" if started else "no change",
                  details={"started": started}, changed=bool(started))


def _integrity(m: TaskManager, p: dict[str, Any]) -> Result:
    """Every published version is readable and consistent with what was published."""
    from .sqlbuild import qi
    from .storage import RefPaths

    svc, problems, checked = m.service, [], 0
    for ref in list(svc.refs.values()):
        if not svc.has_data(ref.id):
            continue
        meta = svc.metas.get(ref.id, {})
        paths = RefPaths(m.settings.data_dir, ref.id)
        checked += 1
        try:
            if ref.is_mmdb:
                info = svc.mmdb(ref).info()
                if info["size"] != (meta.get("mmdb") or {}).get("size"):
                    problems.append(f"{ref.id}: size of the MaxMind DB differs from the published one")
            elif ref.is_bloom:
                svc.bloom(ref)
            else:
                expected = meta.get("parquet_size")
                if expected is not None and paths.current.stat().st_size != expected:
                    problems.append(f"{ref.id}: parquet file size changed since publication")
                rows = svc.engine.scalar(f"SELECT count(*) FROM {qi(ref.table)}")
                if meta.get("row_count") is not None and rows != meta["row_count"]:
                    problems.append(f"{ref.id}: {rows:,} rows read, {meta['row_count']:,} published")
                for col, view in svc.engine.index_views.get(ref.table, {}).items():
                    if svc.engine.scalar(f"SELECT count(*) FROM {qi(view)}") != rows:
                        problems.append(f"{ref.id}: index on '{col}' does not match the data")
        except Exception as e:  # noqa: BLE001
            problems.append(f"{ref.id}: unreadable ({type(e).__name__}: {e})")
    if problems:
        return Result("error", f"{len(problems)} problem(s) on {checked} referential(s): " + "; ".join(problems[:5]),
                      {"checked": checked, "problems": problems})
    return Result(message=f"{checked} published version(s) checked: all readable and consistent", details={"checked": checked})


def _stale(m: TaskManager, p: dict[str, Any]) -> Result:
    svc = m.service
    bad = []
    for ref in svc.refs.values():
        if not ref.enabled:
            continue
        meta = svc.metas.get(ref.id, {})
        health = svc.health(ref, meta)
        if health in ("stale", "error"):
            bad.append({"id": ref.id, "health": health, "last_success_at": meta.get("last_success_at"), "last_error": meta.get("last_error")})
    if bad:
        return Result("warning", f"{len(bad)} referential(s) need attention: " + ", ".join(f"{b['id']} ({b['health']})" for b in bad[:10]),
                      {"referentials": bad})
    return Result(message="every referential is up to date", details={"referentials": []})


def _sources(m: TaskManager, p: dict[str, Any]) -> Result:
    from .network import client

    failures, checked = [], 0
    with client(m.settings, timeout=p["timeout"]) as c:
        for ref in m.service.refs.values():
            if not ref.enabled or ref.source.type != "http":
                continue
            for url in ref.source.urls:
                checked += 1
                try:
                    headers = ref.source.request_headers(url)  # a secret may be restricted to some hosts
                except ValueError as e:
                    failures.append({"id": ref.id, "url": url, "error": str(e)})
                    continue
                try:
                    r = c.head(url, headers=headers)
                    if r.status_code in (403, 405, 501):  # HEAD refused by some servers: first byte only
                        with c.stream("GET", url, headers={**headers, "Range": "bytes=0-0"}) as r:
                            pass
                    if r.status_code >= 400:
                        failures.append({"id": ref.id, "url": url, "error": f"HTTP {r.status_code}"})
                except Exception as e:  # noqa: BLE001
                    failures.append({"id": ref.id, "url": url, "error": f"{type(e).__name__}: {e}"[:300]})
    if failures:
        return Result("warning", f"{len(failures)} source(s) unreachable out of {checked}: "
                      + ", ".join(f"{f['id']} ({f['error'][:60]})" for f in failures[:5]), {"checked": checked, "failures": failures})
    return Result(message=f"{checked} source URL(s) reachable", details={"checked": checked})


def _certificate(m: TaskManager, p: dict[str, Any]) -> Result:
    from cryptography import x509

    host, port = p["host"].strip(), p["port"]
    if not host:
        url = m.settings.public_url or ""
        if not url.startswith("https://"):
            return Result(message="nothing to check: REFEX_PUBLIC_URL is not https and no host is set", changed=False)
        parts = urlsplit(url)
        host, port = parts.hostname or "", parts.port or 443
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE  # expiry is checked even for a private CA
    with socket.create_connection((host, port), timeout=15) as raw, ctx.wrap_socket(raw, server_hostname=host) as s:
        cert = x509.load_der_x509_certificate(s.getpeercert(binary_form=True))
    expires = cert.not_valid_after_utc
    days = (expires - datetime.now(timezone.utc)).days
    details = {"host": host, "port": port, "subject": cert.subject.rfc4514_string(), "issuer": cert.issuer.rfc4514_string(),
               "expires_at": expires.isoformat(), "days_left": days}
    if days < 0:
        return Result("error", f"certificate of {host} expired on {expires:%Y-%m-%d}", details)
    if days < p["warn_days"]:
        return Result("warning", f"certificate of {host} expires in {days} day(s) ({expires:%Y-%m-%d})", details)
    return Result(message=f"certificate of {host} valid {days} more days ({expires:%Y-%m-%d})", details=details)


def _inactive_accounts(m: TaskManager, p: dict[str, Any]) -> Result:
    from sqlalchemy import select

    from .auth import audit, revoke_sessions
    from .models import User, aware

    limit = datetime.now(timezone.utc) - timedelta(days=p["days"])
    found = []
    with m.db() as db:
        admins = [u for u in db.scalars(select(User).where(User.role == "admin", User.is_active.is_(True), User.status == "active"))]
        for u in db.scalars(select(User).where(User.is_active.is_(True), User.status == "active", User.auth_source != "service")):
            last = aware(u.last_login_at) or aware(u.created_at)
            if last and last < limit:
                if u.role == "admin" and len([a for a in admins if a.is_active]) <= 1:
                    continue  # never the last active administrator
                found.append(u.username)
                if p["mode"] == "disable":
                    u.is_active = False
                    revoke_sessions(db, u.id)
                    audit(db, "user.disable_inactive", username="system", target=f"user:{u.username}",
                          detail={"days": p["days"], "last_login_at": iso(aware(u.last_login_at)) if u.last_login_at else None}, commit=False)
        db.commit()
    if not found:
        return Result(message=f"no account inactive for more than {p['days']} days", changed=False)
    verb = "disabled" if p["mode"] == "disable" else "found"
    return Result("success" if p["mode"] == "disable" else "warning",
                  f"{len(found)} account(s) inactive for more than {p['days']} days {verb}: {', '.join(found[:20])}", {"accounts": found})


def _expired_tokens(m: TaskManager, p: dict[str, Any]) -> Result:
    from sqlalchemy import delete, func, select

    from .models import ApiToken

    limit = datetime.now(timezone.utc) - timedelta(days=p["grace_days"])
    with m.db() as db:
        cond = (ApiToken.expires_at.is_not(None)) & (ApiToken.expires_at < limit)
        n = db.scalar(select(func.count()).select_from(ApiToken).where(cond)) or 0
        db.execute(delete(ApiToken).where(cond))
        db.commit()
    return Result(message=f"{n} expired API token(s) removed", details={"removed": n}, changed=n > 0)


def _pending(m: TaskManager, p: dict[str, Any]) -> Result:
    from sqlalchemy import select

    from .models import User

    with m.db() as db:
        pending = list(db.scalars(select(User).where(User.status == "pending").order_by(User.created_at)))
        names = [u.username for u in pending]
        oldest = iso(pending[0].created_at) if pending else None
    if not names:
        return Result(message="no pending access request", changed=False)
    return Result("warning", f"{len(names)} access request(s) waiting for approval: {', '.join(names[:20])}",
                  {"pending": names, "oldest": oldest})


def _ldap_resync(m: TaskManager, p: dict[str, Any]) -> Result:
    from sqlalchemy import select

    from . import appsettings, ldapauth
    from .auth import audit, provision, revoke_sessions
    from .models import User

    with m.db() as db:
        cfg = appsettings.load(db, "ldap")
        ca = appsettings.load(db, "proxy").ca_bundle
        users = list(db.scalars(select(User).where(User.auth_source == "ldap", User.is_active.is_(True))))
        if not users:
            return Result(message="no LDAP account", changed=False)
        found = ldapauth.lookup(cfg, [u.username for u in users], ca)
        updated, missing = 0, []
        for u in users:
            ident = found.get(u.username)
            if ident is None:
                missing.append(u.username)
                if p["missing"] == "disable":
                    u.is_active = False
                    revoke_sessions(db, u.id)
                    audit(db, "user.disable_ldap_missing", username="system", target=f"user:{u.username}", commit=False)
                continue
            before = sorted(g.name for g in u.groups), u.role, u.display_name, u.email
            provision(db, None, ident, cfg.admin_group)
            if (sorted(g.name for g in u.groups), u.role, u.display_name, u.email) != before:
                updated += 1
        db.commit()
    msg = f"{len(users)} LDAP account(s) checked, {updated} updated"
    if missing:
        msg += f"; {len(missing)} no longer in the directory ({'disabled' if p['missing'] == 'disable' else 'reported'}): {', '.join(missing[:20])}"
    return Result("warning" if missing and p["missing"] == "report" else "success", msg, {"updated": updated, "missing": missing})


def _backup(m: TaskManager, p: dict[str, Any]) -> Result:
    from . import backup

    with m.db() as db:
        path = backup.write(db, m.settings.data_dir, m.settings.config_dir, encrypt=p["encrypt"], keep=p["keep"])
    return Result(message=f"{path.name} written ({_human(path.stat().st_size)}, {'encrypted' if p['encrypt'] else 'NOT encrypted'})",
                  details={"file": path.name, "size": path.stat().st_size})


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _ldap_enabled(m: TaskManager) -> bool:
    from . import appsettings

    with m.db() as db:
        return bool(appsettings.load(db, "ldap").enabled)


def _definitions() -> list[TaskDef]:
    report_delete = ["report", "delete"]
    return [
        # maintenance
        TaskDef("purge_sessions", "Purge expired sessions", "maintenance",
                "Deletes the sign-in sessions that have expired.", _purge_sessions, schedule="0 * * * *", quiet=True),
        TaskDef("audit_retention", "Audit log retention", "maintenance",
                "Deletes the audit entries older than the retention period (compliance: check your obligations before enabling it).",
                _audit_retention, schedule="30 3 * * *", enabled=False,
                params=[Param("days", "Retention (days)", "int", 365, min=30, max=3650)]),
        TaskDef("disk_cleanup", "Disk cleanup", "maintenance",
                "Removes the temporary files left behind (interrupted uploads, previews, exports) and reports, or deletes, the "
                "folders of referentials that no longer exist.", _disk_cleanup, schedule="0 4 * * *",
                params=[Param("tmp_hours", "Temporary files older than (hours)", "int", 24, min=1, max=720),
                        Param("orphans", "Folders of deleted referentials", "choice", "report", choices=report_delete)]),
        TaskDef("scan_import", "Import folder scan", "system",
                "Imports the files dropped in import/<referential id>/ (interval: REFEX_IMPORT_POLL_SECONDS).", _scan_import,
                interval=None, quiet=True, available=lambda m: bool(m.settings.import_dir)),
        TaskDef("scan_sync", "Sync folder scan", "system",
                "Imports the files of the sync folder that changed (interval: REFEX_SYNC_POLL_SECONDS).", _scan_sync,
                interval=None, quiet=True, available=lambda m: bool(m.settings.sync_dir)),
        # integrity and monitoring
        TaskDef("integrity_check", "Integrity check", "integrity",
                "Checks that every published version is readable and consistent with what was published (size, row count, "
                "indexes, MaxMind DB and Bloom filters).", _integrity, schedule="15 5 * * *"),
        TaskDef("stale_report", "Stale referentials", "integrity",
                "Reports the referentials whose data is older than their max_age, or whose last update failed.", _stale,
                schedule="0 8 * * *"),
        TaskDef("source_check", "Source availability", "integrity",
                "Checks that the URLs of the HTTP sources answer (HEAD request, through the configured proxy).", _sources,
                schedule="0 7 * * *", params=[Param("timeout", "Timeout (seconds)", "int", 20, min=2, max=300)]),
        TaskDef("certificate_check", "TLS certificate expiry", "integrity",
                "Warns before the certificate of the public URL (or of the given host) expires.", _certificate,
                schedule="0 9 * * *",
                params=[Param("warn_days", "Warn this many days before", "int", 30, min=1, max=365),
                        Param("host", "Host (empty: host of REFEX_PUBLIC_URL)", "str", ""),
                        Param("port", "Port", "int", 443, min=1, max=65535)]),
        # security policy
        TaskDef("inactive_accounts", "Inactive accounts", "security",
                "Reports, or disables, the accounts that have not signed in for a while (never the last administrator; service "
                "accounts are not concerned).", _inactive_accounts, schedule="0 6 * * *", enabled=False,
                params=[Param("days", "Inactive for (days)", "int", 90, min=7, max=3650),
                        Param("mode", "Action", "choice", "report", choices=["report", "disable"])]),
        TaskDef("expired_tokens", "Expired API tokens", "security",
                "Deletes the API tokens expired for a while.", _expired_tokens, schedule="45 3 * * *",
                params=[Param("grace_days", "Expired for (days)", "int", 30, min=0, max=3650)]),
        TaskDef("pending_requests", "Pending access requests", "security",
                "Reminds the access requests waiting for an administrator (audit log and syslog).", _pending, schedule="0 9 * * 1-5"),
        TaskDef("ldap_resync", "LDAP resynchronisation", "security",
                "Updates the groups, name and e-mail of the LDAP accounts from the directory without waiting for their next "
                "sign-in, and reports, or disables, the accounts that left the directory.", _ldap_resync, schedule="30 6 * * *",
                params=[Param("missing", "Accounts no longer in the directory", "choice", "report", choices=["report", "disable"])],
                available=_ldap_enabled),
        # backup
        TaskDef("config_backup", "Configuration backup", "backup",
                "Dated backup of the definitions, accounts, groups, rights, API tokens, settings and internal rows "
                "(data/.backups), restorable from this page.", _backup, schedule="0 2 * * *",
                params=[Param("keep", "Backups kept", "int", 14, min=1, max=365),
                        Param("encrypt", "Encrypted (REFEX_SECRET_KEY)", "bool", True)]),
    ]
