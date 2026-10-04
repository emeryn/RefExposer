"""Settings edited from the administration UI and stored in the database.

Secret fields are encrypted at rest and never sent back to the browser: the API exposes
`<field>_set: true` instead, and an empty value on update keeps the stored secret.
"""

from __future__ import annotations

import logging
import re
import threading
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from .crypto import decrypt, encrypt
from .models import AppSetting

log = logging.getLogger(__name__)


class ProxySettings(BaseModel):
    """Outgoing HTTP(S) access used for downloads and OpenID Connect calls."""

    http_proxy: str = ""
    https_proxy: str = ""
    no_proxy: str = "localhost,127.0.0.1"
    username: str = ""
    password: str = ""
    verify_tls: bool = True
    ca_bundle: str = Field("", description="Additional certificate authorities (PEM), e.g. a company TLS proxy")

    @field_validator("http_proxy", "https_proxy")
    @classmethod
    def _url(cls, v: str) -> str:
        v = v.strip()
        if v and not re.match(r"^(https?|socks5h?)://[^\s]+$", v):
            raise ValueError("proxy URL expected (http://host:port)")
        return v

    @field_validator("ca_bundle")
    @classmethod
    def _pem(cls, v: str) -> str:
        v = v.strip()
        if v and "-----BEGIN CERTIFICATE-----" not in v:
            raise ValueError("PEM certificate(s) expected")
        return v

    @property
    def configured(self) -> bool:
        return bool(self.http_proxy or self.https_proxy)


class LdapSettings(BaseModel):
    enabled: bool = False
    label: str = "Company directory"
    server_url: str = ""
    start_tls: bool = False
    verify_tls: bool = True
    timeout: int = Field(10, ge=1, le=120)
    bind_dn: str = ""
    bind_password: str = ""
    user_base_dn: str = ""
    user_filter: str = "(&(objectClass=person)(uid={username}))"
    username_attr: str = "uid"
    display_name_attr: str = "cn"
    email_attr: str = "mail"
    group_mode: Literal["memberof", "search", "none"] = "memberof"
    group_base_dn: str = ""
    group_filter: str = "(&(objectClass=groupOfNames)(member={user_dn}))"
    group_name_attr: str = "cn"
    group_regex: str = ""
    admin_group: str = ""

    @field_validator("server_url")
    @classmethod
    def _server(cls, v: str) -> str:
        v = v.strip()
        if v and not re.match(r"^ldaps?://[^\s]+$", v):
            raise ValueError("URL expected: ldap://host:389 or ldaps://host:636")
        return v

    @field_validator("group_regex")
    @classmethod
    def _regex(cls, v: str) -> str:
        if v:
            re.compile(v)
        return v

    @field_validator("user_filter")
    @classmethod
    def _filter(cls, v: str) -> str:
        if "{username}" not in v:
            raise ValueError("the filter must contain {username}")
        return v


class OidcSettings(BaseModel):
    enabled: bool = False
    label: str = "Keycloak"
    issuer: str = ""
    discovery_url: str = Field("", description="Internal URL of the discovery document, when it differs from the public issuer")
    client_id: str = ""
    client_secret: str = ""
    scopes: str = "openid profile email"
    username_claim: str = "preferred_username"
    name_claim: str = "name"
    email_claim: str = "email"
    groups_claim: str = "groups"
    strip_group_path: bool = True
    group_regex: str = ""
    admin_group: str = ""
    verify_tls: bool = True

    @field_validator("issuer", "discovery_url")
    @classmethod
    def _http(cls, v: str) -> str:
        v = v.strip().rstrip("/")
        if v and not re.match(r"^https?://[^\s]+$", v):
            raise ValueError("http(s) URL expected")
        return v

    @field_validator("group_regex")
    @classmethod
    def _regex(cls, v: str) -> str:
        if v:
            re.compile(v)
        return v


class BrandingSettings(BaseModel):
    """Name shown in the header, on the sign-in page and in the browser tab (the logo is stored apart)."""

    title: str = Field("RefExposer", max_length=60)
    subtitle: str = Field("Unified referentials", max_length=120)

    @field_validator("title", "subtitle")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()

    @field_validator("title")
    @classmethod
    def _required(cls, v: str) -> str:
        if not v:
            raise ValueError("the title cannot be empty")
        return v


SYSLOG_FACILITIES = ("kern", "user", "mail", "daemon", "auth", "syslog", "lpr", "news", "uucp", "cron", "authpriv", "ftp", "ntp",
                     "security", "console", "solaris-cron", *(f"local{i}" for i in range(8)))


class SyslogSettings(BaseModel):
    """Forwarding of the logs and of the audit trail to a syslog collector (RFC 5424)."""

    enabled: bool = False
    host: str = ""
    port: int = Field(514, ge=1, le=65535)
    protocol: Literal["udp", "tcp", "tls"] = "udp"
    # TCP framing (RFC 6587): octet counting, or a line feed after each message (legacy collectors). TLS always counts.
    framing: Literal["octet-counting", "non-transparent"] = "octet-counting"
    facility: str = "local0"
    level: Literal["trace", "debug", "info", "warning", "error", "critical"] = "info"
    send_logs: bool = True
    send_audit: bool = True
    app_name: str = Field("refexposer", max_length=48)
    hostname: str = Field("", max_length=255, description="HOSTNAME field (empty: name of the container)")
    # Private Enterprise Number of the SD-IDs (audit@PEN, log@PEN); 32473 is the number reserved for documentation (RFC 5612)
    enterprise_id: str = "32473"
    timeout: int = Field(10, ge=1, le=120)
    verify_tls: bool = True
    use_company_ca: bool = Field(True, description="Also trust the certificate authorities of the network settings")
    ca_bundle: str = Field("", description="Certificate authority of the collector (PEM)")
    client_cert: str = Field("", description="Client certificate (PEM), for collectors requiring mutual TLS")
    client_key: str = ""

    @field_validator("host")
    @classmethod
    def _host(cls, v: str) -> str:
        v = v.strip()
        if v and not re.match(r"^[A-Za-z0-9._:-]+$", v.strip("[]")):
            raise ValueError("host name or IP address expected")
        return v.strip("[]")

    @field_validator("facility")
    @classmethod
    def _facility(cls, v: str) -> str:
        if v not in SYSLOG_FACILITIES:
            raise ValueError(f"unknown facility (expected: {', '.join(SYSLOG_FACILITIES)})")
        return v

    @field_validator("enterprise_id")
    @classmethod
    def _pen(cls, v: str) -> str:
        v = v.strip()
        if not re.match(r"^[0-9]+(\.[0-9]+)*$", v):
            raise ValueError("Private Enterprise Number expected (digits, e.g. 32473)")
        return v

    @field_validator("ca_bundle", "client_cert")
    @classmethod
    def _pem(cls, v: str) -> str:
        v = v.strip()
        if v and "-----BEGIN CERTIFICATE-----" not in v:
            raise ValueError("PEM certificate(s) expected")
        return v

    @field_validator("client_key")
    @classmethod
    def _key(cls, v: str) -> str:
        v = v.strip()
        if v and "PRIVATE KEY-----" not in v:
            raise ValueError("PEM private key expected (unencrypted)")
        return v


class MfaSettings(BaseModel):
    """Second factor (TOTP) policy for local and LDAP accounts."""

    mode: Literal["optional", "all", "groups"] = "optional"
    groups: list[str] = Field(default_factory=list, description="Groups whose members must use a second factor (mode 'groups')")

    @field_validator("groups")
    @classmethod
    def _groups(cls, v: list[str]) -> list[str]:
        return sorted({g.strip() for g in v if g.strip()})


class TasksSettings(BaseModel):
    """Schedule, state and parameters of the system tasks changed by administrators ({task id: {...}})."""

    tasks: dict[str, dict[str, Any]] = Field(default_factory=dict)


M = TypeVar("M", bound=BaseModel)

SECTIONS: dict[str, type[BaseModel]] = {"proxy": ProxySettings, "ldap": LdapSettings, "oidc": OidcSettings, "branding": BrandingSettings,
                                         "syslog": SyslogSettings, "tasks": TasksSettings,
                                         "mfa": MfaSettings}
SECRETS: dict[str, tuple[str, ...]] = {"proxy": ("password",), "ldap": ("bind_password",), "oidc": ("client_secret",), "branding": (),
                                       "syslog": ("client_key",), "tasks": (), "mfa": ()}

_cache: dict[str, BaseModel] = {}
_lock = threading.Lock()


def load(db: Session, key: str) -> BaseModel:
    model = SECTIONS[key]
    with _lock:
        if key in _cache:
            return _cache[key]
    row = db.get(AppSetting, key)
    data = dict(row.value) if row else {}
    for f in SECRETS[key]:
        if data.get(f):
            try:
                data[f] = decrypt(data[f])
            except Exception:  # noqa: BLE001
                log.error("Cannot decrypt %s.%s (encryption key changed?)", key, f)
                data[f] = ""
    try:
        value = model.model_validate(data)
    except Exception as e:  # noqa: BLE001
        log.error("Invalid %s settings, defaults used: %s", key, e)
        value = model()
    with _lock:
        _cache[key] = value
    return value


def merge_secrets(key: str, incoming: dict[str, Any], current: BaseModel) -> dict[str, Any]:
    """Empty or missing secrets keep the stored value; `<field>_clear: true` removes it."""
    data = dict(incoming)
    for f in SECRETS[key]:
        if data.pop(f"{f}_clear", False):
            data[f] = ""
        elif not data.get(f):
            data[f] = getattr(current, f)
    return data


def save(db: Session, key: str, value: BaseModel, username: str | None) -> None:
    stored = value.model_dump()
    for f in SECRETS[key]:
        if stored.get(f):
            stored[f] = encrypt(stored[f])
    row = db.get(AppSetting, key)
    if row:
        row.value, row.updated_by = stored, username
    else:
        db.add(AppSetting(key=key, value=stored, updated_by=username))
    db.commit()
    with _lock:
        _cache[key] = value


def public(key: str, value: BaseModel) -> dict[str, Any]:
    data = value.model_dump()
    for f in SECRETS[key]:
        data[f"{f}_set"] = bool(data.get(f))
        data[f] = ""
    return data


def clear_cache() -> None:
    with _lock:
        _cache.clear()
