"""Referentials synchronized from the file system.

Every entry of the sync folder (REFEX_SYNC_DIR) is a referential, without any declaration:

- a file           sync/epss_scores.csv     -> referential "epss-scores"
- a folder         sync/threatfox/          -> referential "threatfox", made of every data file of the folder

A folder may contain a ``refexposer.yml`` file overriding the generated definition (name, description, category, key,
format, options, transform, search_columns, validation, storage...). Files are read in place (the folder may be mounted
read-only); hidden files (rsync temporary files), ``*.part`` / ``*.tmp`` and the README are ignored.

The watcher re-imports a referential when the size or date of its files changes, once they have stopped changing for
REFEX_SYNC_SETTLE_SECONDS (copy finished). The pipeline fingerprint then skips files whose content did not change.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .config import ConfigError, ReferentialConfig, validate_definition

log = logging.getLogger(__name__)

OVERRIDES_FILE = "refexposer.yml"
IGNORED_SUFFIXES = (".part", ".tmp", ".partial", ".filepart", ".crdownload", ".swp", ".lock")
IGNORED_NAMES = {OVERRIDES_FILE, "readme", "readme.md", "readme.txt", "thumbs.db", ".ds_store"}
_FORMATS = {
    ".csv": "csv", ".tsv": "tsv", ".tab": "tsv", ".json": "json", ".jsonl": "jsonl", ".ndjson": "jsonl",
    ".parquet": "parquet", ".pq": "parquet", ".xlsx": "xlsx", ".xml": "xml", ".bloom": "bloom", ".mmdb": "mmdb",
    ".txt": "txt", ".list": "txt", ".lst": "txt",
}
_COMPRESSION = re.compile(r"\.(gz|zip|tgz|tar\.gz|tar)$", re.I)


def slug(name: str, is_file: bool = True) -> str:
    base = name
    if is_file:  # drop the compression then the format extension of a file name
        base = re.sub(r"\.[A-Za-z0-9]{1,8}$", "", _COMPRESSION.sub("", base))
    return re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")[:63] or "referential"


def title(name: str, is_file: bool = True) -> str:
    s = slug(name, is_file).replace("-", " ")
    return s[:1].upper() + s[1:]


def is_data_file(p: Path) -> bool:
    n = p.name
    return p.is_file() and not n.startswith((".", "~")) and n.lower() not in IGNORED_NAMES and not n.lower().endswith(IGNORED_SUFFIXES)


def entry_files(entry: Path) -> list[Path]:
    """Data files of a sync entry (the file itself, or the files of the folder, recursively)."""
    if entry.is_file():
        return [entry] if is_data_file(entry) else []
    return sorted(p for p in entry.rglob("*") if is_data_file(p) and not any(part.startswith(".") for part in p.relative_to(entry).parts))


def guess_format(files: list[Path]) -> str:
    """Format from the extension of the files (compression ignored); CSV when unknown."""
    for f in files:
        ext = Path(_COMPRESSION.sub("", f.name)).suffix.lower()
        if ext in _FORMATS:
            return _FORMATS[ext]
    return "csv"


def signature(files: list[Path]) -> tuple:
    out = []
    for f in files:
        try:
            st = f.stat()
            out.append((str(f), st.st_size, st.st_mtime_ns))
        except OSError:
            out.append((str(f), -1, -1))
    return tuple(out)


@dataclass
class SyncEntry:
    ref: ReferentialConfig
    files: list[Path]
    signature: tuple = field(default_factory=tuple)


def _json_records_path(files: list[Path]) -> str | None:
    """Collection of records of a JSON document read as a whole (e.g. {"vulnerabilities": [...]}), if any."""
    from .convert import json_candidates

    f = next((f for f in files if not _COMPRESSION.search(f.name)), None)
    if f is None:
        return None
    try:
        with f.open("rb") as fh:
            if fh.read(4096).lstrip()[:1] != b"{":
                return None
        cands = json_candidates(f)
    except OSError:
        return None
    return cands[0] if cands else None


def discover(sync_dir: Path) -> tuple[dict[str, SyncEntry], list[ConfigError]]:
    entries: dict[str, SyncEntry] = {}
    errors: list[ConfigError] = []
    if not sync_dir or not sync_dir.is_dir():
        return entries, errors
    for entry in sorted(sync_dir.iterdir()):
        if entry.name.startswith((".", "_")) or (entry.is_file() and not is_data_file(entry)):
            continue
        label = f"sync/{entry.name}"
        files = entry_files(entry)
        if not files:
            continue
        overrides: dict[str, Any] = {}
        ov_file = entry / OVERRIDES_FILE if entry.is_dir() else None
        if ov_file and ov_file.is_file():
            try:
                overrides = yaml.safe_load(ov_file.read_text(encoding="utf-8")) or {}
                if not isinstance(overrides, dict):
                    raise ValueError("a mapping is expected (name: ..., key: ...)")
            except Exception as e:  # noqa: BLE001
                errors.append(ConfigError(file=f"{label}/{OVERRIDES_FILE}", error=f"invalid file: {e}"))
                continue
        ref_id = str(overrides.pop("id", None) or slug(entry.name, entry.is_file()))
        fmt = overrides.get("format") or guess_format(files)
        options = dict(overrides.pop("options", None) or {})
        if fmt == "json" and "records_path" not in options:
            rp = _json_records_path(files)
            if rp:
                options["records_path"] = rp
        definition = {
            "id": ref_id,
            "name": title(entry.name, entry.is_file()),
            "description": f"Synchronized from the file system ({label}).",
            "category": "Synchronized",
            **{k: v for k, v in overrides.items() if k not in ("source",)},
            "format": fmt,
            "options": options,
            "source": {"type": "sync", "path": entry.name},
            "schedule": None,
        }
        try:
            ref = validate_definition(definition)
        except ValueError as e:
            errors.append(ConfigError(file=label, error=str(e)))
            continue
        ref.origin = "sync"
        ref.config_file = label
        if ref_id in entries:
            errors.append(ConfigError(file=label, error=f"identifier '{ref_id}' already used by {entries[ref_id].ref.config_file}"))
            continue
        entries[ref_id] = SyncEntry(ref, files, signature(files + ([ov_file] if ov_file and ov_file.is_file() else [])))
    return entries, errors


def resolve(sync_dir: Path | None, rel: str) -> list[Path]:
    """Current data files of a sync referential (source.path relative to the sync folder)."""
    if not sync_dir:
        raise FileNotFoundError("the sync folder is not configured (REFEX_SYNC_DIR)")
    entry = (sync_dir / rel).resolve()
    if sync_dir.resolve() not in entry.parents:
        raise FileNotFoundError(f"invalid sync path '{rel}'")
    if not entry.exists():
        raise FileNotFoundError(f"sync/{rel} no longer exists")
    files = entry_files(entry)
    if not files:
        raise FileNotFoundError(f"no data file in sync/{rel}")
    return files


def settled(files: list[Path], settle_seconds: float) -> bool:
    """True when no file was modified in the last ``settle_seconds`` (the copy is over)."""
    now = time.time()
    try:
        return all(now - f.stat().st_mtime >= settle_seconds for f in files)
    except OSError:
        return False
