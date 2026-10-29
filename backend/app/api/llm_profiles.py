"""Saved connectors to an inference server: create, test, switch (llmprofiles.py).

Administrators only. Secrets go in and never come back out: the answers report `api_key_set` and the header
names, so the interface can show and edit a profile without ever holding its API key.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import appsettings, llmprofiles, network
from ..appsettings import check_llm_headers, check_llm_options
from ..auth import CurrentUser, audit, require_admin
from ..db import get_db
from ..models import LlmProfile, now
from ..service import Service
from ..storage import utcnow
from .deps import get_service

router = APIRouter(prefix="/admin/llm-profiles", tags=["assistant"], dependencies=[Depends(require_admin)])

MAX_PROFILES = 20


class ProfileIn(BaseModel):
    """A profile as the interface sends it. Absent secrets keep the stored ones."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=80)
    description: str = Field("", max_length=300)
    provider: Literal["ollama", "openai"] = "ollama"
    base_url: str = Field("", max_length=500)
    model: str = Field("", max_length=200)
    verify_tls: bool = True
    timeout: int = Field(120, ge=5, le=900)

    temperature: float = Field(0.2, ge=0, le=2)
    top_p: float = Field(0.95, ge=0, le=1)
    max_tokens: int = Field(2048, ge=64, le=32768)
    context_tokens: int = Field(0, ge=0, le=1_048_576)
    stop: list[str] = Field(default_factory=list)
    options: dict[str, Any] = Field(default_factory=dict)
    small_model: bool = False

    # Empty: keep the stored key. `api_key_clear` removes it.
    api_key: str = Field("", max_length=4096)
    api_key_clear: bool = False
    # A value equal to the mask keeps the stored one (the interface never receives the values)
    headers: dict[str, str] | None = None

    @field_validator("base_url")
    @classmethod
    def _url(cls, v: str) -> str:
        v = v.strip().rstrip("/")
        if v:
            try:
                network.validate_outbound(v, what="address of the inference server")
            except network.UnsafeUrl as e:
                raise ValueError(str(e)) from e
        return v

    @field_validator("stop")
    @classmethod
    def _stop(cls, v: list[str]) -> list[str]:
        out = [s for s in (str(x) for x in v) if s]
        if len(out) > 8:
            raise ValueError("at most 8 stop sequences")
        if any(len(s) > 200 for s in out):
            raise ValueError("a stop sequence is at most 200 characters")
        return out

    @field_validator("options")
    @classmethod
    def _options(cls, v: dict[str, Any]) -> dict[str, Any]:
        # The same rules as the settings: a key of the request itself is never an option
        return check_llm_options(v)

    @field_validator("headers")
    @classmethod
    def _headers(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        return None if v is None else check_llm_headers(v)


class ProfilePatch(ProfileIn):
    """Same fields, all optional."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, min_length=1, max_length=80)
    provider: Literal["ollama", "openai"] | None = None
    base_url: str | None = None
    model: str | None = None
    verify_tls: bool | None = None
    small_model: bool | None = None


def _get(db: Session, profile_id: int) -> LlmProfile:
    profile = db.get(LlmProfile, profile_id)
    if profile is None:
        raise HTTPException(404, "unknown profile")
    return profile


def _unique_name(db: Session, name: str, keep: int | None = None) -> str:
    name = name.strip()
    clash = db.scalars(select(LlmProfile).where(LlmProfile.name == name)).first()
    if clash is not None and clash.id != keep:
        raise HTTPException(409, f"a profile is already called '{name}'")
    return name


def _apply(profile: LlmProfile, data: dict[str, Any]) -> None:
    for field in ("name", "description", "provider", "base_url", "model", "verify_tls", "timeout",
                  "temperature", "top_p", "max_tokens", "context_tokens", "small_model"):
        if field in data:
            setattr(profile, field, data[field])
    if "stop" in data:
        profile.stop = list(data["stop"])
    if "options" in data:
        profile.options = dict(data["options"])


@router.get("", summary="Saved connectors, and which one is in use")
def list_profiles(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    rows = db.scalars(select(LlmProfile).order_by(LlmProfile.name)).all()
    return [llmprofiles.to_dict(p) for p in rows]


@router.post("", summary="Save a connector", status_code=201)
def create_profile(body: ProfileIn, request: Request, db: Session = Depends(get_db),
                   admin: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    total = len(list(db.scalars(select(LlmProfile.id))))
    if total >= MAX_PROFILES:
        raise HTTPException(409, f"at most {MAX_PROFILES} profiles: delete one first")
    data = body.model_dump()
    profile = LlmProfile(name=_unique_name(db, data["name"]), created_by=admin.username,
                         updated_by=admin.username)
    _apply(profile, data)
    # The first profile saved becomes the one in use: otherwise nothing would change and that surprises
    profile.active = total == 0
    db.add(profile)
    db.flush()
    llmprofiles.store_secrets(profile, data["api_key"], data["headers"])
    db.commit()
    appsettings.clear_cache()
    audit(db, "llm_profile.create", user=admin, target=f"llm-profile:{profile.id}", request=request,
          detail={"name": profile.name, "provider": profile.provider, "model": profile.model,
                  "small_model": profile.small_model, "api_key": bool(data["api_key"]), "active": profile.active})
    return llmprofiles.to_dict(profile)


@router.get("/{profile_id}", summary="One connector (never its secrets)")
def get_profile(profile_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    return llmprofiles.to_dict(_get(db, profile_id))


@router.patch("/{profile_id}", summary="Change a connector")
def update_profile(profile_id: int, body: ProfilePatch, request: Request, db: Session = Depends(get_db),
                   admin: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    profile = _get(db, profile_id)
    data = body.model_dump(exclude_unset=True)
    if "name" in data:
        data["name"] = _unique_name(db, data["name"], keep=profile.id)
    _apply(profile, data)
    if data.pop("api_key_clear", False):
        llmprofiles.clear_api_key(profile)
    elif "api_key" in data or "headers" in data:
        llmprofiles.store_secrets(profile, data.get("api_key"), data.get("headers"))
    profile.updated_by, profile.updated_at = admin.username, now()
    db.commit()
    appsettings.clear_cache()
    audit(db, "llm_profile.update", user=admin, target=f"llm-profile:{profile.id}", request=request,
          detail={"name": profile.name, "changed": sorted(k for k in data if k != "api_key")})
    return llmprofiles.to_dict(profile)


@router.post("/{profile_id}/duplicate", summary="Copy a connector to try a variant", status_code=201)
def duplicate_profile(profile_id: int, request: Request, db: Session = Depends(get_db),
                      admin: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    """The copy carries the secrets of the original: trying another model on the same gateway should not mean
    typing the key again."""
    source = _get(db, profile_id)
    if len(list(db.scalars(select(LlmProfile.id)))) >= MAX_PROFILES:
        raise HTTPException(409, f"at most {MAX_PROFILES} profiles: delete one first")
    name, n = f"{source.name} (copy)", 2
    while db.scalars(select(LlmProfile).where(LlmProfile.name == name)).first() is not None:
        name, n = f"{source.name} (copy {n})", n + 1
    copy = LlmProfile(name=name[:80], description=source.description, provider=source.provider,
                      base_url=source.base_url, model=source.model, verify_tls=source.verify_tls,
                      timeout=source.timeout, temperature=source.temperature, top_p=source.top_p,
                      max_tokens=source.max_tokens, context_tokens=source.context_tokens,
                      stop=list(source.stop or []), options=dict(source.options or {}),
                      small_model=source.small_model, created_by=admin.username, updated_by=admin.username)
    db.add(copy)
    db.flush()
    secrets = llmprofiles.secrets_of(source)
    llmprofiles.store_secrets(copy, secrets["api_key"], secrets["headers"])
    db.commit()
    audit(db, "llm_profile.duplicate", user=admin, target=f"llm-profile:{copy.id}", request=request,
          detail={"from": source.name, "name": copy.name})
    return llmprofiles.to_dict(copy)


@router.post("/{profile_id}/activate", summary="Use this connector")
def activate_profile(profile_id: int, request: Request, db: Session = Depends(get_db),
                     admin: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    """The assistant, the agent and the watchdogs switch at once: they read the active profile on every call."""
    profile = _get(db, profile_id)
    llmprofiles.activate(db, profile)
    db.commit()
    appsettings.clear_cache()
    audit(db, "llm_profile.activate", user=admin, target=f"llm-profile:{profile.id}", request=request,
          detail={"name": profile.name, "model": profile.model, "small_model": profile.small_model})
    return llmprofiles.to_dict(profile)


@router.delete("/{profile_id}", summary="Delete a connector")
def delete_profile(profile_id: int, request: Request, db: Session = Depends(get_db),
                   admin: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    profile = _get(db, profile_id)
    name, was_active = profile.name, profile.active
    db.delete(profile)
    db.flush()
    if was_active:
        # Never leave the assistant without a connector when another one is saved
        other = db.scalars(select(LlmProfile).order_by(LlmProfile.name).limit(1)).first()
        if other is not None:
            llmprofiles.activate(db, other)
    db.commit()
    appsettings.clear_cache()
    audit(db, "llm_profile.delete", user=admin, target=f"llm-profile:{profile_id}", request=request,
          detail={"name": name, "was_active": was_active})
    return {"deleted": profile_id, "was_active": was_active}


@router.post("/{profile_id}/test", summary="Test a connector and list the models it serves")
def test_profile(profile_id: int, request: Request, db: Session = Depends(get_db),
                 service: Service = Depends(get_service),
                 admin: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    """Tests the profile given, active or not: that is how you check a connector before switching to it."""
    from ..llm.provider import LLMError, build

    profile = _get(db, profile_id)
    if not profile.base_url:
        raise HTTPException(409, "this profile has no inference server")
    cfg = llmprofiles.as_settings(db, profile, appsettings.load(db, "assistant"))
    try:
        health = build(cfg, service.settings, appsettings.load(db, "proxy")).health()
    except LLMError as e:
        # LLMError carries the diagnostic an administrator needs (which server, which status), never a
        # traceback; the same bounded message is stored and returned
        reason = str(e)[:500]
        profile.last_test_at, profile.last_test_ok = utcnow(), False
        profile.last_test_error = reason
        db.commit()
        return {"ok": False, "error": reason, "models": [], "model_found": False}
    profile.last_test_at, profile.last_test_ok, profile.last_test_error = utcnow(), True, None
    db.commit()
    audit(db, "llm_profile.test", user=admin, target=f"llm-profile:{profile.id}", request=request,
          detail={"name": profile.name, "models": len(health.get("models") or [])})
    return health
