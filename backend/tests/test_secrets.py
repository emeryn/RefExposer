import http.server
import json
import threading

import pytest

from conftest import CSV, refresh

VALUE = "sk-live-9f8e7d6c5b4a"


@pytest.fixture()
def api_server():
    """HTTP source recording the headers it receives."""
    seen: list[dict[str, str]] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            seen.append(dict(self.headers))
            body = CSV.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/csv")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/countries.csv", seen
    srv.shutdown()


def secret(env, name="vendor-key", value=VALUE, **extra):
    r = env.post("/api/admin/secrets", json={"name": name, "value": value, **extra})
    assert r.status_code == 201, r.text
    return r.json()


def define(env, ref_id, source, status=201):
    r = env.post("/api/admin/referentials", json={"config": {"id": ref_id, "name": ref_id, "format": "csv", "key": "code",
                                                             "source": source}, "pull": False})
    assert r.status_code == status, r.text
    return r


def test_secret_sent_but_never_returned(env, api_server):
    url, seen = api_server
    s = secret(env, description="Vendor API", hosts=["127.0.0.1"])
    assert s["reference"] == "${secret:vendor-key}" and "value" not in s
    define(env, "vendor", {"type": "http", "urls": [url], "headers": {"X-Api-Key": "${secret:vendor-key}"}})
    run = refresh(env, "vendor")
    assert run["status"] == "success", run
    assert seen[-1]["X-Api-Key"] == VALUE

    # Never in the answers of the API: secrets, definition, YAML, detail, runs, audit
    for path in ("/api/admin/secrets", "/api/admin/referentials/vendor", "/api/admin/referentials/vendor/yaml",
                 "/api/referentials/vendor", "/api/referentials/vendor/runs", "/api/admin/audit"):
        assert VALUE not in env.get(path).text, path
    definition = env.get("/api/admin/referentials/vendor").json()
    assert definition["config"]["source"]["headers"] == {"X-Api-Key": "${secret:vendor-key}"}
    assert env.get("/api/admin/secrets").json()[0]["used_by"] == ["vendor"]

    # New value: used by the next update, still write-only
    assert env.patch("/api/admin/secrets/vendor-key", json={"value": "sk-rotated"}).status_code == 200
    assert refresh(env, "vendor", force=True)["status"] == "success"
    assert seen[-1]["X-Api-Key"] == "sk-rotated"

    # Used: not deleted unless forced
    assert env.delete("/api/admin/secrets/vendor-key").status_code == 409
    assert env.delete("/api/admin/secrets/vendor-key", params={"force": True}).status_code == 200
    run = refresh(env, "vendor", force=True)
    assert run["status"] == "error" and "unknown secret 'vendor-key'" in run["message"]


def test_secret_restricted_to_its_hosts(env, api_server):
    url, seen = api_server
    secret(env, hosts=["api.vendor.example", "*.vendor.example"])
    define(env, "vendor", {"type": "http", "urls": [url], "headers": {"Authorization": "Bearer ${secret:vendor-key}"}})
    run = refresh(env, "vendor")
    assert run["status"] == "error" and "not allowed for 127.0.0.1" in run["message"], run
    assert not seen  # nothing sent
    # Preview: same rule
    r = env.post("/api/admin/referentials/preview", json={"source": {"type": "http", "urls": [url],
                                                                     "headers": {"X-Key": "${secret:vendor-key}"}}})
    assert r.status_code == 400 and "not allowed" in r.text and not seen


def test_references_checked(env, api_server):
    url, _ = api_server
    r = define(env, "vendor", {"type": "http", "urls": [url], "headers": {"X-Api-Key": "${secret:nope}"}}, status=422)
    assert "unknown secret(s): nope" in r.text
    secret(env)
    r = define(env, "vendor", {"type": "http", "urls": [url + "?key=${secret:vendor-key}"]}, status=422)
    assert "only allowed in header values" in r.text
    assert env.post("/api/admin/secrets", json={"name": "bad name", "value": "x"}).status_code == 422
    assert env.post("/api/admin/secrets", json={"name": "vendor-key", "value": "x"}).status_code == 409


def test_literal_credentials_masked_and_kept(env, api_server):
    url, seen = api_server
    define(env, "vendor", {"type": "http", "urls": [url], "headers": {"X-Token": "literal-123", "Accept": "text/csv"},
                           "basic_auth": "user:pass"})
    d = env.get("/api/admin/referentials/vendor").json()
    assert d["config"]["source"]["headers"] == {"X-Token": "********", "Accept": "********"}
    assert d["config"]["source"]["basic_auth"] == "********"
    assert "literal-123" not in env.get("/api/admin/referentials/vendor/yaml").text
    assert "literal-123" not in json.dumps(env.get("/api/admin/audit").json())

    # The definition sent back unchanged keeps the stored values; a changed header is replaced
    config = {**d["config"], "description": "edited"}
    config["source"] = {**config["source"], "headers": {"X-Token": "********", "Accept": "application/csv"}}
    r = env.put("/api/admin/referentials/vendor", json={"config": config, "pull": False})
    assert r.status_code == 200, r.text
    assert refresh(env, "vendor")["status"] == "success"
    assert seen[-1]["X-Token"] == "literal-123" and seen[-1]["Accept"] == "application/csv"
    assert seen[-1]["Authorization"].startswith("Basic ")

    # The preview of the edited referential uses the stored values too
    r = env.post("/api/admin/referentials/preview", json={"source": {"type": "http", "urls": [url], "headers": {"X-Token": "********"}},
                                                          "referential_id": "vendor", "refresh": True})
    assert r.status_code == 200, r.text
    assert seen[-1]["X-Token"] == "literal-123"
    # A masked value with nothing stored is refused
    r = env.post("/api/admin/referentials/preview", json={"source": {"type": "http", "urls": [url], "headers": {"X-New": "********"}},
                                                          "referential_id": "vendor"})
    assert r.status_code == 400
