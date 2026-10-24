"""Inference servers: one interface, two drivers.

- ``ollama``: ``/api/tags``, ``/api/chat`` (``/api/show`` for the details of a model)
- ``openai``: ``/v1/models``, ``/v1/chat/completions`` (vLLM, llama.cpp server, LocalAI, TGI)

RefExposer is the only client of the server: the browser never calls it. The connection goes through the proxy
and the certificate authorities configured in *Settings › Network & proxy*, so a model served behind the company
TLS proxy or signed by the internal root CA works without disabling verification.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Iterator

import httpx

from .. import network
from ..appsettings import AssistantSettings, ProxySettings
from ..settings import Settings

log = logging.getLogger(__name__)

# A tool call that never completes would hold the connection open for the whole generation timeout
MAX_TOOL_CALLS_PER_STEP = 8


class LLMError(Exception):
    """Reported to the user as it is: the model server is part of their infrastructure."""


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]
    id: str = ""


@dataclass
class Reply:
    """One answer of the model: text, and the tools it wants called."""

    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = ""
    usage: dict[str, Any] = field(default_factory=dict)


class Provider:
    """Base driver. `chat` returns one Reply; `stream` yields text chunks then one final Reply."""

    def __init__(self, cfg: AssistantSettings, settings: Settings, proxy: ProxySettings):
        if not cfg.base_url:
            raise LLMError("no inference server configured (Settings › Assistant)")
        self.cfg = cfg
        self.settings = settings
        self.proxy = proxy

    # ------------------------------------------------------------------ HTTP
    def _client(self, timeout: float | None = None) -> httpx.Client:
        return network.client(self.settings, self.proxy, verify_tls=self.cfg.verify_tls,
                              timeout=timeout or self.cfg.timeout)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.cfg.api_key:
            headers["Authorization"] = f"Bearer {self.cfg.api_key}"
        # Last: a gateway may want the API key under a name of its own, which then wins
        headers.update(self.cfg.headers)
        return headers

    def _url(self, path: str) -> str:
        return self.cfg.base_url.rstrip("/") + path

    def _post(self, path: str, body: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
        try:
            with self._client(timeout) as c:
                r = c.post(self._url(path), json=body, headers=self._headers())
        except httpx.HTTPError as e:
            raise LLMError(f"inference server unreachable at {self.cfg.base_url}: {e}") from e
        return self._json(r)

    def _get(self, path: str, timeout: float | None = None) -> dict[str, Any]:
        try:
            with self._client(timeout) as c:
                r = c.get(self._url(path), headers=self._headers())
        except httpx.HTTPError as e:
            raise LLMError(f"inference server unreachable at {self.cfg.base_url}: {e}") from e
        return self._json(r)

    def _json(self, r: httpx.Response) -> dict[str, Any]:
        if r.status_code >= 400:
            raise LLMError(f"inference server: HTTP {r.status_code} {_detail(r)}")
        try:
            return r.json()
        except ValueError as e:
            raise LLMError(f"unreadable answer from the inference server: {r.text[:200]}") from e

    # ------------------------------------------------------------------ interface
    def models(self) -> list[str]:
        raise NotImplementedError

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> Reply:
        raise NotImplementedError

    def stream(self, messages: list[dict[str, Any]]) -> Iterator[str | Reply]:
        raise NotImplementedError

    def health(self) -> dict[str, Any]:
        models = self.models()
        return {"ok": True, "models": models, "model_found": not self.cfg.model or self.cfg.model in models,
                "parameters": self.parameters()}

    # The two families write a tool exchange differently, down to the type of the arguments: the agent asks the
    # driver for the two messages instead of building them itself.
    def assistant_turn(self, reply: Reply) -> dict[str, Any]:
        """The assistant message that asked for the tools, to put back in the conversation."""
        raise NotImplementedError

    def tool_result(self, call: ToolCall, content: str) -> dict[str, Any]:
        """The answer of one tool, as the model expects to read it."""
        raise NotImplementedError

    def parameters(self) -> dict[str, Any]:
        """What this driver will actually ask for, to show in the interface.

        The named settings are translated into the names of this API, then the free-form options are applied
        on top: a key set there wins, which is how anything the server supports and RefExposer does not name
        can still be used.
        """
        raise NotImplementedError


class OllamaProvider(Provider):
    """Native Ollama API. Tool calling is supported by the models that declare it (llama3.1, qwen2.5, mistral…)."""

    def models(self) -> list[str]:
        data = self._get("/api/tags", timeout=20)
        return sorted(m.get("name", "") for m in (data.get("models") or []) if m.get("name"))

    def parameters(self) -> dict[str, Any]:
        return ollama_parameters(self.cfg)

    def _body(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None, stream: bool) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.cfg.model,
            "messages": messages,
            "stream": stream,
            "options": self.parameters(),
        }
        if tools:
            # Ollama takes the same envelope as the OpenAI API around each function
            body["tools"] = [{"type": "function", "function": t} for t in tools]
        return body

    def assistant_turn(self, reply: Reply) -> dict[str, Any]:
        # `arguments` is an object here, not the JSON string the OpenAI API wants: sending a string makes
        # Ollama answer 400 ("value looks like object, but can't find closing } symbol")
        return {
            "role": "assistant",
            "content": reply.content or "",
            "tool_calls": [{"function": {"name": c.name, "arguments": c.arguments}} for c in reply.tool_calls],
        }

    def tool_result(self, call: ToolCall, content: str) -> dict[str, Any]:
        return {"role": "tool", "content": content, "tool_name": call.name}

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> Reply:
        data = self._post("/api/chat", self._body(messages, tools, False))
        return _ollama_reply(data)

    def stream(self, messages: list[dict[str, Any]]) -> Iterator[str | Reply]:
        body = self._body(messages, None, True)
        content: list[str] = []
        try:
            with self._client() as c:
                with c.stream("POST", self._url("/api/chat"), json=body, headers=self._headers()) as r:
                    if r.status_code >= 400:
                        r.read()
                        raise LLMError(f"inference server: HTTP {r.status_code} {_detail(r)}")
                    for line in r.iter_lines():
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            chunk = json.loads(line)
                        except ValueError:
                            continue
                        if chunk.get("error"):
                            raise LLMError(f"inference server: {chunk['error']}")
                        piece = (chunk.get("message") or {}).get("content") or ""
                        if piece:
                            content.append(piece)
                            yield piece
                        if chunk.get("done"):
                            yield Reply(content="".join(content), finish_reason=chunk.get("done_reason", "stop"),
                                        usage=_ollama_usage(chunk))
                            return
        except httpx.HTTPError as e:
            raise LLMError(f"inference server unreachable at {self.cfg.base_url}: {e}") from e
        yield Reply(content="".join(content), finish_reason="stop")


class OpenAIProvider(Provider):
    """OpenAI-compatible API, as served by vLLM, llama.cpp, LocalAI, TGI and Ollama's own /v1 endpoint."""

    def _url(self, path: str) -> str:
        base = self.cfg.base_url.rstrip("/")
        # The address may already end with /v1, as the servers document it
        if base.endswith("/v1"):
            base = base[:-3]
        return base + path

    def models(self) -> list[str]:
        data = self._get("/v1/models", timeout=20)
        return sorted(m.get("id", "") for m in (data.get("data") or []) if m.get("id"))

    def parameters(self) -> dict[str, Any]:
        return openai_parameters(self.cfg)

    def _body(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None, stream: bool) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.cfg.model,
            "messages": messages,
            "stream": stream,
            **self.parameters(),
        }
        if tools:
            body["tools"] = [{"type": "function", "function": t} for t in tools]
            body["tool_choice"] = "auto"
        if stream:
            body["stream_options"] = {"include_usage": True}
        return body

    def assistant_turn(self, reply: Reply) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": reply.content or "",
            "tool_calls": [{"id": c.id or c.name, "type": "function",
                            "function": {"name": c.name, "arguments": json.dumps(c.arguments)}}
                           for c in reply.tool_calls],
        }

    def tool_result(self, call: ToolCall, content: str) -> dict[str, Any]:
        return {"role": "tool", "tool_call_id": call.id or call.name, "name": call.name, "content": content}

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> Reply:
        data = self._post("/v1/chat/completions", self._body(messages, tools, False))
        return _openai_reply(data)

    def stream(self, messages: list[dict[str, Any]]) -> Iterator[str | Reply]:
        body = self._body(messages, None, True)
        content: list[str] = []
        usage: dict[str, Any] = {}
        finish = "stop"
        try:
            with self._client() as c:
                with c.stream("POST", self._url("/v1/chat/completions"), json=body, headers=self._headers()) as r:
                    if r.status_code >= 400:
                        r.read()
                        raise LLMError(f"inference server: HTTP {r.status_code} {_detail(r)}")
                    for line in r.iter_lines():
                        line = line.strip()
                        if not line.startswith("data:"):
                            continue
                        payload = line[5:].strip()
                        if payload == "[DONE]":
                            break
                        try:
                            chunk = json.loads(payload)
                        except ValueError:
                            continue
                        if chunk.get("usage"):
                            usage = _openai_usage(chunk["usage"])
                        for choice in chunk.get("choices") or []:
                            piece = (choice.get("delta") or {}).get("content") or ""
                            if piece:
                                content.append(piece)
                                yield piece
                            if choice.get("finish_reason"):
                                finish = choice["finish_reason"]
        except httpx.HTTPError as e:
            raise LLMError(f"inference server unreachable at {self.cfg.base_url}: {e}") from e
        yield Reply(content="".join(content), finish_reason=finish, usage=usage)


def build(cfg: AssistantSettings, settings: Settings, proxy: ProxySettings) -> Provider:
    return (OllamaProvider if cfg.provider == "ollama" else OpenAIProvider)(cfg, settings, proxy)


# --------------------------------------------------------------------------- generation parameters
# The named settings carry the same meaning in both APIs but not the same names; the free-form options of
# the configuration are applied last, so a key given there wins over what RefExposer computed.

def ollama_parameters(cfg: AssistantSettings) -> dict[str, Any]:
    """Content of the `options` object of /api/chat."""
    options: dict[str, Any] = {
        "temperature": cfg.temperature,
        "top_p": cfg.top_p,
        "num_predict": cfg.max_tokens,
    }
    if cfg.context_tokens:
        options["num_ctx"] = cfg.context_tokens
    if cfg.stop:
        options["stop"] = list(cfg.stop)
    options.update(cfg.options)
    return options


def openai_parameters(cfg: AssistantSettings) -> dict[str, Any]:
    """Generation fields of /v1/chat/completions.

    There is no equivalent of `num_ctx`: the context window is a property of how the server loaded the model,
    not of the request, so `context_tokens` has no effect on this API.
    """
    params: dict[str, Any] = {
        "temperature": cfg.temperature,
        "top_p": cfg.top_p,
        "max_tokens": cfg.max_tokens,
    }
    if cfg.stop:
        params["stop"] = list(cfg.stop)
    params.update(cfg.options)
    return params


def parameters_for(cfg: AssistantSettings) -> dict[str, Any]:
    """What the configured API would receive, without contacting anything (shown in the administration)."""
    return ollama_parameters(cfg) if cfg.provider == "ollama" else openai_parameters(cfg)


# --------------------------------------------------------------------------- answer shapes
# The two families report tool calls, usage and errors differently; everything is normalised here.

def _ollama_reply(data: dict[str, Any]) -> Reply:
    if data.get("error"):
        raise LLMError(f"inference server: {data['error']}")
    message = data.get("message") or {}
    calls = []
    for item in (message.get("tool_calls") or [])[:MAX_TOOL_CALLS_PER_STEP]:
        fn = item.get("function") or {}
        calls.append(ToolCall(name=str(fn.get("name") or ""), arguments=_arguments(fn.get("arguments")),
                              id=str(item.get("id") or fn.get("name") or "")))
    return Reply(content=message.get("content") or "", tool_calls=calls,
                 finish_reason=data.get("done_reason") or "stop", usage=_ollama_usage(data))


def _openai_reply(data: dict[str, Any]) -> Reply:
    choices = data.get("choices") or []
    if not choices:
        raise LLMError("the inference server returned no answer")
    message = choices[0].get("message") or {}
    calls = []
    for item in (message.get("tool_calls") or [])[:MAX_TOOL_CALLS_PER_STEP]:
        fn = item.get("function") or {}
        calls.append(ToolCall(name=str(fn.get("name") or ""), arguments=_arguments(fn.get("arguments")),
                              id=str(item.get("id") or "")))
    return Reply(content=message.get("content") or "", tool_calls=calls,
                 finish_reason=choices[0].get("finish_reason") or "stop", usage=_openai_usage(data.get("usage") or {}))


def _arguments(raw: Any) -> dict[str, Any]:
    """Arguments of a tool call: an object for Ollama, a JSON string for the OpenAI API."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _ollama_usage(data: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in (("prompt_tokens", data.get("prompt_eval_count")),
                              ("completion_tokens", data.get("eval_count"))) if v}


def _openai_usage(usage: dict[str, Any]) -> dict[str, Any]:
    return {k: usage[k] for k in ("prompt_tokens", "completion_tokens", "total_tokens") if usage.get(k)}


def _detail(r: httpx.Response) -> str:
    try:
        data = r.json()
    except ValueError:
        return r.text[:300]
    for key in ("error", "detail", "message"):
        value = data.get(key) if isinstance(data, dict) else None
        if isinstance(value, dict):
            value = value.get("message") or json.dumps(value)[:300]
        if value:
            return str(value)[:300]
    return json.dumps(data, default=str)[:300]
