"""What the two drivers actually put on the wire.

The assistant tests stub the provider, so they never checked the request bodies. These run the real drivers
over real HTTP against a server that records what it receives: the shape of `tools`, of a tool call and of a
tool result differs between Ollama and the OpenAI API, and getting it wrong is a plain HTTP 400 from the
inference server.
"""
import http.server
import json
import threading

import pytest

from app.appsettings import AssistantSettings, ProxySettings
from app.llm.provider import LLMError, OllamaProvider, OpenAIProvider, Reply, ToolCall, build
from app.settings import get_settings

TOOLS = [{"name": "query_rows", "description": "Rows of a referential",
          "parameters": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}}]
CALL = ToolCall(name="query_rows", arguments={"id": "countries", "limit": 5}, id="call_1")


class Recorder(http.server.BaseHTTPRequestHandler):
    """Answers like an inference server and keeps every request body."""

    received: list[tuple[str, dict]] = []
    reply: dict = {}
    status: int = 200

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
        Recorder.received.append((self.path, body))
        self._send(Recorder.reply, Recorder.status)

    def do_GET(self):  # noqa: N802
        Recorder.received.append((self.path, {}))
        if self.path.endswith("/api/tags"):
            self._send({"models": [{"name": "llama3.1:8b"}, {"name": "qwen2.5"}]})
        else:
            self._send({"data": [{"id": "mistral-7b"}, {"id": "llama3.1"}]})

    def _send(self, payload, status=200):
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass


@pytest.fixture()
def server():
    Recorder.received = []
    Recorder.status = 200
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Recorder)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd
    httpd.shutdown()


def provider_for(server, kind, **changes):
    cfg = AssistantSettings(enabled=True, provider=kind, model="test-model", timeout=10,
                            base_url=f"http://127.0.0.1:{server.server_address[1]}", **changes)
    return build(cfg, get_settings(), ProxySettings())


def last_body(path_ends: str) -> dict:
    return next(body for path, body in reversed(Recorder.received) if path.endswith(path_ends))


# --------------------------------------------------------------------------- model discovery

def test_models_are_read_from_each_api(server):
    assert provider_for(server, "ollama").models() == ["llama3.1:8b", "qwen2.5"]
    assert Recorder.received[-1][0] == "/api/tags"
    assert provider_for(server, "openai").models() == ["llama3.1", "mistral-7b"]
    assert Recorder.received[-1][0] == "/v1/models"


def test_an_address_already_ending_with_v1_is_not_doubled(server):
    port = server.server_address[1]
    cfg = AssistantSettings(enabled=True, provider="openai", model="m",
                            base_url=f"http://127.0.0.1:{port}/v1", timeout=10)
    build(cfg, get_settings(), ProxySettings()).models()
    assert Recorder.received[-1][0] == "/v1/models"


# --------------------------------------------------------------------------- the tool exchange

def test_ollama_wraps_the_tools_and_keeps_the_arguments_an_object(server):
    """Ollama takes `{"type": "function", "function": {...}}`, and `arguments` as an object."""
    Recorder.reply = {"message": {"role": "assistant", "content": "done"}, "done": True}
    ollama = provider_for(server, "ollama")
    ollama.chat([{"role": "user", "content": "hi"}], TOOLS)
    body = last_body("/api/chat")
    assert body["tools"] == [{"type": "function", "function": TOOLS[0]}]
    assert body["options"]["num_predict"] == 2048

    turn = ollama.assistant_turn(Reply(content="", tool_calls=[CALL]))
    # An object, not a JSON string: a string is what made Ollama answer
    # "value looks like object, but can't find closing } symbol"
    assert turn["tool_calls"] == [{"function": {"name": "query_rows",
                                                "arguments": {"id": "countries", "limit": 5}}}]
    assert turn["role"] == "assistant" and turn["content"] == ""
    # Ollama names the tool of a result with tool_name; `name` would be dropped by its decoder
    assert ollama.tool_result(CALL, "5 rows") == {"role": "tool", "content": "5 rows", "tool_name": "query_rows"}


def test_openai_wraps_the_tools_and_serialises_the_arguments(server):
    Recorder.reply = {"choices": [{"message": {"role": "assistant", "content": "done"}, "finish_reason": "stop"}]}
    openai = provider_for(server, "openai")
    openai.chat([{"role": "user", "content": "hi"}], TOOLS)
    body = last_body("/v1/chat/completions")
    assert body["tools"] == [{"type": "function", "function": TOOLS[0]}]
    assert body["tool_choice"] == "auto" and body["max_tokens"] == 2048

    turn = openai.assistant_turn(Reply(content="", tool_calls=[CALL]))
    call = turn["tool_calls"][0]
    assert call["id"] == "call_1" and call["type"] == "function"
    # A JSON string here, as the OpenAI API defines it
    assert json.loads(call["function"]["arguments"]) == {"id": "countries", "limit": 5}
    assert openai.tool_result(CALL, "5 rows") == {
        "role": "tool", "tool_call_id": "call_1", "name": "query_rows", "content": "5 rows"}


def test_a_full_agent_turn_is_accepted_by_both_drivers(server):
    """The messages one driver produces go back to it unchanged on the next call."""
    for kind, path, reply in (
        ("ollama", "/api/chat", {"message": {"role": "assistant", "content": "ok"}, "done": True}),
        ("openai", "/v1/chat/completions", {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}),
    ):
        Recorder.reply = reply
        provider = provider_for(server, kind)
        messages = [{"role": "user", "content": "how many countries?"}]
        asked = Reply(content="", tool_calls=[CALL])
        messages.append(provider.assistant_turn(asked))
        messages.append(provider.tool_result(CALL, "5 rows"))
        provider.chat(messages, TOOLS)
        sent = last_body(path)["messages"]
        assert [m["role"] for m in sent] == ["user", "assistant", "tool"]
        # Whatever the shape, the body is valid JSON that round-trips through HTTP
        assert json.loads(json.dumps(sent)) == sent


# --------------------------------------------------------------------------- answers and errors

def test_tool_calls_are_read_from_both_answer_shapes(server):
    Recorder.reply = {"message": {"role": "assistant", "content": "",
                                  "tool_calls": [{"function": {"name": "query_rows",
                                                               "arguments": {"id": "countries"}}}]},
                      "done": True, "prompt_eval_count": 12, "eval_count": 3}
    reply = provider_for(server, "ollama").chat([{"role": "user", "content": "hi"}], TOOLS)
    assert [(c.name, c.arguments) for c in reply.tool_calls] == [("query_rows", {"id": "countries"})]
    assert reply.usage == {"prompt_tokens": 12, "completion_tokens": 3}

    Recorder.reply = {"choices": [{"message": {"content": "", "tool_calls": [
        {"id": "c1", "type": "function",
         "function": {"name": "query_rows", "arguments": '{"id": "countries"}'}}]},
        "finish_reason": "tool_calls"}], "usage": {"prompt_tokens": 9}}
    reply = provider_for(server, "openai").chat([{"role": "user", "content": "hi"}], TOOLS)
    # The OpenAI API gives the arguments as a JSON string: they come back parsed
    assert [(c.name, c.arguments, c.id) for c in reply.tool_calls] == [("query_rows", {"id": "countries"}, "c1")]
    assert reply.usage == {"prompt_tokens": 9}


def test_arguments_that_are_not_usable_do_not_break_the_turn(server):
    """A model inventing a malformed arguments string must not take the whole exchange down."""
    Recorder.reply = {"choices": [{"message": {"content": "", "tool_calls": [
        {"id": "c1", "function": {"name": "query_rows", "arguments": "not json"}}]}}]}
    reply = provider_for(server, "openai").chat([{"role": "user", "content": "hi"}], TOOLS)
    assert reply.tool_calls[0].arguments == {}


def test_errors_of_the_server_are_reported_readably(server):
    Recorder.status = 400
    Recorder.reply = {"error": "value looks like object, but can't find closing } symbol"}
    with pytest.raises(LLMError) as e:
        provider_for(server, "ollama").chat([{"role": "user", "content": "hi"}], TOOLS)
    assert "HTTP 400" in str(e.value) and "closing }" in str(e.value)

    Recorder.status = 422
    Recorder.reply = {"error": {"message": "model not found", "type": "invalid_request_error"}}
    with pytest.raises(LLMError) as e:
        provider_for(server, "openai").chat([{"role": "user", "content": "hi"}], TOOLS)
    assert "HTTP 422" in str(e.value) and "model not found" in str(e.value)


def test_an_unreachable_server_says_so(server):
    cfg = AssistantSettings(enabled=True, provider="ollama", model="m", timeout=5,
                            base_url="http://127.0.0.1:1")
    with pytest.raises(LLMError) as e:
        build(cfg, get_settings(), ProxySettings()).models()
    assert "unreachable" in str(e.value)


# --------------------------------------------------------------------------- headers

def test_the_api_key_and_the_extra_headers_are_sent(server):
    Recorder.reply = {"message": {"content": "ok"}, "done": True}

    provider = provider_for(server, "ollama", api_key="sk-test",
                            headers={"X-Tenant": "soc", "X-Api-Key": "k-1"})
    sent = provider._headers()
    assert sent["Authorization"] == "Bearer sk-test"
    assert sent["X-Tenant"] == "soc" and sent["X-Api-Key"] == "k-1"


def test_an_extra_header_can_replace_the_authorization(server):
    provider = provider_for(server, "openai", api_key="sk-test",
                            headers={"Authorization": "Token other"})
    assert provider._headers()["Authorization"] == "Token other"


# --------------------------------------------------------------------------- generation parameters

def cfg_with(**changes):
    return AssistantSettings(enabled=True, model="m", base_url="http://x:11434", **changes)


def test_ollama_options_carry_the_named_settings(server):
    from app.llm.provider import ollama_parameters

    assert ollama_parameters(cfg_with(temperature=0.1, top_p=0.9, max_tokens=512)) == {
        "temperature": 0.1, "top_p": 0.9, "num_predict": 512}
    # num_ctx and stop only appear when they are set
    assert ollama_parameters(cfg_with(context_tokens=8192, stop=["</s>", ""])) == {
        "temperature": 0.2, "top_p": 0.95, "num_predict": 2048, "num_ctx": 8192, "stop": ["</s>"]}


def test_free_form_options_are_passed_through_and_win(server):
    from app.llm.provider import ollama_parameters, openai_parameters

    cfg = cfg_with(temperature=0.5, context_tokens=4096,
                   options={"repeat_penalty": 1.15, "top_k": 40, "seed": 7, "num_ctx": 8192,
                            "mirostat": 2, "numa": True, "stop": ["END"]})
    assert ollama_parameters(cfg) == {
        "temperature": 0.5, "top_p": 0.95, "num_predict": 2048,
        # the option wins over context_tokens
        "num_ctx": 8192, "repeat_penalty": 1.15, "top_k": 40, "seed": 7, "mirostat": 2, "numa": True,
        "stop": ["END"]}
    # The same box goes to the top level of an OpenAI request, where the names of that API apply
    assert openai_parameters(cfg_with(options={"repetition_penalty": 1.1, "top_k": 20})) == {
        "temperature": 0.2, "top_p": 0.95, "max_tokens": 2048, "repetition_penalty": 1.1, "top_k": 20}


def test_the_options_reach_the_request(server):
    Recorder.reply = {"message": {"content": "ok"}, "done": True}
    provider_for(server, "ollama", temperature=0.1, max_tokens=512, context_tokens=8192,
                 options={"repeat_penalty": 1.15}).chat([{"role": "user", "content": "hi"}])
    assert last_body("/api/chat")["options"] == {
        "temperature": 0.1, "top_p": 0.95, "num_predict": 512, "num_ctx": 8192, "repeat_penalty": 1.15}

    Recorder.reply = {"choices": [{"message": {"content": "ok"}}]}
    provider_for(server, "openai", options={"repetition_penalty": 1.1}).chat([{"role": "user", "content": "hi"}])
    body = last_body("/v1/chat/completions")
    assert body["repetition_penalty"] == 1.1 and body["model"] == "test-model"
    assert "num_ctx" not in body  # that API has no such parameter


def test_an_option_cannot_replace_the_request_itself(server):
    import pytest as _pytest

    for name in ("model", "messages", "stream", "tools", "options"):
        with _pytest.raises(ValueError, match="part of the request itself"):
            cfg_with(options={name: "x"})
    with _pytest.raises(ValueError, match="invalid option name"):
        cfg_with(options={"bad name": 1})
    with _pytest.raises(ValueError, match="must be a number"):
        cfg_with(options={"nested": {"a": 1}})
    with _pytest.raises(ValueError, match="at most 40 options"):
        cfg_with(options={f"o{i}": 1 for i in range(41)})
    # Numbers, strings, booleans and short lists are kept as they are
    assert cfg_with(options={"a": 1, "b": 1.5, "c": "x", "d": False, "e": ["x", 2]}).options == {
        "a": 1, "b": 1.5, "c": "x", "d": False, "e": ["x", 2]}
