"""Relational search (investigate.py) and the searches an account keeps and shares."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .. import appsettings, entities, investigate
from ..auth import CurrentUser, audit, require_user
from ..db import get_db
from ..models import Group, SavedSearch, SavedSearchShare, User, now as utcnow
from ..service import Service
from .deps import get_service

def enabled(db: Session) -> bool:
    return bool(appsettings.load(db, "experimental").relational_search)  # type: ignore[attr-defined]


def require_feature(db: Session = Depends(get_db)) -> None:
    """The relational search is an experimental feature, off until an administrator turns it on.

    Applied to every route of this module, so a feature that is off cannot be reached by knowing a URL — not
    the search, not the saved searches, not the list of entity types.
    """
    if not enabled(db):
        raise HTTPException(404, "the relational search is not enabled on this instance "
                                 "(Settings › Experimental features)")


router = APIRouter(tags=["investigate"], dependencies=[Depends(require_feature)])

# Availability, outside the gate: the interface has to know whether to show the feature at all
status_router = APIRouter(tags=["investigate"])


@status_router.get("/investigate/status", summary="Whether the relational search is enabled here")
def feature_status(db: Session = Depends(get_db), user: CurrentUser = Depends(require_user)) -> dict[str, Any]:
    """Answers for every account, enabled or not, so the menu and the routes can be hidden rather than failing."""
    return {"enabled": enabled(db), "experimental": True}


# --------------------------------------------------------------------------- the search itself

class SpecIn(BaseModel):
    """One relational search. The same body is what a saved search stores and replays."""

    model_config = ConfigDict(extra="forbid")

    value: str = Field("", max_length=2048, examples=["CVE-2024-3400"],
                      description="A single seed. Use `values` for several; one of the two is required.")
    values: list[str] = Field(default_factory=list, max_length=investigate.MAX_SEEDS,
                              examples=[["CVE-2024-3400", "185.194.93.14"]],
                              description="Several seeds. What ties them together comes back in `connections`: "
                                          "a node reached from more than one seed is a meeting point, and the "
                                          "shortest chain of evidence between each pair is the link. Two "
                                          "values only meet where their expansions overlap, so a `depth` of 2 "
                                          "or 3 is usually what finds a link.")
    entity: str | None = Field(None, max_length=40,
                               description="Entity type of the value; recognised from the value when omitted")
    depth: int = Field(1, ge=1, le=investigate.MAX_DEPTH,
                       description="How many hops to follow from the value")
    scope: list[str] = Field(default_factory=list, max_length=200,
                             description="Referential ids and `category:<name>` entries; the whole readable "
                                         "catalogue when empty")
    follow: list[str] = Field(default_factory=list, max_length=40,
                              description="Only expand through these entity types (all of them when empty)")
    rows_per_lookup: int = Field(5, ge=1, le=investigate.MAX_ROWS_PER_LOOKUP)
    max_nodes: int = Field(investigate.MAX_NODES, ge=10, le=investigate.MAX_NODES)
    match: Literal["auto", "exact", "text"] = Field(
        "auto", description="auto: exact lookups where the value is a recognised identifier **and** a text "
                            "search of the catalogue · exact: typed lookups only · text: like the global search")

    @model_validator(mode="after")
    def _one_seed_at_least(self) -> "SpecIn":
        if not self.value.strip() and not [v for v in self.values if v.strip()]:
            raise ValueError("give a value, or several in values")
        return self

    def to_spec(self) -> investigate.Spec:
        return investigate.Spec(value=self.value, values=list(self.values), entity=self.entity,
                                depth=self.depth, scope=list(self.scope), follow=list(self.follow),
                                rows_per_lookup=self.rows_per_lookup, max_nodes=self.max_nodes,
                                match=self.match)


@router.get("/investigate/entities", summary="Entity types, and where this account can pivot on them")
def list_entities(service: Service = Depends(get_service), user: CurrentUser = Depends(require_user)) -> dict[str, Any]:
    """The kinds of identifier the instance recognises, and the columns of the catalogue that hold each one.

    A column is typed from the values the profiling kept, so the same identifier is found whatever the source
    calls it; `entities` in a definition overrides that for one column. Only referentials this account may
    read are listed.
    """
    found = investigate.where_entities(service, user)
    return {
        "types": [{**entities.info(t.name), "columns": found.get(t.name, [])} for t in entities.TYPES],
        "categories": sorted({c["category"] for cols in found.values() for c in cols}),
        # A value does not have to be one of these: `match` auto also searches the text of the catalogue
        "match_modes": [{"name": k, "description": v} for k, v in investigate.MATCH_MODES.items()],
        "limits": {"max_depth": investigate.MAX_DEPTH, "max_nodes": investigate.MAX_NODES,
                   "max_rows_per_lookup": investigate.MAX_ROWS_PER_LOOKUP,
                   "budget_seconds": investigate.BUDGET_SECONDS},
    }


def _run(service: Service, user: CurrentUser, spec: investigate.Spec) -> dict[str, Any]:
    try:
        return investigate.run(service, user, spec)
    except investigate.InvestigationError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/investigate", summary="Investigate a value across the catalogue")
def run_investigation(body: SpecIn, request: Request, service: Service = Depends(get_service),
                      user: CurrentUser = Depends(require_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Everything the readable catalogue ties to one or more values: the referentials carrying them, the rows
    that are the evidence, and the graph of what they lead to.

    With **several values** (`values`), what matters is usually what ties them together: every node remembers
    which seeds reached it, so `connections` reports the shortest chain of evidence between each pair and
    `shared` every node several of them reach.

    A **superset of `/api/search`**: a recognised identifier is looked up exactly in every column holding that
    kind of value, *and* the text of the catalogue is searched the same way the global search searches it. So
    free text — a product, a vendor, a binary, a label — is never refused: the identifiers found in the
    matching rows become the first hop of the graph.

    Personal data columns are **always masked** here, whatever this account may reveal elsewhere. Every walk is
    bounded (nodes, rows, queries, wall clock) and says in `warnings` what a bound cut off.
    """
    result = _run(service, user, body.to_spec())
    audit(db, "investigate.run", user=user, request=request,
          detail={"values": [v[:200] for v in result["spec"]["values"]], "depth": body.depth,
                  "match": body.match, "referentials": result["stats"]["referentials_matched"],
                  "rows": result["stats"]["rows"], "connections": result["stats"]["connections"]})
    return result


# --------------------------------------------------------------------------- saved searches

def _group_ids(db: Session, user: CurrentUser) -> list[int]:
    if not user.groups:
        return []
    return list(db.scalars(select(Group.id).where(Group.name.in_(user.groups))))


def _visible(db: Session, user: CurrentUser):
    """Searches this account owns or had shared with it (an administrator sees every one)."""
    stmt = select(SavedSearch)
    if user.is_admin:
        return stmt
    shared = select(SavedSearchShare.search_id).where(
        or_(SavedSearchShare.user_id == user.id,
            SavedSearchShare.group_id.in_(_group_ids(db, user) or [-1])))
    return stmt.where(or_(SavedSearch.owner_id == user.id, SavedSearch.id.in_(shared)))


def _level(db: Session, user: CurrentUser, search: SavedSearch) -> str | None:
    """What this account may do with a search: `owner`, `edit`, `read`, or None."""
    if search.owner_id == user.id:
        return "owner"
    if user.is_admin:
        return "owner"
    groups = set(_group_ids(db, user))
    levels = [s.level for s in search.shares
              if s.user_id == user.id or (s.group_id is not None and s.group_id in groups)]
    if not levels:
        return None
    return "edit" if "edit" in levels else "read"


def _get(db: Session, user: CurrentUser, search_id: int, need: str = "read") -> tuple[SavedSearch, str]:
    search = db.get(SavedSearch, search_id)
    level = _level(db, user, search) if search is not None else None
    if search is None or level is None:
        # Not found and not shared with you are the same answer: an identifier never says something exists
        raise HTTPException(404, "unknown saved search, or not one shared with this account")
    if need == "edit" and level == "read":
        raise HTTPException(403, "this search was shared with you for reading: ask its owner to change it")
    if need == "owner" and level != "owner":
        raise HTTPException(403, "only the owner of a saved search may do that")
    return search, level


def _share_dict(share: SavedSearchShare) -> dict[str, Any]:
    return {"id": share.id, "level": share.level,
            "user_id": share.user_id, "username": share.user.username if share.user else None,
            "group_id": share.group_id, "group": share.group.name if share.group else None}


def _public(search: SavedSearch, level: str, shares: bool = True) -> dict[str, Any]:
    out = {
        "id": search.id,
        "name": search.name,
        "description": search.description,
        "spec": search.spec or {},
        "tags": search.tags or [],
        "owner_id": search.owner_id,
        "owner": search.owner.username if search.owner else None,
        "created_at": search.created_at.isoformat() if search.created_at else None,
        "updated_at": search.updated_at.isoformat() if search.updated_at else None,
        "updated_by": search.updated_by,
        "last_run_at": search.last_run_at.isoformat() if search.last_run_at else None,
        # What this account may do with it, so the interface never offers an action it would refuse
        "level": level,
        "shared": bool(search.shares),
    }
    if shares and level in ("owner", "edit"):
        out["shares"] = [_share_dict(s) for s in search.shares]
    return out


class SearchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=120)
    description: str = Field("", max_length=1000)
    spec: SpecIn
    tags: list[str] = Field(default_factory=list, max_length=20)


class ShareIn(BaseModel):
    """One account or one group, never both — like a grant."""

    model_config = ConfigDict(extra="forbid")

    user_id: int | None = None
    group_id: int | None = None
    level: str = Field("read", pattern="^(read|edit)$")


class SharesIn(BaseModel):
    """The whole list of who a search is shared with: what is not in it is unshared."""

    model_config = ConfigDict(extra="forbid")

    shares: list[ShareIn] = Field(default_factory=list, max_length=200)


@router.get("/searches/share-targets", summary="Who this account may share a search with")
def share_targets(db: Session = Depends(get_db), user: CurrentUser = Depends(require_user)) -> dict[str, Any]:
    """The accounts and groups a saved search can be shared with.

    Deliberately narrow for an ordinary account: **its own groups, and the people in them**. Sharing a search
    is not a reason to hand the whole account directory to everyone who signs in, and a colleague you work
    with is a colleague you share a group with. An administrator sees every account and every group.
    """
    if user.is_admin:
        groups = list(db.scalars(select(Group).order_by(Group.name)))
        users = list(db.scalars(select(User).where(User.is_active.is_(True)).order_by(User.username)))
    elif not user.groups:
        groups, users = [], []
    else:
        groups = list(db.scalars(select(Group).where(Group.name.in_(user.groups)).order_by(Group.name)))
        members = {m.id: m for g in groups for m in g.members if m.is_active and m.id != user.id}
        users = sorted(members.values(), key=lambda u: u.username)
    return {
        "users": [{"id": u.id, "username": u.username, "display_name": u.display_name} for u in users],
        "groups": [{"id": g.id, "name": g.name, "description": g.description} for g in groups],
        # So the interface can say why the lists are short, instead of looking broken
        "scoped_to_groups": not user.is_admin,
    }


@router.get("/searches", summary="Relational searches this account owns or had shared with it")
def list_searches(mine: bool = Query(False, description="Only the ones this account owns"),
                  db: Session = Depends(get_db), user: CurrentUser = Depends(require_user)) -> dict[str, Any]:
    stmt = _visible(db, user).order_by(SavedSearch.updated_at.desc())
    if mine:
        stmt = stmt.where(SavedSearch.owner_id == user.id)
    found = list(db.scalars(stmt))
    return {"searches": [_public(s, _level(db, user, s) or "read", shares=False) for s in found],
            "owned": sum(1 for s in found if s.owner_id == user.id)}


@router.post("/searches", summary="Keep a relational search", status_code=201)
def create_search(body: SearchIn, request: Request, db: Session = Depends(get_db),
                  user: CurrentUser = Depends(require_user)) -> dict[str, Any]:
    """A saved search holds the question, not its result: replaying it reads the catalogue as it is then."""
    search = SavedSearch(name=body.name.strip(), description=body.description.strip(),
                         owner_id=user.id, spec=body.spec.model_dump(), tags=body.tags,
                         updated_by=user.username)
    db.add(search)
    db.commit()
    audit(db, "search.create", user=user, request=request, target=f"search:{search.id}",
          detail={"name": search.name, "value": body.spec.value[:200], "entity": body.spec.entity})
    return _public(search, "owner")


@router.get("/searches/{search_id}", summary="One saved search")
def get_search(search_id: int, db: Session = Depends(get_db),
               user: CurrentUser = Depends(require_user)) -> dict[str, Any]:
    search, level = _get(db, user, search_id)
    return _public(search, level)


@router.put("/searches/{search_id}", summary="Change a saved search")
def update_search(search_id: int, body: SearchIn, request: Request, db: Session = Depends(get_db),
                  user: CurrentUser = Depends(require_user)) -> dict[str, Any]:
    search, level = _get(db, user, search_id, need="edit")
    search.name, search.description = body.name.strip(), body.description.strip()
    search.spec, search.tags, search.updated_by = body.spec.model_dump(), body.tags, user.username
    db.commit()
    audit(db, "search.update", user=user, request=request, target=f"search:{search.id}",
          detail={"name": search.name, "value": body.spec.value[:200]})
    return _public(search, level)


@router.delete("/searches/{search_id}", summary="Delete a saved search")
def delete_search(search_id: int, request: Request, db: Session = Depends(get_db),
                  user: CurrentUser = Depends(require_user)) -> dict[str, Any]:
    search, _ = _get(db, user, search_id, need="owner")
    name = search.name
    db.delete(search)
    db.commit()
    audit(db, "search.delete", user=user, request=request, target=f"search:{search_id}", detail={"name": name})
    return {"deleted": search_id}


@router.put("/searches/{search_id}/shares", summary="Who a saved search is shared with")
def set_shares(search_id: int, body: SharesIn, request: Request, db: Session = Depends(get_db),
               user: CurrentUser = Depends(require_user)) -> dict[str, Any]:
    """Sharing gives access to the **question**, never to data: whoever replays it sees what their own grants
    allow. The list replaces what was there, so a subject left out is unshared."""
    search, level = _get(db, user, search_id, need="edit")
    wanted: dict[tuple[int | None, int | None], str] = {}
    for share in body.shares:
        if (share.user_id is None) == (share.group_id is None):
            raise HTTPException(422, "a share names a user or a group, not both and not neither")
        if share.user_id is not None and db.get(User, share.user_id) is None:
            raise HTTPException(422, f"unknown user: {share.user_id}")
        if share.group_id is not None and db.get(Group, share.group_id) is None:
            raise HTTPException(422, f"unknown group: {share.group_id}")
        if share.user_id == search.owner_id:
            continue  # the owner already has it
        wanted[(share.user_id, share.group_id)] = share.level
    existing = {(s.user_id, s.group_id): s for s in search.shares}
    for key, share in existing.items():
        if key not in wanted:
            db.delete(share)
        elif share.level != wanted[key]:
            share.level = wanted[key]
    for key, want in wanted.items():
        if key not in existing:
            db.add(SavedSearchShare(search_id=search.id, user_id=key[0], group_id=key[1], level=want,
                                    created_by=user.username))
    db.commit()
    db.refresh(search)
    audit(db, "search.share", user=user, request=request, target=f"search:{search.id}",
          detail={"name": search.name, "shares": len(wanted)})
    return _public(search, level)


@router.post("/searches/{search_id}/run", summary="Replay a saved search with the rights of this account")
def run_search(search_id: int, request: Request, service: Service = Depends(get_service),
               db: Session = Depends(get_db), user: CurrentUser = Depends(require_user)) -> dict[str, Any]:
    """The catalogue as it is now, read with **this** account's rights: a search shared by a colleague shows
    you what you may read, never what they may."""
    search, level = _get(db, user, search_id)
    try:
        spec = SpecIn.model_validate(search.spec or {})
    except Exception as e:  # noqa: BLE001
        raise HTTPException(409, f"this saved search cannot be replayed: {type(e).__name__}") from e
    result = _run(service, user, spec.to_spec())
    search.last_run_at = utcnow()
    db.commit()
    audit(db, "investigate.run", user=user, request=request, target=f"search:{search.id}",
          detail={"name": search.name, "values": [v[:200] for v in result["spec"]["values"]],
                  "referentials": result["stats"]["referentials_matched"], "rows": result["stats"]["rows"]})
    return {**result, "search": _public(search, level, shares=False)}
