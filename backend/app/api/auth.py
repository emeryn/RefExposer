from __future__ import annotations

from datetime import timedelta
from typing import Any

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import (
    SESSION_COOKIE,
    CurrentUser,
    register_failure,
    audit,
    current_user_allow_pending,
    login,
    require_user,
    open_session,
    provision,
    revoke_sessions,
    to_current,
)
from .. import appsettings, mfa, oidc
from ..db import get_db
from sqlalchemy import func

from ..models import ApiToken, AuthSession, User, aware, now
from ..security import hash_password, hash_token, new_api_token, password_problems, verify_password
from ..service import Service
from ..settings import get_settings
from .deps import get_service, public_base

router = APIRouter(prefix="/auth", tags=["authentication"])


class LoginRequest(BaseModel):
    username: str = Field(..., max_length=64)
    password: str = Field(..., max_length=1024)


class PasswordChange(BaseModel):
    current_password: str = Field(..., max_length=1024)
    new_password: str = Field(..., max_length=1024)


class TokenCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    expires_in_days: int | None = Field(None, ge=1, le=3650)


def _me(user: CurrentUser, service: Service, db: Session | None = None, request: Request | None = None) -> dict[str, Any]:
    data = user.to_dict(list(service.refs))
    if db is not None and user.via == "session":
        db_user = db.get(User, user.id)
        if db_user is not None:
            data["mfa"] = mfa.status(db, db_user, public_base(request) if request is not None else None)
    if user.is_admin and user.status == "active" and db is not None:
        data["pending_requests"] = db.scalar(select(func.count()).select_from(User).where(User.status == "pending")) or 0
    return data


def _set_session_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(settings.session_ttl_hours * 3600),
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        path="/api",
    )




@router.get("/providers", summary="Available sign-in methods (public)")
def providers(db: Session = Depends(get_db)):
    ldap_cfg = appsettings.load(db, "ldap")
    oidc_cfg = appsettings.load(db, "oidc")
    return {
        "local": get_settings().local_login,
        "ldap": {"enabled": ldap_cfg.enabled, "label": ldap_cfg.label},
        "oidc": {"enabled": oidc_cfg.enabled, "label": oidc_cfg.label, "login_url": "/api/auth/oidc/login"},
        "approval_required": True,
    }


@router.get("/oidc/login", summary="Start an OpenID Connect sign-in", include_in_schema=True)
def oidc_login(request: Request, next: str = "/", db: Session = Depends(get_db), service: Service = Depends(get_service)):
    cfg = appsettings.load(db, "oidc")
    if not cfg.enabled:
        raise HTTPException(404, "OpenID Connect sign-in is not enabled")
    try:
        url, cookie = oidc.start(service.settings, cfg, public_base(request), next)
    except oidc.OidcError as e:
        return RedirectResponse(f"/?auth_error={quote(str(e))}", status_code=302)
    resp = RedirectResponse(url, status_code=302)
    resp.set_cookie(oidc.STATE_COOKIE, cookie, max_age=oidc.STATE_TTL, httponly=True,
                    secure=get_settings().secure_cookies, samesite="lax", path="/api/auth/oidc")
    return resp


@router.get("/oidc/callback", summary="Identity provider callback")
def oidc_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
    db: Session = Depends(get_db),
    service: Service = Depends(get_service),
):
    def fail(message: str) -> RedirectResponse:
        audit(db, "auth.login", username=None, success=False, request=request, detail={"provider": "oidc", "error": message[:300]})
        r = RedirectResponse(f"/?auth_error={quote(message)}", status_code=302)
        r.delete_cookie(oidc.STATE_COOKIE, path="/api/auth/oidc")
        return r

    cfg = appsettings.load(db, "oidc")
    if not cfg.enabled:
        return fail("OpenID Connect sign-in is not enabled")
    if error:
        return fail(f"sign-in refused by the identity provider: {error_description or error}")
    if not code or not state:
        return fail("incomplete response from the identity provider")
    try:
        ident, next_path = oidc.finish(service.settings, cfg, public_base(request), code, state, request.cookies.get(oidc.STATE_COOKIE))
    except oidc.OidcError as e:
        return fail(str(e))
    try:
        user = provision(db, request, ident, cfg.admin_group)
        if user.status == "rejected":
            db.commit()
            return fail("your access request has been refused by an administrator")
        if not user.is_active:
            db.commit()
            return fail("account disabled")
        token = open_session(db, request, user)
    except HTTPException as e:
        return fail(str(e.detail))
    audit(db, "auth.login", user=user, request=request, detail={"provider": "oidc", "status": user.status})
    resp = RedirectResponse(oidc.safe_next(next_path), status_code=302)
    _set_session_cookie(resp, token)
    resp.delete_cookie(oidc.STATE_COOKIE, path="/api/auth/oidc")
    return resp


@router.post("/login", summary="Sign in (cookie session)")
def do_login(body: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db), service: Service = Depends(get_service)):
    if request.headers.get("x-requested-with", "").lower() != "refexposer":
        raise HTTPException(403, "X-Requested-With: RefExposer header required")
    user, token, step = login(db, request, body.username, body.password)
    if step:  # second factor: "verify" (code) or "setup" (required, to enrol now)
        return {"mfa": step, "mfa_token": mfa.challenge(user.id, step), "username": user.username, "methods": mfa.methods(db, user),
                "webauthn_available": mfa.webauthn_available(public_base(request))}
    return _session_response(db, user, token, response, service)


def _session_response(db: Session, user: User, token: str, response: Response, service: Service) -> dict[str, Any]:
    _set_session_cookie(response, token)
    sess = db.scalar(select(AuthSession).where(AuthSession.token_hash == hash_token(token)))
    return _me(to_current(db, user, "session", sess.id), service, db)


# --------------------------------------------------------------------------- second factor (TOTP)

class MfaChallenge(BaseModel):
    mfa_token: str = Field(..., max_length=4096)


class MfaCode(BaseModel):
    code: str = Field(..., max_length=64)


class MfaChallengeCode(MfaChallenge, MfaCode):
    pass


def _issuer(db: Session) -> str:
    return appsettings.load(db, "branding").title or "RefExposer"


def _enrolment(db: Session, user: User) -> dict[str, Any]:
    secret = mfa.start_enrolment(user)
    db.commit()
    uri = mfa.otpauth_uri(secret, user.username, _issuer(db))
    return {"secret": secret, "otpauth_uri": uri, "qr_svg": mfa.qr_svg(uri)}


def _challenge_user(db: Session, token: str, purpose: str) -> User:
    data = mfa.read_challenge(token)
    if not data or data.get("p") != purpose:
        raise HTTPException(401, "sign-in expired: enter your password again")
    user = db.get(User, data.get("u"))
    if not user or not user.is_active or user.status != "active":
        raise HTTPException(401, "sign-in expired: enter your password again")
    if user.locked_until and aware(user.locked_until) > now():
        raise HTTPException(429, "account temporarily locked after too many failures, try again later")
    return user


def _wrong_code(db: Session, request: Request, user: User) -> HTTPException:
    register_failure(db, user)
    db.commit()
    audit(db, "auth.mfa", user=user, success=False, request=request)
    return HTTPException(401, "wrong or already used code")


@router.post("/mfa/verify", summary="Sign in: second factor (TOTP or recovery code)")
def mfa_verify(body: MfaChallengeCode, request: Request, response: Response, db: Session = Depends(get_db), service: Service = Depends(get_service)):
    user = _challenge_user(db, body.mfa_token, "verify")
    codes_before = len(user.recovery_codes or [])
    if not mfa.verify(user, body.code):
        raise _wrong_code(db, request, user)
    recovery = len(user.recovery_codes or []) < codes_before
    token = open_session(db, request, user)
    audit(db, "auth.mfa", user=user, request=request, detail={"recovery_code": recovery} if recovery else None)
    return _session_response(db, user, token, response, service)


@router.post("/mfa/setup", summary="Sign in: enrol the second factor required for this account")
def mfa_setup(body: MfaChallenge, db: Session = Depends(get_db)):
    user = _challenge_user(db, body.mfa_token, "setup")
    return _enrolment(db, user)


@router.post("/mfa/setup/confirm", summary="Sign in: confirm the enrolment with a first code")
def mfa_setup_confirm(body: MfaChallengeCode, request: Request, response: Response, db: Session = Depends(get_db),
                      service: Service = Depends(get_service)):
    user = _challenge_user(db, body.mfa_token, "setup")
    codes = mfa.confirm_enrolment(db, user, body.code)
    if codes is None:
        raise _wrong_code(db, request, user)
    token = open_session(db, request, user)
    audit(db, "auth.mfa_enable", user=user, request=request, detail={"method": "totp"})
    return {**_session_response(db, user, token, response, service), "recovery_codes": codes}


# --------------------------------------------------------------------------- security keys / passkeys (WebAuthn)

class WebauthnAnswer(BaseModel):
    state: str = Field(..., max_length=4096)
    credential: dict[str, Any]


class WebauthnChallengeAnswer(MfaChallenge, WebauthnAnswer):
    name: str = Field("", max_length=128)


class WebauthnRegistration(WebauthnAnswer):
    name: str = Field("", max_length=128)


def _mfa_error(e: mfa.MfaError) -> HTTPException:
    return HTTPException(400, str(e))


@router.post("/mfa/webauthn/options", summary="Sign in: security key challenge")
def mfa_webauthn_options(body: MfaChallenge, request: Request, db: Session = Depends(get_db)):
    user = _challenge_user(db, body.mfa_token, "verify")
    try:
        return mfa.authentication_options(db, user, public_base(request))
    except mfa.MfaError as e:
        raise _mfa_error(e) from e


@router.post("/mfa/webauthn/verify", summary="Sign in: second factor with a security key / passkey")
def mfa_webauthn_verify(body: WebauthnChallengeAnswer, request: Request, response: Response, db: Session = Depends(get_db),
                        service: Service = Depends(get_service)):
    user = _challenge_user(db, body.mfa_token, "verify")
    try:
        mfa.authenticate(db, user, public_base(request), body.state, body.credential)
    except mfa.MfaError as e:
        register_failure(db, user)
        db.commit()
        audit(db, "auth.mfa", user=user, success=False, request=request, detail={"method": "webauthn", "error": str(e)[:200]})
        raise HTTPException(401, str(e)) from e
    token = open_session(db, request, user)
    audit(db, "auth.mfa", user=user, request=request, detail={"method": "webauthn"})
    return _session_response(db, user, token, response, service)


@router.post("/mfa/setup/webauthn/options", summary="Sign in: register the security key required for this account")
def mfa_setup_webauthn_options(body: MfaChallenge, request: Request, db: Session = Depends(get_db)):
    user = _challenge_user(db, body.mfa_token, "setup")
    return mfa.registration_options(db, user, public_base(request), _issuer(db))


@router.post("/mfa/setup/webauthn/confirm", summary="Sign in: save the security key and open the session")
def mfa_setup_webauthn_confirm(body: WebauthnChallengeAnswer, request: Request, response: Response, db: Session = Depends(get_db),
                               service: Service = Depends(get_service)):
    user = _challenge_user(db, body.mfa_token, "setup")
    try:
        codes = mfa.register(db, user, public_base(request), body.state, body.credential, body.name)
    except mfa.MfaError as e:
        raise _mfa_error(e) from e
    token = open_session(db, request, user)
    audit(db, "auth.mfa_enable", user=user, request=request, detail={"method": "webauthn"})
    return {**_session_response(db, user, token, response, service), "recovery_codes": codes}


@router.post("/mfa/webauthn/register/options", summary="Add a security key / passkey to my account")
def mfa_webauthn_register_options(request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(require_user)):
    db_user = _session_user(db, user)
    return mfa.registration_options(db, db_user, public_base(request), _issuer(db))


@router.post("/mfa/webauthn/register", summary="Save a security key / passkey (recovery codes shown once with the first factor)")
def mfa_webauthn_register(body: WebauthnRegistration, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(require_user)):
    db_user = _session_user(db, user)
    try:
        codes = mfa.register(db, db_user, public_base(request), body.state, body.credential, body.name)
    except mfa.MfaError as e:
        raise _mfa_error(e) from e
    audit(db, "auth.mfa_enable", user=user, request=request, detail={"method": "webauthn", "name": body.name})
    return {**mfa.status(db, db_user, public_base(request)), "recovery_codes": codes}


@router.delete("/mfa/webauthn/{credential_id}", summary="Remove one of my security keys")
def mfa_webauthn_delete(credential_id: int, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(require_user)):
    db_user = _session_user(db, user)
    keys = mfa.credentials(db, db_user)
    if mfa.required(db, db_user) and not db_user.totp_enabled and len(keys) <= 1:
        raise HTTPException(400, "a second factor is required for this account: add another one before removing this key")
    try:
        mfa.delete_credential(db, db_user, credential_id)
    except mfa.MfaError as e:
        raise HTTPException(404, str(e)) from e
    audit(db, "auth.mfa_disable", user=user, request=request, detail={"method": "webauthn", "credential": credential_id})
    return mfa.status(db, db_user, public_base(request))


def _session_user(db: Session, user: CurrentUser) -> User:
    if user.via != "session":
        raise HTTPException(403, "the second factor is managed from the web interface")
    db_user = db.get(User, user.id)
    if not mfa.applies_to(db_user) or not mfa.available(db):
        raise HTTPException(400, "no second factor for this account: it is provided by the identity provider")
    return db_user


@router.get("/mfa", summary="My second factor")
def mfa_status(request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(require_user)):
    return mfa.status(db, db.get(User, user.id), public_base(request))


@router.post("/mfa/enroll", summary="Start the enrolment of my second factor (QR code)")
def mfa_enroll(db: Session = Depends(get_db), user: CurrentUser = Depends(require_user)):
    db_user = _session_user(db, user)
    if db_user.totp_enabled:
        raise HTTPException(409, "the authenticator app is already enabled: disable it first")
    return _enrolment(db, db_user)


@router.post("/mfa/enable", summary="Enable my second factor with a first code (recovery codes shown once)")
def mfa_enable(body: MfaCode, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(require_user)):
    db_user = _session_user(db, user)
    codes = mfa.confirm_enrolment(db, db_user, body.code) if not db_user.totp_enabled else None
    if codes is None:
        raise HTTPException(400, "wrong code: check the time of your phone and type the current code")
    audit(db, "auth.mfa_enable", user=user, request=request, detail={"method": "totp"})
    return {**mfa.status(db, db_user), "recovery_codes": codes}


@router.post("/mfa/disable", summary="Disable my second factor")
def mfa_disable(body: MfaCode, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(require_user)):
    db_user = _session_user(db, user)
    if mfa.required(db, db_user) and not mfa.credentials(db, db_user):
        raise HTTPException(400, "a second factor is required for this account by the security policy: add a security key first")
    if not db_user.totp_enabled or not mfa.verify(db_user, body.code):
        raise HTTPException(400, "wrong or already used code")
    mfa.disable_totp(db, db_user)
    audit(db, "auth.mfa_disable", user=user, request=request, detail={"method": "totp"})
    return mfa.status(db, db_user)


@router.post("/mfa/recovery-codes", summary="New recovery codes (the previous ones stop working)")
def mfa_recovery_codes(body: MfaCode, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(require_user)):
    db_user = _session_user(db, user)
    if not mfa.has_factor(db, db_user) or not mfa.verify(db_user, body.code):
        raise HTTPException(400, "wrong or already used code")
    codes, digests = mfa.new_recovery_codes()
    db_user.recovery_codes = digests
    audit(db, "auth.mfa_recovery_codes", user=user, request=request)
    return {**mfa.status(db, db_user), "recovery_codes": codes}


@router.post("/logout", summary="Sign out")
def do_logout(request: Request, response: Response, db: Session = Depends(get_db), user: CurrentUser = Depends(current_user_allow_pending)):
    if user.via == "session" and user.credential_id:
        sess = db.get(AuthSession, user.credential_id)
        if sess:
            db.delete(sess)
    audit(db, "auth.logout", user=user, request=request)
    response.delete_cookie(SESSION_COOKIE, path="/api")
    return {"ok": True}


@router.get("/me", summary="Current user and rights")
def me(request: Request, user: CurrentUser = Depends(current_user_allow_pending), service: Service = Depends(get_service), db: Session = Depends(get_db)):
    return _me(user, service, db, request)


@router.get("/password-policy", summary="Password rules")
def password_policy():
    s = get_settings()
    return {"min_length": s.password_min_length, "min_classes": 3}


@router.post("/password", summary="Change my password")
def change_password(body: PasswordChange, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(current_user_allow_pending)):
    db_user = db.get(User, user.id)
    if db_user.auth_source == "service":
        raise HTTPException(400, "a service account has no password: it uses API tokens")
    if db_user.auth_source != "local":
        raise HTTPException(400, "the password of this account is managed by the identity provider")
    valid, _ = verify_password(db_user.password_hash, body.current_password)
    if not valid:
        audit(db, "auth.password_change", user=user, success=False, request=request)
        raise HTTPException(400, "incorrect current password")
    if body.new_password == body.current_password:
        raise HTTPException(400, "the new password must differ from the current one")
    problems = password_problems(body.new_password, user.username)
    if problems:
        raise HTTPException(400, "password too weak: " + ", ".join(problems))
    db_user.password_hash = hash_password(body.new_password)
    db_user.must_change_password = False
    db_user.password_changed_at = now()
    # Other sessions are closed, the current one is kept
    revoke_sessions(db, user.id, except_session=user.credential_id if user.via == "session" else None)
    audit(db, "auth.password_change", user=user, request=request)
    return {"ok": True}


# --------------------------------------------------------------------------- API tokens

def token_dict(t: ApiToken) -> dict[str, Any]:
    return {
        "id": t.id,
        "name": t.name,
        "prefix": t.prefix,
        "created_at": t.created_at,
        "expires_at": t.expires_at,
        "last_used_at": t.last_used_at,
        "expired": bool(t.expires_at and aware(t.expires_at) < now()),
    }


def issue_token(db: Session, user_id: int, body: TokenCreate) -> tuple[ApiToken, str]:
    """Create a token (the clear value is returned once, only its hash is stored)."""
    max_days = get_settings().api_token_max_days
    days = body.expires_in_days
    if max_days and (days is None or days > max_days):
        raise HTTPException(400, f"maximum validity: {max_days} days")
    token, prefix, digest = new_api_token()
    t = ApiToken(
        user_id=user_id,
        name=body.name.strip(),
        prefix=prefix,
        token_hash=digest,
        expires_at=now() + timedelta(days=days) if days else None,
    )
    db.add(t)
    db.flush()
    return t, token


def _no_service(user: CurrentUser) -> None:
    if user.is_service:
        raise HTTPException(403, "the tokens of a service account are managed by administrators")


@router.get("/tokens", summary="My API tokens")
def list_tokens(db: Session = Depends(get_db), user: CurrentUser = Depends(require_user)):
    tokens = db.scalars(select(ApiToken).where(ApiToken.user_id == user.id).order_by(ApiToken.created_at.desc()))
    return [token_dict(t) for t in tokens]


@router.post("/tokens", summary="Create an API token (shown only once)")
def create_token(body: TokenCreate, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(require_user)):
    _no_service(user)
    t, token = issue_token(db, user.id, body)
    audit(db, "token.create", user=user, target=f"token:{t.id}", request=request, detail={"name": t.name})
    return {**token_dict(t), "token": token}


@router.delete("/tokens/{token_id}", summary="Revoke one of my tokens")
def delete_token(token_id: int, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(require_user)):
    _no_service(user)
    t = db.get(ApiToken, token_id)
    if not t or t.user_id != user.id:
        raise HTTPException(404, "token not found")
    db.delete(t)
    audit(db, "token.delete", user=user, target=f"token:{token_id}", request=request, detail={"name": t.name})
    return {"ok": True}
