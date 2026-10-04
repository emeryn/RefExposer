"""LDAP / Active Directory authentication with group retrieval."""

from __future__ import annotations

import logging
import re
import ssl
from dataclasses import dataclass, field
from typing import Any

from ldap3 import ANONYMOUS, NONE, SIMPLE, SUBTREE, Connection, Server, Tls
from ldap3.core.exceptions import LDAPBindError, LDAPException
from ldap3.utils.conv import escape_filter_chars
from ldap3.utils.dn import parse_dn

from .appsettings import LdapSettings

log = logging.getLogger(__name__)


class LdapError(Exception):
    """Configuration or connection problem (not a wrong password)."""


@dataclass
class ExternalIdentity:
    source: str
    external_id: str
    username: str
    display_name: str | None = None
    email: str | None = None
    groups: list[tuple[str, str]] = field(default_factory=list)  # (external id, name)
    raw: dict[str, Any] = field(default_factory=dict)

    def in_group(self, name: str) -> bool:
        name = name.strip().lower()
        return bool(name) and any(name in (gid.lower(), gname.lower()) for gid, gname in self.groups)


def _server(cfg: LdapSettings, ca_bundle: str = "") -> Server:
    tls = None
    if cfg.server_url.startswith("ldaps://") or cfg.start_tls:
        tls = Tls(
            validate=ssl.CERT_REQUIRED if cfg.verify_tls else ssl.CERT_NONE,
            ca_certs_data=ca_bundle or None,
        )
    return Server(cfg.server_url, use_ssl=cfg.server_url.startswith("ldaps://"), tls=tls, get_info=NONE, connect_timeout=cfg.timeout)


def connection_factory(cfg: LdapSettings, user: str | None, password: str | None, ca_bundle: str = "") -> Connection:
    """Open and bind a connection (replaced by a mock in tests). Raises LDAPBindError on refused credentials."""
    conn = Connection(
        _server(cfg, ca_bundle),
        user=user or None,
        password=password or None,
        authentication=SIMPLE if user else ANONYMOUS,
        receive_timeout=cfg.timeout,
        read_only=True,
    )
    if cfg.start_tls and not cfg.server_url.startswith("ldaps://"):
        conn.open()
        conn.start_tls()
    if not conn.bind():
        raise LDAPBindError((conn.result or {}).get("description") or "authentication refused")
    return conn


def _first(entry_attrs: dict[str, Any], name: str) -> str | None:
    v = entry_attrs.get(name)
    if isinstance(v, list):
        v = v[0] if v else None
    return str(v) if v not in (None, "") else None


def _rdn_value(dn: str) -> str:
    try:
        return parse_dn(dn)[0][1]
    except Exception:  # noqa: BLE001
        return dn


def _service_bind(cfg: LdapSettings, ca_bundle: str) -> Connection:
    try:
        return connection_factory(cfg, cfg.bind_dn, cfg.bind_password, ca_bundle)
    except LDAPBindError as e:
        raise LdapError(f"service account bind refused ({cfg.bind_dn or 'anonymous'}): {e}") from e
    except LDAPException as e:
        raise LdapError(f"directory unreachable ({cfg.server_url}): {e}") from e


def _find_user(conn: Connection, cfg: LdapSettings, username: str) -> tuple[str, dict[str, Any]] | None:
    flt = cfg.user_filter.replace("{username}", escape_filter_chars(username))
    attrs = [a for a in {cfg.username_attr, cfg.display_name_attr, cfg.email_attr, "memberOf"} if a]
    conn.search(cfg.user_base_dn, flt, search_scope=SUBTREE, attributes=attrs, size_limit=2)
    entries = [e for e in conn.response or [] if e.get("type") == "searchResEntry"]
    if len(entries) != 1:
        return None
    return entries[0]["dn"], dict(entries[0].get("attributes") or {})


def _groups(conn: Connection, cfg: LdapSettings, dn: str, attrs: dict[str, Any]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if cfg.group_mode == "memberof":
        values = attrs.get("memberOf") or []
        if isinstance(values, str):
            values = [values]
        out = [(str(g), _rdn_value(str(g))) for g in values]
    elif cfg.group_mode == "search":
        flt = cfg.group_filter.replace("{user_dn}", escape_filter_chars(dn))
        conn.search(cfg.group_base_dn or cfg.user_base_dn, flt, search_scope=SUBTREE, attributes=[cfg.group_name_attr])
        for e in conn.response or []:
            if e.get("type") != "searchResEntry":
                continue
            name = _first(dict(e.get("attributes") or {}), cfg.group_name_attr) or _rdn_value(e["dn"])
            out.append((e["dn"], name))
    if cfg.group_regex:
        rx = re.compile(cfg.group_regex)
        out = [g for g in out if rx.search(g[1])]
    return sorted(set(out), key=lambda g: g[1].lower())


def authenticate(cfg: LdapSettings, username: str, password: str, ca_bundle: str = "") -> ExternalIdentity | None:
    """Return the identity when the credentials are valid, None otherwise. Raises LdapError on misconfiguration."""
    if not cfg.enabled or not cfg.server_url:
        return None
    if not password:
        return None  # an empty password would be an anonymous bind
    conn = _service_bind(cfg, ca_bundle)
    try:
        found = _find_user(conn, cfg, username)
        if not found:
            return None
        dn, attrs = found
        try:
            user_conn = connection_factory(cfg, dn, password, ca_bundle)
            user_conn.unbind()
        except LDAPBindError:
            return None
        except LDAPException as e:
            raise LdapError(f"LDAP error: {e}") from e
        groups = _groups(conn, cfg, dn, attrs)
    finally:
        conn.unbind()
    return _identity(cfg, username, dn, attrs, groups)


def _identity(cfg: LdapSettings, username: str, dn: str, attrs: dict[str, Any], groups: list[tuple[str, str]]) -> ExternalIdentity:
    return ExternalIdentity(
        source="ldap",
        external_id=dn,
        username=(_first(attrs, cfg.username_attr) or username).lower(),
        display_name=_first(attrs, cfg.display_name_attr),
        email=_first(attrs, cfg.email_attr),
        groups=groups,
        raw={k: v for k, v in attrs.items() if k != "memberOf"},
    )


def lookup(cfg: LdapSettings, usernames: list[str], ca_bundle: str = "") -> dict[str, ExternalIdentity | None]:
    """Current directory entry of each user (service account bind, no password): periodic resynchronisation.
    None: the user is no longer in the directory (or no longer matches the user filter)."""
    conn = _service_bind(cfg, ca_bundle)
    out: dict[str, ExternalIdentity | None] = {}
    try:
        for username in usernames:
            found = _find_user(conn, cfg, username)
            out[username] = _identity(cfg, username, found[0], found[1], _groups(conn, cfg, found[0], found[1])) if found else None
    finally:
        conn.unbind()
    return out


def test(cfg: LdapSettings, username: str | None = None, password: str | None = None, ca_bundle: str = "") -> dict[str, Any]:
    """Diagnostic used by the settings page."""
    steps: list[dict[str, Any]] = []
    try:
        conn = _service_bind(cfg, ca_bundle)
        steps.append({"ok": True, "step": "Service account connection and bind"})
        try:
            conn.search(cfg.user_base_dn, cfg.user_filter.replace("{username}", "*"), search_scope=SUBTREE, attributes=[cfg.username_attr], size_limit=5)
            sample = [_first(dict(e.get("attributes") or {}), cfg.username_attr) for e in conn.response or [] if e.get("type") == "searchResEntry"]
            steps.append({"ok": bool(sample), "step": f"User search in {cfg.user_base_dn}", "detail": ", ".join(s for s in sample if s) or "no result"})
        finally:
            conn.unbind()
    except (LdapError, LDAPException) as e:
        steps.append({"ok": False, "step": "Directory connection", "detail": str(e)})
        return {"ok": False, "steps": steps}
    identity = None
    if username:
        try:
            ident = authenticate(cfg.model_copy(update={"enabled": True}), username, password or "", ca_bundle)
        except LdapError as e:
            steps.append({"ok": False, "step": f"Authentication of {username}", "detail": str(e)})
            return {"ok": False, "steps": steps}
        if ident:
            identity = {
                "dn": ident.external_id, "username": ident.username, "display_name": ident.display_name,
                "email": ident.email, "groups": [g[1] for g in ident.groups],
                "admin": ident.in_group(cfg.admin_group) if cfg.admin_group else False,
            }
            steps.append({"ok": True, "step": f"Authentication of {username}", "detail": f"{len(ident.groups)} group(s)"})
        else:
            steps.append({"ok": False, "step": f"Authentication of {username}", "detail": "user not found or incorrect password"})
    return {"ok": all(s["ok"] for s in steps), "steps": steps, "identity": identity}

