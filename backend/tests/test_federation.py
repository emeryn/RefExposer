"""Synchronization between instances: a replica reads the catalog of a peer with the token of a service account, creates
the referentials (definition, quality rules, SQL transformation kept) and replicates their published data."""

import socket
import threading
import time

import pytest

from conftest import new_client, refresh


@pytest.fixture()
def served(env):
    """The application also served over HTTP: it plays the peer of itself (replicas get a prefix)."""
    import uvicorn

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(env.app_, host="127.0.0.1", port=port, lifespan="off", log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(5)


def wait_idle(client, ref_id, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/api/referentials/{ref_id}")
        if r.status_code == 200 and not r.json()["current_run"]:
            return client.get(f"/api/referentials/{ref_id}/runs", params={"limit": 1}).json()[0]
        time.sleep(0.1)
    raise TimeoutError(ref_id)


def service_token(env, ref_ids: list[str]) -> str:
    group = env.post("/api/admin/groups", json={"name": "Replicas"}).json()
    svc = env.post("/api/admin/service-accounts", json={"username": "replica", "group_ids": [group["id"]]}).json()
    for rid in ref_ids:
        assert env.post("/api/admin/grants", json={"referential_id": rid, "group_id": group["id"], "level": "read"}).status_code in (200, 201)
    return env.post(f"/api/admin/users/{svc['id']}/tokens", json={"name": "replica"}).json()["token"]


def test_replica_of_a_peer(env, served):
    # Upstream referential: key, SQL transformation, quality rule, index
    raw = env.data_dir / "iocs" / "raw"
    raw.mkdir(parents=True)
    (raw / "i.csv").write_text("ioc,family\n1.1.1.1,emotet\n2.2.2.2,qakbot\n")
    cfg = {"id": "iocs", "name": "IOCs", "description": "Shared", "category": "Threat intel", "format": "csv", "key": "ioc",
           "source": {"type": "local", "path": "raw/*.csv"}, "transform": "SELECT *, upper(family) AS fam FROM {source}",
           "quality": {"rules": [{"column": "ioc", "check": "not_null", "severity": "error"}]}, "storage": {"indexes": ["family"]}}
    assert env.post("/api/admin/referentials", json={"config": cfg, "pull": False}).status_code == 201
    assert refresh(env, "iocs")["status"] == "success"
    refresh(env, "words")
    token = service_token(env, ["iocs"])

    # The catalog only lists what the service account may read
    api_headers = {"Authorization": f"Bearer {token}"}
    api = new_client(env)
    api.headers.pop("X-Requested-With")
    doc = api.get("/api/federation/catalog", headers=api_headers).json()
    assert [d["id"] for d in doc["datasets"]] == ["iocs"] and doc["instance"]["account"] == "replica"
    entry = doc["datasets"][0]
    assert entry["transform"] and entry["quality"]["rules"][0]["check"] == "not_null" and entry["data"]["format"] == "parquet"

    # Connection test, then the peer
    r = env.post("/api/admin/federation/catalog", json={"url": served, "token": "rfx_wrong"})
    assert r.status_code == 400 and "refused the token" in r.json()["detail"], r.text
    r = env.post("/api/admin/federation/catalog", json={"url": served, "token": token})
    assert r.status_code == 200 and r.json()["datasets"][0]["quality_rules"] == 1, r.text
    r = env.put("/api/admin/federation/peers/prod", json={"url": served + "/", "token": token, "prefix": "prod-", "schedule": "*/15 * * * *"})
    assert r.status_code == 200, r.text
    secrets = {s["name"]: s for s in env.get("/api/admin/secrets").json()}
    assert secrets["peer-prod"]["hosts"] == ["127.0.0.1"]  # the token only goes to the peer

    # 1. First synchronization: the replica is created and filled
    report = env.post("/api/admin/federation/peers/prod/sync").json()
    assert report["created"] == ["prod-iocs"] and report["updates_started"] == ["prod-iocs"], report
    run = wait_idle(env, "prod-iocs")
    assert run["status"] == "success" and run["rows"] == 2, run
    info = env.get("/api/referentials/prod-iocs").json()
    assert info["federation"]["peer"] == "prod" and info["federation"]["remote_id"] == "iocs"
    assert info["federation"]["transform"] == cfg["transform"] and info["category"] == "Threat intel"
    assert info["config"]["quality"]["rules"][0]["check"] == "not_null" and info["key"] == "ioc"
    assert info["quality"]["status"] == "ok" and "family" in info["indexes"]
    assert env.get("/api/referentials/prod-iocs/lookup/2.2.2.2").json()["fam"] == "QAKBOT"  # transformed upstream

    # 2. Nothing changed upstream: nothing to do
    report = env.post("/api/admin/federation/peers/prod/sync").json()
    assert report["unchanged"] == ["prod-iocs"] and report["updates_started"] == [], report

    # 3. New version upstream (and a changed description): definition and data follow
    (raw / "i.csv").write_text("ioc,family\n1.1.1.1,emotet\n2.2.2.2,qakbot\n3.3.3.3,lumma\n")
    cfg["description"] = "Shared, v2"
    assert env.put("/api/admin/referentials/iocs", json={"config": cfg, "pull": False}).status_code == 200
    assert refresh(env, "iocs")["rows"] == 3
    report = env.post("/api/admin/federation/peers/prod/sync").json()
    assert report["updated"] == ["prod-iocs"] and report["updates_started"] == ["prod-iocs"], report
    assert wait_idle(env, "prod-iocs")["rows"] == 3
    assert env.get("/api/referentials/prod-iocs").json()["description"] == "Shared, v2"

    # A replica is not edited here
    r = env.put("/api/admin/referentials/prod-iocs", json={"config": {**cfg, "id": "prod-iocs"}, "pull": False})
    assert r.status_code == 409 and "peer 'prod'" in r.json()["detail"]

    # 4. No longer shared: the replica stays, reported missing
    group = next(g for g in env.get("/api/admin/groups").json() if g["name"] == "Replicas")
    for g in env.get("/api/admin/grants", params={"group_id": group["id"]}).json():
        env.delete(f"/api/admin/grants/{g['id']}")
    report = env.post("/api/admin/federation/peers/prod/sync").json()
    assert report["missing"] == ["iocs"] and report["status"] == "warning", report
    peers = env.get("/api/admin/federation").json()["peers"]
    assert peers[0]["replicas"][0]["missing"] is True and peers[0]["replicas"][0]["rows"] == 3
    assert env.get("/api/referentials/prod-iocs").json()["row_count"] == 3

    # 5. Removing the peer with its replicas
    r = env.delete("/api/admin/federation/peers/prod", params={"remove_replicas": True})
    assert r.status_code == 200 and r.json()["replicas_removed"] == ["prod-iocs"], r.text
    assert env.get("/api/referentials/prod-iocs").status_code == 404
    assert "peer-prod" not in {s["name"] for s in env.get("/api/admin/secrets").json()}


def test_peer_settings_checks(env):
    r = env.put("/api/admin/federation/peers/x", json={"url": "ftp://nope"})
    assert r.status_code == 422
    r = env.put("/api/admin/federation/peers/x", json={"url": "https://peer.example"})
    assert r.status_code == 422 and "API token" in r.json()["detail"]
    r = env.post("/api/admin/federation/peers/unknown/sync")
    assert r.status_code == 400
    # An address carrying a query or a fragment would send the call — and the API token with it — to a path
    # other than the catalog once the path is appended
    for bad in ("https://peer.example/?x=1", "https://peer.example/#"):
        r = env.put("/api/admin/federation/peers/x", json={"url": bad, "token": "t"})
        assert r.status_code == 422, bad


def test_the_catalog_address_is_built_from_the_parts_of_the_peer_address():
    from app.federation import CATALOG_PATH, catalog_url

    assert catalog_url("https://peer.example") == "https://peer.example" + CATALOG_PATH
    assert catalog_url("https://peer.example/") == "https://peer.example" + CATALOG_PATH
    assert catalog_url("https://peer.example/refex/") == "https://peer.example/refex" + CATALOG_PATH
    for bad in ("https://peer.example/#", "https://peer.example/?a=b"):
        with pytest.raises(ValueError):
            catalog_url(bad)
