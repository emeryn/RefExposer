import io
import sqlite3
import time
import zipfile

from conftest import refresh

# Minimal NIST NSRL RDSv3 database: FILE table, PKG table, VERSION
SCHEMA = """
CREATE TABLE FILE (sha256 VARCHAR NOT NULL, sha1 VARCHAR NOT NULL, md5 VARCHAR NOT NULL, crc32 VARCHAR NOT NULL,
  file_name VARCHAR NOT NULL, file_size INTEGER NOT NULL, package_id INTEGER NOT NULL,
  CONSTRAINT PK_FILE__FILE PRIMARY KEY (sha256, sha1, md5, file_name, file_size, package_id));
CREATE TABLE PKG (package_id INTEGER NOT NULL, name VARCHAR NOT NULL, version VARCHAR NOT NULL);
CREATE TABLE VERSION (version VARCHAR UNIQUE NOT NULL, description VARCHAR NOT NULL);
CREATE VIEW DISTINCT_HASH AS SELECT DISTINCT sha256, sha1, md5, crc32 FROM FILE;
"""


def sha(i: int, n: int = 64) -> str:
    return f"{i:0{n}X}"


def insert(i: int, name: str | None = None) -> str:
    return (f"INSERT INTO FILE(sha256,sha1,md5,crc32,file_name,file_size,package_id) VALUES("
            f"'{sha(i)}','{sha(i, 40)}','{sha(i, 32)}','{sha(i, 8)}','{name or f'file{i}.dll'}',{i},{i % 3});")


def make_db(path, count: int) -> None:
    con = sqlite3.connect(path)
    con.executescript(SCHEMA + "".join(insert(i) for i in range(1, count + 1))
                      + "INSERT INTO PKG VALUES (0,'Office','2016'),(1,'Windows','11'),(2,'Linux','6');"
                      + "INSERT INTO VERSION VALUES ('2026.03.1','full');")
    con.commit()
    con.close()


def nsrl_zip(folder: str, files: dict[str, bytes]) -> bytes:
    """Zip laid out like the NIST publications: a folder, readme, signatures, schema and the data file."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{folder}/readme.txt", "NSRL RDS test publication\n")
        zf.writestr(f"{folder}/signatures.txt", "0000 readme.txt\n")
        zf.writestr(f"{folder}/RDS.schema.sql", SCHEMA)
        for name, data in files.items():
            zf.writestr(f"{folder}/{name}", data)
    return buf.getvalue()


def delta(statements: list[str]) -> bytes:
    return ("BEGIN TRANSACTION;\n" + "\n".join(statements) + "\nCOMMIT;\n").encode()


def wait_idle(client, ref_id, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not client.get(f"/api/referentials/{ref_id}").json()["current_run"]:
            return client.get(f"/api/referentials/{ref_id}/runs", params={"limit": 1}).json()[0]
        time.sleep(0.1)
    raise TimeoutError(ref_id)


def logs(env, ref_id) -> str:
    return "\n".join(line["msg"] for line in env.get(f"/api/referentials/{ref_id}/runs", params={"limit": 1}).json()[0]["logs"])


def test_sqlite_referential_reads_a_table_or_view(env):
    raw = env.data_dir / "rds" / "raw"
    raw.mkdir(parents=True)
    make_db(raw / "rds.db", 50)
    cfg = {"id": "rds", "name": "RDS", "format": "sqlite", "source": {"type": "local", "path": "raw/*.db"},
           "options": {"table": "FILE"}, "key": "sha1"}
    assert env.post("/api/admin/referentials", json={"config": cfg, "pull": False}).status_code == 201
    run = refresh(env, "rds")
    assert run["status"] == "success" and run["rows"] == 50, run
    row = env.get(f"/api/referentials/rds/lookup/{sha(7, 40)}").json()
    assert row["file_name"] == "file7.dll" and row["file_size"] == 7 and row["package_id"] == 1
    # A view keeps its column types (attached database)
    cfg["options"] = {"table": "DISTINCT_HASH"}
    assert env.put("/api/admin/referentials/rds", json={"config": cfg, "pull": False}).status_code == 200
    run = refresh(env, "rds")
    assert run["status"] == "success" and run["rows"] == 50, run
    cols = {c["name"]: c["type"] for c in env.get("/api/referentials/rds").json()["columns"]}
    assert cols["sha1"] == "VARCHAR", cols
    # Unknown table: the update fails, the published version stays
    cfg["options"] = {"table": "NOPE"}
    env.put("/api/admin/referentials/rds", json={"config": cfg, "pull": False})
    assert refresh(env, "rds")["status"] == "error"
    assert env.get("/api/referentials/rds").json()["row_count"] == 50


def test_preview_sqlite_lists_tables(env, http_dir, tmp_path):
    make_db(tmp_path / "RDS_test_minimal.db", 20)
    (env.www / "full.zip").write_bytes(nsrl_zip("RDS_test", {"RDS_test_minimal.db": (tmp_path / "RDS_test_minimal.db").read_bytes()}))
    url = f"http://127.0.0.1:{http_dir[1]}/full.zip"
    p = env.post("/api/admin/referentials/preview", json={"source": {"type": "http", "urls": [url]}}).json()
    assert p["detected_format"] == "sqlite", p
    assert p["records_path_candidates"][:1] == ["DISTINCT_HASH"] and {"FILE", "PKG", "VERSION"} <= set(p["records_path_candidates"])
    assert "choose the table" in p["error"]
    p = env.post("/api/admin/referentials/preview", json={"source": {"type": "http", "urls": [url]}, "options": {"table": "FILE"}}).json()
    assert p["error"] is None and p["total_rows"] == 20 and len(p["columns"]) == 7, p


def test_preview_too_large_is_set_by_hand(env, http_dir, monkeypatch):
    from app import preview

    monkeypatch.setattr(preview, "FULL_MAX_BYTES", 100)
    (env.www / "RDS_2026.03.1_modern.zip").write_bytes(b"PK" + b"x" * 500)
    url = f"http://127.0.0.1:{http_dir[1]}/RDS_2026.03.1_modern.zip"
    p = env.post("/api/admin/referentials/preview", json={"source": {"type": "http", "urls": [url]}, "format": "sqlite"}).json()
    assert p["error"] is None and "too large to be analysed" in p["unanalysed"] and p["format"] == "sqlite", p
    assert p["files"][0]["size"] == 502


def test_sqlite_deltas(env, http_dir, tmp_path):
    base_url = f"http://127.0.0.1:{http_dir[1]}"
    make_db(tmp_path / "RDS_2026.03.1_modern_minimal.db", 100)
    (env.www / "full.zip").write_bytes(nsrl_zip("RDS_2026.03.1_modern_minimal", {
        "RDS_2026.03.1_modern_minimal.db": (tmp_path / "RDS_2026.03.1_modern_minimal.db").read_bytes()}))
    (env.www / "d1.zip").write_bytes(nsrl_zip("RDS_2026.06.1_modern_minimal_delta", {
        "RDS_2026.06.1_modern_minimal_delta.sql": delta([insert(i) for i in range(101, 121)]
                                                        + [f"DELETE FROM FILE WHERE sha1 = '{sha(5, 40)}';",
                                                           f"UPDATE FILE SET file_name = 'renamed.dll' WHERE sha1 = '{sha(6, 40)}';"])}))
    cfg = {"id": "nsrl", "name": "NSRL", "format": "sqlite", "source": {"urls": [f"{base_url}/full.zip"]},
           "options": {"table": "FILE"}, "key": "sha1", "incremental": {"urls": [f"{base_url}/d1.zip"]}}
    assert env.post("/api/admin/referentials", json={"config": cfg, "pull": False}).status_code == 201, \
        env.post("/api/admin/referentials", json={"config": cfg, "pull": False}).text

    # 1. Full database, then the listed delta on top
    run = refresh(env, "nsrl")
    assert run["status"] == "success" and run["rows"] == 119, run  # 100 + 20 - 1
    text = logs(env, "nsrl")
    assert "Full database needed (no database kept yet)" in text and "22 statements applied" in text
    assert env.get(f"/api/referentials/nsrl/lookup/{sha(6, 40)}").json()["file_name"] == "renamed.dll"
    assert env.get(f"/api/referentials/nsrl/lookup/{sha(5, 40)}").status_code == 404
    kept = list((env.data_dir / "nsrl" / "base").iterdir())
    assert [f.name for f in kept] == ["RDS_2026.03.1_modern_minimal.db"]
    assert not list((env.data_dir / "nsrl" / "raw").rglob("*.zip"))  # the archive is not kept

    # 2. Nothing new: nothing downloaded, no new version
    run = refresh(env, "nsrl")
    assert run["status"] == "unchanged", run
    assert "Downloading" not in logs(env, "nsrl")

    # 3. A new delta added at the end of the list: only this one is downloaded and applied
    (env.www / "d2.zip").write_bytes(nsrl_zip("d2", {"RDS_2026.09.1_modern_minimal_delta.sql": delta([insert(i) for i in range(121, 131)])}))
    cfg["incremental"]["urls"].append(f"{base_url}/d2.zip")
    assert env.put("/api/admin/referentials/nsrl", json={"config": cfg, "pull": False}).status_code == 200
    run = refresh(env, "nsrl")
    assert run["status"] == "success" and run["rows"] == 129, run
    text = logs(env, "nsrl")
    assert "full.zip" not in text and "d1.zip" not in text and "10 statements applied" in text

    # 4. A broken delta: nothing applied (one transaction), the published version and the database are kept
    (env.www / "d3.zip").write_bytes(nsrl_zip("d3", {"RDS_2026.12.1_modern_minimal_delta.sql": delta(
        [insert(i) for i in range(131, 141)] + ["INSERT INTO NOPE VALUES (1);"])}))
    cfg["incremental"]["urls"].append(f"{base_url}/d3.zip")
    env.put("/api/admin/referentials/nsrl", json={"config": cfg, "pull": False})
    run = refresh(env, "nsrl")
    assert run["status"] == "rejected" and "no such table: NOPE" in run["message"] and "nothing applied" in run["message"], run
    assert env.get("/api/referentials/nsrl").json()["row_count"] == 129
    con = sqlite3.connect(kept[0])
    assert con.execute("SELECT count(*) FROM FILE").fetchone()[0] == 129
    con.close()

    # 5. Fixed delta (same URL): applied once
    (env.www / "d3.zip").write_bytes(nsrl_zip("d3", {"RDS_2026.12.1_modern_minimal_delta.sql": delta([insert(i) for i in range(131, 141)])}))
    run = refresh(env, "nsrl")
    assert run["status"] == "success" and run["rows"] == 139, run
    applied = env.get("/api/referentials/nsrl").json()
    state = (env.data_dir / "nsrl" / "meta.json").read_text()
    assert state.count('"statements"') == 3, state

    # 6. Delta imported by hand (upload): applied to the kept database, refused the second time
    sql = delta([insert(i) for i in range(141, 146)])
    r = env.post("/api/referentials/nsrl/import", files=[("files", ("manual_delta.sql", sql, "application/sql"))])
    assert r.status_code == 200, r.text
    run = wait_idle(env, "nsrl")
    assert run["status"] == "success" and run["rows"] == 144, run
    env.delete("/api/referentials/nsrl/pin")
    r = env.post("/api/referentials/nsrl/import", files=[("files", ("manual_delta.sql", sql, "application/sql"))])
    run = wait_idle(env, "nsrl")
    assert run["status"] == "rejected" and "already been applied" in run["message"], run
    env.delete("/api/referentials/nsrl/pin")

    # 7. A delta removed from the list: the full database is loaded again and the list applied on top
    cfg["incremental"]["urls"] = [f"{base_url}/d1.zip", f"{base_url}/d3.zip"]
    env.put("/api/admin/referentials/nsrl", json={"config": cfg, "pull": False})
    run = refresh(env, "nsrl")
    assert run["status"] == "success" and run["rows"] == 129, run  # 100 + 20 - 1 + 10 (d2 and the manual delta are gone)
    assert "the list of deltas changed" in logs(env, "nsrl")
    assert applied  # detail readable all along


def test_sqlite_incremental_config_validation():
    import pytest

    from app.config import validate_definition

    base = {"id": "x", "name": "x", "format": "sqlite", "source": {"urls": ["https://a/full.zip"]}, "options": {"table": "FILE"}}
    assert validate_definition({**base, "incremental": {"urls": []}}).incremental.urls == []
    with pytest.raises(ValueError, match="SQL files"):
        validate_definition({**base, "incremental": {"urls": [], "full_every": "30d"}})
    with pytest.raises(ValueError, match="cannot be confidential"):
        validate_definition({**base, "incremental": {"urls": []}, "confidential": True})
    with pytest.raises(ValueError, match="at least one delta URL"):
        validate_definition({**base, "format": "bloom", "options": {}, "incremental": {"urls": []}})
