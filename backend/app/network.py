"""Outgoing HTTP clients: proxy (settings from the UI, else HTTP(S)_PROXY variables) and trusted CAs."""

from __future__ import annotations

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
