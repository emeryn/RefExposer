import json

import pytest

from conftest import create_user, new_client


def service_token(env, username="agent"):
    r = env.post("/api/admin/service-accounts", json={"username": username, "display_name": "AI agent"})
    assert r.status_code == 201, r.text
    uid = r.json()["id"]
    r = env.post(f"/api/admin/users/{uid}/tokens", json={"name": "mcp"})
    assert r.status_code == 201, r.text
    return uid, r.json()["token"]


def enable(env, account_ids, read_only=False):
    r = env.put("/api/admin/settings/mcp", json={"enabled": True, "account_ids": account_ids, "read_only": read_only})
    assert r.status_code == 200, r.text


class Mcp:
    def __init__(self, env, token):
        self.c = new_client(env)
        self.headers = {"Authorization": f"Bearer {token}"}
        self.n = 0

    def raw(self, payload):
        return self.c.post("/api/mcp", json=payload, headers=self.headers)

    def rpc(self, method, params=None):
        self.n += 1
        r = self.raw({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params or {}})
        assert r.status_code == 200, r.text
        return r.json()

    def tool(self, tool_name, **args):
        res = self.rpc("tools/call", {"name": tool_name, "arguments": args})["result"]
        text = res["content"][0]["text"]
        try:
            data = json.loads(text)
        except ValueError:
            data = text
        return res["isError"], data


@pytest.fixture()
def agent(env):
    uid, token = service_token(env)
    enable(env, [uid])
    return Mcp(env, token)


def test_disabled_by_default_and_reserved_to_allowed_service_accounts(env):
    uid, token = service_token(env)
    m = Mcp(env, token)
    assert m.raw({"jsonrpc": "2.0", "id": 1, "method": "initialize"}).status_code == 404
    # Only service accounts can be chosen
    admin_id = env.get("/api/auth/me").json()["id"]
    assert env.put("/api/admin/settings/mcp", json={"enabled": True, "account_ids": [admin_id]}).status_code == 422
    other_uid, other_token = service_token(env, "other")
    enable(env, [uid])
    assert Mcp(env, other_token).raw({"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code == 403
    assert new_client(env).post("/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code == 401
    # A personal token of a user is refused
    create_user(env, "bob")
    bob = new_client(env, "bob", "Us3r-Passw0rd!")
    bob_token = bob.post("/api/auth/tokens", json={"name": "t"}, headers={"X-Requested-With": "RefExposer"}).json()["token"]
    assert Mcp(env, bob_token).raw({"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code == 403
    assert m.rpc("ping")["result"] == {}


def test_protocol(agent):
    init = agent.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})
    assert init["result"]["protocolVersion"] == "2025-06-18"
    assert init["result"]["capabilities"] == {"tools": {"listChanged": False}}
    assert agent.raw({"jsonrpc": "2.0", "method": "notifications/initialized"}).status_code == 202
    tools = {t["name"]: t for t in agent.rpc("tools/list")["result"]["tools"]}
    assert {"list_referentials", "create_referential", "create_secret", "discovery_scan", "api_request"} <= set(tools)
    assert tools["delete_referential"]["annotations"]["destructiveHint"] is True
    assert agent.rpc("nope")["error"]["code"] == -32601
    batch = agent.raw([{"jsonrpc": "2.0", "id": "a", "method": "ping"}, {"jsonrpc": "2.0", "method": "notifications/x"}]).json()
    assert batch == [{"jsonrpc": "2.0", "id": "a", "result": {}}]
    assert agent.c.get("/api/mcp", headers=agent.headers).status_code == 405


def test_agent_administers_through_the_server_only(env, agent):
    # The token alone keeps the rights of the account (user): no administration on the REST API
    assert agent.c.get("/api/admin/users", headers=agent.headers).status_code == 403
    # A forged elevation header is refused
    r = agent.c.get("/api/admin/users", headers={**agent.headers, "X-Refexposer-Mcp": "1.deadbeef"})
    assert r.status_code == 401

    err, refs = agent.tool("list_referentials", filter="countries")
    assert not err and [r["id"] for r in refs] == ["countries"]
    err, data = agent.tool("create_secret", name="vendor", value="sk-very-secret", hosts=["api.example.com"])
    assert not err and data["reference"] == "${secret:vendor}"
    err, data = agent.tool("list_secrets")
    assert not err and "sk-very-secret" not in json.dumps(data)

    config = {"name": "Mots", "format": "txt", "source": {"type": "local", "path": "raw/*.txt"}}
    (env.data_dir / "agent-words" / "raw").mkdir(parents=True)
    (env.data_dir / "agent-words" / "raw" / "w.txt").write_text("alpha\nbeta\n", encoding="utf-8")
    err, data = agent.tool("create_referential", id="agent-words", config=config, pull=False)
    assert not err, data
    err, data = agent.tool("refresh_referential", id="agent-words")
    assert not err
    err, data = agent.tool("create_referential", id="agent-words", config=config)
    assert err and data.startswith("HTTP 409")
    err, data = agent.tool("api_request", method="GET", path="/api/admin/groups")
    assert not err and isinstance(data, list)
    err, data = agent.tool("api_request", method="POST", path="/api/auth/tokens", body={"name": "escape"})
    assert err and "not allowed" in data
    err, data = agent.tool("api_endpoints", filter="/admin/secrets")
    assert not err and {"method": "GET", "path": "/api/admin/secrets", "summary": data[0]["summary"]} in data

    # Audit: the service account, through the MCP server
    entries = env.get("/api/admin/audit", params={"limit": 50}).json()["rows"]
    created = [e for e in entries if e["action"] == "referential.create" and e["target"] == "ref:agent-words"]
    assert created and created[0]["username"] == "agent" and created[0]["detail"]["via"] == "mcp"

    # Disabling the server withdraws the powers at once
    env.put("/api/admin/settings/mcp", json={"enabled": False, "account_ids": []})
    assert agent.raw({"jsonrpc": "2.0", "id": 9, "method": "ping"}).status_code == 404


def test_read_only_mode(env):
    uid, token = service_token(env)
    enable(env, [uid], read_only=True)
    m = Mcp(env, token)
    names = {t["name"] for t in m.rpc("tools/list")["result"]["tools"]}
    assert "list_referentials" in names and "run_sql" in names
    assert not names & {"create_referential", "delete_referential", "create_secret", "refresh_referential"}
    assert "error" in m.rpc("tools/call", {"name": "create_secret", "arguments": {"name": "x", "value": "y"}})
    err, data = m.tool("api_request", method="POST", path="/api/system/reload")
    assert err and "read-only" in data
    err, data = m.tool("run_sql", sql="SELECT 1 AS one")
    assert not err, data


# --------------------------------------------------------------------------- data server (/api/mcp/user)

class UserMcp(Mcp):
    def raw(self, payload):
        return self.c.post("/api/mcp/user", json=payload, headers=self.headers)


def personal_token(client):
    r = client.post("/api/auth/tokens", json={"name": "mcp"}, headers={"X-Requested-With": "RefExposer"})
    assert r.status_code in (200, 201), r.text
    return r.json()["token"]


def test_user_data_server(env):
    from conftest import USER_PASSWORD, create_user

    bob = create_user(env, "bob")
    assert env.post("/api/admin/grants", json={"referential_id": "countries", "user_id": bob["id"], "level": "read"}).status_code == 201
    from conftest import refresh

    assert refresh(env, "countries")["status"] == "success"
    assert refresh(env, "words")["status"] == "success"
    token = personal_token(new_client(env, "bob", USER_PASSWORD))
    m = UserMcp(env, token)
    assert m.raw({"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code == 404  # disabled by default

    assert env.put("/api/admin/settings/mcp", json={"users_enabled": True}).status_code == 200
    init = m.rpc("initialize", {"protocolVersion": "2025-06-18"})["result"]
    assert init["serverInfo"]["name"] == "refexposer"
    names = {t["name"] for t in m.rpc("tools/list")["result"]["tools"]}
    assert {"list_referentials", "query_rows", "lookup", "lookup_many", "search", "get_quality", "whoami"} <= names
    assert not names & {"run_sql", "sql_schema", "create_referential", "api_request", "list_users", "create_secret"}
    assert all(t["annotations"]["readOnlyHint"] for t in m.rpc("tools/list")["result"]["tools"])

    # Only what bob may read
    err, refs = m.tool("list_referentials")
    assert not err and [r["id"] for r in refs] == ["countries"]
    err, row = m.tool("lookup", id="countries", value="DE")
    assert not err and row["label"] == "Allemagne"
    err, rows = m.tool("query_rows", id="countries", filters={"population__gt": 60000000}, sort="-population")
    assert not err and [r["code"] for r in rows["rows"]] == ["DE", "FR"]
    err, res = m.tool("lookup_many", id="countries", values=["FR", "XX"])
    assert not err
    err, data = m.tool("lookup", id="words", value="alpha")
    assert err and data.startswith("HTTP 404")
    assert "error" in m.rpc("tools/call", {"name": "run_sql", "arguments": {"sql": "SELECT 1"}})
    err, me = m.tool("whoami")
    assert not err and me["username"] == "bob"
    assert env.get("/api/auth/me").json()["mcp_url"].endswith("/api/mcp/user")

    # No administration through it: no such tool, and a forged elevation header is refused
    assert m.rpc("tools/call", {"name": "create_referential", "arguments": {"id": "x", "config": {}}})["error"]["code"] == -32602
    r = m.c.post("/api/mcp/user", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                 headers={**m.headers, "X-Refexposer-Mcp": "1.forged"})
    assert r.status_code == 401

    # Restricted to some groups
    gid = env.post("/api/admin/groups", json={"name": "Analysts"}).json()["id"]
    assert env.put("/api/admin/settings/mcp", json={"users_enabled": True, "user_groups": ["Analysts"]}).status_code == 200
    assert m.raw({"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code == 403
    assert env.patch(f"/api/admin/groups/{gid}", json={"name": "Analysts", "member_ids": [bob["id"]]}).status_code == 200
    assert m.raw({"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code == 200

    # An administrator (personal token) gets SQL, still no administration tool
    admin = UserMcp(env, personal_token(env))
    names = {t["name"] for t in admin.rpc("tools/list")["result"]["tools"]}
    assert "run_sql" in names and "create_referential" not in names
    err, res = admin.tool("run_sql", sql="SELECT count(*) AS n FROM countries")
    assert not err and res["row_count"] == 1 and "5" in str(res["rows"][0]), res
