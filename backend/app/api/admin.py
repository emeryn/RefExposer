from __future__ import annotations

import re
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import CurrentUser, audit, require_admin, revoke_sessions
from ..db import get_db
from ..models import ALL_REFERENTIALS, SERVICE_PASSWORD, ApiToken, AuditLog, AuthSession, Grant, Group, User, WebauthnCredential, aware, now
from ..security import hash_password, password_problems
from ..service import Service
from .auth import TokenCreate, token_dict, issue_token
from .deps import get_service

router = APIRouter(prefix="/admin", tags=["administration"], dependencies=[Depends(require_admin)])

USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._@-]{1,63}$")


# --------------------------------------------------------------------------- schemas

def valid_username(v: str) -> str:
    v = v.strip().lower()
    if not USERNAME_RE.match(v):
        raise ValueError("invalid username (2 to 64 characters: lowercase letters, digits, . _ @ -)")
    return v


class UserCreate(BaseModel):
    username: str
    display_name: str | None = Field(None, max_length=128)
    email: str | None = Field(None, max_length=255)
    role: Literal["admin", "advanced", "user"] = "user"
    password: str = Field(..., max_length=1024)
    must_change_password: bool = True
    group_ids: list[int] = Field(default_factory=list)

    @field_validator("username")
    @classmethod
    def _username(cls, v: str) -> str:
        return valid_username(v)


class ServiceAccountCreate(BaseModel):
    username: str
    display_name: str | None = Field(None, max_length=128, description="What the account is used for")
    email: str | None = Field(None, max_length=255, description="Contact of the team owning the tool")
    group_ids: list[int] = Field(default_factory=list)

    @field_validator("username")
    @classmethod
    def _username(cls, v: str) -> str:
        return valid_username(v)


class UserUpdate(BaseModel):
    display_name: str | None = Field(None, max_length=128)
    email: str | None = Field(None, max_length=255)
    role: Literal["admin", "advanced", "user"] | None = None
    is_active: bool | None = None
    password: str | None = Field(None, max_length=1024)
    must_change_password: bool | None = None
    group_ids: list[int] | None = None
    unlock: bool = False


class ApprovalIn(BaseModel):
    role: Literal["admin", "advanced", "user"] | None = None
    group_ids: list[int] = Field(default_factory=list, description="Local groups to add")
    reason: str | None = Field(None, max_length=500)


class GroupIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    description: str | None = Field(None, max_length=255)
    member_ids: list[int] | None = None


class GrantIn(BaseModel):
    referential_id: str = Field(..., max_length=64)
    user_id: int | None = None
    group_id: int | None = None
    level: Literal["read", "manage"] = "read"


# --------------------------------------------------------------------------- helpers

def user_dict(u: User, db: Session) -> dict[str, Any]:
    tokens = db.scalar(select(func.count()).select_from(ApiToken).where(ApiToken.user_id == u.id))
    sessions = db.scalar(select(func.count()).select_from(AuthSession).where(AuthSession.user_id == u.id, AuthSession.expires_at > now()))
    return {
        "id": u.id,
        "username": u.username,
        "display_name": u.display_name,
        "email": u.email,
        "role": u.role,
        "is_active": u.is_active,
        "must_change_password": u.must_change_password,
        "locked": bool(u.locked_until and aware(u.locked_until) > now()),
        "groups": [{"id": g.id, "name": g.name} for g in sorted(u.groups, key=lambda g: g.name)],
        "last_login_at": u.last_login_at,
        "password_changed_at": u.password_changed_at,
        "created_at": u.created_at,
        "token_count": tokens,
        "session_count": sessions,
        "auth_source": u.auth_source,
        "is_service": u.auth_source == "service",
        "mfa_enabled": bool(u.totp_enabled) or bool(db.scalar(select(func.count()).select_from(WebauthnCredential).where(WebauthnCredential.user_id == u.id))),
        "token_last_used_at": db.scalar(select(func.max(ApiToken.last_used_at)).where(ApiToken.user_id == u.id)),
        "external_id": u.external_id,
        "status": u.status,
        "approved_at": u.approved_at,
        "approved_by": u.approved_by,
        "last_sync_at": u.last_sync_at,
        "groups": [{"id": g.id, "name": g.name, "source": g.source} for g in sorted(u.groups, key=lambda g: g.name)],
    }


def group_dict(g: Group, db: Session) -> dict[str, Any]:
    grants = db.scalar(select(func.count()).select_from(Grant).where(Grant.group_id == g.id))
    return {
        "id": g.id,
        "name": g.name,
        "description": g.description,
        "created_at": g.created_at,
        "source": g.source,
        "external_id": g.external_id,
        "members": [{"id": u.id, "username": u.username, "display_name": u.display_name} for u in sorted(g.members, key=lambda u: u.username)],
        "grant_count": grants,
    }


def grant_dict(g: Grant, service: Service) -> dict[str, Any]:
    ref = service.refs.get(g.referential_id)
    return {
        "id": g.id,
        "referential_id": g.referential_id,
        "referential_name": "All referentials" if g.referential_id == ALL_REFERENTIALS else (ref.name if ref else None),
        "level": g.level,
        "subject_type": "user" if g.user_id else "group",
        "user": {"id": g.user.id, "username": g.user.username, "display_name": g.user.display_name} if g.user else None,
        "group": {"id": g.group.id, "name": g.group.name} if g.group else None,
        "created_at": g.created_at,
        "created_by": g.created_by,
    }


def _active_admins(db: Session, exclude: int | None = None) -> int:
    q = select(func.count()).select_from(User).where(User.role == "admin", User.is_active.is_(True), User.status == "active")
    if exclude is not None:
        q = q.where(User.id != exclude)
    return db.scalar(q) or 0


def _groups(db: Session, ids: list[int]) -> list[Group]:
    groups = list(db.scalars(select(Group).where(Group.id.in_(ids)))) if ids else []
    if len(groups) != len(set(ids)):
        raise HTTPException(400, "unknown group")
    return groups


def _check_password(password: str, username: str) -> None:
    problems = password_problems(password, username)
    if problems:
        raise HTTPException(400, "password too weak: " + ", ".join(problems))


# --------------------------------------------------------------------------- users

@router.get("/users", summary="Users")
def list_users(status: Literal["active", "pending", "rejected"] | None = None, db: Session = Depends(get_db)):
    q = select(User).order_by(User.username)
    if status:
        q = q.where(User.status == status)
    return [user_dict(u, db) for u in db.scalars(q)]


@router.post("/users/{user_id}/approve", summary="Approve an access request")
def approve_user(user_id: int, body: ApprovalIn, request: Request, db: Session = Depends(get_db), admin: CurrentUser = Depends(require_admin)):
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(404, "user not found")
    u.status, u.approved_at, u.approved_by = "active", now(), admin.username
    if body.role and u.auth_source != "service":
        u.role = body.role
    if body.group_ids:
        extra = [g for g in _groups(db, body.group_ids) if g.source == "local" and g not in u.groups]
        u.groups = [*u.groups, *extra]
    audit(db, "user.approve", user=admin, target=f"user:{u.username}", request=request,
          detail={"role": u.role, "groups": sorted(g.name for g in u.groups)})
    return user_dict(u, db)


@router.post("/users/{user_id}/reject", summary="Refuse an access request")
def reject_user(user_id: int, body: ApprovalIn, request: Request, db: Session = Depends(get_db), admin: CurrentUser = Depends(require_admin)):
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(404, "user not found")
    if u.id == admin.id:
        raise HTTPException(400, "you cannot refuse your own access")
    if u.role == "admin" and u.status == "active" and _active_admins(db, exclude=u.id) == 0:
        raise HTTPException(400, "not possible: this account is the last active administrator")
    u.status, u.approved_at, u.approved_by = "rejected", now(), admin.username
    revoke_sessions(db, u.id)
    audit(db, "user.reject", user=admin, target=f"user:{u.username}", request=request, detail={"reason": body.reason})
    return user_dict(u, db)


@router.post("/users", summary="Create a user", status_code=201)
def create_user(body: UserCreate, request: Request, db: Session = Depends(get_db), admin: CurrentUser = Depends(require_admin)):
    if db.scalar(select(User.id).where(User.username == body.username)):
        raise HTTPException(409, f"the username '{body.username}' already exists")
    _check_password(body.password, body.username)
    u = User(
        username=body.username,
        display_name=body.display_name,
        email=body.email,
        role=body.role,
        password_hash=hash_password(body.password),
        must_change_password=body.must_change_password,
        password_changed_at=now(),
        groups=_groups(db, body.group_ids),
    )
    db.add(u)
    db.flush()
    audit(db, "user.create", user=admin, target=f"user:{u.username}", request=request, detail={"role": u.role})
    return user_dict(u, db)


@router.post("/service-accounts", summary="Create a service account (API tokens only)", status_code=201)
def create_service_account(body: ServiceAccountCreate, request: Request, db: Session = Depends(get_db), admin: CurrentUser = Depends(require_admin)):
    """A service account never signs in to the interface: it calls the API with tokens created by an
    administrator, with the rights given to it (directly or through its groups), like a user."""
    if db.scalar(select(User.id).where(User.username == body.username)):
        raise HTTPException(409, f"the username '{body.username}' already exists")
    u = User(
        username=body.username,
        display_name=body.display_name,
        email=body.email,
        role="user",
        auth_source="service",
        password_hash=SERVICE_PASSWORD,
        must_change_password=False,
        status="active",
        approved_at=now(),
        approved_by=admin.username,
        groups=_groups(db, body.group_ids),
    )
    db.add(u)
    db.flush()
    audit(db, "user.create", user=admin, target=f"user:{u.username}", request=request,
          detail={"service_account": True, "groups": sorted(g.name for g in u.groups)})
    return user_dict(u, db)


def _user(db: Session, user_id: int) -> User:
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(404, "user not found")
    return u


@router.get("/users/{user_id}/tokens", summary="API tokens of a user")
def list_user_tokens(user_id: int, db: Session = Depends(get_db)):
    _user(db, user_id)
    return [token_dict(t) for t in db.scalars(select(ApiToken).where(ApiToken.user_id == user_id).order_by(ApiToken.created_at.desc()))]


@router.post("/users/{user_id}/tokens", summary="Create an API token for a service account (shown only once)", status_code=201)
def create_user_token(user_id: int, body: TokenCreate, request: Request, db: Session = Depends(get_db), admin: CurrentUser = Depends(require_admin)):
    u = _user(db, user_id)
    if u.auth_source != "service":
        raise HTTPException(400, "tokens are created by administrators for service accounts only: users create their own")
    t, token = issue_token(db, u.id, body)
    audit(db, "token.create", user=admin, target=f"token:{t.id}", request=request, detail={"name": t.name, "for": u.username})
    return {**token_dict(t), "token": token}


@router.delete("/users/{user_id}/tokens/{token_id}", summary="Revoke an API token of a user")
def delete_user_token(user_id: int, token_id: int, request: Request, db: Session = Depends(get_db), admin: CurrentUser = Depends(require_admin)):
    u = _user(db, user_id)
    t = db.get(ApiToken, token_id)
    if not t or t.user_id != u.id:
        raise HTTPException(404, "token not found")
    db.delete(t)
    audit(db, "token.delete", user=admin, target=f"token:{token_id}", request=request, detail={"name": t.name, "for": u.username})
    return {"ok": True}


@router.patch("/users/{user_id}", summary="Update a user")
def update_user(user_id: int, body: UserUpdate, request: Request, db: Session = Depends(get_db), admin: CurrentUser = Depends(require_admin)):
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(404, "user not found")
    changes: dict[str, Any] = {}
    if u.auth_source == "service":
        if body.role not in (None, "user"):
            raise HTTPException(400, "a service account has the user role: give it rights on referentials instead")
        if body.password or body.must_change_password:
            raise HTTPException(400, "a service account has no password: it uses API tokens")
    losing_admin = (body.role == "user" and u.role == "admin") or (body.is_active is False and u.role == "admin")
    if losing_admin and _active_admins(db, exclude=u.id) == 0:
        raise HTTPException(400, "not possible: this account is the last active administrator")
    for f in ("display_name", "email", "role", "is_active", "must_change_password"):
        v = getattr(body, f)
        if v is not None and v != getattr(u, f):
            changes[f] = v
            setattr(u, f, v)
    if body.group_ids is not None:
        # Only local groups are edited here: groups synchronised from LDAP/OIDC are kept
        local = [g for g in _groups(db, body.group_ids) if g.source == "local"]
        u.groups = [g for g in u.groups if g.source != "local"] + local
        changes["groups"] = sorted(g.name for g in u.groups)
    if body.password and u.auth_source != "local":
        raise HTTPException(400, "the password of this account is managed by the identity provider")
    if body.password:
        _check_password(body.password, u.username)
        u.password_hash = hash_password(body.password)
        u.password_changed_at = now()
        u.must_change_password = True if body.must_change_password is None else body.must_change_password
        changes["password"] = "reset"
    if body.unlock:
        u.locked_until, u.failed_logins = None, 0
        changes["unlock"] = True
    if body.password or body.is_active is False:
        revoke_sessions(db, u.id)
    audit(db, "user.update", user=admin, target=f"user:{u.username}", request=request, detail=changes)
    return user_dict(u, db)


@router.delete("/users/{user_id}", summary="Delete a user")
def delete_user(user_id: int, request: Request, db: Session = Depends(get_db), admin: CurrentUser = Depends(require_admin)):
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(404, "user not found")
    if u.id == admin.id:
        raise HTTPException(400, "you cannot delete your own account")
    if u.role == "admin" and u.is_active and _active_admins(db, exclude=u.id) == 0:
        raise HTTPException(400, "not possible: this account is the last active administrator")
    db.delete(u)
    audit(db, "user.delete", user=admin, target=f"user:{u.username}", request=request)
    return {"ok": True}


@router.delete("/users/{user_id}/mfa", summary="Reset the second factor of a user (lost phone)")
def reset_user_mfa(user_id: int, request: Request, db: Session = Depends(get_db), admin: CurrentUser = Depends(require_admin)):
    from .. import mfa

    u = _user(db, user_id)
    mfa.reset(db, u)
    audit(db, "user.mfa_reset", user=admin, target=f"user:{u.username}", request=request)
    return user_dict(u, db)


@router.delete("/users/{user_id}/sessions", summary="Sign a user out of all sessions")
def revoke_user_sessions(user_id: int, request: Request, db: Session = Depends(get_db), admin: CurrentUser = Depends(require_admin)):
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(404, "user not found")
    revoke_sessions(db, u.id)
    audit(db, "user.sessions_revoke", user=admin, target=f"user:{u.username}", request=request)
    return {"ok": True}


# --------------------------------------------------------------------------- groups

@router.get("/groups", summary="Groups")
def list_groups(db: Session = Depends(get_db)):
    return [group_dict(g, db) for g in db.scalars(select(Group).order_by(Group.name))]


@router.post("/groups", summary="Create a group", status_code=201)
def create_group(body: GroupIn, request: Request, db: Session = Depends(get_db), admin: CurrentUser = Depends(require_admin)):
    name = body.name.strip()
    if db.scalar(select(Group.id).where(Group.name == name)):
        raise HTTPException(409, f"the group '{name}' already exists")
    g = Group(name=name, description=body.description)
    if body.member_ids:
        g.members = list(db.scalars(select(User).where(User.id.in_(body.member_ids))))
    db.add(g)
    db.flush()
    audit(db, "group.create", user=admin, target=f"group:{g.name}", request=request)
    return group_dict(g, db)


@router.patch("/groups/{group_id}", summary="Update a group")
def update_group(group_id: int, body: GroupIn, request: Request, db: Session = Depends(get_db), admin: CurrentUser = Depends(require_admin)):
    g = db.get(Group, group_id)
    if not g:
        raise HTTPException(404, "group not found")
    name = body.name.strip()
    if name != g.name and db.scalar(select(Group.id).where(Group.name == name)):
        raise HTTPException(409, f"the group '{name}' already exists")
    g.name, g.description = name, body.description
    if body.member_ids is not None:
        if g.source != "local":
            raise HTTPException(400, "the members of this group are synchronized from the identity provider")
        g.members = list(db.scalars(select(User).where(User.id.in_(body.member_ids))))
    audit(db, "group.update", user=admin, target=f"group:{g.name}", request=request,
          detail={"members": sorted(u.username for u in g.members)})
    return group_dict(g, db)


@router.delete("/groups/{group_id}", summary="Delete a group")
def delete_group(group_id: int, request: Request, db: Session = Depends(get_db), admin: CurrentUser = Depends(require_admin)):
    g = db.get(Group, group_id)
    if not g:
        raise HTTPException(404, "group not found")
    db.delete(g)
    audit(db, "group.delete", user=admin, target=f"group:{g.name}", request=request)
    return {"ok": True}


# --------------------------------------------------------------------------- grants

@router.get("/grants", summary="Referential access rights")
def list_grants(
    referential_id: str | None = None,
    user_id: int | None = None,
    group_id: int | None = None,
    db: Session = Depends(get_db),
    service: Service = Depends(get_service),
):
    q = select(Grant).order_by(Grant.referential_id, Grant.id)
    if referential_id:
        # The '*' grants also apply to every referential
        q = q.where(Grant.referential_id.in_([referential_id, ALL_REFERENTIALS]))
    if user_id:
        q = q.where(Grant.user_id == user_id)
    if group_id:
        q = q.where(Grant.group_id == group_id)
    return [grant_dict(g, service) for g in db.scalars(q).unique()]


@router.post("/grants", summary="Grant (or change) a right", status_code=201)
def upsert_grant(body: GrantIn, request: Request, db: Session = Depends(get_db), admin: CurrentUser = Depends(require_admin), service: Service = Depends(get_service)):
    if (body.user_id is None) == (body.group_id is None):
        raise HTTPException(400, "indiquez soit user_id, soit group_id")
    if body.referential_id != ALL_REFERENTIALS and body.referential_id not in service.refs:
        raise HTTPException(404, f"unknown referential: '{body.referential_id}'")
    if body.user_id and not db.get(User, body.user_id):
        raise HTTPException(404, "user not found")
    if body.group_id and not db.get(Group, body.group_id):
        raise HTTPException(404, "group not found")
    g = db.scalar(select(Grant).where(
        Grant.referential_id == body.referential_id,
        Grant.user_id.is_(None) if body.user_id is None else Grant.user_id == body.user_id,
        Grant.group_id.is_(None) if body.group_id is None else Grant.group_id == body.group_id,
    ))
    if g:
        g.level = body.level
    else:
        g = Grant(referential_id=body.referential_id, user_id=body.user_id, group_id=body.group_id, level=body.level, created_by=admin.username)
        db.add(g)
    db.flush()
    db.refresh(g)
    subject = f"user:{g.user.username}" if g.user else f"group:{g.group.name}"
    audit(db, "grant.set", user=admin, target=f"ref:{g.referential_id}", request=request, detail={"subject": subject, "level": g.level})
    return grant_dict(g, service)


@router.delete("/grants/{grant_id}", summary="Remove a right")
def delete_grant(grant_id: int, request: Request, db: Session = Depends(get_db), admin: CurrentUser = Depends(require_admin)):
    g = db.get(Grant, grant_id)
    if not g:
        raise HTTPException(404, "right not found")
    subject = f"user:{g.user.username}" if g.user else f"group:{g.group.name}"
    db.delete(g)
    audit(db, "grant.delete", user=admin, target=f"ref:{g.referential_id}", request=request, detail={"subject": subject, "level": g.level})
    return {"ok": True}


# --------------------------------------------------------------------------- audit

@router.get("/audit", summary="Audit log")
def audit_log(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    username: str | None = None,
    action: str | None = None,
    target: str | None = None,
    success: bool | None = None,
    db: Session = Depends(get_db),
):
    q = select(AuditLog)
    if username:
        q = q.where(AuditLog.username.startswith(username.strip().lower()))
    if action:
        q = q.where(AuditLog.action.startswith(action))
    if target:
        q = q.where(AuditLog.target.contains(target))
    if success is not None:
        q = q.where(AuditLog.success.is_(success))
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.scalars(q.order_by(AuditLog.at.desc(), AuditLog.id.desc()).limit(limit).offset(offset))
    return {
        "total": total,
        "rows": [
            {"id": a.id, "at": a.at, "username": a.username, "action": a.action, "target": a.target,
             "success": a.success, "ip": a.ip, "detail": a.detail}
            for a in rows
        ],
    }
