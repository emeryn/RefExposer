"""Encryption of secrets stored in the database (LDAP bind password, OIDC client secret, proxy password).

AES-256-GCM (authenticated encryption). A 256-bit key keeps a 128-bit margin against Grover's
algorithm. The key is derived (HKDF-SHA256) from REFEX_SECRET_KEY, or generated once and kept in
data/.secret_key (permissions 600) when the variable is not set.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import secrets
from functools import lru_cache
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .settings import get_settings

log = logging.getLogger(__name__)
PREFIX = "enc:v1:"


def _key_file() -> Path:
    return get_settings().data_dir / ".secret_key"


MIN_KEY_LENGTH = 32


def check_key() -> None:
    """Refuse a weak REFEX_SECRET_KEY at startup (e.g. a forgotten placeholder)."""
    value = (get_settings().secret_key or "").strip()
    if value and len(value) < MIN_KEY_LENGTH:
        raise RuntimeError(
            f"REFEX_SECRET_KEY is too short ({len(value)} characters, minimum {MIN_KEY_LENGTH}): "
            "generate it with 'openssl rand -base64 48' or leave it empty for an automatically generated key"
        )


def key_source() -> str:
    return "env" if (get_settings().secret_key or "").strip() else "file"


@lru_cache
def _key() -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=b"refexposer-settings", info=b"aes-256-gcm").derive(_material())


@lru_cache
def _material() -> bytes:
    """Master secret: REFEX_SECRET_KEY, or data/.secret_key (generated once)."""
    material = (get_settings().secret_key or "").strip().encode()
    if not material:
        path = _key_file()
        if path.exists():
            material = path.read_bytes().strip()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            material = base64.b64encode(secrets.token_bytes(32))
            path.write_bytes(material)
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
            log.warning("Encryption key generated in %s: back it up or set REFEX_SECRET_KEY", path)
    return material


def derive(purpose: str, length: int = 32) -> bytes:
    """Independent key for one purpose (HKDF-SHA256 of the master secret): a key never serves two uses."""
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=b"refexposer-data", info=purpose.encode()).derive(_material())


def encrypt(value: str) -> str:
    nonce = secrets.token_bytes(12)
    ct = AESGCM(_key()).encrypt(nonce, value.encode(), None)
    return PREFIX + base64.b64encode(nonce + ct).decode()


def decrypt(value: str) -> str:
    if not value.startswith(PREFIX):
        return value
    raw = base64.b64decode(value[len(PREFIX):])
    return AESGCM(_key()).decrypt(raw[:12], raw[12:], None).decode()


def reset_cache() -> None:
    _key.cache_clear()
    _material.cache_clear()
    _record_key.cache_clear()
    _blind_key.cache_clear()
    parquet_key.cache_clear()


# --------------------------------------------------------------------------- confidential referentials

@lru_cache(maxsize=512)
def parquet_key(ref_id: str) -> tuple[str, str]:
    """(name, key) of the Parquet Modular Encryption key of a referential (AES-GCM, DuckDB). The key is 32
    characters (a 256-bit DuckDB key) carrying 192 random bits; its name is not secret."""
    name = "rk_" + hashlib.sha256(ref_id.encode()).hexdigest()[:20]
    return name, base64.b64encode(derive(f"parquet:{ref_id}", 24)).decode()


@lru_cache
def _record_key() -> bytes:
    return derive("internal-records")


@lru_cache
def _blind_key() -> bytes:
    return derive("internal-record-keys")


def encrypt_data(value: str, context: str) -> str:
    """AES-256-GCM, bound to `context` (e.g. the referential id): a value cannot be moved to another referential."""
    nonce = secrets.token_bytes(12)
    ct = AESGCM(_record_key()).encrypt(nonce, value.encode(), context.encode())
    return PREFIX + base64.b64encode(nonce + ct).decode()


def decrypt_data(value: str, context: str) -> str:
    raw = base64.b64decode(value[len(PREFIX):])
    return AESGCM(_record_key()).decrypt(raw[:12], raw[12:], context.encode()).decode()


def blind_index(context: str, value: str) -> str:
    """Deterministic keyed fingerprint (HMAC-SHA256): uniqueness and exact lookups without storing the value."""
    return "h:" + hmac.new(_blind_key(), f"{context}\0{value}".encode(), hashlib.sha256).hexdigest()
