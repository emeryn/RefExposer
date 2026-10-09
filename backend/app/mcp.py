"""Administration MCP server (Model Context Protocol): an AI agent drives RefExposer through /api/mcp.

- Disabled by default; enabled by an administrator (Settings > MCP), who chooses the service accounts allowed.
- The agent authenticates with an API token of one of these service accounts (Authorization: Bearer rfx_...).
  Through the MCP server only, the account acts as an administrator: its token alone keeps its usual rights on the
  REST API, and disabling the server (or removing the account) withdraws the powers at once.
- Every tool calls the REST API in-process, as this account: same validations, same audit log (action by the
  service account, `via: mcp`). Secret values are never returned (write-only secret manager).
- Read-only mode: only the tools that read are offered, and the API refuses any change made through the server.
- Transport: Streamable HTTP, stateless, JSON responses (POST /api/mcp; no server-sent event stream).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from . import __version__, appsettings
from .db import get_db

router = APIRouter(tags=["mcp"])

INTERNAL_HEADER = "x-refexposer-mcp"
PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
MAX_RESULT_CHARS = 60_000
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
# Requests that only read although sent with POST (allowed in read-only mode)
READ_POSTS = [re.compile(p) for p in (r"^/api/sql$", r"^/api/admin/referentials/preview$", r"^/api/referentials/[^/]+/lookup$",
                                       r"^/api/admin/discovery/scan$")]
# Never through the generic tool: sign-in, second factor, personal tokens of the service account itself, the server
BLOCKED_PATHS = re.compile(r"^/api/(auth/(?!me$)|mcp)")


# --------------------------------------------------------------------------- elevation of the internal calls

def _key() -> bytes:
    from .crypto import derive

    return derive("mcp-internal-calls")


def _sign(token_id: int, ts: int) -> str:
    return hmac.new(_key(), f"{token_id}:{ts}".encode(), hashlib.sha256).hexdigest()


def internal_header(token_id: int) -> str:
    ts = int(time.time())
    return f"{ts}.{_sign(token_id, ts)}"


def is_read_request(method: str, path: str) -> bool:
    return method.upper() in SAFE_METHODS or (method.upper() == "POST" and any(p.match(path) for p in READ_POSTS))


def elevate(db: Session, request: Request, user, token_id: int):
    """Internal call of the MCP server (signed header): the allowed service account acts as an administrator."""
    value = request.headers.get(INTERNAL_HEADER, "")
    ts, _, sig = value.partition(".")
    if not (ts.isdigit() and abs(time.time() - int(ts)) < 300 and hmac.compare_digest(sig, _sign(token_id, int(ts)))):
        raise HTTPException(401, "invalid MCP call")
    cfg = appsettings.load(db, "mcp")
    if not (cfg.enabled and user.is_service and user.id in cfg.account_ids):
        raise HTTPException(403, "the MCP server is disabled, or this account is not allowed to use it")
    if cfg.read_only and not is_read_request(request.method, request.url.path):
        raise HTTPException(403, "the MCP server is in read-only mode")
    user.role = "admin"
    user.grants = {}
    user.via = "mcp"
    return user


# --------------------------------------------------------------------------- tools

@dataclass
class Tool:
    name: str
    description: str
    properties: dict[str, Any]
    required: tuple[str, ...]
    call: Callable[[dict[str, Any]], tuple[str, str, dict[str, Any] | None, Any]]  # args -> method, path, query, body
    write: bool = False
    shape: Callable[[Any, dict[str, Any]], Any] | None = None  # trims a large answer

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {"type": "object", "properties": self.properties, "required": list(self.required)},
            "annotations": {"readOnlyHint": not self.write, "destructiveHint": self.write and self.name.startswith("delete"),
                            "openWorldHint": self.name in ("preview_source", "discovery_scan", "refresh_referential")},
        }


def _p(value: str) -> str:
    return quote(str(value), safe="")


S = {"type": "string"}
I = {"type": "integer"}
B = {"type": "boolean"}
O = {"type": "object"}
REF_ID = {"type": "string", "description": "Referential id"}
SOURCE = {"type": "object", "description": "Source: {type: http|git|local, urls: [...], headers: {...}, basic_auth, repository, "
                                           "ref, path, token, username, extract}. Credentials: ${secret:<name>} references "
                                           "(see list_secrets), never literal values"}
CONFIG = {"type": "object", "description": "Referential definition, as in the YAML files: name, description, category, tags, "
                                           "source, format, options, transform (DuckDB SQL over {source}), key, "
                                           "search_columns, schedule (cron), validation, storage, confidential, enabled. "
                                           "Use preview_source first to choose the format and options"}


def _compact_list(data: Any, args: dict[str, Any]) -> Any:
    keys = ("id", "name", "category", "format", "source_type", "origin", "kind", "enabled", "health", "status",
            "row_count", "data_updated_at", "last_error", "schedule", "large")
    rows = [{k: r.get(k) for k in keys if k in r} for r in data]
    text = (args.get("filter") or "").lower()
    if text:
        rows = [r for r in rows if text in json.dumps(r, default=str).lower()]
    return rows


def _compact_preview(data: Any, args: dict[str, Any]) -> Any:
    if isinstance(data, dict) and isinstance(data.get("rows"), list):
        data = {**data, "rows": data["rows"][:10]}
    return data


def _compact_detail(data: Any, args: dict[str, Any]) -> Any:
    if isinstance(data, dict) and isinstance(data.get("current_run"), dict):
        run = data["current_run"]
        data = {**data, "current_run": {**run, "logs": (run.get("logs") or [])[-20:]}}
    return data


TOOLS: list[Tool] = [
    # ----- read
    Tool("get_overview", "Service status: version, referentials, running jobs, storage, scheduler.", {}, (),
         lambda a: ("GET", "/api/system", None, None)),
    Tool("list_referentials", "Every referential with its state (health, rows, last update, error). Optional text filter.",
         {"filter": S}, (), lambda a: ("GET", "/api/referentials", None, None), shape=_compact_list),
    Tool("get_referential", "Detail of a referential: columns, profile, sources, current run with its logs, configuration.",
         {"id": REF_ID}, ("id",), lambda a: ("GET", f"/api/referentials/{_p(a['id'])}", None, None), shape=_compact_detail),
    Tool("get_definition", "Editable definition of a referential (literal credentials masked; ${secret:...} references shown).",
         {"id": REF_ID}, ("id",), lambda a: ("GET", f"/api/admin/referentials/{_p(a['id'])}", None, None)),
    Tool("list_runs", "Last update runs of a referential, with their logs.", {"id": REF_ID, "limit": I}, ("id",),
         lambda a: ("GET", f"/api/referentials/{_p(a['id'])}/runs", {"limit": a.get("limit", 5)}, None)),
    Tool("query_rows", "Rows of a referential. `filters`: {column: value} or {column__op: value} with op among eq, ne, gt, "
         "gte, lt, lte, contains, startswith, endswith, in, regex, isnull. `q`: text search. `sort`: column or -column.",
         {"id": REF_ID, "filters": O, "q": S, "sort": S, "columns": S, "limit": I, "offset": I}, ("id",),
         lambda a: ("GET", f"/api/referentials/{_p(a['id'])}/rows",
                    {**(a.get("filters") or {}), **{k: a[k] for k in ("q", "sort", "columns", "offset") if a.get(k) not in (None, "")},
                     "limit": min(int(a.get("limit") or 20), 200)}, None)),
    Tool("lookup", "Exact lookup of a value on the key of a referential (IP for a MaxMind DB, value for a Bloom filter).",
         {"id": REF_ID, "value": S}, ("id", "value"),
         lambda a: ("GET", f"/api/referentials/{_p(a['id'])}/lookup/{_p(a['value'])}", None, None)),
    Tool("search", "Search a value in every referential.", {"q": S, "limit": I}, ("q",),
         lambda a: ("GET", "/api/search", {"q": a["q"], "limit": a.get("limit", 5)}, None)),
    Tool("run_sql", "Read-only DuckDB SQL over the referentials (tables named after the referentials, see sql_schema).",
         {"sql": S, "max_rows": I}, ("sql",),
         lambda a: ("POST", "/api/sql", None, {"sql": a["sql"], "max_rows": min(int(a.get("max_rows") or 100), 1000)})),
    Tool("sql_schema", "Tables and columns available to run_sql.", {}, (), lambda a: ("GET", "/api/sql/schema", None, None)),
    Tool("preview_source", "Analyse a source before creating a referential: download (sampled), detected format, "
         "JSON/XML record paths, columns and first rows. Same arguments as a definition.",
         {"source": SOURCE, "format": S, "options": O, "transform": S, "referential_id": S, "refresh": B}, ("source",),
         lambda a: ("POST", "/api/admin/referentials/preview", None,
                    {k: a[k] for k in ("source", "format", "options", "transform", "referential_id", "refresh") if a.get(k) is not None}),
         shape=_compact_preview),
    Tool("list_secrets", "Secrets of the secret manager: names, allowed hosts and referentials using them (never the values). "
         "Reference one in a source as ${secret:<name>}.", {}, (), lambda a: ("GET", "/api/admin/secrets", None, None)),
    Tool("discovery_scan", "Bulk discovery: list the data files of a Git repository or of an HTTP folder (directory listing) "
         "and propose one referential definition per file (format, options detected when analyse is true).",
         {"source": {"type": "object", "description": "{type: git, repository, ref, path (glob, e.g. output/*), token, username} "
                                                      "or {type: http, url (folder), pattern (glob), depth, headers, basic_auth}"},
          "analyse": B, "category": S, "tags": {"type": "array", "items": S}, "schedule": S}, ("source",),
         lambda a: ("POST", "/api/admin/discovery/scan", None, a)),
    Tool("export_configuration", "Export the referential definitions as YAML (with the rights of the groups and the names of "
         "the secrets they need, never their values), to set up another environment.",
         {"ids": {"type": "array", "items": S, "description": "Referential ids (all when empty)"}, "grants": B}, (),
         lambda a: ("GET", "/api/admin/config/export", {"ids": ",".join(a.get("ids") or []) or None, "grants": a.get("grants", True)}, None)),
    Tool("list_users", "Users and service accounts.", {}, (), lambda a: ("GET", "/api/admin/users", None, None)),
    Tool("list_groups", "Groups and their members.", {}, (), lambda a: ("GET", "/api/admin/groups", None, None)),
    Tool("list_grants", "Access rights on the referentials.", {"referential_id": S}, (),
         lambda a: ("GET", "/api/admin/grants", {k: a[k] for k in ("referential_id",) if a.get(k)}, None)),
    Tool("get_audit_log", "Audit log (most recent first).", {"limit": I, "action": S, "username": S}, (),
         lambda a: ("GET", "/api/admin/audit", {"limit": a.get("limit", 50), **{k: a[k] for k in ("action", "username") if a.get(k)}}, None)),
    Tool("list_tasks", "System tasks (backups, purges, checks) and their last runs.", {}, (),
         lambda a: ("GET", "/api/admin/tasks", None, None)),
    Tool("get_settings", "Administration settings (secrets masked).", {}, (), lambda a: ("GET", "/api/admin/settings", None, None)),
    Tool("api_endpoints", "Every endpoint of the REST API (method, path, summary), to use with api_request. Optional filter.",
         {"filter": S}, (), lambda a: ("ENDPOINTS", "", None, None)),
    Tool("api_request", "Any call to the REST API as an administrator (see api_endpoints). path starts with /api/. "
         "In read-only mode, only reading calls are accepted.",
         {"method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]}, "path": S, "query": O, "body": {}},
         ("method", "path"), lambda a: (a["method"].upper(), a["path"], a.get("query"), a.get("body"))),
    # ----- write
    Tool("create_referential", "Create a referential (definition stored in the database) and start its import.",
         {"id": {"type": "string", "description": "Identifier: lowercase letters, digits, '-' and '_'"}, "config": CONFIG,
          "pull": B, "grant_group_ids": {"type": "array", "items": I}}, ("id", "config"),
         lambda a: ("POST", "/api/admin/referentials", None, {"config": {**a["config"], "id": a["id"]}, "pull": a.get("pull", True),
                                                              "grant_group_ids": a.get("grant_group_ids") or []}), write=True),
    Tool("update_referential", "Replace the definition of a referential created from the interface (get_definition first: "
         "masked values sent back unchanged keep their value).",
         {"id": REF_ID, "config": CONFIG, "pull": B}, ("id", "config"),
         lambda a: ("PUT", f"/api/admin/referentials/{_p(a['id'])}", None, {"config": a["config"], "pull": a.get("pull", True)}),
         write=True),
    Tool("delete_referential", "Delete a referential created from the interface (and its data unless purge is false).",
         {"id": REF_ID, "purge": B}, ("id",),
         lambda a: ("DELETE", f"/api/admin/referentials/{_p(a['id'])}", {"purge": a.get("purge", True)}, None), write=True),
    Tool("refresh_referential", "Start the update of a referential (force: rebuild even when the source is unchanged).",
         {"id": REF_ID, "force": B}, ("id",),
         lambda a: ("POST", f"/api/referentials/{_p(a['id'])}/refresh", {"force": a.get("force", False)}, None), write=True),
    Tool("cancel_run", "Cancel the running update of a referential.", {"id": REF_ID}, ("id",),
         lambda a: ("DELETE", f"/api/referentials/{_p(a['id'])}/run", None, None), write=True),
    Tool("create_secret", "Store a credential in the secret manager (encrypted, never returned). `hosts`: hosts it may be "
         "sent to (e.g. api.example.com, *.example.com), any when empty.",
         {"name": S, "value": S, "description": S, "hosts": {"type": "array", "items": S}}, ("name", "value"),
         lambda a: ("POST", "/api/admin/secrets", None, {k: a[k] for k in ("name", "value", "description", "hosts") if a.get(k) is not None}),
         write=True),
    Tool("update_secret", "Change the value, description or allowed hosts of a secret.",
         {"name": S, "value": S, "description": S, "hosts": {"type": "array", "items": S}}, ("name",),
         lambda a: ("PATCH", f"/api/admin/secrets/{_p(a['name'])}", None,
                    {k: a[k] for k in ("value", "description", "hosts") if a.get(k) is not None}), write=True),
    Tool("delete_secret", "Delete a secret (refused while a referential uses it, unless force).", {"name": S, "force": B}, ("name",),
         lambda a: ("DELETE", f"/api/admin/secrets/{_p(a['name'])}", {"force": a.get("force", False)}, None), write=True),
    Tool("discovery_apply", "Create the referentials proposed by discovery_scan (the `config` of each selected candidate, "
         "possibly edited). Existing identifiers are skipped.",
         {"configs": {"type": "array", "items": O}, "pull": B, "grant_group_ids": {"type": "array", "items": I}}, ("configs",),
         lambda a: ("POST", "/api/admin/discovery/apply", None,
                    {"configs": a["configs"], "pull": a.get("pull", True), "grant_group_ids": a.get("grant_group_ids") or []}),
         write=True),
    Tool("import_configuration", "Import referential definitions (an export, or a file of config/). apply=false returns the plan "
         "(create, update, unchanged, skip, conflict, error; missing secrets and groups); apply=true applies it.",
         {"content": S, "apply": B, "on_existing": {"type": "string", "enum": ["skip", "update"]}, "ids": {"type": "array", "items": S},
          "grants": B, "create_groups": B, "pull": B}, ("content",),
         lambda a: ("POST", "/api/admin/config/import", None, a), write=True),
    Tool("grant_access", "Give (or change) a right on a referential ('*' = all) to a group or a user: read or manage.",
         {"referential_id": S, "group_id": I, "user_id": I, "level": {"type": "string", "enum": ["read", "manage"]}},
         ("referential_id",), lambda a: ("POST", "/api/admin/grants", None,
                                         {k: a[k] for k in ("referential_id", "group_id", "user_id", "level") if a.get(k) is not None}),
         write=True),
    Tool("reload_configuration", "Reload the YAML definitions and the sync folder.", {}, (),
         lambda a: ("POST", "/api/system/reload", None, None), write=True),
    Tool("run_task", "Run a system task now (see list_tasks).", {"task_id": S}, ("task_id",),
         lambda a: ("POST", f"/api/admin/tasks/{_p(a['task_id'])}/run", None, None), write=True),
]
TOOLS_BY_NAME = {t.name: t for t in TOOLS}

INSTRUCTIONS = (
    "RefExposer exposes referentials (CSV, JSON, Parquet... downloaded from HTTP, Git or files, normalized to Parquet). "
    "To add a source: preview_source, then create_referential with the detected format and options, then follow it with "
    "get_referential / list_runs. Credentials always go to the secret manager (create_secret) and are referenced as "
    "${secret:<name>} in headers, basic_auth or token. For many files at once (a Git repository, an HTTP folder): "
    "discovery_scan then discovery_apply. Everything else: api_endpoints and api_request."
)


# --------------------------------------------------------------------------- JSON-RPC

def _error(id_: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def _result(id_: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def _text(data: Any) -> str:
    text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, default=str, indent=1)
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + f"\n… truncated ({len(text):,} characters): narrow the request (filters, limit)"
    return text


class McpCaller:
    """Calls the REST API in-process, as the service account, elevated by a signed header."""

    def __init__(self, request: Request, token: str, token_id: int, read_only: bool):
        self.request, self.token, self.token_id, self.read_only = request, token, token_id, read_only

    def endpoints(self, text: str) -> list[dict[str, str]]:
        out = []
        for path, ops in self.request.app.openapi().get("paths", {}).items():
            for method, op in ops.items():
                if BLOCKED_PATHS.match(path) or (self.read_only and not is_read_request(method, path)):
                    continue
                item = {"method": method.upper(), "path": path, "summary": op.get("summary", "")}
                if not text or text.lower() in json.dumps(item).lower():
                    out.append(item)
        return out

    async def call(self, tool: Tool, args: dict[str, Any]) -> dict[str, Any]:
        method, path, query, body = tool.call(args)
        if method == "ENDPOINTS":
            return {"content": [{"type": "text", "text": _text(self.endpoints(args.get("filter") or ""))}], "isError": False}
        if not path.startswith("/api/") or BLOCKED_PATHS.match(path) or ".." in path:
            return {"content": [{"type": "text", "text": f"path not allowed: {path}"}], "isError": True}
        if self.read_only and not is_read_request(method, path):
            return {"content": [{"type": "text", "text": "the MCP server is in read-only mode"}], "isError": True}
        headers = {"Authorization": f"Bearer {self.token}", INTERNAL_HEADER: internal_header(self.token_id)}
        client_ip = self.request.headers.get("x-forwarded-for") or (self.request.client.host if self.request.client else None)
        if client_ip:
            headers["X-Forwarded-For"] = client_ip
        transport = httpx.ASGITransport(app=self.request.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://refexposer.internal", timeout=600) as c:
            r = await c.request(method, path, params={k: v for k, v in (query or {}).items() if v is not None},
                                json=body if body is not None and method != "GET" else None, headers=headers)
        try:
            data = r.json()
        except ValueError:
            data = r.text
        if r.status_code >= 400:
            detail = data.get("detail") if isinstance(data, dict) else data
            return {"content": [{"type": "text", "text": f"HTTP {r.status_code}: {_text(detail)}"}], "isError": True}
        if tool.shape:
            data = tool.shape(data, args)
        return {"content": [{"type": "text", "text": _text(data)}], "isError": False}


def mcp_account(request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    """The MCP server only answers when enabled, to an API token of an allowed service account."""
    from .auth import authenticate

    cfg = appsettings.load(db, "mcp")
    if not cfg.enabled:
        raise HTTPException(404, "the MCP server is disabled (Settings > MCP)")
    authz = request.headers.get("authorization", "")
    if not authz.lower().startswith("bearer "):
        raise HTTPException(401, "API token of a service account required (Authorization: Bearer rfx_...)",
                            headers={"WWW-Authenticate": "Bearer"})
    user = authenticate(request, db)
    if user is None or user.via != "token":
        raise HTTPException(401, "API token required", headers={"WWW-Authenticate": "Bearer"})
    if not user.is_service or user.id not in cfg.account_ids:
        raise HTTPException(403, "this account is not allowed to use the MCP server (service accounts chosen in Settings > MCP)")
    return {"user": user, "token": authz[7:].strip(), "token_id": user.credential_id, "read_only": cfg.read_only}


@router.get("/mcp", include_in_schema=False)
def mcp_get():
    # No server-initiated stream: every answer is the response of its POST
    return Response(status_code=405, headers={"Allow": "POST"})


@router.delete("/mcp", include_in_schema=False)
def mcp_delete():
    return Response(status_code=405, headers={"Allow": "POST"})


@router.post("/mcp", summary="MCP server (JSON-RPC 2.0, Streamable HTTP)")
async def mcp_post(request: Request, account: dict[str, Any] = Depends(mcp_account)):
    try:
        payload = json.loads(await request.body())
    except ValueError:
        return JSONResponse(_error(None, -32700, "parse error"), status_code=400)
    batch = isinstance(payload, list)
    messages = payload if batch else [payload]
    caller = McpCaller(request, account["token"], account["token_id"], account["read_only"])
    responses = []
    for msg in messages:
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or "method" not in msg:
            if isinstance(msg, dict) and ("result" in msg or "error" in msg):
                continue  # answer of the client to a request of the server: none is sent
            responses.append(_error(msg.get("id") if isinstance(msg, dict) else None, -32600, "invalid request"))
            continue
        if "id" not in msg:  # notification (notifications/initialized, cancelled...): nothing to answer
            continue
        responses.append(await _handle(caller, msg))
    if not responses:
        return Response(status_code=202)
    return JSONResponse(responses if batch else responses[0])


async def _handle(caller: McpCaller, msg: dict[str, Any]) -> dict[str, Any]:
    id_, method, params = msg["id"], msg["method"], msg.get("params") or {}
    if method == "initialize":
        asked = params.get("protocolVersion")
        return _result(id_, {
            "protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "refexposer-admin", "title": "RefExposer administration", "version": __version__},
            "instructions": INSTRUCTIONS + (" The server is in read-only mode." if caller.read_only else ""),
        })
    if method == "ping":
        return _result(id_, {})
    if method == "tools/list":
        return _result(id_, {"tools": [t.schema() for t in TOOLS if not (t.write and caller.read_only)]})
    if method == "tools/call":
        tool = TOOLS_BY_NAME.get(params.get("name", ""))
        if tool is None or (tool.write and caller.read_only):
            return _error(id_, -32602, f"unknown tool: {params.get('name')}")
        args = params.get("arguments") or {}
        missing = [r for r in tool.required if args.get(r) in (None, "")]
        if missing:
            return _result(id_, {"content": [{"type": "text", "text": f"missing argument(s): {', '.join(missing)}"}], "isError": True})
        try:
            return _result(id_, await caller.call(tool, args))
        except Exception as e:  # noqa: BLE001
            return _result(id_, {"content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}], "isError": True})
    if method in ("resources/list", "prompts/list"):
        return _result(id_, {method.split("/")[0]: []})
    return _error(id_, -32601, f"method not found: {method}")
