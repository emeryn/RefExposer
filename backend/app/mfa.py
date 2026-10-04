"""Second factor: time-based one-time passwords (TOTP, RFC 6238), compatible with Google Authenticator,
Microsoft Authenticator, FreeOTP, 1Password..., and security keys / passkeys (WebAuthn, FIDO2).

- For local and LDAP accounts only: OpenID Connect accounts get their second factor from the identity
  provider, service accounts only use API tokens. When single sign-on is the only way to sign in (local
  sign-in disabled and no LDAP directory), the feature is off.
- Optional by default; administrators can require it for everybody or for some groups.
- 6 digits, 30-second steps, one step of tolerance, a code is accepted once (replay protection).
- WebAuthn: any number of security keys / passkeys per account; signature counter checked (cloned keys).
- 10 one-time recovery codes, shared by both methods, stored as fingerprints.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import struct
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from .crypto import PREFIX, decrypt, encrypt
from .security import hash_token

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from .models import User

DIGITS, PERIOD, WINDOW = 6, 30, 1
CHALLENGE_TTL = 300  # seconds to type the code after the password
RECOVERY_CODES = 10


# --------------------------------------------------------------------------- TOTP

def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _code(secret: str, step: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10 ** DIGITS).zfill(DIGITS)


def current_code(secret: str, at: float | None = None) -> str:
    return _code(secret, int((at or time.time()) // PERIOD))


def match(secret: str, code: str, last_step: int | None, at: float | None = None) -> int | None:
    """Time step matched by `code` (None: wrong code or already used)."""
    code = "".join(c for c in str(code) if c.isdigit())
    if len(code) != DIGITS:
        return None
    now = int((at or time.time()) // PERIOD)
    for step in range(now - WINDOW, now + WINDOW + 1):
        if hmac.compare_digest(_code(secret, step), code):
            return step if last_step is None or step > last_step else None
    return None


def otpauth_uri(secret: str, account: str, issuer: str) -> str:
    label = quote(f"{issuer}:{account}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}&algorithm=SHA1&digits={DIGITS}&period={PERIOD}"


def qr_svg(uri: str) -> str:
    import segno

    return segno.make(uri, error="m").svg_data_uri(scale=5, border=2)


# --------------------------------------------------------------------------- recovery codes

def new_recovery_codes() -> tuple[list[str], list[str]]:
    """(codes shown once, fingerprints stored)."""
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    codes = ["".join(secrets.choice(alphabet) for _ in range(4)) + "-" + "".join(secrets.choice(alphabet) for _ in range(4))
             for _ in range(RECOVERY_CODES)]
    return codes, [hash_token(c) for c in codes]


def use_recovery_code(user: "User", code: str) -> bool:
    digest = hash_token(code.strip().lower())
    stored = list(user.recovery_codes or [])
    if digest not in stored:
        return False
    stored.remove(digest)
    user.recovery_codes = stored
    return True


# --------------------------------------------------------------------------- per-user state

def applies_to(user: "User") -> bool:
    """Accounts concerned by the second factor of RefExposer."""
    return user.auth_source in ("local", "ldap")


def available(db: "Session") -> bool:
    """Off when single sign-on is the only way to sign in."""
    from . import appsettings
    from .settings import get_settings

    return get_settings().local_login or bool(appsettings.load(db, "ldap").enabled)


def required(db: "Session", user: "User") -> bool:
    from . import appsettings

    policy = appsettings.load(db, "mfa")
    if not applies_to(user) or not available(db) or policy.mode == "optional":
        return False
    if policy.mode == "all":
        return True
    wanted = {g.lower() for g in policy.groups}
    return any(g.name.lower() in wanted for g in user.groups)


def secret_of(user: "User") -> str | None:
    return decrypt(user.totp_secret) if user.totp_secret else None


def verify(user: "User", code: str) -> bool:
    """TOTP code (when enabled) or recovery code; records the step used (replay protection)."""
    secret = secret_of(user) if user.totp_enabled else None
    if secret:
        step = match(secret, code, user.totp_last_step)
        if step is not None:
            user.totp_last_step = step
            return True
    return use_recovery_code(user, code)


class MfaError(ValueError):
    pass


def credentials(db: "Session", user: "User") -> list:
    from sqlalchemy import select

    from .models import WebauthnCredential

    return list(db.scalars(select(WebauthnCredential).where(WebauthnCredential.user_id == user.id).order_by(WebauthnCredential.created_at)))


def has_factor(db: "Session", user: "User") -> bool:
    return bool(user.totp_enabled) or bool(credentials(db, user))


def methods(db: "Session", user: "User") -> dict[str, bool]:
    return {"totp": bool(user.totp_enabled), "webauthn": bool(credentials(db, user)),
            "recovery_code": bool(user.recovery_codes)}


def _first_factor_codes(db: "Session", user: "User") -> list[str]:
    """Recovery codes are created with the first factor (and kept when another one is added)."""
    if has_factor(db, user) and user.recovery_codes:
        return []
    codes, digests = new_recovery_codes()
    user.recovery_codes = digests
    return codes


def start_enrolment(user: "User") -> str:
    """New secret, not active until a code confirms it."""
    secret = new_secret()
    user.totp_secret = encrypt(secret)
    user.totp_enabled = False
    user.totp_last_step = None
    return secret


def confirm_enrolment(db: "Session", user: "User", code: str) -> list[str] | None:
    """Enable TOTP when `code` matches the pending secret; returns the new recovery codes (empty when the
    account already had a second factor), None for a wrong code."""
    from .models import now

    secret = secret_of(user)
    step = match(secret, code, None) if secret else None
    if step is None:
        return None
    codes = _first_factor_codes(db, user)
    user.totp_enabled, user.totp_enabled_at, user.totp_last_step = True, now(), step
    return codes


def disable_totp(db: "Session", user: "User") -> None:
    user.totp_secret, user.totp_enabled, user.totp_enabled_at, user.totp_last_step = None, False, None, None
    if not credentials(db, user):
        user.recovery_codes = None


def reset(db: "Session", user: "User") -> None:
    """Every second factor of the account (lost phone or key): TOTP, security keys, recovery codes."""
    for c in credentials(db, user):
        db.delete(c)
    user.totp_secret, user.totp_enabled, user.totp_enabled_at, user.totp_last_step, user.recovery_codes = None, False, None, None, None


def status(db: "Session", user: "User", base_url: str | None = None) -> dict[str, Any]:
    keys = credentials(db, user)
    enabled = bool(user.totp_enabled) or bool(keys)
    return {
        "available": available(db) and applies_to(user),
        "enabled": enabled,
        "required": required(db, user),
        "totp": bool(user.totp_enabled),
        "enabled_at": user.totp_enabled_at,
        "webauthn": [{"id": k.id, "name": k.name, "created_at": k.created_at, "last_used_at": k.last_used_at} for k in keys],
        "webauthn_available": webauthn_available(base_url) if base_url else None,
        "recovery_codes_left": len(user.recovery_codes or []) if enabled else 0,
    }


# --------------------------------------------------------------------------- WebAuthn (security keys, passkeys)

WEBAUTHN_TTL = 300


def relying_party(base_url: str) -> tuple[str, str]:
    """(RP ID, origin) from the public URL of the application."""
    from urllib.parse import urlsplit

    parts = urlsplit(base_url)
    origin = f"{parts.scheme}://{parts.netloc}"
    return parts.hostname or "", origin


def webauthn_available(base_url: str) -> bool:
    """Browsers only allow WebAuthn in a secure context: https, or localhost."""
    rp_id, origin = relying_party(base_url)
    return origin.startswith("https://") or rp_id in ("localhost", "127.0.0.1", "::1")


def _state(user_id: int, purpose: str, challenge_bytes: bytes) -> str:
    from webauthn.helpers import bytes_to_base64url

    return encrypt(json.dumps({"u": user_id, "p": purpose, "c": bytes_to_base64url(challenge_bytes), "exp": time.time() + WEBAUTHN_TTL}))


def _read_state(state: str, user_id: int, purpose: str) -> bytes:
    from webauthn.helpers import base64url_to_bytes

    if not isinstance(state, str) or not state.startswith(PREFIX):
        raise MfaError("invalid security key request: start again")
    try:
        data = json.loads(decrypt(state))
    except Exception as e:  # noqa: BLE001
        raise MfaError("invalid security key request: start again") from e
    if data.get("u") != user_id or data.get("p") != purpose or data.get("exp", 0) < time.time():
        raise MfaError("security key request expired: start again")
    return base64url_to_bytes(data["c"])


def registration_options(db: "Session", user: "User", base_url: str, issuer: str) -> dict[str, Any]:
    from webauthn import generate_registration_options, options_to_json
    from webauthn.helpers import base64url_to_bytes
    from webauthn.helpers.structs import (
        AuthenticatorSelectionCriteria,
        PublicKeyCredentialDescriptor,
        ResidentKeyRequirement,
        UserVerificationRequirement,
    )

    rp_id, _ = relying_party(base_url)
    opts = generate_registration_options(
        rp_id=rp_id, rp_name=issuer, user_id=str(user.id).encode(), user_name=user.username,
        user_display_name=user.display_name or user.username,
        exclude_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(c.credential_id)) for c in credentials(db, user)],
        authenticator_selection=AuthenticatorSelectionCriteria(resident_key=ResidentKeyRequirement.PREFERRED,
                                                               user_verification=UserVerificationRequirement.PREFERRED),
    )
    return {"options": json.loads(options_to_json(opts)), "state": _state(user.id, "webauthn-register", opts.challenge)}


def register(db: "Session", user: "User", base_url: str, state: str, credential: dict[str, Any], name: str) -> list[str]:
    """Save a new security key; returns the new recovery codes (empty when the account already had a factor)."""
    from webauthn import verify_registration_response
    from webauthn.helpers import bytes_to_base64url

    from .models import WebauthnCredential

    rp_id, origin = relying_party(base_url)
    challenge_bytes = _read_state(state, user.id, "webauthn-register")
    try:
        v = verify_registration_response(credential=credential, expected_challenge=challenge_bytes, expected_rp_id=rp_id, expected_origin=origin)
    except Exception as e:  # noqa: BLE001
        raise MfaError(f"security key not accepted: {e}") from e
    codes = _first_factor_codes(db, user)
    transports = (credential.get("response") or {}).get("transports") if isinstance(credential, dict) else None
    db.add(WebauthnCredential(user_id=user.id, credential_id=bytes_to_base64url(v.credential_id),
                              public_key=bytes_to_base64url(v.credential_public_key), sign_count=v.sign_count,
                              transports=transports or None, name=(name or "Security key").strip()[:128] or "Security key"))
    return codes


def authentication_options(db: "Session", user: "User", base_url: str) -> dict[str, Any]:
    from webauthn import generate_authentication_options, options_to_json
    from webauthn.helpers import base64url_to_bytes
    from webauthn.helpers.structs import PublicKeyCredentialDescriptor, UserVerificationRequirement

    rp_id, _ = relying_party(base_url)
    keys = credentials(db, user)
    if not keys:
        raise MfaError("no security key registered for this account")
    opts = generate_authentication_options(
        rp_id=rp_id, user_verification=UserVerificationRequirement.PREFERRED,
        allow_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(c.credential_id)) for c in keys],
    )
    return {"options": json.loads(options_to_json(opts)), "state": _state(user.id, "webauthn-auth", opts.challenge)}


def authenticate(db: "Session", user: "User", base_url: str, state: str, credential: dict[str, Any]) -> None:
    """Check an assertion of one of the keys of the user (raises MfaError)."""
    from webauthn import verify_authentication_response
    from webauthn.helpers import base64url_to_bytes

    from .models import now

    rp_id, origin = relying_party(base_url)
    challenge_bytes = _read_state(state, user.id, "webauthn-auth")
    key = next((c for c in credentials(db, user) if c.credential_id == (credential or {}).get("id")), None)
    if key is None:
        raise MfaError("unknown security key for this account")
    try:
        v = verify_authentication_response(credential=credential, expected_challenge=challenge_bytes, expected_rp_id=rp_id,
                                           expected_origin=origin, credential_public_key=base64url_to_bytes(key.public_key),
                                           credential_current_sign_count=key.sign_count)
    except Exception as e:  # noqa: BLE001  (wrong signature, cloned key: counter going back...)
        raise MfaError(f"security key not accepted: {e}") from e
    key.sign_count, key.last_used_at = v.new_sign_count, now()


def delete_credential(db: "Session", user: "User", credential_id: int) -> None:
    key = next((c for c in credentials(db, user) if c.id == credential_id), None)
    if key is None:
        raise MfaError("unknown security key")
    db.delete(key)
    db.flush()
    if not has_factor(db, user):
        user.recovery_codes = None


# --------------------------------------------------------------------------- sign-in challenge

def challenge(user_id: int, purpose: str) -> str:
    """Short-lived, encrypted proof that the password was right: exchanged for a session with a code."""
    return encrypt(json.dumps({"u": user_id, "p": purpose, "exp": time.time() + CHALLENGE_TTL, "n": secrets.token_hex(8)}))


def read_challenge(token: str) -> dict[str, Any] | None:
    if not isinstance(token, str) or not token.startswith(PREFIX):  # decrypt() passes clear text through: never trust it
        return None
    try:
        data = json.loads(decrypt(token))
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(data, dict) or data.get("exp", 0) < time.time():
        return None
    return data
