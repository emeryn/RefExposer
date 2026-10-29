"""Synchronization between RefExposer instances (federation).

Upstream, the instance holding the referentials: nothing to set up but a service account, with an API token and the read
right on the referentials to share. /api/federation/catalog lists them for that account: definitions (quality rules,
SQL transformation, key, search columns, indexes, downloads...), published versions and the URL of their data.

Downstream, the replica: an administrator adds a peer (URL of the upstream instance and token of the service account,
kept in the secret manager and only sent to the host of the peer). Each synchronization, on the schedule of the peer:
- reads the catalog, then creates or updates the local definition of each chosen referential (id: prefix + remote id),
  which records where it comes from (`federation`);
- starts the update of the replicas whose published version changed over there. Their source is the published file of
  the peer (Parquet, Bloom filter or MaxMind DB), downloaded as is: the transformation already ran upstream, the
  quality rules are checked again on the replica.
A referential removed upstream (or no longer shared) is kept on the replica, reported as missing.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit, urlunsplit

from apscheduler.triggers.cron import CronTrigger
from pydantic import BaseModel, Field, field_validator

from .storage import iso, utcnow

if TYPE_CHECKING:
    from .config import ReferentialConfig
    from .service import Service

log = logging.getLogger(__name__)

CATALOG_PATH = "/api/federation/catalog"
FORMAT = "refexposer-federation"
VERSION = 1
# Storage settings that describe the published data (the others are choices of each instance)
_STORAGE_FIELDS = ("sort_by", "indexes", "row_group_size", "profile", "track_changes", "check_key", "keep_previous")
_lock = threading.Lock()


class FederationError(Exception):
    pass


class Peer(BaseModel):
    """Another RefExposer instance whose referentials are replicated here."""

    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,31}$")
    url: str
    # Secret of the secret manager holding the API token of the service account of the peer
    secret: str
    # Local identifiers: prefix + remote identifier (e.g. "prod-")
    prefix: str = Field("", pattern=r"^[a-z0-9_-]{0,20}$")
    # Remote identifiers to replicate; every shared referential when empty
    datasets: list[str] = Field(default_factory=list)
    schedule: str = "0 * * * *"
    enabled: bool = True
    # Category of the replicas (the category of the peer when empty)
    category: str = ""
    # Groups (names) given the read right on the replicas when they are created
    grant_groups: list[str] = Field(default_factory=list)

    @field_validator("url")
    @classmethod
    def _url(cls, v: str) -> str:
        v = v.strip().rstrip("/")
        if not re.match(r"^https?://[^\s/]+(/\S*)?$", v):
            raise ValueError("URL of the RefExposer instance expected (https://refexposer.example.com)")
        if re.match(r"^https?://[^/]*@", v):
            raise ValueError("no credentials in the URL: give the API token")
        if "?" in v or "#" in v:
            raise ValueError("the address of the instance only, without ? or #")
        return v

    @field_validator("schedule")
    @classmethod
    def _schedule(cls, v: str) -> str:
        CronTrigger.from_crontab(v)
        return v

    @property
    def host(self) -> str:
        return (urlsplit(self.url).hostname or "").lower()


class FederationSettings(BaseModel):
    peers: list[Peer] = Field(default_factory=list)


# --------------------------------------------------------------------------- upstream: catalog

def catalog_entry(service: "Service", ref: "ReferentialConfig") -> dict[str, Any]:
    """What a replica needs to rebuild a referential: its definition (without the source, which stays here) and its
    published version."""
    meta = service.metas.get(ref.id, {})
    data_path = f"/api/referentials/{ref.id}/raw" if ref.is_artifact else f"/api/referentials/{ref.id}/download/parquet"
    return {
        "id": ref.id,
        "name": ref.name,
        "description": ref.description,
        "category": ref.category,
        "tags": ref.tags,
        "homepage": ref.homepage,
        "license": ref.license,
        "owner": ref.owner,
        "owner_team": ref.owner_team,
        "visibility": ref.visibility,
        # Columns the published file holds masked: the replica receives them masked (the parquet it downloads is
        # generated from the masked view) and says so, instead of presenting pseudonyms as real values
        "pii_columns": [c["name"] for c in (meta.get("columns") or [])
                        if c["name"].lower() in {m.lower() for m in (ref.pii.columns if ref.pii else [])}] or None,
        "kind": "bloom" if ref.is_bloom else "mmdb" if ref.is_mmdb else "internal" if ref.is_internal else "table",
        "format": ref.format,
        "source_type": ref.source.type,
        "key": ref.key,
        "search_columns": ref.search_columns,
        "transform": ref.transform,
        "quality": ref.quality.model_dump(mode="json"),
        "validation": ref.validation.model_dump(mode="json"),
        "storage": {k: v for k, v in ref.storage.model_dump(mode="json").items() if k in _STORAGE_FIELDS},
        "downloads": list(ref.downloads),
        "max_age": ref.max_age,
        "confidential": ref.confidential,
        # Bloom filter / MaxMind DB: reading options of the published file
        "options": ref.options if ref.is_artifact else {},
        "federation": ref.federation.model_dump() if ref.federation else None,
        "version": meta.get("version"),
        "fingerprint": meta.get("fingerprint"),
        "data_updated_at": meta.get("data_updated_at"),
        "row_count": meta.get("row_count"),
        "columns": meta.get("columns") or [],
        "quality_result": {k: (meta.get("quality") or {}).get(k) for k in ("score", "status", "checked_at")} if meta.get("quality") else None,
        "data": {"path": data_path, "format": ref.format if ref.is_artifact else "parquet"},
    }


def build_catalog(service: "Service", user: Any, instance_url: str | None) -> dict[str, Any]:
    from . import __version__

    refs = sorted((r for r in service.refs.values() if user.can_read(r.id) and service.has_data(r.id)), key=lambda r: r.id)
    return {
        "format": FORMAT,
        "version": VERSION,
        "instance": {"url": instance_url, "refexposer": __version__, "account": user.username},
        "generated_at": iso(utcnow()),
        "datasets": [catalog_entry(service, r) for r in refs],
    }


# --------------------------------------------------------------------------- downstream: peers

def load_settings() -> FederationSettings:
    from . import appsettings
    from .db import session_factory

    with session_factory()() as db:
        return appsettings.load(db, "federation")  # type: ignore[return-value]


def get_peer(name: str) -> Peer:
    for p in load_settings().peers:
        if p.name == name:
            return p
    raise FederationError(f"unknown peer: '{name}'")


def _state_file(service: "Service") -> Path:
    return service.settings.data_dir / ".system" / "federation.json"


def load_state(service: "Service") -> dict[str, Any]:
    try:
        return json.loads(_state_file(service).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(service: "Service", state: dict[str, Any]) -> None:
    f = _state_file(service)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(f)


def forget_peer(service: "Service", name: str) -> None:
    with _lock:
        state = load_state(service)
        state.pop(name, None)
        _save_state(service, state)


def catalog_url(url: str) -> str:
    """Address of the catalog of a peer, built from the parts of its address rather than concatenated.

    Appending the path to the address as text sends the request somewhere else as soon as the address of the
    peer carries a query or a fragment (`https://peer/#` + the path asks for `https://peer/`), and the call
    carries the API token of a service account: the path it reaches has to be the path meant.
    """
    value = (url or "").strip()
    # The characters, not the parts urlsplit() reports: a trailing '#' splits into an *empty* fragment and
    # would still cut the path off the request
    if "?" in value or "#" in value:
        raise ValueError("address of the peer: the address of the instance only, without ? or #")
    parts = urlsplit(value)
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/") + CATALOG_PATH, "", ""))


def fetch_catalog(service: "Service", url: str, token: str) -> dict[str, Any]:
    """Catalog of the peer, read with the token of its service account."""
    import httpx

    from .ingest import http_client

    from .network import UnsafeUrl, validate_outbound

    # The address of the peer is configured by an administrator: checked before each call, so a peer whose
    # name later resolves to this machine or to a metadata service is refused rather than followed. This call
    # carries the API token of the service account on the peer, so redirects are not followed: the catalog is
    # at a known path, and a 302 would hand that token to whoever answers next.
    try:
        target = validate_outbound(catalog_url(url), what="address of the peer")
    except (UnsafeUrl, ValueError) as e:
        raise FederationError(str(e)) from e
    try:
        with http_client(service.settings, follow_redirects=False) as client:
            r = client.get(target, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"}, timeout=60)
    except httpx.HTTPError as e:
        raise FederationError(f"peer unreachable ({target}): {e}") from e
    if r.status_code in (401, 403):
        raise FederationError(f"the peer refused the token (HTTP {r.status_code}): check the API token of its service account")
    if r.status_code == 404:
        raise FederationError(f"no federation catalog at {target}: is it a RefExposer instance (of a recent version)?")
    if r.status_code >= 400:
        raise FederationError(f"peer error (HTTP {r.status_code}): {r.text[:300]}")
    try:
        doc = r.json()
    except ValueError as e:
        raise FederationError(f"the answer of {target} is not a federation catalog") from e
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        raise FederationError(f"the answer of {target} is not a federation catalog")
    return doc


def peer_token(peer: Peer) -> str:
    from .config import expand_env

    try:
        return expand_env(f"${{secret:{peer.secret}}}", peer.url)
    except ValueError as e:
        raise FederationError(str(e)) from e


def local_id(peer: Peer, remote_id: str) -> str:
    return (peer.prefix + remote_id)[:63]


def local_definition(peer: Peer, ds: dict[str, Any]) -> dict[str, Any]:
    """Definition of the replica: everything of the remote referential but its source, replaced by its published file."""
    data = ds.get("data") or {}
    fmt = data.get("format") or "parquet"
    masked = list(ds.get("pii_columns") or [])
    description = ds.get("description") or ""
    if masked:
        # The peer served the data masked: those columns hold what it chose to show, not the real values. No
        # masking is applied again here — that would mask a pseudonym — but it is said in plain sight.
        note = (f"Personal data received masked from {peer.name}: {', '.join(masked)}. "
                "Their real values stay on that instance.")
        description = f"{description}\n\n{note}" if description else note
    config: dict[str, Any] = {
        "name": ds["name"],
        "description": description,
        "category": peer.category or ds.get("category") or "General",
        "tags": list(ds.get("tags") or []),
        "homepage": ds.get("homepage"),
        "license": ds.get("license"),
        "owner": ds.get("owner"),
        "owner_team": ds.get("owner_team"),
        "visibility": ds.get("visibility") or "internal",
        "format": fmt,
        "source": {"type": "http", "urls": [peer.url + data.get("path", "")],
                   "headers": {"Authorization": f"Bearer ${{secret:{peer.secret}}}"}},
        "options": dict(ds.get("options") or {}) if fmt != "parquet" else {},
        "key": ds.get("key"),
        "search_columns": list(ds.get("search_columns") or []),
        "max_age": ds.get("max_age"),
        "confidential": bool(ds.get("confidential")),
        "federation": {"peer": peer.name, "remote_id": ds["id"], "remote_url": f"{peer.url}/referentials/{ds['id']}",
                       "transform": ds.get("transform"), "remote_format": ds.get("format"), "remote_source": ds.get("source_type")},
    }
    if fmt == "parquet":  # tables: same quality rules, guards, layout and generated downloads
        config.update({"quality": ds.get("quality") or {}, "validation": ds.get("validation") or {},
                       "storage": dict(ds.get("storage") or {}), "downloads": list(ds.get("downloads") or [])})
    return config


def _same(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)


def sync_peer(service: "Service", name: str, user: str | None = None, start_updates: bool = True) -> dict[str, Any]:
    """Read the catalog of the peer, create / update the definitions of the replicas, start the updates of the
    replicas whose remote version changed. Returns the report (also kept in data/.system/federation.json)."""
    from sqlalchemy import select

    from .api.admin_referentials import _clean
    from .config import validate_definition
    from .db import session_factory
    from .models import Grant, Group, ReferentialDefinition
    from .auth import audit

    peer = get_peer(name)
    started_at = iso(utcnow())
    report: dict[str, Any] = {"peer": name, "started_at": started_at, "created": [], "updated": [], "unchanged": [],
                              "updates_started": [], "conflicts": [], "errors": [], "missing": []}
    with _lock:
        state = load_state(service)
    peer_state = state.get(name) or {}
    known: dict[str, Any] = dict(peer_state.get("datasets") or {})
    try:
        catalog = fetch_catalog(service, peer.url, peer_token(peer))
    except FederationError as e:
        peer_state.update({"last_sync_at": started_at, "status": "error", "message": str(e)})
        with _lock:
            state = load_state(service)
            state[name] = peer_state
            _save_state(service, state)
        raise
    datasets = [d for d in catalog.get("datasets") or [] if not peer.datasets or d.get("id") in peer.datasets]
    if peer.datasets:
        report["missing"] = sorted(set(peer.datasets) - {d.get("id") for d in datasets})
    changed = False
    with session_factory()() as db:
        groups = {g.name: g for g in db.scalars(select(Group).where(Group.name.in_(peer.grant_groups)))} if peer.grant_groups else {}
        for ds in datasets:
            rid = ds.get("id")
            lid = local_id(peer, rid)
            try:
                existing = service.refs.get(lid)
                stored = db.get(ReferentialDefinition, lid)
                owner = ((stored.config if stored else {}) or {}).get("federation") or {}
                if (existing and not stored) or (stored and owner.get("peer") != name):
                    where = f"config/{existing.config_file}" if existing and existing.origin == "file" else "this instance"
                    report["conflicts"].append({"id": rid, "local_id": lid, "error": f"'{lid}' already exists on {where}: "
                                                                                    "set a prefix for the peer"})
                    continue
                config = _clean(validate_definition({**local_definition(peer, ds), "id": lid}).model_dump(mode="json", exclude={"id"}))
                if stored is None:
                    db.add(ReferentialDefinition(id=lid, config=config, created_by=f"federation:{name}", updated_by=f"federation:{name}"))
                    for g in groups.values():
                        db.add(Grant(referential_id=lid, group_id=g.id, level="read", created_by=f"federation:{name}"))
                    report["created"].append(lid)
                    changed = True
                elif not _same(stored.config, config):
                    if lid in service.active:  # changed when the update ends, at the next synchronization
                        report["unchanged"].append(lid)
                        continue
                    stored.config = config
                    stored.updated_by = f"federation:{name}"
                    report["updated"].append(lid)
                    changed = True
                else:
                    report["unchanged"].append(lid)
            except ValueError as e:
                report["errors"].append({"id": rid, "local_id": lid, "error": str(e)})
        db.commit()
        if report["created"] or report["updated"]:
            audit(db, "federation.sync", username=user or f"federation:{name}", target=f"peer:{name}",
                  detail={k: report[k] for k in ("created", "updated", "conflicts")})
    if changed:
        service.reload()
    now = iso(utcnow())
    seen = set()
    for ds in datasets:
        lid = local_id(peer, ds["id"])
        if lid not in service.refs or any(c["local_id"] == lid for c in report["conflicts"] + report["errors"]):
            continue
        seen.add(ds["id"])
        before = known.get(ds["id"]) or {}
        remote = {"version": ds.get("version"), "fingerprint": ds.get("fingerprint")}
        due = (remote != {k: before.get(k) for k in remote} or not service.has_data(lid)
               or (service.metas.get(lid) or {}).get("status") in ("error", "corrupted", "rejected"))
        entry = {**before, "local_id": lid, "name": ds.get("name"), "remote_version": ds.get("version"),
                 "remote_rows": ds.get("row_count"), "remote_updated_at": ds.get("data_updated_at"), "seen_at": now, "missing": False}
        if due and start_updates and service.refs[lid].enabled:
            run = service.submit(lid, "federation", False, user)
            if run is not None:
                report["updates_started"].append(lid)
                entry.update(remote)  # this version is being replicated
        elif not due:
            entry.update(remote)
        known[ds["id"]] = entry
    for rid, entry in known.items():  # replicated before, no longer in the catalog: kept here
        if rid not in seen and not entry.get("missing"):
            entry["missing"] = True
            report["missing"].append(rid)
    status = "error" if report["errors"] else "warning" if report["conflicts"] or report["missing"] else "success"
    message = (f"{len(datasets)} referential(s) shared: {len(report['created'])} created, {len(report['updated'])} updated, "
               f"{len(report['updates_started'])} update(s) started"
               + (f", {len(report['conflicts'])} conflict(s)" if report["conflicts"] else "")
               + (f", {len(report['missing'])} missing" if report["missing"] else "")
               + (f", {len(report['errors'])} error(s)" if report["errors"] else ""))
    report.update({"status": status, "message": message, "finished_at": now,
                   "instance": catalog.get("instance"), "shared": len(catalog.get("datasets") or [])})
    with _lock:
        state = load_state(service)
        state[name] = {"last_sync_at": started_at, "status": status, "message": message, "datasets": known,
                       "instance": catalog.get("instance"), "last_report": report}
        _save_state(service, state)
    log.info("[federation:%s] %s", name, message)
    return report


def _scheduled_sync(service: "Service", name: str) -> None:
    try:
        sync_peer(service, name)
    except FederationError as e:
        log.warning("[federation:%s] %s", name, e)
    except Exception:  # noqa: BLE001
        log.exception("[federation:%s] synchronization failed", name)


def schedule_peers(service: "Service") -> None:
    """One job per enabled peer, on its schedule (called with the jobs of the referentials)."""
    try:
        peers = load_settings().peers
    except Exception as e:  # noqa: BLE001 - database not ready (tests, first start)
        log.debug("federation settings unavailable: %s", e)
        return
    for p in peers:
        if p.enabled:
            service.scheduler.add_job(_scheduled_sync, CronTrigger.from_crontab(p.schedule, timezone=service.tz),
                                      args=[service, p.name], id=f"peer:{p.name}", name=f"Synchronization with {p.name}",
                                      coalesce=True, max_instances=1, misfire_grace_time=3600, replace_existing=True)
