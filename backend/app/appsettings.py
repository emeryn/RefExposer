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
from .federation import FederationSettings
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


class McpSettings(BaseModel):
    """Administration MCP server (/api/mcp): an AI agent drives RefExposer with the API token of a service account."""

    enabled: bool = False
    # Service accounts allowed: through the MCP server only, they act as administrators
    account_ids: list[int] = Field(default_factory=list)
    # Only the tools that read (no creation, change, deletion, update)
    read_only: bool = False
    # Data server (/api/mcp/user): every account queries, with its own API token, the referentials it may read
    users_enabled: bool = False
    # Groups allowed to use it (names); every account when empty
    user_groups: list[str] = Field(default_factory=list)


# Keys that make up the request itself: an option of the same name would change what is asked, not how
RESERVED_LLM_KEYS = frozenset({
    "model", "messages", "stream", "stream_options", "tools", "tool_choice", "prompt", "options", "format",
    "suffix", "images", "n",
})


def check_llm_options(v: dict[str, Any]) -> dict[str, Any]:
    """Free-form parameters sent to the inference server as they are. Shared by the settings and by a saved
    profile, so both refuse the same things."""
    if len(v) > 40:
        raise ValueError("at most 40 options")
    out: dict[str, Any] = {}
    for name, value in v.items():
        name = str(name).strip()
        if not name:
            continue
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,48}", name):
            raise ValueError(f"invalid option name: {name!r}")
        if name in RESERVED_LLM_KEYS:
            raise ValueError(f"'{name}' is part of the request itself and cannot be set as an option")
        if isinstance(value, bool) or isinstance(value, (int, float)):
            out[name] = value
        elif isinstance(value, str):
            if len(value) > 500:
                raise ValueError(f"the value of '{name}' is too long")
            out[name] = value
        elif isinstance(value, list) and len(value) <= 32 and all(isinstance(x, (str, int, float)) for x in value):
            out[name] = value
        else:
            raise ValueError(f"the value of '{name}' must be a number, a string, a boolean or a list")
    return out


def check_llm_headers(v: dict[str, str]) -> dict[str, str]:
    """Extra headers sent with every call to the inference server."""
    if len(v) > 20:
        raise ValueError("at most 20 headers")
    out = {}
    for name, value in v.items():
        name, value = str(name).strip(), str(value).strip()
        if not name:
            continue
        if not re.fullmatch(r"[A-Za-z0-9!#$%&'*+.^_`|~-]{1,64}", name):
            raise ValueError(f"invalid header name: {name!r}")
        # A newline in a value would let a second header be injected into the request
        if len(value) > 1024 or re.search(r"[\r\n]", value):
            raise ValueError(f"invalid value for the header {name}")
        if name.lower() in ("host", "content-length", "transfer-encoding", "connection"):
            raise ValueError(f"the header {name} is set by the client itself")
        out[name] = value
    return out


class AssistantSettings(BaseModel):
    """In-application assistant: a chatbot answering from the referentials, optionally acting as an agent.

    The model runs on a server of your own (Ollama, or anything speaking the OpenAI API: vLLM, llama.cpp,
    LocalAI, TGI). RefExposer is its only client: the browser never calls it, and the connection uses the
    proxy and the certificate authorities configured in *Network & proxy*.

    **The connection and the generation fields belong to the active profile** (llmprofiles.py) as soon as one
    exists: `load()` overlays it, so everything reading these settings sees the model in use without knowing
    about profiles. What is stored here for those fields is the fallback — what an instance configured before
    profiles existed keeps using, and what a fresh instance has until a profile is saved. The rest of this
    section is policy, which does not change when you switch model: who may use the assistant, whether it may
    write, whether confidential referentials are in reach, how long conversations are kept.
    """

    enabled: bool = False
    # ollama: /api/tags, /api/chat · openai: /v1/models, /v1/chat/completions
    provider: Literal["ollama", "openai"] = "ollama"
    base_url: str = Field("", description="Address of the inference server, e.g. http://ollama.internal:11434")
    # Optional: most local servers have no authentication. Sent as `Authorization: Bearer <key>`.
    api_key: str = ""
    # Extra headers sent with every call, for a gateway that wants its own (tenant, routing, API key under
    # another name). They never carry a value RefExposer generates: what is written here is what is sent.
    headers: dict[str, str] = Field(default_factory=dict)
    model: str = ""
    verify_tls: bool = True
    timeout: int = Field(120, ge=5, le=900)

    # Generation. The effective values are shown in the interface: some servers ignore what they do not
    # support, and the names differ between the two APIs.
    temperature: float = Field(0.2, ge=0, le=2)
    top_p: float = Field(0.95, ge=0, le=1)
    # Tokens the answer may use (Ollama: num_predict · OpenAI: max_tokens)
    max_tokens: int = Field(2048, ge=64, le=32768)
    # Context window the model is loaded with (Ollama: num_ctx). 0: whatever the server decides, which is
    # often small; raise it when the agent reads large tool results.
    context_tokens: int = Field(0, ge=0, le=1_048_576)
    # Sequences that stop the generation
    stop: list[str] = Field(default_factory=list)
    # Anything else the inference server accepts, sent as it is: for Ollama inside `options`
    # (repeat_penalty, top_k, min_p, mirostat, seed, num_thread...), for an OpenAI-compatible server at the
    # top level of the request (repetition_penalty, top_k, frequency_penalty, seed...). Set last, so a key
    # given here wins over the fields above.
    options: dict[str, Any] = Field(default_factory=dict)
    # Messages of the conversation kept in the prompt (the oldest ones are dropped beyond that)
    history_messages: int = Field(20, ge=2, le=200)

    # Small model (1B, 3B): the agent adapts to it — fewer tools, a shorter prompt, one tool call at a
    # time, smaller results. A profile carries this, because it is a property of the model.
    small_model: bool = False

    # Set by the overlay: which saved profile these values come from (read only, never stored)
    profile_id: int | None = None
    profile_name: str = ""

    # Agent mode: the model calls the REST API through the session of the user who is chatting, so it never
    # does anything that user could not do in the interface.
    agent_enabled: bool = False
    max_steps: int = Field(8, ge=1, le=30)
    # Tools that change something (create a referential, grant a right...). They still obey the rights of
    # the user: a reader gets the same 403 as in the interface.
    allow_writes: bool = False
    # Scheduled watchdogs (watchdog.py): the agent investigating on its own, on a crontab or after a
    # synchronization. Off by default, and reserved to administrators and advanced users even when on — an
    # ordinary account never sees the menu. Needs the agent mode.
    watchdogs_enabled: bool = False
    # Confidential referentials queryable by the assistant. Off: nothing of theirs ever reaches the model,
    # neither their rows nor their names (enforced in the authorization layer, not in the prompt).
    allow_confidential: bool = False
    # Who may use the assistant: these groups (names) and these accounts (ids). Everyone when both are
    # empty; administrators always. Service accounts never: they have no interface session.
    groups: list[str] = Field(default_factory=list)
    user_ids: list[int] = Field(default_factory=list)
    # Empty: the default prompt shipped with the application (llm/prompts/assistant.md)
    system_prompt: str = ""
    # Conversations older than this are purged by the system task; 0 keeps them
    retention_days: int = Field(30, ge=0, le=3650)
    # Generation requests allowed per user and per minute
    rate_per_minute: int = Field(20, ge=1, le=600)

    @field_validator("base_url")
    @classmethod
    def _url(cls, v: str) -> str:
        v = v.strip().rstrip("/")
        if v and not re.match(r"^https?://[^\s/]+(/[^\s]*)?$", v):
            raise ValueError("address of the inference server expected (http://host:port)")
        return v

    @field_validator("stop")
    @classmethod
    def _stop(cls, v: list[str]) -> list[str]:
        out = [s for s in (str(x) for x in v) if s]
        if len(out) > 8:
            raise ValueError("at most 8 stop sequences")
        if any(len(s) > 200 for s in out):
            raise ValueError("a stop sequence is at most 200 characters")
        return out

    @field_validator("options")
    @classmethod
    def _options(cls, v: dict[str, Any]) -> dict[str, Any]:
        return check_llm_options(v)

    @field_validator("headers")
    @classmethod
    def _headers(cls, v: dict[str, str]) -> dict[str, str]:
        return check_llm_headers(v)

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.model)


# Written so that each part can match in exactly one way: with the dot excluded from the character
# classes, the engine has nothing to backtrack over (the previous form was quadratic on "a@b.b.b.b…")
EMAIL_RE = re.compile(r"^[^@\s<>,;]{1,64}@[^@\s<>,;.]{1,63}(\.[^@\s<>,;.]{1,63}){1,8}$")


class SmtpSettings(BaseModel):
    """Outgoing e-mail server, used by the notification channels of type e-mail."""

    host: str = ""
    port: int = Field(587, ge=1, le=65535)
    # starttls: plain connection upgraded (587) · tls: TLS from the start (465) · none: no encryption (relay of the LAN)
    security: Literal["starttls", "tls", "none"] = "starttls"
    username: str = ""
    password: str = ""
    from_address: str = ""
    verify_tls: bool = True
    timeout: int = Field(20, ge=1, le=120)

    @field_validator("host")
    @classmethod
    def _host(cls, v: str) -> str:
        v = v.strip()
        if v and not re.match(r"^[A-Za-z0-9._:-]+$", v.strip("[]")):
            raise ValueError("host name or IP address expected")
        return v.strip("[]")

    @field_validator("from_address")
    @classmethod
    def _from(cls, v: str) -> str:
        v = v.strip()
        address = v.rsplit("<", 1)[-1].rstrip(">").strip() if "<" in v else v
        if v and not EMAIL_RE.match(address):
            raise ValueError("e-mail address expected (alerts@example.com or RefExposer <alerts@example.com>)")
        return v

    @property
    def configured(self) -> bool:
        return bool(self.host and self.from_address)


NotificationEvent = Literal["failure", "recovered", "published", "quality"]


class NotificationChannel(BaseModel):
    """Where to send the events of the referentials: e-mail recipients or a generic webhook."""

    id: str = ""
    name: str = Field(..., min_length=1, max_length=80)
    type: Literal["email", "webhook"]
    enabled: bool = True
    # failure: update in error, corrupted or rejected source · recovered: first success after a failure · published: new version
    # quality: a new version published with failed quality rules (warning), the previous one passing them
    events: list[NotificationEvent] = Field(default_factory=lambda: ["failure", "recovered"])
    # A referential failing again and again: notified once (when it starts failing), unless repeat_failures
    repeat_failures: bool = False
    # Referentials concerned (ids), and / or their categories; every referential when both are empty
    referentials: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    # e-mail
    recipients: list[str] = Field(default_factory=list)
    notify_owner: bool = Field(False, description="Also the owner of the referential, when it is an e-mail address")
    # webhook
    url: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    # ${secret:<name>}: HMAC-SHA256 of the body, sent in X-RefExposer-Signature (sha256=<hex>)
    signing_secret: str = ""
    # JSON body with {{placeholders}} (Teams, Discord, PagerDuty...); the generic JSON payload when empty
    template: str = ""

    @field_validator("recipients")
    @classmethod
    def _recipients(cls, v: list[str]) -> list[str]:
        out = []
        for r in v:
            r = r.strip()
            if r and not EMAIL_RE.match(r):
                raise ValueError(f"invalid e-mail address: {r}")
            if r and r not in out:
                out.append(r)
        return out

    @field_validator("url")
    @classmethod
    def _url(cls, v: str) -> str:
        v = v.strip()
        if v and not re.match(r"^https?://\S+$", v):
            raise ValueError("URL expected (https://...)")
        return v

    @field_validator("signing_secret")
    @classmethod
    def _signing(cls, v: str) -> str:
        v = v.strip()
        if v and not re.fullmatch(r"\$\{secret:[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\}", v):
            raise ValueError("signing secret: a ${secret:<name>} reference of the secret manager")
        return v

    @field_validator("events")
    @classmethod
    def _events(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("choose at least one event")
        return list(dict.fromkeys(v))

    def check(self) -> "NotificationChannel":
        if self.type == "email" and not (self.recipients or self.notify_owner):
            raise ValueError("an e-mail channel needs recipients (or the owner of the referential)")
        if self.type == "webhook" and not self.url:
            raise ValueError("a webhook needs a URL")
        return self


class NotificationsSettings(BaseModel):
    channels: list[NotificationChannel] = Field(default_factory=list)


class AlertsSettings(BaseModel):
    """What the *Alerts* page reports (alerts.py).

    The feed gathers several kinds of event, and not every instance cares about all of them: an instance whose
    sources publish irregularly reports a drift on every update, and the noise hides the rest. An administrator
    hides the kinds that do not apply here, for everyone — the bell counts exactly what the page shows.

    Only the display is concerned: nothing stops being watched, and e-mails and webhooks keep their own
    per-channel configuration. Kinds are named rather than listed, so a kind added in a later version is shown
    until an administrator hides it.
    """

    hidden_kinds: list[str] = Field(default_factory=list,
                                    description="Alert kinds kept out of the page: watchdog, failure, quality, "
                                                "stale, drift")
    # Nothing below this is reported at all. A watchdog finding keeps the severity the model gave it; the other
    # kinds have a fixed one (a failed update is HIGH, a stale referential MEDIUM, a volume that grew LOW...).
    min_severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] = "LOW"
    # What counts as a volume drift: a version that changes the number of rows by at least this much, on a
    # referential that had at least `drift_min_rows` rows before (below that the ratio means nothing).
    drift_ratio: float = Field(0.2, ge=0.01, le=10)
    drift_min_rows: int = Field(100, ge=1, le=1_000_000_000)

    @field_validator("hidden_kinds")
    @classmethod
    def _kinds(cls, v: list[str]) -> list[str]:
        from .alerts import KINDS  # here: alerts.py reads these settings, and owns the list of kinds

        unknown = sorted(set(v) - set(KINDS))
        if unknown:
            raise ValueError(f"unknown alert kind(s): {', '.join(unknown)} (available: {', '.join(KINDS)})")
        return list(dict.fromkeys(v))

    def shows(self, kind: str) -> bool:
        return kind not in self.hidden_kinds


M = TypeVar("M", bound=BaseModel)

SECTIONS: dict[str, type[BaseModel]] = {"proxy": ProxySettings, "ldap": LdapSettings, "oidc": OidcSettings, "branding": BrandingSettings,
                                         "syslog": SyslogSettings, "tasks": TasksSettings,
                                         "mfa": MfaSettings, "mcp": McpSettings, "smtp": SmtpSettings,
                                         "notifications": NotificationsSettings, "federation": FederationSettings,
                                         "assistant": AssistantSettings, "alerts": AlertsSettings}
SECRETS: dict[str, tuple[str, ...]] = {"proxy": ("password",), "ldap": ("bind_password",), "oidc": ("client_secret",), "branding": (),
                                       "syslog": ("client_key",), "tasks": (), "mfa": (), "mcp": (), "smtp": ("password",),
                                       "notifications": (), "federation": (), "assistant": ("api_key",),
                                       "alerts": ()}

_cache: dict[str, BaseModel] = {}
_lock = threading.Lock()


def _read(db: Session, key: str) -> BaseModel:
    model = SECTIONS[key]
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
        return model.model_validate(data)
    except Exception as e:  # noqa: BLE001
        log.error("Invalid %s settings, defaults used: %s", key, e)
        return model()


def stored_assistant(db: Session) -> BaseModel:
    """The assistant settings as they are stored, **without** the active profile applied: the fallback
    connection. Only llmprofiles.bootstrap needs this; everything else wants load()."""
    return _read(db, "assistant")


def load(db: Session, key: str) -> BaseModel:
    with _lock:
        if key in _cache:
            return _cache[key]
    value = _read(db, key)
    if key == "assistant":
        # The connection and the generation fields come from the saved profile in use, when there is one
        from . import llmprofiles

        value = llmprofiles.overlay(db, value)
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


def audit_safe(key: str, value: BaseModel) -> dict[str, Any]:
    """What a settings change may be written to the audit log and to syslog.

    The encrypted fields are replaced, and so are the **values** of the free-form header maps: a gateway key
    is routinely put in a header, and a credential has no business in a log line.
    """
    out: dict[str, Any] = {}
    for name, field in value.model_dump().items():
        if name in SECRETS[key]:
            out[name] = "***" if field else ""
        elif name == "headers" and isinstance(field, dict):
            out[name] = {h: "***" for h in field}
        else:
            out[name] = field
    return out


def public(key: str, value: BaseModel) -> dict[str, Any]:
    data = value.model_dump()
    for f in SECRETS[key]:
        data[f"{f}_set"] = bool(data.get(f))
        data[f] = ""
    return data


def clear_cache() -> None:
    with _lock:
        _cache.clear()
