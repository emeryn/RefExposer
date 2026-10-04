"""Update pipeline of a referential: download -> decompress -> transform -> validate -> publish."""

from __future__ import annotations

import fnmatch
import gzip
import hashlib
import logging
import os
import re
import shutil
import tarfile
import time
import uuid
import zipfile
import zlib
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlparse

import duckdb
import httpx
import orjson

from .bloom import BloomError, BloomFilter
from .config import ReferentialConfig
from .crypto import encrypt_data, parquet_key
from .settings import Settings
from .sqlbuild import build_source_sql, lit, parquet_reader, qi
from .storage import RefPaths, dir_size, iso, read_meta, utcnow, write_meta

log = logging.getLogger(__name__)


class RunCancelled(Exception):
    pass


class RunRejected(Exception):
    """The new version failed validation; the previous one is kept."""


class SourceCorrupted(RunRejected):
    """The source itself is unusable (empty, error page, unreadable archive...): nothing is replaced."""


# Text returned by servers instead of the expected data (error pages, captive portals...)
_ERROR_PAGE = re.compile(
    rb"^\s*(<!doctype html|<html|<\?xml[^>]*>\s*<html|\d{3}:? ?(not found|forbidden|unauthorized|bad gateway|internal server error|service unavailable)"
    rb"|(not found|access denied|forbidden|rate limit exceeded)\s*$)",
    re.IGNORECASE,
)
_MAGIC = {"parquet": (b"PAR1",), "xlsx": (b"PK\x03\x04",), "bloom": None, "mmdb": None}


def check_source_file(path: Path, fmt: str, label: str | None = None) -> None:
    """Refuse files that cannot be a valid source: empty, error page, wrong signature."""
    label = label or path.name
    size = path.stat().st_size
    if size == 0:
        raise SourceCorrupted(f"corrupted source: '{label}' is empty (0 bytes)")
    try:
        with (gzip.open(path, "rb") if path.name.lower().endswith(".gz") else path.open("rb")) as f:
            head = f.read(4096)
    except (OSError, EOFError, zlib.error) as e:
        raise SourceCorrupted(f"corrupted source: unreadable archive '{label}' ({e})") from e
    if not head:
        raise SourceCorrupted(f"corrupted source: '{label}' is empty once decompressed")
    stripped = head.lstrip(b"\xef\xbb\xbf \t\r\n")
    if not stripped:
        raise SourceCorrupted(f"corrupted source: '{label}' only contains whitespace")
    if fmt in ("parquet", "xlsx") and not any(head.startswith(m) for m in _MAGIC[fmt]):
        raise SourceCorrupted(f"corrupted source: '{label}' does not have the signature of a {fmt} file")
    if head.startswith(b"version https://git-lfs.github.com/spec/"):
        raise SourceCorrupted(f"corrupted source: '{label}' is a Git LFS pointer, not the data (Git LFS files are not supported: "
                              "publish the file as a release asset and use its URL)")
    if fmt in ("parquet", "xlsx", "bloom", "mmdb"):  # binary formats: checked when published
        return
    if _ERROR_PAGE.match(stripped[:400]):
        excerpt = stripped[:80].decode("utf-8", "replace").strip().replace("\n", " ")
        raise SourceCorrupted(f"corrupted source: '{label}' contains an error page instead of data ('{excerpt}')")
    if fmt == "json" and stripped[:1] not in (b"{", b"["):
        excerpt = stripped[:60].decode("utf-8", "replace").strip()
        raise SourceCorrupted(f"corrupted source: '{label}' is not a JSON document (starts with '{excerpt}')")
    if fmt == "jsonl" and stripped[:1] != b"{":
        raise SourceCorrupted(f"corrupted source: '{label}' is not JSON Lines")
    if fmt == "xml" and stripped[:1] != b"<":
        raise SourceCorrupted(f"corrupted source: '{label}' is not an XML document")


class Run:
    """A single update run with live progress, serialized into runs.jsonl at the end."""

    def __init__(self, ref_id: str, trigger: str, force: bool = False, user: str | None = None):
        self.id = uuid.uuid4().hex[:12]
        self.ref_id = ref_id
        self.trigger = trigger
        self.force = force
        self.user = user
        self.status = "queued"
        self.phase = "queued"
        self.queued_at = utcnow()
        self.started_at = None
        self.finished_at = None
        self.message: str | None = None
        self.logs: list[dict[str, Any]] = []
        self.bytes_done = 0
        self.bytes_total = 0
        self.stats: dict[str, Any] = {}

    def log(self, msg: str, level: str = "info") -> None:
        self.logs.append({"t": iso(utcnow()), "level": level, "msg": msg})
        getattr(log, "warning" if level == "warn" else level, log.info)("[%s] %s", self.ref_id, msg)

    def to_dict(self, with_logs: bool = True) -> dict[str, Any]:
        duration = None
        if self.started_at:
            duration = round(((self.finished_at or utcnow()) - self.started_at).total_seconds(), 2)
        d = {
            "id": self.id,
            "ref_id": self.ref_id,
            "trigger": self.trigger,
            "user": self.user,
            "force": self.force,
            "status": self.status,
            "phase": self.phase,
            "queued_at": iso(self.queued_at),
            "started_at": iso(self.started_at),
            "finished_at": iso(self.finished_at),
            "duration": duration,
            "message": self.message,
            "progress": {"done": self.bytes_done, "total": self.bytes_total},
            **self.stats,
        }
        if with_logs:
            d["logs"] = self.logs
        return d


# --------------------------------------------------------------------------- download

def _filename(resp: httpx.Response, url: str) -> str:
    cd = resp.headers.get("content-disposition", "")
    m = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)\"?", cd)
    name = unquote(m.group(1)) if m else unquote(Path(urlparse(str(resp.url)).path).name)
    if not name:
        name = unquote(Path(urlparse(url).path).name) or "download"
    return re.sub(r"[^\w.\-]+", "_", name)


def _safe_member(base: Path, name: str) -> Path:
    target = (base / name).resolve()
    if not str(target).startswith(str(base.resolve())):
        raise ValueError(f"unsafe archive path: {name}")
    return target


# Line-based formats DuckDB reads straight from .gz files: those are kept compressed (no huge decompressed copy)
STREAMABLE_GZIP = ("csv", "tsv", "txt", "jsonl")


def keeps_gzip(path: Path, fmt: str | None) -> bool:
    name = path.name.lower()
    return fmt in STREAMABLE_GZIP and name.endswith(".gz") and not name.endswith(".tar.gz")


def unpack(path: Path, dest: Path, pattern: str | None, run: Run, fmt: str | None = None) -> list[Path]:
    """Decompress gz / zip / tar archives, returning the produced files.

    A .gz file of a line-based format (csv, tsv, txt, jsonl) is kept as is: DuckDB reads it while decompressing."""
    if keeps_gzip(path, fmt):
        run.log(f"{path.name}: kept compressed, read directly by the import")
        return [path]
    try:
        return _unpack(path, dest, pattern, run)
    except (OSError, EOFError, zipfile.BadZipFile, tarfile.TarError, zlib.error) as e:
        raise SourceCorrupted(f"corrupted source: unreadable archive '{path.name}' ({e})") from e


def _unpack(path: Path, dest: Path, pattern: str | None, run: Run) -> list[Path]:
    name = path.name.lower()
    if name.endswith((".tar.gz", ".tgz", ".tar")):
        out_dir = dest / re.sub(r"(\.tar\.gz|\.tgz|\.tar)$", "", path.name, flags=re.I)
        shutil.rmtree(out_dir, ignore_errors=True)
        out_dir.mkdir(parents=True)
        files = []
        with tarfile.open(path) as tar:
            for m in tar.getmembers():
                if not m.isfile() or (pattern and not fnmatch.fnmatch(m.name, pattern)):
                    continue
                target = _safe_member(out_dir, m.name)
                target.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(m) as src, target.open("wb") as dst:  # type: ignore[union-attr]
                    shutil.copyfileobj(src, dst, 1 << 20)
                files.append(target)
        path.unlink()
        run.log(f"Tar archive extracted: {len(files)} file(s)")
        return sorted(files)
    if name.endswith(".gz"):
        target = path.with_name(path.name[:-3])
        with gzip.open(path, "rb") as src, target.open("wb") as dst:
            shutil.copyfileobj(src, dst, 1 << 20)
        path.unlink()
        run.log(f"Decompressed {path.name} -> {target.name} ({target.stat().st_size:,} bytes)")
        return [target]
    if name.endswith(".zip"):
        out_dir = dest / path.name[:-4]
        shutil.rmtree(out_dir, ignore_errors=True)
        out_dir.mkdir(parents=True)
        files = []
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                if info.is_dir() or (pattern and not fnmatch.fnmatch(info.filename, pattern)):
                    continue
                target = _safe_member(out_dir, info.filename)
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst, 1 << 20)
                files.append(target)
        path.unlink()
        run.log(f"Zip archive extracted: {len(files)} file(s)")
        return sorted(files)
    return [path]


def download(
    client: httpx.Client,
    url: str,
    dest: Path,
    headers: dict[str, str],
    extract: str | None,
    previous: dict[str, Any] | None,
    run: Run,
    cancelled: Callable[[], bool] = lambda: False,
    fmt: str = "csv",
) -> dict[str, Any]:
    """Download `url` into `dest` (decompressing archives). Returns the source entry."""
    headers = dict(headers)
    dest.mkdir(parents=True, exist_ok=True)
    prev_files = [dest / f for f in (previous or {}).get("files", [])]
    # Files may have been discarded after the build (storage.keep_raw: false): the ETag is still valid
    can_revalidate = bool(previous) and (previous.get("discarded") or (prev_files and all(f.exists() for f in prev_files))) and not run.force
    if can_revalidate:
        if previous.get("etag"):
            headers["If-None-Match"] = previous["etag"]
        if previous.get("last_modified"):
            headers["If-Modified-Since"] = previous["last_modified"]

    run.log(f"Downloading {url}")
    # The download lands in a staging area: previous files are only replaced once the new ones are validated
    staging = dest / ".incoming"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    with client.stream("GET", url, headers=headers) as resp:
        if resp.status_code == 304 and can_revalidate:
            run.log("Source unchanged (HTTP 304), local files kept")
            return {**previous, "checked_at": iso(utcnow()), "not_modified": True}
        resp.raise_for_status()
        name = _filename(resp, url)
        total = int(resp.headers.get("content-length") or 0)
        run.bytes_total += total
        part = staging / (name + ".part")
        size = 0
        started = time.monotonic()
        with part.open("wb") as f:
            for chunk in resp.iter_bytes(1 << 20):
                if cancelled():
                    raise RunCancelled()
                f.write(chunk)
                size += len(chunk)
                run.bytes_done += len(chunk)
        if size == 0:
            shutil.rmtree(staging, ignore_errors=True)
            raise SourceCorrupted(f"corrupted remote source: {url} returned an empty file (0 bytes)")
        if total and size != total and "content-encoding" not in resp.headers:
            raise IOError(f"incomplete download ({size} / {total} bytes)")
        elapsed = max(time.monotonic() - started, 1e-6)
        run.log(f"Received {name}: {size:,} bytes in {elapsed:.1f}s ({size / elapsed / 1e6:.1f} MB/s)")
        etag, last_modified = resp.headers.get("etag"), resp.headers.get("last-modified")

    final = staging / name
    os.replace(part, final)
    try:
        staged = unpack(final, staging, extract, run, fmt)
        if not staged:
            raise SourceCorrupted(f"corrupted remote source: no usable file in {name}")
        for f in staged:
            check_source_file(f, fmt, f"{name} → {f.name}" if f.name != name else name)
    except SourceCorrupted:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    produced = []
    for f in staged:
        target = dest / f.relative_to(staging)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(f, target)
        produced.append(target)
    shutil.rmtree(staging, ignore_errors=True)
    remote_date = None
    if last_modified:
        try:
            remote_date = iso(parsedate_to_datetime(last_modified))
        except (TypeError, ValueError):
            pass
    return {
        "url": url,
        "final_url": str(resp.url),
        "files": [str(p.relative_to(dest)).replace("\\", "/") for p in produced],
        "etag": etag,
        "last_modified": last_modified,
        "remote_date": remote_date,
        "downloaded_bytes": size,
        "fetched_at": iso(utcnow()),
        "checked_at": iso(utcnow()),
        "not_modified": False,
    }


def http_client(settings: Settings) -> httpx.Client:
    """Client honouring the proxy and certificates configured in the administration settings."""
    from .network import client

    return client(settings)


def _cleanup_raw(paths: RefPaths, keep: set[str]) -> None:
    for p in sorted(paths.raw.rglob("*"), reverse=True):
        if ".incoming" in p.parts:
            continue
        rel = str(p.relative_to(paths.raw)).replace("\\", "/")
        if p.is_file() and rel not in keep:
            p.unlink(missing_ok=True)
        elif p.is_dir() and not any(p.iterdir()):
            p.rmdir()


def _fingerprint(files: list[Path], config_hash: str) -> str:
    h = hashlib.sha256(config_hash.encode())
    for f in files:
        h.update(f.name.encode())
        with f.open("rb") as fh:
            h.update(hashlib.file_digest(fh, "sha256").digest())
    return h.hexdigest()


# --------------------------------------------------------------------------- pipeline

def _connect(settings: Settings, paths: RefPaths, fmt: str, key: tuple[str, str] | None = None) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    if key:
        # Writing encrypted Parquet needs the OpenSSL crypto of httpfs (the built-in one can only decrypt)
        con.execute("LOAD httpfs")
        con.execute(f"PRAGMA add_parquet_key({lit(key[0])}, {lit(key[1])})")
    con.execute(f"SET temp_directory = {lit(str(paths.work / 'duckdb'))}")
    if settings.duckdb_memory_limit:
        con.execute(f"SET memory_limit = {lit(settings.duckdb_memory_limit)}")
    if settings.duckdb_threads:
        con.execute(f"SET threads = {int(settings.duckdb_threads)}")
    if fmt == "xlsx":
        try:
            con.execute("LOAD excel")
        except duckdb.Error:
            con.execute("INSTALL excel; LOAD excel")
    return con


def _profile(con: duckdb.DuckDBPyConnection, rel: str) -> list[dict[str, Any]]:
    res = con.execute(f"SUMMARIZE SELECT * FROM {rel}")
    cols = [d[0] for d in res.description]
    out = []
    for row in res.fetchall():
        d = dict(zip(cols, row))
        out.append({
            "name": d.get("column_name"),
            "type": d.get("column_type"),
            "min": None if d.get("min") is None else str(d["min"])[:200],
            "max": None if d.get("max") is None else str(d["max"])[:200],
            "approx_unique": d.get("approx_unique"),
            "avg": None if d.get("avg") is None else str(d["avg"])[:50],
            "std": None if d.get("std") is None else str(d["std"])[:50],
            "q25": None if d.get("q25") is None else str(d["q25"])[:50],
            "q50": None if d.get("q50") is None else str(d["q50"])[:50],
            "q75": None if d.get("q75") is None else str(d["q75"])[:50],
            "count": d.get("count"),
            "null_percentage": float(d["null_percentage"]) if d.get("null_percentage") is not None else None,
        })
    return out


def compute_changes(con: duckdb.DuckDBPyConnection, current: str, previous: str, key: str,
                    current_key: str | None = None, previous_key: str | None = None) -> dict[str, int]:
    k = qi(key)
    cur, prev = parquet_reader(current, current_key), parquet_reader(previous, previous_key)
    # One pass over both versions: each row is reduced to its key and a hash of its content
    side = "SELECT {k} AS k, hash(CAST(t AS VARCHAR)) AS h FROM {src} t"
    counts = (
        "SELECT count(*) FILTER (WHERE p.k IS NULL), count(*) FILTER (WHERE c.k IS NULL), "
        "count(*) FILTER (WHERE c.k IS NOT NULL AND p.k IS NOT NULL AND c.h <> p.h) "
        f"FROM ({side.format(k=k, src=cur)}) c FULL OUTER JOIN ({side.format(k=k, src=prev)}) p ON c.k = p.k"
    )
    try:
        added, removed, modified = con.execute(counts).fetchone()
    except duckdb.Error:  # schema changed in a way the hash cannot compare
        added = con.execute(f"SELECT count(*) FROM {cur} c ANTI JOIN {prev} p ON c.{k} = p.{k}").fetchone()[0]
        removed = con.execute(f"SELECT count(*) FROM {prev} p ANTI JOIN {cur} c ON c.{k} = p.{k}").fetchone()[0]
        modified = None
    return {"added": added, "removed": removed, "modified": modified}


_COMPRESSED = re.compile(r"\.(gz|zip|tgz|tar\.gz|tar)$", re.IGNORECASE)


def strip_compression(name: str) -> str:
    return re.sub(r"\.(gz|zip|tgz|tar\.gz)$", "", name, flags=re.IGNORECASE)


def manual_groups(ref: ReferentialConfig, files: list[Path]) -> list[list[Path]]:
    """Map manually provided files to the sources of the referential. A referential combining
    several URLs (joins in the transform) needs one file per URL, named like the original."""
    urls = ref.source.urls if ref.source.type == "http" else []
    if len(urls) <= 1:
        return [files]
    expected = [strip_compression(Path(u.split("?")[0]).name) for u in urls]
    by_name = {strip_compression(f.name): f for f in files}
    missing = [e for e in expected if e not in by_name]
    if missing:
        raise RunRejected(
            f"this referential combines {len(urls)} sources: provide one file per source, named "
            f"{', '.join(expected)} (missing: {', '.join(missing)})"
        )
    return [[by_name[e]] for e in expected]


def internal_sql(ref: ReferentialConfig, path: Path, count: int) -> str:
    """Typed SELECT over the exported rows of an internal referential."""
    from .config import DUCKDB_TYPES

    cols = [(c.name, DUCKDB_TYPES[c.type]) for c in ref.columns]
    if count == 0:
        return "SELECT " + ", ".join(f"CAST(NULL AS {t}) AS {qi(n)}" for n, t in cols) + " WHERE false"
    spec = "{" + ", ".join(f"{lit(n)}: {lit(t)}" for n, t in cols) + "}"
    return f"SELECT {', '.join(qi(n) for n, _ in cols)} FROM read_json({lit(str(path))}, format = 'newline_delimited', columns = {spec})"


def execute_run(
    ref: ReferentialConfig,
    settings: Settings,
    run: Run,
    cancelled: Callable[[], bool] = lambda: False,
    manual: dict[str, Any] | None = None,
    internal_rows: Callable[[str], list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Run the full pipeline. Returns the new meta. Raises on failure."""
    paths = RefPaths(settings.data_dir, ref.id).ensure()
    meta = read_meta(paths)
    run.started_at = utcnow()
    run.status = "running"

    # 1. Acquire source files ------------------------------------------------
    run.phase = "download"
    groups: list[list[Path]] = []
    sources: list[dict[str, Any]] = []
    if ref.is_internal:
        # Rows managed in RefExposer: exported from the database, then published like any source
        records = internal_rows(ref.id) if internal_rows else []
        export = paths.raw / "records.jsonl"
        with export.open("wb") as f:
            for r in records:
                f.write(orjson.dumps(r) + b"\n")
        groups.append([export])
        sources.append({"url": None, "internal": True, "files": ["raw/records.jsonl"], "records": len(records), "checked_at": iso(utcnow())})
        run.log(f"Internal referential: {len(records):,} row(s) exported from the database")
    elif manual is not None:
        # Manual import (upload or import folder): files provided instead of the usual source
        files: list[Path] = []
        for f in manual["files"]:
            files.extend(unpack(f, f.parent, ref.source.extract, run, ref.format))
        for f in files:
            check_source_file(f, ref.format, f.name)
        groups = manual_groups(ref, sorted(files))
        names = [f.name for f in files]
        how = "file upload" if manual["origin"] == "upload" else "import folder"
        who = f" by {manual['by']}" if manual.get("by") else ""
        run.log(f"Manual import ({how}{who}): {', '.join(names)}")
        sources.append({
            "url": None,
            "manual": True,
            "origin": manual["origin"],
            "by": manual.get("by"),
            "files": [str(f.relative_to(paths.root)).replace("\\", "/") if f.is_relative_to(paths.root) else str(f) for f in files],
            "imported_at": iso(utcnow()),
            "checked_at": iso(utcnow()),
        })
    elif ref.source.type == "http":
        # A changed configuration (transform, confidentiality...) needs the files again: no conditional request
        # (HTTP 304) when the downloaded files were not kept
        config_changed = bool(meta.get("config_hash")) and meta["config_hash"] != ref.config_hash
        previous_sources = {} if config_changed else {s.get("url"): s for s in meta.get("sources", [])}
        with http_client(settings) as client:
            for url in ref.source.urls:
                entry = download(client, url, paths.raw, ref.source.request_headers(), ref.source.extract, previous_sources.get(url), run, cancelled, ref.format)
                sources.append(entry)
                groups.append([paths.raw / f for f in entry["files"]])
        _cleanup_raw(paths, {f for s in sources for f in s["files"]})
        if not run.force and paths.published.exists() and all(s.get("not_modified") and s.get("discarded") for s in sources):
            run.log("Sources unchanged (HTTP 304): the published version is kept")
            run.status = "unchanged"
            run.stats.update({"rows": meta.get("row_count")})
            meta["last_checked_at"] = iso(utcnow())
            meta["sources"] = sources
            return meta
    elif ref.source.type == "git":
        from .gitsource import GitError, fetch, remote_commit

        previous = (meta.get("sources") or [{}])[0]
        same_config = meta.get("config_hash") == ref.config_hash
        try:
            if not run.force and same_config and previous.get("commit") and paths.published.exists():
                head = remote_commit(settings, ref.source)
                if head and head == previous["commit"]:
                    run.log(f"Repository unchanged (commit {head[:12]}): the published version is kept")
                    run.status = "unchanged"
                    run.stats.update({"rows": meta.get("row_count")})
                    meta["last_checked_at"] = iso(utcnow())
                    meta["sources"] = [{**previous, "checked_at": iso(utcnow())}]
                    return meta
            if cancelled():
                raise RunCancelled()
            commit, files = fetch(settings, ref.source, paths.raw / "git", run.log)
        except GitError as e:
            raise SourceCorrupted(f"git source unavailable: {e}") from e
        if not files:
            raise SourceCorrupted(f"corrupted source: no file of the repository matches '{ref.source.path}' (commit {commit[:12]})")
        groups.append(files)
        sources.append({
            "url": ref.source.repository,
            "ref": ref.source.ref or "default branch",
            "commit": commit,
            "files": [str(f.relative_to(paths.root)).replace("\\", "/") for f in files],
            "checked_at": iso(utcnow()),
        })
    elif ref.source.type == "sync":
        # Files pushed into the sync folder: read in place; archives are first copied and unpacked under raw/
        from .sync import resolve

        found = resolve(settings.sync_dir, ref.source.path or "")
        staging = paths.raw / "sync"
        shutil.rmtree(staging, ignore_errors=True)
        files = []
        for f in found:
            if keeps_gzip(f, ref.format):
                files.append(f)  # read in place, compressed
            elif _COMPRESSED.search(f.name):
                staging.mkdir(parents=True, exist_ok=True)
                copy = staging / f.name
                shutil.copyfile(f, copy)
                files.extend(unpack(copy, staging, ref.source.extract, run, ref.format))
            else:
                files.append(f)
        run.log(f"Sync folder: {len(found)} file(s) in sync/{ref.source.path}")
        groups.append(sorted(files))
        sources.append({
            "url": None,
            "sync": ref.source.path,
            "files": ["sync/" + f.relative_to(settings.sync_dir.resolve()).as_posix() for f in found],
            "checked_at": iso(utcnow()),
        })
    else:
        files = sorted(p for p in paths.root.glob(ref.source.path or "") if p.is_file())
        if not files:
            raise FileNotFoundError(f"no file matches '{ref.source.path}' in {paths.root}")
        run.log(f"Local source: {len(files)} file(s)")
        groups.append(files)
        sources.append({
            "url": None,
            "files": [str(p.relative_to(paths.root)).replace("\\", "/") for p in files],
            "checked_at": iso(utcnow()),
        })

    all_files = [f for g in groups for f in g]
    if ref.source.type not in ("http", "internal") and manual is None:  # downloaded / imported files are already checked
        for f in all_files:
            check_source_file(f, ref.format)
    run.stats["downloaded_bytes"] = run.bytes_done
    fingerprint = _fingerprint(all_files, ref.config_hash)
    meta["sources"] = sources
    meta["raw_size"] = sum(f.stat().st_size for f in all_files)
    meta["last_checked_at"] = iso(utcnow())

    if not run.force and fingerprint == meta.get("fingerprint") and paths.published.exists():
        run.log("Content identical to the published version: no rebuild needed")
        run.status = "unchanged"
        run.stats.update({"rows": meta.get("row_count")})
        return meta

    if ref.is_bloom:
        return _publish_bloom(ref, run, paths, meta, all_files, sources, fingerprint)
    if ref.is_mmdb:
        return _publish_mmdb(ref, run, paths, meta, all_files, sources, fingerprint)

    # 2. Transform into parquet ------------------------------------------------
    run.phase = "transform"
    if cancelled():
        raise RunCancelled()
    paths.work.mkdir(parents=True, exist_ok=True)
    tmp = paths.work / "next.parquet"
    tmp.unlink(missing_ok=True)
    if ref.is_internal:
        sql = internal_sql(ref, all_files[0], sources[0]["records"])
    else:
        sql = build_source_sql(ref.format, [[str(f) for f in g] for g in groups], ref.options, ref.transform)
    st = ref.storage
    copy_opts = f"FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE {st.row_group_size}"
    # Confidential: Parquet Modular Encryption (AES-GCM) of every written file. `was_encrypted`: state of the
    # published version, which may differ when the confidentiality has just been switched
    was_encrypted = bool(meta.get("encrypted")) and paths.current.exists()
    switched = paths.current.exists() and was_encrypted != ref.confidential
    pkey = None
    if ref.confidential or was_encrypted:
        pkey = parquet_key(ref.id)
    new_key = pkey[0] if ref.confidential else None
    if new_key:
        copy_opts += f", ENCRYPTION_CONFIG {{footer_key: {lit(new_key)}}}"
    if st.sort_by:
        sql = f"SELECT * FROM ({sql}) ORDER BY {', '.join(qi(c) for c in st.sort_by)}"
    con = _connect(settings, paths, ref.format, pkey)
    index_tmp: dict[str, Path] = {}
    try:
        t0 = time.monotonic()
        run.log("Transforming and writing parquet" + (f" (sorted on {', '.join(st.sort_by)})" if st.sort_by else ""))
        con.execute(f"COPY ({sql}) TO {lit(str(tmp))} ({copy_opts})")
        rel = parquet_reader(str(tmp), new_key)
        if new_key:  # the footer of an encrypted file is not readable by parquet_file_metadata()
            rows = con.execute(f"SELECT count(*) FROM {rel}").fetchone()[0]
        else:  # row count from the parquet footer: no scan
            rows = con.execute(f"SELECT coalesce(sum(num_rows), 0) FROM parquet_file_metadata({lit(str(tmp))})").fetchone()[0]
        columns = [{"name": r[0], "type": r[1]} for r in con.execute(f"DESCRIBE SELECT * FROM {rel}").fetchall()]
        run.log(f"{rows:,} rows, {len(columns)} columns produced in {time.monotonic() - t0:.1f}s")
        large = rows > settings.large_rows
        if large:
            run.log(f"Large referential (> {settings.large_rows:,} rows): costly processing is limited")
        for c in [*st.sort_by, *st.indexes]:
            if c not in {col["name"] for col in columns}:
                raise RunRejected(f"unknown sort/index column: '{c}'")
        for c in st.indexes:
            t1 = time.monotonic()
            path = paths.work / f"next.by_{RefPaths.index_name(c)}.parquet"
            con.execute(f"COPY (SELECT * FROM {rel} ORDER BY {qi(c)}) TO {lit(str(path))} ({copy_opts})")
            index_tmp[c] = path
            run.log(f"Index on '{c}' written in {time.monotonic() - t1:.1f}s ({path.stat().st_size:,} bytes)")

        # 3. Validate ---------------------------------------------------------
        run.phase = "validate"
        col_names = {c["name"] for c in columns}
        if rows == 0 and not ref.is_internal:
            raise SourceCorrupted("corrupted source: no row produced (empty file, or unreadable with this format)")
        if rows < ref.validation.min_rows and not ref.is_internal:
            raise RunRejected(f"{rows} row(s) produced, minimum expected: {ref.validation.min_rows}")
        prev_rows = meta.get("row_count") if paths.current.exists() else None
        if ref.validation.max_drop_pct is not None and prev_rows:
            drop = (prev_rows - rows) / prev_rows * 100
            if drop > ref.validation.max_drop_pct:
                raise RunRejected(f"row count dropped by {drop:.1f}% ({prev_rows:,} -> {rows:,}), threshold {ref.validation.max_drop_pct}%")
        key_unique = None
        if ref.key and ref.key not in col_names:
            raise RunRejected(f"the key '{ref.key}' is not among the produced columns")
        if ref.key and ref.is_internal:
            key_unique = True  # guaranteed by the database (one row per key)
        elif ref.key and (large or not st.check_key):
            run.log("Key uniqueness check skipped (volume or configuration)")
        elif ref.key:
            distinct = con.execute(f"SELECT count(DISTINCT {qi(ref.key)}) FROM {rel}").fetchone()[0]
            key_unique = distinct == rows
            if not key_unique:
                msg = f"key '{ref.key}' is not unique: {distinct:,} distinct values for {rows:,} rows"
                if ref.validation.unique_key:
                    raise RunRejected(msg)
                run.log(msg, "warn")
        for c in ref.search_columns:
            if c not in col_names:
                run.log(f"search column '{c}' is missing from the data set", "warn")

        # 4. Changes versus the published version -------------------------------
        changes = None
        if ref.key and paths.current.exists() and (large or not st.track_changes):
            run.log("Change tracking skipped (volume or configuration)")
        elif ref.key and paths.current.exists():
            try:
                changes = compute_changes(con, str(tmp), str(paths.current), ref.key, new_key, pkey[0] if was_encrypted else None)
                run.log(f"Changes: +{changes['added']:,} / -{changes['removed']:,} / ~{changes['modified'] if changes['modified'] is not None else '?'} modified")
            except duckdb.Error as e:
                run.log(f"Cannot compute the changes: {e}", "warn")

        # 5. Profile ------------------------------------------------------------
        run.phase = "profile"
        mode = st.profile if not (large and st.profile == "full") else "sample"
        profile = []
        if mode != "none":
            try:
                t1 = time.monotonic()
                source = rel if mode == "full" else f"(SELECT * FROM {rel} USING SAMPLE 1000000 ROWS)"
                profile = _profile(con, source)
                run.log(f"Column profile computed{' on a sample of 1,000,000 rows' if mode == 'sample' else ''} in {time.monotonic() - t1:.1f}s")
            except duckdb.Error as e:
                run.log(f"Cannot compute the column profile: {e}", "warn")
    finally:
        con.close()

    # 6. Publish --------------------------------------------------------------
    run.phase = "publish"
    if paths.current.exists() and st.keep_previous and not switched:
        os.replace(paths.current, paths.previous)
    else:
        # Confidentiality switched: the previous version (other encryption state) is not kept
        paths.previous.unlink(missing_ok=True)
        if switched:
            run.log("Confidentiality changed: previous version and generated downloads removed")
    os.replace(tmp, paths.current)
    for c, path in index_tmp.items():
        os.replace(path, paths.index_path(c))
    wanted = {paths.index_path(c) for c in st.indexes}
    for f in paths.index_files():
        if f not in wanted:
            f.unlink(missing_ok=True)
    shutil.rmtree(paths.work, ignore_errors=True)
    if (not st.keep_raw or ref.confidential) and ref.source.type in ("http", "internal", "git"):
        shutil.rmtree(paths.raw, ignore_errors=True)
        paths.raw.mkdir(parents=True, exist_ok=True)
        for s in sources:
            s["discarded"] = True
        if ref.source.type == "http":
            run.log("Source files deleted after import" + (" (confidential)" if ref.confidential else " (storage.keep_raw: false)"))
    if ref.confidential or switched:
        shutil.rmtree(paths.root / "exports", ignore_errors=True)  # downloads generated from a previous version

    now = iso(utcnow())
    meta.update({
        "version": int(meta.get("version", 0)) + 1,
        "fingerprint": fingerprint,
        "config_hash": ref.config_hash,
        "data_updated_at": now,
        "previous_row_count": prev_rows,
        "row_count": rows,
        "columns": columns,
        "profile": [] if ref.confidential else profile,
        # The column profile holds data values (min, max...): encrypted for a confidential referential
        "profile_enc": encrypt_data(orjson.dumps(profile).decode(), ref.id) if ref.confidential else None,
        "encrypted": ref.confidential,
        "key_unique": key_unique,
        "changes": changes,
        "parquet_size": paths.current.stat().st_size,
        "has_previous": paths.previous.exists(),
        "storage_size": dir_size(paths.root),
        "large": large,
        "profile_sampled": mode == "sample",
        "sort_by": st.sort_by,
        "indexes": st.indexes,
        "sources": sources,
    })
    run.stats.update({"rows": rows, "previous_rows": prev_rows, "changes": changes, "version": meta["version"]})
    run.log(f"Version {meta['version']} published ({meta['parquet_size']:,} bytes of parquet)")
    return meta


def _publish_bloom(ref: ReferentialConfig, run: Run, paths: RefPaths, meta: dict[str, Any],
                   files: list[Path], sources: list[dict[str, Any]], fingerprint: str) -> dict[str, Any]:
    """Bloom filters are published as is: validated, then exposed for membership tests."""
    run.phase = "validate"
    if len(files) != 1:
        raise RunRejected(f"a single Bloom filter file is expected ({len(files)} found)")
    try:
        bf = BloomFilter(files[0])
    except (BloomError, OSError) as e:
        raise RunRejected(f"unreadable Bloom filter: {e}") from e
    info = bf.info()
    bf.close()
    rows = info["elements"]
    run.log(f"Bloom filter: {rows:,} elements, {info['hash_functions']} hash functions, "
            f"{info['bits']:,} bits, estimated false positive rate {info['estimated_fp_rate']:.1e}")
    if rows < ref.validation.min_rows:
        raise RunRejected(f"{rows} element(s), minimum expected: {ref.validation.min_rows}")
    prev_rows = meta.get("row_count") if paths.bloom.exists() else None
    if ref.validation.max_drop_pct is not None and prev_rows:
        drop = (prev_rows - rows) / prev_rows * 100
        if drop > ref.validation.max_drop_pct:
            raise RunRejected(f"element count dropped by {drop:.1f}% ({prev_rows:,} -> {rows:,})")

    run.phase = "publish"
    if paths.bloom.exists() and ref.storage.keep_previous:
        os.replace(paths.bloom, paths.previous_bloom)
    if ref.source.type == "http" or sources[0].get("manual"):
        os.replace(files[0], paths.bloom)  # no copy of a large file: the ETag still allows 304 revalidation
        for s in sources:
            s["discarded"] = True
    else:
        shutil.copyfile(files[0], paths.bloom)
    meta.update({
        "version": int(meta.get("version", 0)) + 1,
        "fingerprint": fingerprint,
        "config_hash": ref.config_hash,
        "data_updated_at": iso(utcnow()),
        "previous_row_count": prev_rows,
        "row_count": rows,
        "columns": [{"name": "value", "type": "VARCHAR"}, {"name": "present", "type": "BOOLEAN"}],
        "profile": [],
        "changes": None,
        "kind": "bloom",
        "bloom": info,
        "parquet_size": info["size"],
        "storage_size": dir_size(paths.root),
        "sources": sources,
    })
    run.stats.update({"rows": rows, "previous_rows": prev_rows, "version": meta["version"]})
    run.log(f"Version {meta['version']} published")
    return meta


def _publish_mmdb(ref: ReferentialConfig, run: Run, paths: RefPaths, meta: dict[str, Any],
                  files: list[Path], sources: list[dict[str, Any]], fingerprint: str) -> dict[str, Any]:
    """MaxMind DB files are published as is (raw download for tools), after a check that they open and
    keep the same database type (a City database never silently becomes an ASN one)."""
    from .geoip import MmdbDatabase, MmdbError, is_mmdb

    run.phase = "validate"
    # MaxMind archives also hold COPYRIGHT.txt and LICENSE.txt: the database is the .mmdb member
    candidates = [f for f in files if f.suffix.lower() == ".mmdb"] or [f for f in files if is_mmdb(f)]
    if len(candidates) != 1:
        names = ", ".join(f.name for f in files[:10])
        raise RunRejected(f"a single MaxMind DB (.mmdb) file is expected, {len(candidates)} found ({names}): "
                          "set the archive filter (e.g. '*City.mmdb')")
    source = candidates[0]
    try:
        db = MmdbDatabase(source)
        info = db.info()
        db.close()
    except (MmdbError, OSError) as e:
        raise SourceCorrupted(f"corrupted source: unreadable MaxMind DB '{source.name}' ({e})") from e
    rows = info["node_count"]
    run.log(f"MaxMind DB {info['database_type']} built on {info['build_date'][:10]}: IPv{info['ip_version']}, "
            f"{rows:,} nodes, {info['size']:,} bytes")
    previous = meta.get("mmdb") or {}
    if previous.get("database_type") and previous["database_type"] != info["database_type"] and not run.force:
        raise RunRejected(f"database type changed ({previous['database_type']} -> {info['database_type']}): "
                          "check the source, or force the update to accept it")
    if previous.get("build_epoch") and info["build_epoch"] < previous["build_epoch"] and not run.force:
        raise RunRejected(f"older database than the published one (built {info['build_date'][:10]}): force the update to accept it")
    if rows < ref.validation.min_rows:
        raise RunRejected(f"{rows} node(s), minimum expected: {ref.validation.min_rows}")
    prev_rows = meta.get("row_count") if paths.mmdb.exists() else None
    if ref.validation.max_drop_pct is not None and prev_rows:
        drop = (prev_rows - rows) / prev_rows * 100
        if drop > ref.validation.max_drop_pct:
            raise RunRejected(f"node count dropped by {drop:.1f}% ({prev_rows:,} -> {rows:,})")

    run.phase = "publish"
    if paths.mmdb.exists() and ref.storage.keep_previous:
        os.replace(paths.mmdb, paths.previous_mmdb)
    if ref.source.type == "http" or sources[0].get("manual"):
        os.replace(source, paths.mmdb)  # no copy of a large file: the ETag still allows 304 revalidation
        for s in sources:
            s["discarded"] = True
    else:
        shutil.copyfile(source, paths.mmdb)
    info["file_name"] = source.name
    meta.update({
        "version": int(meta.get("version", 0)) + 1,
        "fingerprint": fingerprint,
        "config_hash": ref.config_hash,
        "data_updated_at": iso(utcnow()),
        "previous_row_count": prev_rows,
        "row_count": rows,
        "columns": [{"name": "ip", "type": "VARCHAR"}, {"name": "network", "type": "VARCHAR"}],
        "profile": [],
        "changes": None,
        "kind": "mmdb",
        "mmdb": info,
        "parquet_size": info["size"],
        "storage_size": dir_size(paths.root),
        "sources": sources,
    })
    run.stats.update({"rows": rows, "previous_rows": prev_rows, "version": meta["version"]})
    run.log(f"Version {meta['version']} published")
    return meta


def save_meta(settings: Settings, ref_id: str, meta: dict[str, Any]) -> None:
    write_meta(RefPaths(settings.data_dir, ref_id), meta)
