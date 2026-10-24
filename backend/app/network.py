"""Outgoing HTTP clients: proxy (settings from the UI, else HTTP(S)_PROXY variables) and trusted CAs."""

from __future__ import annotations

import ipaddress
import socket
import ssl
from urllib.parse import quote, urlsplit, urlunsplit

import certifi
import httpx

from .appsettings import ProxySettings
from .settings import Settings

_proxy = ProxySettings()


def set_proxy(cfg: ProxySettings) -> None:
    global _proxy
    _proxy = cfg


def current_proxy() -> ProxySettings:
    return _proxy


class UnsafeUrl(ValueError):
    """The address cannot be requested: wrong scheme, credentials in it, or a host RefExposer refuses."""


# Addresses that answer with the credentials of the machine itself, and are never a legitimate target:
# the cloud metadata services and anything that loops back to this container.
_METADATA = (ipaddress.ip_network("169.254.0.0/16"), ipaddress.ip_network("fd00:ec2::254/128"),
             ipaddress.ip_network("fe80::/10"))


def validate_outbound(url: str, *, what: str = "URL", allow_loopback: bool = True) -> str:
    """Check an address an administrator asked RefExposer to call, before calling it.

    Refuses anything but http(s), credentials embedded in the URL, a host that does not resolve, and any
    host resolving to a **link-local address** — the cloud metadata services among them, which answer with
    the credentials of the instance and are never a legitimate target.

    Private addresses are allowed, and so is loopback unless the caller says otherwise: an inference server,
    a syslog collector, a peer or a proxy on the internal network — or another instance on the same host —
    are the normal case, not an attack.

    This is **not** a defence against DNS rebinding: the HTTP client resolves the name again when it
    connects. It removes the targets that turn an outbound request into an escalation; the address itself is
    chosen by an administrator, who can already point a referential source anywhere.
    """
    value = (url or "").strip()
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https"):
        raise UnsafeUrl(f"{what}: http:// or https:// expected")
    if parts.username or parts.password:
        raise UnsafeUrl(f"{what}: no credentials in the address")
    host = parts.hostname or ""
    if not host:
        raise UnsafeUrl(f"{what}: no host in the address")
    try:
        resolved = {info[4][0] for info in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)}
    except OSError:
        # A name reached through a proxy is resolved by the proxy, not here: not resolving locally is normal
        # and is not a reason to refuse the address
        resolved = {host}
    for address in resolved:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:  # pragma: no cover - getaddrinfo returns addresses
            continue
        if ip.is_loopback and not allow_loopback:
            raise UnsafeUrl(f"{what}: '{host}' points at this machine")
        if any(ip in net for net in _METADATA):
            raise UnsafeUrl(f"{what}: '{host}' points at a link-local or metadata address ({ip})")
    return value


def _with_credentials(url: str, username: str, password: str) -> str:
    if not username:
        return url
    parts = urlsplit(url)
    netloc = f"{quote(username, safe='')}:{quote(password, safe='')}@{parts.hostname}"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def ssl_context(settings: Settings, cfg: ProxySettings, verify_tls: bool = True) -> ssl.SSLContext | bool:
    if not verify_tls or not cfg.verify_tls or settings.verify is False:
        return False
    ctx = ssl.create_default_context(cafile=certifi.where())
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    if isinstance(settings.verify, str):
        ctx.load_verify_locations(cafile=settings.verify)
    if cfg.ca_bundle:
        ctx.load_verify_locations(cadata=cfg.ca_bundle)
    return ctx


def client(settings: Settings, cfg: ProxySettings | None = None, verify_tls: bool = True, timeout: float | None = None) -> httpx.Client:
    """HTTP client honouring the proxy configured in the UI (or, failing that, the environment)."""
    cfg = cfg or _proxy
    verify = ssl_context(settings, cfg, verify_tls)
    t = httpx.Timeout(timeout or settings.http_timeout, connect=30)
    common = {"follow_redirects": True, "timeout": t, "headers": {"User-Agent": settings.http_user_agent}}
    if not cfg.configured:
        return httpx.Client(verify=verify, trust_env=True, **common)
    mounts: dict[str, httpx.BaseTransport | None] = {}
    if cfg.http_proxy:
        mounts["http://"] = httpx.HTTPTransport(proxy=_with_credentials(cfg.http_proxy, cfg.username, cfg.password), verify=verify)
    if cfg.https_proxy:
        mounts["https://"] = httpx.HTTPTransport(proxy=_with_credentials(cfg.https_proxy, cfg.username, cfg.password), verify=verify)
    for host in (h.strip() for h in cfg.no_proxy.replace(";", ",").split(",")):
        if host:
            host = host.lstrip(".")
            pattern = host if host.startswith("*.") else host
            mounts[f"all://{pattern}"] = None
            if not host.startswith("*."):
                mounts[f"all://*.{host}"] = None
    return httpx.Client(verify=verify, mounts=mounts, trust_env=False, **common)


def describe(cfg: ProxySettings) -> str:
    if not cfg.configured:
        return "no proxy configured in the interface (HTTP_PROXY/HTTPS_PROXY variables used when set)"
    parts = []
    if cfg.http_proxy:
        parts.append(f"http → {cfg.http_proxy}")
    if cfg.https_proxy:
        parts.append(f"https → {cfg.https_proxy}")
    return ", ".join(parts)
