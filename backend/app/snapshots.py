"""Time travel: keep past versions of a referential and compare two of them.

Turned on per referential (`storage.time_travel`). At each publication the Parquet file of the new version is
kept under ``data/<id>/snapshots/`` as a **hard link** on the published file: it costs no extra space, and it
keeps the version alive once the published file is replaced. A retention policy
(``storage.max_snapshots``) drops the oldest ones.

The comparison runs in DuckDB over the two files. A row is identified by ``primary_key_columns`` (or the key of
the referential, or — failing both — by a hash of the whole row, which can then only report additions and
removals). Only the columns both versions have are compared, so a schema change does not make the whole diff
unreadable.

Masking applies here as everywhere else: the personal data columns of the referential are masked unless the
account is allowed to reveal them and asked for it (pii.py).
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import orjson
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .config import ReferentialConfig
from .engine import Engine, rows_to_records
from .models import ReferentialSnapshot
from .pii import expression
from .sqlbuild import parquet_reader, qi
from .storage import RefPaths, iso, utcnow

log = logging.getLogger(__name__)

FOLDER = "snapshots"
LATEST = "latest"
MAX_DIFF_ROWS = 1000


class SnapshotError(ValueError):
    """The comparison cannot be made (unknown snapshot, file gone, no column in common)."""


def folder(data_dir: Path, ref_id: str) -> Path:
    return RefPaths(data_dir, ref_id).root / FOLDER


def schema_hash(columns: Iterable[dict[str, Any]]) -> str:
    payload = [[c.get("name"), c.get("type")] for c in columns]
    return hashlib.sha256(orjson.dumps(payload)).hexdigest()[:16]


# --------------------------------------------------------------------------- keeping versions

def record(db: Session, data_dir: Path, ref: ReferentialConfig, meta: dict[str, Any],
           run_id: str | None = None) -> ReferentialSnapshot | None:
    """Keep the version just published as a snapshot. Returns it, or None when time travel is off."""
    if not ref.storage.time_travel:
        return None
    paths = RefPaths(data_dir, ref.id)
    if not paths.current.exists():
        return None
    version = int(meta.get("version") or 0)
    if db.scalar(select(ReferentialSnapshot.id)
                 .where(ReferentialSnapshot.referential_id == ref.id, ReferentialSnapshot.version == version)):
        return None  # already kept (a republication that did not change the version)
    target = folder(data_dir, ref.id) / f"v{version:06d}-{utcnow().strftime('%Y%m%dT%H%M%SZ')}.parquet"
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(paths.current, target)
    except OSError:
        # Another file system, or hard links unavailable: a copy still works, it just takes the space
        shutil.copy2(paths.current, target)
    columns = meta.get("columns") or []
    snap = ReferentialSnapshot(
        referential_id=ref.id,
        version=version,
        row_count=meta.get("row_count"),
        file=target.name,
        size=target.stat().st_size,
        schema_hash=schema_hash(columns),
        columns=columns,
        encrypted=bool(meta.get("encrypted")),
        run_id=run_id,
    )
    db.add(snap)
    db.flush()
    prune(db, data_dir, ref.id, ref.storage.max_snapshots)
    db.commit()
    log.info("[%s] snapshot of version %s kept (%s)", ref.id, version, target.name)
    return snap


def prune(db: Session, data_dir: Path, ref_id: str, keep: int) -> int:
    """Drop the oldest snapshots beyond the retention policy. Returns how many were removed."""
    rows = list(db.scalars(select(ReferentialSnapshot).where(ReferentialSnapshot.referential_id == ref_id)
                           .order_by(ReferentialSnapshot.version.desc(), ReferentialSnapshot.id.desc())))
    removed = 0
    for snap in rows[max(keep, 1):]:
        (folder(data_dir, ref_id) / snap.file).unlink(missing_ok=True)
        db.delete(snap)
        removed += 1
    return removed


def purge(db: Session, data_dir: Path, ref_id: str) -> None:
    """Forget every snapshot of a referential (it was deleted, or time travel was turned off)."""
    db.execute(delete(ReferentialSnapshot).where(ReferentialSnapshot.referential_id == ref_id))
    shutil.rmtree(folder(data_dir, ref_id), ignore_errors=True)


def listing(db: Session, data_dir: Path, ref_id: str) -> list[dict[str, Any]]:
    rows = db.scalars(select(ReferentialSnapshot).where(ReferentialSnapshot.referential_id == ref_id)
                      .order_by(ReferentialSnapshot.version.desc(), ReferentialSnapshot.id.desc())).all()
    base = folder(data_dir, ref_id)
    return [{
        "id": s.id,
        "version": s.version,
        "created_at": s.created_at,
        "row_count": s.row_count,
        "size": s.size,
        "columns": len(s.columns or []),
        "schema_hash": s.schema_hash,
        "encrypted": s.encrypted,
        "available": (base / s.file).is_file(),
        "run_id": s.run_id,
    } for s in rows]


# --------------------------------------------------------------------------- comparing two versions

@dataclass
class Side:
    """One side of a comparison: a snapshot, or the version published right now."""

    label: str
    path: Path
    columns: list[str]
    encrypted: bool
    version: int | None = None
    created_at: Any = None
    row_count: int | None = None

    def describe(self) -> dict[str, Any]:
        return {"ref": self.label, "version": self.version, "created_at": self.created_at,
                "row_count": self.row_count, "columns": len(self.columns)}


def side(db: Session, data_dir: Path, ref: ReferentialConfig, meta: dict[str, Any], token: str) -> Side:
    """Resolve `latest` (the published version) or the identifier of a snapshot."""
    paths = RefPaths(data_dir, ref.id)
    if token in (LATEST, "current", ""):
        if not paths.current.exists():
            raise SnapshotError("this referential has no published version")
        return Side(LATEST, paths.current, [c["name"] for c in meta.get("columns") or []],
                    bool(meta.get("encrypted")), meta.get("version"), meta.get("data_updated_at"),
                    meta.get("row_count"))
    if not token.isdigit():
        raise SnapshotError(f"unknown snapshot: '{token}'")
    snap = db.get(ReferentialSnapshot, int(token))
    if snap is None or snap.referential_id != ref.id:
        raise SnapshotError(f"unknown snapshot: '{token}'")
    path = folder(data_dir, ref.id) / snap.file
    if not path.is_file():
        raise SnapshotError(f"the file of the snapshot of version {snap.version} is gone")
    return Side(str(snap.id), path, [c["name"] for c in snap.columns or []], snap.encrypted,
                snap.version, snap.created_at, snap.row_count)


def _projection(columns: list[str], masked: set[str], strategy: str) -> str:
    return ", ".join(
        f"{expression(c, strategy)} AS {qi(c)}" if c.lower() in masked else qi(c) for c in columns
    )


def _reader(s: Side, ref_id: str, columns: list[str], masked: set[str], strategy: str) -> str:
    from .crypto import parquet_key

    key = parquet_key(ref_id)[0] if s.encrypted else None
    return f"(SELECT {_projection(columns, masked, strategy)} FROM {parquet_reader(str(s.path.resolve()), key)})"


def key_columns(ref: ReferentialConfig, columns: list[str]) -> list[str]:
    """Columns identifying a row, among those both versions have. Empty: rows are compared whole."""
    wanted = ref.primary_key_columns or ([ref.key] if ref.key else [])
    have = {c.lower(): c for c in columns}
    chosen = [have[c.lower()] for c in wanted if c.lower() in have]
    return chosen if len(chosen) == len(wanted) else []


def diff(engine: Engine, ref: ReferentialConfig, a: Side, b: Side, *, kind: str = "summary",
         limit: int = 50, offset: int = 0, reveal: bool = False) -> dict[str, Any]:
    """Compare two versions. `kind`: summary, added, removed or modified.

    `a` is the older side, `b` the newer one: *added* means present in `b` only.
    """
    columns = [c for c in b.columns if c in set(a.columns)]
    if not columns:
        raise SnapshotError("the two versions have no column in common: nothing to compare")
    from .crypto import parquet_key
    from .pii import columns_of

    for s in (a, b):
        if s.encrypted:
            engine.add_key(engine.main, parquet_key(ref.id))
    masked = set() if reveal else {c.lower() for c in columns_of(ref)}
    strategy = ref.pii.strategy if ref.pii else "partial"
    old = _reader(a, ref.id, columns, masked, strategy)
    new = _reader(b, ref.id, columns, masked, strategy)
    keys = key_columns(ref, columns)

    if keys:
        on = " AND ".join(f"n.{qi(k)} IS NOT DISTINCT FROM o.{qi(k)}" for k in keys)
        added = f"SELECT n.* FROM {new} n ANTI JOIN {old} o ON {on}"
        removed = f"SELECT o.* FROM {old} o ANTI JOIN {new} n ON {on}"
        modified = (f"SELECT {', '.join(f'n.{qi(k)}' for k in keys)}, to_json(o) AS __before, to_json(n) AS __after "
                    f"FROM {new} n JOIN {old} o ON {on} "
                    f"WHERE md5(CAST(n AS VARCHAR)) <> md5(CAST(o AS VARCHAR))")
    else:
        # No logical key: a row is its own identity, so a change reads as one removal and one addition
        rows_new = f"(SELECT *, md5(CAST(t AS VARCHAR)) AS __h FROM {new} t)"
        rows_old = f"(SELECT *, md5(CAST(t AS VARCHAR)) AS __h FROM {old} t)"
        added = f"SELECT * EXCLUDE (__h) FROM {rows_new} n WHERE __h NOT IN (SELECT __h FROM {rows_old})"
        removed = f"SELECT * EXCLUDE (__h) FROM {rows_old} o WHERE __h NOT IN (SELECT __h FROM {rows_new})"
        modified = None

    if kind == "summary":
        counts = {
            "added": engine.scalar(f"SELECT count(*) FROM ({added})") or 0,
            "removed": engine.scalar(f"SELECT count(*) FROM ({removed})") or 0,
            "modified": (engine.scalar(f"SELECT count(*) FROM ({modified})") or 0) if modified else 0,
        }
        return {
            "kind": "summary", "from": a.describe(), "to": b.describe(), "keys": keys,
            "columns": columns, "masked": sorted(masked), "revealed": reveal and bool(columns_of(ref)),
            "comparable": bool(keys), **counts,
            "schema_changed": sorted(set(a.columns) ^ set(b.columns)),
        }

    limit = max(1, min(limit, MAX_DIFF_ROWS))
    if kind in ("added", "removed"):
        base = added if kind == "added" else removed
        order = ", ".join(qi(k) for k in keys) or "1"
        cols, rows = engine.fetch(f"SELECT * FROM ({base}) ORDER BY {order} LIMIT {limit} OFFSET {max(offset, 0)}")
        return {"kind": kind, "total": engine.scalar(f"SELECT count(*) FROM ({base})") or 0,
                "columns": [{"name": c, "type": ""} for c in cols], "rows": rows_to_records(cols, rows)}
    if kind != "modified":
        raise SnapshotError(f"unknown comparison: '{kind}'")
    if modified is None:
        return {"kind": "modified", "total": 0, "rows": [], "columns": [],
                "note": "set primary_key_columns (or the key of the referential) to see modified rows"}
    order = ", ".join(qi(k) for k in keys)
    _, rows = engine.fetch(f"{modified} ORDER BY {order} LIMIT {limit} OFFSET {max(offset, 0)}")
    out = []
    for row in rows:
        before, after = orjson.loads(row[-2]), orjson.loads(row[-1])
        changes = [{"column": c, "before": before.get(c), "after": after.get(c)}
                   for c in dict.fromkeys([*after.keys(), *before.keys()]) if before.get(c) != after.get(c)]
        out.append({"key": " / ".join("" if v is None else str(v) for v in row[:len(keys)]), "changes": changes})
    return {"kind": "modified", "total": engine.scalar(f"SELECT count(*) FROM ({modified})") or 0,
            "columns": [], "rows": out}


def summary_text(result: dict[str, Any]) -> str:
    """The one-line statistics of a comparison, as the interface and the agent show them."""
    return (f"+{result.get('added', 0):,} added, -{result.get('removed', 0):,} removed, "
            f"~{result.get('modified', 0):,} modified").replace(",", " ")


def stored_size(db: Session, data_dir: Path, ref_id: str) -> int:
    base = folder(data_dir, ref_id)
    if not base.exists():
        return 0
    return sum(p.stat().st_size for p in base.glob("*.parquet") if p.is_file())


# Kept for the run journal: what the publication did with the snapshots
def note(snap: ReferentialSnapshot | None) -> str | None:
    return f"snapshot kept: version {snap.version} ({iso(snap.created_at)})" if snap else None
