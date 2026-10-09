"""Administration settings: outgoing proxy and certificates, LDAP directory, OpenID Connect."""

from __future__ import annotations

import os
import time
from typing import Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

from .. import appsettings, ldapauth, logsetup, network, oidc, syslog
from ..auth import CurrentUser, audit, require_admin
from ..crypto import key_source
from ..db import get_db
from ..service import Service
from .deps import get_service, public_base

router = APIRouter(prefix="/admin/settings", tags=["settings"], dependencies=[Depends(require_admin)])

Section = Literal["proxy", "ldap", "oidc", "branding", "syslog", "mfa", "mcp", "smtp"]




def _validated(db: Session, section: str, data: dict[str, Any]) -> Any:
    current = appsettings.load(db, section)
    merged = appsettings.merge_secrets(section, {k: v for k, v in data.items() if not k.endswith("_set")}, current)
    try:
        return appsettings.SECTIONS[section].model_validate(merged)
    except ValidationError as e:
        msg = "; ".join(f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors())
        raise HTTPException(422, msg) from e


@router.get("", summary="All settings (secrets masked)")
def get_settings_all(request: Request, db: Session = Depends(get_db), service: Service = Depends(get_service)):
    oidc_cfg = appsettings.load(db, "oidc")
    return {
        "proxy": appsettings.public("proxy", appsettings.load(db, "proxy")),
        "ldap": appsettings.public("ldap", appsettings.load(db, "ldap")),
        "oidc": appsettings.public("oidc", oidc_cfg),
        "branding": appsettings.public("branding", appsettings.load(db, "branding")),
        "syslog": appsettings.public("syslog", appsettings.load(db, "syslog")),
        "syslog_status": syslog.status(),
        "mfa": appsettings.public("mfa", appsettings.load(db, "mfa")),
        "mcp": appsettings.public("mcp", appsettings.load(db, "mcp")),
        "smtp": appsettings.public("smtp", appsettings.load(db, "smtp")),
        "mcp_url": public_base(request).rstrip("/") + "/api/mcp",
        "local_login": service.settings.local_login,
        "log_level": logsetup.stdout_level_name(),
        "public_url": service.settings.public_url,
        "oidc_redirect_uri": oidc.redirect_uri(oidc_cfg, public_base(request)),
        "secret_key_source": key_source(),
        "environment_proxy": {k: os.environ.get(k) for k in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY") if os.environ.get(k)},
    }


@router.put("/{section}", summary="Save a settings section")
def put_section(section: Section, request: Request, data: dict[str, Any] = Body(...), db: Session = Depends(get_db), admin: CurrentUser = Depends(require_admin)):
    value = _validated(db, section, data)
    if section == "mcp":  # only service accounts drive the MCP server (API tokens, never a session)
        from ..models import User

        for uid in value.account_ids:
            u = db.get(User, uid)
            if not u or u.auth_source != "service":
                raise HTTPException(422, f"account {uid}: only service accounts may use the MCP server")
    appsettings.save(db, section, value, admin.username)
    if section == "proxy":
        network.set_proxy(value)
    if section in ("syslog", "proxy"):  # the collector may trust the company CAs of the network settings
        syslog.install(appsettings.load(db, "syslog"), appsettings.load(db, "proxy"))
    oidc.clear_cache()
    changed = {k: ("***" if k in appsettings.SECRETS[section] else v) for k, v in value.model_dump().items()}
    audit(db, f"settings.{section}", user=admin, target=f"settings:{section}", request=request, detail=changed)
    return appsettings.public(section, value)


class ProxyTest(BaseModel):
    config: dict[str, Any] = Field(default_factory=dict)
    url: str = "https://www.cisa.gov/"


@router.post("/proxy/test", summary="Test access to a URL with these network settings")
def test_proxy(body: ProxyTest, db: Session = Depends(get_db), service: Service = Depends(get_service)):
    cfg = _validated(db, "proxy", body.config) if body.config else appsettings.load(db, "proxy")
    t0 = time.perf_counter()
    try:
        with network.client(service.settings, cfg, timeout=20) as c:
            r = c.get(body.url)
        return {"ok": r.status_code < 400, "status": r.status_code, "elapsed_ms": round((time.perf_counter() - t0) * 1000),
                "route": network.describe(cfg), "error": None}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "status": None, "elapsed_ms": round((time.perf_counter() - t0) * 1000),
                "route": network.describe(cfg), "error": f"{type(e).__name__}: {e}"}


class LdapTest(BaseModel):
    config: dict[str, Any] = Field(default_factory=dict)
    username: str | None = None
    password: str | None = None


@router.post("/ldap/test", summary="Test the directory connection (and optionally a user)")
def test_ldap(body: LdapTest, db: Session = Depends(get_db)):
    cfg = _validated(db, "ldap", body.config) if body.config else appsettings.load(db, "ldap")
    if not cfg.server_url:
        raise HTTPException(400, "server URL missing")
    return ldapauth.test(cfg, body.username or None, body.password, appsettings.load(db, "proxy").ca_bundle)


@router.post("/oidc/test", summary="Test the OpenID Connect provider (discovery, keys)")
def test_oidc(request: Request, config: dict[str, Any] = Body(default_factory=dict, embed=True), db: Session = Depends(get_db), service: Service = Depends(get_service)):
    cfg = _validated(db, "oidc", config) if config else appsettings.load(db, "oidc")
    oidc.clear_cache()
    return oidc.test(service.settings, cfg, public_base(request))


class SmtpTest(BaseModel):
    config: dict[str, Any] = Field(default_factory=dict)
    to: str = Field(..., description="Recipient of the test e-mail")


@router.post("/smtp/test", summary="Send a test e-mail with these settings")
def test_smtp(body: SmtpTest, db: Session = Depends(get_db), admin: CurrentUser = Depends(require_admin)):
    from ..appsettings import EMAIL_RE
    from ..notify import send_mail

    cfg = _validated(db, "smtp", body.config) if body.config else appsettings.load(db, "smtp")
    if not EMAIL_RE.match(body.to.strip()):
        raise HTTPException(422, "e-mail address expected")
    t0 = time.perf_counter()
    try:
        send_mail(cfg, [body.to.strip()], "[RefExposer] Test e-mail",  # type: ignore[arg-type]
                  f"Test e-mail sent by {admin.username} from Administration › Settings › E-mail.\n\n"
                  "The notifications of the referentials (Administration › Notifications) use this server.",
                  appsettings.load(db, "proxy").ca_bundle)  # type: ignore[attr-defined]
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "elapsed_ms": round((time.perf_counter() - t0) * 1000), "error": f"{type(e).__name__}: {e}"}
    return {"ok": True, "elapsed_ms": round((time.perf_counter() - t0) * 1000), "error": None}


@router.post("/syslog/test", summary="Send a test message to the syslog collector")
def test_syslog(config: dict[str, Any] = Body(default_factory=dict, embed=True), db: Session = Depends(get_db)):
    cfg = _validated(db, "syslog", config) if config else appsettings.load(db, "syslog")
    if not cfg.host:
        raise HTTPException(400, "collector host missing")
    return syslog.test(cfg, appsettings.load(db, "proxy"))
