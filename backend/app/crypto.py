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
    return settings_key(_material())


def settings_key(material: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=b"refexposer-settings", info=b"aes-256-gcm").derive(material)


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
    return derive_with(_material(), purpose, length)


def derive_with(material: bytes, purpose: str, length: int = 32) -> bytes:
    """`derive` under another master secret: what reads an instance encrypted with a key that is not ours
    (restoring a full backup made elsewhere, see rekey.py). Never cached: a foreign key is held no longer
    than the operation it was given to."""
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=b"refexposer-data", info=purpose.encode()).derive(material)


# --------------------------------------------------------------------------- the master secret itself

def material() -> bytes:
    """The master secret of this instance. Only what re-encrypts an instance has any business reading it."""
    return _material()


def read_material(value: str) -> bytes:
    """Master secret given by hand: REFEX_SECRET_KEY of the other instance, or the content of its
    data/.secret_key file. Written exactly as it is there — surrounding spaces and newlines are dropped,
    nothing else is interpreted."""
    raw = (value or "").strip()
    if not raw:
        raise ValueError("no encryption key given")
    return raw.encode()


def fingerprint(material: bytes) -> str:
    """Short public name of a master secret: two instances sharing a key share this value, and nothing of the
    key can be read back from it (HKDF-SHA256, 64 bits kept)."""
    return derive_with(material, "key-fingerprint", 8).hex()


def key_fingerprint() -> str:
    return fingerprint(_material())


def seal_with(key: bytes, value: str, context: str | None = None) -> str:
    nonce = secrets.token_bytes(12)
    ct = AESGCM(key).encrypt(nonce, value.encode(), context.encode() if context is not None else None)
    return PREFIX + base64.b64encode(nonce + ct).decode()


def open_with(key: bytes, value: str, context: str | None = None) -> str:
    raw = base64.b64decode(value[len(PREFIX):])
    return AESGCM(key).decrypt(raw[:12], raw[12:], context.encode() if context is not None else None).decode()


def encrypt(value: str) -> str:
    return seal_with(_key(), value)


def decrypt(value: str) -> str:
    if not value.startswith(PREFIX):
        return value
    return open_with(_key(), value)


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
    return parquet_key_with(_material(), ref_id)


def parquet_key_with(material: bytes, ref_id: str, suffix: str = "") -> tuple[str, str]:
    """`parquet_key` under another master secret. `suffix` gives the key another *name*: re-encrypting a file
    registers the old and the new key on one connection, which two identical names could not do."""
    name = "rk_" + hashlib.sha256(ref_id.encode()).hexdigest()[:20] + suffix
    return name, base64.b64encode(derive_with(material, f"parquet:{ref_id}", 24)).decode()


@lru_cache
def _record_key() -> bytes:
    return record_key(_material())


def record_key(material: bytes) -> bytes:
    return derive_with(material, "internal-records")


@lru_cache
def _blind_key() -> bytes:
    return blind_key(_material())


def blind_key(material: bytes) -> bytes:
    return derive_with(material, "internal-record-keys")


def encrypt_data(value: str, context: str) -> str:
    """AES-256-GCM, bound to `context` (e.g. the referential id): a value cannot be moved to another referential."""
    return seal_with(_record_key(), value, context)


def decrypt_data(value: str, context: str) -> str:
    return open_with(_record_key(), value, context)


def blind_index(context: str, value: str) -> str:
    """Deterministic keyed fingerprint (HMAC-SHA256): uniqueness and exact lookups without storing the value."""
    return blind_with(_blind_key(), context, value)


def blind_with(key: bytes, context: str, value: str) -> str:
    return "h:" + hmac.new(key, f"{context}\0{value}".encode(), hashlib.sha256).hexdigest()
