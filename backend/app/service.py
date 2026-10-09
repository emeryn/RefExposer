"""Application service: registry, job queue, scheduler and status of referentials."""

from __future__ import annotations

import json
import logging
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from . import __version__, sync
from .config import ConfigError, ReferentialConfig, load_db_registry, load_registry
from .downloads import Downloads
from .engine import Engine
from .ingest import Run, RunCancelled, RunRejected, SourceCorrupted, execute_run
from .settings import Settings
from .storage import RefPaths, append_run, dir_size, iso, parse_iso, read_meta, read_runs, utcnow, write_meta

log = logging.getLogger(__name__)


class BusyError(Exception):
    pass


class Service:
    def __init__(self, settings: Settings):
        self.settings = settings
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.engine = Engine(settings.data_dir, settings.duckdb_memory_limit, settings.duckdb_threads, settings.query_timeout)
        self.downloads = Downloads(self.engine, settings)
        self._blooms: dict[str, tuple[float, Any]] = {}
        self._mmdbs: dict[str, tuple[float, Any]] = {}
        self.refs: dict[str, ReferentialConfig] = {}
        self.config_errors: list[ConfigError] = []
        self.metas: dict[str, dict[str, Any]] = {}
        self.active: dict[str, Run] = {}
        self._cancel: set[str] = set()
        self._lock = threading.RLock()
        self._inbox_seen: dict[str, tuple[int, float]] = {}
        self._pending_publish: dict[str, str | None] = {}
        self._debounce: dict[str, threading.Timer] = {}  # internal referentials waiting for edits to settle
        self.inbox_unknown: list[str] = []
        self._sync: dict[str, Any] = {}  # id -> sync.SyncEntry
        self._sync_state: tuple | None = None  # digest of the last discovery (entries and errors)
        self._sync_seen: dict[str, tuple] = {}  # last file signature observed per sync referential
        self._sync_done: dict[str, tuple] = self._load_sync_state()  # signature already imported
        self.executor = ThreadPoolExecutor(max_workers=max(1, settings.max_concurrent_jobs), thread_name_prefix="ingest")
        from .notify import Notifier

        self.notifier = Notifier(settings)  # e-mails and webhooks on failures, recoveries, new versions
        self.tz = ZoneInfo(settings.timezone)
        self.scheduler = BackgroundScheduler(timezone=self.tz)
        self.started_at = utcnow()
        from .tasks import TaskManager

        self.tasks = TaskManager(self)

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self.reload()
        if self.settings.scheduler_enabled:
            self.scheduler.start()
            # System tasks (folder scans, maintenance, monitoring, security, backups): see tasks.py
            self.tasks.schedule_all()
        mode = self.settings.refresh_on_startup
        if mode != "none":
            for ref in self.refs.values():
                # Internal referentials are always checked: edits not yet published at the last stop are picked
                # up, and an identical export ends as "unchanged" without any rebuild
                if ref.enabled and (mode == "all" or ref.is_internal or not RefPaths(self.settings.data_dir, ref.id).current.exists()):
                    if ref.is_sync and ref.id in self._sync:
                        self._sync_done[ref.id] = self._sync[ref.id].signature
                    self.submit(ref.id, "startup")
            self._save_sync_state()

    def stop(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
        with self._lock:
            for timer in self._debounce.values():
                timer.cancel()
            self._debounce.clear()
        self.tasks.stop()
        self.notifier.stop()
        self._cancel.update(self.active)
        self.executor.shutdown(wait=False, cancel_futures=True)

    def _load_all(self) -> tuple[dict[str, ReferentialConfig], list[ConfigError]]:
        """YAML files first, then definitions created from the UI (stored in the database)."""
        refs, errors = load_registry(self.settings.config_dir)
        try:
            from .db import session_factory

            with session_factory()() as db:
                db_refs, db_errors = load_db_registry(db)
        except RuntimeError:  # database not initialised (unit usage)
            return refs, errors
        errors.extend(db_errors)
        for ref_id, ref in db_refs.items():
            if ref_id in refs:
                errors.append(ConfigError(file=f"base:{ref_id}", error=f"identifier already defined in config/{refs[ref_id].config_file}"))
                continue
            refs[ref_id] = ref
        self._add_sync(refs, errors)
        return refs, errors

    # ------------------------------------------------------------------ sync folder
    def _add_sync(self, refs: dict[str, ReferentialConfig], errors: list[ConfigError]) -> None:
        if not self.settings.sync_dir:
            self._sync = {}
            return
        entries, sync_errors = sync.discover(self.settings.sync_dir)
        self._sync_state = self._sync_digest(entries, sync_errors)
        errors.extend(sync_errors)
        for ref_id, entry in list(entries.items()):
            if ref_id in refs:
                where = refs[ref_id].config_file or "the interface"
                errors.append(ConfigError(file=entry.ref.config_file, error=f"identifier '{ref_id}' already defined in {where}"))
                del entries[ref_id]
                continue
            refs[ref_id] = entry.ref
        self._sync = entries

    @staticmethod
    def _sync_digest(entries: dict[str, Any], errors: list[ConfigError]) -> tuple:
        return tuple(sorted((i, e.ref.config_hash) for i, e in entries.items())), tuple(sorted((e.file, e.error) for e in errors))

    def _sync_state_file(self) -> Path:
        return self.settings.data_dir / ".sync_state.json"

    def _load_sync_state(self) -> dict[str, tuple]:
        try:
            data = json.loads(self._sync_state_file().read_text(encoding="utf-8"))
            return {k: tuple(tuple(x) for x in v) for k, v in data.items()}
        except (OSError, ValueError):
            return {}

    def _save_sync_state(self) -> None:
        try:
            self._sync_state_file().write_text(json.dumps({k: list(v) for k, v in self._sync_done.items()}), encoding="utf-8")
        except OSError as e:
            log.warning("Cannot save the sync state: %s", e)

    def scan_sync(self) -> list[str]:
        """Pick up new, changed and removed entries of the sync folder. Returns the started imports."""
        if not self.settings.sync_dir:
            return []
        entries, errors = sync.discover(self.settings.sync_dir)
        if self._sync_digest(entries, errors) != self._sync_state:
            self.reload()  # entry added or removed, refexposer.yml changed, new error...
        started: list[str] = []
        for ref_id, entry in entries.items():  # fresh signatures (self._sync holds those of the last reload)
            if ref_id not in self._sync:
                continue
            sig = entry.signature
            previous = self._sync_seen.get(ref_id)
            self._sync_seen[ref_id] = sig
            if sig == self._sync_done.get(ref_id):
                continue
            # Wait until the files stop changing: same signature as the previous scan, nothing written recently
            if previous != sig or not sync.settled(entry.files, self.settings.sync_settle_seconds) or ref_id in self.active:
                continue
            self._sync_done[ref_id] = sig
            self._save_sync_state()
            log.info("[%s] sync folder: files changed, import started", ref_id)
            if self.submit(ref_id, "sync"):
                started.append(ref_id)
        return started

    def reload(self) -> dict[str, Any]:
        refs, errors = self._load_all()
        with self._lock:
            removed = set(self.refs) - set(refs)
            self.refs, self.config_errors = refs, errors
            for ref_id in removed:
                self.metas.pop(ref_id, None)
            for table in list(self.engine.views):
                if table not in {r.table for r in refs.values()}:
                    self.engine.unregister(table)
            for ref in refs.values():
                paths = RefPaths(self.settings.data_dir, ref.id)
                meta = read_meta(paths)
                if meta.get("status") in ("running", "queued") and ref.id not in self.active:
                    meta["status"] = "error"
                    meta["last_error"] = "update interrupted (service restart)"
                    write_meta(paths, meta)
                self.metas[ref.id] = self._in_memory(ref.id, meta)
                if paths.current.exists():
                    try:
                        self._register(ref, meta)
                    except Exception as e:  # noqa: BLE001
                        log.error("Cannot expose %s: %s", ref.id, e)
        self._schedule_all()
        self._prepare_import_dir()
        return {"loaded": len(refs), "errors": [e.model_dump() for e in errors], "removed": sorted(removed)}

    def _schedule_all(self) -> None:
        for job in self.scheduler.get_jobs():
            if not job.id.startswith("__"):  # internal jobs (session purge...) are kept
                job.remove()
        for ref in self.refs.values():
            if ref.enabled and ref.schedule:
                self.scheduler.add_job(
                    self.submit,
                    CronTrigger.from_crontab(ref.schedule, timezone=self.tz),
                    args=[ref.id, "schedule"],
                    id=ref.id,
                    name=ref.name,
                    coalesce=True,
                    max_instances=1,
                    misfire_grace_time=3600,
                    replace_existing=True,
                )

    # ------------------------------------------------------------------ jobs
    def submit(self, ref_id: str, trigger: str = "manual", force: bool = False, user: str | None = None,
               manual: dict[str, Any] | None = None) -> Run | None:
        with self._lock:
            if ref_id in self.active:
                if manual is not None:
                    raise BusyError("an update is already running for this referential")
                return self.active[ref_id]
            meta = self.metas.setdefault(ref_id, {})
            if trigger in ("schedule", "startup") and meta.get("pinned"):
                log.info("[%s] frozen version: automatic update skipped", ref_id)
                return None
            ref = self.refs[ref_id]
            run = Run(ref.id, trigger, force, user)
            self.active[ref_id] = run
            meta["status"] = "queued"
        self.executor.submit(self._execute, ref, run, manual)
        return run

    # ------------------------------------------------------------------ internal referentials
    @staticmethod
    def _internal_rows(ref_id: str) -> list[dict[str, Any]]:
        from sqlalchemy import select

        from .db import session_factory
        from .models import InternalRecord
        from .records_codec import load

        with session_factory()() as db:
            # Only the data column: no ORM object per row
            return [load(ref_id, d) for d in db.scalars(select(InternalRecord.data).where(InternalRecord.referential_id == ref_id).order_by(InternalRecord.row_key))]

    def publish_internal(self, ref_id: str, user: str | None, delay: float | None = None) -> None:
        """Republish an internal referential after edits. The publication waits `delay` seconds
        (internal_publish_delay by default) without other edits, so that a burst of edits gives one
        publication. Edits made while a publication is running are picked up by one more publication
        when it ends."""
        delay = self.settings.internal_publish_delay if delay is None else delay
        with self._lock:
            timer = self._debounce.pop(ref_id, None)
            if timer is not None:  # already waiting: wait again from now
                timer.cancel()
                run = self.active.get(ref_id)
                if run is not None:
                    run.user = user or run.user
                if delay <= 0:
                    self._start_debounced(ref_id)
                else:
                    self._arm(ref_id, delay)
                return
            if ref_id in self.active:
                self._pending_publish[ref_id] = user
                return
            if delay > 0 and ref_id in self.refs:
                # Shown as queued right away (current_run), started when the edits settle
                self.active[ref_id] = Run(ref_id, "edit", False, user)
                self.metas.setdefault(ref_id, {})["status"] = "queued"
                self._arm(ref_id, delay)
                return
        self.submit(ref_id, "edit", False, user)

    def _arm(self, ref_id: str, delay: float) -> None:
        timer = threading.Timer(delay, self._start_debounced, (ref_id,))
        timer.daemon = True
        self._debounce[ref_id] = timer
        timer.start()

    def _start_debounced(self, ref_id: str) -> None:
        with self._lock:
            self._debounce.pop(ref_id, None)
            run, ref = self.active.get(ref_id), self.refs.get(ref_id)
            if run is None or run.trigger != "edit" or run.started_at is not None:
                return
            if ref is None:  # deleted meanwhile
                self.active.pop(ref_id, None)
                return
        self.executor.submit(self._execute, ref, run, None)

    def discard_scheduled(self, ref_id: str) -> None:
        """Forget a publication still waiting for edits to settle (referential deleted)."""
        with self._lock:
            timer = self._debounce.pop(ref_id, None)
            if timer is not None:
                timer.cancel()
                self.active.pop(ref_id, None)

    @staticmethod
    def _in_memory(ref_id: str, meta: dict[str, Any]) -> dict[str, Any]:
        """Meta kept in memory: the encrypted column profile of a confidential referential is decrypted here
        (never written back in clear: meta.json is always written from the stored form)."""
        if not meta.get("profile_enc"):
            return meta
        from .crypto import decrypt_data

        try:
            return {**meta, "profile": json.loads(decrypt_data(meta["profile_enc"], ref_id))}
        except Exception:  # noqa: BLE001  (key changed)
            log.error("[%s] cannot decrypt the column profile (encryption key changed?)", ref_id)
            return meta

    def _register(self, ref: ReferentialConfig, meta: dict[str, Any]) -> None:
        """Expose the published version; an encrypted one (confidential) is read with its key."""
        from .crypto import parquet_key

        paths = RefPaths(self.settings.data_dir, ref.id)
        key = parquet_key(ref.id) if meta.get("encrypted") else None
        self.engine.register(ref.table, paths.current, self.index_files(ref), key)

    def is_pinned(self, ref_id: str) -> bool:
        return bool(self.metas.get(ref_id, {}).get("pinned"))

    def set_pinned(self, ref_id: str, pinned: dict[str, Any] | None) -> None:
        paths = RefPaths(self.settings.data_dir, ref_id)
        meta = read_meta(paths)
        if pinned:
            meta["pinned"] = pinned
        else:
            meta.pop("pinned", None)
        write_meta(paths, meta)
        with self._lock:
            self.metas[ref_id] = {**self.metas.get(ref_id, {}), "pinned": pinned} if pinned else {
                k: v for k, v in self.metas.get(ref_id, {}).items() if k != "pinned"}

    # ------------------------------------------------------------------ manual imports
    def import_files(self, ref_id: str, files_dir: Path, origin: str, user: str | None, pin: bool = False) -> Run:
        """Import the files of `files_dir` as the new version of the referential.
        origin: "upload" (sent from the UI / API) or "inbox" (dropped in the import folder)."""
        files = sorted(p for p in files_dir.iterdir() if p.is_file())
        if not files:
            raise ValueError("no file to import")
        manual = {"files": files, "dir": files_dir, "origin": origin, "by": user, "pin": pin}
        return self.submit(ref_id, origin, False, user, manual)  # type: ignore[return-value]

    def _prepare_import_dir(self) -> None:
        root = self.settings.import_dir
        if not root:
            return
        try:
            root.mkdir(parents=True, exist_ok=True)
            # Folders of referentials that no longer exist (or became internal) are removed when they hold nothing but the README
            wanted = {ref.id for ref in self.refs.values() if not (ref.is_internal or ref.is_sync)}
            for d in root.iterdir():
                if d.is_dir() and not d.name.startswith(".") and d.name not in wanted:
                    content = list(d.iterdir())
                    if all(f.name == "README.txt" for f in content):
                        for f in content:
                            f.unlink()
                        d.rmdir()
            for ref in self.refs.values():
                if ref.is_internal or ref.is_sync:  # fed by the editor / the API, or already by files
                    continue
                d = root / ref.id
                d.mkdir(exist_ok=True)
                readme = d / "README.txt"
                if not readme.exists():
                    readme.write_text(
                        f"Drop here the files to import into the referential '{ref.name}' ({ref.format}).\n"
                        "They are picked up automatically a few seconds after the copy ends, then moved\n"
                        "to .done/ (import succeeded) or .failed/ (with an ERROR.txt file explaining the problem).\n"
                        "Tip: for large files, copy under a temporary name (*.part) then rename.\n",
                        encoding="utf-8",
                    )
        except OSError as e:
            log.warning("Import folder unusable (%s): %s", root, e)

    def scan_import_dir(self) -> list[str]:
        """Pick up stable files dropped in <import_dir>/<referential id>/. Returns the started imports."""
        root = self.settings.import_dir
        started: list[str] = []
        if not root or not root.exists():
            return started
        unknown = []
        now = datetime.now().timestamp()
        for d in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
            ref = self.refs.get(d.name)
            if ref is None or ref.is_internal:
                unknown.append(d.name)
                continue
            files = [f for f in d.iterdir() if f.is_file() and not f.name.startswith(".") and f.name != "README.txt"
                     and not f.name.lower().endswith((".part", ".tmp", ".crdownload", ".partial", ".filepart", ".!ut"))]
            if not files:
                continue
            ready = True
            for f in files:
                st = f.stat()
                sig = (st.st_size, st.st_mtime)
                prev = self._inbox_seen.get(str(f))
                self._inbox_seen[str(f)] = sig
                if prev != sig or now - st.st_mtime < 5:
                    ready = False
            if not ready or ref.id in self.active:
                continue
            batch = d / ".processing" / datetime.now().strftime("%Y%m%d-%H%M%S")
            batch.mkdir(parents=True, exist_ok=True)
            for f in files:
                shutil.move(str(f), str(batch / f.name))
                self._inbox_seen.pop(str(f), None)
            log.info("[%s] import folder: %d file(s) picked up", ref.id, len(files))
            try:
                self.import_files(ref.id, batch, "inbox", None)
                started.append(ref.id)
            except Exception as e:  # noqa: BLE001
                self._finish_inbox(batch, ok=False, message=str(e))
        self.inbox_unknown = unknown
        return started

    def _finish_inbox(self, batch: Path, ok: bool, message: str | None = None, keep_files: bool = True) -> None:
        base = batch.parent.parent  # <import_dir>/<id>
        target = base / (".done" if ok else ".failed") / batch.name
        target.parent.mkdir(parents=True, exist_ok=True)
        if keep_files:
            shutil.move(str(batch), str(target))
        else:  # confidential referential: only the outcome is kept
            shutil.rmtree(batch, ignore_errors=True)
            target.mkdir(exist_ok=True)
            (target / "FILES-DELETED.txt").write_text("Confidential referential: the imported files are not kept.\n", encoding="utf-8")
        if not ok:
            (target / "ERROR.txt").write_text(f"Import refused: {message}\n", encoding="utf-8")
        else:
            done = sorted((base / ".done").iterdir())
            for old in done[: max(0, len(done) - self.settings.import_keep_done)]:
                shutil.rmtree(old, ignore_errors=True)
        processing = base / ".processing"
        if processing.exists() and not any(processing.iterdir()):
            processing.rmdir()

    def cancel(self, ref_id: str) -> bool:
        with self._lock:
            if ref_id not in self.active:
                return False
            timer = self._debounce.pop(ref_id, None)
            if timer is not None:  # not started yet: simply forgotten
                timer.cancel()
                self.active.pop(ref_id, None)
                self.metas[ref_id] = self._in_memory(ref_id, read_meta(RefPaths(self.settings.data_dir, ref_id)))
                return True
            self._cancel.add(ref_id)
            return True

    def _execute(self, ref: ReferentialConfig, run: Run, manual: dict[str, Any] | None = None) -> None:
        paths = RefPaths(self.settings.data_dir, ref.id)
        old_meta = dict(self.metas.get(ref.id, {}))
        previous_status = read_meta(paths).get("status")  # last finished update (in memory: queued)
        self.metas[ref.id] = {**old_meta, "status": "running"}
        try:
            meta = execute_run(ref, self.settings, run, cancelled=lambda: ref.id in self._cancel, manual=manual,
                               internal_rows=self._internal_rows)
            if manual is not None:
                meta["manual_import"] = {"origin": manual["origin"], "by": manual.get("by"), "at": iso(utcnow()),
                                         "files": [f.name for f in manual["files"]], "run": run.id}
                if manual.get("pin"):
                    meta["pinned"] = {"by": manual.get("by"), "at": iso(utcnow()), "reason": "manual import"}
            elif run.status != "unchanged":
                meta.pop("manual_import", None)
            if run.status != "unchanged":
                run.status = "success"
            meta.update({
                "status": "ok",
                "last_error": None,
                "last_success_at": iso(utcnow()),
            })
            if paths.current.exists():
                self._register(ref, meta)
            if ref.downloads and run.status == "success":
                run.phase = "downloads"
                self.downloads.prebuild(ref, meta)
        except RunCancelled:
            run.status, run.message = "cancelled", "cancelled by the user"
            run.log("Update cancelled", "warn")
            meta = {**read_meta(paths), "status": "cancelled", "last_error": run.message}
        except SourceCorrupted as e:
            run.status, run.message = "corrupted", str(e)
            run.log(f"{e}. The published version is kept.", "error")
            meta = {**read_meta(paths), "status": "corrupted", "last_error": str(e)}
        except RunRejected as e:
            run.status, run.message = "rejected", str(e)
            run.log(f"New version rejected: {e}. The previous version is kept.", "error")
            meta = {**read_meta(paths), "status": "rejected", "last_error": str(e)}
        except Exception as e:  # noqa: BLE001
            log.exception("Run %s failed", run.id)
            run.status, run.message = "error", f"{type(e).__name__}: {e}"
            run.log(run.message, "error")
            meta = {**read_meta(paths), "status": "error", "last_error": run.message}
        finally:
            run.finished_at = utcnow()
            run.phase = "done"
        if manual is not None:
            ok = run.status in ("success", "unchanged")
            try:
                if ref.confidential:  # confidential: the imported files are never kept in clear
                    if manual["origin"] == "inbox":
                        self._finish_inbox(manual["dir"], ok, run.message, keep_files=False)
                    else:
                        shutil.rmtree(manual["dir"], ignore_errors=True)
                elif manual["origin"] == "inbox":
                    self._finish_inbox(manual["dir"], ok, run.message)
                elif ok:  # uploads: keep the imported files as the source of the current version
                    for old in (paths.root / "manual").glob("*"):
                        if old != manual["dir"]:
                            shutil.rmtree(old, ignore_errors=True)
                else:
                    shutil.rmtree(manual["dir"], ignore_errors=True)
            except OSError as e:
                log.warning("[%s] cannot move the imported files: %s", ref.id, e)
        if run.status in ("success", "unchanged"):
            run.message = run.message or ("No change" if run.status == "unchanged" else "New version published")
        meta["last_run"] = run.to_dict(with_logs=False)
        meta["last_attempt_at"] = iso(run.finished_at)
        meta["storage_size"] = dir_size(paths.root)
        write_meta(paths, meta)
        append_run(paths, run.to_dict(), self.settings.keep_runs)
        try:
            self.notifier.run_finished(ref, run.to_dict(with_logs=False), previous_status, meta.get("status"), run.logs)
        except Exception:  # noqa: BLE001 (a notification never breaks an update)
            log.exception("[%s] notifications not sent", ref.id)
        with self._lock:
            self.metas[ref.id] = self._in_memory(ref.id, meta)
            self.active.pop(ref.id, None)
            self._cancel.discard(ref.id)
            again = self._pending_publish.pop(ref.id, False)
        if again is not False and ref.id in self.refs:
            self.submit(ref.id, "edit", False, again)

    # ------------------------------------------------------------------ status
    def index_files(self, ref: ReferentialConfig) -> dict[str, tuple[str, Path]]:
        paths = RefPaths(self.settings.data_dir, ref.id)
        return {c: (RefPaths.index_name(c), paths.index_path(c)) for c in ref.storage.indexes if paths.index_path(c).exists()}

    def is_large(self, ref_id: str) -> bool:
        return (self.metas.get(ref_id, {}).get("row_count") or 0) > self.settings.large_rows

    def get(self, ref_id: str) -> ReferentialConfig | None:
        return self.refs.get(ref_id)

    def has_data(self, ref_id: str) -> bool:
        ref = self.refs.get(ref_id)
        if ref and ref.is_bloom:
            return RefPaths(self.settings.data_dir, ref.id).bloom.exists()
        if ref and ref.is_mmdb:
            return RefPaths(self.settings.data_dir, ref.id).mmdb.exists()
        return bool(ref and ref.table in self.engine.views)

    def bloom(self, ref: ReferentialConfig):
        """Opened Bloom filter of a referential (reopened when a new version is published)."""
        from .bloom import BloomFilter

        path = RefPaths(self.settings.data_dir, ref.id).bloom
        mtime = path.stat().st_mtime
        with self._lock:
            cached = self._blooms.get(ref.id)
            if cached and cached[0] == mtime:
                return cached[1]
            bf = BloomFilter(path)
            self._blooms[ref.id] = (mtime, bf)
            return bf

    def mmdb(self, ref: ReferentialConfig):
        """Opened MaxMind DB of a referential (reopened when a new version is published). The previous reader is
        not closed: lookups still running keep reading the old file, which stays readable once replaced."""
        from .geoip import MmdbDatabase

        path = RefPaths(self.settings.data_dir, ref.id).mmdb
        mtime = path.stat().st_mtime
        with self._lock:
            cached = self._mmdbs.get(ref.id)
            if cached and cached[0] == mtime:
                return cached[1]
            db = MmdbDatabase(path)
            self._mmdbs[ref.id] = (mtime, db)
            return db

    def next_run(self, ref_id: str) -> datetime | None:
        job = self.scheduler.get_job(ref_id) if self.scheduler.running else None
        if job:
            return job.next_run_time
        ref = self.refs.get(ref_id)
        if ref and ref.schedule and ref.enabled:
            return CronTrigger.from_crontab(ref.schedule, timezone=self.tz).get_next_fire_time(None, datetime.now(self.tz))
        return None

    def _stale_after(self, ref: ReferentialConfig) -> timedelta | None:
        if ref.max_age_delta:
            return ref.max_age_delta
        if ref.schedule:
            trig = CronTrigger.from_crontab(ref.schedule, timezone=self.tz)
            first = trig.get_next_fire_time(None, datetime.now(self.tz))
            second = trig.get_next_fire_time(first, first + timedelta(seconds=1)) if first else None
            if first and second:
                return max((second - first) * 2, timedelta(hours=1))
        return None

    def health(self, ref: ReferentialConfig, meta: dict[str, Any]) -> str:
        """ok | stale | error | running | empty | disabled"""
        if ref.id in self.active:
            return "running"
        has_data = self.has_data(ref.id)
        if meta.get("status") in ("error", "rejected", "cancelled", "corrupted") and not has_data:
            return "error"
        if not has_data:
            return "empty" if ref.enabled else "disabled"
        if meta.get("status") in ("error", "rejected", "corrupted"):
            return "error"
        stale_after = self._stale_after(ref)
        last = parse_iso(meta.get("last_success_at"))
        if stale_after and last and utcnow() - last > stale_after:
            return "stale"
        return "ok"

    def summary(self, ref: ReferentialConfig, access: str | None = None) -> dict[str, Any]:
        meta = self.metas.get(ref.id, {})
        run = self.active.get(ref.id)
        nxt = self.next_run(ref.id)
        return {
            "id": ref.id,
            "name": ref.name,
            "description": ref.description,
            "category": ref.category,
            "tags": ref.tags,
            "homepage": ref.homepage,
            "license": ref.license,
            "owner": ref.owner,
            "format": ref.format,
            "source_type": ref.source.type,
            "origin": ref.origin,
            "kind": "bloom" if ref.is_bloom else "mmdb" if ref.is_mmdb else "internal" if ref.is_internal else "table",
            "config_file": ref.config_file,
            "source_urls": ref.source.urls,
            "key": ref.key,
            "table": ref.table,
            "schedule": ref.schedule,
            "enabled": ref.enabled,
            "next_run_at": iso(nxt) if nxt else None,
            "health": self.health(ref, meta),
            "has_data": self.has_data(ref.id),
            "status": meta.get("status"),
            "last_error": meta.get("last_error"),
            "version": meta.get("version"),
            "row_count": meta.get("row_count"),
            "column_count": len(meta.get("columns") or []),
            "previous_row_count": meta.get("previous_row_count"),
            "changes": meta.get("changes"),
            "parquet_size": meta.get("parquet_size"),
            "raw_size": meta.get("raw_size"),
            "storage_size": meta.get("storage_size"),
            "last_success_at": meta.get("last_success_at"),
            "last_attempt_at": meta.get("last_attempt_at"),
            "last_checked_at": meta.get("last_checked_at"),
            "data_updated_at": meta.get("data_updated_at"),
            "last_run": meta.get("last_run"),
            "current_run": run.to_dict(with_logs=False) if run else None,
            "access": access,
            "pinned": meta.get("pinned"),
            "confidential": ref.confidential,
            # state of the published version: differs from `confidential` until the next publication
            "encrypted": bool(meta.get("encrypted")),
            "manual_import": meta.get("manual_import"),
            "import_folder": str(self.settings.import_dir / ref.id) if self.settings.import_dir and not (ref.is_sync or ref.is_internal) else None,
            "sync_path": f"{self.settings.sync_dir}/{ref.source.path}" if ref.is_sync else None,
            "large": self.is_large(ref.id),
            "sort_by": ref.storage.sort_by,
            "indexes": list(self.engine.index_views.get(ref.table, {})),
        }

    def detail(self, ref: ReferentialConfig, access: str | None = None) -> dict[str, Any]:
        meta = self.metas.get(ref.id, {})
        run = self.active.get(ref.id)
        config = ref.model_dump(mode="json", exclude={"config_file"})
        # Source headers may carry credentials (API keys...): never expose their values
        config["source"]["headers"] = {k: "***" for k in config["source"].get("headers", {})}
        for secret in ("basic_auth", "token"):
            if config["source"].get(secret):
                config["source"][secret] = "***"
        return {
            **self.summary(ref, access),
            "current_run": run.to_dict(with_logs=True) if run else None,
            "columns": meta.get("columns") or [],
            "profile": meta.get("profile") or [],
            "key_unique": meta.get("key_unique"),
            "profile_sampled": meta.get("profile_sampled", False),
            "bloom": meta.get("bloom"),
            "mmdb": meta.get("mmdb"),
            "index_views": self.engine.index_views.get(ref.table, {}),
            "has_previous": RefPaths(self.settings.data_dir, ref.id).previous.exists(),
            "sources": meta.get("sources") or [],
            "search_columns": ref.search_columns,
            "max_age": ref.max_age,
            "config": config,
            "config_file": ref.config_file,
        }

    def runs(self, ref_id: str, limit: int = 50) -> list[dict[str, Any]]:
        out = []
        if ref_id in self.active:
            out.append(self.active[ref_id].to_dict())
        out.extend(read_runs(RefPaths(self.settings.data_dir, ref_id), limit))
        return out[:limit]

    def system(self) -> dict[str, Any]:
        import duckdb

        jobs = []
        if self.scheduler.running:
            for job in self.scheduler.get_jobs():
                if job.id.startswith(("__", "task:")):
                    continue
                jobs.append({"id": job.id, "name": job.name, "next_run_at": iso(job.next_run_time)})
        disk = dir_size(self.settings.data_dir)
        return {
            "version": __version__,
            "duckdb_version": duckdb.__version__,
            "started_at": iso(self.started_at),
            "data_dir": str(self.settings.data_dir),
            "config_dir": str(self.settings.config_dir),
            "timezone": self.settings.timezone,
            "scheduler_enabled": self.settings.scheduler_enabled,
            "scheduler_running": self.scheduler.running,
            "max_concurrent_jobs": self.settings.max_concurrent_jobs,
            "import_dir": str(self.settings.import_dir) if self.settings.import_dir else None,
            "import_poll_seconds": self.settings.import_poll_seconds,
            "import_unknown_folders": self.inbox_unknown,
            "public_url": self.settings.public_url,
            "sync_dir": str(self.settings.sync_dir) if self.settings.sync_dir else None,
            "sync_poll_seconds": self.settings.sync_poll_seconds,
            "sync_settle_seconds": self.settings.sync_settle_seconds,
            "sync_referentials": sorted(self._sync),
            "refresh_on_startup": self.settings.refresh_on_startup,
            "excel_support": self.engine.excel,
            "limits": {
                "api_max_limit": self.settings.api_max_limit,
                "sql_timeout": self.settings.sql_timeout,
                "sql_max_rows": self.settings.sql_max_rows,
                "export_max_rows": self.settings.export_max_rows,
            },
            "storage_size": disk,
            "referential_count": len(self.refs),
            "active_runs": [r.to_dict(with_logs=False) for r in self.active.values()],
            "scheduled_jobs": sorted(jobs, key=lambda j: j["next_run_at"] or ""),
            "config_errors": [e.model_dump() for e in self.config_errors],
        }
