from __future__ import annotations

from typing import Any, Literal

import orjson
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from ..auth import CurrentUser
from ..config import ReferentialConfig
from ..service import Service


class ORJSONResponse(JSONResponse):
    media_type = "application/json"

    def render(self, content: Any) -> bytes:
        return orjson.dumps(content, option=orjson.OPT_NON_STR_KEYS | orjson.OPT_SERIALIZE_NUMPY, default=str)


def get_service(request: Request) -> Service:
    return request.app.state.service


def public_base(request: Request) -> str:
    """Base URL of the application: REFEX_PUBLIC_URL, or derived from the request (proxy headers included)."""
    configured = request.app.state.service.settings.public_url
    return configured or str(request.base_url).rstrip("/")


def get_ref(service: Service, ref_id: str, user: CurrentUser, level: Literal["read", "manage"] = "read") -> ReferentialConfig:
    """Return the referential if the user may access it. Unreadable referentials are reported
    as unknown (404) so that their existence is not disclosed."""
    ref = service.get(ref_id)
    if not ref or not user.can_read(ref_id):
        raise HTTPException(404, f"unknown referential: '{ref_id}'")
    if level == "manage" and not user.can_manage(ref_id):
        raise HTTPException(403, f"manage right required on '{ref_id}'")
    return ref


def get_ref_with_data(service: Service, ref_id: str, user: CurrentUser) -> ReferentialConfig:
    ref = get_ref(service, ref_id, user)
    if not service.has_data(ref_id):
        raise HTTPException(409, f"the referential '{ref_id}' has no data yet (start an update)")
    return ref


def readable_tables(service: Service, user: CurrentUser) -> frozenset[str]:
    return frozenset(r.table for r in service.refs.values() if user.can_read(r.id) and service.has_data(r.id) and not r.is_artifact)


def require_table(ref: ReferentialConfig) -> ReferentialConfig:
    if ref.is_bloom:
        raise HTTPException(400, "operation not available on a Bloom filter: use the value lookup (/lookup)")
    if ref.is_mmdb:
        raise HTTPException(400, "operation not available on a MaxMind DB: use the IP lookup (/lookup/{ip}) or download the raw file")
    return ref
