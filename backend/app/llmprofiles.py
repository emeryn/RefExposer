"""Saved connectors to an inference server, and the one in use.

A profile holds everything that describes *where the model is and how it answers*: the server, the model, the
generation parameters, and whether the agent should adapt to a small model. The assistant settings keep what
is *policy* — whether it is on, who may use it, whether it may write, how long conversations are kept — so
switching from one model to another changes nothing about who is allowed to do what.

One profile is `active`. `appsettings.load(db, "assistant")` overlays it on the stored settings, so every
caller (the chat, the watchdogs, the connection test) goes on reading one object and nothing else had to
change. With no profile at all, the fields stored in the settings are used as they were before profiles
existed, which is also what an instance upgrading from an earlier version starts from.

**Secrets.** The API key and the values of the extra headers (where a gateway key routinely lives) are kept in
one AES-256-GCM blob bound to the profile, like the secret manager. They are never returned by the API: it
answers `api_key_set` and the header *names*. A write that leaves a secret empty keeps the stored one, and
`${MASK}` as a header value means the same — so the interface can show a profile without ever holding its
secrets.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .crypto import decrypt_data, encrypt_data
from .models import LlmProfile, now
from .secretstore import MASK

log = logging.getLogger(__name__)

# Fields a profile owns: they are overlaid on the assistant settings when one is active
CONNECTION_FIELDS = ("provider", "base_url", "model", "verify_tls", "timeout")
GENERATION_FIELDS = ("temperature", "top_p", "max_tokens", "context_tokens", "small_model")
DEFAULT_NAME = "Default"


def _context(profile_id: int) -> str:
    """The blob of a profile cannot be moved to another one: the identifier is part of the encryption."""
    return f"llm-profile:{profile_id}"


def secrets_of(profile: LlmProfile) -> dict[str, Any]:
    """{"api_key": str, "headers": {name: value}} of a profile, decrypted."""
    if not profile.secrets_enc:
        return {"api_key": "", "headers": {}}
    try:
        data = json.loads(decrypt_data(profile.secrets_enc, _context(profile.id)))
    except Exception:  # noqa: BLE001 - the instance key changed, or the row was moved
        log.error("Cannot decrypt the secrets of the LLM profile '%s' (encryption key changed?)", profile.name)
        return {"api_key": "", "headers": {}}
    return {"api_key": str(data.get("api_key") or ""),
            "headers": {str(k): str(v) for k, v in (data.get("headers") or {}).items()}}


def store_secrets(profile: LlmProfile, api_key: str | None, headers: dict[str, str] | None) -> None:
    """Write the secrets of a profile. `None` keeps what is stored; an empty API key keeps it too, and a
    header value equal to the mask keeps that header's value (the interface never receives them)."""
    current = secrets_of(profile)
    # None (not sent) and "" (sent empty) both mean "keep what is stored": the interface never holds the value
    key = api_key or current["api_key"]
    if headers is None:
        merged = current["headers"]
    else:
        merged = {name: (current["headers"].get(name, "") if value == MASK else value)
                  for name, value in headers.items()}
    profile.header_names = sorted(merged)
    profile.secrets_enc = encrypt_data(json.dumps({"api_key": key, "headers": merged}), _context(profile.id))


def clear_api_key(profile: LlmProfile) -> None:
    current = secrets_of(profile)
    profile.secrets_enc = encrypt_data(json.dumps({"api_key": "", "headers": current["headers"]}),
                                       _context(profile.id))


# --------------------------------------------------------------------------- the active profile

def active(db: Session) -> LlmProfile | None:
    return db.scalars(select(LlmProfile).where(LlmProfile.active.is_(True)).limit(1)).first()


def activate(db: Session, profile: LlmProfile) -> None:
    """Make this profile the one the assistant uses. Exactly one row is active afterwards."""
    for other in db.scalars(select(LlmProfile).where(LlmProfile.active.is_(True))):
        other.active = False
    profile.active = True
    db.flush()


def overlay(db: Session, settings: Any) -> Any:
    """The assistant settings with the active profile applied, or unchanged when there is none."""
    profile = active(db)
    if profile is None:
        return settings
    secrets = secrets_of(profile)
    update: dict[str, Any] = {f: getattr(profile, f) for f in CONNECTION_FIELDS + GENERATION_FIELDS}
    update.update({
        "api_key": secrets["api_key"],
        "headers": secrets["headers"],
        "stop": list(profile.stop or []),
        "options": dict(profile.options or {}),
        "profile_id": profile.id,
        "profile_name": profile.name,
    })
    return settings.model_copy(update=update)


# --------------------------------------------------------------------------- what the API returns

def to_dict(profile: LlmProfile, with_secrets: bool = False) -> dict[str, Any]:
    """A profile as the API returns it: the secrets are reported as present or absent, never given."""
    secrets = secrets_of(profile)
    out = {
        "id": profile.id,
        "name": profile.name,
        "description": profile.description,
        "active": profile.active,
        "provider": profile.provider,
        "base_url": profile.base_url,
        "model": profile.model,
        "verify_tls": profile.verify_tls,
        "timeout": profile.timeout,
        "temperature": profile.temperature,
        "top_p": profile.top_p,
        "max_tokens": profile.max_tokens,
        "context_tokens": profile.context_tokens,
        "stop": list(profile.stop or []),
        "options": dict(profile.options or {}),
        "small_model": profile.small_model,
        "api_key_set": bool(secrets["api_key"]),
        # Names only: a gateway key often lives in a header value, which never leaves the database
        "header_names": list(profile.header_names or []),
        "configured": bool(profile.base_url and profile.model),
        "last_test_at": profile.last_test_at,
        "last_test_ok": profile.last_test_ok,
        "last_test_error": profile.last_test_error,
        "created_at": profile.created_at,
        "updated_at": profile.updated_at,
        "created_by": profile.created_by,
        "updated_by": profile.updated_by,
    }
    if with_secrets:
        out["headers"] = secrets["headers"]
    return out


def as_settings(db: Session, profile: LlmProfile, policy: Any) -> Any:
    """`policy` (the assistant settings) with **this** profile applied, whether it is the active one or not:
    used to test a profile before switching to it."""
    secrets = secrets_of(profile)
    update: dict[str, Any] = {f: getattr(profile, f) for f in CONNECTION_FIELDS + GENERATION_FIELDS}
    update.update({"api_key": secrets["api_key"], "headers": secrets["headers"],
                   "stop": list(profile.stop or []), "options": dict(profile.options or {})})
    return policy.model_copy(update=update)


# --------------------------------------------------------------------------- first start

def bootstrap(db: Session) -> LlmProfile | None:
    """Turn the connection stored in the assistant settings into a profile, once.

    An instance that was configured before profiles existed keeps working either way (the settings are the
    fallback), but it should see its connection in the list rather than nothing at all. Runs at startup, where
    the encryption key is available — a migration is the wrong place to re-encrypt a secret.
    """
    from . import appsettings

    if db.scalars(select(LlmProfile).limit(1)).first() is not None:
        return None
    stored = appsettings.stored_assistant(db)
    if not stored.base_url:
        return None
    profile = LlmProfile(
        name=DEFAULT_NAME,
        description="Connection configured before the profiles existed.",
        active=True,
        provider=stored.provider,
        base_url=stored.base_url,
        model=stored.model,
        verify_tls=stored.verify_tls,
        timeout=stored.timeout,
        temperature=stored.temperature,
        top_p=stored.top_p,
        max_tokens=stored.max_tokens,
        context_tokens=stored.context_tokens,
        stop=list(stored.stop or []),
        options=dict(stored.options or {}),
        created_by="system",
        updated_by="system",
    )
    db.add(profile)
    db.flush()
    store_secrets(profile, stored.api_key, dict(stored.headers or {}))
    profile.updated_at = now()
    db.commit()
    appsettings.clear_cache()
    log.info("LLM profile '%s' created from the assistant settings", profile.name)
    return profile
