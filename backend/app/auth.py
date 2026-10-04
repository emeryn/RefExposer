"""Authentication (sessions, API tokens), authorization (roles, per-referential grants) and audit."""

from __future__ import annotations

import base64
import logging
import secrets
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Literal

from fastapi import Depends, HTTPException, Request
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from . import syslog
from .db import get_db
from .models import ALL_REFERENTIALS, LEVELS, ApiToken, AuditLog, AuthSession, Grant, Group, User, aware, now
from .security import burn_verification, hash_password, hash_token, new_session_token, verify_password
from .settings import Settings, get_settings

log = logging.getLogger(__name__)

SESSION_COOKIE = "refex_session"
CSRF_HEADER = "x-requested-with"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
Access = Literal["read", "manage"]


@dataclass
class CurrentUser:
    id: int
    username: str
    display_name: str | None
    email: str | None
    role: str
    must_change_password: bool
    groups: list[str] = field(default_factory=list)
    grants: dict[str, int] = field(default_factory=dict)  # referential id -> level
    via: Literal["session", "token"] = "session"
    credential_id: int | None = None
    status: str = "active"
    auth_source: str = "local"

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def is_service(self) -> bool:
        """Service account: API tokens only, never an interface session."""
        return self.auth_source == "service"

    @property
    def can_create_internal(self) -> bool:
        """Admins and advanced users may create internal referentials."""
        return self.role in ("admin", "advanced")

    @property
    def can_use_sql(self) -> bool:
        """The SQL console is reserved to admins and advanced users."""
        return self.role in ("admin", "advanced")

    def level(self, ref_id: str) -> int:
        if self.is_admin:
            return LEVELS["manage"]
        return max(self.grants.get(ref_id, 0), self.grants.get(ALL_REFERENTIALS, 0))

    def access(self, ref_id: str) -> Access | None:
        lvl = self.level(ref_id)
        return "manage" if lvl >= LEVELS["manage"] else "read" if lvl >= LEVELS["read"] else None

    def can_read(self, ref_id: str) -> bool:
        return self.level(ref_id) >= LEVELS["read"]

    def can_manage(self, ref_id: str) -> bool:
        return self.level(ref_id) >= LEVELS["manage"]

    def to_dict(self, ref_ids: list[str]) -> dict[str, Any]:
        return {
            "id": self.id,
            "username": self.username,
            "display_name": self.display_name,
            "email": self.email,
            "role": self.role,
            "is_admin": self.is_admin,
            "can_create_internal": self.can_create_internal,
            "can_use_sql": self.can_use_sql,
            "is_service": self.is_service,
            "must_change_password": self.must_change_password,
            "status": self.status,
            "auth_source": self.auth_source,
            "groups": self.groups,
            "auth": self.via,
            "permissions": {r: a for r in ref_ids if (a := self.access(r))},
        }


def client_ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()[:64]
    return request.client.host if request.client else None


def audit(
    db: Session,
    action: str,
    *,
    user: CurrentUser | User | None = None,
    username: str | None = None,
    target: str | None = None,
    success: bool = True,
    request: Request | None = None,
    detail: dict[str, Any] | None = None,
    commit: bool = True,
) -> None:
    ip = client_ip(request) if request else None
    name = getattr(user, "username", None) or username
    db.add(AuditLog(
        user_id=getattr(user, "id", None),
        username=name,
        action=action,
        target=target,
        success=success,
        ip=ip,
        detail=detail,
    ))
    syslog.audit_event(action, name, target, success, ip, detail)
    if commit:
        db.commit()


def load_grants(db: Session, user: User) -> dict[str, int]:
    group_ids = [g.id for g in user.groups]
    cond = Grant.user_id == user.id
    if group_ids:
        cond = or_(cond, Grant.group_id.in_(group_ids))
    out: dict[str, int] = {}
    for g in db.scalars(select(Grant).where(cond)):
        out[g.referential_id] = max(out.get(g.referential_id, 0), LEVELS.get(g.level, 0))
    return out


def to_current(db: Session, user: User, via: Literal["session", "token"], credential_id: int) -> CurrentUser:
    return CurrentUser(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        role=user.role,
        must_change_password=user.must_change_password,
        groups=sorted(g.name for g in user.groups),
        grants={} if user.role == "admin" else load_grants(db, user),
        via=via,
        credential_id=credential_id,
        status=user.status,
        auth_source=user.auth_source,
    )


# --------------------------------------------------------------------------- request authentication

def authenticate(request: Request, db: Session) -> CurrentUser | None:
    settings = get_settings()
    t = now()
    authz = request.headers.get("authorization", "")
    token = None
    if authz.lower().startswith("bearer "):
        token = authz[7:].strip()
    elif authz.lower().startswith("basic "):
        # Tools that only know HTTP Basic (wget, curl -u, URLs with credentials): the API token is the password
        try:
            _, _, token = base64.b64decode(authz[6:].strip(), validate=True).decode("utf-8").partition(":")
        except (ValueError, UnicodeDecodeError):
            token = ""
        if not token.startswith("rfx_"):
            raise HTTPException(401, "HTTP Basic: use an API token (rfx_...) as the password",
                                headers={"WWW-Authenticate": 'Basic realm="RefExposer API", charset="UTF-8"'})
    if token is not None:
        api_token = db.scalar(select(ApiToken).where(ApiToken.token_hash == hash_token(token)))
        if not api_token or (api_token.expires_at and aware(api_token.expires_at) < t):
            raise HTTPException(401, "invalid or expired API token", headers={"WWW-Authenticate": "Bearer"})
        user = api_token.user
        if not user.is_active or user.status != "active":
            raise HTTPException(401, "account disabled or not approved")
        if not api_token.last_used_at or t - aware(api_token.last_used_at) > timedelta(minutes=1):
            api_token.last_used_at = t
            db.commit()
        return to_current(db, user, "token", api_token.id)

    cookie = request.cookies.get(SESSION_COOKIE)
    if not cookie:
        return None
    sess = db.scalar(select(AuthSession).where(AuthSession.token_hash == hash_token(cookie)))
    if not sess or aware(sess.expires_at) < t or not sess.user.is_active or sess.user.status == "rejected":
        return None
    if sess.user.auth_source == "service":  # service accounts only use API tokens
        return None
    if sess.user.auth_source == "local" and not settings.local_login:  # local sign-in disabled since
        return None
    # Cookie-authenticated state-changing requests must carry a custom header that
    # cross-site forms cannot set (CSRF protection, in addition to SameSite=Lax).
    if request.method not in SAFE_METHODS and request.headers.get(CSRF_HEADER, "").lower() != "refexposer":
        raise HTTPException(403, "X-Requested-With: RefExposer header required")
    if t - aware(sess.last_seen_at) > timedelta(minutes=1):
        sess.last_seen_at = t
        sess.expires_at = t + timedelta(hours=settings.session_ttl_hours)  # sliding expiration
        db.commit()
    return to_current(db, sess.user, "session", sess.id)


def current_user_allow_pending(request: Request, db: Session = Depends(get_db)) -> CurrentUser:
    user = authenticate(request, db)
    if not user:
        # Tools (wget...) only send Basic credentials after a Basic challenge; the web interface never gets one,
        # so that browsers do not open their own sign-in dialog
        challenge = "Bearer" if request.headers.get(CSRF_HEADER) else 'Bearer, Basic realm="RefExposer API", charset="UTF-8"'
        raise HTTPException(401, "authentication required", headers={"WWW-Authenticate": challenge})
    request.state.user = user
    return user


def require_user(user: CurrentUser = Depends(current_user_allow_pending)) -> CurrentUser:
    if user.status == "pending":
        raise HTTPException(403, "access pending approval by an administrator")
    if user.must_change_password:
        raise HTTPException(403, "password change required before continuing")
    return user


def require_admin(user: CurrentUser = Depends(require_user)) -> CurrentUser:
    if not user.is_admin:
        raise HTTPException(403, "reserved to administrators")
    return user


def require_sql(user: CurrentUser = Depends(require_user)) -> CurrentUser:
    if not user.can_use_sql:
        raise HTTPException(403, "the SQL console is reserved to administrators and advanced users")
    return user


# --------------------------------------------------------------------------- login

def open_session(db: Session, request: Request, user: User) -> str:
    """Create a session for an authenticated user (commits)."""
    settings = get_settings()
    t = now()
    user.failed_logins = 0
    user.locked_until = None
    user.last_login_at = t
    token, digest = new_session_token()
    db.add(AuthSession(
        token_hash=digest,
        user_id=user.id,
        expires_at=t + timedelta(hours=settings.session_ttl_hours),
        last_seen_at=t,
        ip=client_ip(request),
        user_agent=(request.headers.get("user-agent") or "")[:255],
    ))
    db.commit()
    return token


def register_failure(db: Session, user: User) -> None:
    settings = get_settings()
    user.failed_logins += 1
    if user.failed_logins >= settings.login_max_attempts:
        user.locked_until = now() + timedelta(minutes=settings.login_lockout_minutes)
        user.failed_logins = 0


def _check_status(db: Session, request: Request, user: User, provider: str) -> None:
    if user.status == "rejected":
        audit(db, "auth.login", user=user, success=False, request=request, detail={"reason": "rejected", "provider": provider})
        raise HTTPException(403, "your access request has been refused by an administrator")
    if not user.is_active:
        audit(db, "auth.login", user=user, success=False, request=request, detail={"reason": "disabled", "provider": provider})
        raise HTTPException(401, "incorrect username or password")


def provision(db: Session, request: Request | None, ident, admin_group: str = "") -> User:
    """Create or update the account of an external identity (LDAP / OIDC) and synchronise its groups.

    New accounts are created in the `pending` state: an administrator must approve them."""
    t = now()
    user = db.scalar(select(User).where(User.auth_source == ident.source, User.external_id == ident.external_id))
    created = False
    if user is None:
        clash = db.scalar(select(User).where(User.username == ident.username[:64]))
        if clash:
            audit(db, "auth.login", username=ident.username[:64], success=False, request=request,
                  detail={"reason": "username already used", "provider": ident.source})
            raise HTTPException(409, f"the username '{ident.username}' is already used by another account: contact an administrator")
        user = User(
            username=ident.username[:64],
            auth_source=ident.source,
            external_id=ident.external_id,
            password_hash="!external",  # never valid for a local login
            role="user",
            status="pending",
        )
        db.add(user)
        created = True
    user.display_name = (ident.display_name or user.display_name or None) and str(ident.display_name or user.display_name)[:128]
    user.email = (ident.email or user.email or None) and str(ident.email or user.email)[:255]
    user.last_sync_at = t

    # Groups from the identity provider replace the previous provider groups; local groups are kept
    synced = []
    for ext_id, name in ident.groups:
        g = db.scalar(select(Group).where(Group.source == ident.source, Group.external_id == ext_id))
        if g is None:
            gname = name[:64]
            if db.scalar(select(Group.id).where(Group.name == gname)):
                gname = f"{name} ({ident.source})"[:64]
            g = Group(name=gname, source=ident.source, external_id=ext_id,
                      description=f"Synchronized from {'LDAP' if ident.source == 'ldap' else 'OpenID Connect'}")
            db.add(g)
            db.flush()
        synced.append(g)
    user.groups = [g for g in user.groups if g.source != ident.source] + synced
    if admin_group:
        user.role = "admin" if ident.in_group(admin_group) else "user"
    db.flush()
    if created:
        audit(db, "auth.access_request", user=user, request=request, commit=False,
              detail={"provider": ident.source, "groups": [g.name for g in synced]})
    return user


def login(db: Session, request: Request, username: str, password: str) -> tuple[User, str | None, str | None]:
    """Verify credentials (local account, else LDAP directory) and open a session: (user, session token, None).
    When a second factor is expected, no session is opened: (user, None, "verify" | "setup")."""
    from . import appsettings, ldapauth

    settings = get_settings()
    uname = username.strip().lower()
    user = db.scalar(select(User).where(User.username == uname))
    t = now()
    generic = HTTPException(401, "incorrect username or password")

    if user and user.locked_until and aware(user.locked_until) > t:
        burn_verification(password)
        audit(db, "auth.login", user=user, success=False, request=request, detail={"reason": "locked"})
        raise HTTPException(429, "account temporarily locked after too many failures, try again later")

    # Directory accounts (existing LDAP users, or unknown identifiers when LDAP is enabled)
    if user is None or user.auth_source == "ldap":
        ldap_cfg = appsettings.load(db, "ldap")
        if not ldap_cfg.enabled:
            burn_verification(password)
            audit(db, "auth.login", username=uname[:64], success=False, request=request,
                  detail={"reason": "unknown user" if user is None else "ldap disabled"})
            raise generic
        try:
            ident = ldapauth.authenticate(ldap_cfg, uname, password, appsettings.load(db, "proxy").ca_bundle)
        except ldapauth.LdapError as e:
            log.error("LDAP: %s", e)
            audit(db, "auth.login", username=uname[:64], success=False, request=request, detail={"reason": "ldap error", "error": str(e)[:300]})
            raise HTTPException(503, "directory unavailable, try again later or contact an administrator") from e
        if ident is None:
            if user:
                register_failure(db, user)
            audit(db, "auth.login", user=user, username=uname[:64], success=False, request=request,
                  detail={"reason": "bad password", "provider": "ldap"})
            raise generic
        user = provision(db, request, ident, ldap_cfg.admin_group)
        _check_status(db, request, user, "ldap")
        step = mfa_step(db, user)
        if step:  # password right: the session is opened once the second factor is checked
            audit(db, "auth.login", user=user, request=request, detail={"provider": "ldap", "status": user.status, "mfa": step})
            return user, None, step
        token = open_session(db, request, user)
        audit(db, "auth.login", user=user, request=request, detail={"provider": "ldap", "status": user.status})
        return user, token, None

    if user.auth_source == "service":
        burn_verification(password)
        audit(db, "auth.login", user=user, success=False, request=request, detail={"reason": "service account"})
        raise generic

    if user.auth_source == "local" and not settings.local_login:
        burn_verification(password)
        audit(db, "auth.login", user=user, success=False, request=request, detail={"reason": "local sign-in disabled"})
        raise HTTPException(403, "sign-in with a local account is disabled: use single sign-on")

    if user.auth_source != "local":
        burn_verification(password)
        audit(db, "auth.login", user=user, success=False, request=request, detail={"reason": f"{user.auth_source} account"})
        raise HTTPException(401, "this account signs in with OpenID Connect (dedicated button)")

    valid, needs_rehash = verify_password(user.password_hash, password)
    if not valid:
        register_failure(db, user)
        audit(db, "auth.login", user=user, success=False, request=request, detail={"reason": "bad password"})
        raise generic
    _check_status(db, request, user, "local")
    if needs_rehash:
        user.password_hash = hash_password(password)
    step = mfa_step(db, user)
    if step:
        audit(db, "auth.login", user=user, request=request, detail={"mfa": step})
        return user, None, step
    token = open_session(db, request, user)
    audit(db, "auth.login", user=user, request=request)
    return user, token, None


def mfa_step(db: Session, user: User) -> str | None:
    """Second factor expected after a right password: "verify" (enrolled), "setup" (required, not enrolled yet)."""
    from . import mfa

    if user.status != "active" or not mfa.applies_to(user) or not mfa.available(db):
        return None
    if mfa.has_factor(db, user):
        return "verify"
    return "setup" if mfa.required(db, user) else None


def revoke_sessions(db: Session, user_id: int, except_session: int | None = None) -> None:
    q = select(AuthSession).where(AuthSession.user_id == user_id)
    for s in db.scalars(q):
        if s.id != except_session:
            db.delete(s)


def purge_expired_sessions(db: Session) -> int:
    expired = list(db.scalars(select(AuthSession).where(AuthSession.expires_at < now())))
    for s in expired:
        db.delete(s)
    db.commit()
    return len(expired)


# --------------------------------------------------------------------------- bootstrap

def bootstrap_admin(db: Session, settings: Settings) -> None:
    """Create the administrator defined by REFEX_ADMIN_* when no active admin exists."""
    uname = settings.admin_username.strip().lower()
    existing = db.scalar(select(User).where(User.username == uname))
    if existing and settings.admin_reset_password and settings.admin_password:
        existing.password_hash = hash_password(settings.admin_password)
        existing.role, existing.is_active, existing.status = "admin", True, "active"
        existing.failed_logins, existing.locked_until = 0, None
        existing.must_change_password = settings.admin_must_change_password
        existing.password_changed_at = now()
        revoke_sessions(db, existing.id)
        audit(db, "user.bootstrap_reset", username="system", target=uname, commit=False)
        db.commit()
        log.warning("Password of administrator '%s' reset from REFEX_ADMIN_PASSWORD", uname)
        return

    has_admin = db.scalar(select(User.id).where(User.role == "admin", User.is_active.is_(True), User.status == "active").limit(1))
    if has_admin:
        return
    password = settings.admin_password
    generated = not password
    if generated:
        password = secrets.token_urlsafe(18)
    if existing:
        existing.role, existing.is_active, existing.status, existing.auth_source = "admin", True, "active", "local"
        existing.password_hash = hash_password(password)
        existing.must_change_password = settings.admin_must_change_password or generated
    else:
        db.add(User(
            username=uname,
            display_name="Administrator",
            email=settings.admin_email,
            role="admin",
            password_hash=hash_password(password),
            must_change_password=settings.admin_must_change_password or generated,
            password_changed_at=now(),
        ))
    audit(db, "user.bootstrap", username="system", target=uname, commit=False)
    db.commit()
    if generated:
        log.warning("=" * 70)
        log.warning("Initial administrator created: %s / %s", uname, password)
        log.warning("Set REFEX_ADMIN_PASSWORD to choose this password.")
        log.warning("=" * 70)
    else:
        log.info("Bootstrap administrator '%s' created", uname)
