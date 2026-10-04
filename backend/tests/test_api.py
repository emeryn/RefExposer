import gzip

from conftest import CSV, refresh, write


def test_catalog_and_config_errors(env):
    refs = {r["id"]: r for r in env.get("/api/referentials").json()}
    assert set(refs) == {"countries", "words", "nested", "remote", "pdns"}
    assert refs["countries"]["health"] == "empty"
    errors = env.get("/api/system").json()["config_errors"]
    assert len(errors) == 1 and "broken" in errors[0]["file"]


def test_local_csv_pipeline(env):
    run = refresh(env, "countries")
    assert run["status"] == "success", run
    assert run["rows"] == 5

    detail = env.get("/api/referentials/countries").json()
    assert detail["health"] == "ok"
    assert detail["key_unique"] is True
    assert {c["name"] for c in detail["columns"]} == {"code", "label", "population", "created"}
    assert len(detail["profile"]) == 4

    r = env.get("/api/referentials/countries/rows", params={"population__gte": "50000000", "sort": "-population"}).json()
    assert [x["code"] for x in r["rows"]] == ["DE", "FR", "IT"] and r["total"] == 3
    r = env.get("/api/referentials/countries/rows", params={"q": "royaume", "columns": "code,label"}).json()
    assert r["rows"] == [{"code": "BE", "label": "Belgique, royaume"}]
    r = env.get("/api/referentials/countries/rows", params={"created__lt": "1950-01-01"}).json()
    assert r["total"] == 3
    assert env.get("/api/referentials/countries/rows", params={"nope": "1"}).status_code == 400
    assert env.get("/api/referentials/countries/rows", params={"population__gt": "abc"}).status_code == 400

    assert env.get("/api/referentials/countries/lookup/FR").json()["label"] == "France"
    assert env.get("/api/referentials/countries/lookup/XX").status_code == 404
    batch = env.post("/api/referentials/countries/lookup", json={"values": ["FR", "XX", "IT"]}).json()
    assert batch["found"] == 2 and batch["missing"] == ["XX"] and batch["results"]["IT"]["label"] == "Italie"

    stats = env.get("/api/referentials/countries/columns/population/stats").json()
    assert stats["kind"] == "number" and stats["distinct"] == 5 and sum(b["count"] for b in stats["histogram"]) == 5
    stats = env.get("/api/referentials/countries/columns/created/stats").json()
    assert stats["kind"] == "temporal" and stats["histogram"]

    exp = env.get("/api/referentials/countries/export", params={"format": "csv", "code__in": "FR,DE", "sort": "code"})
    assert exp.status_code == 200 and exp.text.splitlines()[1].startswith("DE,")
    exp = env.get("/api/referentials/countries/export", params={"format": "json"})
    assert len(exp.json()) == 5
    exp = env.get("/api/referentials/countries/export", params={"format": "parquet"})
    assert exp.content[:4] == b"PAR1"

    # Unchanged source -> no rebuild
    assert refresh(env, "countries")["status"] == "unchanged"

    # New version: one added, one removed, one modified
    path = env.data_dir / "countries" / "raw" / "a.csv"
    write(path, CSV.replace("IT,Italie", "IT,Italia").replace("ES,Espagne,48000000,1978-12-29\n", "") + "PT,Portugal,10300000,1976-04-25\n")
    run = refresh(env, "countries")
    assert run["status"] == "success" and run["changes"] == {"added": 1, "removed": 1, "modified": 1}
    added = env.get("/api/referentials/countries/changes", params={"kind": "added"}).json()
    assert added["rows"][0]["code"] == "PT"
    removed = env.get("/api/referentials/countries/changes", params={"kind": "removed"}).json()
    assert removed["rows"][0]["code"] == "ES"
    modified = env.get("/api/referentials/countries/changes", params={"kind": "modified"}).json()
    assert modified["rows"] == [{"key": "IT", "changes": [{"column": "label", "before": "Italie", "after": "Italia"}]}]

    # Validation guard: losing > 50% rows is rejected and the previous version kept
    write(path, "code,label,population,created\nFR,France,1,2000-01-01\n")
    run = refresh(env, "countries")
    assert run["status"] == "rejected"
    detail = env.get("/api/referentials/countries").json()
    assert detail["row_count"] == 5 and detail["health"] == "error" and detail["has_data"]

    activity = env.get("/api/activity").json()
    assert activity[0]["ref_id"] == "countries" and activity[0]["ref_name"] == "Pays"


def test_txt_and_json_formats(env):
    assert refresh(env, "words")["status"] == "success"
    rows = env.get("/api/referentials/words/rows", params={"sort": "value"}).json()["rows"]
    assert [r["value"] for r in rows] == ["alpha", "beta", "gamma"]

    assert refresh(env, "nested")["status"] == "success"
    rows = env.get("/api/referentials/nested/rows", params={"sort": "id"}).json()
    assert rows["rows"][0] == {"id": 1, "name": "un", "lvl": 2}


def test_http_source_and_conditional_download(env):
    run = refresh(env, "remote")
    assert run["status"] == "success" and run["rows"] == 5
    assert (env.data_dir / "remote" / "raw" / "remote.csv.gz").exists()  # kept compressed, read by DuckDB
    second = refresh(env, "remote")
    assert second["status"] == "unchanged"
    assert any("304" in log["msg"] for log in second["logs"])
    with gzip.open(env.www / "remote.csv.gz", "wt", encoding="utf-8") as f:
        f.write(CSV + "NL,Pays-Bas,17900000,1815-03-16\n")
    assert refresh(env, "remote", force=True)["rows"] == 6


def test_sql_console_sandbox(env):
    refresh(env, "countries")
    r = env.post("/api/sql", json={"sql": "SELECT code, population FROM countries ORDER BY population DESC LIMIT 2;"}).json()
    assert r["rows"] == [["DE", 84000000], ["FR", 68000000]] and r["columns"][0]["name"] == "code"
    assert env.post("/api/sql", json={"sql": "SELECT * FROM countries", "max_rows": 2}).json()["truncated"] is True
    schema = env.get("/api/sql/schema").json()
    assert schema[0]["table"] == "countries"
    for bad in [
        "DROP VIEW countries",
        "SELECT 1; SELECT 2",
        "SELECT * FROM read_csv('/etc/passwd')",
        "COPY countries TO '/tmp/x.csv'",
        "ATTACH '/tmp/x.db'",
        "SET enable_external_access = true",
        "INSTALL httpfs",
    ]:
        assert env.post("/api/sql", json={"sql": bad}).status_code == 400, bad


def test_global_search(env):
    refresh(env, "countries")
    res = env.get("/api/search", params={"q": "fr"}).json()["results"]
    assert res[0]["id"] == "countries" and res[0]["rows"][0]["code"] == "FR"
