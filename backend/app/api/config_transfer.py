"""Export and import of the referential configuration, to set up another environment (test -> production...).

The export is a YAML (or JSON) document in the format of the files of config/ (a `referentials:` list), with:
- `grants`: read / manage rights given to groups (by name, portable) and to users (by user name);
- `groups`: the groups these rights use;
- `secrets`: the secrets the definitions reference: names, descriptions and allowed hosts, never the values;
- `refexposer_export`: version, date, author, source instance.
Literal credentials still written in definitions are masked by default (`credentials=masked`): on import, a masked value
keeps the value of the target when the referential exists there, and is refused otherwise.

The import first returns a plan (create, update, unchanged, skip, conflict, error per referential, missing secrets and
groups), then applies the chosen part: definitions, rights, missing groups, imports started or not. Referentials of the
sync folder are not exported (they come from the files of each environment); internal referentials are exported without
their rows.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

import yaml
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import CurrentUser, audit, require_admin
from ..config import secret_names
from ..db import get_db
from ..models import Grant, Group, ReferentialDefinition, SourceSecret, User
from ..secretstore import MASK, mask_config
from ..service import Service
from .admin_referentials import _clean, _validate, store_definition
from .deps import get_service, public_base

router = APIRouter(prefix="/admin/config", tags=["configuration export / import"], dependencies=[Depends(require_admin)])

FORMAT = "refexposer-config"
VERSION = 1
_ORIGIN_FIELDS = ("origin", "config_file")


def _definition(ref: Any, credentials: str) -> dict[str, Any]:
    config = _clean(ref.model_dump(mode="json"))
    for f in _ORIGIN_FIELDS:
        config.pop(f, None)
    return config if credentials == "included" else mask_config(config)


def build_export(db: Session, service: Service, ids: list[str] | None, grants: bool, credentials: str,
                 author: str, instance: str | None) -> dict[str, Any]:
    refs = [r for r in service.refs.values() if r.origin != "sync" and (not ids or r.id in ids)]
    unknown = sorted(set(ids or []) - {r.id for r in refs})
    if unknown:
        raise HTTPException(404, f"unknown or not exportable referential(s): {', '.join(unknown)}")
    refs.sort(key=lambda r: r.id)
    doc: dict[str, Any] = {
        "refexposer_export": {"format": FORMAT, "version": VERSION, "exported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                              "exported_by": author, "instance": instance, "credentials": credentials,
                              "referentials": len(refs)},
        "referentials": [_definition(r, credentials) for r in refs],
    }
    names = sorted(set().union(*(secret_names(r.source.model_dump()) for r in refs)) if refs else set())
    if names:
        stored = {s.name: s for s in db.scalars(select(SourceSecret).where(SourceSecret.name.in_(names)))}
        doc["secrets"] = [{"name": n, "description": stored[n].description if n in stored else None,
                           "hosts": (stored[n].hosts or []) if n in stored else [],
                           "used_by": sorted(r.id for r in refs if n in secret_names(r.source.model_dump()))} for n in names]
    if grants:
        ref_ids = {r.id for r in refs}
        rows = [g for g in db.scalars(select(Grant).order_by(Grant.referential_id, Grant.id))
                if g.referential_id in ref_ids or (g.referential_id == "*" and not ids)]
        out, groups = [], {}
        for g in rows:
            entry: dict[str, Any] = {"referential": g.referential_id, "level": g.level}
            if g.group is not None:
                entry["group"] = g.group.name
                groups[g.group.name] = {"name": g.group.name, "description": g.group.description}
            elif g.user is not None:
                entry["user"] = g.user.username
            out.append(entry)
        if out:
            doc["grants"] = out
            doc["groups"] = sorted(groups.values(), key=lambda x: x["name"])
    return doc


@router.get("/export", summary="Export the referential definitions (YAML or JSON file)")
def export_config(
    request: Request,
    ids: str | None = Query(None, description="Comma-separated referential ids (all when empty)"),
    grants: bool = Query(True, description="Include the rights of the groups and users"),
    credentials: Literal["masked", "included"] = Query("masked", description="Literal credentials of the definitions"),
    format: Literal["yaml", "json"] = Query("yaml"),
    db: Session = Depends(get_db),
    service: Service = Depends(get_service),
    admin: CurrentUser = Depends(require_admin),
):
    wanted = [i.strip() for i in ids.split(",") if i.strip()] if ids else None
    doc = build_export(db, service, wanted, grants, credentials, admin.username, public_base(request))
    audit(db, "config.export", user=admin, request=request,
          detail={"referentials": len(doc["referentials"]), "grants": grants, "credentials": credentials})
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if format == "json":
        body, media, ext = json.dumps(doc, ensure_ascii=False, indent=2), "application/json", "json"
    else:
        header = ("# RefExposer configuration export: import it from Administration › Referentials › Import,\n"
                  "# or drop it into config/ (the 'referentials' list is read like any definition file).\n")
        body = header + yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=110)
        media, ext = "application/yaml", "yaml"
    return Response(body, media_type=f"{media}; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="refexposer-config-{stamp}.{ext}"'})


# --------------------------------------------------------------------------- import

class ImportRequest(BaseModel):
    content: str = Field(..., description="Exported document (YAML or JSON), or any file of config/")
    apply: bool = Field(False, description="false: plan only; true: apply it")
    on_existing: Literal["skip", "update"] = Field("skip", description="Referentials already defined (from the interface)")
    ids: list[str] | None = Field(None, description="Referentials to create / update (all when absent; [] for the rights only)")
    grants: bool = Field(True, description="Import the rights")
    create_groups: bool = Field(True, description="Create the missing groups of the rights")
    pull: bool = Field(False, description="Start the imports of the referentials created or changed")


def _parse(content: str) -> dict[str, Any]:
    try:
        doc = yaml.safe_load(content)  # JSON is valid YAML
    except yaml.YAMLError as e:
        raise HTTPException(400, f"unreadable document: {e}") from e
    if isinstance(doc, list):
        doc = {"referentials": doc}
    elif isinstance(doc, dict) and "referentials" not in doc and "id" in doc:
        doc = {"referentials": [doc]}
    if not isinstance(doc, dict) or not isinstance(doc.get("referentials"), list):
        raise HTTPException(400, "no 'referentials' list in the document")
    meta = doc.get("refexposer_export") or {}
    if meta and (meta.get("format") != FORMAT or int(meta.get("version") or 0) > VERSION):
        raise HTTPException(400, f"unsupported export (format {meta.get('format')}, version {meta.get('version')})")
    return doc


def _has_mask(config: dict[str, Any]) -> bool:
    src = config.get("source") or {}
    return MASK in (src.get("headers") or {}).values() or MASK in (src.get("basic_auth"), src.get("token"))


def _plan(db: Session, service: Service, doc: dict[str, Any], body: ImportRequest) -> list[dict[str, Any]]:
    existing_secrets = set(db.scalars(select(SourceSecret.name)))
    plan, seen = [], set()
    for raw in doc["referentials"]:
        if not isinstance(raw, dict):
            plan.append({"id": None, "action": "error", "reason": "not a definition"})
            continue
        ref_id = str(raw.get("id") or "").strip()
        item: dict[str, Any] = {"id": ref_id, "name": raw.get("name"), "type": (raw.get("source") or {}).get("type", "http")}
        config = {k: v for k, v in raw.items() if k not in ("id", *_ORIGIN_FIELDS)}
        missing = sorted(secret_names(config) - existing_secrets)
        if missing:
            item["missing_secrets"] = missing
        if not ref_id or ref_id in seen:
            item.update(action="error", reason="identifier missing or repeated in the document")
            plan.append(item)
            continue
        seen.add(ref_id)
        if body.ids is not None and ref_id not in body.ids:
            item.update(action="skip", reason="not selected")
            plan.append(item)
            continue
        current = service.refs.get(ref_id)
        stored = db.get(ReferentialDefinition, ref_id)
        if current is not None and stored is None:
            where = f"config/{current.config_file}" if current.origin == "file" else f"the sync folder ({current.config_file})"
            item.update(action="conflict", reason=f"defined in {where}: change it there")
            plan.append(item)
            continue
        if stored is not None and body.on_existing == "skip":
            item.update(action="skip", reason="already defined (option: update the existing ones)")
            plan.append(item)
            continue
        try:
            validated = _validate(db, config, ref_id, stored.config if stored else None)
        except HTTPException as e:
            reason = e.detail
            if _has_mask(config) and stored is None:
                reason = "masked credential in the export: export with credentials included, or use secrets"
            item.update(action="error", reason=reason)
            plan.append(item)
            continue
        if stored is None:
            item["action"] = "create"
        elif _clean(stored.config) == validated:
            item["action"] = "unchanged"
        else:
            item["action"] = "update"
            item["changed"] = sorted(k for k in set(validated) | set(stored.config) if validated.get(k) != stored.config.get(k))
        item["config"] = validated
        plan.append(item)
    return plan


@router.post("/import", summary="Plan (apply=false) or apply the import of referential definitions")
def import_config(body: ImportRequest, request: Request, db: Session = Depends(get_db), service: Service = Depends(get_service),
                  admin: CurrentUser = Depends(require_admin)):
    doc = _parse(body.content)
    plan = _plan(db, service, doc, body)
    # Rights: for every referential of the document present on this instance (whatever the selection of definitions)
    planned = {p["id"] for p in plan if p["id"] and p["id"] in service.refs}
    groups = {g.name: g for g in db.scalars(select(Group))}
    wanted_groups = {g.get("group") for g in doc.get("grants") or [] if isinstance(g, dict) and g.get("group")}
    missing_groups = sorted(n for n in wanted_groups if n and n not in groups)
    existing_secrets = set(db.scalars(select(SourceSecret.name)))
    secrets = [s for s in doc.get("secrets") or [] if isinstance(s, dict) and s.get("name") not in existing_secrets]
    known = {s["name"] for s in secrets}
    for p in plan:
        for n in p.get("missing_secrets", []):
            if n not in known:
                secrets.append({"name": n, "description": None, "hosts": [], "used_by": [p["id"]]})
                known.add(n)
    result: dict[str, Any] = {
        "export": doc.get("refexposer_export"),
        "plan": [{k: v for k, v in p.items() if k != "config"} for p in plan],
        "missing_secrets": secrets,
        "missing_groups": missing_groups,
        "grants": len(doc.get("grants") or []),
        "applied": False,
    }
    if not body.apply:
        return result

    created, updated, errors = [], [], []
    for p in plan:
        if p["action"] == "create":
            try:
                created.append(store_definition(db, service, admin, request, {**p["config"], "id": p["id"]}, [], origin="import"))
            except HTTPException as e:
                db.rollback()
                errors.append({"id": p["id"], "error": e.detail})
        elif p["action"] == "update":
            d = db.get(ReferentialDefinition, p["id"])
            if p["id"] in service.active:
                errors.append({"id": p["id"], "error": "an update is running: retry when it is finished"})
                continue
            d.config, d.updated_by = p["config"], admin.username
            audit(db, "referential.update", user=admin, target=f"ref:{p['id']}", request=request,
                  detail={"origin": "import", "changed": p.get("changed")})
            updated.append(p["id"])
    groups_created, grants_added = [], 0
    if body.grants:
        if body.create_groups:
            for name in missing_groups:
                desc = next((g.get("description") for g in doc.get("groups") or [] if isinstance(g, dict) and g.get("name") == name), None)
                g = Group(name=name, description=desc)
                db.add(g)
                groups[name] = g
                groups_created.append(name)
            db.flush()
        users = {u.username: u for u in db.scalars(select(User))}
        targets = planned | set(created)
        for g in doc.get("grants") or []:
            if not isinstance(g, dict) or g.get("level") not in ("read", "manage"):
                continue
            ref_id = g.get("referential")
            if ref_id != "*" and ref_id not in targets:
                continue
            group = groups.get(g.get("group")) if g.get("group") else None
            user = users.get(g.get("user")) if g.get("user") else None
            if group is None and user is None:
                continue
            q = select(Grant).where(Grant.referential_id == ref_id,
                                    Grant.group_id == group.id if group is not None else Grant.user_id == user.id)  # type: ignore[union-attr]
            current = db.scalar(q)
            if current is None:
                db.add(Grant(referential_id=ref_id, group_id=group.id if group else None, user_id=user.id if user else None,
                             level=g["level"], created_by=admin.username))
                grants_added += 1
            elif current.level != g["level"]:
                current.level = g["level"]
                grants_added += 1
    db.commit()
    if created or updated:
        service.reload()
    runs = 0
    for ref_id in [*created, *updated]:
        ref = service.refs.get(ref_id)
        if ref is None:
            continue
        if ref.is_internal:
            service.publish_internal(ref_id, admin.username, delay=0)  # empty table, rows are not exported
        elif body.pull and ref.enabled and service.submit(ref_id, "manual", False, admin.username):
            runs += 1
    audit(db, "config.import", user=admin, request=request,
          detail={"created": created, "updated": updated, "errors": len(errors), "groups_created": groups_created,
                  "grants": grants_added})
    result.update(applied=True, created=created, updated=updated, errors=errors, groups_created=groups_created,
                  grants_changed=grants_added, runs=runs)
    return result
