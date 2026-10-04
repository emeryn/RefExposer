"""Source analysis for the referential editor: fetch, detect the format, preview the result.

Downloads are cached for a while in data/.tmp/preview so that the administrator can iterate
on options and transforms without downloading the source again.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
import shutil
import threading
import time
import uuid
import zlib
from pathlib import Path
from typing import Any

import duckdb

from .config import FORMATS
from .engine import rows_to_lists
from .ingest import Run, _filename, check_source_file, download, http_client, unpack
from .settings import Settings
from .convert import ConvertError, json_candidates, xml_candidates
from .sqlbuild import build_source_sql, lit

PREVIEW_TTL = 30 * 60
PREVIEW_ROWS = 50
COUNT_MAX_BYTES = 300 * 1024 * 1024
# Line-based sources (csv, tsv, txt, jsonl, possibly .gz) are analysed on a sample: their beginning, decompressed on the
# fly, so that a multi-GB file is previewed in seconds. Other formats need the whole file, up to FULL_MAX_BYTES.
SAMPLE_BYTES = 32 * 1024 * 1024
SAMPLE_EXTENSIONS = {".csv", ".tsv", ".tab", ".txt", ".list", ".lst", ".dat", ".jsonl", ".ndjson"}
FULL_MAX_BYTES = 1024 * 1024 * 1024
UPLOAD_ID_RE = re.compile(r"^[a-f0-9]{32}$")

_EXT_FORMATS = {
    ".csv": "csv",
    ".tsv": "tsv",
    ".tab": "tsv",
    ".jsonl": "jsonl",
    ".ndjson": "jsonl",
    ".json": "json",
    ".parquet": "parquet",
    ".pq": "parquet",
    ".xlsx": "xlsx",
    ".bloom": "bloom",
    ".mmdb": "mmdb",
    ".xml": "xml",
}

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock(key: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


class PreviewError(Exception):
    pass


# --------------------------------------------------------------------------- uploads

def uploads_dir(settings: Settings) -> Path:
    return settings.tmp_dir / "uploads"


def safe_filename(name: str) -> str:
    name = Path(name.replace("\\", "/")).name
    name = re.sub(r"[^\w.\-]+", "_", name).strip("._") or "file"
    return name[:120]


def upload_path(settings: Settings, upload_id: str) -> Path:
    if not UPLOAD_ID_RE.match(upload_id or ""):
        raise PreviewError("invalid upload identifier")
    path = uploads_dir(settings) / upload_id
    if not path.is_dir():
        raise PreviewError("uploaded file not found or expired, upload it again")
    return path


def store_upload(settings: Settings, files: list[tuple[str, Any]]) -> dict[str, Any]:
    """Save uploaded files (name, binary stream) and unpack archives."""
    cleanup(settings)
    upload_id = uuid.uuid4().hex
    dest = uploads_dir(settings) / upload_id
    dest.mkdir(parents=True)
    run = Run("upload", "manual")
    produced: list[Path] = []
    for name, stream in files:
        target = dest / safe_filename(name)
        with target.open("wb") as f:
            shutil.copyfileobj(stream, f, 1 << 20)
        produced.extend(unpack(target, dest, None, run))
    return {"upload_id": upload_id, "files": _describe(produced, dest)}


def _describe(files: list[Path], base: Path) -> list[dict[str, Any]]:
    return [{"name": str(f.relative_to(base)).replace("\\", "/"), "size": f.stat().st_size} for f in files]


def cleanup(settings: Settings) -> None:
    """Remove previews and uploads older than the TTL (uploads: 24 h)."""
    now = time.time()
    for root, ttl in ((settings.tmp_dir / "preview", PREVIEW_TTL), (uploads_dir(settings), 24 * 3600)):
        if not root.exists():
            continue
        for d in root.iterdir():
            try:
                if now - d.stat().st_mtime > ttl:
                    shutil.rmtree(d, ignore_errors=True)
            except OSError:
                pass


# --------------------------------------------------------------------------- acquisition

def _request_headers(source: dict[str, Any]) -> dict[str, str]:
    from .config import SourceConfig

    try:
        return SourceConfig.model_validate({"type": "http", "urls": ["x"], "headers": source.get("headers") or {},
                                            "basic_auth": source.get("basic_auth") or None}).request_headers()
    except ValueError as e:  # e.g. undefined ${REFEX_SOURCE_...} variable
        raise PreviewError(str(e)) from e


def fetch_url(settings: Settings, url: str, headers: dict[str, str], extract: str | None, refresh: bool) -> tuple[list[Path], list[str], Path, bool]:
    key = hashlib.sha256(json.dumps([url, headers, extract], sort_keys=True).encode()).hexdigest()[:24]
    dest = settings.tmp_dir / "preview" / key
    marker = dest / ".entry.json"
    with _lock(key):
        if not refresh and marker.exists() and time.time() - marker.stat().st_mtime < PREVIEW_TTL:
            entry = json.loads(marker.read_text(encoding="utf-8"))
            files = [dest / f for f in entry["files"]]
            if all(f.exists() for f in files):
                return files, [f"{url}: reusing the copy downloaded {int((time.time() - marker.stat().st_mtime) / 60)} min ago"], dest, bool(entry.get("sampled"))
        shutil.rmtree(dest, ignore_errors=True)
        run = Run("preview", "manual")
        with http_client(settings) as client:
            entry = _sample(client, url, dest, headers, run)
            if entry is None:  # format read as a whole: full download
                entry = download(client, url, dest, headers, extract, None, run, fmt="whole")  # decompressed, no format-specific check
        marker.write_text(json.dumps(entry), encoding="utf-8")
        return [dest / f for f in entry["files"]], [log["msg"] for log in run.logs], dest, bool(entry.get("sampled"))


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return str(n)


def _sample(client, url: str, dest: Path, headers: dict[str, str], run: Run) -> dict[str, Any] | None:
    """Download the beginning of a line-based source (decompressed on the fly), or None when the format needs the
    whole file. Raises PreviewError for whole-file formats larger than FULL_MAX_BYTES."""
    run.log(f"Downloading {url}")
    with client.stream("GET", url, headers=headers) as resp:
        resp.raise_for_status()
        name = _filename(resp, url)
        total = int(resp.headers.get("content-length") or 0)
        lower = name.lower()
        gz = lower.endswith(".gz") and not lower.endswith(".tar.gz")
        base = name[:-3] if gz else name
        if Path(base).suffix.lower() not in SAMPLE_EXTENSIONS or lower.endswith((".zip", ".tgz", ".tar.gz", ".tar")):
            if total > FULL_MAX_BYTES:
                raise PreviewError(f"{name} ({_human(total)}) is too large to be analysed: this format is read as a whole. "
                                   "Set the format and options by hand, or analyse a smaller extract of the file.")
            return None
        dest.mkdir(parents=True, exist_ok=True)
        target = dest / base
        written, truncated = 0, False
        dec = zlib.decompressobj(47) if gz else None  # 47: gzip or zlib header
        with target.open("wb") as f:
            for chunk in resp.iter_bytes(1 << 20):
                if dec is not None:
                    try:
                        out = dec.decompress(chunk)
                        while dec.eof and dec.unused_data:  # concatenated gzip members
                            rest = dec.unused_data
                            dec = zlib.decompressobj(47)
                            out += dec.decompress(rest)
                    except zlib.error as e:
                        raise PreviewError(f"corrupted source: unreadable archive '{name}' ({e})") from e
                else:
                    out = chunk
                f.write(out)
                written += len(out)
                if written >= SAMPLE_BYTES:
                    truncated = True
                    break
    if truncated:  # keep complete lines only
        with target.open("rb+") as f:
            f.seek(max(0, written - 4 * 1024 * 1024))
            tail = f.read()
            cut = tail.rfind(b"\n")
            if cut >= 0:
                f.truncate(max(0, written - len(tail)) + cut + 1)
        size = f" ({_human(total)}{' compressed' if gz else ''})" if total else ""
        run.log(f"Preview on a sample: the first {_human(target.stat().st_size)}{' once decompressed' if gz else ''} of {name}{size}. "
                "The whole file is read by the import.")
    else:
        run.log(f"Received {name}: {_human(target.stat().st_size)}{' (decompressed)' if gz else ''}")
    if target.stat().st_size == 0:
        raise PreviewError(f"corrupted source: {url} returned an empty file")
    check_source_file(target, "csv", name)
    return {"url": url, "files": [target.name], "sampled": truncated, "remote_size": total}


def fetch_git(settings: Settings, source: dict[str, Any], refresh: bool) -> tuple[list[Path], list[str], Path]:
    """Files of a Git repository for the preview (cached like the downloads)."""
    from .config import SourceConfig
    from .gitsource import GitError, fetch

    try:
        src = SourceConfig.model_validate({"type": "git", "repository": (source.get("repository") or "").strip(), "path": source.get("path"),
                                           "ref": source.get("ref") or None, "token": source.get("token") or None,
                                           "username": source.get("username") or None})
    except ValueError as e:
        raise PreviewError(str(e).splitlines()[-1]) from e
    key = hashlib.sha256(json.dumps([src.repository, src.ref, src.path, src.token], sort_keys=True).encode()).hexdigest()[:24]
    dest = settings.tmp_dir / "preview" / f"git-{key}"
    logs: list[str] = []
    if dest.is_dir() and not refresh and any(dest.rglob("*")):
        logs.append("Repository already fetched: reusing the copy (Analyse again to fetch it again)")
    else:
        try:
            commit, _ = fetch(settings, src, dest, logs.append)
        except (GitError, ValueError) as e:
            raise PreviewError(str(e)) from e
    files = sorted(p for p in dest.glob((src.path or "").lstrip("/")) if p.is_file())
    if not files:
        raise PreviewError(f"no file of the repository matches '{src.path}'")
    for f in files:
        check_source_file(f, "csv", f.name)
    return files, logs, dest


def acquire(settings: Settings, source: dict[str, Any], ref_root: Path | None, refresh: bool = False) -> tuple[list[list[Path]], list[str], list[dict[str, Any]]]:
    """Return (file groups, logs, file descriptions) for a source description."""
    stype = source.get("type", "http")
    logs: list[str] = []
    described: list[dict[str, Any]] = []
    groups: list[list[Path]] = []
    if stype == "http":
        urls = [u for u in source.get("urls") or [] if u.strip()]
        if not urls:
            raise PreviewError("give at least one URL")
        for url in urls:
            if not re.match(r"^https?://", url.strip()):
                raise PreviewError(f"invalid URL (http/https expected): {url}")
            files, log, base, sampled = fetch_url(settings, url.strip(), _request_headers(source), source.get("extract"), refresh)
            logs.extend(log)
            groups.append(files)
            described.extend({**d, "url": url, "sampled": sampled} for d in _describe(files, base))
    elif stype == "upload":
        base = upload_path(settings, source.get("upload_id", ""))
        files = sorted(p for p in base.rglob("*") if p.is_file())
        groups.append(files)
        described.extend(_describe(files, base))
    elif stype == "git":
        files, log, base = fetch_git(settings, source, refresh)
        logs.extend(log)
        groups.append(files)
        described.extend(_describe(files, base))
    elif stype == "local":
        if ref_root is None:
            raise PreviewError("local source: unknown referential")
        files = sorted(p for p in ref_root.glob(source.get("path") or "") if p.is_file())
        groups.append(files)
        described.extend(_describe(files, ref_root))
    else:
        raise PreviewError(f"unknown source type: {stype}")
    if not any(groups):
        raise PreviewError("no usable file in the source")
    return groups, logs, described


# --------------------------------------------------------------------------- detection

def _head(path: Path, size: int = 65536) -> str:
    opener = gzip.open if path.name.lower().endswith(".gz") else open
    with opener(path, "rb") as f:
        return f.read(size).decode("utf-8", errors="replace")


def sniff_csv(path: Path) -> dict[str, Any] | None:
    try:
        con = duckdb.connect()
        res = con.execute(f"SELECT * FROM sniff_csv({lit(str(path))})")
        row = dict(zip([d[0] for d in res.description], res.fetchone()))
        cols = row.get("Columns") or []
        return {
            "delimiter": row.get("Delimiter"),
            "quote": row.get("Quote"),
            "has_header": row.get("HasHeader"),
            "skip_rows": row.get("SkipRows"),
            "column_count": len(cols),
        }
    except duckdb.Error:
        return None


def detect_format(files: list[Path]) -> tuple[str, dict[str, Any]]:
    """Guess the referential format from the first file. Returns (format, details)."""
    # Archives of a MaxMind DB also hold COPYRIGHT.txt / LICENSE.txt: the database decides
    if any(p.suffix.lower() == ".mmdb" for p in files):
        return "mmdb", {}
    f = files[0]
    ext = Path(re.sub(r"\.gz$", "", f.name, flags=re.I)).suffix.lower()  # .csv.gz is read as .csv
    fmt = _EXT_FORMATS.get(ext)
    if fmt == "json":
        head = _head(f).lstrip()
        lines = [ln for ln in head.splitlines() if ln.strip()][:3]
        if head.startswith("{") and len(lines) >= 2 and all(ln.strip().startswith("{") and ln.strip().endswith("}") for ln in lines[:2]):
            fmt = "jsonl"
    if fmt in ("json", "jsonl", "parquet", "xlsx", "xml", "bloom", "mmdb"):
        return fmt, {}
    sniff = sniff_csv(f)
    if fmt is None and ext in (".txt", ".lst", ".list", ".dat", "") and (not sniff or sniff["column_count"] <= 1):
        return "txt", {"sniff": sniff}
    if fmt is None:
        fmt = "tsv" if sniff and sniff.get("delimiter") == "\t" else "csv"
    return fmt, {"sniff": sniff}


def json_records_candidates(path: Path) -> list[str]:
    """Record collections of a JSON document (records_path hints): arrays of objects, objects of records (``path.*``)."""
    head = _head(path, 2 * 1024 * 1024)
    if head.lstrip().startswith("["):
        return []
    structured = json_candidates(path)
    found = re.findall(r'"([A-Za-z_][\w-]*)"\s*:\s*\[\s*\{', head)
    return list(dict.fromkeys([*structured, *found]))[:10]


def records_candidates(path: Path, fmt: str) -> list[str]:
    try:
        if fmt == "json":
            return json_records_candidates(path)
        if fmt == "xml":
            return [tag for tag, _ in xml_candidates(path)][:10]
    except (ConvertError, OSError):
        pass
    return []


def _duplicate_keys_fix(error: Exception, fmt: str, options: dict[str, Any]) -> list[int] | None:
    if fmt not in ("json", "jsonl", "xml") or "maximum_depth" in options or "Duplicate name" not in str(error):
        return None
    return [3, 2, 1]


# --------------------------------------------------------------------------- preview

def preview(
    settings: Settings,
    source: dict[str, Any],
    fmt: str | None,
    options: dict[str, Any],
    transform: str | None,
    ref_root: Path | None = None,
    refresh: bool = False,
    timeout: float = 120,
) -> dict[str, Any]:
    cleanup(settings)
    groups, logs, described = acquire(settings, source, ref_root, refresh)
    all_files = [f for g in groups for f in g]
    detected, details = detect_format(all_files)
    fmt = fmt or detected
    if fmt not in FORMATS:
        raise PreviewError(f"unknown format: {fmt}")
    result: dict[str, Any] = {
        "files": described,
        "logs": logs,
        "detected_format": detected,
        "format": fmt,
        "sniff": details.get("sniff"),
        "records_path_candidates": records_candidates(all_files[0], fmt),
        "applied_options": {},
        "sampled": any(d.get("sampled") for d in described),
        "columns": [],
        "rows": [],
        "total_rows": None,
        "error": None,
        "sql": None,
    }
    if fmt == "mmdb":
        from .geoip import MmdbDatabase, MmdbError, is_mmdb

        dbs = [f for f in all_files if f.suffix.lower() == ".mmdb"] or [f for f in all_files if is_mmdb(f)]
        if len(dbs) != 1:
            result["error"] = f"a single .mmdb file is expected, {len(dbs)} found: set the archive filter (e.g. '*City.mmdb')"
            return result
        try:
            db = MmdbDatabase(dbs[0])
            info = db.info()
            db.close()
        except (MmdbError, OSError) as e:
            result["error"] = f"unreadable MaxMind DB: {e}"
            return result
        result["columns"] = [{"name": "property", "type": "VARCHAR"}, {"name": "value", "type": "VARCHAR"}]
        result["rows"] = [["file", dbs[0].name], *([k, str(v)] for k, v in info.items())]
        result["total_rows"] = info["node_count"]
        return result
    if fmt == "bloom":
        from .bloom import BloomError, BloomFilter

        try:
            bf = BloomFilter(all_files[0])
            info = bf.info()
            bf.close()
        except (BloomError, OSError) as e:
            result["error"] = f"unreadable Bloom filter: {e}"
            return result
        result["columns"] = [{"name": "property", "type": "VARCHAR"}, {"name": "value", "type": "VARCHAR"}]
        result["rows"] = [[k, str(v)] for k, v in info.items()]
        result["total_rows"] = info["elements"]
        return result
    try:
        sql = build_source_sql(fmt, [[str(f) for f in g] for g in groups], options, transform)
    except Exception as e:  # noqa: BLE001
        result["error"] = str(e)
        return result
    result["sql"] = sql

    con = duckdb.connect()
    con.execute(f"SET temp_directory = {lit(str(settings.tmp_dir / 'duckdb'))}")
    if fmt == "xlsx":
        try:
            con.execute("LOAD excel")
        except duckdb.Error:
            con.execute("INSTALL excel; LOAD excel")
    timer = threading.Timer(timeout, con.interrupt)
    timer.start()
    try:
        try:
            res = con.execute(f"SELECT * FROM ({sql}) LIMIT {PREVIEW_ROWS}")
        except duckdb.Error as e:
            retry = _duplicate_keys_fix(e, fmt, options)
            if retry is None:
                raise
            # Nested objects with keys differing only by case: keep the deeper levels as JSON
            for depth in retry:
                try:
                    sql = build_source_sql(fmt, [[str(f) for f in g] for g in groups], {**options, "maximum_depth": depth}, transform)
                    res = con.execute(f"SELECT * FROM ({sql}) LIMIT {PREVIEW_ROWS}")
                    break
                except duckdb.Error:
                    if depth == retry[-1]:
                        raise
            result["applied_options"] = {"maximum_depth": depth}
            result["sql"] = sql
            result["logs"].append(f"nested objects with duplicate keys: reading with maximum_depth={depth} (deeper levels kept as JSON)")
        result["columns"] = [{"name": d[0], "type": str(d[1])} for d in res.description]
        result["rows"] = rows_to_lists(res.fetchall())
        if not result["sampled"] and sum(f.stat().st_size for f in all_files) <= COUNT_MAX_BYTES:
            result["total_rows"] = con.execute(f"SELECT count(*) FROM ({sql})").fetchone()[0]
    except duckdb.Error as e:
        result["error"] = str(e)
    finally:
        timer.cancel()
        con.close()
    return result


def adopt_upload(settings: Settings, upload_id: str, raw_dir: Path) -> list[str]:
    """Move uploaded files into a referential raw directory (replacing its content)."""
    src = upload_path(settings, upload_id)
    shutil.rmtree(raw_dir, ignore_errors=True)
    raw_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(raw_dir))
    return [str(p.relative_to(raw_dir)).replace("\\", "/") for p in sorted(raw_dir.rglob("*")) if p.is_file()]
