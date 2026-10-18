"""Disk space seen by the administrators: free space of each volume, and what takes the space in data/.

Read only, by design: nothing here deletes or moves a file. The walk stays under the folders of the instance (data,
import, sync, temporary files), never follows a symbolic link, and counts a file hard-linked twice once (a Parquet
source published as is is the same file as the published version). Paths are shown relative to their folder. The
result is cached for CACHE_SECONDS and computed by one request at a time: reloading the page cannot load the server.
"""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .resources import min_free_disk
from .storage import iso, utcnow

if TYPE_CHECKING:
    from .service import Service

CACHE_SECONDS = 30
MIN_REFRESH_SECONDS = 5  # a forced refresh walks the folders again at most this often
MAX_ENTRIES = 2_000_000  # beyond, the walk stops and the figures are marked as partial
LARGEST_FILES = 20

# What a file of data/<id>/ is, by its place (first match wins: a hard link is counted under the first one)
CATEGORIES = ("published", "indexes", "previous", "base", "sources", "downloading", "work", "manual", "exports", "other")
LABELS = {
    "published": "Published version",
    "indexes": "Indexes (sorted copies)",
    "previous": "Previous version (rollback)",
    "base": "Kept database (SQLite deltas)",
    "sources": "Source files kept",
    "downloading": "Downloads in progress or interrupted",
    "work": "Build in progress",
    "manual": "Imported files kept",
    "exports": "Generated downloads (cache)",
    "other": "History and metadata",
}

_lock = threading.Lock()
_cache: tuple[float, dict[str, Any]] | None = None


def _category(rel: tuple[str, ...]) -> str:
    name, top = rel[-1], rel[0]
    if len(rel) == 1:
        if name.startswith("current.by_"):
            return "indexes"
        if name.startswith("current."):
            return "published"
        if name.startswith("previous."):
            return "previous"
        return "other"
    if top == "raw":
        return "downloading" if len(rel) > 1 and rel[1] in (".partial", ".incoming") else "sources"
    return {"base": "base", ".work": "work", "manual": "manual", "exports": "exports"}.get(top, "other")


class _Walk:
    def __init__(self) -> None:
        self.seen: set[tuple[int, int]] = set()
        self.entries = 0
        self.partial = False
        self.largest: list[tuple[int, str]] = []

    def files(self, root: Path):
        """(relative parts, size) of the regular files under `root`: no symbolic link followed, hard links once."""
        stack = [(root, ())]
        while stack:
            folder, rel = stack.pop()
            try:
                it = os.scandir(folder)
            except OSError:
                continue
            with it:
                for e in it:
                    self.entries += 1
                    if self.entries > MAX_ENTRIES:
                        self.partial = True
                        return
                    try:
                        st = e.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if stat.S_ISDIR(st.st_mode):
                        stack.append((Path(e.path), (*rel, e.name)))
                    elif stat.S_ISREG(st.st_mode):
                        key = (st.st_dev, st.st_ino)
                        if st.st_nlink > 1:
                            if key in self.seen:
                                continue
                            self.seen.add(key)
                        size = getattr(st, "st_blocks", 0) * 512 or st.st_size  # space used (sparse files)
                        yield (*rel, e.name), size

    def note(self, label: str, size: int) -> None:
        if size >= (1 << 20):
            self.largest.append((size, label))
            if len(self.largest) > LARGEST_FILES * 4:
                self.largest = sorted(self.largest, reverse=True)[:LARGEST_FILES]


def _volume(name: str, path: Path | None, reserve: int) -> dict[str, Any] | None:
    if path is None:
        return None
    p = Path(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    try:
        du = shutil.disk_usage(p)
        dev = p.stat().st_dev
    except OSError:
        return None
    free_pct = du.free / du.total * 100 if du.total else 100.0
    level = "critical" if du.free < reserve * 2 else "warning" if free_pct < 15 else "ok"
    return {"name": name, "path": str(path), "total": du.total, "used": du.used, "free": du.free, "dev": dev,
            "free_pct": round(free_pct, 1), "level": level}


def _compute(service: "Service") -> dict[str, Any]:
    started = time.monotonic()
    settings = service.settings
    data = Path(settings.data_dir)
    reserve = min_free_disk(settings)
    walk = _Walk()
    refs: list[dict[str, Any]] = []
    system: dict[str, int] = {}
    totals = dict.fromkeys(CATEGORIES, 0)
    try:
        children = sorted(os.scandir(data), key=lambda e: e.name)
    except OSError:
        children = []
    for child in children:
        try:
            st = child.stat(follow_symlinks=False)
        except OSError:
            continue
        if not stat.S_ISDIR(st.st_mode):
            if stat.S_ISREG(st.st_mode):
                system["files at the root"] = system.get("files at the root", 0) + st.st_size
            continue
        if child.name.startswith("."):  # .system (backups, history), .tmp (uploads, previews)...
            size = 0
            for rel, n in walk.files(Path(child.path)):
                size += n
                walk.note(f"data/{child.name}/{'/'.join(rel)}", n)
            system[f"data/{child.name}"] = size
            continue
        ref = service.refs.get(child.name)
        meta = service.metas.get(child.name) or {}
        cats = dict.fromkeys(CATEGORIES, 0)
        for rel, n in walk.files(Path(child.path)):
            c = _category(rel)
            cats[c] += n
            walk.note(f"data/{child.name}/{'/'.join(rel)}", n)
        for c, n in cats.items():
            totals[c] += n
        refs.append({
            "id": child.name,
            "name": ref.name if ref else None,
            "kind": ("bloom" if ref.is_bloom else "mmdb" if ref.is_mmdb else "internal" if ref.is_internal else "table") if ref else None,
            "orphan": ref is None,  # data left by a deleted referential
            "rows": meta.get("row_count"),
            "status": meta.get("status"),
            "total": sum(cats.values()),
            "categories": {c: n for c, n in cats.items() if n},
            "keep_raw": ref.storage.keep_raw if ref else None,
            "keep_previous": ref.storage.keep_previous if ref else None,
        })
    # Folders outside data/ (other volumes, or the memory of the container for /tmp)
    extra: dict[str, int] = {}
    for label, folder in (("import", settings.import_dir), ("sync", settings.sync_dir),
                          ("temporary (/tmp)", Path(tempfile.gettempdir()))):
        if folder and Path(folder).exists() and not str(Path(folder).resolve()).startswith(str(data.resolve())):
            extra[label] = sum(n for _, n in walk.files(Path(folder)))
    volumes, devs = [], set()
    for name, path in (("data", data), ("import", settings.import_dir), ("sync", settings.sync_dir),
                       ("temporary (/tmp)", Path(tempfile.gettempdir()))):
        v = _volume(name, path, reserve)
        if v and v["dev"] not in devs:  # one entry per file system
            devs.add(v["dev"])
            volumes.append(v)
        elif v:
            next(x for x in volumes if x["dev"] == v["dev"])["name"] += f", {name}"
    for v in volumes:
        v.pop("dev", None)
    refs.sort(key=lambda r: r["total"], reverse=True)
    return {
        "computed_at": iso(utcnow()),
        "duration": round(time.monotonic() - started, 2),
        "partial": walk.partial,
        "reserve": reserve,
        "volumes": volumes,
        "categories": [{"id": c, "label": LABELS[c], "size": totals[c]} for c in CATEGORIES if totals[c]],
        "referentials": refs,
        "system": [{"name": k, "size": v} for k, v in sorted(system.items(), key=lambda kv: -kv[1])],
        "outside": [{"name": k, "size": v} for k, v in extra.items()],
        "largest_files": [{"path": p, "size": n} for n, p in sorted(walk.largest, reverse=True)[:LARGEST_FILES]],
        "data_total": sum(r["total"] for r in refs) + sum(system.values()),
    }


def report(service: "Service", refresh: bool = False) -> dict[str, Any]:
    """Cached for CACHE_SECONDS; one computation at a time (the others wait for it and get its result)."""
    global _cache
    with _lock:
        age = time.monotonic() - _cache[0] if _cache else None
        if age is not None and (age < MIN_REFRESH_SECONDS or (not refresh and age < CACHE_SECONDS)):
            return _cache[1]
        result = _compute(service)
        _cache = (time.monotonic(), result)
        return result
