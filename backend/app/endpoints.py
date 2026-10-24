"""Inventory of the outgoing connections: every URL the referentials contact, grouped by host (an API called by several
referentials appears once, with all of them), with the authentication used (names only, never values), the estimated
number of calls per day, the last contact and errors. The other connections of the instance (notification webhooks,
SMTP, syslog, LDAP, OpenID Connect, proxy) are listed too: together, the allowlist of a firewall or of a proxy.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from apscheduler.triggers.cron import CronTrigger

from .config import secret_names

_SENSITIVE = re.compile(r"(key|token|secret|pass|pwd|auth|sig|signature|credential|session|apikey|access)", re.I)
_DEFAULT_PORTS = {"http": 80, "https": 443}


def safe_url(url: str) -> str:
    """URL without credentials: user info and the values of sensitive query parameters (api_key...) masked."""
    p = urlparse(url)
    netloc = p.hostname or ""
    if p.port:
        netloc += f":{p.port}"
    if p.username or p.password:
        netloc = "***@" + netloc
    query = urlencode([(k, "***" if _SENSITIVE.search(k) else v) for k, v in parse_qsl(p.query, keep_blank_values=True)], safe="*")
    return urlunparse((p.scheme, netloc, p.path, p.params, query, ""))


def host_key(url: str) -> str:
    p = urlparse(url)
    port = p.port if p.port and p.port != _DEFAULT_PORTS.get(p.scheme) else None
    return f"{p.scheme}://{(p.hostname or '').lower()}{f':{port}' if port else ''}"


@lru_cache(maxsize=512)
def calls_per_day(schedule: str | None) -> float:
    """Average number of runs per day of a cron expression (over the next 7 days).

    Counted in UTC: a cron expression fires as often whatever the time zone, except on the two days a year a
    daylight saving change adds or removes an hour, which does not matter for an estimate."""
    if not schedule:
        return 0.0
    try:
        trig = CronTrigger.from_crontab(schedule, timezone="UTC")
    except ValueError:
        return 0.0
    now = datetime.now(trig.timezone)
    end, count, t = now + timedelta(days=7), 0, trig.get_next_fire_time(None, now)
    while t and t < end and count < 20_000:
        count += 1
        t = trig.get_next_fire_time(t, t + timedelta(seconds=1))
    return round(count / 7, 2)


def _auth(source: dict[str, Any]) -> tuple[list[str], list[str]]:
    """(kinds of authentication, secrets referenced) of a source."""
    kinds = []
    if source.get("headers"):
        kinds += [f"header {h}" for h in source["headers"]]
    if source.get("basic_auth"):
        kinds.append("basic")
    if source.get("token"):
        kinds.append("git token")
    return kinds, sorted(secret_names([source.get("headers"), source.get("basic_auth"), source.get("token")]))


def referential_endpoints(ref: Any, meta: dict[str, Any]) -> list[dict[str, Any]]:
    src = ref.source.model_dump()
    kinds, secrets = _auth(src)
    seen = {s.get("url"): s for s in meta.get("sources") or [] if s.get("url")}
    per_day = calls_per_day(ref.schedule) if ref.enabled else 0.0
    base = {"referential": ref.id, "name": ref.name, "category": ref.category, "enabled": ref.enabled, "schedule": ref.schedule,
            "health_status": meta.get("status"), "last_error": meta.get("last_error"), "auth": kinds, "secrets": secrets}
    out = []
    targets: list[tuple[str, str]] = []
    if ref.source.type == "http":
        targets += [(u, "source") for u in ref.source.urls]
    elif ref.source.type == "git" and ref.source.repository:
        targets.append((ref.source.repository, "git"))
    if ref.incremental:
        targets += [(u, "delta") for u in ref.incremental.urls]
    enrich = ref.options.get("enrich_bulk_url") if isinstance(ref.options, dict) else None
    if enrich:  # online enrichment of the Bloom filter lookups: called at query time, not by the updates
        targets.append((str(enrich), "enrichment"))
    for url, role in targets:
        last = seen.get(url) or {}
        out.append({**base, "url": url, "role": role,
                    "calls_per_day": per_day if role != "enrichment" else None,
                    "auth": base["auth"] if role != "enrichment" else (["key"] if ref.options.get("enrich_key") else []),
                    "last_contact": last.get("checked_at") or last.get("fetched_at"),
                    "last_download": last.get("fetched_at") if not last.get("not_modified") else None,
                    "downloaded_bytes": last.get("downloaded_bytes"),
                    "not_modified": last.get("not_modified")})
    return out


def other_connections(db: Any) -> list[dict[str, Any]]:
    from . import appsettings

    out: list[dict[str, Any]] = []
    for ch in appsettings.load(db, "notifications").channels:  # type: ignore[attr-defined]
        if ch.type == "webhook" and ch.url:
            out.append({"kind": "notification webhook", "name": ch.name, "url": ch.url, "enabled": ch.enabled,
                        "secrets": sorted(secret_names([ch.headers, ch.signing_secret]))})
    smtp = appsettings.load(db, "smtp")
    if smtp.host:  # type: ignore[attr-defined]
        out.append({"kind": "SMTP", "name": smtp.from_address, "url": f"smtp://{smtp.host}:{smtp.port}", "enabled": True,  # type: ignore[attr-defined]
                    "detail": smtp.security})  # type: ignore[attr-defined]
    syslog = appsettings.load(db, "syslog")
    if syslog.host:  # type: ignore[attr-defined]
        out.append({"kind": "syslog", "name": syslog.protocol.upper(), "url": f"syslog://{syslog.host}:{syslog.port}",  # type: ignore[attr-defined]
                    "enabled": syslog.enabled})  # type: ignore[attr-defined]
    ldap = appsettings.load(db, "ldap")
    if ldap.server_url:  # type: ignore[attr-defined]
        out.append({"kind": "LDAP", "name": ldap.label, "url": ldap.server_url, "enabled": ldap.enabled})  # type: ignore[attr-defined]
    oidc = appsettings.load(db, "oidc")
    for u in {oidc.issuer, oidc.discovery_url} - {""}:  # type: ignore[attr-defined]
        out.append({"kind": "OpenID Connect", "name": oidc.label, "url": u, "enabled": oidc.enabled})  # type: ignore[attr-defined]
    proxy = appsettings.load(db, "proxy")
    for u in {proxy.http_proxy, proxy.https_proxy} - {""}:  # type: ignore[attr-defined]
        out.append({"kind": "proxy", "name": "outgoing proxy", "url": u, "enabled": True})
    for o in out:
        o["host"] = host_key(o["url"]) if "://" in o["url"] else o["url"]
        o["url"] = safe_url(o["url"])
    return out


def inventory(service: Any, db: Any) -> dict[str, Any]:
    entries = [e for ref in service.refs.values() for e in referential_endpoints(ref, service.metas.get(ref.id, {}))]
    hosts: dict[str, dict[str, Any]] = {}
    for e in entries:
        h = hosts.setdefault(host_key(e["url"]), {"host": host_key(e["url"]), "urls": {}, "referentials": set(), "calls_per_day": 0.0,
                                                  "auth": set(), "secrets": set(), "errors": set(), "roles": set(),
                                                  "last_contact": None, "downloaded_bytes": 0})
        url = safe_url(e["url"])
        u = h["urls"].setdefault(url, {"url": url, "referentials": [], "calls_per_day": 0.0, "last_contact": None})
        u["referentials"].append({k: e[k] for k in ("referential", "name", "role", "enabled", "schedule", "calls_per_day", "auth",
                                                    "secrets", "health_status", "last_error", "last_contact", "last_download",
                                                    "downloaded_bytes", "not_modified")})
        u["calls_per_day"] += e["calls_per_day"] or 0
        u["last_contact"] = max(filter(None, [u["last_contact"], e["last_contact"]]), default=None)
        h["referentials"].add(e["referential"])
        h["calls_per_day"] += e["calls_per_day"] or 0
        h["auth"].update(e["auth"])
        h["secrets"].update(e["secrets"])
        h["roles"].add(e["role"])
        if e["health_status"] in ("error", "corrupted", "rejected"):
            h["errors"].add(e["referential"])
        h["last_contact"] = max(filter(None, [h["last_contact"], e["last_contact"]]), default=None)
        h["downloaded_bytes"] += e["downloaded_bytes"] or 0
    out_hosts = []
    for h in hosts.values():
        urls = sorted(h["urls"].values(), key=lambda u: (-len(u["referentials"]), u["url"]))
        for u in urls:
            u["calls_per_day"] = round(u["calls_per_day"], 2)
            u["shared"] = len({r["referential"] for r in u["referentials"]}) > 1
        out_hosts.append({
            "host": h["host"],
            "scheme": urlparse(h["host"]).scheme,
            "urls": urls,
            "url_count": len(urls),
            "referentials": sorted(h["referentials"]),
            "referential_count": len(h["referentials"]),
            "calls_per_day": round(h["calls_per_day"], 2),
            "auth": sorted(h["auth"]),
            "secrets": sorted(h["secrets"]),
            "roles": sorted(h["roles"]),
            "errors": sorted(h["errors"]),
            "last_contact": h["last_contact"],
            "downloaded_bytes": h["downloaded_bytes"],
        })
    out_hosts.sort(key=lambda h: (-h["referential_count"], h["host"]))
    others = other_connections(db)
    return {
        "hosts": out_hosts,
        "others": others,
        "totals": {
            "hosts": len(out_hosts),
            "urls": sum(h["url_count"] for h in out_hosts),
            "referentials": len({e["referential"] for e in entries}),
            "calls_per_day": round(sum(h["calls_per_day"] for h in out_hosts), 1),
            "insecure_hosts": sum(1 for h in out_hosts if h["scheme"] == "http"),
            "shared_urls": sum(1 for h in out_hosts for u in h["urls"] if u["shared"]),
        },
        "local_sources": sorted(r.id for r in service.refs.values() if r.source.type in ("local", "sync", "internal")),
    }
