"""Full-dataset downloads, generated once per version and cached in data/<id>/exports/."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import ReferentialConfig
from .engine import Engine
from .settings import Settings
from .sqlbuild import lit, parquet_reader, qi
from .storage import RefPaths, iso, utcnow

log = logging.getLogger(__name__)

EXCEL_MAX_ROWS = 1_048_575

# format -> (extension, DuckDB COPY options, media type, label)
FORMATS: dict[str, tuple[str, str | None, str, str]] = {
    "csv": ("csv", "FORMAT csv, HEADER true", "text/csv", "CSV"),
    "csv.gz": ("csv.gz", "FORMAT csv, HEADER true, COMPRESSION gzip", "application/gzip", "Compressed CSV (gzip)"),
    "xlsx": ("xlsx", "FORMAT xlsx, HEADER true", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "Excel"),
    "json": ("json", "FORMAT json, ARRAY true", "application/json", "JSON"),
    "jsonl": ("jsonl", "FORMAT json", "application/x-ndjson", "JSON Lines"),
    "parquet": ("parquet", None, "application/vnd.apache.parquet", "Parquet"),
}


class DownloadError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


@dataclass
class DownloadFile:
    path: Path
    filename: str
    media_type: str
    size: int
    sha256: str
    # Generated for this request only (confidential referential): deleted once sent
    temporary: bool = False


def sha256_file(path: Path) -> str:
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


class Downloads:
    def __init__(self, engine: Engine, settings: Settings):
        self.engine = engine
        self.settings = settings
        self._locks: dict[tuple[str, str], threading.Lock] = {}
        self._guard = threading.Lock()

    def _lock(self, ref_id: str, fmt: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault((ref_id, fmt), threading.Lock())

    # ------------------------------------------------------------------ index
    def _dir(self, ref_id: str) -> Path:
        return RefPaths(self.settings.data_dir, ref_id).root / "exports"

    def _index(self, ref_id: str) -> dict[str, Any]:
        p = self._dir(ref_id) / "index.json"
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_index(self, ref_id: str, index: dict[str, Any]) -> None:
        d = self._dir(ref_id)
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / "index.json.tmp"
        tmp.write_text(json.dumps(index, indent=2), encoding="utf-8")
        os.replace(tmp, d / "index.json")

    # ------------------------------------------------------------------ listing
    def available(self, ref: ReferentialConfig, meta: dict[str, Any]) -> list[dict[str, Any]]:
        version = meta.get("version")
        rows = meta.get("row_count") or 0
        index = self._index(ref.id)
        out = []
        for fmt, (_, _, _, label) in FORMATS.items():
            entry = index.get(fmt) or {}
            ready = entry.get("version") == version and bool(entry.get("file")) and (self._dir(ref.id) / entry["file"]).is_file()
            if ref.confidential:  # generated (in clear) for each request, never kept
                ready, entry = False, {}
            elif fmt == "parquet":
                ready = True
                entry = {**entry, "size": meta.get("parquet_size")} if entry.get("version") == version else {"size": meta.get("parquet_size")}
            too_large = fmt == "xlsx" and rows > EXCEL_MAX_ROWS
            reason = f"more than {EXCEL_MAX_ROWS:,} rows (Excel limit)" if too_large else None
            if fmt != "parquet" and rows > self.settings.large_rows:
                too_large, reason = True, "volume too large: use the Parquet format"
            out.append({
                "format": fmt,
                "label": label,
                "ready": ready and not too_large,
                "size": entry.get("size") if ready else None,
                "sha256": entry.get("sha256") if ready else None,
                "generated_at": entry.get("generated_at") if ready else None,
                "unavailable_reason": reason,
                "prebuilt": fmt in ref.downloads,
            })
        return out

    def _source_paths(self, ref: ReferentialConfig, meta: dict[str, Any]) -> list[tuple[Path, str, str | None]]:
        """(path, display name, url) of the files of the current version's sources."""
        paths = RefPaths(self.settings.data_dir, ref.id)
        out = []
        for s in meta.get("sources") or []:
            # Downloaded files live in raw/; manual imports and local files are relative to the referential folder
            base = paths.raw if ref.source.type == "http" and not s.get("manual") and not s.get("internal") else paths.root
            for f in s.get("files", []):
                if s.get("sync") is not None and self.settings.sync_dir:  # files of the sync folder: "sync/<path>"
                    p = self.settings.sync_dir / f.removeprefix("sync/")
                    if p.is_file():
                        out.append((p, f, None))
                    continue
                p = base / f  # absolute paths (import folder) are kept as is
                if p.is_file():
                    out.append((p, Path(f).name if Path(f).is_absolute() else f, s.get("url")))
        return out

    def sources(self, ref: ReferentialConfig, meta: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"name": name, "size": p.stat().st_size, "url": url} for p, name, url in self._source_paths(ref, meta)]

    # ------------------------------------------------------------------ generation
    def get(self, ref: ReferentialConfig, meta: dict[str, Any], fmt: str) -> DownloadFile:
        if fmt not in FORMATS:
            raise DownloadError(404, f"unknown format: {fmt}")
        paths = RefPaths(self.settings.data_dir, ref.id)
        if not paths.current.exists():
            raise DownloadError(409, "no published data")
        version = meta.get("version") or 0
        ext, copy_opts, media, _ = FORMATS[fmt]
        filename = f"{ref.id}-v{version}.{ext}"
        if fmt != "parquet" and (meta.get("row_count") or 0) > self.settings.large_rows:
            raise DownloadError(413, "volume too large for this format: download the Parquet file")
        if fmt == "xlsx" and (meta.get("row_count") or 0) > EXCEL_MAX_ROWS:
            raise DownloadError(413, f"too many rows for Excel ({meta.get('row_count'):,} > {EXCEL_MAX_ROWS:,}): use CSV or Parquet")
        if fmt == "xlsx" and not self.engine.excel:
            raise DownloadError(501, "Excel export unavailable (DuckDB 'excel' extension not loaded)")

        if ref.confidential:
            return self._temporary(ref, f"SELECT * FROM {qi(ref.table)}", filename, ext, copy_opts or "FORMAT parquet, COMPRESSION zstd", media)

        with self._lock(ref.id, fmt):
            index = self._index(ref.id)
            entry = index.get(fmt) or {}
            if fmt == "parquet":
                if entry.get("version") != version:
                    entry = {"version": version, "file": None, "size": paths.current.stat().st_size,
                             "sha256": sha256_file(paths.current), "generated_at": iso(utcnow())}
                    index[fmt] = entry
                    self._save_index(ref.id, index)
                return DownloadFile(paths.current, filename, media, entry["size"], entry["sha256"])

            target = self._dir(ref.id) / f"v{version}.{ext}"
            if entry.get("version") != version or not target.is_file():
                self._dir(ref.id).mkdir(parents=True, exist_ok=True)
                tmp = target.with_name(target.name + ".tmp")
                tmp.unlink(missing_ok=True)
                cur = self.engine.cursor()
                try:
                    cur.execute(f"COPY (SELECT * FROM read_parquet({lit(str(paths.current.resolve()))})) TO {lit(str(tmp))} ({copy_opts})")
                finally:
                    cur.close()
                os.replace(tmp, target)
                entry = {"version": version, "file": target.name, "size": target.stat().st_size,
                         "sha256": sha256_file(target), "generated_at": iso(utcnow())}
                index[fmt] = entry
                self._save_index(ref.id, index)
                self._purge_old(ref.id, index)
                log.info("[%s] download %s generated (%s bytes)", ref.id, fmt, entry["size"])
            return DownloadFile(target, filename, media, entry["size"], entry["sha256"])

    def _temporary(self, ref: ReferentialConfig, sql: str, filename: str, ext: str, copy_opts: str, media: str) -> DownloadFile:
        """Clear copy for one request, in the temporary folder, deleted after sending (confidential referential:
        the published files are encrypted with a key that is never handed out)."""
        target = self.settings.tmp_dir / "exports" / f"{uuid.uuid4().hex}.{ext}"
        target.parent.mkdir(parents=True, exist_ok=True)
        cur = self.engine.cursor()
        try:
            cur.execute(f"COPY ({sql}) TO {lit(str(target))} ({copy_opts})")
        finally:
            cur.close()
        return DownloadFile(target, filename, media, target.stat().st_size, sha256_file(target), temporary=True)

    def _purge_old(self, ref_id: str, index: dict[str, Any]) -> None:
        keep = {e.get("file") for e in index.values() if e.get("file")} | {"index.json"}
        for p in self._dir(ref_id).iterdir():
            if p.is_file() and p.name not in keep and not p.name.endswith(".tmp"):
                p.unlink(missing_ok=True)

    def source_archive(self, ref: ReferentialConfig, meta: dict[str, Any]) -> DownloadFile:
        """Original source files: the file itself when there is only one, a zip otherwise."""
        entries = self._source_paths(ref, meta)
        files = [p for p, _, _ in entries]
        if not files:
            raise DownloadError(404, "no source file available")
        if len(files) == 1:
            f = files[0]
            return DownloadFile(f, f"{ref.id}-source-{f.name}", "application/octet-stream", f.stat().st_size, self._cached_sha(ref.id, f))
        key = (meta.get("fingerprint") or "x")[:12]
        with self._lock(ref.id, "source"):
            index = self._index(ref.id)
            entry = index.get("source") or {}
            target = self._dir(ref.id) / f"source-{key}.zip"
            if entry.get("file") != target.name or not target.is_file():
                self._dir(ref.id).mkdir(parents=True, exist_ok=True)
                tmp = target.with_name(target.name + ".tmp")
                with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
                    for f, name, _ in entries:
                        zf.write(f, arcname=name)
                os.replace(tmp, target)
                entry = {"version": meta.get("version"), "file": target.name, "size": target.stat().st_size,
                         "sha256": sha256_file(target), "generated_at": iso(utcnow())}
                index["source"] = entry
                self._save_index(ref.id, index)
                self._purge_old(ref.id, index)
            return DownloadFile(target, f"{ref.id}-sources.zip", "application/zip", entry["size"], entry["sha256"])

    def _cached_sha(self, ref_id: str, f: Path) -> str:
        index = self._index(ref_id)
        key = f"sha:{f.name}"
        st = f.stat()
        stamp = f"{st.st_size}:{int(st.st_mtime)}"
        entry = index.get(key)
        if entry and entry.get("stamp") == stamp:
            return entry["sha256"]
        sha = sha256_file(f)
        index[key] = {"stamp": stamp, "sha256": sha}
        self._save_index(ref_id, index)
        return sha

    def previous(self, ref: ReferentialConfig, meta: dict[str, Any] | None = None) -> DownloadFile:
        p = RefPaths(self.settings.data_dir, ref.id).previous
        if not p.exists():
            raise DownloadError(404, "no previous version")
        if (meta or {}).get("encrypted"):  # previous version of a confidential referential: encrypted like the current one
            from .crypto import parquet_key

            return self._temporary(ref, f"SELECT * FROM {parquet_reader(str(p.resolve()), parquet_key(ref.id)[0])}",
                                   f"{ref.id}-previous.parquet", "parquet", "FORMAT parquet, COMPRESSION zstd", FORMATS["parquet"][2])
        return DownloadFile(p, f"{ref.id}-previous.parquet", FORMATS["parquet"][2], p.stat().st_size, self._cached_sha(ref.id, p))

    def artifact(self, ref: ReferentialConfig, meta: dict[str, Any], previous: bool = False) -> DownloadFile:
        """Raw file of a Bloom filter or a MaxMind DB, as published (current or previous version). A MaxMind DB keeps
        its original name (GeoLite2-City.mmdb...), so that tools find the file they expect."""
        paths = RefPaths(self.settings.data_dir, ref.id)
        if ref.is_mmdb:
            p = paths.previous_mmdb if previous else paths.mmdb
            name = (meta.get("mmdb") or {}).get("file_name") or f"{ref.id}.mmdb"
            if previous:
                name = name[:-5] + "-previous.mmdb" if name.lower().endswith(".mmdb") else name + "-previous"
            media = "application/octet-stream"
        elif ref.is_bloom:
            p = paths.previous_bloom if previous else paths.bloom
            name = f"{ref.id}-previous.bloom" if previous else f"{ref.id}-v{meta.get('version')}.bloom"
            media = "application/octet-stream"
        else:
            raise DownloadError(400, "not a raw file referential")
        if not p.exists():
            raise DownloadError(404, "no previous version" if previous else "no published version")
        return DownloadFile(p, name, media, p.stat().st_size, self._cached_sha(ref.id, p))

    def prebuild(self, ref: ReferentialConfig, meta: dict[str, Any]) -> None:
        if ref.confidential:  # nothing generated in advance: no clear copy on disk
            return
        for fmt in ref.downloads:
            try:
                self.get(ref, meta, fmt)
            except Exception as e:  # noqa: BLE001
                log.warning("[%s] prebuild of %s failed: %s", ref.id, fmt, e)


