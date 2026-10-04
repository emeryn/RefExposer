"""Storage form of the rows of internal referentials.

Rows of a confidential referential are encrypted in the database (AES-256-GCM bound to the referential id),
and their key is replaced by a keyed fingerprint (HMAC-SHA256): uniqueness and lookups by key still work,
but neither the values nor the keys can be read from the database or its backups. Each stored row says
whether it is encrypted ({"_enc": ...}), so reading never depends on the current setting.
"""

from __future__ import annotations

import json
from typing import Any

from .config import ReferentialConfig
from .crypto import blind_index, decrypt_data, encrypt_data

ENC = "_enc"


def db_key(ref: ReferentialConfig, key: str) -> str:
    """Value of the row_key column for a key."""
    return blind_index(ref.id, key) if ref.confidential else key


def store(ref: ReferentialConfig, key: str, record: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """(row_key, data) to write for a validated record."""
    if not ref.confidential:
        return key, record
    return blind_index(ref.id, key), {ENC: encrypt_data(json.dumps(record, separators=(",", ":")), ref.id)}


def load(ref_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """Record of a stored row (decrypted when needed)."""
    if isinstance(data, dict) and ENC in data:
        return json.loads(decrypt_data(data[ENC], ref_id))
    return data


def is_encrypted(data: dict[str, Any]) -> bool:
    return isinstance(data, dict) and ENC in data
