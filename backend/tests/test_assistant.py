"""The in-application assistant: conversations, agent mode and the confidential referentials guard.

The inference server is replaced by a scripted provider: the agent loop, the in-process API calls made with
the session of the user, and the authorization that goes with them are the real ones.
"""
import json

import pytest

from app.llm.provider import OllamaProvider
from conftest import USER_PASSWORD, create_user, new_client, refresh
from test_admin_referentials import wait_idle

OWNERS = """code,owner
FR,team-paris
DE,team-berlin
"""


class Scripted:
    """Inference server answering a programmed list of replies."""

    def __init__(self, replies):
        from app.llm.provider import Reply

        self.replies = [r if isinstance(r, Reply) else Reply(content=r) for r in replies]
        self.calls = []        # messages of each chat() call
        self.tool_names = []   # tools offered on the first call

    def _next(self):
        return self.replies.pop(0) if self.replies else __import__("app.llm.provider", fromlist=["Reply"]).Reply(
            content="(no more scripted reply)")

    def chat(self, messages, tools=None):
        self.calls.append(messages)
        if tools is not None and not self.tool_names:
            self.tool_names = [t["name"] for t in tools]
        return self._next()

    def stream(self, messages):
        self.calls.append(messages)
        reply = self._next()
        if reply.content:
            yield reply.content
        yield reply

    def health(self):
        return {"ok": True, "models": ["test-model"], "model_found": True}

    # The shape of a tool exchange is the driver's business: the real Ollama one is used here, so these
    # tests go through the same messages a real server would receive
    assistant_turn = OllamaProvider.assistant_turn
    tool_result = OllamaProvider.tool_result


def tool_call(tool, /, **arguments):
    from app.llm.provider import Reply, ToolCall

    return Reply(tool_calls=[ToolCall(name=tool, arguments=arguments, id=tool)])


@pytest.fixture()
def llm(env, monkeypatch):
    """Configures the assistant and lets a test script what the model answers."""
    import app.api.assistant_api as api

    holder = {}

    def script(*replies):
        holder["provider"] = Scripted(list(replies))
        return holder["provider"]

    api._rate.clear()  # the limiter is per process: each test starts with its own budget

    monkeypatch.setattr(api, "build", lambda cfg, settings, proxy: holder["provider"])
    env.script = script  # type: ignore[attr-defined]
    return script


def configure(env, **changes):
    body = {"enabled": True, "provider": "ollama", "base_url": "http://ollama.internal:11434",
            "model": "test-model", "agent_enabled": True, **changes}
    r = env.put("/api/admin/settings/assistant", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def ask(client, question, agent=True, conversation=None):
    """Send a question and collect the server-sent events."""
    if conversation is None:
        conversation = client.post("/api/assistant/conversations", json={}).json()["id"]
    r = client.post(f"/api/assistant/conversations/{conversation}/messages",
                    json={"content": question, "agent": agent})
    assert r.status_code == 200, r.text
    events = []
    for line in r.text.splitlines():
        if line.startswith("data: ") and line != "data: [DONE]":
            events.append(json.loads(line[6:]))
    return conversation, events


def final(events):
    return next(e for e in events if e.get("type") == "done")


def tool_results(provider, tool=None):
    """Every tool result that was handed to the model, in order."""
    seen, out = set(), []
    for messages in provider.calls:
        for i, m in enumerate(messages):
            name = m.get("tool_name") or m.get("name")
            if m.get("role") != "tool" or (tool and name != tool) or i in seen:
                continue
            out.append(m["content"])
        seen = set()
    unique = []
    for content in out:
        if content not in unique:
            unique.append(content)
    return unique


def source(env, ref_id, name, content, **config):
    folder = env.data_dir / ref_id / "raw"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "data.csv").write_text(content, encoding="utf-8")
    r = env.post("/api/admin/referentials", json={
        "config": {"id": ref_id, "name": name, "format": "csv",
                   "source": {"type": "local", "path": "raw/*.csv"}, **config}})
    assert r.status_code == 201, r.text
    assert wait_idle(env, ref_id)["status"] == "success"


# --------------------------------------------------------------------------- availability

def test_hidden_until_configured(env):
    assert env.get("/api/assistant").json() == {
        "available": False, "enabled": False, "configured": False, "model": None, "agent": False,
        "writes": False, "confidential": False, "watchdogs": False, "max_steps": 8, "max_chars": 8000}
    # Nothing is reachable while it is disabled
    assert env.get("/api/assistant/conversations").status_code == 403
    assert env.post("/api/assistant/conversations", json={}).status_code == 403

    configure(env, agent_enabled=False)
    status = env.get("/api/assistant").json()
    assert status["available"] and status["enabled"] and status["configured"]
    assert status["model"] == "test-model" and not status["agent"] and not status["writes"]


def test_limited_to_named_accounts(env):
    configure(env)
    create_user(env, "alice", role="user")
    create_user(env, "bob", role="user")
    alice_id = next(u["id"] for u in env.get("/api/admin/users").json() if u["username"] == "alice")
    configure(env, user_ids=[alice_id])

    alice = new_client(env, "alice", USER_PASSWORD)
    bob = new_client(env, "bob", USER_PASSWORD)
    assert alice.get("/api/assistant").json()["available"] is True
    assert bob.get("/api/assistant").json()["available"] is False
    assert bob.get("/api/assistant/conversations").status_code == 403
    # An administrator always may, whatever the lists say
    assert env.get("/api/assistant").json()["available"] is True


def test_groups_and_accounts_add_up(env):
    configure(env)
    group = env.post("/api/admin/groups", json={"name": "ai-users"}).json()
    create_user(env, "member", role="user", group_ids=[group["id"]])
    create_user(env, "named", role="user")
    named_id = next(u["id"] for u in env.get("/api/admin/users").json() if u["username"] == "named")
    create_user(env, "neither", role="user")
    configure(env, groups=["ai-users"], user_ids=[named_id])

    for who, expected in (("member", True), ("named", True), ("neither", False)):
        client = new_client(env, who, USER_PASSWORD)
        assert client.get("/api/assistant").json()["available"] is expected, who


def test_a_service_account_cannot_be_allowed(env):
    configure(env)
    account = env.post("/api/admin/service-accounts", json={"username": "feeder"}).json()
    r = env.put("/api/admin/settings/assistant", json={
        "enabled": True, "base_url": "http://ollama.internal:11434", "model": "test-model",
        "user_ids": [account["id"]]})
    assert r.status_code == 422 and "no interface session" in r.text
    r = env.put("/api/admin/settings/assistant", json={
        "enabled": True, "base_url": "http://ollama.internal:11434", "model": "test-model",
        "user_ids": [9999]})
    assert r.status_code == 422 and "unknown" in r.text


def test_limited_to_the_allowed_groups(env):
    group = env.post("/api/admin/groups", json={"name": "ai-users"}).json()
    configure(env, groups=["ai-users"])
    create_user(env, "outsider", role="user")
    outsider = new_client(env, "outsider", USER_PASSWORD)
    assert outsider.get("/api/assistant").json()["available"] is False
    assert outsider.get("/api/assistant/conversations").status_code == 403

    create_user(env, "insider", role="user", group_ids=[group["id"]])
    insider = new_client(env, "insider", USER_PASSWORD)
    assert insider.get("/api/assistant").json()["available"] is True
    assert insider.get("/api/assistant/conversations").status_code == 200


# --------------------------------------------------------------------------- conversations

def test_conversation_persists_and_resets(env, llm):
    configure(env)
    llm("Hello, what can I do for you?")
    conv, events = ask(env, "Hi", agent=False)
    assert [e["text"] for e in events if e["type"] == "token"] == ["Hello, what can I do for you?"]
    assert final(events)["content"] == "Hello, what can I do for you?"

    # The exchange is stored: it is still there on the next load
    detail = env.get(f"/api/assistant/conversations/{conv}").json()
    assert [(m["role"], m["content"]) for m in detail["messages"]] == [
        ("user", "Hi"), ("assistant", "Hello, what can I do for you?")]
    assert detail["title"] == "Hi"

    # A second question keeps the history: the model receives the whole conversation
    llm("Still here.")
    ask(env, "And now?", agent=False, conversation=conv)
    detail = env.get(f"/api/assistant/conversations/{conv}").json()
    assert len(detail["messages"]) == 4
    assert [c["id"] for c in env.get("/api/assistant/conversations").json()] == [conv]

    assert env.delete("/api/assistant/conversations").json() == {"deleted": 1}
    assert env.get("/api/assistant/conversations").json() == []
    assert env.get(f"/api/assistant/conversations/{conv}").status_code == 404


def test_history_is_sent_to_the_model(env, llm):
    configure(env)
    llm("one")
    conv, _ = ask(env, "first question", agent=False)
    provider = llm("two")
    ask(env, "second question", agent=False, conversation=conv)
    sent = provider.calls[-1]
    assert sent[0]["role"] == "system"
    assert [m["content"] for m in sent[1:]] == ["first question", "one", "second question"]


def test_a_conversation_belongs_to_one_account(env, llm):
    configure(env)
    llm("hello")
    conv, _ = ask(env, "mine", agent=False)
    create_user(env, "other", role="user")
    other = new_client(env, "other", USER_PASSWORD)
    assert other.get(f"/api/assistant/conversations/{conv}").status_code == 404
    assert other.delete(f"/api/assistant/conversations/{conv}").status_code == 404
    assert other.post(f"/api/assistant/conversations/{conv}/messages",
                      json={"content": "hi"}).status_code == 404
    assert other.get("/api/assistant/conversations").json() == []


# --------------------------------------------------------------------------- agent mode

def test_agent_reads_a_referential_through_the_api(env, llm):
    refresh(env, "countries")
    configure(env)
    provider = llm(tool_call("query_rows", id="countries", limit=5), "France has 68 million inhabitants.")
    _, events = ask(env, "How many people live in France?")

    steps = [e for e in events if e["type"] == "step"]
    assert [s["tool"] for s in steps] == ["query_rows"]
    assert steps[0]["ok"] and "row(s)" in steps[0]["summary"]
    assert final(events)["content"] == "France has 68 million inhabitants."
    # The rows really came back from the API and were given to the model
    rows = tool_results(provider, "query_rows")
    assert rows and "France" in rows[0]


def test_tools_are_not_offered_without_agent_mode(env, llm):
    refresh(env, "countries")
    configure(env)
    provider = llm("I cannot look that up.")
    ask(env, "How many countries?", agent=False)
    assert provider.tool_names == []


def test_writes_are_refused_unless_allowed(env, llm):
    refresh(env, "countries")
    configure(env, allow_writes=False)
    provider = llm(tool_call("refresh_referential", id="countries"), "I cannot update it myself.")
    _, events = ask(env, "Update countries")
    # The tool is not even offered, so the call is reported as unknown rather than run
    assert "refresh_referential" not in provider.tool_names
    assert [s["ok"] for s in events if s["type"] == "step"] == [False]
    assert env.get("/api/referentials/countries").json()["current_run"] is None


def test_write_tools_follow_the_rights_of_the_account(env, llm):
    refresh(env, "countries")
    configure(env, allow_writes=True)
    create_user(env, "reader", role="user")
    env.post("/api/admin/grants", json={"referential_id": "countries",
                                        "user_id": env.get("/api/admin/users").json()[-1]["id"],
                                        "level": "read"})
    reader = new_client(env, "reader", USER_PASSWORD)
    llm(tool_call("refresh_referential", id="countries"), "You do not have the right to update it.")
    _, events = ask(reader, "Update countries")
    step = next(e for e in events if e["type"] == "step")
    assert not step["ok"] and step["summary"] == "not allowed for this account"


def test_agent_hands_over_a_query_and_a_file(env, llm):
    refresh(env, "countries")
    configure(env)
    llm(tool_call("propose_sql", sql="SELECT * FROM countries", title="Every country"),
        tool_call("offer_download", filename="countries.json", format="json", content='[{"code": "FR"}]'),
        "Here is the query and the file.")
    conv, events = ask(env, "Give me every country as a file and a query")
    artifacts = final(events)["artifacts"]
    assert [a["kind"] for a in artifacts] == ["sql", "file"]
    assert artifacts[0]["sql"] == "SELECT * FROM countries" and artifacts[0]["title"] == "Every country"
    assert artifacts[1]["filename"] == "countries.json" and artifacts[1]["format"] == "json"
    # Kept with the message, so the buttons are still there after a reload
    stored = env.get(f"/api/assistant/conversations/{conv}").json()["messages"][-1]
    assert [a["kind"] for a in stored["artifacts"]] == ["sql", "file"]


def test_agent_creates_a_referential_from_a_query(env, llm):
    refresh(env, "countries")
    source(env, "owners", "Owners", OWNERS, key="code")
    configure(env, allow_writes=True)
    llm(tool_call("create_referential_from_sql", id="owned", name="Owned countries", key="code",
                  sql="SELECT c.code, c.label, o.owner FROM countries c JOIN owners o ON o.code = c.code"),
        "The referential 'owned' is being built.")
    _, events = ask(env, "Make a referential joining countries and owners")
    step = next(e for e in events if e["type"] == "step")
    assert step["ok"], step
    assert wait_idle(env, "owned")["status"] == "success"
    assert env.get("/api/referentials/owned").json()["kind"] == "combined"


def test_agent_stops_after_max_steps(env, llm):
    refresh(env, "countries")
    configure(env, max_steps=2)
    llm(tool_call("list_referentials"), tool_call("list_referentials"), "I kept looking and found this.")
    _, events = ask(env, "Loop please")
    assert len([e for e in events if e["type"] == "step"]) == 2
    assert final(events)["error"] == "stopped after 2 steps"
    assert final(events)["content"] == "I kept looking and found this."


# --------------------------------------------------------------------------- confidential referentials

def confidential(env):
    source(env, "secrets", "Secret owners", OWNERS, key="code", confidential=True)


def test_confidential_referential_never_reaches_the_model(env, llm):
    refresh(env, "countries")
    confidential(env)
    configure(env, allow_confidential=False)
    assert env.get("/api/assistant").json()["confidential"] is False

    provider = llm(tool_call("list_referentials"), tool_call("query_rows", id="secrets"),
                   tool_call("lookup", id="secrets", value="FR"),
                   tool_call("run_sql", sql="SELECT * FROM secrets"),
                   tool_call("get_referential", id="secrets"),
                   tool_call("search", q="team-paris"),
                   "I have no access to that referential.")
    _, events = ask(env, "What is in the secrets referential?")
    steps = [e for e in events if e["type"] == "step"]

    # The listing does not even mention it
    listing = tool_results(provider, "list_referentials")[0]
    assert "countries" in listing and "secrets" not in listing
    # Every direct attempt is refused before the call is made
    for step in steps[1:3] + steps[4:5]:
        assert not step["ok"] and step["summary"] == "confidential referential", step
    # SQL cannot reach it either: the table is not in the sandbox of this call
    sql_result = tool_results(provider, "run_sql")[0]
    assert "does not exist" in sql_result or "Table with name" in sql_result, sql_result
    # A cross-referential search does not reach it either
    search_result = json.loads(tool_results(provider, "search")[0])
    assert [r["id"] for r in search_result["results"]] == ["countries"]
    assert all(r["rows"] == [] for r in search_result["results"])

    # Nothing coming from the referential was ever handed to the model: no row of it, no name, no column.
    # Its identifier only appears where the model itself wrote it, in its own tool calls.
    # "team-paris" is left out: the search endpoint echoes the term the model asked for, which is its own text
    answers = json.dumps(tool_results(provider), default=str)
    for leak in ("team-berlin", "Secret owners", "owner"):
        assert leak not in answers, leak


def test_confidential_referential_when_allowed(env, llm):
    confidential(env)
    configure(env, allow_confidential=True)
    assert env.get("/api/assistant").json()["confidential"] is True
    provider = llm(tool_call("query_rows", id="secrets", limit=5), "FR belongs to team-paris.")
    _, events = ask(env, "Who owns FR?")
    assert next(e for e in events if e["type"] == "step")["ok"]
    assert "team-paris" in tool_results(provider, "query_rows")[0]


def test_confidential_guard_blocks_the_admin_definition_too(env, llm):
    """An administrator sees every definition in the interface; the assistant must not, for a confidential one."""
    refresh(env, "countries")
    confidential(env)
    configure(env, allow_confidential=False, allow_writes=True)
    provider = llm(tool_call("get_definition", id="secrets"), tool_call("list_users"), "Nothing to report.")
    _, events = ask(env, "Show me the definition of secrets")
    step = next(e for e in events if e["type"] == "step")
    assert not step["ok"] and step["summary"] == "confidential referential"
    # The definition was never read: nothing of it reached the model
    answers = json.dumps(tool_results(provider), default=str)
    for leak in ("Secret owners", "team-berlin", "raw/*.csv"):
        assert leak not in answers, leak


def test_signed_header_cannot_be_forged(env):
    """The restriction header of the assistant is signed: a browser cannot send one to widen its rights."""
    configure(env, allow_confidential=False)
    r = env.get("/api/referentials", headers={"x-refexposer-agent": "1.deadbeef"})
    assert r.status_code == 401
    r = env.get("/api/referentials", headers={"x-refexposer-agent": "nonsense"})
    assert r.status_code == 401


def test_rate_limited(env, llm):
    configure(env, rate_per_minute=1)
    llm("first", "second")
    conv, _ = ask(env, "one", agent=False)
    r = env.post(f"/api/assistant/conversations/{conv}/messages", json={"content": "two", "agent": False})
    assert r.status_code == 429


def test_connection_test_is_admin_only(env, llm):
    configure(env)
    llm()
    assert env.post("/api/assistant/test").json() == {"ok": True, "models": ["test-model"], "model_found": True}
    create_user(env, "plain", role="user")
    plain = new_client(env, "plain", USER_PASSWORD)
    assert plain.post("/api/assistant/test").status_code == 403


# --------------------------------------------------------------------------- connector configuration

def test_api_key_is_optional_and_never_returned(env):
    configure(env)
    assistant = env.get("/api/admin/settings").json()["assistant"]
    assert assistant["api_key"] == "" and assistant["api_key_set"] is False

    configure(env, api_key="sk-secret")
    assistant = env.get("/api/admin/settings").json()["assistant"]
    assert assistant["api_key"] == "" and assistant["api_key_set"] is True
    # Saving without a value keeps the stored key
    configure(env, api_key="")
    assert env.get("/api/admin/settings").json()["assistant"]["api_key_set"] is True


def test_extra_headers_are_stored_and_sent(env):
    configure(env, headers={"X-Api-Key": "k-123", "X-Tenant": "soc"})
    assert env.get("/api/admin/settings").json()["assistant"]["headers"] == {"X-Api-Key": "k-123", "X-Tenant": "soc"}

    from app import appsettings
    from app.db import session_factory
    from app.llm.provider import build
    from app.settings import get_settings

    with session_factory()() as db:
        cfg = appsettings.load(db, "assistant")
        provider = build(cfg, get_settings(), appsettings.load(db, "proxy"))
    sent = provider._headers()
    assert sent["X-Api-Key"] == "k-123" and sent["X-Tenant"] == "soc"
    assert sent["Content-Type"] == "application/json"
    assert "Authorization" not in sent  # no API key configured


def test_refused_headers(env):
    for headers, reason in (
        ({"X-Bad\nName": "v"}, "invalid header name"),
        ({"X-Ok": "line\r\nInjected: yes"}, "invalid value"),
        ({"Host": "elsewhere"}, "set by the client itself"),
        ({f"H{i}": "v" for i in range(21)}, "at most 20 headers"),
    ):
        r = env.put("/api/admin/settings/assistant", json={
            "enabled": True, "base_url": "http://ollama.internal:11434", "model": "test-model", "headers": headers})
        assert r.status_code == 422, (headers, r.text)
        assert reason in r.text, (headers, r.text)


def test_model_list_comes_from_the_server(env, llm):
    """The interface offers the models the server declares, once the connection worked."""
    configure(env, model="")
    llm()
    assert env.get("/api/assistant").json()["configured"] is False  # no model chosen yet
    answer = env.post("/api/assistant/test").json()
    assert answer["ok"] and answer["models"] == ["test-model"]
    configure(env, model="test-model")
    assert env.get("/api/assistant").json()["configured"] is True


def test_the_root_certificate_of_the_proxy_settings_is_used(env):
    """The connector builds its TLS context from the company authorities configured once, in Network & proxy."""
    import ssl

    from app import appsettings, network
    from app.db import session_factory
    from app.settings import get_settings

    pem = (env.data_dir / "ca.pem")
    configure(env)
    with session_factory()() as db:
        proxy = appsettings.load(db, "proxy")
        assert proxy.ca_bundle == ""
        context = network.ssl_context(get_settings(), proxy, verify_tls=True)
    assert isinstance(context, ssl.SSLContext) and context.verify_mode == ssl.CERT_REQUIRED
    assert not pem.exists()  # nothing of its own to configure: the bundle of the proxy settings is the one used


def test_actions_of_the_agent_are_audited_as_such(env, llm):
    """Each action the agent takes is in the audit log under the account, marked as made by the agent."""
    refresh(env, "countries")
    configure(env, allow_writes=True)
    llm(tool_call("refresh_referential", id="countries"), "The update is running.")
    _, events = ask(env, "Update countries")
    assert next(e for e in events if e["type"] == "step")["ok"]

    rows = env.get("/api/admin/audit", params={"limit": 50}).json()["rows"]
    asked = [e for e in rows if e["action"] == "assistant.ask"]
    refreshed = [e for e in rows if e["action"] == "referential.refresh" and (e.get("detail") or {}).get("via")]
    assert asked and asked[0]["username"] == "admin"
    assert refreshed and refreshed[0]["detail"]["via"] == "agent"
    assert refreshed[0]["username"] == "admin"


def test_one_question_stores_exactly_one_pair(env, llm):
    """The first question of a new conversation is stored once, whatever the interface shows while it streams."""
    configure(env)
    llm("Only once.")
    conv, _ = ask(env, "first question", agent=False)
    messages = env.get(f"/api/assistant/conversations/{conv}").json()["messages"]
    assert [(m["role"], m["content"]) for m in messages] == [
        ("user", "first question"), ("assistant", "Only once.")]
    # And reading the conversation while the answer is being written shows the question once
    assert sum(1 for m in messages if m["role"] == "user" and m["content"] == "first question") == 1
