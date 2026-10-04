"""On-disk layout of a referential:

data/<id>/
  raw/               downloaded (and decompressed) source files
  current.parquet    normalized dataset currently exposed
  previous.parquet   previous version, used to compute changes
  meta.json          state of the referential (last run, schema, profile...)
  runs.jsonl         history of update runs
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import orjson

_lock = threading.RLock()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


class RefPaths:
    def __init__(self, data_dir: Path, ref_id: str):
        self.root = data_dir / ref_id
        self.raw = self.root / "raw"
        self.current = self.root / "current.parquet"
        self.previous = self.root / "previous.parquet"
        self.meta = self.root / "meta.json"
        self.runs = self.root / "runs.jsonl"
        self.work = self.root / ".work"
        self.bloom = self.root / "current.bloom"
        self.previous_bloom = self.root / "previous.bloom"
        self.mmdb = self.root / "current.mmdb"
        self.previous_mmdb = self.root / "previous.mmdb"

    @property
    def published(self) -> Path:
        """File of the published version (parquet, Bloom filter or MaxMind DB)."""
        if self.current.exists():
            return self.current
        if self.mmdb.exists():
            return self.mmdb
        return self.bloom if self.bloom.exists() else self.current

    @staticmethod
    def index_name(column: str) -> str:
        return re.sub(r"[^a-z0-9_]", "_", column.lower())

    def index_path(self, column: str) -> Path:
        """Copy of the current version sorted on `column`."""
        return self.root / f"current.by_{self.index_name(column)}.parquet"

    def index_files(self) -> list[Path]:
        return sorted(self.root.glob("current.by_*.parquet"))

    def ensure(self) -> "RefPaths":
        self.raw.mkdir(parents=True, exist_ok=True)
        return self


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def read_meta(paths: RefPaths) -> dict[str, Any]:
    with _lock:
        if not paths.meta.exists():
            return {}
        try:
            return json.loads(paths.meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}


def write_meta(paths: RefPaths, meta: dict[str, Any]) -> None:
    with _lock:
        _atomic_write(paths.meta, orjson.dumps(meta, option=orjson.OPT_INDENT_2 | orjson.OPT_NON_STR_KEYS, default=str))


def append_run(paths: RefPaths, run: dict[str, Any], keep: int) -> None:
    with _lock:
        paths.root.mkdir(parents=True, exist_ok=True)
        with paths.runs.open("ab") as f:
            f.write(orjson.dumps(run, default=str) + b"\n")
        # Occasional compaction to bound the history size
        if paths.runs.stat().st_size > 2_000_000 or keep and _count_lines(paths.runs) > keep * 2:
            lines = paths.runs.read_bytes().splitlines()[-keep:]
            _atomic_write(paths.runs, b"\n".join(lines) + b"\n")


def _count_lines(path: Path) -> int:
    with path.open("rb") as f:
        return sum(1 for _ in f)


def read_runs(paths: RefPaths, limit: int = 50) -> list[dict[str, Any]]:
    with _lock:
        if not paths.runs.exists():
            return []
        lines = paths.runs.read_bytes().splitlines()
    out = []
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            out.append(orjson.loads(line))
        except orjson.JSONDecodeError:
            continue
        if len(out) >= limit:
            break
    return out


def dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
