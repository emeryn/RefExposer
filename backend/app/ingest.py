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
import threading
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
from .bloom import normalize as bloom_normalize
from .config import ReferentialConfig, parse_duration
from .crypto import encrypt_data, parquet_key
from .quality import SAMPLE_ROWS as QUALITY_SAMPLE_ROWS
from .resources import sqlite_cache_kib
from .quality import evaluate as evaluate_quality
from .settings import Settings
from .sqlbuild import (SQLITE_MAGIC, attach_sqlite, build_source_sql, fallback_reason, is_sqlite_file, lit, parquet_reader,
                       parquet_sample, qi, read_fallbacks)
from .storage import RefPaths, dir_size, iso, parse_iso, read_meta, utcnow, write_meta

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
SQLITE_SUFFIXES = (".db", ".sqlite", ".sqlite3")


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
    if fmt == "sqlite":
        # Archives also hold readme / schema / signature files (e.g. NIST NSRL): only the databases are read
        if path.suffix.lower() in SQLITE_SUFFIXES and not head.startswith(SQLITE_MAGIC):
            raise SourceCorrupted(f"corrupted source: '{label}' does not have the signature of a SQLite database")
        return
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

    def __init__(self, ref_id: str, trigger: str, force: bool = False, user: str | None = None, replace: bool = False):
        self.id = uuid.uuid4().hex[:12]
        self.ref_id = ref_id
        self.trigger = trigger
        self.force = force
        # Merged referential (deltas): publish the new data set alone instead of merging it (new base)
        self.replace = replace
        # Interrupted by a stop of the service (not by a user, not a crash): recorded in the journal, started again
        self.shutdown = False
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
        # New attempts of a failed download (None: DOWNLOAD_RETRIES), set from the source / the settings
        self.retries: int | None = None
        # File holding the run while it is going on: when the service dies (out of memory...), the next start
        # finds it, with the logs and the phase reached
        self.journal: Path | None = None

    def log(self, msg: str, level: str = "info") -> None:
        self.logs.append({"t": iso(utcnow()), "level": level, "msg": msg})
        getattr(log, "warning" if level == "warn" else level, log.info)("[%s] %s", self.ref_id, msg)
        self.save_journal()

    def save_journal(self) -> None:
        if self.journal is None:
            return
        try:
            tmp = self.journal.with_suffix(".tmp")
            tmp.write_bytes(orjson.dumps(self.to_dict()))
            os.replace(tmp, self.journal)
        except OSError:
            pass

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
            "replace": self.replace,
            **({"shutdown": True} if self.shutdown else {}),
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


# Above this size of source files, the update runs in low memory mode (see _connect)
LARGE_SOURCE_BYTES = 2 << 30

# Line-based formats DuckDB reads straight from .gz files: those are kept compressed (no huge decompressed copy)
STREAMABLE_GZIP = ("csv", "tsv", "txt", "jsonl")


def keeps_gzip(path: Path, fmt: str | None) -> bool:
    name = path.name.lower()
    return fmt in STREAMABLE_GZIP and name.endswith(".gz") and not name.endswith(".tar.gz")


def inflated_while_downloading(name: str, fmt: str | None) -> bool:
    """A .gz of a format read as a whole (Parquet, JSON, XML, SQLite...): decompressed as it arrives, so that the
    compressed file never sits on the disk next to the decompressed one (53 GB + 91 GB for a passive DNS dump)."""
    low = name.lower()
    return low.endswith(".gz") and not low.endswith(".tar.gz") and not keeps_gzip(Path(name), fmt)


_SYNC = b"\x00\x00\xff\xff"  # end of the empty stored block written by a deflate sync flush (byte aligned)
RESUME_POINT_EVERY = 64 << 20  # compressed bytes between two resume points kept


class _Inflater:
    """Streaming gzip decompression (several members allowed, like gzip -d).

    It also records **resume points**: the ends of the empty stored blocks written by a sync flush (00 00 FF FF), which
    many compressors emit regularly (pigz, Python gzip.flush()...). There the deflate stream is byte aligned and the
    whole state of the decompressor is the last 32 KB of output, already on disk: a transfer stopped by a restart of the
    service resumes from the last point (raw deflate primed with that window) instead of from zero. Points are only taken
    in the first member, whose CRC is then checked over the whole file (see `trailer`)."""

    def __init__(self, zin: int = 0, zout: int = 0, window: bytes | None = None) -> None:
        self.raw = window is not None  # resumed in the middle of the first member: no gzip header, trailer read here
        self._d = zlib.decompressobj(-zlib.MAX_WBITS, zdict=window) if window else (
            zlib.decompressobj(-zlib.MAX_WBITS) if self.raw else zlib.decompressobj(16 + zlib.MAX_WBITS))
        self.zin, self.zout = zin, zout
        self.started = zin > 0
        self.member = 1
        self.points: list[tuple[int, int]] = []  # (compressed offset, decompressed offset)
        self.trailer: bytes | None = None  # raw mode: CRC32 + ISIZE of the first member
        self.first_member_size: int | None = None
        self._after = b""  # bytes after the end of a raw member: its trailer, then the next members
        self._tail = b""

    @property
    def finished(self) -> bool:
        if self.raw and self.member == 1:
            return self._d.eof and self.trailer is not None and not self._after
        return self.started and self._d.eof and not self._d.unused_data

    def _next_member(self, data: bytes) -> None:
        self.member += 1
        self._d = zlib.decompressobj(16 + zlib.MAX_WBITS)

    def _feed(self, data: bytes) -> bytes:
        out = []
        self.zin += len(data)
        while data:
            if self._d.eof:
                if self.raw and self.member == 1 and self.trailer is None:
                    self._after += data
                    if len(self._after) < 8:
                        break
                    self.trailer, data, self._after = self._after[:8], self._after[8:], b""
                    self.first_member_size = self.zout + sum(len(o) for o in out)
                    if not data:
                        break
                self._next_member(data)
            self.started = True
            out.append(self._d.decompress(data))
            data = self._d.unused_data if self._d.eof else b""
        result = b"".join(out)
        self.zout += len(result)
        return result

    def feed(self, data: bytes) -> bytes:
        """Decompress `data`, cut at the sync points so that each point maps a compressed offset to a decompressed one."""
        if self.member > 1 or self._d.eof:
            return self._feed(data)
        out, start = [], 0
        buf = self._tail + data
        i = buf.find(_SYNC)
        while i != -1:
            end = i + 4 - len(self._tail)  # end of the marker, in `data`
            if end > start:
                out.append(self._feed(data[start:end]))
                start = end
                if self.member == 1 and not self._d.eof and (not self.points or self.zin - self.points[-1][0] >= RESUME_POINT_EVERY):
                    self.points = [*self.points[-5:], (self.zin, self.zout)]
            i = buf.find(_SYNC, i + 1)
        out.append(self._feed(data[start:]))
        self._tail = buf[-3:]
        return b"".join(out)


def _resume_inflated(client: httpx.Client, url: str, headers: dict[str, str], state: dict[str, Any], part: Path,
                     run: Run) -> _Inflater | None:
    """Resume a download decompressed on the fly, left by a previous run (service restarted): from the last resume point,
    the decompression starts again primed with the 32 KB of output before it, and what it produces must be exactly the
    bytes already on disk after that point. Otherwise the previous point is tried. None: start again from zero."""
    size = part.stat().st_size if part.exists() else 0
    for c, d in reversed([tuple(p) for p in state.get("points") or []]):
        if d > size or not c:
            continue
        with part.open("rb") as f:
            f.seek(max(0, d - 32768))
            window = f.read(d - max(0, d - 32768))
            overlap = f.read(min(size - d, 4 << 20))
        inflater = _Inflater(zin=c, zout=d, window=window)
        produced = bytearray()
        req = {k: v for k, v in headers.items() if k not in ("If-None-Match", "If-Modified-Since")}
        req.update({"Range": f"bytes={c}-", "If-Range": _validator(state)})
        try:
            with client.stream("GET", url, headers=req) as resp:
                if resp.status_code != 206:  # changed meanwhile, or ranges no longer accepted
                    return None
                for chunk in resp.iter_bytes():
                    produced += inflater.feed(chunk)
                    if len(produced) >= max(len(overlap), 1 << 20) or inflater.finished:
                        break
        except zlib.error:
            produced = bytearray()  # not a real resume point: the previous one is tried
        except httpx.HTTPError as e:
            run.log(f"Resuming the decompressed download failed ({e}): started again", "warn")
            return None
        if len(produced) < len(overlap) or bytes(produced[:len(overlap)]) != overlap:
            continue
        with part.open("r+b") as f:  # the bytes after the point are replaced by the ones just checked
            f.seek(d)
            f.truncate()
            f.write(produced)
        run.log(f"Download resumed after a restart at {c:,} / {state.get('total') or 0:,} compressed bytes "
                f"({d:,} bytes already decompressed, checked against the stream)")
        return inflater
    return None


def _check_crc(part: Path, inflater: _Inflater, cancelled: Callable[[], bool]) -> None:
    """After a resumed decompression: CRC32 and size of the first member, over the whole file, as gzip -t does."""
    if inflater.trailer is None or inflater.first_member_size is None:
        return
    crc, isize = int.from_bytes(inflater.trailer[:4], "little"), int.from_bytes(inflater.trailer[4:], "little")
    if inflater.first_member_size % (1 << 32) != isize:
        raise SourceCorrupted(f"corrupted download: {inflater.first_member_size:,} bytes decompressed, the gzip trailer says "
                              f"{isize:,} (mod 2^32)")
    value, left = 0, inflater.first_member_size
    with part.open("rb") as f:
        while left > 0:
            if cancelled():
                raise RunCancelled()
            chunk = f.read(min(left, 16 << 20))
            if not chunk:
                break
            value = zlib.crc32(chunk, value)
            left -= len(chunk)
    if value != crc:
        raise SourceCorrupted("corrupted download: the CRC32 of the decompressed file does not match the gzip trailer "
                              "after the download was resumed (downloaded again at the next update)")


def unpack(path: Path, dest: Path, pattern: str | None, run: Run, fmt: str | None = None, guard: Any = None) -> list[Path]:
    """Decompress gz / zip / tar archives, returning the produced files.

    A .gz file of a line-based format (csv, tsv, txt, jsonl) is kept as is: DuckDB reads it while decompressing."""
    if keeps_gzip(path, fmt):
        run.log(f"{path.name}: kept compressed, read directly by the import")
        return [path]
    try:
        return _unpack(path, dest, pattern, run, guard)
    except (OSError, EOFError, zipfile.BadZipFile, tarfile.TarError, zlib.error) as e:
        raise SourceCorrupted(f"corrupted source: unreadable archive '{path.name}' ({e})") from e


def _copy(src: Any, dst: Any, guard: Any = None) -> None:
    """copyfileobj, with the free space watched (DiskGuard) while an archive is extracted."""
    if guard is None:
        shutil.copyfileobj(src, dst, 1 << 20)
        return
    while chunk := src.read(1 << 20):
        dst.write(chunk)
        guard.wrote(len(chunk))


def _unpack(path: Path, dest: Path, pattern: str | None, run: Run, guard: Any = None) -> list[Path]:
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
                    _copy(src, dst, guard)
                files.append(target)
        path.unlink()
        run.log(f"Tar archive extracted: {len(files)} file(s)")
        return sorted(files)
    if name.endswith(".gz"):
        target = path.with_name(path.name[:-3])
        with gzip.open(path, "rb") as src, target.open("wb") as dst:
            _copy(src, dst, guard)
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
                    _copy(src, dst, guard)
                files.append(target)
        path.unlink()
        run.log(f"Zip archive extracted: {len(files)} file(s)")
        return sorted(files)
    return [path]


# New attempts after a failure of the remote site (like curl --retry), unless set by the source or the settings
DOWNLOAD_RETRIES = 5
# Answers worth trying again (the same as curl --retry): timeout, too many requests, server errors
RETRY_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})
RETRY_MAX_WAIT = 300  # seconds: longest wait, even when the server asks for more (Retry-After)


class _Interrupted(Exception):
    """The connection ended before the announced size."""


class _RetryableStatus(Exception):
    """Answer of a site temporarily unavailable (RETRY_STATUSES)."""

    def __init__(self, error: httpx.HTTPStatusError):
        super().__init__(f"HTTP {error.response.status_code}")
        self.error = error
        self.retry_after = error.response.headers.get("retry-after")


def retry_wait(attempt: int, retry_after: str | None = None) -> float:
    """Seconds before attempt `attempt` + 1: the Retry-After of the server (seconds or date), else 1, 2, 4... s up
    to 60 s, like curl."""
    if retry_after:
        try:
            wait = float(retry_after) if retry_after.strip().isdigit() else                 (parsedate_to_datetime(retry_after) - utcnow()).total_seconds()
            return max(0.0, min(wait, RETRY_MAX_WAIT))
        except (TypeError, ValueError):
            pass
    return float(min(2 ** (attempt - 1), 60))


def _sleep(seconds: float, cancelled: Callable[[], bool]) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cancelled():
            raise RunCancelled()
        time.sleep(min(0.5, max(0.0, end - time.monotonic())))


def _partial(dest: Path, url: str) -> tuple[Path, Path]:
    """Interrupted download of `url` and the validators of its response (resumed later)."""
    key = hashlib.sha256(url.encode()).hexdigest()[:16]
    return dest / ".partial" / f"{key}.part", dest / ".partial" / f"{key}.json"


def _validator(state: dict[str, Any]) -> str | None:
    """Value of If-Range: a strong ETag (a weak one is not allowed), else the date of the response."""
    etag = state.get("etag")
    return etag if etag and not etag.startswith("W/") else state.get("last_modified")


def _partial_state(part: Path, state_file: Path, url: str) -> dict[str, Any]:
    """State of a download interrupted by a previous run, {} when there is nothing to resume."""
    try:
        state = orjson.loads(state_file.read_bytes()) if part.exists() else {}
    except (OSError, orjson.JSONDecodeError):
        state = {}
    if state.get("url") != url or not _validator(state):
        part.unlink(missing_ok=True)
        state_file.unlink(missing_ok=True)
        return {}
    return state


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
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Download `url` into `dest` (decompressing archives). Returns the source entry.
    With `settings`, the free space is watched while writing (REFEX_MIN_FREE_DISK)."""
    from .resources import DiskFull, DiskGuard

    guard = DiskGuard(settings, dest, f"download of {url}") if settings else None
    try:
        return _download(client, url, dest, headers, extract, previous, run, cancelled, fmt, guard)
    except DiskFull as e:
        raise RunRejected(str(e)) from e


def _download(client: httpx.Client, url: str, dest: Path, headers: dict[str, str], extract: str | None,
              previous: dict[str, Any] | None, run: Run, cancelled: Callable[[], bool], fmt: str, guard: Any) -> dict[str, Any]:
    from .resources import DiskFull, fmt_gb, free_disk

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
    # An interrupted transfer is kept (with the validators of its response) and resumed with an HTTP Range request,
    # by the next attempt of this run or by the next run: a source of tens of GB is not downloaded again from zero
    part, state_file = _partial(dest, url)
    state = _partial_state(part, state_file, url)
    digest = None  # SHA-256 of the received bytes: fingerprint of the source, without reading the files again
    # .gz of a format read as a whole: decompressed as it arrives (`part` then holds the decompressed bytes and
    # `inflater.zin` counts the compressed ones, the offset of a resumed request)
    inflater: _Inflater | None = None
    resumed = False  # decompressed transfer resumed from a resume point left by a previous run
    if state.get("inflate"):
        inflater = _resume_inflated(client, url, headers, state, part, run)
        if inflater is None:
            if state.get("points"):
                run.log("The decompressed download left by the previous run cannot be resumed: started again", "warn")
            part.unlink(missing_ok=True)
            state_file.unlink(missing_ok=True)
            state = {}
        else:
            resumed = True
            # The compressed bytes before the point are gone: the source is identified by its validators
            digest = hashlib.sha256(f"{state.get('etag')}|{state.get('last_modified')}|{state.get('total')}".encode())
    saved_point = inflater.points[-1] if inflater and inflater.points else None
    done0, total0 = run.bytes_done, run.bytes_total
    started = time.monotonic()
    attempt = 0
    try:
        while True:
            if inflater is not None and resumed and inflater.finished:  # the check of the resume point reached the end
                total, size, name = state.get("total") or inflater.zin, inflater.zin, state.get("name") or (unquote(Path(urlparse(url).path).name) or "download")
                etag, last_modified, final_url = state.get("etag"), state.get("last_modified"), url
                break
            if inflater is not None:
                offset = inflater.zin
            else:
                offset = part.stat().st_size if state and part.exists() else 0
            progress0 = offset
            req = dict(headers)
            if offset:  # a newer version may have started: If-Range sends the whole file when it changed
                req = {k: v for k, v in headers.items() if k not in ("If-None-Match", "If-Modified-Since")}
                req["Range"] = f"bytes={offset}-"
                req["If-Range"] = _validator(state)
            try:
                with client.stream("GET", url, headers=req) as resp:
                    if resp.status_code == 304 and can_revalidate and not offset:
                        run.log("Source unchanged (HTTP 304), local files kept")
                        return {**previous, "checked_at": iso(utcnow()), "not_modified": True}
                    if offset and inflater is None and resp.status_code == 416 and offset == state.get("total"):
                        run.log(f"Download already complete ({offset:,} bytes)")
                        total, mode = offset, None
                    elif offset and resp.status_code == 206:
                        total, mode = offset + int(resp.headers.get("content-length") or 0), "ab"
                        run.log(f"Download resumed at {offset:,} / {total:,} bytes")
                    else:
                        try:
                            resp.raise_for_status()
                        except httpx.HTTPStatusError as e:
                            if resp.status_code in RETRY_STATUSES:
                                raise _RetryableStatus(e) from e
                            raise
                        if offset:
                            run.log("The server sent the whole file (source changed, or resuming not supported): downloading it again", "warn")
                        offset, mode, resumed = 0, "wb", False
                        total = int(resp.headers.get("content-length") or 0)
                        digest = hashlib.sha256()
                        state = {"url": url, "name": _filename(resp, url), "etag": resp.headers.get("etag"),
                                 "last_modified": resp.headers.get("last-modified"), "total": total}
                        plain = "content-encoding" not in resp.headers
                        inflater = _Inflater() if plain and inflated_while_downloading(state["name"], fmt) else None
                        # Resumable only with a strong validator, and when the bytes are stored as sent. A decompressed
                        # transfer is resumed by the next runs from its resume points (state saved with them)
                        if not plain or not _validator(state):
                            state = {}
                        part.parent.mkdir(parents=True, exist_ok=True)
                        if state:
                            state_file.write_bytes(orjson.dumps({**state, "inflate": inflater is not None, "points": []}))
                        else:
                            state_file.unlink(missing_ok=True)
                        if guard is not None and total:
                            # Decompressed, the file is at least as large as what is announced
                            free = free_disk(dest)
                            if free < total + guard.reserve:
                                raise DiskFull(f"download of {url}: {fmt_gb(total)} announced"
                                               + (" (more once decompressed)" if inflater else "")
                                               + f", {fmt_gb(free)} free with a reserve of {fmt_gb(guard.reserve)} "
                                               "(REFEX_MIN_FREE_DISK): not started")
                        if inflater is not None:
                            run.log(f"{state.get('name') or _filename(resp, url)}: decompressed while it is downloaded")
                    if digest is None:  # resumed by another run: hash of the bytes already received
                        with part.open("rb") as fh:
                            digest = hashlib.file_digest(fh, "sha256")
                    run.bytes_total, run.bytes_done = total0 + total, done0 + offset
                    if mode:
                        with part.open(mode) as f:
                            # Written as received (no 1 MB buffer): an interruption loses none of the received bytes
                            for chunk in resp.iter_bytes():
                                if cancelled():
                                    raise RunCancelled()
                                digest.update(chunk)
                                run.bytes_done += len(chunk)
                                if inflater is not None:
                                    try:
                                        chunk = inflater.feed(chunk)
                                    except zlib.error as e:
                                        raise SourceCorrupted(f"corrupted remote source: {url} is not a valid gzip file ({e})") from e
                                f.write(chunk)
                                if guard is not None:
                                    guard.wrote(len(chunk))
                                if inflater is not None and state and inflater.points and inflater.points[-1] != saved_point:
                                    # The bytes up to the new resume point are written before the point is recorded
                                    f.flush()
                                    saved_point = inflater.points[-1]
                                    state_file.write_bytes(orjson.dumps({**state, "inflate": True, "points": inflater.points}))
                    size = inflater.zin if inflater is not None else part.stat().st_size
                    if total and size < total and "content-encoding" not in resp.headers:
                        raise _Interrupted(f"incomplete download ({size:,} / {total:,} bytes)")
                    if inflater is not None and not inflater.finished:
                        raise SourceCorrupted(f"corrupted remote source: {url} ends before the end of its gzip stream (truncated file)")
                    etag = resp.headers.get("etag") or state.get("etag")
                    last_modified = resp.headers.get("last-modified") or state.get("last_modified")
                    final_url = str(resp.url)
                    name = state.get("name") or _filename(resp, url)
                break
            except (httpx.TransportError, _Interrupted, _RetryableStatus) as e:
                # Remote site failing (connection refused or cut, timeout, 429, 5xx...): tried again later, like curl
                # --retry. A transfer with a strong validator is resumed where it stopped, otherwise started again.
                # Only the failures in a row count: an attempt that received data (a cut after 14 GB) starts the count again
                received = inflater.zin if inflater is not None else (part.stat().st_size if part.exists() else 0)
                if state and received > progress0:
                    attempt = 0
                attempt += 1
                retries = run.retries if run.retries is not None else DOWNLOAD_RETRIES
                if attempt > retries:
                    if isinstance(e, _RetryableStatus):
                        raise e.error from None
                    if isinstance(e, _Interrupted):
                        raise IOError(str(e)) from e
                    raise
                wait = retry_wait(attempt, e.retry_after if isinstance(e, _RetryableStatus) else None)
                what = (f"resumed at {received:,} bytes" if state and received else "started again")
                if inflater is not None and not (state and received):
                    inflater = None  # started again: a new decompression
                reason = str(e) or type(e).__name__
                run.log(f"Download of {url} failed ({reason}): {what} in {wait:.0f}s, "
                        f"attempt {attempt + 1}/{retries + 1}", "warn")
                _sleep(wait, cancelled)
    except BaseException as e:
        # Kept for the next run when it can resume it: the bytes as sent, or a decompression with resume points
        resumable = bool(state) and (inflater is None or bool(inflater.points)) and not isinstance(e, SourceCorrupted)
        if not resumable:
            part.unlink(missing_ok=True)
            state_file.unlink(missing_ok=True)
        raise
    if resumed and inflater is not None:
        run.log("Checking the CRC32 of the decompressed file (download resumed after a restart)")
        try:
            _check_crc(part, inflater, cancelled)
        except SourceCorrupted:
            part.unlink(missing_ok=True)
            state_file.unlink(missing_ok=True)
            raise
    state_file.unlink(missing_ok=True)
    if size == 0:
        part.unlink(missing_ok=True)
        shutil.rmtree(staging, ignore_errors=True)
        raise SourceCorrupted(f"corrupted remote source: {url} returned an empty file (0 bytes)")
    elapsed = max(time.monotonic() - started, 1e-6)
    run.log(f"Received {name}: {size:,} bytes in {elapsed:.1f}s ({(run.bytes_done - done0) / elapsed / 1e6:.1f} MB/s)"
            + (f", {part.stat().st_size:,} bytes once decompressed" if inflater is not None else ""))

    if inflater is not None:
        name = name[:-3]  # decompressed: the .gz never reached the disk
    final = staging / name
    os.replace(part, final)
    try:
        staged = unpack(final, staging, extract, run, fmt, guard)
        if not staged:
            raise SourceCorrupted(f"corrupted remote source: no usable file in {name}")
        for f in staged:
            check_source_file(f, fmt, f"{name} → {f.name}" if f.name != name else name)
    except (SourceCorrupted, DiskFull):  # nothing half-extracted is left behind
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
        "final_url": final_url,
        "files": [str(p.relative_to(dest)).replace("\\", "/") for p in produced],
        "etag": etag,
        "last_modified": last_modified,
        "remote_date": remote_date,
        "downloaded_bytes": size,
        "sha256": digest.hexdigest(),
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


def _fingerprint_stat(files: list[Path], config_hash: str, extra: str = "") -> str:
    """Fingerprint from the name, size and modification time of the files (databases of hundreds of GB)."""
    h = hashlib.sha256((config_hash + extra).encode())
    for f in files:
        st = f.stat()
        h.update(f"{f.name}|{st.st_size}|{st.st_mtime_ns}".encode())
    return h.hexdigest()


FULL_HASH_MAX = 1 << 30  # larger files are fingerprinted from their size and modification time (not read again)


def _fingerprint(files: list[Path], config_hash: str) -> str:
    h = hashlib.sha256(config_hash.encode())
    for f in files:
        h.update(f.name.encode())
        st = f.stat()
        if st.st_size > FULL_HASH_MAX:
            h.update(f"|{st.st_size}|{st.st_mtime_ns}".encode())
            continue
        with f.open("rb") as fh:
            h.update(hashlib.file_digest(fh, "sha256").digest())
    return h.hexdigest()


def _fingerprint_downloads(sources: list[dict[str, Any]], config_hash: str) -> str | None:
    """Fingerprint from the hashes computed while downloading (no file read again), None without them."""
    if not sources or not all(s.get("sha256") for s in sources):
        return None
    return hashlib.sha256((config_hash + "".join(s["sha256"] for s in sources)).encode()).hexdigest()


# --------------------------------------------------------------------------- pipeline

def _connect(settings: Settings, paths: RefPaths, fmt: str, key: tuple[str, str] | None = None,
             large_source: bool = False, read_keys: list[tuple[str, str]] | None = None) -> duckdb.DuckDBPyConnection:
    from .resources import ingest_limits, temp_limit

    con = duckdb.connect()
    if key:
        # Writing encrypted Parquet needs the OpenSSL crypto of httpfs (the built-in one can only decrypt)
        con.execute("LOAD httpfs")
        con.execute(f"PRAGMA add_parquet_key({lit(key[0])}, {lit(key[1])})")
    for source_key in read_keys or []:  # confidential sources of a unified referential
        if source_key != key:
            con.execute(f"PRAGMA add_parquet_key({lit(source_key[0])}, {lit(source_key[1])})")
    con.execute(f"SET temp_directory = {lit(str(paths.work / 'duckdb'))}")
    # Spill files of a large sort: bounded by the free space minus the reserve (default: 90 % of the free space,
    # enough to fill the disk shared with the database and the other referentials)
    limit = temp_limit(settings, paths.work)
    if limit:
        con.execute(f"SET max_temp_directory_size = {lit(limit)}")
    memory, threads = ingest_limits(settings)
    con.execute(f"SET memory_limit = {lit(memory)}")
    con.execute(f"SET threads = {int(threads)}")
    if large_source:
        # The order of the rows is not kept (a sort orders them anyway): much less memory to copy GB of rows
        con.execute("SET preserve_insertion_order = false")
    if fmt == "xlsx":
        try:
            con.execute("LOAD excel")
        except duckdb.Error:
            con.execute("INSTALL excel; LOAD excel")
    return con


class _Watch(threading.Event):
    """Set to stop watching; `disk_full` holds the message when the query was stopped because the disk was filling."""

    disk_full: str | None = None


def _interrupt_on_cancel(con: duckdb.DuckDBPyConnection, cancelled: Callable[[], bool],
                         disk: tuple[Path, int] | None = None) -> _Watch:
    """Interrupt the running DuckDB query as soon as the run is cancelled, or when the free space of `disk` (folder,
    reserve in bytes) falls below the reserve: the file written by a COPY is not bounded by max_temp_directory_size.
    Set the returned event to stop watching."""
    from .resources import fmt_gb, free_disk

    done = _Watch()

    def watch() -> None:
        while not done.wait(0.5):
            if cancelled():
                con.interrupt()
                return
            if disk and disk[1]:
                free = free_disk(disk[0])
                if free < disk[1]:
                    done.disk_full = (f"disk almost full while writing the new version ({fmt_gb(free)} free, reserve "
                                      f"{fmt_gb(disk[1])}, REFEX_MIN_FREE_DISK): stopped before filling it")
                    con.interrupt()
                    return

    threading.Thread(target=watch, daemon=True, name="cancel-watch").start()
    return done


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


PROFILE_SAMPLE_ROWS = 1_000_000


def _passthrough_source(ref: ReferentialConfig, groups: list[list[Path]]) -> Path | None:
    """The source when it can be published without being rewritten: a single Parquet file, without transform,
    reading options or encryption (published files are written with the layout and compression of the source)."""
    files = [f for g in groups for f in g]
    if ref.format != "parquet" or len(files) != 1 or ref.transform or ref.options or ref.confidential or ref.is_combined:
        return None
    return files[0]


def _make_room(settings: Settings, run: Run, paths: RefPaths, meta: dict[str, Any]) -> None:
    """Before a large update: when the free space (minus the reserve) cannot hold a new source and a new version of
    the sizes of the last ones, the previous version (kept for rollback) is removed first, instead of the update
    failing halfway."""
    from .resources import fmt_gb, free_disk, min_free_disk

    need = int(meta.get("raw_size") or 0) + int(meta.get("parquet_size") or 0)
    if need < (1 << 30) or not paths.previous.exists():
        return
    room = free_disk(paths.root) - min_free_disk(settings)
    if room >= need:
        return
    st = paths.previous.stat()
    freed = st.st_size if st.st_nlink == 1 else 0  # a hard link to the source frees nothing
    paths.previous.unlink(missing_ok=True)
    meta["has_previous"] = False
    run.log(f"Not enough free disk for the update (about {fmt_gb(need)} needed, {fmt_gb(max(room, 0))} usable): "
            f"previous version removed ({fmt_gb(freed)} freed)", "warn")


RECOMPRESS_SAMPLE_ROWS = 1_000_000


def _keep_or_recompress(con: duckdb.DuckDBPyConnection, settings: Settings, run: Run, source: Path) -> Path | None:
    """A large Parquet source is published as is only when it is well compressed. Uncompressed or barely compressed
    data (e.g. the THC passive DNS: 91 GB once its .gz is decompressed) is rewritten with zstd, often 3 to 5 times
    smaller and faster to query, when the disk can hold the copy. The size of the copy is measured on a sample of rows.
    Returns the source to publish as is, None to rewrite it."""
    from .resources import fmt_gb, free_disk, min_free_disk

    src = lit(str(source))
    try:
        stats = con.execute(f"SELECT compression, sum(total_compressed_size), sum(total_uncompressed_size) "
                            f"FROM parquet_metadata({src}) GROUP BY ALL").fetchall()
        rows = con.execute(f"SELECT coalesce(sum(num_rows), 0) FROM parquet_file_metadata({src})").fetchone()[0]
    except duckdb.Error:
        return source
    stored, plain = sum(r[1] or 0 for r in stats), sum(r[2] or 0 for r in stats)
    codecs = ", ".join(sorted({str(r[0]) for r in stats}))
    if not rows or not plain or stored / plain < 0.75:  # compressed: published as is
        return source
    size = source.stat().st_size
    n = min(rows, RECOMPRESS_SAMPLE_ROWS)
    sample = source.parent / f".{source.name}.sample.parquet"
    try:
        con.execute(f"COPY (SELECT * FROM read_parquet({src}) LIMIT {n}) TO {lit(str(sample))} (FORMAT parquet, COMPRESSION zstd)")
        estimate = int(sample.stat().st_size * rows / n)
    except duckdb.Error:
        return source
    finally:
        sample.unlink(missing_ok=True)
    if estimate > size * 0.8:
        run.log(f"Parquet source stored with {codecs}: zstd would barely reduce it, published as is")
        return source
    room = free_disk(source.parent) - min_free_disk(settings)
    if room < estimate * 1.2:
        run.log(f"Parquet source stored with {codecs} ({fmt_gb(size)}): it would take about {fmt_gb(estimate)} with zstd, "
                f"but only {fmt_gb(max(room, 0))} can be used: published as is (slower queries). Free some space to "
                "have it rewritten at the next update", "warn")
        return source
    run.log(f"Parquet source stored with {codecs} ({fmt_gb(size)}): rewritten with zstd, about {fmt_gb(estimate)} "
            "(smaller, faster queries)")
    return None


def _sorted_row_groups(con: duckdb.DuckDBPyConnection, path: str, column: str) -> bool:
    """True when the row groups of the file follow each other without overlapping on `column` (min / max statistics
    of the footer, no scan): lookups on it then read only the matching row groups, as after a sort."""
    try:
        col_type = {r[0]: r[1] for r in con.execute(f"DESCRIBE SELECT * FROM read_parquet({lit(path)})").fetchall()}.get(column)
        if col_type is None:
            return False
        bounds = (f"try_cast(coalesce(stats_min_value, stats_min) AS {col_type}) AS lo, "
                  f"try_cast(coalesce(stats_max_value, stats_max) AS {col_type}) AS hi")
        groups, missing, overlaps = con.execute(
            f"SELECT count(*), count(*) FILTER (WHERE lo IS NULL OR hi IS NULL), count(*) FILTER (WHERE lo < prev_hi) "
            f"FROM (SELECT lo, hi, lag(hi) OVER (ORDER BY row_group_id) AS prev_hi FROM "
            f"(SELECT row_group_id, {bounds} FROM parquet_metadata({lit(path)}) WHERE path_in_schema = {lit(column)}))"
        ).fetchone()
    except duckdb.Error:
        return False
    # A single row group says nothing about the order of its rows (and costs little to rewrite)
    return groups > 1 and not missing and not overlaps


def _link_or_copy(source: Path, target: Path, link: bool) -> str:
    """Hard link (no copy, same volume), else copy. The published file is never modified in place."""
    if link:
        try:
            os.link(source, target)
            return "hard link"
        except OSError:
            pass
    shutil.copyfile(source, target)
    return "copy"


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


class _NothingToMerge(Exception):
    """The delta holds no row: the published version stays as it is."""


def _merge_into_published(con: duckdb.DuckDBPyConnection, ref: ReferentialConfig, run: Run, paths: RefPaths, tmp: Path,
                          new_key: str | None, old_key: str | None, copy_opts: str, base: bool = False) -> dict[str, Any] | None:
    """Merge the data set just written to `tmp` (a delta) into the published version, then put the result in `tmp`.
    With a key, the rows of the delta replace the published rows of the same key (and the rows flagged in
    merge.delete_column remove them); without key, the rows are added, identical rows kept once. New columns are added
    (empty for the older rows). Returns {rows: rows of the delta, deleted: rows flagged for deletion}.
    `base`: the data set is published alone (first one, or replace): only the rows flagged for deletion are left out."""
    assert ref.merge is not None
    delta = parquet_reader(str(tmp), new_key)
    columns = {r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {delta}").fetchall()}
    key, flag_col = ref.key, ref.merge.delete_column
    flagged = bool(flag_col and flag_col in columns)
    if base:
        if not flagged:
            return None
        sql = f"SELECT * EXCLUDE ({qi(flag_col)}) FROM {delta} WHERE NOT coalesce(TRY_CAST({qi(flag_col)} AS BOOLEAN), false)"
        what = "flagged rows of the data set left out"
    else:
        rows = con.execute(f"SELECT count(*) FROM {delta}").fetchone()[0]
        if rows == 0:
            raise _NothingToMerge()
        if key and key not in columns:
            raise RunRejected(f"the delta has no '{key}' column (key of the referential): it cannot be merged")
        current = parquet_reader(str(paths.current), old_key)
        deleted = 0
        if flagged:
            flag = f"coalesce(TRY_CAST({qi(flag_col)} AS BOOLEAN), false)"
            added = f"SELECT * EXCLUDE ({qi(flag_col)}) FROM {delta} WHERE NOT {flag}"
            deleted = con.execute(f"SELECT count(*) FROM {delta} WHERE {flag}").fetchone()[0]
        else:
            added = f"SELECT * FROM {delta}"
        if key:  # upsert: the published rows whose key is in the delta (replaced or deleted) are left out
            sql = f"SELECT o.* FROM {current} o ANTI JOIN {delta} d ON o.{qi(key)} = d.{qi(key)} UNION ALL BY NAME {added}"
        else:
            sql = f"SELECT * FROM {current} UNION BY NAME {added}"
        what = (f"Delta merged into the published version: {rows:,} row(s)" + (f", {deleted:,} flagged for deletion" if deleted else "")
                + (f" (upsert on '{key}')" if key else " (added, identical rows kept once)"))
    if ref.storage.sort_by:
        sql = f"SELECT * FROM ({sql}) ORDER BY {', '.join(qi(c) for c in ref.storage.sort_by)}"
    out = paths.work / "next.merged.parquet"
    try:
        con.execute(f"COPY ({sql}) TO {lit(str(out))} ({copy_opts})")
    except duckdb.InterruptException:
        raise
    except duckdb.Error as e:
        out.unlink(missing_ok=True)
        raise RunRejected(f"the delta cannot be merged into the published version: {e}") from e
    os.replace(out, tmp)
    run.log(what[0].upper() + what[1:])
    return None if base else {"rows": rows, "deleted": deleted}


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


def _copy_with_fallbacks(con: duckdb.DuckDBPyConnection, ref: ReferentialConfig, run: Run, sql: str,
                         rebuild: Callable[[dict[str, Any]], str], target: str, out: Path) -> str:
    """Write the data set, retrying with the reader options that recover from a read error (a spreadsheet
    column typed from its first value, nested keys differing only by case). Returns the SQL actually used."""
    try:
        con.execute(f"COPY ({sql}) {target}")
        return sql
    except duckdb.Error as e:
        if isinstance(e, (duckdb.InterruptException, duckdb.OutOfMemoryException)):
            raise
        retry = read_fallbacks(str(e), ref.format, ref.options)
        if not retry:
            raise
        for extra in retry:
            out.unlink(missing_ok=True)  # the failed COPY may have left a partial file
            try:
                sql = rebuild(extra)
                con.execute(f"COPY ({sql}) {target}")
            except duckdb.Error:
                if extra is retry[-1]:
                    raise
                continue
            run.log(fallback_reason(extra), "warn")
            return sql
        raise


def execute_run(
    ref: ReferentialConfig,
    settings: Settings,
    run: Run,
    cancelled: Callable[[], bool] = lambda: False,
    manual: dict[str, Any] | None = None,
    internal_rows: Callable[[str], list[dict[str, Any]]] | None = None,
    combine_parts: Callable[[ReferentialConfig], list[Any]] | None = None,
) -> dict[str, Any]:
    """Run the full pipeline. Returns the new meta. Raises on failure."""
    paths = RefPaths(settings.data_dir, ref.id).ensure()
    meta = read_meta(paths)
    run.retries = ref.source.retries if ref.source.retries is not None else settings.download_retries
    run.started_at = utcnow()
    run.status = "running"
    if cancelled():  # cancelled while waiting in the queue
        raise RunCancelled()

    _make_room(settings, run, paths, meta)

    # 1. Acquire source files ------------------------------------------------
    run.phase = "download"
    groups: list[list[Path]] = []
    sources: list[dict[str, Any]] = []
    combined: list[Any] = []
    if ref.is_combined:
        # Unified referential: the published data of the other referentials, read where it already is
        from .combine import CombineError, describe

        if combine_parts is None:
            raise RunRejected("a unified referential can only be built by the service")
        try:
            combined = combine_parts(ref)
        except CombineError as e:
            raise RunRejected(str(e)) from e
        groups = [[p.path] for p in combined]
        sources = describe(combined)
        cfg = ref.combine
        how = "enriched with" if cfg and cfg.mode == "enrich" else "union of"
        run.log(f"Unified referential ({how} {len(combined)} referential(s)): "
                + ", ".join(f"{p.id} v{p.version} ({p.row_count:,} rows)" if p.row_count else f"{p.id} v{p.version}"
                            for p in combined))
    elif ref.is_internal:
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
        if ref.is_sqlite and ref.incremental:  # a full database and / or deltas, applied to the kept database
            groups, _ = _acquire_sqlite_base(ref, settings, run, paths, meta, cancelled, manual_files=sorted(files))
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
    elif ref.source.type == "http" and ref.incremental and ref.is_bloom:
        return _run_bloom_incremental(ref, settings, run, paths, meta, cancelled)
    elif ref.source.type == "http" and ref.incremental and ref.is_sqlite:
        groups, sources = _acquire_sqlite_base(ref, settings, run, paths, meta, cancelled)
    elif ref.source.type == "http":
        # A changed configuration (transform, confidentiality...) needs the files again: no conditional request
        # (HTTP 304) when the downloaded files were not kept
        config_changed = bool(meta.get("config_hash")) and meta["config_hash"] != ref.config_hash
        previous_sources = {} if config_changed else {s.get("url"): s for s in meta.get("sources", [])}
        with http_client(settings) as client:
            for url in ref.source.urls:
                entry = download(client, url, paths.raw, ref.source.request_headers(url), ref.source.extract, previous_sources.get(url), run, cancelled, ref.format, settings)
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
    # Downloaded, imported and already published files (a unified referential) are checked when they are produced
    if ref.source.type not in ("http", "internal", "referential") and manual is None:
        for f in all_files:
            check_source_file(f, ref.format)
    if ref.is_sqlite:
        groups = [kept for kept in ([f for f in g if is_sqlite_file(f)] for g in groups) if kept]
        all_files = [f for g in groups for f in g]
        if not all_files:
            raise SourceCorrupted("corrupted source: no SQLite database in the source files")
    run.stats["downloaded_bytes"] = run.bytes_done
    if ref.is_combined:
        # The sources are published versions: nothing to read again to know whether they changed
        versions = "".join(f"{p.id}:{p.version}:{p.updated_at}" for p in combined)
        fingerprint = hashlib.sha256((ref.config_hash + versions).encode()).hexdigest()
    elif ref.is_sqlite:
        state = meta.get("incremental") or {}
        fingerprint = _fingerprint_stat(all_files, ref.config_hash, orjson.dumps(state.get("applied") or []).decode())
    else:
        downloaded = ref.source.type == "http" and manual is None and not ref.is_internal
        fingerprint = (downloaded and _fingerprint_downloads(sources, ref.config_hash)) or _fingerprint(all_files, ref.config_hash)
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
    st = ref.storage

    def source_sql(extra: dict[str, Any] | None = None) -> str:
        """The SELECT producing the data set; `extra` adds reader options (read_fallbacks)."""
        if ref.is_combined:
            from .combine import build_sql

            sql = build_sql(ref, combined)
        elif ref.is_internal:
            sql = internal_sql(ref, all_files[0], sources[0]["records"])
        else:
            sql = build_source_sql(ref.format, [[str(f) for f in g] for g in groups], {**ref.options, **(extra or {})}, ref.transform)
        if st.sort_by:
            sql = f"SELECT * FROM ({sql}) ORDER BY {', '.join(qi(c) for c in st.sort_by)}"
        return sql

    sql = source_sql()
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
    large_source = meta["raw_size"] > LARGE_SOURCE_BYTES
    con = _connect(settings, paths, ref.format, pkey, large_source,
                   read_keys=[p.key for p in combined if p.key] or None)
    if large_source:
        memory, threads = (con.execute(f"SELECT current_setting('{k}')").fetchone()[0] for k in ("memory_limit", "threads"))
        run.log(f"Large source ({meta['raw_size'] / (1 << 30):.1f} GB): DuckDB limited to {memory} of memory and {threads} "
                "thread(s), beyond which it works on disk")
    if ref.is_sqlite:
        attach_sqlite(con, [str(f) for f in all_files])
    index_tmp: dict[str, Path] = {}
    quality = None
    # Deltas: merged into the published version, unless the data set is published alone (replace: new base)
    merging = ref.is_merge and paths.current.exists() and not run.replace
    merged: dict[str, Any] | None = None
    if ref.is_merge and not merging:
        run.log("Merged referential: " + ("the data set replaces the published version (new base)" if run.replace
                                          else "first data set, published as the base of the next deltas"))
    from .resources import min_free_disk

    # A COPY of billions of rows stops at once when cancelled, or before it fills the disk
    watching = _interrupt_on_cancel(con, cancelled, (paths.work, min_free_disk(settings)))
    built = False  # files of a failed build (tens of GB of next.parquet) are not left in work/
    published_as_is = False
    try:
        t0 = time.monotonic()
        source = _passthrough_source(ref, groups)
        if source is not None and st.sort_by and not _sorted_row_groups(con, str(source), st.sort_by[0]):
            run.log(f"The Parquet source is not sorted on '{st.sort_by[0]}': it is rewritten sorted (long for billions of rows)")
            source = None
        if source is not None and large_source:
            source = _keep_or_recompress(con, settings, run, source)
        published_as_is = source is not None
        if source is not None:
            # A single Parquet file, nothing to change: published as is (hard link, else copy), not rewritten row by row
            # Files of RefExposer (downloads, imports) are linked; files of the user are copied, as they may be rewritten in place
            how = _link_or_copy(source, tmp, link=manual is not None or ref.source.type in ("http", "git"))
            run.log(f"Parquet source published as is ({how}" + (f", already sorted on '{st.sort_by[0]}'" if st.sort_by else "") + ")")
        else:
            run.log("Transforming and writing parquet" + (f" (sorted on {', '.join(st.sort_by)})" if st.sort_by else ""))
            sql = _copy_with_fallbacks(con, ref, run, sql, source_sql, f"TO {lit(str(tmp))} ({copy_opts})", tmp)
        if merging:  # the data set is a delta: merged into the published version
            merged = _merge_into_published(con, ref, run, paths, tmp, new_key, pkey[0] if was_encrypted else None, copy_opts)
        elif ref.is_merge:  # published alone: the rows flagged for deletion are still left out
            _merge_into_published(con, ref, run, paths, tmp, new_key, None, copy_opts, base=True)
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

        # Data quality: one scan for every rule; a failed 'error' rule rejects the version
        run.phase = "quality"
        quality = evaluate_quality(con, rel, col_names, ref.quality, ref.key, examples=not ref.confidential, large=large,
                                   sample=parquet_sample(str(tmp), new_key, rows, QUALITY_SAMPLE_ROWS, st.row_group_size))
        if quality:
            quality["checked_at"] = iso(utcnow())
            run.stats["quality"] = {"score": quality["score"], "status": quality["status"]}
            run.log(f"Quality: score {quality['score']}/100 ({quality['status']}), {len(quality['checks'])} check(s) "
                    f"in {quality['duration']}s" + (" on a sample (large referential)" if quality.get("sampled") else ""))
            failed = [c for c in quality["checks"] if not c["ok"]]
            for c in failed:
                detail = c.get("error") or f"{c['pass_pct']}% of the rows pass, {c['threshold']}% required ({c['failing']:,} failing)"
                run.log(f"Quality rule '{c['name']}' ({c['severity']}): {detail}", "warn" if c["severity"] == "warning" else "error")
            if quality["status"] == "failed" and not ref.is_internal:
                blocking = [c["name"] for c in failed if c["severity"] == "error"]
                raise RunRejected(f"quality rule(s) not met: {', '.join(blocking)}")

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
                if mode == "full" or rows <= PROFILE_SAMPLE_ROWS:
                    source, sampled = rel, ""
                else:
                    # Blocks of rows picked at random: only they are read (a sample of single rows reads the whole file)
                    source = parquet_sample(str(tmp), new_key, rows, PROFILE_SAMPLE_ROWS, st.row_group_size)
                    sampled = f" on a sample of about {PROFILE_SAMPLE_ROWS:,} rows"
                profile = _profile(con, source)
                run.log(f"Column profile computed{sampled} in {time.monotonic() - t1:.1f}s")
            except duckdb.Error as e:
                run.log(f"Cannot compute the column profile: {e}", "warn")
        built = True
    except duckdb.InterruptException as e:
        if watching.disk_full:
            raise RunRejected(watching.disk_full) from e
        raise RunCancelled() from e
    except duckdb.OutOfMemoryException as e:
        if "max_temp_directory_size" in str(e):  # spill files reached the free space minus the reserve
            from .resources import fmt_gb, free_disk, min_free_disk

            raise RunRejected(f"not enough disk space for the work files of DuckDB ({fmt_gb(free_disk(paths.work))} free, "
                              f"reserve {fmt_gb(min_free_disk(settings))}): stopped before filling the disk. A sort or an "
                              "index of billions of rows needs about the size of the uncompressed data") from e
        raise
    except _NothingToMerge:
        shutil.rmtree(paths.work, ignore_errors=True)
        run.log("Empty delta: nothing to merge, the published version is kept")
        run.status = "unchanged"
        run.stats.update({"rows": meta.get("row_count")})
        meta["fingerprint"] = fingerprint  # the same empty data set is not read again
        return meta
    finally:
        watching.set()
        con.close()
        if not built:
            shutil.rmtree(paths.work, ignore_errors=True)
    if cancelled():  # interrupted in a step that tolerates errors (changes, profile): nothing is published
        shutil.rmtree(paths.work, ignore_errors=True)
        raise RunCancelled()

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
    # A large source that is not the published file itself (decompressed, rewritten, transformed) would double the disk
    # used, and triple it during the next update (old source, new source, new version): not kept above keep_raw_max
    from .resources import fmt_gb, keep_raw_max

    too_big = (not published_as_is and keep_raw_max(settings) and meta["raw_size"] > keep_raw_max(settings)
               and ref.source.type in ("http", "git"))
    if (not st.keep_raw or ref.confidential or too_big) and ref.source.type in ("http", "internal", "git"):
        shutil.rmtree(paths.raw, ignore_errors=True)
        paths.raw.mkdir(parents=True, exist_ok=True)
        for s in sources:
            s["discarded"] = True
        if ref.source.type == "http":
            why = ("confidential" if ref.confidential else "storage.keep_raw: false" if not st.keep_raw
                   else f"{fmt_gb(meta['raw_size'])}, more than REFEX_KEEP_RAW_MAX")
            run.log(f"Source files deleted after import ({why})")
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
        "quality": quality,
    })
    if ref.is_merge:  # deltas merged since the base (the last data set published alone)
        before = meta.get("merge") or {}
        meta["merge"] = {
            "base_at": before.get("base_at") if merged else now,
            "deltas": int(before.get("deltas") or 0) + 1 if merged else 0,
            "last_delta_rows": merged["rows"] if merged else rows,
            "last_delta_deleted": merged["deleted"] if merged else 0,
            "last_merged_at": now if merged else before.get("last_merged_at"),
        }
        run.stats["merge"] = {"delta_rows": meta["merge"]["last_delta_rows"], "merged": bool(merged)}
    else:
        meta.pop("merge", None)
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


def _run_bloom_incremental(ref: ReferentialConfig, settings: Settings, run: Run, paths: RefPaths, meta: dict[str, Any],
                           cancelled: Callable[[], bool]) -> dict[str, Any]:
    """Bloom filter updated by deltas: the full filter when it changed (checked at most every `full_every`),
    then the changed deltas added to a copy of the published filter. Inserting a value twice changes nothing,
    so a delta already contained in a new full filter is simply applied again."""
    inc = ref.incremental
    assert inc is not None
    state = dict(meta.get("incremental") or {})
    same_config = meta.get("config_hash") == ref.config_hash
    previous = {s.get("url"): s for s in meta.get("sources", [])} if paths.bloom.exists() and same_config else {}
    last_check = parse_iso(state.get("full_checked_at"))
    url = ref.source.urls[0]
    full_due = (run.force or not previous or not inc.full_every or last_check is None
                or utcnow() - last_check >= parse_duration(inc.full_every))
    work = paths.work / "incremental"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    headers = ref.source.request_headers  # per URL: a secret may be restricted to some hosts
    delta_fmt = "bloom" if inc.format == "bloom" else "txt"

    new_full: Path | None = None
    full_entry = previous.get(url) or {"url": url}
    deltas: list[tuple[dict[str, Any], Path]] = []
    try:
        with http_client(settings) as client:
            if full_due:
                full_entry = download(client, url, paths.raw, headers(url), ref.source.extract, previous.get(url), run, cancelled, "bloom", settings)
                state["full_checked_at"] = iso(utcnow())
                if not full_entry.get("not_modified"):
                    files = [paths.raw / f for f in full_entry["files"]]
                    if len(files) != 1:
                        raise RunRejected(f"a single Bloom filter file is expected ({len(files)} found)")
                    new_full = files[0]
            else:
                run.log(f"Full filter not checked (at most every {inc.full_every}): deltas only")
            for i, durl in enumerate(inc.urls):
                # After a new full filter, every delta is applied again: no conditional request
                prev = previous.get(durl) if new_full is None else None
                dest = work / f"delta{i}"
                entry = download(client, durl, dest, headers(durl), None, prev, run, cancelled, delta_fmt, settings)
                deltas.append(({**entry, "delta": True}, dest))
    except Exception:
        shutil.rmtree(work, ignore_errors=True)
        raise

    changed = [(e, d) for e, d in deltas if not e.get("not_modified")]
    sources = [full_entry, *(e for e, _ in deltas)]
    meta["last_checked_at"] = iso(utcnow())
    if new_full is None and not changed:
        shutil.rmtree(work, ignore_errors=True)
        run.log("Full filter and deltas unchanged: the published version is kept")
        run.status = "unchanged"
        run.stats.update({"rows": meta.get("row_count")})
        meta["sources"] = sources
        meta["incremental"] = state
        return meta

    # Work on a copy: the published filter keeps answering lookups until the new one replaces it
    run.phase = "build"
    target = work / "current.bloom"
    added = 0
    try:
        if new_full is not None:
            os.replace(new_full, target)
        else:
            run.log(f"Copying the published filter ({paths.bloom.stat().st_size:,} bytes) to apply {len(changed)} delta(s)")
            shutil.copyfile(paths.bloom, target)
        try:
            bf = BloomFilter(target, writable=True)
        except (BloomError, OSError) as e:
            raise RunRejected(f"unreadable Bloom filter: {e}") from e
        mode = ref.options.get("normalize", "upper")
        pattern = re.compile(ref.options["pattern"]) if ref.options.get("pattern") else None
        try:
            for entry, dest in changed:
                before = bf.count
                for f in (dest / name for name in entry["files"]):
                    if inc.format == "bloom":
                        try:
                            delta = BloomFilter(f)
                        except (BloomError, OSError) as e:
                            raise SourceCorrupted(f"corrupted delta: {entry['url']} is not a Bloom filter ({e})") from e
                        try:
                            bf.merge(delta)
                        except BloomError as e:
                            raise RunRejected(f"delta {entry['url']}: {e}") from e
                        finally:
                            delta.close()
                    else:
                        skipped = _add_values(bf, f, mode, pattern, cancelled)
                        if skipped:
                            run.log(f"{f.name}: {skipped:,} line(s) ignored (do not match the expected values)", "warn")
                entry["added"] = bf.count - before
                entry["discarded"] = True  # revalidated with its ETag next time
                added += entry["added"]
                run.log(f"Delta {entry['url']}: {entry['added']:,} new element(s)")
            bf.flush()
            info = bf.info()
        finally:
            bf.close()
        max_fp = inc.max_fp_rate or (info["target_fp_rate"] or 0.0) * 10
        if max_fp and info["estimated_fp_rate"] > max_fp:
            raise RunRejected(f"filter saturated: estimated false positive rate {info['estimated_fp_rate']:.1e} "
                              f"> {max_fp:.1e} ({info['elements']:,} elements for a capacity of {info['capacity']:,}); "
                              "a new full filter, sized for more elements, is needed")
        fingerprint = _fingerprint([target], ref.config_hash)
        meta = _publish_bloom(ref, run, paths, meta, [target], sources, fingerprint)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    _cleanup_raw(paths, set())

    now = iso(utcnow())
    if new_full is not None:
        state.update({"full_fetched_at": now, "added_since_full": added, "deltas_since_full": len(changed)})
        run.log(f"New full filter, {len(changed)} delta(s) applied on top ({added:,} new element(s))")
    else:
        state["added_since_full"] = int(state.get("added_since_full") or 0) + added
        state["deltas_since_full"] = int(state.get("deltas_since_full") or 0) + len(changed)
    if changed:
        state["last_delta_at"] = now
    meta["incremental"] = state
    run.stats.update({"added": added, "full": new_full is not None})
    return meta


def _add_values(bf: BloomFilter, path: Path, mode: str, pattern: re.Pattern | None, cancelled: Callable[[], bool]) -> int:
    """Insert the values of a delta file (one per line, # comments). Returns the number of ignored lines."""
    skipped = 0
    opener = gzip.open if path.name.lower().endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f):
            if i % 100_000 == 0 and cancelled():
                raise RunCancelled()
            value = line.strip()
            if not value or value.startswith("#"):
                continue
            value = bloom_normalize(value, mode)
            if pattern and not pattern.fullmatch(value):
                skipped += 1
                continue
            bf.add(value)
    return skipped


_TX_CONTROL = re.compile(r"^\s*(BEGIN|COMMIT|END|ROLLBACK)\b", re.I)
_INSERT = re.compile(r"^\s*INSERT\b", re.I)


def _sql_statements(path: Path):
    """Statements of a SQL file, read as a stream (a delta can hold GB of INSERT statements).
    Yields (line number, statement). sqlite3 shell commands (.read, .bail...) are skipped."""
    import sqlite3

    buf = ""
    with path.open(encoding="utf-8-sig") as f:
        for lineno, line in enumerate(f, 1):
            if not buf and (not line.strip() or line.lstrip().startswith((".", "--"))):
                continue
            buf += line
            if ";" not in line:
                continue
            # Usually one statement per line; a line may also hold several, or end a multi-line statement
            start = 0
            for i, ch in enumerate(buf):
                if ch == ";" and sqlite3.complete_statement(buf[start:i + 1]):
                    stmt = buf[start:i + 1].strip()
                    if stmt != ";":
                        yield lineno, stmt
                    start = i + 1
            buf = buf[start:] if buf[start:].strip() else ""
    if buf.strip():
        raise RunRejected(f"delta {path.name}: the last statement is incomplete (truncated file?)")


def apply_sql_delta(db: Path, sql_file: Path, run: Run, cancelled: Callable[[], bool] = lambda: False,
                    cache_kib: int = 262_144, origin: str | None = None) -> int:
    """Run the statements of a SQL delta (INSERT / UPDATE / DELETE, e.g. NIST NSRL RDSv3) in the database, in a
    single transaction: on any error or cancellation nothing is applied. Returns the number of statements.
    An INSERT of a row already in the database (UNIQUE constraint) is skipped: the row is there. With `origin`
    (NIST NSRL mode), the inserted rows are recorded as coming from this publication (nsrl.track_origin).

    The transaction runs in WAL mode: the changed pages are appended to the -wal file instead of copying every
    original page to a rollback journal first (4 to 5 times faster on a database of tens of GB), and it stays atomic,
    also when the service is killed (the uncommitted frames are ignored). The database goes back to the usual
    journal at the end, which writes the pages into it."""
    import sqlite3

    con = sqlite3.connect(db, isolation_level=None)
    count = duplicates = 0
    started = time.monotonic()
    try:
        con.execute(f"PRAGMA cache_size = -{int(cache_kib)}")
        con.execute("PRAGMA journal_mode = WAL")
        con.execute("PRAGMA synchronous = NORMAL")
        con.execute("BEGIN IMMEDIATE")
        if origin:
            from .nsrl import track_origin

            track_origin(con, origin)
        for lineno, stmt in _sql_statements(sql_file):
            if _TX_CONTROL.match(stmt):  # the transaction of the delta file is replaced by ours
                continue
            try:
                con.execute(stmt)
            except sqlite3.IntegrityError as e:
                if not (_INSERT.match(stmt) and "UNIQUE constraint failed" in str(e)):
                    raise RunRejected(f"delta {sql_file.name}, line {lineno}: {e} (nothing applied)") from e
                duplicates += 1
            except sqlite3.Error as e:
                raise RunRejected(f"delta {sql_file.name}, line {lineno}: {e} (nothing applied)") from e
            count += 1
            if count % 200_000 == 0:
                if cancelled():
                    raise RunCancelled()
                run.log(f"{sql_file.name}: {count:,} statements applied ({count / (time.monotonic() - started):,.0f}/s)")
        con.execute("COMMIT")
    except BaseException:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise
    finally:
        try:
            con.execute("PRAGMA journal_mode = DELETE")  # checkpoint: the database file is complete again
        finally:
            con.close()
    run.log(f"Delta {sql_file.name}: {count:,} statements applied in {time.monotonic() - started:.1f}s"
            + (f" ({duplicates:,} rows already in the database, skipped)" if duplicates else ""))
    return count


def _delta_sql_files(files: list[Path]) -> list[Path]:
    """SQL files of a delta, in name order; the schema shipped with the NSRL deltas is not a delta."""
    return sorted(f for f in files if f.suffix.lower() == ".sql" and "schema" not in f.name.lower())


class DeltaOutOfSequence(RunRejected):
    """A chained delta (NIST NSRL) made for another release than the one of the database."""


def apply_chained_delta(db: Path, sql_file: Path, run: Run, cancelled: Callable[[], bool] = lambda: False,
                        cache_kib: int = 262_144, origin: str | None = None) -> int | None:
    """Apply a delta, checking its place in the chain of releases when it carries one (NIST NSRL: each delta brings
    the previous release to the next one). Returns the number of statements, None when the database already holds it."""
    from .nsrl import db_version, delta_versions, version_key

    before, after = delta_versions(sql_file)
    current = db_version(db) if before or after else None
    if current and after and version_key(after) <= version_key(current):
        run.log(f"Delta {sql_file.name} (release {after}) skipped: the database is already at release {current}")
        return None
    if current and before and before != current:
        raise DeltaOutOfSequence(f"delta {sql_file.name} updates release {before} to {after}, but the database is at release "
                                 f"{current}: the deltas must be applied in order, without gap (nothing applied)")
    n = apply_sql_delta(db, sql_file, run, cancelled, cache_kib, origin)
    if after:
        run.log(f"Database now at release {after}")
    return n


def _acquire_nsrl(ref: ReferentialConfig, settings: Settings, run: Run, paths: RefPaths, state: dict[str, Any],
                  new_base: Callable[[Path, dict[str, Any]], Path], save_state: Callable[[], None],
                  applied: list[dict[str, Any]], cancelled: Callable[[], bool]) -> list[dict[str, Any]]:
    """NIST NSRL: publications of the set found on the download page. The latest full database is loaded once, then
    each new quarterly delta is applied to it: no list to maintain. Returns the source entries."""
    from . import nsrl

    nsrl_set, page = ref.options["nsrl"], ref.source.urls[0]
    base_info = state.get("base") or {}
    base = paths.base / base_info["file"] if base_info.get("file") else None
    run.log(f"Reading the NIST download page: {page}")
    with http_client(settings) as client:
        try:
            fulls, deltas = nsrl.discover(client, page, nsrl_set, ref.source.request_headers(page),
                                          run.retries if run.retries is not None else DOWNLOAD_RETRIES, run.log)
        except nsrl.NsrlError as e:
            raise SourceCorrupted(str(e)) from e
    usable = base is not None and base.exists() and base_info.get("set") == nsrl_set and not run.force
    current = nsrl.db_version(base) if usable and base else None
    latest = max([*fulls, *deltas], key=nsrl.version_key)
    run.log(f"NIST NSRL '{nsrl_set}': latest release {latest}, latest full database {max(fulls, key=nsrl.version_key)}"
            + (f", kept database at release {current}" if current else ""))
    sources: list[dict[str, Any]] = []

    def load_full(version: str) -> Path:
        url = fulls[version]
        run.log(f"Full database {version} needed: downloading {url}")
        dest = paths.raw / "full"
        for old in dest.iterdir() if dest.exists() else ():
            if old.name != ".partial":  # interrupted download of the database: resumed
                shutil.rmtree(old, ignore_errors=True) if old.is_dir() else old.unlink(missing_ok=True)
        with http_client(settings) as client:
            entry = download(client, url, dest, ref.source.request_headers(url), ref.source.extract, None, run, cancelled, "sqlite", settings)
        dbs = [dest / f for f in entry["files"] if is_sqlite_file(dest / f)]
        if len(dbs) != 1:
            raise SourceCorrupted(f"corrupted source: a single SQLite database is expected in {url} ({len(dbs)} found)")
        kept = new_base(dbs[0], {"url": url, "set": nsrl_set, "etag": entry.get("etag"), "last_modified": entry.get("last_modified")})
        shutil.rmtree(dest, ignore_errors=True)
        sources.append({**entry, "files": [], "discarded": True})
        return kept

    for attempt in range(2):
        full, todo = nsrl.plan(fulls, deltas, current)
        if full:
            base = load_full(full)
        assert base is not None
        if todo:
            run.log(f"{len(todo)} delta(s) to apply: {', '.join(v for v, _ in todo)}")
        work = paths.work / "deltas"
        try:
            with http_client(settings) as client:
                for version, durl in todo:
                    shutil.rmtree(work, ignore_errors=True)
                    entry = download(client, durl, work, ref.source.request_headers(durl), None, None, run, cancelled, "sql", settings)
                    files = _delta_sql_files([work / f for f in entry["files"]])
                    if not files:
                        raise SourceCorrupted(f"corrupted delta: no SQL file in {durl}")
                    counts = [apply_chained_delta(base, f, run, cancelled, sqlite_cache_kib(settings), f.name) for f in files]
                    applied.append({"url": durl, "version": version, "file": ", ".join(f.name for f in files),
                                    "statements": sum(c or 0 for c in counts), "skipped": all(c is None for c in counts),
                                    "at": iso(utcnow())})
                    save_state()
                    sources.append({**entry, "files": [], "discarded": True, "delta": True})
            break
        except DeltaOutOfSequence as e:
            # A new full database only helps when it is more recent than the kept one (tens of GB downloaded)
            if attempt or full or not current or nsrl.version_key(max(fulls, key=nsrl.version_key)) <= nsrl.version_key(current):
                raise
            run.log(f"{e}: the latest full database is loaded again")
            current = None
        finally:
            shutil.rmtree(work, ignore_errors=True)
    if not sources:
        sources.append({"url": page, "files": [], "discarded": True, "base": base.name, "checked_at": iso(utcnow())})
    return sources


def _acquire_sqlite_base(ref: ReferentialConfig, settings: Settings, run: Run, paths: RefPaths, meta: dict[str, Any],
                         cancelled: Callable[[], bool], manual_files: list[Path] | None = None) -> tuple[list[list[Path]], list[dict[str, Any]]]:
    """SQLite database updated by SQL deltas (e.g. NIST NSRL RDSv3).

    The full database is kept in base/ and each delta is applied once, in order: `incremental.urls` lists the deltas
    to apply on top of the full database of `source.urls`. Adding a URL at the end applies only that delta; removing or
    reordering an applied delta (or forcing the update) loads the full database again, then applies the whole list.
    A manual import brings a full database (it replaces the kept one, the listed deltas are then applied on top) and /
    or deltas (.sql, or an archive holding one), applied once to the kept database.
    Returns the file groups to read (the kept database) and the source entries."""
    assert ref.incremental is not None
    state = dict(meta.get("incremental") or {})
    applied: list[dict[str, Any]] = list(state.get("applied") or [])
    base_info = state.get("base") or {}
    base = paths.base / base_info["file"] if base_info.get("file") else None
    url = ref.source.urls[0]
    applied_urls = [a["url"] for a in applied if a.get("url")]
    wanted = ref.incremental.urls

    def save_state() -> None:
        # Written at once: the kept database already holds the change, even if the rest of the run fails
        state["applied"] = applied
        meta["incremental"] = state
        on_disk = read_meta(paths)
        on_disk["incremental"] = state
        write_meta(paths, on_disk)

    def new_base(db: Path, origin: dict[str, Any]) -> Path:
        nonlocal base
        paths.base.mkdir(parents=True, exist_ok=True)
        target = paths.base / db.name
        for old in paths.base.iterdir():
            if old != target:
                old.unlink(missing_ok=True)
        shutil.move(db, target)  # the import folder may be another volume: copied then deleted
        base = target
        applied.clear()
        state["base"] = {**origin, "file": target.name, "size": target.stat().st_size, "loaded_at": iso(utcnow())}
        save_state()
        run.log(f"Full database kept: {target.name} ({target.stat().st_size:,} bytes)")
        return target

    sources: list[dict[str, Any]] = []
    manual_db = [f for f in manual_files or [] if is_sqlite_file(f)]
    manual_deltas = _delta_sql_files(manual_files or [])
    if manual_files is not None and not manual_db and not manual_deltas:
        raise RunRejected("nothing to import: give a SQLite database (.db) or a SQL delta (.sql, or an archive holding one)")
    if len(manual_db) > 1:
        raise RunRejected(f"a single full database is expected ({len(manual_db)} found)")

    nsrl_set = ref.options.get("nsrl")
    if manual_db:
        new_base(manual_db[0], {"url": None, "manual": True, "set": nsrl_set})
    if nsrl_set and (manual_files is None or manual_db):
        # NIST NSRL: the deltas newer than the database are found and applied (after a full database imported by hand too)
        sources = _acquire_nsrl(ref, settings, run, paths, state, new_base, save_state, applied, cancelled)
        base = paths.base / state["base"]["file"]
    elif nsrl_set:
        if base is None or not base.exists():
            raise RunRejected("no full database to apply the delta to: update the referential first (the full database is downloaded)")
    elif manual_db:
        pass
    else:
        reload = (run.force and manual_files is None) or base is None or not base.exists() \
            or (base_info.get("url") not in (None, url)) or applied_urls != wanted[:len(applied_urls)]
        if reload and manual_files is not None:
            raise RunRejected("no full database to apply the delta to: import the full database first, or update the referential")
        if reload:
            why = ("forced update" if run.force else "no database kept yet" if base is None or not base.exists()
                   else "the source changed" if base_info.get("url") not in (None, url) else "the list of deltas changed")
            run.log(f"Full database needed ({why}): downloading {url}")
            dest = paths.raw / "full"
            for old in dest.iterdir() if dest.exists() else ():
                if old.name != ".partial":  # interrupted download of the database: resumed
                    shutil.rmtree(old, ignore_errors=True) if old.is_dir() else old.unlink(missing_ok=True)
            with http_client(settings) as client:
                entry = download(client, url, dest, ref.source.request_headers(url), ref.source.extract, None, run, cancelled, "sqlite", settings)
            dbs = [dest / f for f in entry["files"] if is_sqlite_file(dest / f)]
            if len(dbs) != 1:
                raise SourceCorrupted(f"corrupted source: a single SQLite database is expected in {url} ({len(dbs)} found)")
            new_base(dbs[0], {"url": url, "etag": entry.get("etag"), "last_modified": entry.get("last_modified")})
            shutil.rmtree(dest, ignore_errors=True)
            sources.append({**entry, "files": [], "discarded": True})
    if not sources:
        sources.append({"url": url if not base_info.get("manual") else None, "files": [], "discarded": True,
                        "base": (state.get("base") or {}).get("file"), "checked_at": iso(utcnow())})
    assert base is not None

    # Deltas imported by hand: applied once
    for f in manual_deltas:
        size = f.stat().st_size
        if any(a.get("file") == f.name and a.get("size") == size for a in applied):
            raise RunRejected(f"the delta {f.name} has already been applied to this database")
        n = apply_chained_delta(base, f, run, cancelled, sqlite_cache_kib(settings), f.name if nsrl_set else None)
        applied.append({"url": None, "file": f.name, "size": size, "statements": n or 0, "skipped": n is None, "manual": True,
                        "at": iso(utcnow())})
        save_state()

    # Listed deltas not applied yet (after a new full database: all of them). Not with a manual delta only.
    if not nsrl_set and (manual_files is None or manual_db):
        pending = wanted[len([a for a in applied if a.get("url")]):]
        if pending:
            run.log(f"{len(pending)} delta(s) to apply")
        work = paths.work / "deltas"
        try:
            with http_client(settings) as client:
                for durl in pending:
                    shutil.rmtree(work, ignore_errors=True)
                    entry = download(client, durl, work, ref.source.request_headers(durl), None, None, run, cancelled, "sql", settings)
                    files = _delta_sql_files([work / f for f in entry["files"]])
                    if not files:
                        raise SourceCorrupted(f"corrupted delta: no SQL file in {durl}")
                    counts = [apply_chained_delta(base, f, run, cancelled, sqlite_cache_kib(settings)) for f in files]
                    applied.append({"url": durl, "file": ", ".join(f.name for f in files), "statements": sum(c or 0 for c in counts),
                                    "skipped": all(c is None for c in counts), "at": iso(utcnow())})
                    save_state()
                    sources.append({**entry, "files": [], "discarded": True, "delta": True})
        finally:
            shutil.rmtree(work, ignore_errors=True)
    from .nsrl import db_version, ensure_origin

    if nsrl_set:  # read with the publication of each row (sqlbuild.reader_expr): none recorded yet after a full database
        import sqlite3

        con = sqlite3.connect(base)
        try:
            ensure_origin(con)
            con.commit()
        finally:
            con.close()
    version = db_version(base)  # release of a NIST NSRL database, shown with the applied deltas
    if version != state.get("version"):
        state["version"] = version
        save_state()
    return [[base]], sources


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
