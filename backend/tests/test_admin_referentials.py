import gzip

import time

from conftest import USER_PASSWORD, create_user, new_client, refresh

SEMICOLON_CSV = "code;libelle;montant\nA1;Alpha;10,5\nB2;Beta;20\nC3;Gamma;30\n"


def wait_idle(client, ref_id, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not client.get(f"/api/referentials/{ref_id}").json()["current_run"]:
            return client.get(f"/api/referentials/{ref_id}/runs", params={"limit": 1}).json()[0]
        time.sleep(0.1)
    raise TimeoutError(ref_id)


def test_preview_http_source(env):
    port = env.get("/api/admin/referentials/remote").json()["config"]["source"]["urls"][0].split(":")[2].split("/")[0]
    url = f"http://127.0.0.1:{port}/remote.csv.gz"
    r = env.post("/api/admin/referentials/preview", json={"source": {"type": "http", "urls": [url]}})
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["detected_format"] == "csv" and p["format"] == "csv" and p["error"] is None
    assert [c["name"] for c in p["columns"]] == ["code", "label", "population", "created"]
    assert p["total_rows"] == 5 and len(p["rows"]) == 5 and p["files"][0]["name"] == "remote.csv"
    # Second call reuses the cached download
    p2 = env.post("/api/admin/referentials/preview", json={"source": {"type": "http", "urls": [url]}, "transform": "SELECT code FROM {source} WHERE population > 50000000"}).json()
    assert p2["total_rows"] == 3 and "reusing the copy" in p2["logs"][0]
    bad = env.post("/api/admin/referentials/preview", json={"source": {"type": "http", "urls": [url]}, "transform": "SELECT nope FROM {source}"}).json()
    assert bad["error"] and "nope" in bad["error"]
    assert env.post("/api/admin/referentials/preview", json={"source": {"type": "http", "urls": ["ftp://x"]}}).status_code == 400


def test_upload_create_update_delete(env):
    group = env.post("/api/admin/groups", json={"name": "Finance"}).json()
    up = env.post("/api/admin/referentials/uploads", files=[("files", ("budget.csv.gz", gzip.compress(SEMICOLON_CSV.encode()), "application/gzip"))])
    assert up.status_code == 200, up.text
    upload_id = up.json()["upload_id"]
    assert up.json()["files"] == [{"name": "budget.csv", "size": len(SEMICOLON_CSV)}]

    p = env.post("/api/admin/referentials/preview", json={"source": {"type": "upload", "upload_id": upload_id}, "options": {"decimal_separator": ","}}).json()
    assert p["sniff"]["delimiter"] == ";" and p["total_rows"] == 3, p
    assert p["columns"][2]["type"] == "DOUBLE"

    created = env.post("/api/admin/referentials", json={
        "config": {"id": "budget", "name": "Budget", "category": "Finance", "format": "csv",
                   "options": {"decimal_separator": ","}, "key": "code", "source": {"type": "http", "urls": ["http://ignored"]}},
        "upload_id": upload_id, "grant_group_ids": [group["id"]], "pull": True,
    })
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["origin"] == "database" and body["editable"] and {k: body["config"]["source"][k] for k in ("type", "path", "urls", "headers", "extract")} == {"type": "local", "path": "raw/**/*", "urls": [], "headers": {}, "extract": None}
    run = wait_idle(env, "budget")
    assert run["status"] == "success" and run["rows"] == 3 and run["user"] == "admin"
    assert env.get("/api/referentials/budget/lookup/B2").json()["montant"] == 20.0

    # The group received read access
    create_user(env, "fanny", group_ids=[group["id"]])
    fanny = new_client(env, "fanny", USER_PASSWORD)
    assert [r["id"] for r in fanny.get("/api/referentials").json()] == ["budget"]
    assert fanny.get("/api/admin/referentials").status_code == 403
    assert fanny.post("/api/admin/referentials/preview", json={"source": {"type": "http", "urls": ["http://x"]}}).status_code == 403

    # Replace the file: new version with the changes computed on the key
    new_csv = SEMICOLON_CSV.replace("Beta", "Bêta") + "D4;Delta;40\n"
    r = env.post("/api/admin/referentials/budget/upload", files=[("files", ("budget.csv", new_csv.encode(), "text/csv"))])
    assert r.status_code == 200, r.text
    run = wait_idle(env, "budget")
    assert run["status"] == "success" and run["changes"] == {"added": 1, "removed": 0, "modified": 1}

    # Update the definition (name, schedule) without re-uploading
    cfg = env.get("/api/admin/referentials/budget").json()["config"]
    cfg.update({"name": "Budget 2026", "schedule": "0 6 * * *"})
    r = env.put("/api/admin/referentials/budget", json={"config": cfg, "pull": False})
    assert r.status_code == 200, r.text
    assert env.get("/api/referentials/budget").json()["name"] == "Budget 2026"
    assert env.put("/api/admin/referentials/budget", json={"config": {**cfg, "schedule": "not a cron"}}).status_code == 422
    yml = env.get("/api/admin/referentials/budget/yaml").text
    assert "id: budget" in yml and "Budget 2026" in yml

    # Delete with purge: definition, data and grants are removed
    assert env.delete("/api/admin/referentials/budget").status_code == 200
    assert env.get("/api/referentials/budget").status_code == 404
    assert not (env.data_dir / "budget").exists()
    assert env.get("/api/admin/grants", params={"group_id": group["id"]}).json() == []


def test_create_from_url_and_conflicts(env):
    port = env.get("/api/admin/referentials/remote").json()["config"]["source"]["urls"][0].split(":")[2].split("/")[0]
    cfg = {"id": "remote-copy", "name": "Copie distante", "format": "csv", "key": "code", "downloads": ["csv"],
           "source": {"type": "http", "urls": [f"http://127.0.0.1:{port}/remote.csv.gz"]}}
    r = env.post("/api/admin/referentials", json={"config": cfg})
    assert r.status_code == 201, r.text
    assert wait_idle(env, "remote-copy")["rows"] == 5
    # Pre-generated download
    assert any(f["format"] == "csv" and f["ready"] for f in env.get("/api/referentials/remote-copy/downloads").json()["formats"])

    assert env.post("/api/admin/referentials", json={"config": cfg}).status_code == 409
    assert env.post("/api/admin/referentials", json={"config": {**cfg, "id": "countries"}}).status_code == 409
    assert env.post("/api/admin/referentials", json={"config": {**cfg, "id": "Bad Id"}}).status_code == 422
    # YAML-defined referentials are read-only from the UI
    yaml_cfg = env.get("/api/admin/referentials/countries").json()
    assert yaml_cfg["origin"] == "file" and not yaml_cfg["editable"]
    assert env.put("/api/admin/referentials/countries", json={"config": yaml_cfg["config"]}).status_code == 409
    assert env.delete("/api/admin/referentials/countries").status_code == 409
    # Definitions survive a configuration reload
    env.post("/api/system/reload")
    assert env.get("/api/referentials/remote-copy").status_code == 200


def test_downloads(env):
    refresh(env, "countries")
    listing = env.get("/api/referentials/countries/downloads").json()
    assert {f["format"] for f in listing["formats"]} == {"csv", "csv.gz", "xlsx", "json", "jsonl", "parquet"}
    assert listing["sources"][0]["name"] == "raw/a.csv"

    r = env.get("/api/referentials/countries/download/csv")
    assert r.status_code == 200 and r.text.startswith("code,label") and len(r.text.splitlines()) == 6
    assert "countries-v1.csv" in r.headers["content-disposition"]
    sha = r.headers["x-checksum-sha256"]
    assert env.get("/api/referentials/countries/download/csv", headers={"If-None-Match": f'"{sha}"'}).status_code == 304
    generated = next(f for f in env.get("/api/referentials/countries/downloads").json()["formats"] if f["format"] == "csv")
    assert generated["ready"] and generated["sha256"] == sha

    assert gzip.decompress(env.get("/api/referentials/countries/download/csv.gz").content).decode().startswith("code,label")
    assert len(env.get("/api/referentials/countries/download/json").json()) == 5
    assert env.get("/api/referentials/countries/download/parquet").content[:4] == b"PAR1"
    assert env.get("/api/referentials/countries/download/xlsx").content[:2] == b"PK"
    assert env.get("/api/referentials/countries/download/source").text.startswith("code,label")
    assert env.get("/api/referentials/countries/download/previous").status_code == 404
    assert env.get("/api/referentials/countries/download/exe").status_code == 404

    # A new version invalidates the cache
    path = env.data_dir / "countries" / "raw" / "a.csv"
    path.write_text(path.read_text(encoding="utf-8") + "PT,Portugal,10300000,1976-04-25\n", encoding="utf-8")
    refresh(env, "countries")
    r = env.get("/api/referentials/countries/download/csv")
    assert len(r.text.splitlines()) == 7 and r.headers["x-checksum-sha256"] != sha
    assert env.get("/api/referentials/countries/download/previous").content[:4] == b"PAR1"

    catalog = env.get("/api/downloads").json()
    assert [c["id"] for c in catalog] == ["countries"]

    create_user(env, "gus")
    gus = new_client(env, "gus", USER_PASSWORD)
    assert gus.get("/api/referentials/countries/download/csv").status_code == 404
    assert gus.get("/api/downloads").json() == []
    audit = env.get("/api/admin/audit", params={"action": "referential.download"}).json()
    assert audit["total"] >= 5


def test_generated_openapi(env):
    refresh(env, "countries")
    refresh(env, "words")
    spec = env.get("/api/referentials/countries/openapi.json").json()
    assert spec["openapi"].startswith("3.1") and spec["info"]["title"] == "API — Pays"
    rows = spec["paths"]["/api/referentials/countries/rows"]["get"]
    params = {p["name"]: p for p in rows["parameters"]}
    assert params["population__gte"]["schema"]["type"] == "integer"
    assert params["code"]["example"] in ("FR", "DE", "IT", "ES", "BE")
    assert "created__lte" in params and "label__contains" in params
    row_schema = spec["components"]["schemas"]["countries_row"]["properties"]
    assert row_schema["created"]["format"] == "date" and row_schema["population"]["type"] == ["integer", "null"]
    assert "/api/referentials/countries/lookup/{value}" in spec["paths"]
    assert "/api/referentials/countries/download/{format}" in spec["paths"]
    assert spec["servers"][0]["url"] == "http://testserver"

    assert env.get("/api/catalog/docs", params={"ref": "countries"}).text.count("/api/referentials/countries/openapi.json") == 1
    assert env.get("/api/catalog/docs", params={"ref": "x';alert(1)"}).status_code == 400

    # The combined specification only documents accessible referentials
    create_user(env, "hal")
    hal = new_client(env, "hal", USER_PASSWORD)
    hal_id = hal.get("/api/auth/me").json()["id"]
    env.post("/api/admin/grants", json={"referential_id": "words", "user_id": hal_id})
    tags = [t["name"] for t in hal.get("/api/catalog/openapi.json").json()["tags"]]
    assert tags == ["Mots"]
    assert hal.get("/api/referentials/countries/openapi.json").status_code == 404
