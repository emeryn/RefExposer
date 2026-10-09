"""Secret manager of the sources: API keys, tokens and passwords referenced as ${secret:<name>}.

- Values are encrypted at rest (AES-256-GCM, key derived from REFEX_SECRET_KEY, bound to the secret name) and never
  returned by the API: definitions only hold the reference, resolved at the moment a request is sent.
- References are only allowed where nothing is displayed: header values, `basic_auth` and the Git `token`.
- A secret may be restricted to some hosts (`hosts`, fnmatch patterns): it is never sent anywhere else, even by an
  administrator (or an agent) pointing a source at another server.
- Literal credentials still written in definitions are masked in the administration API; a masked value sent back
  unchanged keeps the stored one.
"""

from __future__ import annotations

import fnmatch
import re
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config as config_module
from .config import SECRET_NAME, secret_names
from .crypto import decrypt_data, encrypt_data
from .models import SourceSecret

NAME_RE = re.compile(r"^" + SECRET_NAME + r"$")
HOST_RE = re.compile(r"^[A-Za-z0-9*?.\-\[\]:]+$")
MASK = "********"
_SECRET_FIELDS = ("basic_auth", "token")


class SecretError(ValueError):
    pass


def _context(name: str) -> str:
    return f"source-secret:{name}"


def seal(name: str, value: str) -> str:
    return encrypt_data(value, _context(name))


def unseal(secret: SourceSecret) -> str:
    return decrypt_data(secret.value, _context(secret.name))


def check_hosts(hosts: list[str]) -> list[str]:
    out = []
    for h in hosts:
        h = h.strip().lower()
        if not h:
            continue
        if not HOST_RE.match(h):
            raise SecretError(f"invalid host pattern: '{h}' (e.g. api.example.com, *.example.com)")
        out.append(h)
    return out


def host_allowed(hosts: list[str] | None, url: str | None) -> bool:
    if not hosts:
        return True
    host = (urlparse(url).hostname or "").lower() if url else ""
    return bool(host) and any(fnmatch.fnmatchcase(host, p) for p in hosts)


def resolve(db: Session, name: str, url: str | None) -> str:
    secret = db.scalar(select(SourceSecret).where(SourceSecret.name == name))
    if secret is None:
        raise SecretError(f"unknown secret '{name}' (secret manager)")
    if not host_allowed(secret.hosts, url):
        host = urlparse(url).hostname if url else None
        raise SecretError(f"secret '{name}' is not allowed for {host or 'this destination'} "
                          f"(allowed hosts: {', '.join(secret.hosts or [])})")
    return unseal(secret)


def install() -> None:
    """Make ${secret:<name>} resolvable by the source configuration (downloads, Git, preview)."""
    from .db import session_factory

    def resolver(name: str, url: str | None) -> str:
        with session_factory()() as db:
            return resolve(db, name, url)

    config_module.SECRET_RESOLVER = resolver


def existing(db: Session) -> set[str]:
    return set(db.scalars(select(SourceSecret.name)))


def check_references(db: Session, config: dict[str, Any]) -> None:
    """Refuse a definition referencing a secret that does not exist."""
    missing = sorted(secret_names(config) - existing(db))
    if missing:
        raise SecretError(f"unknown secret(s): {', '.join(missing)} (create them in the secret manager first)")


def usage(refs: dict[str, Any]) -> dict[str, list[str]]:
    """Secret name -> ids of the referentials referencing it."""
    out: dict[str, list[str]] = {}
    for ref in refs.values():
        for name in secret_names(ref.source.model_dump()):
            out.setdefault(name, []).append(ref.id)
    return out


# --------------------------------------------------------------------------- literal credentials of definitions

def _sensitive(value: Any) -> bool:
    """A literal credential: a value without any reference (${secret:...} / ${REFEX_SOURCE_...})."""
    return isinstance(value, str) and bool(value) and "${" not in value


def mask_source(source: dict[str, Any] | None) -> dict[str, Any] | None:
    """Copy of a source where literal header values, Basic authentication and token are masked."""
    if not source:
        return source
    out = dict(source)
    if out.get("headers"):
        out["headers"] = {k: (MASK if _sensitive(v) else v) for k, v in out["headers"].items()}
    for f in _SECRET_FIELDS:
        if _sensitive(out.get(f)):
            out[f] = MASK
    return out


def mask_config(config: dict[str, Any]) -> dict[str, Any]:
    return {**config, "source": mask_source(config.get("source"))} if config.get("source") else config


def unmask_source(incoming: dict[str, Any] | None, stored: dict[str, Any] | None) -> dict[str, Any] | None:
    """Masked values sent back unchanged keep the stored value; a masked value with nothing stored is refused."""
    if not incoming:
        return incoming
    stored = stored or {}
    out = dict(incoming)
    if out.get("headers"):
        headers = {}
        for k, v in out["headers"].items():
            if v == MASK:
                if k not in (stored.get("headers") or {}):
                    raise SecretError(f"header '{k}': enter its value (or select a secret)")
                v = stored["headers"][k]
            headers[k] = v
        out["headers"] = headers
    for f in _SECRET_FIELDS:
        if out.get(f) == MASK:
            if not stored.get(f):
                raise SecretError(f"'{f}': enter its value (or select a secret)")
            out[f] = stored[f]
    return out
