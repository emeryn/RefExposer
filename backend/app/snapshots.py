"""Kept versions of a referential: time travel, saved states, comparison and restore.

Two ways a version is kept under ``data/<id>/snapshots/``, both as a **hard link** on the published file, which
costs no extra space and keeps the version alive once the published file is replaced:

- **time travel** (``storage.time_travel``), which keeps every published version and drops the oldest ones
  beyond ``storage.max_snapshots``;
- a **saved state**, taken on demand by someone with the manage right, with a label. It is never dropped
  automatically, and it is what `restore_files` puts back.

**Restoring** publishes the file of a kept version again, as a **new version**: the version number moves
forward, because restoring is publishing something, not rewinding time. The state being replaced is saved
first, so a restore can itself be undone.

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
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import orjson
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .config import ReferentialConfig
from .engine import Engine, rows_to_records
from .models import ReferentialSnapshot
from .pii import expression
from .sqlbuild import lit, parquet_reader, qi
from .storage import RefPaths, iso, utcnow

log = logging.getLogger(__name__)

FOLDER = "snapshots"
LATEST = "latest"
MAX_DIFF_ROWS = 1000
# Saved states kept per referential: they are never dropped by the retention, so they need a limit of their own
MAX_MANUAL = 25


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
    version = int(meta.get("version") or 0)
    if db.scalar(select(ReferentialSnapshot.id)
                 .where(ReferentialSnapshot.referential_id == ref.id, ReferentialSnapshot.version == version,
                        ReferentialSnapshot.manual.is_(False))):
        return None  # already kept (a republication that did not change the version)
    return _keep(db, data_dir, ref, meta, run_id=run_id)


def capture(db: Session, data_dir: Path, ref: ReferentialConfig, meta: dict[str, Any], label: str = "",
            user: str | None = None) -> ReferentialSnapshot:
    """Save the state of a referential on demand, whether time travel is on or not.

    The same version can be saved several times (before each change someone wants to be able to undo), so this
    does not refuse an existing version the way `record` does.
    """
    kept = db.scalar(select(func.count(ReferentialSnapshot.id))
                     .where(ReferentialSnapshot.referential_id == ref.id,
                            ReferentialSnapshot.manual.is_(True))) or 0
    if kept >= MAX_MANUAL:
        raise SnapshotError(f"at most {MAX_MANUAL} saved states per referential: delete one first")
    snap = _keep(db, data_dir, ref, meta, manual=True, label=label.strip()[:200], user=user)
    if snap is None:
        raise SnapshotError("this referential has no published version to save")
    return snap


def _keep(db: Session, data_dir: Path, ref: ReferentialConfig, meta: dict[str, Any], run_id: str | None = None,
          manual: bool = False, label: str = "", user: str | None = None) -> ReferentialSnapshot | None:
    """Hard-link the published file into the snapshots folder and record what it held."""
    paths = RefPaths(data_dir, ref.id)
    if not paths.current.exists():
        return None
    version = int(meta.get("version") or 0)
    stamp = utcnow().strftime("%Y%m%dT%H%M%SZ")
    target = folder(data_dir, ref.id) / f"v{version:06d}-{stamp}.parquet"
    if target.exists():  # two saves within the same second
        target = target.with_name(f"v{version:06d}-{stamp}-{uuid.uuid4().hex[:6]}.parquet")
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
        manual=manual,
        label=label,
        created_by=user,
    )
    db.add(snap)
    db.flush()
    prune(db, data_dir, ref_id=ref.id, keep=ref.storage.max_snapshots)
    db.commit()
    log.info("[%s] %s of version %s kept (%s)", ref.id, "state" if manual else "snapshot", version, target.name)
    return snap


def prune(db: Session, data_dir: Path, ref_id: str, keep: int) -> int:
    """Drop the oldest snapshots beyond the retention policy. Returns how many were removed.

    A saved state is never dropped here: it was kept on purpose, and only a person deletes it.
    """
    rows = list(db.scalars(select(ReferentialSnapshot)
                           .where(ReferentialSnapshot.referential_id == ref_id,
                                  ReferentialSnapshot.manual.is_(False))
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
        # Saved by hand: it carries a label, the account that saved it, and the retention leaves it alone
        "manual": s.manual,
        "label": s.label or "",
        "created_by": s.created_by,
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


# --------------------------------------------------------------------------- restoring a kept version

def restore_files(settings: Any, ref: ReferentialConfig, meta: dict[str, Any], snap: ReferentialSnapshot,
                  run: Any) -> dict[str, Any]:
    """Publish the file of a kept version again, as the current version of the referential.

    Everything the published version is made of is rebuilt from that file — the sorted copies (indexes), the
    row count, the columns, the column profile, the quality rules and the changes against the version being
    replaced — so what comes out is a published version like any other. What this does **not** do is rewind:
    the version number moves forward, and the source the referential reads is left untouched. The next update
    from that source therefore brings its data back, which is why a restore freezes the referential.
    """
    from .crypto import encrypt_data, parquet_key
    from .ingest import PROFILE_SAMPLE_ROWS, QUALITY_SAMPLE_ROWS, _connect, _profile, compute_changes
    from .quality import evaluate as evaluate_quality
    from .sqlbuild import parquet_sample

    paths = RefPaths(settings.data_dir, ref.id)
    source = folder(settings.data_dir, ref.id) / snap.file
    if not source.is_file():
        raise SnapshotError(f"the file of version {snap.version} is gone: it cannot be restored")
    if bool(snap.encrypted) != bool(ref.confidential):
        raise SnapshotError(
            "this version was published with the other confidentiality setting: it is "
            f"{'encrypted' if snap.encrypted else 'in clear'} while the referential is now "
            f"{'confidential' if ref.confidential else 'not confidential'}")
    st = ref.storage
    key = parquet_key(ref.id) if ref.confidential else None
    key_name = key[0] if key else None
    paths.work.mkdir(parents=True, exist_ok=True)
    con = _connect(settings, paths, "parquet", key=key)
    index_tmp: dict[str, Path] = {}
    try:
        rel = parquet_reader(str(source), key_name)
        rows = con.execute(f"SELECT count(*) FROM {rel}").fetchone()[0]
        columns = [{"name": r[0], "type": r[1]} for r in con.execute(f"DESCRIBE SELECT * FROM {rel}").fetchall()]
        col_names = {c["name"] for c in columns}
        run.log(f"Version {snap.version} of {iso(snap.created_at)}: {rows:,} rows, {len(columns)} columns")
        missing = [c for c in [*st.sort_by, *st.indexes] if c not in col_names]
        if missing:
            raise SnapshotError("this version does not have the sort / index column(s) configured today: "
                                + ", ".join(missing))
        large = rows > settings.large_rows
        prev_rows = meta.get("row_count")

        # What the restore changes, against the version it replaces
        changes = None
        if ref.key and ref.key in col_names and paths.current.exists() and not large and st.track_changes:
            try:
                changes = compute_changes(con, str(source), str(paths.current), ref.key, key_name, key_name)
                run.log(f"Changes against the published version: +{changes['added']:,} / "
                        f"-{changes['removed']:,} / "
                        f"~{changes['modified'] if changes['modified'] is not None else '?'} modified")
            except Exception as e:  # noqa: BLE001 (a comparison that fails never stops a restore)
                run.log(f"Cannot compute the changes: {e}", "warn")

        # The sorted copies belong to the data: they are written again from the version being restored
        run.phase = "index"
        copy_opts = f"FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE {st.row_group_size}"
        if key:
            copy_opts += ", ENCRYPTION_CONFIG {footer_key: " + lit(key[0]) + "}"
        for c in st.indexes:
            path = paths.work / f"next.by_{RefPaths.index_name(c)}.parquet"
            con.execute(f"COPY (SELECT * FROM {rel} ORDER BY {qi(c)}) TO {lit(str(path))} ({copy_opts})")
            index_tmp[c] = path
            run.log(f"Index on '{c}' written again ({path.stat().st_size:,} bytes)")

        run.phase = "quality"
        quality = evaluate_quality(con, rel, col_names, ref.quality, ref.key, examples=not ref.confidential,
                                   large=large,
                                   sample=parquet_sample(str(source), key_name, rows, QUALITY_SAMPLE_ROWS,
                                                         st.row_group_size))
        if quality:
            quality["checked_at"] = iso(utcnow())
            run.log(f"Quality: score {quality['score']}/100 ({quality['status']})")

        run.phase = "profile"
        profile = []
        mode = st.profile if not (large and st.profile == "full") else "sample"
        if mode != "none":
            try:
                src = rel if (mode == "full" or rows <= PROFILE_SAMPLE_ROWS) else parquet_sample(
                    str(source), key_name, rows, PROFILE_SAMPLE_ROWS, st.row_group_size)
                profile = _profile(con, src)
            except Exception as e:  # noqa: BLE001
                run.log(f"Cannot compute the column profile: {e}", "warn")
    finally:
        con.close()

    # Publish: the file of the kept version becomes the published one — a hard link, as everywhere here, so
    # restoring a version does not consume it.
    run.phase = "publish"
    tmp = paths.work / "restored.parquet"
    tmp.unlink(missing_ok=True)
    try:
        os.link(source, tmp)
    except OSError:
        shutil.copy2(source, tmp)
    if paths.current.exists() and st.keep_previous:
        os.replace(paths.current, paths.previous)
    else:
        paths.previous.unlink(missing_ok=True)
    os.replace(tmp, paths.current)
    for column, path in index_tmp.items():
        os.replace(path, paths.index_path(column))
    wanted = {paths.index_path(c) for c in st.indexes}
    for f in paths.index_files():
        if f not in wanted:
            f.unlink(missing_ok=True)
    shutil.rmtree(paths.work, ignore_errors=True)
    # The generated downloads were built from the version that has just been replaced
    shutil.rmtree(paths.root / "exports", ignore_errors=True)

    now = iso(utcnow())
    out = dict(meta)
    out.update({
        "version": int(meta.get("version", 0)) + 1,
        # Forgotten on purpose: the next update must read the source again instead of finding it "unchanged"
        "fingerprint": "",
        "data_updated_at": now,
        "previous_row_count": prev_rows,
        "row_count": rows,
        "columns": columns,
        "profile": [] if ref.confidential else profile,
        "profile_enc": encrypt_data(orjson.dumps(profile).decode(), ref.id) if ref.confidential else None,
        "encrypted": bool(ref.confidential),
        "changes": changes,
        "parquet_size": paths.current.stat().st_size,
        "has_previous": paths.previous.exists(),
        "sort_by": st.sort_by,
        "indexes": st.indexes,
        "quality": quality,
        "large": large,
        "profile_sampled": mode == "sample",
        # Where the published version comes from, shown in the interface like a manual import
        "restored": {"snapshot_id": snap.id, "version": snap.version, "label": snap.label or "",
                     "saved_at": iso(snap.created_at), "at": now, "by": run.user},
    })
    out.pop("manual_import", None)
    run.stats.update({"rows": rows, "previous_rows": prev_rows, "changes": changes, "version": out["version"],
                      "restored_from": snap.version})
    run.log(f"Version {out['version']} published, holding the data of version {snap.version}")
    return out
