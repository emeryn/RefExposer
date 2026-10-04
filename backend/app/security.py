"""Password and token hashing.

Passwords: Argon2id (RFC 9106), memory-hard, 256-bit digest. There is no standardized
"post-quantum" password hash: quantum computers only offer a quadratic speed-up (Grover)
on preimage search, which a 256-bit digest (128-bit post-quantum margin) and the memory
cost of Argon2id absorb. Parameters are configurable and hashes are upgraded on login
when they change.

Session / API tokens: 256 bits of randomness, stored as SHA3-256 digests (a fast hash is
appropriate for high-entropy secrets and allows constant-time lookup by digest).
"""

from __future__ import annotations

import hashlib
import secrets
from functools import lru_cache

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from .settings import get_settings

TOKEN_PREFIX = "rfx_"


@lru_cache
def hasher() -> PasswordHasher:
    s = get_settings()
    return PasswordHasher(
        time_cost=s.argon2_time_cost,
        memory_cost=s.argon2_memory_kib,
        parallelism=s.argon2_parallelism,
        hash_len=32,
        salt_len=16,
        type=Type.ID,
    )


def hash_password(password: str) -> str:
    return hasher().hash(password)


def verify_password(stored_hash: str, password: str) -> tuple[bool, bool]:
    """Return (valid, needs_rehash)."""
    try:
        hasher().verify(stored_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False, False
    return True, hasher().check_needs_rehash(stored_hash)


@lru_cache
def _dummy_hash() -> str:
    return hash_password(secrets.token_hex(16))


def burn_verification(password: str) -> None:
    """Spend the same time as a real verification (unknown user) to avoid user enumeration."""
    verify_password(_dummy_hash(), password)


def hash_token(token: str) -> str:
    return hashlib.sha3_256(token.encode()).hexdigest()


def new_session_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    return token, hash_token(token)


def new_api_token() -> tuple[str, str, str]:
    """Return (token, prefix shown in the UI, digest)."""
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    return token, token[:12], hash_token(token)


def password_problems(password: str, username: str | None = None) -> list[str]:
    s = get_settings()
    problems = []
    if len(password) < s.password_min_length:
        problems.append(f"at least {s.password_min_length} characters")
    classes = sum(
        bool(any(f(c) for c in password))
        for f in (str.islower, str.isupper, str.isdigit, lambda c: not c.isalnum())
    )
    if classes < 3:
        problems.append("at least 3 kinds of characters among lowercase, uppercase, digits and symbols")
    if username and username.lower() in password.lower():
        problems.append("must not contain the username")
    return problems
