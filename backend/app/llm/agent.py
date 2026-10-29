"""The assistant and its agent mode.

Every tool the model calls goes through the REST API **in-process, as the account that is chatting**: the same
authentication, the same per-referential grants, the same validations and the same audit log as a click in the
interface. The model therefore never has rights of its own — a reader gets the 404 of a referential it may not
read, an administrator can create one. Nothing is elevated, unlike the administration MCP server.

Confidential referentials: when *Settings › Assistant* does not allow them, their identifiers are put in
``CurrentUser.denied`` for the whole tool call, which ``level()`` checks before the role. Every read goes
through it — rows, lookups, exports, downloads, search, and the tables registered in the SQL sandbox — so
neither their data nor their names can reach the model. The guard below refuses the call outright as well, and
filters what the administration endpoints list, so a confidential referential is simply not there.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

import httpx
from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from .. import appsettings
from ..appsettings import AssistantSettings
from ..auth import CurrentUser
from ..mcp import TOOLS_BY_NAME, USER_TOOLS, Tool, as_text
from .provider import LLMError, Provider, Reply, ToolCall

log = logging.getLogger(__name__)

AGENT_HEADER = "x-refexposer-agent"
PROMPTS = Path(__file__).parent / "prompts"
MAX_TOOL_RESULT_CHARS = 20_000
# Of what a tool answered, what is kept with the step so the exchange can be read back afterwards
STEP_RESULT_CHARS = 2_000


class Small:
    """What "the model is a small one" means, in one place.

    A 1B or 3B model does not fail at agent mode for one reason: it picks the wrong tool out of twenty, it
    calls three at once and loses track, it fills a 4k context with one result, and it writes the tool call
    as text instead of using the tool-call field. Each of those is handled here rather than hoped away by
    asking the model to be careful.
    """

    # The tools a small model can actually choose between. Reading only, and no artifact tools: handing back
    # a file or a query is a judgement call that goes wrong more often than it helps at this size.
    # `investigate` is offered even here: one call answers what a value touches across the catalogue, where a
    # small model would otherwise need a search and a lookup per referential
    TOOLS = ("list_referentials", "get_referential", "query_rows", "lookup", "search", "investigate")
    # Plus this one when the account may use SQL, because one query often replaces four tool calls
    SQL_TOOLS = ("run_sql", "sql_schema")
    # Tool calls honoured per step: the rest of a burst is dropped, with the first result to work from
    CALLS_PER_STEP = 1
    # Steps before it must conclude (the configured maximum still caps this)
    MAX_STEPS = 4
    # A result that does not fit the context is worse than a truncated one
    MAX_RESULT_CHARS = 2_000
    # Rows a tool may return: a small model summarises ten rows, not two hundred
    MAX_LIMIT = 10
    # Messages of the conversation kept in the prompt
    HISTORY = 6


def small_prompt() -> str:
    return (PROMPTS / "assistant-small.md").read_text(encoding="utf-8")
# Referential identifiers, as the configuration allows them
REF_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
# Keys naming a referential in an API answer or in the arguments of a tool
REF_KEYS = ("id", "ref_id", "referential_id", "referential", "remote_id")
# Arguments of a tool call that name the referential it acts on, and the ones that list several of them.
# Only these are checked against the scope of the exchange: anything deeper is data, not a referential.
REF_ARGUMENTS = ("id", "ref_id", "referential_id", "referential", "remote_id")
REF_LIST_ARGUMENTS = ("ids", "referentials")


# --------------------------------------------------------------------------- signed internal calls

def _key() -> bytes:
    from ..crypto import derive

    return derive("assistant-internal-calls")


def _sign(user_id: int, ts: int, scope: str = "") -> str:
    return hmac.new(_key(), f"{user_id}:{ts}:{scope}".encode(), hashlib.sha256).hexdigest()


def header(user_id: int, scope: Iterable[str] | None = None) -> str:
    """Value of the signed header carrying one tool call.

    `scope`: the only referentials the call may touch (a watchdog is pointed at a fixed set and must not
    wander off it). Empty or absent: the account keeps its usual reach.
    """
    ts = int(time.time())
    names = ",".join(sorted(scope)) if scope else ""
    return f"{ts}.{names}.{_sign(user_id, ts, names)}"


def restrict(db: Session, request: Request, user: CurrentUser) -> CurrentUser:
    """Tool call of the assistant: the account keeps its rights, minus the referentials the model may not see.

    The header is signed with a key derived from the instance secret, so a browser cannot forge it: without a
    valid signature the call is refused rather than silently treated as an ordinary one. The scope it carries
    is signed too, so narrowing a watchdog to a few referentials cannot be undone by the call itself.
    """
    parts = request.headers.get(AGENT_HEADER, "").split(".")
    ts, scope, sig = parts if len(parts) == 3 else ("", "", "")
    if not (ts.isdigit() and abs(time.time() - int(ts)) < 300
            and hmac.compare_digest(sig, _sign(user.id, int(ts), scope))):
        raise HTTPException(401, "invalid assistant call")
    cfg = appsettings.load(db, "assistant")
    if not allowed(cfg, user):
        raise HTTPException(403, "the assistant is disabled, or this account is not allowed to use it")
    user.via = "agent"
    if scope:
        user.allowed = frozenset(scope.split(","))
    if not cfg.allow_confidential:
        user.denied = confidential_ids(request.app.state.service)
    return user


def confidential_ids(service: Any) -> frozenset[str]:
    return frozenset(r.id for r in service.refs.values() if r.confidential)


def _named_referential(args: Any, matches: Callable[[str], bool]) -> str | None:
    """The first referential named anywhere in the arguments of a tool call for which `matches` is true."""
    if isinstance(args, str):
        return args if matches(args) else None
    if isinstance(args, dict):
        values: Iterable[Any] = args.values()
    elif isinstance(args, (list, tuple)):
        values = args
    else:
        return None
    for value in values:
        found = _named_referential(value, matches)
        if found:
            return found
    return None


def created_referentials(name: str, arguments: dict[str, Any]) -> list[str]:
    """The identifiers a tool call is about to create, for the tools that create a referential."""
    if name in ("create_referential", "create_referential_from_sql"):
        return [str(arguments.get("id") or "")]
    if name == "discovery_apply":
        return [str(c.get("id") or "") for c in (arguments.get("configs") or []) if isinstance(c, dict)]
    return []


@dataclass
class Caller:
    """What a tool call needs to reach the REST API in process, whoever started the exchange.

    Built from the request of a user who is chatting, or synthesised for a watchdog running on its own
    (watchdog.py), which authenticates with a session of its own instead of forwarding one.
    """

    app: Any
    service: Any
    headers: dict[str, str] = field(default_factory=dict)
    client_ip: str | None = None
    # Referentials the exchange is restricted to, carried signed with every call (None: no restriction)
    scope: frozenset[str] | None = None
    # Whether that scope is a filter the person chatting chose, rather than the fixed scope of a watchdog.
    # A filter is said out loud (they know what they picked) and a referential created for them joins it; the
    # scope of a watchdog stays invisible, so anything outside it simply does not exist for the model.
    filter_scope: bool = False

    @classmethod
    def of(cls, request: Request, scope: frozenset[str] | None = None) -> "Caller":
        headers = {name: value for name in ("authorization", "cookie")
                   if (value := request.headers.get(name))}
        ip = request.headers.get("x-forwarded-for") or (request.client.host if request.client else None)
        return cls(request.app, request.app.state.service, headers, ip, scope, filter_scope=scope is not None)

    def widen(self, *ref_ids: str) -> None:
        """A referential being created joins the scope of the exchange.

        A conversation narrowed to a few referentials must still be able to make a new one and read it back:
        without this the creation itself would be refused, the new identifier being outside the scope. It adds
        no right — the call still goes through the rights of the account — and it never happens for a watchdog,
        which cannot write at all.
        """
        ids = {r for r in ref_ids if isinstance(r, str) and REF_ID.match(r)}
        if self.scope is not None and self.filter_scope and ids:
            self.scope = self.scope | ids


def allowed(cfg: AssistantSettings, user: CurrentUser) -> bool:
    """Whether this account may use the assistant.

    Enabled, and either nothing restricts it, or the account is named directly or belongs to an allowed group.
    Administrators always may.
    """
    if not cfg.enabled:
        return False
    if user.is_admin:
        return True
    if not cfg.groups and not cfg.user_ids:
        return True
    return user.id in cfg.user_ids or bool(set(cfg.groups) & set(user.groups))


# --------------------------------------------------------------------------- confidential guard

class Guard:
    """Keeps every trace of a confidential referential out of the prompt.

    ``CurrentUser.denied`` already makes the API answer 404 for them; this refuses the call before it is made
    (a clear message instead of a puzzling error) and removes them from the answers of the administration
    endpoints, which list the definitions without going through the read grants.
    """

    def __init__(self, denied: frozenset[str]):
        self.denied = denied

    def check(self, args: Any) -> str | None:
        """The identifier of a denied referential named anywhere in the arguments, if any."""
        if not self.denied:
            return None
        return _named_referential(args, self.denied.__contains__)

    def path(self, path: str) -> str | None:
        if not self.denied:
            return None
        return next((seg for seg in path.split("/") if seg in self.denied), None)

    def filter(self, data: Any) -> Any:
        """Drop the entries describing a denied referential from an answer, at any depth."""
        if not self.denied:
            return data
        if isinstance(data, list):
            return [self.filter(item) for item in data if not self._names_denied(item)]
        if isinstance(data, dict):
            return {k: self.filter(v) for k, v in data.items()}
        return data

    def _names_denied(self, item: Any) -> bool:
        return isinstance(item, dict) and any(item.get(k) in self.denied for k in REF_KEYS)


# --------------------------------------------------------------------------- tools

@dataclass
class Step:
    """One tool call of the agent, kept with the message and shown in the interface.

    `result` holds the first characters of what the tool answered and `ms` how long it took: without them a
    finished exchange only says which tools were called, which is not enough to tell how an answer — or the
    verdict of a watchdog — was reached.
    """

    tool: str
    arguments: dict[str, Any]
    ok: bool
    summary: str
    result: str = ""
    ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"tool": self.tool, "arguments": self.arguments, "ok": self.ok, "summary": self.summary,
                "result": self.result, "ms": self.ms}


# Handed to the user instead of being described: a query to open in the SQL console, a file to download.
ARTIFACT_TOOLS: dict[str, Tool] = {}


def _artifact_tools() -> dict[str, Tool]:
    """Tools answered by the agent itself: they produce something for the user, not an API call."""
    if ARTIFACT_TOOLS:
        return ARTIFACT_TOOLS
    S = {"type": "string"}
    ARTIFACT_TOOLS["propose_sql"] = Tool(
        "propose_sql",
        "Hand a read-only SQL query to the user, opened in the SQL console with one click (it is not run). Use it "
        "when the answer is a query to keep, adjust or turn into a referential, rather than rows to read.",
        {"sql": S, "title": S, "explanation": S}, ("sql",), lambda a: ("ARTIFACT", "sql", None, a))
    ARTIFACT_TOOLS["offer_download"] = Tool(
        "offer_download",
        "Hand a file to the user as a download: give the whole content. Use it after run_sql or query_rows to "
        "deliver a result as JSON or CSV instead of pasting it in the answer.",
        {"filename": S, "format": {"type": "string", "enum": ["json", "csv", "txt", "sql", "yaml"]}, "content": S},
        ("filename", "format", "content"), lambda a: ("ARTIFACT", "file", None, a))
    return ARTIFACT_TOOLS


def tools_for(cfg: AssistantSettings, user: CurrentUser,
              hidden: frozenset[str] = frozenset()) -> list[Tool]:
    """The tools offered to the model, which are the ones this account may actually use.

    Reading tools come from the data MCP server (the same ones an agent gets with an API token). The tools that
    change something are the administration ones, offered only when writes are allowed; the API then refuses
    anything this account may not do, exactly as in the interface.

    `hidden` drops the tools of an experimental feature an administrator has not turned on, so the model is
    never offered a call whose endpoint would only answer that the feature is off (mcp.hidden_tools).

    A small model gets a short list: the choice is the hard part at that size, not the call.
    """
    if cfg.small_model:
        wanted = (set(Small.TOOLS) | (set(Small.SQL_TOOLS) if user.can_use_sql else set())) - hidden
        return [t for t in USER_TOOLS if t.name in wanted]
    tools = [t for t in USER_TOOLS
             if t.name not in hidden and (t.name not in ("run_sql", "sql_schema") or user.can_use_sql)]
    tools += list(_artifact_tools().values())
    if user.can_use_sql:
        tools.append(TOOLS_BY_NAME["preview_source"])
    if cfg.allow_writes:
        names = ["refresh_referential", "cancel_run"]
        if user.can_use_sql:  # creating a referential from a query: same right as the SQL console
            names.append("create_referential_from_sql")
        if user.is_admin:
            names += ["create_referential", "update_referential", "delete_referential", "get_definition",
                      "discovery_scan", "discovery_apply", "grant_access", "list_groups", "list_users",
                      "list_secrets", "get_audit_log", "list_tasks", "run_task", "get_overview",
                      "reload_configuration", "export_configuration"]
        for name in names:
            tool = TOOLS_BY_NAME.get(name) or _extra_tools().get(name)
            if tool and tool.name not in {t.name for t in tools}:
                tools.append(tool)
    return tools


EXTRA_TOOLS: dict[str, Tool] = {}


def _extra_tools() -> dict[str, Tool]:
    """Tools of the assistant that the MCP servers do not declare."""
    if EXTRA_TOOLS:
        return EXTRA_TOOLS
    S = {"type": "string"}
    EXTRA_TOOLS["create_referential_from_sql"] = Tool(
        "create_referential_from_sql",
        "Keep a read-only SQL query as a referential of its own: the query is stored, not its rows, and the "
        "referential is rebuilt whenever one of the referentials it reads publishes a new version. Use it for a "
        "unified referential (a join, a union, a selection) instead of copying rows.",
        {"sql": S, "id": {"type": "string", "description": "Identifier: lowercase letters, digits, '-' and '_'"},
         "name": S, "description": S, "category": S, "key": S}, ("sql", "id", "name"),
        lambda a: ("POST", "/api/sql/referential", None,
                   {k: a[k] for k in ("sql", "id", "name", "description", "category", "key") if a.get(k)}),
        write=True)
    return EXTRA_TOOLS


def schemas(tools: list[Tool]) -> list[dict[str, Any]]:
    """Tool definitions in the shape both provider families accept."""
    return [{"name": t.name, "description": t.description,
             "parameters": {"type": "object", "properties": t.properties, "required": list(t.required)}}
            for t in tools]


# --------------------------------------------------------------------------- the agent

@dataclass
class Turn:
    """What one exchange produced, kept with the assistant message."""

    content: str = ""
    steps: list[Step] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class Agent:
    """Runs one exchange: the model answers, calls tools, reads their results and answers again."""

    def __init__(self, caller: Caller, cfg: AssistantSettings, provider: Provider, user: CurrentUser,
                 agent_mode: bool, system_prompt: str | None = None,
                 hidden_tools: frozenset[str] = frozenset()):
        self.caller = caller
        self.cfg = cfg
        self.provider = provider
        self.user = user
        self.agent_mode = agent_mode and cfg.agent_enabled
        self.guard = Guard(frozenset() if cfg.allow_confidential else confidential_ids(caller.service))
        self.tools = tools_for(cfg, user, hidden_tools) if self.agent_mode else []
        self.by_name = {t.name: t for t in self.tools}
        # Replaces the prompt of the settings (a watchdog has its own job and its own answer format)
        self.base_prompt = system_prompt

    # ------------------------------------------------------------------ prompt
    @property
    def small(self) -> bool:
        """The active profile says the model is a small one (1B, 3B)."""
        return bool(self.cfg.small_model) and self.base_prompt is None

    @property
    def max_steps(self) -> int:
        return min(self.cfg.max_steps, Small.MAX_STEPS) if self.small else self.cfg.max_steps

    def system_prompt(self) -> str:
        base = self.base_prompt or self.cfg.system_prompt
        if not base and self.small:
            base = small_prompt()
        prompt = (base or default_prompt()).strip()
        if self.small:
            # Every extra sentence costs context and dilutes the instructions that matter
            facts = ["You only see the referentials the account you answer may read."]
            if self.caller.scope:
                facts.append("Only these referentials exist: " + ", ".join(sorted(self.caller.scope)) + ".")
            return prompt + "\n\n" + "\n".join(f"- {f}" for f in facts)
        facts = [
            f"The account you are answering is '{self.user.username}', role '{self.user.role}'.",
            "Every tool call runs with the rights of that account: what it may not read does not exist for you.",
        ]
        if self.caller.scope:
            facts.append("You are restricted to these referentials: " + ", ".join(sorted(self.caller.scope))
                         + ". Nothing else exists for you, and asking for another one returns nothing.")
            if self.agent_mode and self.cfg.allow_writes:
                facts.append("That restriction is a filter the user chose, not a ban on working: a referential "
                             "you create for them joins the list and can be read afterwards.")
        if self.guard.denied:
            facts.append("Confidential referentials are out of scope for this assistant: they are not listed and "
                         "cannot be queried. Say so plainly if the question needs one.")
        if self.agent_mode and self.cfg.allow_writes:
            facts.append("You may change things. Say what you are about to change and why before doing it, and "
                         "never delete anything the user did not ask for by name.")
        elif self.agent_mode:
            facts.append("You may only read. Explain what the user has to do themselves for anything else.")
        return prompt + "\n\n" + "\n".join(f"- {f}" for f in facts)

    def messages(self, history: list[dict[str, str]]) -> list[dict[str, Any]]:
        keep = min(self.cfg.history_messages, Small.HISTORY) if self.small else self.cfg.history_messages
        kept = history[-keep:]
        return [{"role": "system", "content": self.system_prompt()}, *kept]

    # ------------------------------------------------------------------ run
    def run(self, history: list[dict[str, str]]) -> Iterator[dict[str, Any]]:
        """Yield events: {type: step|token|done|error}. The caller turns them into server-sent events."""
        turn = Turn()
        try:
            if not self.agent_mode:
                yield from self._answer(self.messages(history), turn)
            else:
                yield from self._loop(history, turn)
        except LLMError as e:
            turn.error = str(e)
            yield {"type": "error", "message": str(e)}
        yield {"type": "done", "content": turn.content, "steps": [s.to_dict() for s in turn.steps],
               "artifacts": turn.artifacts, "usage": turn.usage, "error": turn.error}

    def _answer(self, messages: list[dict[str, Any]], turn: Turn) -> Iterator[dict[str, Any]]:
        """Final answer, streamed token by token."""
        for piece in self.provider.stream(messages):
            if isinstance(piece, Reply):
                turn.content = piece.content
                turn.usage = piece.usage or turn.usage
                return
            yield {"type": "token", "text": piece}

    def _loop(self, history: list[dict[str, str]], turn: Turn) -> Iterator[dict[str, Any]]:
        messages = self.messages(history)
        definitions = schemas(self.tools)
        for _ in range(self.max_steps):
            reply = self.provider.chat(messages, definitions)
            turn.usage = reply.usage or turn.usage
            if not reply.tool_calls and self.small and reply.content:
                # A small model often writes the call it wanted instead of using the tool-call field
                written = text_tool_call(reply.content, self.by_name)
                if written is not None:
                    reply.tool_calls = [written]
            if not reply.tool_calls:
                # The model answered: stream that answer so the user sees it arrive
                if reply.content:
                    turn.content = reply.content
                    yield {"type": "token", "text": reply.content}
                    return
                break
            if self.small:
                # One at a time: a burst of calls fills the context and the model loses the thread
                reply.tool_calls = reply.tool_calls[:Small.CALLS_PER_STEP]
            # The shape of a tool exchange belongs to the driver: Ollama and the OpenAI API disagree on it
            messages.append(self.provider.assistant_turn(reply))
            for call in reply.tool_calls:
                started = time.perf_counter()
                step, result = self._execute(call, turn)
                step.ms = int((time.perf_counter() - started) * 1000)
                step.result = (result or "")[:STEP_RESULT_CHARS]
                turn.steps.append(step)
                yield {"type": "step", **step.to_dict()}
                messages.append(self.provider.tool_result(call, result))
        else:
            turn.error = f"stopped after {self.max_steps} steps"
        # Out of steps, or an answer with no content: ask for a plain conclusion from what was gathered
        messages.append({"role": "user", "content": "Answer now, from what you have gathered. Do not call any tool."})
        yield from self._answer(messages, turn)

    # ------------------------------------------------------------------ tools
    def _execute(self, call: ToolCall, turn: Turn) -> tuple[Step, str]:
        tool = self.by_name.get(call.name)
        if tool is None:
            return Step(call.name, call.arguments, False, "unknown tool"), f"unknown tool: {call.name}"
        if self.guard.check(call.arguments):
            # The identifier is left out on purpose: nothing of a confidential referential goes to the model
            return (Step(call.name, call.arguments, False, "confidential referential"),
                    "that referential is confidential: this assistant cannot read it, and no other way to "
                    "reach it will work. Tell the user it is out of scope.")
        outside = self._outside(call.arguments)
        if outside is not None:
            # The API answers 404 on its own (the scope is in `CurrentUser.allowed`), but a model reads "not
            # found" as an invitation to try another way. Saying it plainly costs one step instead of three.
            return (Step(call.name, call.arguments, False, f"outside the chosen referentials: {outside}"),
                    f"'{outside}' is not one of the referentials this exchange is limited to "
                    f"({', '.join(sorted(self.caller.scope or ()))}). Tell the user it is out of scope.")
        missing = [name for name in tool.required if call.arguments.get(name) in (None, "")]
        if missing:
            return (Step(call.name, call.arguments, False, f"missing argument(s): {', '.join(missing)}"),
                    f"missing argument(s): {', '.join(missing)}")
        if self.small:
            call.arguments = small_arguments(call.arguments)
        if tool.write:
            self.caller.widen(*created_referentials(call.name, call.arguments))
        try:
            method, path, query, body = tool.call(call.arguments)
        except Exception as e:  # noqa: BLE001 - arguments invented by the model
            return Step(call.name, call.arguments, False, f"invalid arguments: {e}"), f"invalid arguments: {e}"
        if method == "ARTIFACT":
            artifact = self._artifact(path, call.arguments)
            turn.artifacts.append(artifact)
            summary = artifact.get("title") or artifact.get("filename") or path
            return Step(call.name, call.arguments, True, summary), "handed to the user"
        return self._api(call, tool, method, path, query, body)

    def _outside(self, args: Any) -> str | None:
        """A referential the call points at that exists but is outside the filter of this exchange.

        Only for a filter the user chose: the scope of a watchdog is not announced, so a referential outside it
        answers "not found" like one that does not exist.

        Only the arguments that name a referential are read, and only at the top level: a filter such as
        `{"filters": {"id": "words"}}` is a column of the rows, not a referential, and refusing the call
        because a cell value happens to match an identifier would be a bug of its own.
        """
        scope = self.caller.scope
        if scope is None or not self.caller.filter_scope or not isinstance(args, dict):
            return None
        named = [args[key] for key in REF_ARGUMENTS if isinstance(args.get(key), str)]
        for key in REF_LIST_ARGUMENTS:
            value = args.get(key)
            if isinstance(value, (list, tuple)):
                named += [item for item in value if isinstance(item, str)]
        known = self.caller.service.refs
        return next((ref for ref in named if ref in known and ref not in scope), None)

    def _artifact(self, kind: str, args: dict[str, Any]) -> dict[str, Any]:
        if kind == "sql":
            sql = str(args.get("sql") or "")[:20_000]
            return {"kind": "sql", "sql": sql, "title": str(args.get("title") or "Query")[:200],
                    "explanation": str(args.get("explanation") or "")[:2000]}
        name = re.sub(r"[^A-Za-z0-9._-]", "-", str(args.get("filename") or "result"))[:100] or "result"
        return {"kind": "file", "filename": name, "format": str(args.get("format") or "txt"),
                "content": str(args.get("content") or "")[:2_000_000]}

    def _api(self, call: ToolCall, tool: Tool, method: str, path: str, query: dict[str, Any] | None,
             body: Any) -> tuple[Step, str]:
        if not path.startswith("/api/") or ".." in path:
            return Step(call.name, call.arguments, False, "path not allowed"), f"path not allowed: {path}"
        if self.guard.path(path):
            return (Step(call.name, call.arguments, False, "confidential referential"),
                    "that referential is confidential: this assistant cannot read it.")
        if tool.write and not self.cfg.allow_writes:
            return (Step(call.name, call.arguments, False, "read-only assistant"),
                    "this assistant may only read: tell the user to do it from the interface")
        status, data = self._call_api(method, path, query, body)
        if status >= 400:
            detail = data.get("detail") if isinstance(data, dict) else data
            summary = f"HTTP {status}"
            if status in (401, 403):
                summary = "not allowed for this account"
            elif status == 404:
                summary = "not found, or not readable by this account"
            return Step(call.name, call.arguments, False, summary), f"HTTP {status}: {as_text(detail)}"
        if tool.shape:
            data = tool.shape(data, call.arguments)
        data = self.guard.filter(data)
        text = as_text(data)
        cap = Small.MAX_RESULT_CHARS if self.small else MAX_TOOL_RESULT_CHARS
        if len(text) > cap:
            text = text[:cap] + "\n… truncated: narrow the request (filters, limit, columns)"
        return Step(call.name, call.arguments, True, _summary(data)), text

    def _call_api(self, method: str, path: str, query: dict[str, Any] | None, body: Any) -> tuple[int, Any]:
        """Call the REST API in-process, carrying the authentication of the request being served.

        The agent runs in a worker thread (the answer is streamed from a synchronous generator), so the ASGI
        application — which only speaks async — is entered through a loop of its own for each call.
        """
        headers = {AGENT_HEADER: header(self.user.id, self.caller.scope), "x-requested-with": "RefExposer",
                   **self.caller.headers}
        if self.caller.client_ip:
            headers["x-forwarded-for"] = self.caller.client_ip
        params = {k: v for k, v in (query or {}).items() if v is not None}
        payload = body if body is not None and method != "GET" else None

        async def call() -> tuple[int, Any]:
            transport = httpx.ASGITransport(app=self.caller.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://refexposer.internal",
                                         timeout=300) as c:
                r = await c.request(method, path, params=params, json=payload, headers=headers)
            try:
                return r.status_code, r.json()
            except ValueError:
                return r.status_code, r.text

        try:
            return asyncio.run(call())
        except (httpx.HTTPError, RuntimeError) as e:
            return 500, {"detail": f"internal call failed: {e}"}


def small_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """Arguments of a tool call, kept to what fits a small context: `limit=500` returns more rows than the
    model can hold, and it asks for it because the description mentions the maximum."""
    out = dict(arguments)
    for name in ("limit", "top"):
        value = out.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            continue
        try:
            out[name] = min(int(value), Small.MAX_LIMIT)
        except (TypeError, ValueError):
            out[name] = Small.MAX_LIMIT
    if "limit" not in out:
        out["limit"] = Small.MAX_LIMIT
    return out


# A tool call written in the answer instead of asked for properly: ```json {"name": …, "arguments": {…}} ```,
# or a bare object. Only a name the model was actually offered is accepted.
_FENCE = re.compile(r"```(?:json|tool_call|tool)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)


def text_tool_call(content: str, offered: dict[str, Tool]) -> ToolCall | None:
    """The tool call a small model wrote as text, if that is what the answer is."""
    candidates = [m.group(1) for m in _FENCE.finditer(content or "")]
    stripped = (content or "").strip()
    if stripped.startswith("{"):
        candidates.append(stripped)
    for raw in candidates:
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        # The two shapes seen in the wild: {name, arguments} and {tool_call: {...}} / {function: {...}}
        for shape in (data, data.get("tool_call"), data.get("function")):
            if not isinstance(shape, dict):
                continue
            name = shape.get("name") or shape.get("tool")
            if not isinstance(name, str) or name not in offered:
                continue
            arguments = shape.get("arguments")
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except ValueError:
                    arguments = {}
            if not isinstance(arguments, dict):
                arguments = {k: v for k, v in shape.items() if k not in ("name", "tool", "arguments")}
            return ToolCall(name=name, arguments=arguments, id=name)
    return None


def _summary(data: Any) -> str:
    """One line describing a tool result, for the step trace shown to the user."""
    if isinstance(data, list):
        return f"{len(data)} item(s)"
    if isinstance(data, dict):
        for key in ("total", "row_count", "found"):
            if isinstance(data.get(key), int):
                rows = len(data["rows"]) if isinstance(data.get("rows"), list) else data[key]
                return f"{rows} row(s) of {data[key]}" if rows != data[key] else f"{data[key]} row(s)"
        if isinstance(data.get("rows"), list):
            return f"{len(data['rows'])} row(s)"
        if data.get("id"):
            return str(data["id"])
    return "ok"


def default_prompt() -> str:
    return (PROMPTS / "assistant.md").read_text(encoding="utf-8")
