from conftest import refresh


def define(env, ref_id, source, **extra):
    r = env.post("/api/admin/referentials", json={"config": {"id": ref_id, "name": ref_id, "format": "csv", "source": source, **extra},
                                                  "pull": False})
    assert r.status_code == 201, r.text


def test_inventory_grouped_by_host(env, http_dir):
    base = f"http://127.0.0.1:{http_dir[1]}"
    assert env.post("/api/admin/secrets", json={"name": "vendor", "value": "sk-endpoint-secret"}).status_code == 201
    define(env, "feed-a", {"type": "http", "urls": [f"{base}/remote.csv.gz"]}, schedule="0 * * * *")
    define(env, "feed-b", {"type": "http", "urls": [f"{base}/remote.csv.gz"], "headers": {"X-Key": "${secret:vendor}"}},
           schedule="0 5 * * *")
    define(env, "feed-c", {"type": "http", "urls": [f"{base}/other.csv?api_key=sk-in-url&limit=10"]}, enabled=False)
    define(env, "gitref", {"type": "git", "repository": "https://git.example.org/team/data.git", "path": "a.csv"})
    assert refresh(env, "feed-a")["status"] == "success"
    assert env.post("/api/admin/notifications", json={"name": "Chat", "type": "webhook", "url": "https://chat.example.com/hook"}).status_code == 201

    inv = env.get("/api/admin/endpoints").json()
    hosts = {h["host"]: h for h in inv["hosts"]}
    local = hosts[f"http://127.0.0.1:{http_dir[1]}"]
    # 'remote' (test configuration) downloads the same file: one URL, three referentials
    assert {"feed-a", "feed-b", "feed-c", "remote"} == set(local["referentials"]) and local["url_count"] == 2
    shared = next(u for u in local["urls"] if u["url"].endswith("/remote.csv.gz"))
    assert shared["shared"] is True and {"feed-a", "feed-b", "remote"} == {r["referential"] for r in shared["referentials"]}
    per_ref = {r["referential"]: r["calls_per_day"] for r in shared["referentials"]}
    assert per_ref["feed-a"] == 24 and per_ref["feed-b"] == 1 and shared["calls_per_day"] == sum(per_ref.values())
    assert shared["last_contact"]  # feed-a was updated
    assert local["secrets"] == ["vendor"] and "header X-Key" in local["auth"]
    masked = next(u for u in local["urls"] if "other.csv" in u["url"])
    assert "sk-in-url" not in masked["url"] and "api_key=***" in masked["url"] and "limit=10" in masked["url"]
    assert masked["referentials"][0]["enabled"] is False and masked["referentials"][0]["calls_per_day"] == 0
    assert hosts["https://git.example.org"]["roles"] == ["git"]
    assert inv["totals"]["shared_urls"] == 1 and inv["totals"]["insecure_hosts"] >= 1
    assert any(o["kind"] == "notification webhook" and o["host"] == "https://chat.example.com" for o in inv["others"])
    assert "countries" in inv["local_sources"]
    text = env.get("/api/admin/endpoints").text
    assert "sk-endpoint-secret" not in text and "sk-in-url" not in text

    csv = env.get("/api/admin/endpoints/export").text.splitlines()
    assert csv[0].startswith("host,url,referential") and sum("remote.csv.gz" in line for line in csv) == 3
    hosts_csv = env.get("/api/admin/endpoints/export", params={"hosts_only": True}).text.splitlines()
    # The first column of each line is compared, not searched for: a substring check on a URL says nothing
    # about which host the line is really about
    exported = {line.split(",")[0] for line in hosts_csv[1:]}
    assert {"https://git.example.org", "https://chat.example.com"} <= exported


def test_reserved_to_administrators(env):
    from conftest import USER_PASSWORD, create_user, new_client

    create_user(env, "bob")
    assert new_client(env, "bob", USER_PASSWORD).get("/api/admin/endpoints").status_code == 403


def test_helpers():
    from app.endpoints import calls_per_day, host_key, safe_url

    assert calls_per_day("*/15 * * * *") == 96 and calls_per_day(None) == 0 and calls_per_day("0 6 * * 1") == round(1 / 7, 2)
    assert host_key("https://API.example.com:443/x") == "https://api.example.com"
    assert host_key("http://h:8080/x") == "http://h:8080"
    assert safe_url("https://u:p@h/x?token=abc&q=1") == "https://***@h/x?token=***&q=1"
