"""Internal referentials: rows managed in RefExposer (editor and API), plus their creation and schema."""

from __future__ import annotations

import re
import shutil
import uuid
from collections.abc import Iterable
from datetime import date, datetime
from typing import Any, Literal

import duckdb
from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import String, cast, delete, func, or_, select
from sqlalchemy.orm import Session

from ..auth import CurrentUser, audit, require_user
from ..config import ColumnDef, ReferentialConfig, validate_definition
from ..db import get_db
from ..engine import rows_to_records
from ..models import Grant, InternalRecord, ReferentialDefinition
from ..records_codec import db_key, is_encrypted, load, store
from ..service import Service
from ..storage import RefPaths
from .deps import get_ref, get_service

router = APIRouter(tags=["internal referentials"])

MAX_BULK = 50_000
_TRUE = {"true", "1", "yes", "y", "oui", "vrai", "x"}
_FALSE = {"false", "0", "no", "n", "non", "faux", ""}


class RecordError(ValueError):
    pass


# --------------------------------------------------------------------------- values

def coerce(col: ColumnDef, value: Any) -> Any:
    """Convert a value to the column type (JSON-friendly representation)."""
    if value is None or (isinstance(value, str) and value.strip() == "" and col.type != "text"):
        return None
    t = col.type
    try:
        if t == "text":
            if isinstance(value, (dict, list)):
                raise ValueError
            return str(value)
        if t == "integer":
            if isinstance(value, bool):
                raise ValueError
            if isinstance(value, float):
                if not value.is_integer():
                    raise ValueError
                return int(value)
            return int(str(value).strip())
        if t == "number":
            if isinstance(value, bool):
                raise ValueError
            return float(str(value).strip().replace(",", ".")) if isinstance(value, str) else float(value)
        if t == "boolean":
            if isinstance(value, bool):
                return value
            s = str(value).strip().lower()
            if s in _TRUE:
                return True
            if s in _FALSE:
                return False
            raise ValueError
        if t == "date":
            if isinstance(value, datetime):
                return value.date().isoformat()
            if isinstance(value, date):
                return value.isoformat()
            return date.fromisoformat(str(value).strip()[:10]).isoformat()
        if t == "datetime":
            if isinstance(value, datetime):
                return value.isoformat()
            return datetime.fromisoformat(str(value).strip().replace("Z", "+00:00")).isoformat()
        if t == "list":
            if isinstance(value, (list, tuple)):
                return [str(v) for v in value if v is not None and str(v) != ""]
            return [v.strip() for v in str(value).split(",") if v.strip()]
    except (TypeError, ValueError) as e:
        raise RecordError(f"column '{col.name}': '{value}' is not a valid {t}") from e
    return value


def normalize(ref: ReferentialConfig, data: dict[str, Any], base: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
    """Validate a record against the schema. `base` = existing record (partial updates)."""
    if not isinstance(data, dict):
        raise RecordError("a record must be a JSON object")
    cols = {c.name: c for c in ref.columns}
    unknown = [k for k in data if k not in cols and not k.startswith("_")]
    if unknown:
        raise RecordError(f"unknown column(s): {', '.join(unknown)} (columns: {', '.join(cols)})")
    merged = {**(base or {}), **{k: v for k, v in data.items() if k in cols}}
    out = {name: coerce(col, merged.get(name)) for name, col in cols.items()}
    key_col = cols[ref.key]
    if out[ref.key] in (None, "") and key_col.auto:
        out[ref.key] = str(uuid.uuid4())
    missing = [n for n, c in cols.items() if (c.required or n == ref.key) and out.get(n) in (None, "", [])]
    if missing:
        raise RecordError(f"missing value for required column(s): {', '.join(missing)}")
    return str(out[ref.key]), out


# --------------------------------------------------------------------------- helpers

def _internal(service: Service, ref_id: str, user: CurrentUser, level: Literal["read", "manage"] = "read") -> ReferentialConfig:
    ref = get_ref(service, ref_id, user, level)
    if not ref.is_internal:
        raise HTTPException(400, "not an internal referential: its rows come from its source")
    return ref


def _plain_key(ref: ReferentialConfig, r: InternalRecord, rec: dict[str, Any] | None = None) -> str:
    """Key of a stored row (the row_key column of an encrypted row is a fingerprint)."""
    if not is_encrypted(r.data):
        return r.row_key
    return str((rec if rec is not None else load(ref.id, r.data)).get(ref.key))


def _row_dict(ref: ReferentialConfig, r: InternalRecord) -> dict[str, Any]:
    rec = load(ref.id, r.data)
    return {**rec, "_key": _plain_key(ref, r, rec), "_updated_at": r.updated_at, "_updated_by": r.updated_by}


def _find(db: Session, ref: ReferentialConfig, key: str) -> InternalRecord | None:
    return db.scalar(select(InternalRecord).where(InternalRecord.referential_id == ref.id, InternalRecord.row_key == db_key(ref, key)))


def _set(r: InternalRecord, ref: ReferentialConfig, key: str, rec: dict[str, Any], username: str) -> None:
    r.row_key, r.data = store(ref, key, rec)
    r.updated_by = username


def _new(ref: ReferentialConfig, key: str, rec: dict[str, Any], username: str) -> InternalRecord:
    row_key, data = store(ref, key, rec)
    return InternalRecord(referential_id=ref.id, row_key=row_key, data=data, created_by=username, updated_by=username)


def _existing(db: Session, ref: ReferentialConfig, keys: Iterable[str] | None = None) -> dict[str, InternalRecord]:
    """Stored rows by key: all of them, or only those of `keys`."""
    base = select(InternalRecord).where(InternalRecord.referential_id == ref.id)
    if keys is None:
        return {_plain_key(ref, r): r for r in db.scalars(base)}
    by_db_key = {db_key(ref, k): k for k in set(keys)}
    stored = sorted(by_db_key)
    out: dict[str, InternalRecord] = {}
    for i in range(0, len(stored), 1000):  # bounded IN lists
        out.update((by_db_key[r.row_key], r) for r in db.scalars(base.where(InternalRecord.row_key.in_(stored[i:i + 1000]))))
    return out


def _audit_key(ref: ReferentialConfig, key: Any) -> Any:
    """Keys of a confidential referential are data: not written to the audit log."""
    return "***" if ref.confidential and key is not None else key


# Searches made of these characters are pre-filtered in the database on the JSON text of the rows (a superset
# of the matches, then checked exactly); others (quotes, brackets, accents...) may be escaped in the JSON text
_PREFILTER = re.compile(r"^[A-Za-z0-9 _.@:/+-]+$")


def _matches(key: str, rec: dict[str, Any], needle: str) -> bool:
    return needle in key.lower() or any(needle in str(v).lower() for v in rec.values() if v is not None)


def _apply(db: Session, ref: ReferentialConfig, user: CurrentUser, upserts: list[dict[str, Any]], deletes: list[str],
           replace_all: bool = False, create_only: bool = False) -> dict[str, Any]:
    """Apply a batch of changes atomically. Raises HTTPException(400/409) with the faulty row."""
    if len(upserts) + len(deletes) > MAX_BULK:
        raise HTTPException(413, f"at most {MAX_BULK:,} changes per request")
    normalized: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()
    for i, data in enumerate(upserts):
        try:
            key, rec = normalize(ref, data)
        except RecordError as e:
            raise HTTPException(400, f"row {i + 1}: {e}") from e
        if key in seen:
            raise HTTPException(400, f"row {i + 1}: duplicate key '{key}' in the request")
        seen.add(key)
        normalized.append((key, rec))
    # Only the rows concerned are loaded, unless the whole content is replaced
    existing = _existing(db, ref, None if replace_all else [*seen, *map(str, deletes)])
    created = updated = unchanged = 0
    for i, (key, rec) in enumerate(normalized):
        row = existing.get(key)
        if row is None:
            db.add(_new(ref, key, rec, user.username))
            created += 1
        elif create_only:
            raise HTTPException(409, f"row {i + 1}: key '{key}' already exists")
        elif load(ref.id, row.data) != rec or is_encrypted(row.data) != ref.confidential:
            _set(row, ref, key, rec, user.username)
            updated += 1
        else:
            unchanged += 1
    to_delete = set(deletes)
    if replace_all:
        to_delete |= set(existing) - seen
    deleted = 0
    for key in to_delete:
        row = existing.get(str(key))
        if row is not None:
            db.delete(row)
            deleted += 1
    return {"created": created, "updated": updated, "unchanged": unchanged, "deleted": deleted}


def _commit(db: Session, service: Service, ref: ReferentialConfig, user: CurrentUser, request: Request, action: str, detail: dict[str, Any]) -> None:
    audit(db, action, user=user, target=f"ref:{ref.id}", request=request, detail=detail)  # commits
    service.publish_internal(ref.id, user.username)


# --------------------------------------------------------------------------- rows

@router.get("/referentials/{ref_id}/records", summary="Internal referential: editable rows")
def list_records(
    ref_id: str,
    q: str | None = None,
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    service: Service = Depends(get_service),
    user: CurrentUser = Depends(require_user),
    db: Session = Depends(get_db),
):
    ref = _internal(service, ref_id, user)
    base = select(InternalRecord).where(InternalRecord.referential_id == ref_id)
    needle = (q or "").strip().lower()
    if ref.confidential:
        # Encrypted rows: decrypted, filtered and sorted here (the database only holds ciphertexts)
        rows = []
        for r in db.scalars(base):
            rec = load(ref.id, r.data)
            key = _plain_key(ref, r, rec)
            if not needle or _matches(key, rec, needle):
                rows.append((key, r))
        rows.sort(key=lambda x: x[0])
        total, page = len(rows), [r for _, r in rows[offset: offset + limit]]
    elif not needle:  # paginated by the database
        total = db.scalar(select(func.count()).select_from(base.subquery()))
        page = list(db.scalars(base.order_by(InternalRecord.row_key).offset(offset).limit(limit)))
    else:
        if _PREFILTER.match(needle):
            text = func.lower(cast(InternalRecord.data, String))
            base = base.where(or_(func.lower(InternalRecord.row_key).contains(needle, autoescape=True), text.contains(needle, autoescape=True)))
        rows = [r for r in db.scalars(base.order_by(InternalRecord.row_key)) if _matches(r.row_key, load(ref.id, r.data), needle)]
        total, page = len(rows), rows[offset: offset + limit]
    return {
        "total": total,
        "columns": [c.model_dump() for c in ref.columns],
        "key": ref.key,
        "records": [_row_dict(ref, r) for r in page],
        "can_edit": user.can_manage(ref_id),
    }


@router.get("/referentials/{ref_id}/records/{key:path}", summary="Internal referential: one row")
def get_record(ref_id: str, key: str, service: Service = Depends(get_service), user: CurrentUser = Depends(require_user), db: Session = Depends(get_db)):
    ref = _internal(service, ref_id, user)
    row = _find(db, ref, key)
    if not row:
        raise HTTPException(404, f"no row with key '{key}'")
    return _row_dict(ref, row)


@router.post("/referentials/{ref_id}/records", summary="Internal referential: add row(s)", status_code=201)
def create_records(
    ref_id: str,
    request: Request,
    body: dict[str, Any] = Body(..., examples=[{"code": "FR", "label": "France"}, {"records": [{"code": "FR"}, {"code": "DE"}]}]),
    upsert: bool = Query(False, description="Update rows whose key already exists instead of failing"),
    service: Service = Depends(get_service),
    user: CurrentUser = Depends(require_user),
    db: Session = Depends(get_db),
):
    ref = _internal(service, ref_id, user, "manage")
    single = not isinstance(body.get("records"), list)
    records = [body] if single else body["records"]
    if single:  # normalise first so that a generated key is known
        try:
            key, rec = normalize(ref, body)
        except RecordError as e:
            raise HTTPException(400, str(e)) from e
        records = [rec]
    result = _apply(db, ref, user, records, [], create_only=not upsert)
    keys = [] if ref.confidential else [str(r.get(ref.key)) for r in records[:20]]
    _commit(db, service, ref, user, request, "record.create", {**result, "keys": keys})
    if single:
        return _row_dict(ref, _find(db, ref, key))
    return result


@router.put("/referentials/{ref_id}/records/{key:path}", summary="Internal referential: create or replace a row")
def replace_record(ref_id: str, key: str, request: Request, body: dict[str, Any] = Body(...), service: Service = Depends(get_service),
                   user: CurrentUser = Depends(require_user), db: Session = Depends(get_db)):
    ref = _internal(service, ref_id, user, "manage")
    data = {ref.key: key, **body} if body.get(ref.key) in (None, "") else body
    try:
        new_key, rec = normalize(ref, data)
    except RecordError as e:
        raise HTTPException(400, str(e)) from e
    row = _find(db, ref, key)
    if new_key != key:  # key change (rename)
        if _find(db, ref, new_key):
            raise HTTPException(409, f"key '{new_key}' already exists")
    if row is None:
        row = _new(ref, new_key, rec, user.username)
        db.add(row)
    else:
        _set(row, ref, new_key, rec, user.username)
    db.flush()
    out = _row_dict(ref, row)
    _commit(db, service, ref, user, request, "record.update",
            {"key": _audit_key(ref, key), "new_key": _audit_key(ref, new_key) if new_key != key else None})
    return out


@router.patch("/referentials/{ref_id}/records/{key:path}", summary="Internal referential: update some columns of a row")
def patch_record(ref_id: str, key: str, request: Request, body: dict[str, Any] = Body(...), service: Service = Depends(get_service),
                 user: CurrentUser = Depends(require_user), db: Session = Depends(get_db)):
    ref = _internal(service, ref_id, user, "manage")
    row = _find(db, ref, key)
    if not row:
        raise HTTPException(404, f"no row with key '{key}'")
    if ref.key in body and str(body[ref.key]) != key:
        raise HTTPException(400, "use PUT to change the key of a row")
    try:
        _, rec = normalize(ref, body, base=load(ref.id, row.data))
    except RecordError as e:
        raise HTTPException(400, str(e)) from e
    _set(row, ref, key, rec, user.username)
    db.flush()
    out = _row_dict(ref, row)
    _commit(db, service, ref, user, request, "record.update", {"key": _audit_key(ref, key), "columns": sorted(body)})
    return out


@router.delete("/referentials/{ref_id}/records/{key:path}", summary="Internal referential: delete a row")
def delete_record(ref_id: str, key: str, request: Request, service: Service = Depends(get_service),
                  user: CurrentUser = Depends(require_user), db: Session = Depends(get_db)):
    ref = _internal(service, ref_id, user, "manage")
    row = _find(db, ref, key)
    if not row:
        raise HTTPException(404, f"no row with key '{key}'")
    db.delete(row)
    _commit(db, service, ref, user, request, "record.delete", {"key": _audit_key(ref, key)})
    return {"deleted": key}


class BulkIn(BaseModel):
    upsert: list[dict[str, Any]] = Field(default_factory=list, description="Rows to create or update (matched on the key)")
    delete: list[str] = Field(default_factory=list, description="Keys of the rows to delete")
    replace_all: bool = Field(False, description="Delete every row absent from `upsert` (full synchronisation)")


@router.post("/referentials/{ref_id}/records/_bulk", summary="Internal referential: synchronise many rows at once")
def bulk_records(ref_id: str, body: BulkIn, request: Request, service: Service = Depends(get_service),
                 user: CurrentUser = Depends(require_user), db: Session = Depends(get_db)):
    ref = _internal(service, ref_id, user, "manage")
    result = _apply(db, ref, user, body.upsert, body.delete, body.replace_all)
    _commit(db, service, ref, user, request, "record.bulk", {**result, "replace_all": body.replace_all})
    return result


@router.post("/referentials/{ref_id}/records/_import", summary="Internal referential: load rows from a file (CSV, Excel, JSON)")
def import_records(
    ref_id: str,
    request: Request,
    file: UploadFile = File(...),
    mode: Literal["upsert", "replace"] = Form("upsert", description="upsert: add/update · replace: the file becomes the full content"),
    service: Service = Depends(get_service),
    user: CurrentUser = Depends(require_user),
    db: Session = Depends(get_db),
):
    ref = _internal(service, ref_id, user, "manage")
    from ..preview import detect_format, store_upload
    from ..sqlbuild import reader_expr

    stored = store_upload(service.settings, [(file.filename or "file", file.file)])
    folder = service.settings.tmp_dir / "uploads" / stored["upload_id"]
    try:
        files = sorted(p for p in folder.rglob("*") if p.is_file())
        fmt, _ = detect_format(files)
        if fmt not in ("csv", "tsv", "json", "jsonl", "xlsx", "parquet"):
            raise HTTPException(400, f"unsupported file format for an import: {fmt}")
        con = duckdb.connect()
        if fmt == "xlsx":
            con.execute("LOAD excel")
        options = {"all_varchar": True} if fmt in ("csv", "tsv") else {}
        res = con.execute(f"SELECT * FROM {reader_expr(fmt, [str(files[0])], options)} LIMIT {MAX_BULK + 1}")
        cols = [d[0] for d in res.description]
        records = rows_to_records(cols, res.fetchall())
        if len(records) > MAX_BULK:
            raise HTTPException(413, f"at most {MAX_BULK:,} rows per import")
        known = {c.name for c in ref.columns}
        records = [{k: v for k, v in r.items() if k in known} for r in records]
        if not any(records):
            raise HTTPException(400, f"no column of the file matches the schema ({', '.join(known)})")
        result = _apply(db, ref, user, records, [], replace_all=mode == "replace")
    except duckdb.Error as e:
        raise HTTPException(400, f"unreadable file: {e}") from e
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    _commit(db, service, ref, user, request, "record.import", {**result, "file": file.filename, "mode": mode})
    return {**result, "rows_in_file": len(records)}


# --------------------------------------------------------------------------- internal referentials

class InternalIn(BaseModel):
    id: str
    name: str
    description: str = ""
    category: str = "Internal"
    tags: list[str] = Field(default_factory=list)
    owner: str | None = None
    key: str
    columns: list[dict[str, Any]]
    search_columns: list[str] = Field(default_factory=list)
    confidential: bool = Field(False, description="Rows encrypted in the database, published files encrypted at rest")
    renames: dict[str, str] = Field(default_factory=dict, description="Schema update: {old column name: new name}")


def _require_creator(user: CurrentUser) -> None:
    if user.role not in ("admin", "advanced"):
        raise HTTPException(403, "reserved to administrators and advanced users")


def _definition(body: InternalIn) -> dict[str, Any]:
    return {
        "name": body.name, "description": body.description, "category": body.category or "Internal", "tags": body.tags,
        "owner": body.owner, "key": body.key, "columns": body.columns, "search_columns": body.search_columns,
        "source": {"type": "internal"}, "format": "jsonl", "confidential": body.confidential,
    }


@router.post("/internal-referentials", summary="Create an internal referential (admins, advanced users)", status_code=201)
def create_internal(body: InternalIn, request: Request, service: Service = Depends(get_service),
                    user: CurrentUser = Depends(require_user), db: Session = Depends(get_db)):
    _require_creator(user)
    ref_id = body.id.strip()
    try:
        ref = validate_definition({**_definition(body), "id": ref_id})
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    if ref_id in service.refs or db.get(ReferentialDefinition, ref_id):
        raise HTTPException(409, f"identifier '{ref_id}' is already used")
    shutil.rmtree(RefPaths(service.settings.data_dir, ref_id).root, ignore_errors=True)
    db.add(ReferentialDefinition(id=ref_id, config=ref.model_dump(mode="json", exclude={"id"}), created_by=user.username, updated_by=user.username))
    if not user.is_admin:  # the creator manages its referential
        db.add(Grant(referential_id=ref_id, user_id=user.id, level="manage", created_by=user.username))
    audit(db, "internal.create", user=user, target=f"ref:{ref_id}", request=request, detail={"columns": [c["name"] for c in body.columns]})
    service.reload()
    service.publish_internal(ref_id, user.username, delay=0)
    return service.detail(service.refs[ref_id], "manage")


@router.put("/internal-referentials/{ref_id}", summary="Update the description and the columns of an internal referential")
def update_internal(ref_id: str, body: InternalIn, request: Request, service: Service = Depends(get_service),
                    user: CurrentUser = Depends(require_user), db: Session = Depends(get_db)):
    _require_creator(user)
    old = _internal(service, ref_id, user, "manage")
    try:
        ref = validate_definition({**_definition(body), "id": ref_id})
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    # Migrate the stored rows to the new schema (renames, removed columns, type changes)
    rows = list(db.scalars(select(InternalRecord).where(InternalRecord.referential_id == ref_id)))
    errors: list[str] = []
    migrated: list[tuple[InternalRecord, str, dict[str, Any]]] = []
    for r in rows:
        data = {body.renames.get(k, k): v for k, v in load(ref_id, r.data).items()}
        data = {k: v for k, v in data.items() if k in {c.name for c in ref.columns}}
        try:
            key, rec = normalize(ref, data)
            migrated.append((r, key, rec))
        except RecordError as e:
            errors.append(f"row '{_audit_key(ref, _plain_key(old, r))}': {e}")
    if errors:
        raise HTTPException(400, f"{len(errors)} existing row(s) do not fit the new columns: " + "; ".join(errors[:5]))
    if len({k for _, k, _ in migrated}) != len(migrated):
        raise HTTPException(400, "the new key would not be unique")
    for r, key, rec in migrated:
        r.row_key, r.data = store(ref, key, rec)
    definition = db.get(ReferentialDefinition, ref_id)
    definition.config, definition.updated_by = ref.model_dump(mode="json", exclude={"id"}), user.username
    audit(db, "internal.update", user=user, target=f"ref:{ref_id}", request=request,
          detail={"columns": [c.name for c in ref.columns], "previous": [c.name for c in old.columns], "renames": body.renames})
    service.reload()
    service.publish_internal(ref_id, user.username, delay=0)
    return service.detail(service.refs[ref_id], "manage")


@router.delete("/internal-referentials/{ref_id}", summary="Delete an internal referential and its rows")
def delete_internal(ref_id: str, request: Request, service: Service = Depends(get_service),
                    user: CurrentUser = Depends(require_user), db: Session = Depends(get_db)):
    _require_creator(user)
    _internal(service, ref_id, user, "manage")
    service.discard_scheduled(ref_id)
    if ref_id in service.active:
        raise HTTPException(409, "a publication is running, retry in a moment")
    db.execute(delete(InternalRecord).where(InternalRecord.referential_id == ref_id))
    db.execute(delete(Grant).where(Grant.referential_id == ref_id))
    d = db.get(ReferentialDefinition, ref_id)
    if d:
        db.delete(d)
    audit(db, "internal.delete", user=user, target=f"ref:{ref_id}", request=request)
    service.reload()
    shutil.rmtree(RefPaths(service.settings.data_dir, ref_id).root, ignore_errors=True)
    return {"ok": True}

