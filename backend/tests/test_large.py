import json

from conftest import refresh


def make_large(env, threshold=100):
    env.app_.state.service.settings.large_rows = threshold


def test_sorted_layout_and_indexes(env):
    run = refresh(env, "pdns")
    assert run["status"] == "success" and run["rows"] == 3002
    root = env.data_dir / "pdns"
    assert (root / "current.parquet").exists() and (root / "current.by_rdata.parquet").exists()
    assert any("Index on 'rdata'" in log["msg"] for log in run["logs"])
    # Data is physically sorted on rrname
    rows = env.get("/api/referentials/pdns/rows", params={"limit": 3}).json()["rows"]
    assert [r["rrname"] for r in rows] == sorted(r["rrname"] for r in rows)
    # keep_previous: false
    assert refresh(env, "pdns", force=True)["status"] == "success"
    assert not (root / "previous.parquet").exists()
    detail = env.get("/api/referentials/pdns").json()
    assert detail["sort_by"] == ["rrname"] and detail["indexes"] == ["rdata"] and detail["index_views"] == {"rdata": "pdns__by_rdata"}


def test_large_mode_guardrails(env):
    refresh(env, "pdns")
    make_large(env)
    detail = env.get("/api/referentials/pdns").json()
    assert detail["large"] is True

    # Exact and prefix lookups on the sort column / index
    r = env.get("/api/referentials/pdns/rows", params={"rrname": "www.circl.lu"}).json()
    assert r["total"] == 1 and r["rows"][0]["rdata"] == "185.194.93.14"
    r = env.get("/api/referentials/pdns/rows", params={"rdata": "mx.circl.lu"}).json()
    assert r["total"] == 1 and r["rows"][0]["rrname"] == "mail.circl.lu"
    r = env.get("/api/referentials/pdns/rows", params={"rrname__startswith": "host12", "sort": "-count"}).json()
    assert r["total"] == 111 and r["rows"][0]["rrname"] == "host1299.example4.com"
    r = env.get("/api/referentials/pdns/rows", params={"rrname__startswith": "HOST12"}).json()
    assert r["total"] == 0  # case-sensitive on sorted columns
    r = env.get("/api/referentials/pdns/rows", params={"q": "www.circ"}).json()
    assert r["total"] == 1  # full-text becomes a prefix search on the indexed search column
    r = env.get("/api/referentials/pdns/rows", params={"rdata__in": json.dumps(["mx.circl.lu", "185.194.93.14"])}).json()
    assert r["total"] == 2

    # Unindexed filter: allowed, but the total is not computed (would scan everything)
    r = env.get("/api/referentials/pdns/rows", params={"rrtype": "MX"}).json()
    assert r["total"] is None and r["rows"][0]["rrname"] == "mail.circl.lu"
    # Sorting the whole volume on another column is refused, sorting on the sort column is fine
    assert env.get("/api/referentials/pdns/rows", params={"sort": "-count"}).status_code == 400
    assert env.get("/api/referentials/pdns/rows", params={"sort": "rrname"}).status_code == 200
    # Facets disabled, column statistics sampled, heavy downloads refused
    f = env.get("/api/referentials/pdns/facets").json()
    assert f["facets"] == [] and "disabled" in f["disabled"]
    assert env.get("/api/referentials/pdns/columns/count/stats").json()["sampled"] is True
    assert env.get("/api/referentials/pdns/download/csv").status_code == 413
    assert env.get("/api/referentials/pdns/download/parquet").status_code == 200
    formats = {f["format"]: f for f in env.get("/api/referentials/pdns/downloads").json()["formats"]}
    assert formats["csv"]["unavailable_reason"] and formats["parquet"]["unavailable_reason"] is None

    # Lookups use the index copy
    assert env.get("/api/referentials/pdns/lookup/www.circl.lu").json()["count"] == 7
    b = env.post("/api/referentials/pdns/lookup", json={"values": ["mx.circl.lu", "1.2.3.4"], "column": "rdata"}).json()
    assert b["found"] == 1 and b["missing"] == ["1.2.3.4"]

    # SQL console: the sorted copy is a table of its own
    r = env.post("/api/sql", json={"sql": "SELECT count(*) FROM pdns__by_rdata WHERE rdata = 'mx.circl.lu'"}).json()
    assert r["rows"] == [[1]]


def test_query_timeout(env):
    refresh(env, "pdns")
    eng = env.app_.state.service.engine
    eng.query_timeout = 0.001
    try:
        r = env.get("/api/referentials/pdns/rows", params={"rrtype": "A", "q": "x"})
        assert r.status_code in (200, 408)  # tiny dataset may finish before the timer
        from app.engine import SqlTimeout
        import pytest
        with pytest.raises(SqlTimeout):
            eng.fetch("SELECT count(*) FROM range(3000000000) a")
    finally:
        eng.query_timeout = 60
