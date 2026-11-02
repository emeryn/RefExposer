"""Remote storage of a referential's published data: an S3 bucket in place of the local disk.

Why there is a cache at all. Everything that serves a referential reads a **local file**: DuckDB over its
Parquet file and the sorted copies, the Bloom filter and the MaxMind DB through their own readers, and the SQL
console runs in a sandbox with network access switched off on purpose. So a referential "stored remotely" is
one whose home is the bucket and whose local files are a cache of it:

- after each successful update, the published files are sent to the bucket (`push`);
- a referential nobody has read for a while is **freed**: its files leave the disk and its table stops being
  exposed, while `meta.json` keeps the list of what the bucket holds (`free`);
- opening it brings everything back and exposes it again (`bring_back`), which the API does by itself the
  first time someone asks for its data.

A whole referential is freed or brought back, never half of one: a half-cached referential would mean every
query path having to wonder which of its files is there, and the first wrong answer would be a wrong result
rather than a slow one.

What the bucket holds, under `<prefix>/<referential id>/`: `current.parquet`, `previous.parquet`, the sorted
copies `current.by_*.parquet`, `current.bloom` / `previous.bloom`, `current.mmdb` / `previous.mmdb`, and the
kept versions of `.snapshots/`. What it never holds: `meta.json`, `runs.jsonl` and the downloaded sources of
`raw/` — the first two are what tells a freed referential apart from an empty one, and the sources come from
their own source again.

A confidential referential is sent as it is, which is to say encrypted (Parquet Modular Encryption): the bucket
never sees its rows, and the key stays on the instance.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from sqlalchemy.orm import Session

from .config import ReferentialConfig
from .settings import Settings
from .storage import RefPaths, dir_size, iso, read_meta, utcnow, write_meta

log = logging.getLogger(__name__)

# Published files a referential's data is made of, in the order a restore wants them
PUBLISHED = ("current.parquet", "previous.parquet", "current.bloom", "previous.bloom", "current.mmdb", "previous.mmdb")
SNAPSHOTS = ".snapshots"


class RemoteStorageError(ValueError):
    """Remote storage is not configured, or the bucket cannot hold what was asked of it."""


@dataclass
class Target:
    """The bucket of this instance, and how much disk its cache may use."""

    cfg: Any  # S3Config
    prefix: str
    cache_bytes: int
    keep_hours: int

    def key(self, ref_id: str, relative: str) -> str:
        base = self.prefix.rstrip("/") + "/" if self.prefix.strip("/") else ""
        return f"{base}{ref_id}/{relative}"

    def folder(self, ref_id: str) -> str:
        """Prefix holding one referential, trailing slash included: without it, listing 'places' would also
        return the objects of 'places2' — and deleting what the listing returned would take them with it."""
        return self.key(ref_id, "")

    def describe(self) -> str:
        return self.cfg.describe()


def target(db: Session, required: bool = True) -> Target | None:
    """The configured bucket, or None when remote storage is off."""
    from . import appsettings

    section: Any = appsettings.load(db, "storage")
    if not section.enabled:
        if required:
            raise RemoteStorageError("remote storage is not enabled (Settings › Storage)")
        return None
    return Target(cfg=section.s3, prefix=section.prefix, cache_bytes=section.cache_bytes,
                  keep_hours=section.keep_hours)


def is_remote(ref: ReferentialConfig) -> bool:
    return getattr(ref.storage, "location", "local") == "remote"


# --------------------------------------------------------------------------- what makes up a referential

def local_files(settings: Settings, ref_id: str) -> list[tuple[Path, str]]:
    """(file, name inside the bucket) of everything published for a referential that is on the disk."""
    paths = RefPaths(settings.data_dir, ref_id)
    out: list[tuple[Path, str]] = []
    for name in PUBLISHED:
        p = paths.root / name
        if p.is_file():
            out.append((p, name))
    out.extend((p, p.name) for p in paths.index_files())
    kept = paths.root / SNAPSHOTS
    if kept.is_dir():
        out.extend((p, f"{SNAPSHOTS}/{p.name}") for p in sorted(kept.iterdir()) if p.is_file())
    return out


def recorded(meta: dict[str, Any]) -> dict[str, Any]:
    """What `meta.json` says the bucket holds: {name: {"size": n, "etag": "..."}}."""
    remote = meta.get("remote") or {}
    objects = remote.get("objects")
    return objects if isinstance(objects, dict) else {}


def freed(meta: dict[str, Any]) -> bool:
    """True when `meta.json` says the data was freed from the disk (and so has to be brought back to serve it)."""
    return bool((meta.get("remote") or {}).get("freed"))


def cached_bytes(settings: Settings, refs: dict[str, ReferentialConfig]) -> int:
    """Disk used by the local copies of the referentials stored remotely."""
    total = 0
    for ref in refs.values():
        if is_remote(ref):
            total += sum(p.stat().st_size for p, _ in local_files(settings, ref.id))
    return total


# --------------------------------------------------------------------------- sending

def push(settings: Settings, tgt: Target, ref: ReferentialConfig, meta: dict[str, Any],
         log_line: Callable[[str], None] = lambda m: None) -> dict[str, Any]:
    """Send to the bucket what it does not already hold, and drop what the referential no longer has.

    Returns the `remote` block to write into `meta.json`. A file the bucket already holds with the same size,
    and which has not been written since the last push, is left alone: publishing a version again does not send
    the kept versions and the untouched indexes a second time. The written-since check matters — two versions of
    the same data can weigh exactly the same, and comparing sizes alone would then keep the older one.
    """
    from datetime import datetime, timezone
    from .s3 import S3Client, S3Error

    files = local_files(settings, ref.id)
    if not files:
        raise RemoteStorageError(f"[{ref.id}] nothing published yet: nothing to send")
    pushed_at = _parse(((meta.get("remote") or {}).get("at")))
    objects: dict[str, Any] = {}
    sent = skipped = 0
    sent_bytes = 0
    with S3Client(tgt.cfg, settings) as client:
        held = {o.key: o for o in client.list(tgt.folder(ref.id))}
        for path, name in files:
            key = tgt.key(ref.id, name)
            stat = path.stat()
            size = stat.st_size
            known = held.get(key)
            written = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
            if known is not None and known.size == size and pushed_at is not None and written <= pushed_at:
                objects[name] = {"size": size, "etag": known.etag}
                skipped += 1
                continue
            etag = client.upload(key, path)
            objects[name] = {"size": size, "etag": etag}
            sent += 1
            sent_bytes += size
        # Files the referential no longer has (a version replaced, a kept version dropped, an index removed)
        extra = [key for key in held if key not in {tgt.key(ref.id, n) for _, n in files}]
        for key in extra:
            try:
                client.delete(key)
            except S3Error as e:  # pragma: no cover - the copy is right even if the old object stays
                log.warning("[%s] %s not deleted from the bucket: %s", ref.id, key, e)
    log_line(f"Sent to {tgt.describe()}: {sent} file(s) ({sent_bytes:,} bytes)"
             + (f", {skipped} already there" if skipped else "")
             + (f", {len(extra)} removed" if extra else ""))
    return {"bucket": tgt.cfg.bucket, "endpoint": tgt.cfg.endpoint, "prefix": tgt.prefix,
            "at": iso(utcnow()), "objects": objects, "bytes": sum(o["size"] for o in objects.values()),
            "last_sent": sent, "freed": False}


def _parse(value: Any) -> Any:
    from .storage import parse_iso

    return parse_iso(value) if isinstance(value, str) else None


# --------------------------------------------------------------------------- freeing and bringing back

def check_freeable(settings: Settings, ref: ReferentialConfig, meta: dict[str, Any]) -> list[tuple[Path, str]]:
    """The files that may be freed, or RemoteStorageError saying why not.

    Every file on the disk has to be in the bucket, with the same size: freeing a file nobody else has is not
    freeing disk space, it is losing data. Asked before anything is dropped, and before the table stops being
    exposed, so a refusal leaves the referential exactly as it was.
    """
    objects = recorded(meta)
    files = local_files(settings, ref.id)
    if not files:
        raise RemoteStorageError(f"[{ref.id}] nothing on the disk to free")
    missing = [name for _, name in files if name not in objects]
    if missing:
        raise RemoteStorageError(f"[{ref.id}] the bucket does not hold {', '.join(missing[:5])}: send the data "
                                 "to the bucket first")
    mismatched = [name for path, name in files if objects[name].get("size") != path.stat().st_size]
    if mismatched:
        raise RemoteStorageError(f"[{ref.id}] what the bucket holds for {', '.join(mismatched[:5])} is not what is "
                                 "on the disk: send the data to the bucket again first")
    return files


def free(settings: Settings, ref: ReferentialConfig, meta: dict[str, Any],
         log_line: Callable[[str], None] = lambda m: None) -> dict[str, Any]:
    """Drop the local copies of a referential the bucket holds. The data stays in the bucket."""
    objects = recorded(meta)
    files = check_freeable(settings, ref, meta)
    released = 0
    for path, _ in files:
        released += path.stat().st_size
        path.unlink(missing_ok=True)
    kept = RefPaths(settings.data_dir, ref.id).root / SNAPSHOTS
    if kept.is_dir() and not any(kept.iterdir()):
        kept.rmdir()
    remote = {**(meta.get("remote") or {}), "freed": True, "freed_at": iso(utcnow()), "objects": objects}
    log_line(f"[{ref.id}] {released:,} bytes freed from the disk ({len(files)} file(s) kept in "
             f"{remote.get('bucket') or 'the bucket'})")
    return {"remote": remote, "released": released, "files": len(files)}


def bring_back(settings: Settings, tgt: Target, ref: ReferentialConfig, meta: dict[str, Any],
               log_line: Callable[[str], None] = lambda m: None) -> dict[str, Any]:
    """Download everything the bucket holds for a referential, so it can be served again.

    The files land through a `.part` file each (see s3.py), so an interrupted download never leaves a file that
    looks published. Should one fail, those already there are kept: the download is simply run again.
    """
    from .s3 import S3Client

    objects = recorded(meta)
    if not objects:
        raise RemoteStorageError(f"[{ref.id}] the bucket holds nothing for this referential")
    paths = RefPaths(settings.data_dir, ref.id)
    paths.root.mkdir(parents=True, exist_ok=True)
    downloaded = 0
    files = 0
    with S3Client(tgt.cfg, settings) as client:
        for name, info in sorted(objects.items()):
            dest = paths.root / name
            if dest.is_file() and dest.stat().st_size == info.get("size"):
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            downloaded += client.download(tgt.key(ref.id, name), dest)
            files += 1
    remote = {**(meta.get("remote") or {}), "freed": False, "brought_back_at": iso(utcnow())}
    log_line(f"[{ref.id}] {files} file(s) downloaded from {tgt.describe()} ({downloaded:,} bytes)")
    return {"remote": remote, "downloaded": downloaded, "files": files}


def forget(settings: Settings, tgt: Target, ref_id: str) -> int:
    """Delete everything the bucket holds for a referential (it is being deleted). Returns the object count."""
    from .s3 import S3Client

    with S3Client(tgt.cfg, settings) as client:
        keys = [o.key for o in client.list(tgt.folder(ref_id))]
        for key in keys:
            client.delete(key)
    return len(keys)


# --------------------------------------------------------------------------- what the task does

def candidates(settings: Settings, refs: dict[str, ReferentialConfig], last_read: dict[str, float],
               tgt: Target, exclude: set[str] = frozenset()) -> list[tuple[str, int, float]]:
    """(referential, bytes on the disk, when it was last read) of what may be freed, coldest first.

    Left out: a referential being updated, one read within `keep_hours`, and one whose data the bucket does
    not hold in full — freeing that one would lose it.
    """
    import time

    now = time.time()
    out: list[tuple[str, int, float]] = []
    for ref in refs.values():
        if not is_remote(ref) or ref.id in exclude:
            continue
        paths = RefPaths(settings.data_dir, ref.id)
        meta = read_meta(paths)
        if freed(meta):
            continue
        try:
            files = check_freeable(settings, ref, meta)
        except RemoteStorageError:  # the bucket does not hold it all: it is not a candidate
            continue
        read_at = last_read.get(ref.id) or max((p.stat().st_mtime for p, _ in files), default=0.0)
        if tgt.keep_hours and now - read_at < tgt.keep_hours * 3600:
            continue
        out.append((ref.id, sum(p.stat().st_size for p, _ in files), read_at))
    return sorted(out, key=lambda row: row[2])


def apply_quota(settings: Settings, tgt: Target, refs: dict[str, ReferentialConfig], last_read: dict[str, float],
                free_one: Callable[[str], int], exclude: set[str] = frozenset(),
                log_line: Callable[[str], None] = lambda m: None) -> dict[str, Any]:
    """Free the coldest referentials until the cache fits in `cache_bytes`. 0: nothing is ever freed.

    `free_one` is given by the service (it is what also stops exposing the table) and returns the bytes freed.
    """
    if not tgt.cache_bytes:
        return {"freed": [], "released": 0, "cached": cached_bytes(settings, refs)}
    cached = cached_bytes(settings, refs)
    if cached <= tgt.cache_bytes:
        return {"freed": [], "released": 0, "cached": cached}
    released = 0
    done: list[str] = []
    for ref_id, size, _ in candidates(settings, refs, last_read, tgt, exclude):
        if cached - released <= tgt.cache_bytes:
            break
        try:
            released += free_one(ref_id)
            done.append(ref_id)
        except (RemoteStorageError, OSError) as e:
            log.warning("[%s] not freed: %s", ref_id, e)
    if done:
        log_line(f"Local cache over its limit: {len(done)} referential(s) freed ({released:,} bytes) — "
                 f"{', '.join(done[:10])}")
    elif cached > tgt.cache_bytes:
        log_line(f"Local cache over its limit ({cached:,} / {tgt.cache_bytes:,} bytes) but nothing can be freed "
                 "yet: what is cached was read recently, or the bucket does not hold it all")
    return {"freed": done, "released": released, "cached": cached - released}


def usage(settings: Settings, refs: dict[str, ReferentialConfig]) -> dict[str, Any]:
    """What the interface shows: how many referentials are stored remotely, cached, or freed."""
    remote = [ref for ref in refs.values() if is_remote(ref)]
    cached = []
    away = []
    for ref in remote:
        meta = read_meta(RefPaths(settings.data_dir, ref.id))
        (away if freed(meta) else cached).append(ref.id)
    return {"referentials": sorted(r.id for r in remote), "cached": sorted(cached), "freed": sorted(away),
            "cached_bytes": cached_bytes(settings, refs)}


def update_meta(settings: Settings, ref_id: str, remote: dict[str, Any]) -> dict[str, Any]:
    """Write the `remote` block of a referential's meta.json, keeping the rest as it is."""
    paths = RefPaths(settings.data_dir, ref_id)
    meta = read_meta(paths)
    meta["remote"] = remote
    meta["storage_size"] = dir_size(paths.root)
    write_meta(paths, meta)
    return meta
