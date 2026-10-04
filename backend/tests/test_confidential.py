"""Confidential referentials: data encrypted at rest (Parquet AES-GCM, internal rows in the database),
no clear copy left on disk, transparent use through the API."""

import gzip
import json
import os
import time

import duckdb

from conftest import CSV, refresh


def wait_idle(env, ref_id, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not env.get(f"/api/referentials/{ref_id}").json()["current_run"]:
            return env.get(f"/api/referentials/{ref_id}/runs", params={"limit": 1}).json()[0]
        time.sleep(0.05)
    raise TimeoutError(ref_id)


def clear_bytes(env, ref_id) -> bytes:
    """Everything stored for the referential on disk."""
    root = env.data_dir / ref_id
    return b"".join(p.read_bytes() for p in root.rglob("*") if p.is_file())


def publish_csv(env, content: str):
    target = env.www / "remote.csv.gz"
    previous = target.stat().st_mtime
    with gzip.open(target, "wt", encoding="utf-8") as f:
        f.write(content)
    os.utime(target, (previous + 10, previous + 10))  # no HTTP 304 within the same second


def test_confidential_table(env, tmp_path):
    url = env.get("/api/admin/referentials/remote").json()["config"]["source"]["urls"][0]
    cfg = {"id": "secret", "name": "Secret countries", "format": "csv", "key": "code", "confidential": True,
           "source": {"type": "http", "urls": [url]}}
    assert env.post("/api/admin/referentials", json={"config": cfg}).status_code == 201
    run = wait_idle(env, "secret")
    assert run["status"] == "success", run
    d = env.get("/api/referentials/secret").json()
    assert d["confidential"] is True and d["encrypted"] is True

    # Nothing readable on disk: encrypted parquet, no source file, column profile encrypted in meta.json
    stored = clear_bytes(env, "secret")
    for value in (b"Allemagne", b"Belgique", b"68000000"):
        assert value not in stored, value
    assert not any((env.data_dir / "secret" / "raw").iterdir())
    meta = json.loads((env.data_dir / "secret" / "meta.json").read_text(encoding="utf-8"))
    assert meta["encrypted"] and meta["profile"] == [] and meta["profile_enc"].startswith("enc:v1:")
    try:
        duckdb.connect().execute(f"SELECT * FROM read_parquet('{(env.data_dir / 'secret' / 'current.parquet').as_posix()}')").fetchall()
        raise AssertionError("readable without the key")
    except duckdb.InvalidInputException:
        pass

    # Transparent through the API: rows, lookup, facets, statistics, global search, SQL console, profile
    assert env.get("/api/referentials/secret/rows", params={"code": "DE"}).json()["rows"][0]["label"] == "Allemagne"
    assert env.get("/api/referentials/secret/lookup/FR").json()["population"] == 68000000
    assert env.get("/api/referentials/secret/facets").json()["total"] == 5
    assert env.get("/api/referentials/secret/columns/label/stats").json()["non_null"] == 5
    assert any(r["id"] == "secret" and r["total"] for r in env.get("/api/search", params={"q": "IT"}).json()["results"])
    assert env.post("/api/sql", json={"sql": "SELECT count(*) FROM secret"}).json()["rows"] == [[5]]
    assert {p["name"] for p in env.get("/api/referentials/secret").json()["profile"]} >= {"code", "label"}

    # Downloads: generated in clear for each request, never kept on disk
    csv = env.get("/api/referentials/secret/download/csv")
    assert csv.status_code == 200 and "Allemagne" in csv.text and csv.headers["cache-control"] == "no-store"
    downloaded = tmp_path / "secret.parquet"
    downloaded.write_bytes(env.get("/api/referentials/secret/download/parquet").content)
    assert duckdb.connect().execute(f"SELECT count(*) FROM read_parquet('{downloaded.as_posix()}')").fetchone()[0] == 5  # clear copy
    assert not (env.data_dir / "secret" / "exports").exists()
    assert not list((env.data_dir / ".tmp" / "exports").glob("*.*"))  # temporary copies deleted once sent
    assert env.get("/api/referentials/secret/export", params={"format": "csv", "code": "IT"}).text.count("Italie") == 1

    # New version: changes computed between two encrypted versions; restart keeps everything readable
    publish_csv(env, CSV + "NL,Pays-Bas,17900000,1815-03-16\n")
    run = refresh(env, "secret")
    assert run["status"] == "success" and run["changes"]["added"] == 1, run
    assert env.get("/api/referentials/secret/changes", params={"kind": "added"}).json()["rows"][0]["code"] == "NL"
    assert b"Pays-Bas" not in clear_bytes(env, "secret")
    env.app_.state.service.reload()
    assert env.get("/api/referentials/secret/lookup/NL").json()["label"] == "Pays-Bas"
    assert env.get("/api/referentials/secret").json()["profile"]

    # No longer confidential: published again in clear, the encrypted previous version is not kept
    r = env.put("/api/admin/referentials/secret", json={"config": {**cfg, "confidential": False}})
    assert r.status_code == 200, r.text
    run = wait_idle(env, "secret")
    assert run["status"] == "success", (run, env.get("/api/referentials/secret/runs").json()[0]["logs"])
    assert env.get("/api/referentials/secret").json()["encrypted"] is False
    assert not (env.data_dir / "secret" / "previous.parquet").exists()
    assert duckdb.connect().execute(
        f"SELECT count(*) FROM read_parquet('{(env.data_dir / 'secret' / 'current.parquet').as_posix()}')").fetchone()[0] == 6


SCHEMA = {"id": "staff", "name": "Staff", "key": "email", "confidential": True,
          "columns": [{"name": "email", "type": "text", "required": True}, {"name": "salary", "type": "integer"}]}


def test_confidential_internal_referential(env):
    from sqlalchemy import select

    from app.db import session_factory
    from app.models import AuditLog, InternalRecord

    assert env.post("/api/internal-referentials", json=SCHEMA).status_code == 201
    wait_idle(env, "staff")
    rows = [{"email": "alice@example.com", "salary": 51000}, {"email": "bob@example.com", "salary": 47000}]
    assert env.post("/api/referentials/staff/records/_bulk", json={"upsert": rows}).json()["created"] == 2
    assert env.post("/api/referentials/staff/records", json={"email": "alice@example.com"}).status_code == 409  # uniqueness kept

    def stored():
        with session_factory()() as db:
            return [(r.row_key, r.data) for r in db.scalars(select(InternalRecord).where(InternalRecord.referential_id == "staff"))]

    # In the database: encrypted values, keys replaced by a keyed fingerprint
    for row_key, data in stored():
        assert row_key.startswith("h:") and list(data) == ["_enc"]
        assert "alice" not in json.dumps(data) and "51000" not in json.dumps(data)

    # Used normally through the API
    assert env.get("/api/referentials/staff/records/alice@example.com").json()["salary"] == 51000
    listing = env.get("/api/referentials/staff/records", params={"q": "bob"}).json()
    assert listing["total"] == 1 and listing["records"][0]["_key"] == "bob@example.com"
    assert [r["_key"] for r in env.get("/api/referentials/staff/records").json()["records"]] == ["alice@example.com", "bob@example.com"]
    assert env.patch("/api/referentials/staff/records/bob@example.com", json={"salary": 48000}).json()["salary"] == 48000
    r = env.put("/api/referentials/staff/records/bob@example.com", json={"email": "robert@example.com", "salary": 48000})
    assert r.json()["_key"] == "robert@example.com"
    assert env.get("/api/referentials/staff/records/bob@example.com").status_code == 404
    assert env.delete("/api/referentials/staff/records/robert@example.com").status_code == 200

    # Published encrypted; keys absent from the audit log
    wait_idle(env, "staff")
    assert env.get("/api/referentials/staff/lookup/alice@example.com").json()["salary"] == 51000
    assert b"alice" not in clear_bytes(env, "staff")
    with session_factory()() as db:
        details = [json.dumps(a.detail) for a in db.scalars(select(AuditLog).where(AuditLog.target == "ref:staff"))]
    assert details and not any("alice" in d or "bob" in d for d in details)

    # Switched off: the rows are decrypted in the database
    r = env.put("/api/internal-referentials/staff", json={**SCHEMA, "confidential": False})
    assert r.status_code == 200, r.text
    assert stored() == [("alice@example.com", {"email": "alice@example.com", "salary": 51000})]


def test_raw_files_cannot_be_confidential(env):
    base = {"id": "geo3", "name": "Geo", "source": {"type": "http", "urls": ["http://x/y"]}, "confidential": True}
    assert env.post("/api/admin/referentials", json={"config": {**base, "format": "mmdb"}}).status_code == 422
    assert env.post("/api/admin/referentials", json={"config": {**base, "format": "bloom"}}).status_code == 422


def test_config_hash_of_existing_definitions_is_stable():
    """New fields (basic_auth, confidential) left at their defaults keep the hash of the definitions written
    before them: no rebuild of every referential after an upgrade."""
    import hashlib

    from app.config import ReferentialConfig

    ref = ReferentialConfig.model_validate({"id": "x", "name": "X", "format": "csv", "source": {"urls": ["http://h/a.csv"]}})
    legacy = {
        "format": "csv", "options": {}, "transform": None,
        "source": {"type": "http", "urls": ["http://h/a.csv"], "path": None, "headers": {}, "extract": None},
        "storage": {k: v for k, v in ref.storage.model_dump().items() if k in ("sort_by", "indexes", "row_group_size")},
        "columns": [],
    }
    assert ref.config_hash == hashlib.sha256(json.dumps(legacy, sort_keys=True, default=str).encode()).hexdigest()[:16]
    assert ref.model_copy(update={"confidential": True}).config_hash != ref.config_hash
