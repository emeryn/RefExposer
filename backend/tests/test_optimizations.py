"""Debounced publication of internal referentials, database-side row listing, capped global search,
single-scan facets and change tracking."""

import time

from conftest import refresh

SCHEMA = {
    "id": "vendors", "name": "Vendors", "key": "code",
    "columns": [{"name": "code", "type": "text", "required": True}, {"name": "label", "type": "text"}, {"name": "tags", "type": "list"}],
}


def wait_idle(client, ref_id, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not client.get(f"/api/referentials/{ref_id}").json()["current_run"]:
            return
        time.sleep(0.05)
    raise TimeoutError(ref_id)


def test_internal_edits_are_published_once(env):
    service = env.app_.state.service
    assert env.post("/api/internal-referentials", json=SCHEMA).status_code == 201
    wait_idle(env, "vendors")
    service.settings.internal_publish_delay = 1.5
    for i in range(5):
        assert env.post("/api/referentials/vendors/records", json={"code": f"V{i}", "label": f"Vendor {i}"}).status_code == 201
    # Waiting for the edits to settle: shown as a queued run
    assert env.get("/api/referentials/vendors").json()["current_run"]["status"] == "queued"
    wait_idle(env, "vendors")
    runs = env.get("/api/referentials/vendors/runs").json()
    assert [r["trigger"] for r in runs] == ["edit", "edit"]  # creation, then one publication for the five edits
    assert env.get("/api/referentials/vendors").json()["row_count"] == 5

    # A waiting publication can be cancelled, and a deleted referential is not published
    env.post("/api/referentials/vendors/records", json={"code": "V9"})
    assert env.delete("/api/referentials/vendors/run").status_code == 200
    assert not env.get("/api/referentials/vendors").json()["current_run"]
    env.post("/api/referentials/vendors/records", json={"code": "V10"})
    assert env.delete("/api/internal-referentials/vendors").status_code == 200
    time.sleep(2)
    assert "vendors" not in service.active


def test_list_records_search_and_pages(env):
    assert env.post("/api/internal-referentials", json=SCHEMA).status_code == 201
    rows = [{"code": f"K{i:03}", "label": f"Label {i}"} for i in range(120)]
    rows += [{"code": "ACME", "label": "Acme Corp", "tags": ["cloud", "eu"]}, {"code": "SOC", "label": 'Société "Générale"'}]
    assert env.post("/api/referentials/vendors/records/_bulk", json={"upsert": rows}).json()["created"] == 122

    page = env.get("/api/referentials/vendors/records", params={"limit": 50, "offset": 100}).json()
    assert page["total"] == 122 and len(page["records"]) == 22 and page["records"][0]["_key"] == "K099"
    q = lambda text: env.get("/api/referentials/vendors/records", params={"q": text}).json()  # noqa: E731
    assert [r["_key"] for r in q("acme")["records"]] == ["ACME"]
    assert [r["_key"] for r in q("cloud")["records"]] == ["ACME"]  # list values
    assert q("label")["total"] == 120  # column names in the stored JSON do not match by themselves
    assert q("code")["total"] == 0
    assert [r["_key"] for r in q("société")["records"]] == ["SOC"]  # accents and quotes: exact check only
    assert [r["_key"] for r in q('"générale"')["records"]] == ["SOC"]
    assert q("K11")["total"] == 10

    # Bulk updates only load the rows they touch, and still detect existing keys
    r = env.post("/api/referentials/vendors/records/_bulk", json={"upsert": [{"code": "ACME", "label": "Acme"}], "delete": ["K000"]}).json()
    assert r == {"created": 0, "updated": 1, "unchanged": 0, "deleted": 1}
    assert env.post("/api/referentials/vendors/records", json={"code": "K001"}).status_code == 409


def test_global_search_is_capped(env):
    refresh(env, "pdns")
    res = {r["id"]: r for r in env.get("/api/search", params={"q": "host1", "limit": 5}).json()["results"]}["pdns"]
    assert res["total"] == 1000 and res["total_capped"] is True and len(res["rows"]) == 5
    # Exact key match found through the sorted key even outside the first candidates
    res = {r["id"]: r for r in env.get("/api/search", params={"q": "HOST1999.EXAMPLE4.COM"}).json()["results"]}["pdns"]
    assert res["rows"][0]["rrname"] == "host1999.example4.com" and res["total_capped"] is False


def test_facets_cached_per_version(env):
    refresh(env, "countries")
    first = env.get("/api/referentials/countries/facets", params={"population__gte": "50000000"}).json()
    again = env.get("/api/referentials/countries/facets", params={"population__gte": "50000000"}).json()
    assert first["total"] == again["total"] == 3
    assert {**first, "elapsed_ms": 0} == {**again, "elapsed_ms": 0}
    # The population range ignores its own filter; the label facet uses it
    pop = next(r for r in first["ranges"] if r["column"] == "population")
    assert pop["min"] == "11700000"
    label = next(f for f in first["facets"] if f["column"] == "label")
    assert len(label["values"]) == 3
    # A new version gives fresh results
    (env.data_dir / "countries" / "raw" / "a.csv").write_text(
        "code,label,population,created\nFR,France,68000000,1958-10-04\nNL,Pays-Bas,17900000,1815-03-16\n"
        "DE,Allemagne,84000000,1949-05-23\n", encoding="utf-8")
    refresh(env, "countries")
    assert env.get("/api/referentials/countries/facets", params={"population__gte": "50000000"}).json()["total"] == 2
