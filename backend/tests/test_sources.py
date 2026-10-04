"""Corrupted remote sources, manual imports (upload, import folder) and internal referentials."""
import gzip
import time

from conftest import CSV, USER_PASSWORD, create_user, new_client, refresh


def wait_idle(client, ref_id, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        d = client.get(f"/api/referentials/{ref_id}").json()
        if not d["current_run"]:
            return client.get(f"/api/referentials/{ref_id}/runs", params={"limit": 1}).json()[0]
        time.sleep(0.1)
    raise TimeoutError(ref_id)


# --------------------------------------------------------------------------- corrupted sources

def test_corrupted_remote_source_keeps_current_version(env):
    assert refresh(env, "remote")["status"] == "success"
    raw = env.data_dir / "remote" / "raw" / "remote.csv.gz"  # line-based .gz sources are kept compressed
    good = raw.read_bytes()

    def serve(content: bytes):
        with gzip.open(env.www / "remote.csv.gz", "wb") as f:
            f.write(content)

    for content, expected in [
        (b"", "empty"),
        (b"<!DOCTYPE html><html><body>Maintenance</body></html>", "error page"),
        (b"404: Not Found", "error page"),
    ]:
        serve(content)
        run = refresh(env, "remote", force=True)
        assert run["status"] == "corrupted", (content, run)
        assert expected in run["message"]
        detail = env.get("/api/referentials/remote").json()
        assert detail["has_data"] and detail["row_count"] == 5 and detail["health"] == "error" and detail["status"] == "corrupted"
        assert raw.read_bytes() == good  # previous copy of the source untouched
        assert not (env.data_dir / "remote" / "raw" / ".incoming").exists()

    (env.www / "remote.csv.gz").write_bytes(b"\x1f\x8b\x08garbage")  # truncated gzip
    run = refresh(env, "remote", force=True)
    assert run["status"] == "corrupted" and "unreadable" in run["message"]
    # Back to normal
    serve(CSV.encode())
    assert refresh(env, "remote", force=True)["status"] == "success"


def test_empty_local_file_and_wrong_json(env):
    assert refresh(env, "countries")["status"] == "success"
    (env.data_dir / "countries" / "raw" / "a.csv").write_text("", encoding="utf-8")
    run = refresh(env, "countries")
    assert run["status"] == "corrupted" and "empty" in run["message"]
    (env.data_dir / "nested" / "raw" / "n.json").write_text("404: Not Found", encoding="utf-8")
    assert refresh(env, "nested")["status"] == "corrupted"


# --------------------------------------------------------------------------- manual import

def test_manual_upload_and_pin(env):
    assert refresh(env, "remote")["status"] == "success"
    files = [("files", ("remote.csv", (CSV + "NL,Pays-Bas,17900000,1815-03-16\n").encode(), "text/csv"))]
    r = env.post("/api/referentials/remote/import", files=files, data={"pin": "true"})
    assert r.status_code == 200, r.text
    run = wait_idle(env, "remote")
    assert run["status"] == "success" and run["rows"] == 6 and run["trigger"] == "upload" and run["user"] == "admin"
    d = env.get("/api/referentials/remote").json()
    assert d["manual_import"]["origin"] == "upload" and d["manual_import"]["files"] == ["remote.csv"]
    assert d["pinned"]["by"] == "admin"
    # Frozen: manual refresh refused, automatic ones skipped
    assert env.post("/api/referentials/remote/refresh").status_code == 409
    service = env.app_.state.service
    assert service.submit("remote", "schedule") is None
    assert env.get("/api/referentials/remote/download/source").text.count("\n") == 7
    # Unfreeze: the remote source takes over again
    assert env.delete("/api/referentials/remote/pin").status_code == 200
    assert refresh(env, "remote")["rows"] == 5
    assert "manual_import" not in env.get("/api/referentials/remote").json() or env.get("/api/referentials/remote").json()["manual_import"] is None

    # Corrupted upload: refused, current version kept
    r = env.post("/api/referentials/remote/import", files=[("files", ("remote.csv", b"", "text/csv"))])
    assert r.status_code == 200
    assert wait_idle(env, "remote")["status"] == "corrupted"
    assert env.get("/api/referentials/remote").json()["row_count"] == 5

    # Read-only users cannot import
    create_user(env, "ivan")
    ivan = new_client(env, "ivan", USER_PASSWORD)
    env.post("/api/admin/grants", json={"referential_id": "remote", "user_id": ivan.get("/api/auth/me").json()["id"]})
    assert ivan.post("/api/referentials/remote/import", files=files).status_code == 403


def test_import_folder(env, tmp_path):
    service = env.app_.state.service
    service.settings.import_dir = tmp_path / "import"
    service._prepare_import_dir()
    inbox = tmp_path / "import" / "countries"
    assert (inbox / "README.txt").exists()

    (inbox / "pays.csv").write_text(CSV + "PT,Portugal,10300000,1976-04-25\n", encoding="utf-8")
    old = time.time() - 30
    import os

    os.utime(inbox / "pays.csv", (old, old))
    assert service.scan_import_dir() == []  # first sighting: waits for the file to be stable
    assert service.scan_import_dir() == ["countries"]
    run = wait_idle(env, "countries")
    assert run["status"] == "success" and run["rows"] == 6 and run["trigger"] == "inbox"
    done = list((inbox / ".done").iterdir())
    assert len(done) == 1 and (done[0] / "pays.csv").exists()
    assert not [f for f in inbox.iterdir() if f.is_file() and f.name != "README.txt"]

    # An empty file ends up in .failed with an explanation
    (inbox / "empty.csv").write_text("", encoding="utf-8")
    os.utime(inbox / "empty.csv", (old, old))
    service.scan_import_dir()
    service.scan_import_dir()
    assert wait_idle(env, "countries")["status"] == "corrupted"
    failed = list((inbox / ".failed").iterdir())
    assert (failed[0] / "ERROR.txt").read_text(encoding="utf-8").startswith("Import refused")
    assert env.get("/api/referentials/countries").json()["row_count"] == 6

    # Unknown folders are reported
    (tmp_path / "import" / "unknown").mkdir()
    (tmp_path / "import" / "unknown" / "data.csv").write_text(CSV, encoding="utf-8")
    service.scan_import_dir()
    assert env.get("/api/system").json()["import_unknown_folders"] == ["unknown"]

    # Folders of removed referentials go away when they only hold the README; others are kept
    (tmp_path / "import" / "removed").mkdir()
    (tmp_path / "import" / "removed" / "README.txt").write_text("x", encoding="utf-8")
    service._prepare_import_dir()
    assert not (tmp_path / "import" / "removed").exists() and (tmp_path / "import" / "unknown").exists()


# --------------------------------------------------------------------------- internal referentials

SCHEMA = {
    "id": "suppliers", "name": "Suppliers", "category": "Internal", "key": "code",
    "columns": [
        {"name": "code", "type": "text", "required": True},
        {"name": "label", "type": "text", "required": True},
        {"name": "employees", "type": "integer"},
        {"name": "rating", "type": "number"},
        {"name": "approved", "type": "boolean"},
        {"name": "since", "type": "date"},
        {"name": "tags", "type": "list"},
    ],
}


def test_internal_referential_lifecycle(env):
    # Only admins and advanced users create internal referentials
    create_user(env, "julia")
    julia = new_client(env, "julia", USER_PASSWORD)
    assert julia.post("/api/internal-referentials", json=SCHEMA).status_code == 403
    adv = create_user(env, "kevin", role="advanced")
    kevin = new_client(env, "kevin", USER_PASSWORD)
    assert kevin.get("/api/auth/me").json()["can_create_internal"] is True
    r = kevin.post("/api/internal-referentials", json=SCHEMA)
    assert r.status_code == 201, r.text
    assert r.json()["kind"] == "internal" and r.json()["access"] == "manage"
    wait_idle(env, "suppliers")
    assert env.get("/api/referentials/suppliers").json()["row_count"] == 0  # empty but published
    assert env.get("/api/admin/grants", params={"user_id": adv["id"]}).json()[0]["level"] == "manage"

    # Rows: typed validation
    r = kevin.post("/api/referentials/suppliers/records", json={"code": "ACME", "label": "Acme", "employees": "120", "rating": "4,5",
                                                               "approved": "oui", "since": "2020-03-01", "tags": "cloud, eu"})
    assert r.status_code == 201, r.text
    rec = r.json()
    assert rec["employees"] == 120 and rec["rating"] == 4.5 and rec["approved"] is True and rec["tags"] == ["cloud", "eu"]
    assert rec["_updated_by"] == "kevin"
    assert kevin.post("/api/referentials/suppliers/records", json={"code": "ACME", "label": "x"}).status_code == 409
    assert "not a valid integer" in kevin.post("/api/referentials/suppliers/records", json={"code": "B", "label": "B", "employees": "lots"}).json()["detail"]
    assert "unknown column" in kevin.post("/api/referentials/suppliers/records", json={"code": "C", "label": "C", "color": "red"}).json()["detail"]
    assert "required" in kevin.post("/api/referentials/suppliers/records", json={"code": "D"}).json()["detail"]

    # Bulk synchronisation, then the published data reflects the rows
    r = kevin.post("/api/referentials/suppliers/records/_bulk", json={"upsert": [
        {"code": "GLOBEX", "label": "Globex", "employees": 9000}, {"code": "INITECH", "label": "Initech", "approved": False}]})
    assert r.json() == {"created": 2, "updated": 0, "unchanged": 0, "deleted": 0}
    wait_idle(env, "suppliers")
    rows = env.get("/api/referentials/suppliers/rows", params={"sort": "code"}).json()
    assert [x["code"] for x in rows["rows"]] == ["ACME", "GLOBEX", "INITECH"] and rows["columns"][2]["type"] == "BIGINT"
    assert env.get("/api/referentials/suppliers/lookup/GLOBEX").json()["employees"] == 9000

    # Patch, rename (PUT), delete
    assert kevin.patch("/api/referentials/suppliers/records/ACME", json={"rating": 3}).json()["rating"] == 3.0
    r = kevin.put("/api/referentials/suppliers/records/INITECH", json={"code": "INITRODE", "label": "Initrode"})
    assert r.json()["_key"] == "INITRODE"
    assert kevin.delete("/api/referentials/suppliers/records/GLOBEX").status_code == 200
    wait_idle(env, "suppliers")
    assert env.get("/api/referentials/suppliers/records").json()["total"] == 2
    history = env.get("/api/referentials/suppliers/runs").json()
    assert history[0]["trigger"] == "edit" and history[0]["status"] in ("success", "unchanged")

    # Import a CSV file (replace mode)
    csv = "code,label,employees,extra\nX1,Xenon,5,ignored\nY2,Yttrium,7,ignored\n"
    r = kevin.post("/api/referentials/suppliers/records/_import", files={"file": ("load.csv", csv.encode(), "text/csv")}, data={"mode": "replace"})
    assert r.status_code == 200, r.text
    assert r.json()["created"] == 2 and r.json()["deleted"] == 2
    wait_idle(env, "suppliers")
    assert env.get("/api/referentials/suppliers").json()["row_count"] == 2

    # Read-only users can query but not edit; the source manual import does not apply
    env.post("/api/admin/grants", json={"referential_id": "suppliers", "user_id": julia.get("/api/auth/me").json()["id"]})
    assert julia.get("/api/referentials/suppliers/records").json()["can_edit"] is False
    assert julia.post("/api/referentials/suppliers/records", json={"code": "Z", "label": "Z"}).status_code == 403
    assert kevin.post("/api/referentials/suppliers/import", files=[("files", ("a.csv", b"x", "text/csv"))]).status_code == 400

    # Schema change: rename a column, drop another, add one; type conflicts are refused
    new = {**SCHEMA, "columns": [
        {"name": "code", "type": "text", "required": True}, {"name": "name", "type": "text", "required": True},
        {"name": "employees", "type": "integer"}, {"name": "country", "type": "text"}], "renames": {"label": "name"}}
    r = kevin.put("/api/internal-referentials/suppliers", json=new)
    assert r.status_code == 200, r.text
    assert env.get("/api/referentials/suppliers/records/X1").json() == {**env.get("/api/referentials/suppliers/records/X1").json(), "name": "Xenon", "country": None}
    bad = {**new, "renames": {}, "columns": [*new["columns"][:2], {"name": "employees", "type": "boolean"}, new["columns"][3]]}
    assert "do not fit" in kevin.put("/api/internal-referentials/suppliers", json=bad).json()["detail"]

    # Generated OpenAPI documents the write endpoints
    spec = env.get("/api/referentials/suppliers/openapi.json").json()
    assert "post" in spec["paths"]["/api/referentials/suppliers/records"]

    wait_idle(env, "suppliers")
    assert kevin.delete("/api/internal-referentials/suppliers").status_code == 200
    assert env.get("/api/referentials/suppliers").status_code == 404


def test_auto_key(env):
    schema = {"id": "notes", "name": "Notes", "key": "id", "columns": [{"name": "id", "type": "text", "auto": True}, {"name": "text", "type": "text"}]}
    assert env.post("/api/internal-referentials", json=schema).status_code == 201
    r = env.post("/api/referentials/notes/records", json={"text": "hello"}).json()
    assert len(r["id"]) == 36 and r["_key"] == r["id"]
    bad = {**schema, "id": "notes2", "columns": [{"name": "id", "type": "integer", "auto": True}]}
    assert env.post("/api/internal-referentials", json=bad).status_code == 422
