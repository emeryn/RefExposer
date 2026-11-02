"""Full backup: the whole instance in one file, restorable as it was — configuration **and** data.

What backup.py writes is the configuration alone (definitions, accounts, rights, settings, internal rows): small,
dated, and restorable on a fresh instance that then downloads its sources again. This module writes everything:

- every table of the database (accounts, definitions, settings, internal rows, saved searches, watchdogs, the
  audit log), dumped as JSON so the archive restores onto SQLite or PostgreSQL alike;
- the YAML definitions of the configuration folder;
- `data/`: the published versions (current / previous Parquet, Bloom filters, MaxMind DBs), their indexes, the
  metadata, the run history, the SQLite bases kept for deltas, and the system folder.

Left out on purpose: the temporary folders (`.tmp`, `.work`), the downloads generated from a published version
(`exports/`, rebuilt on demand), the previous backups (`.backups/`), and **the encryption key**
(`data/.secret_key`) — an archive that carried its own key would protect nothing. Keep that key, or
`REFEX_SECRET_KEY`, somewhere else: without it a restored instance cannot read its secrets or its confidential
referentials. The downloaded source files (`raw/`) are left out too unless asked for: they come from their
source again, and they are routinely the largest thing on the disk.

Shape of the file: a streamed `tar` (gzip), so an instance of several hundred GB is never held in memory, and
`manifest.json` comes first so a restore knows what it is looking at before reading the rest. Encryption, on
by default, is AES-256-GCM applied **chunk by chunk** (`_Sealed`): each chunk carries its own nonce and is
authenticated with its own index, so a truncated, reordered or altered archive is refused rather than
half-restored.

Destinations: a file on the server (`data/.backups/full/`) and / or an S3 bucket (see s3.py). The schedule of
the `full_backup` task sets how often it runs.
"""

from __future__ import annotations

import gzip
import io
import json
import logging
import os
import re
import secrets
import shutil
import tarfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Callable, Iterator

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import DateTime, Integer, delete, insert, select, text
from sqlalchemy.orm import Session

from . import __version__
from .crypto import derive
from .models import Base
from .settings import Settings
from .storage import iso, utcnow

log = logging.getLogger(__name__)

FORMAT = "refexposer-full-backup"
MAGIC = b"RFXFB1"
NAME_RE = re.compile(r"^refexposer-full-\d{8}-\d{6}-\d{3}\.tar\.gz(\.enc)?$")
CHUNK = 4 << 20  # plaintext bytes per sealed chunk
LENGTH = 4  # bytes of the length prefix of a sealed chunk

# Tables never carried over: a restored instance signs everybody in again rather than reviving old sessions
SKIP_TABLES = ("sessions",)
# Folders and files of data/ that a backup does not carry (see the module docstring)
SKIP_DIRS = (".tmp", ".backups", ".work", "exports")
# Working folders of a restore going on: never carried, and never walked into
RESTORE_PREFIX = ".restore-"
SKIP_FILES = (".secret_key", "running.json")
# Rebuilt by the first update, and nothing is lost by leaving them out
SKIP_SUFFIXES = (".tmp", ".part", ".incoming")


class BackupError(ValueError):
    """Something the archive is not, or a restore that cannot go ahead."""


def full_dir(data_dir: Path) -> Path:
    return data_dir / ".backups" / "full"


def _key() -> bytes:
    return derive("full-backup")


# --------------------------------------------------------------------------- chunked encryption

class _SealedWriter(io.RawIOBase):
    """File-like that seals what is written to it: MAGIC, then `[length][nonce][ciphertext]` per chunk.

    The chunk index goes into the additional authenticated data, so chunks cannot be swapped or dropped, and a
    final empty chunk marks the end: an archive cut short fails to open instead of restoring a part of itself.
    """

    def __init__(self, target: BinaryIO):
        self._target = target
        self._aes = AESGCM(_key())
        self._buffer = bytearray()
        self._index = 0
        self._target.write(MAGIC)

    def writable(self) -> bool:
        return True

    def write(self, data: Any) -> int:  # noqa: ANN401 - the signature of RawIOBase
        view = bytes(data)
        self._buffer += view
        while len(self._buffer) >= CHUNK:
            self._seal(bytes(self._buffer[:CHUNK]))
            del self._buffer[:CHUNK]
        return len(view)

    def _seal(self, plain: bytes) -> None:
        nonce = secrets.token_bytes(12)
        sealed = self._aes.encrypt(nonce, plain, MAGIC + self._index.to_bytes(8, "big"))
        self._target.write(len(sealed).to_bytes(LENGTH, "big") + nonce + sealed)
        self._index += 1

    def close(self) -> None:
        if self.closed:
            return
        if self._buffer:
            self._seal(bytes(self._buffer))
            self._buffer.clear()
        self._seal(b"")  # end marker: a truncated archive has no valid empty chunk at the end
        super().close()


def _unseal(source: BinaryIO) -> Iterator[bytes]:
    """Plaintext chunks of a sealed archive, or BackupError when it was made with another key or is damaged."""
    if source.read(len(MAGIC)) != MAGIC:
        raise BackupError("not an encrypted RefExposer full backup")
    aes = AESGCM(_key())
    index = 0
    while True:
        header = source.read(LENGTH)
        if len(header) < LENGTH:
            raise BackupError("truncated backup: it stops before its end marker")
        size = int.from_bytes(header, "big")
        nonce = source.read(12)
        sealed = source.read(size)
        if len(nonce) < 12 or len(sealed) < size:
            raise BackupError("truncated backup: a chunk is incomplete")
        try:
            plain = aes.decrypt(nonce, sealed, MAGIC + index.to_bytes(8, "big"))
        except InvalidTag as e:
            raise BackupError("cannot decrypt the backup: it was made with another REFEX_SECRET_KEY, or is damaged") from e
        index += 1
        if not plain:  # end marker
            return
        yield plain


class _UnsealedReader(io.RawIOBase):
    """Read-only file-like over the plaintext of a sealed archive (what tarfile streams from)."""

    def __init__(self, source: BinaryIO):
        self._chunks = _unseal(source)
        self._buffer = b""
        self._done = False

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int:  # noqa: ANN401 - the signature of RawIOBase
        while not self._buffer and not self._done:
            try:
                self._buffer = next(self._chunks)
            except StopIteration:
                self._done = True
        take = min(len(buffer), len(self._buffer))
        buffer[:take] = self._buffer[:take]
        self._buffer = self._buffer[take:]
        return take


# --------------------------------------------------------------------------- what goes in

def _skip(relative: Path) -> bool:
    parts = relative.parts
    if any(p in SKIP_DIRS or p.startswith(RESTORE_PREFIX) for p in parts):
        return True
    if parts[-1] in SKIP_FILES or parts[-1].endswith(SKIP_SUFFIXES):
        return True
    # The SQLite database is dumped as JSON (database.json.gz): its file, and the WAL beside it, would restore
    # a state that does not match the dump
    return bool(re.match(r"^refexposer\.db(-wal|-shm)?$", parts[-1]))


def data_files(data_dir: Path, include_raw: bool = False) -> Iterator[tuple[Path, Path]]:
    """(absolute path, path inside the archive) of every file of data/ a backup carries."""
    root = data_dir.resolve()
    for current, dirs, names in os.walk(root):
        here = Path(current)
        relative_dir = here.relative_to(root)
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith(RESTORE_PREFIX)
                         and (include_raw or d != "raw"))
        if not include_raw and "raw" in relative_dir.parts:
            continue
        for name in sorted(names):
            relative = relative_dir / name
            if _skip(relative):
                continue
            path = here / name
            if path.is_symlink() or not path.is_file():
                continue
            yield path, Path("data") / relative


def alembic_revision(db: Session) -> str:
    try:
        return (db.execute(text("SELECT version_num FROM alembic_version")).scalar() or "").strip()
    except Exception:  # noqa: BLE001 - a database without the table is simply unknown
        return ""


def _value(v: Any) -> Any:
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray, memoryview)):
        import base64

        return {"__bytes__": base64.b64encode(bytes(v)).decode()}
    return v


def _tables() -> list[Any]:
    """Every table, in an order foreign keys accept (reversed for deletion), minus the ones never carried over."""
    return [t for t in Base.metadata.sorted_tables if t.name not in SKIP_TABLES]


def dump_database(db: Session) -> dict[str, Any]:
    """Every row of every table, as JSON-safe values."""
    tables = {t.name: [{k: _value(v) for k, v in row._mapping.items()} for row in db.execute(select(t))]
              for t in _tables()}
    return {"alembic_revision": alembic_revision(db), "dialect": db.get_bind().dialect.name,
            "tables": tables, "counts": {k: len(v) for k, v in tables.items()}}


# --------------------------------------------------------------------------- writing

@dataclass
class Written:
    path: Path
    size: int
    encrypted: bool
    manifest: dict[str, Any] = field(default_factory=dict)
    uploaded_to: str | None = None


def _add_bytes(tar: tarfile.TarFile, name: str, payload: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(payload)
    info.mtime = int(utcnow().timestamp())
    info.mode = 0o600
    tar.addfile(info, io.BytesIO(payload))


def write(db: Session, settings: Settings, encrypt: bool = True, include_raw: bool = False, keep: int = 4,
          log_line: Callable[[str], None] = lambda m: None) -> Written:
    """Write a full backup into data/.backups/full/ and return what was written.

    The free space is watched while writing: an archive that would fill the disk the database and the
    referentials share stops with a clear error instead (REFEX_MIN_FREE_DISK).
    """
    from .resources import DiskFull, DiskGuard

    folder = full_dir(settings.data_dir)
    folder.mkdir(parents=True, exist_ok=True)
    # The name carries the date down to the millisecond, so backups written in the same second do not share one
    # — which would silently replace the earlier file, here and in the bucket. Names still sort chronologically.
    now = datetime.now(timezone.utc)
    stamp, ms = now.strftime("%Y%m%d-%H%M%S"), now.microsecond // 1000
    suffix = ".tar.gz" + (".enc" if encrypt else "")
    for attempt in range(1000):
        target = folder / f"refexposer-full-{stamp}-{(ms + attempt) % 1000:03d}{suffix}"
        if not target.exists():
            break
    else:  # pragma: no cover - a thousand backups in the same second
        raise BackupError("cannot find a free name for the backup")
    name = target.name
    tmp = target.with_name(target.name + ".tmp")
    guard = DiskGuard(settings, folder, f"full backup {name}")
    guard.check()

    files = list(data_files(settings.data_dir, include_raw))
    config_files = sorted(p for p in settings.config_dir.glob("*.y*ml") if p.is_file()) if settings.config_dir.is_dir() else []
    database = dump_database(db)
    rows = sum(database["counts"].values())
    log_line(f"Full backup: {len(database['counts'])} table(s) / {rows:,} rows, {len(config_files)} definition file(s), "
             f"{len(files)} file(s) of data/ ({sum(p.stat().st_size for p, _ in files):,} bytes)"
             + ("" if include_raw else ", downloaded sources left out"))
    manifest = {
        "format": FORMAT,
        "version": 1,
        "app_version": __version__,
        "created_at": iso(utcnow()),
        "encrypted": encrypt,
        "include_raw": include_raw,
        "alembic_revision": database["alembic_revision"],
        "dialect": database["dialect"],
        "counts": database["counts"],
        "rows": rows,
        "config_files": [p.name for p in config_files],
        "data_files": len(files),
        "data_bytes": sum(p.stat().st_size for p, _ in files),
        "referentials": sorted({rel.parts[1] for _, rel in files if len(rel.parts) > 2 and not rel.parts[1].startswith(".")}),
    }
    try:
        with tmp.open("wb") as raw:
            sealed = _SealedWriter(raw) if encrypt else None
            stream: Any = io.BufferedWriter(sealed) if sealed is not None else raw
            try:
                with tarfile.open(fileobj=stream, mode="w|gz", compresslevel=6) as tar:
                    # First member: a restore reads it before anything else
                    _add_bytes(tar, "manifest.json", json.dumps(manifest, indent=2).encode())
                    _add_bytes(tar, "database.json.gz",
                               gzip.compress(json.dumps(database, separators=(",", ":")).encode(), compresslevel=6))
                    for p in config_files:
                        tar.add(p, arcname=f"config/{p.name}")
                    for path, relative in files:
                        try:
                            tar.add(path, arcname=relative.as_posix())
                        except (FileNotFoundError, PermissionError) as e:
                            # A file an update replaced while the backup was running: noted, not fatal
                            log_line(f"{relative.as_posix()} skipped ({type(e).__name__})")
                            continue
                        guard.wrote(path.stat().st_size)
            finally:
                if sealed is not None:
                    stream.flush()
                    sealed.close()
        os.replace(tmp, target)
    except DiskFull:
        tmp.unlink(missing_ok=True)
        raise
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    size = target.stat().st_size
    log_line(f"{name} written ({size:,} bytes, {'encrypted' if encrypt else 'NOT encrypted'})")
    for old in list_files(settings.data_dir)[keep:]:  # newest first
        (folder / old["name"]).unlink(missing_ok=True)
        log_line(f"{old['name']} deleted (only the last {keep} are kept)")
    return Written(path=target, size=size, encrypted=encrypt, manifest=manifest)


# --------------------------------------------------------------------------- listing

def list_files(data_dir: Path) -> list[dict[str, Any]]:
    folder = full_dir(data_dir)
    if not folder.is_dir():
        return []
    files = [p for p in folder.iterdir() if p.is_file() and NAME_RE.match(p.name)]
    return [{"name": p.name, "size": p.stat().st_size, "encrypted": p.name.endswith(".enc"),
             "created_at": iso(datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc))}
            for p in sorted(files, key=lambda p: p.name, reverse=True)]


def path_of(data_dir: Path, name: str) -> Path:
    if not NAME_RE.match(name):
        raise BackupError("invalid backup name")
    p = full_dir(data_dir) / name
    if not p.is_file():
        raise BackupError("unknown backup")
    return p


# --------------------------------------------------------------------------- the bucket copy

def _s3_key(prefix: str, name: str) -> str:
    return f"{prefix.rstrip('/') + '/' if prefix.strip('/') else ''}{name}"


def upload(cfg: Any, settings: Settings, written: Written, prefix: str, keep: int,
           log_line: Callable[[str], None] = lambda m: None) -> str:
    """Send a full backup to the bucket and drop the oldest ones over `keep`. Returns the object key."""
    from .s3 import S3Client

    key = _s3_key(prefix, written.path.name)
    with S3Client(cfg, settings) as client:
        client.upload(key, written.path)
        log_line(f"Copied to s3://{cfg.bucket}/{key} ({written.size:,} bytes)")
        extra = sorted((o for o in client.list(prefix.lstrip("/")) if NAME_RE.match(o.name)),
                       key=lambda o: o.name, reverse=True)[keep:]
        for obj in extra:
            client.delete(obj.key)
        if extra:
            log_line(f"{len(extra)} older backup(s) deleted from the bucket (only the last {keep} are kept)")
    written.uploaded_to = f"s3://{cfg.bucket}/{key}"
    return key


def list_remote(cfg: Any, settings: Settings, prefix: str) -> list[dict[str, Any]]:
    """Full backups held in the bucket, newest first (their name carries the date they were written)."""
    from .s3 import S3Client

    with S3Client(cfg, settings) as client:
        objects = [o for o in client.list(prefix.lstrip("/")) if NAME_RE.match(o.name)]
    return [{"name": o.name, "key": o.key, "size": o.size, "encrypted": o.name.endswith(".enc"),
             "last_modified": o.last_modified} for o in sorted(objects, key=lambda o: o.name, reverse=True)]


def download_remote(cfg: Any, settings: Settings, prefix: str, name: str,
                    log_line: Callable[[str], None] = lambda m: None) -> Path:
    """Bring a backup of the bucket back into data/.backups/full/ so it can be restored like a local one."""
    from .s3 import S3Client

    if not NAME_RE.match(name):
        raise BackupError("invalid backup name")
    folder = full_dir(settings.data_dir)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / name
    with S3Client(cfg, settings) as client:
        size = client.download(_s3_key(prefix, name), target)
    log_line(f"{name} downloaded from s3://{cfg.bucket} ({size:,} bytes)")
    return target


def delete_remote(cfg: Any, settings: Settings, prefix: str, name: str) -> None:
    from .s3 import S3Client

    if not NAME_RE.match(name):
        raise BackupError("invalid backup name")
    with S3Client(cfg, settings) as client:
        client.delete(_s3_key(prefix, name))


# --------------------------------------------------------------------------- reading

def _open(path: Path) -> tuple[Any, Any]:
    """(tar stream, underlying file) of an archive, sealed or not."""
    raw = path.open("rb")
    try:
        if path.name.endswith(".enc"):
            stream: Any = io.BufferedReader(_UnsealedReader(raw))
        else:
            stream = raw
        return tarfile.open(fileobj=stream, mode="r|gz"), raw
    except BackupError:
        raw.close()
        raise
    except tarfile.TarError as e:
        raw.close()
        raise BackupError("not a RefExposer full backup (unreadable archive)") from e


def read_manifest(path: Path) -> dict[str, Any]:
    """The manifest alone, without extracting anything: what the UI lists and what a restore checks."""
    tar, raw = _open(path)
    try:
        member = next((m for m in tar if m.name == "manifest.json"), None)
        if member is None:
            raise BackupError("not a RefExposer full backup (no manifest)")
        payload = tar.extractfile(member)
        data = json.loads((payload.read() if payload else b"") or b"{}")
    except (tarfile.TarError, ValueError) as e:
        raise BackupError("not a RefExposer full backup (unreadable manifest)") from e
    finally:
        tar.close()
        raw.close()
    if data.get("format") != FORMAT:
        raise BackupError("not a RefExposer full backup")
    return data


def _safe(name: str) -> bool:
    """A member path a restore accepts: relative, no '..', inside one of the folders a backup writes."""
    if name.startswith("/") or "\\" in name or ".." in Path(name).parts:
        return False
    return name in ("manifest.json", "database.json.gz") or name.startswith(("config/", "data/"))


# --------------------------------------------------------------------------- restoring

@dataclass
class Restored:
    created_at: str
    app_version: str
    counts: dict[str, int]
    referentials: list[str]
    config_files: list[str]
    data_files: int
    data_bytes: int
    removed: list[str] = field(default_factory=list)


def extract(path: Path, staging: Path, log_line: Callable[[str], None] = lambda m: None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Unpack an archive into `staging`. Returns (manifest, database dump).

    Nothing of the instance is touched here: the archive is read and checked first, so a damaged or foreign
    file fails before anything is replaced.
    """
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    manifest: dict[str, Any] = {}
    database: dict[str, Any] = {}
    files = 0
    tar, raw = _open(path)
    try:
        for member in tar:
            if not _safe(member.name):
                raise BackupError(f"the archive holds an unexpected path: {member.name}")
            if member.isdir():
                continue
            if not member.isfile():  # no symlink, device or hard link is ever restored
                raise BackupError(f"the archive holds something that is not a file: {member.name}")
            payload = tar.extractfile(member)
            if payload is None:
                continue
            if member.name == "manifest.json":
                manifest = json.loads(payload.read() or b"{}")
                if manifest.get("format") != FORMAT:
                    raise BackupError("not a RefExposer full backup")
                continue
            if member.name == "database.json.gz":
                database = json.loads(gzip.decompress(payload.read()))
                continue
            if not manifest:
                raise BackupError("not a RefExposer full backup (the manifest does not come first)")
            target = staging / member.name
            if not target.resolve().is_relative_to(staging.resolve()):
                raise BackupError(f"the archive holds a path that leaves the folder: {member.name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("wb") as f:
                shutil.copyfileobj(payload, f, CHUNK)
            files += 1
    except (tarfile.TarError, ValueError, OSError) as e:
        shutil.rmtree(staging, ignore_errors=True)
        raise BackupError(f"the archive cannot be read: {e}") from e
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    finally:
        tar.close()
        raw.close()
    if not manifest or not database:
        shutil.rmtree(staging, ignore_errors=True)
        raise BackupError("not a RefExposer full backup (manifest or database missing)")
    log_line(f"Archive read: {files} file(s) unpacked, backup of {manifest.get('created_at')} "
             f"(RefExposer {manifest.get('app_version')})")
    return manifest, database


def restore_database(db: Session, database: dict[str, Any]) -> dict[str, int]:
    """Replace every table by the dump, in one transaction. Sessions are emptied: everybody signs in again."""
    import base64

    revision = (database.get("alembic_revision") or "").strip()
    current = alembic_revision(db)
    if revision and current and revision != current:
        raise BackupError(
            f"the backup was made on another schema version (backup {revision}, instance {current}): restore it on an "
            "instance running the version it was made with, which brings the schema up to date by itself"
        )
    rows_by_table = database.get("tables")
    if not isinstance(rows_by_table, dict):
        raise BackupError("the backup holds no database dump")
    tables = _tables()
    db.execute(text("DELETE FROM sessions"))
    for t in reversed(tables):
        db.execute(delete(t))
    counts: dict[str, int] = {}
    for t in tables:
        dates = {c.name for c in t.columns if isinstance(c.type, DateTime)}
        rows = []
        for r in rows_by_table.get(t.name, []):
            row = {}
            for k, v in r.items():
                if k not in t.c:
                    continue
                if isinstance(v, dict) and "__bytes__" in v:
                    v = base64.b64decode(v["__bytes__"])
                elif k in dates and isinstance(v, str):
                    v = datetime.fromisoformat(v)
                row[k] = v
            rows.append(row)
        for i in range(0, len(rows), 1000):  # a dump of an audit log holds a great many rows
            db.execute(insert(t), rows[i:i + 1000])
        counts[t.name] = len(rows)
    if db.get_bind().dialect.name == "postgresql":  # explicit ids were inserted: move the sequences after them
        for t in tables:
            if "id" in t.c and isinstance(t.c.id.type, Integer):
                db.execute(text(f"SELECT setval(pg_get_serial_sequence('{t.name}', 'id'), "
                                f"COALESCE((SELECT MAX(id) FROM {t.name}), 0) + 1, false)"))
    db.commit()
    return counts


def restore(db: Session, settings: Settings, path: Path, remove_extra: bool = False,
            log_line: Callable[[str], None] = lambda m: None) -> Restored:
    """Put the instance back as the archive holds it: database, definitions and data/.

    `remove_extra`: also delete the folders of referentials the backup does not carry, so that the instance
    matches the archive exactly. Off by default, so a restore adds back what was lost without throwing away a
    referential created since.

    Each referential folder is swapped in place: the new folder is moved in only once it is complete, and the
    one it replaces is kept aside. Should a move fail halfway, every folder already swapped is put back before
    the error is raised, so a failed restore leaves the data it found rather than a half-replaced `data/`.
    """
    staging = settings.data_dir / f".restore-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    aside = staging.with_name(staging.name + "-replaced")
    manifest, database = extract(path, staging, log_line)
    swapped: list[tuple[Path, Path]] = []  # (folder put aside, where it came from)
    try:
        counts = restore_database(db, database)
        log_line(f"Database restored: {sum(counts.values()):,} rows in {len(counts)} table(s)")

        if (staging / "config").is_dir() and settings.config_dir.is_dir():
            carried_files = set()
            for p in sorted((staging / "config").iterdir()):
                if p.is_file():
                    shutil.copyfile(p, settings.config_dir / p.name)
                    carried_files.add(p.name)
            log_line(f"{len(carried_files)} definition file(s) restored into {settings.config_dir}")
            if remove_extra:
                # A YAML file the backup does not carry would otherwise bring its referentials back at the
                # next load, which is not what "match the archive exactly" means
                stale = [p for p in sorted(settings.config_dir.glob("*.y*ml")) if p.name not in carried_files]
                for p in stale:
                    p.unlink(missing_ok=True)
                if stale:
                    log_line(f"{len(stale)} definition file(s) absent from the backup deleted: "
                             f"{', '.join(p.name for p in stale)}")

        aside.mkdir(parents=True, exist_ok=True)
        source_root = staging / "data"
        restored_entries = sorted(p.name for p in source_root.iterdir()) if source_root.is_dir() else []
        removed: list[str] = []
        try:
            for name in restored_entries:
                incoming, live = source_root / name, settings.data_dir / name
                if incoming.is_dir():
                    if live.exists():
                        live.replace(aside / name)
                        swapped.append((aside / name, live))
                    incoming.replace(live)
                else:
                    shutil.copyfile(incoming, live)
            if remove_extra:
                carried = set(restored_entries)
                for child in sorted(settings.data_dir.iterdir()):
                    if child.is_dir() and not child.name.startswith(".") and child.name not in carried:
                        child.replace(aside / child.name)
                        swapped.append((aside / child.name, child))
                        removed.append(child.name)
                if removed:
                    log_line(f"Folders of {len(removed)} referential(s) absent from the backup removed: "
                             f"{', '.join(removed[:20])}")
        except BaseException:
            for kept, live in reversed(swapped):
                shutil.rmtree(live, ignore_errors=True)  # whatever the failed restore put there
                try:
                    kept.replace(live)
                except OSError:  # pragma: no cover - the folder stays in the .restore-*-replaced folder
                    log.error("Cannot put %s back: it is in %s", live.name, aside)
            swapped.clear()
            raise
        log_line(f"{len(restored_entries)} entry(ies) of data/ restored ({manifest.get('data_bytes', 0):,} bytes)")
        return Restored(created_at=manifest.get("created_at") or "", app_version=manifest.get("app_version") or "",
                        counts=counts, referentials=manifest.get("referentials") or [],
                        config_files=manifest.get("config_files") or [], data_files=manifest.get("data_files") or 0,
                        data_bytes=manifest.get("data_bytes") or 0, removed=removed)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        # The replaced folders are only dropped once the restore went through (a rollback emptied this list)
        shutil.rmtree(aside, ignore_errors=True)
