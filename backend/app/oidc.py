"""OpenID Connect (Keycloak, ...) login: authorization code flow with PKCE, state and nonce.

The transient state (state, nonce, PKCE verifier, return path) travels in an encrypted,
short-lived cookie, so no server-side storage is needed between the redirect and the callback.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from typing import Any
from urllib.parse import urlencode

import jwt

from .appsettings import OidcSettings
from .crypto import decrypt, encrypt
from .ldapauth import ExternalIdentity
from .network import client
from .settings import Settings

STATE_COOKIE = "refex_oidc"
STATE_TTL = 600
ALLOWED_ALGS = ["RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512"]
_cache: dict[str, tuple[float, dict[str, Any]]] = {}


class OidcError(Exception):
    pass


def _get_json(settings: Settings, cfg: OidcSettings, url: str, ttl: float = 600, force: bool = False) -> dict[str, Any]:
    hit = _cache.get(url)
    if hit and not force and time.time() - hit[0] < ttl:
        return hit[1]
    try:
        with client(settings, verify_tls=cfg.verify_tls, timeout=15) as c:
            r = c.get(url)
            r.raise_for_status()
            data = r.json()
    except Exception as e:  # noqa: BLE001
        raise OidcError(f"identity provider unreachable ({url}): {e}") from e
    _cache[url] = (time.time(), data)
    return data


def discovery_url(cfg: OidcSettings) -> str:
    url = cfg.discovery_url or cfg.issuer
    if not url:
        raise OidcError("issuer not configured")
    return url if url.endswith("openid-configuration") else f"{url}/.well-known/openid-configuration"


def discovery(settings: Settings, cfg: OidcSettings, force: bool = False) -> dict[str, Any]:
    doc = _get_json(settings, cfg, discovery_url(cfg), force=force)
    for k in ("issuer", "authorization_endpoint", "token_endpoint", "jwks_uri"):
        if not doc.get(k):
            raise OidcError(f"incomplete discovery document ({k} missing)")
    if cfg.issuer and doc["issuer"].rstrip("/") != cfg.issuer:
        raise OidcError(f"announced issuer '{doc['issuer']}' differs from the configured issuer '{cfg.issuer}'")
    return doc


def redirect_uri(cfg: OidcSettings, base_url: str) -> str:
    return f"{base_url.rstrip('/')}/api/auth/oidc/callback"


def safe_next(path: str | None) -> str:
    """Only allow local paths (no open redirect)."""
    if not path or not path.startswith("/") or path.startswith("//") or "\\" in path:
        return "/"
    return path


def start(settings: Settings, cfg: OidcSettings, base_url: str, next_path: str | None) -> tuple[str, str]:
    """Return (authorization URL, encrypted state cookie)."""
    doc = discovery(settings, cfg)
    state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    cookie = encrypt(json.dumps({"s": state, "n": nonce, "v": verifier, "next": safe_next(next_path), "exp": time.time() + STATE_TTL}))
    params = {
        "response_type": "code",
        "client_id": cfg.client_id,
        "redirect_uri": redirect_uri(cfg, base_url),
        "scope": cfg.scopes or "openid",
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    sep = "&" if "?" in doc["authorization_endpoint"] else "?"
    return f"{doc['authorization_endpoint']}{sep}{urlencode(params)}", cookie


def _claim(claims: dict[str, Any], path: str) -> Any:
    cur: Any = claims
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _verify_id_token(settings: Settings, cfg: OidcSettings, doc: dict[str, Any], token: str, nonce: str) -> dict[str, Any]:
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as e:
        raise OidcError(f"unreadable ID token: {e}") from e
    alg = header.get("alg")
    if alg not in ALLOWED_ALGS:
        raise OidcError(f"signature algorithm refused: {alg}")
    key = None
    for force in (False, True):  # refresh the key set once (key rotation)
        jwks = jwt.PyJWKSet.from_dict(_get_json(settings, cfg, doc["jwks_uri"], force=force))
        key = next((k for k in jwks.keys if k.key_id == header.get("kid")), None)
        if key:
            break
    if key is None:
        raise OidcError("unknown signing key")
    try:
        claims = jwt.decode(token, key.key, algorithms=[alg], audience=cfg.client_id, issuer=doc["issuer"], leeway=60,
                            options={"require": ["exp", "iat", "iss", "aud", "sub"]})
    except jwt.PyJWTError as e:
        raise OidcError(f"invalid ID token: {e}") from e
    if not hmac.compare_digest(str(claims.get("nonce", "")), nonce):
        raise OidcError("invalid nonce")
    return claims


def finish(settings: Settings, cfg: OidcSettings, base_url: str, code: str, state: str, cookie: str | None) -> tuple[ExternalIdentity, str]:
    if not cookie:
        raise OidcError("sign-in session expired, please start again")
    try:
        data = json.loads(decrypt(cookie))
    except Exception as e:  # noqa: BLE001
        raise OidcError("invalid sign-in state") from e
    if data.get("exp", 0) < time.time():
        raise OidcError("sign-in session expired, please start again")
    if not hmac.compare_digest(str(data.get("s")), state or ""):
        raise OidcError("invalid state parameter")
    doc = discovery(settings, cfg)
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri(cfg, base_url),
        "code_verifier": data["v"],
        "client_id": cfg.client_id,
    }
    try:
        with client(settings, verify_tls=cfg.verify_tls, timeout=15) as c:
            auth = (cfg.client_id, cfg.client_secret) if cfg.client_secret else None
            r = c.post(doc["token_endpoint"], data=form, auth=auth)
            if r.status_code >= 400:
                raise OidcError(f"code exchange refused ({r.status_code}): {r.text[:300]}")
            tokens = r.json()
            claims = _verify_id_token(settings, cfg, doc, tokens.get("id_token", ""), data["n"])
            if doc.get("userinfo_endpoint") and tokens.get("access_token"):
                u = c.get(doc["userinfo_endpoint"], headers={"Authorization": f"Bearer {tokens['access_token']}"})
                if u.status_code == 200:
                    info = u.json()
                    if info.get("sub") == claims["sub"]:
                        claims = {**info, **claims}  # id_token claims win, userinfo completes (groups...)
    except OidcError:
        raise
    except Exception as e:  # noqa: BLE001
        raise OidcError(f"cannot talk to the identity provider: {e}") from e
    return identity_from_claims(cfg, claims), data.get("next", "/")


def identity_from_claims(cfg: OidcSettings, claims: dict[str, Any]) -> ExternalIdentity:
    raw_groups = _claim(claims, cfg.groups_claim) if cfg.groups_claim else None
    if isinstance(raw_groups, str):
        raw_groups = [raw_groups]
    groups: list[tuple[str, str]] = []
    for g in raw_groups or []:
        g = str(g)
        name = g.rstrip("/").split("/")[-1] if cfg.strip_group_path else g
        groups.append((g, name or g))
    if cfg.group_regex:
        rx = re.compile(cfg.group_regex)
        groups = [g for g in groups if rx.search(g[1])]
    username = str(_claim(claims, cfg.username_claim) or claims["sub"]).strip().lower()
    return ExternalIdentity(
        source="oidc",
        external_id=str(claims["sub"]),
        username=username,
        display_name=_claim(claims, cfg.name_claim),
        email=_claim(claims, cfg.email_claim),
        groups=sorted(set(groups), key=lambda g: g[1].lower()),
        raw={k: v for k, v in claims.items() if k not in ("nonce", "at_hash", "c_hash")},
    )


def test(settings: Settings, cfg: OidcSettings, base_url: str) -> dict[str, Any]:
    steps = []
    try:
        doc = discovery(settings, cfg, force=True)
        steps.append({"ok": True, "step": "Discovery document", "detail": discovery_url(cfg)})
        steps.append({"ok": True, "step": "Issuer", "detail": doc["issuer"]})
        jwks = _get_json(settings, cfg, doc["jwks_uri"], force=True)
        steps.append({"ok": bool(jwks.get("keys")), "step": "Signing keys (JWKS)", "detail": f"{len(jwks.get('keys', []))} key(s)"})
        if not cfg.client_id:
            steps.append({"ok": False, "step": "Client", "detail": "client_id manquant"})
    except OidcError as e:
        steps.append({"ok": False, "step": "Identity provider", "detail": str(e)})
        doc = {}
    return {
        "ok": all(s["ok"] for s in steps),
        "steps": steps,
        "redirect_uri": redirect_uri(cfg, base_url),
        "endpoints": {k: doc.get(k) for k in ("authorization_endpoint", "token_endpoint", "userinfo_endpoint", "end_session_endpoint")},
    }


def clear_cache() -> None:
    _cache.clear()
