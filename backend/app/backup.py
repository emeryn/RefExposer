"""Configuration backups: referential definitions, accounts, groups, rights, API tokens, settings and the rows
of internal referentials, in one dated file, restorable.

The file holds password hashes, token fingerprints and encrypted secrets: it is encrypted (AES-256-GCM, key
derived from REFEX_SECRET_KEY) unless asked otherwise, and can only be restored with the same key. It does not
replace the backup of the database and of data/ (published versions, history, audit log).
"""

from __future__ import annotations

import gzip
import json
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import DateTime, Integer, delete, insert, select, text
from sqlalchemy.orm import Session

from . import __version__
from .crypto import derive
from .models import (
    ApiToken,
    AppSetting,
    AuthSession,
    Grant,
    Group,
    InternalRecord,
    ReferentialDefinition,
    User,
    WebauthnCredential,
    user_groups,
)

FORMAT = "refexposer-config-backup"
MAGIC = b"RFXBK1"
NAME_RE = re.compile(r"^refexposer-config-\d{8}-\d{6}\.json\.gz(\.enc)?$")
# Insertion order (foreign keys); deletion uses the reverse order
TABLES = [AppSetting.__table__, ReferentialDefinition.__table__, Group.__table__, User.__table__, user_groups,
          Grant.__table__, ApiToken.__table__, WebauthnCredential.__table__, InternalRecord.__table__]


class BackupError(ValueError):
    pass


def backup_dir(data_dir: Path) -> Path:
    return data_dir / ".backups"


def _key() -> bytes:
    return derive("config-backup")


def _value(v: Any) -> Any:
    return v.isoformat() if isinstance(v, datetime) else v


def export(db: Session, config_dir: Path | None = None) -> dict[str, Any]:
    tables = {t.name: [{k: _value(v) for k, v in row._mapping.items()} for row in db.execute(select(t))] for t in TABLES}
    files = {}
    if config_dir and config_dir.is_dir():
        files = {p.name: p.read_text(encoding="utf-8") for p in sorted(config_dir.glob("*.y*ml")) if p.is_file()}
    return {"format": FORMAT, "version": 1, "app_version": __version__, "created_at": datetime.now(timezone.utc).isoformat(),
            "tables": tables, "config_files": files, "counts": {k: len(v) for k, v in tables.items()}}


def write(db: Session, data_dir: Path, config_dir: Path | None, encrypt: bool = True, keep: int = 14) -> Path:
    payload = gzip.compress(json.dumps(export(db, config_dir), separators=(",", ":")).encode(), compresslevel=6)
    name = f"refexposer-config-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.json.gz" + (".enc" if encrypt else "")
    if encrypt:
        nonce = secrets.token_bytes(12)
        payload = MAGIC + nonce + AESGCM(_key()).encrypt(nonce, payload, MAGIC)
    folder = backup_dir(data_dir)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / name
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_bytes(payload)
    tmp.replace(target)
    for old in list_files(data_dir)[keep:]:  # newest first
        (folder / old["name"]).unlink(missing_ok=True)
    return target


def list_files(data_dir: Path) -> list[dict[str, Any]]:
    folder = backup_dir(data_dir)
    if not folder.is_dir():
        return []
    files = [p for p in folder.iterdir() if p.is_file() and NAME_RE.match(p.name)]
    return [{"name": p.name, "size": p.stat().st_size, "encrypted": p.name.endswith(".enc"),
             "created_at": datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).isoformat()}
            for p in sorted(files, key=lambda p: p.name, reverse=True)]


def path_of(data_dir: Path, name: str) -> Path:
    if not NAME_RE.match(name):
        raise BackupError("invalid backup name")
    p = backup_dir(data_dir) / name
    if not p.is_file():
        raise BackupError("unknown backup")
    return p


def read(raw: bytes) -> dict[str, Any]:
    if raw.startswith(MAGIC):
        try:
            raw = AESGCM(_key()).decrypt(raw[len(MAGIC):len(MAGIC) + 12], raw[len(MAGIC) + 12:], MAGIC)
        except Exception as e:  # noqa: BLE001
            raise BackupError("cannot decrypt the backup: it was made with another REFEX_SECRET_KEY, or is damaged") from e
    try:
        data = json.loads(gzip.decompress(raw))
    except (OSError, ValueError) as e:
        raise BackupError("not a RefExposer configuration backup") from e
    if data.get("format") != FORMAT or not isinstance(data.get("tables"), dict):
        raise BackupError("not a RefExposer configuration backup")
    return data


def restore(db: Session, data: dict[str, Any]) -> dict[str, int]:
    """Replace the configuration by the backup, in one transaction. Every session is closed."""
    rows_by_table = data["tables"]
    db.execute(delete(AuthSession.__table__))
    for t in reversed(TABLES):
        db.execute(delete(t))
    counts = {}
    for t in TABLES:
        dates = {c.name for c in t.columns if isinstance(c.type, DateTime)}
        rows = [{k: (datetime.fromisoformat(v) if k in dates and isinstance(v, str) else v) for k, v in r.items() if k in t.c}
                for r in rows_by_table.get(t.name, [])]
        if rows:
            db.execute(insert(t), rows)
        counts[t.name] = len(rows)
    if db.get_bind().dialect.name == "postgresql":  # explicit ids were inserted: move the sequences after them
        for t in TABLES:
            if "id" in t.c and isinstance(t.c.id.type, Integer):
                db.execute(text(f"SELECT setval(pg_get_serial_sequence('{t.name}', 'id'), COALESCE((SELECT MAX(id) FROM {t.name}), 0) + 1, false)"))
    db.commit()
    return counts
