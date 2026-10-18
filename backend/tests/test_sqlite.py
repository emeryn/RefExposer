import io
import json
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


# NIST NSRL publications: download page, full database, chained quarterly deltas -----------------------------------
RDS_SCHEMA = """
CREATE TABLE FILE (sha256 VARCHAR NOT NULL, sha1 VARCHAR NOT NULL, md5 VARCHAR NOT NULL, crc32 VARCHAR NOT NULL,
  file_name VARCHAR NOT NULL, file_size INTEGER NOT NULL, package_id INTEGER NOT NULL,
  CONSTRAINT PK_FILE__FILE PRIMARY KEY (sha256, sha1, md5, crc32, file_name, file_size, package_id));
CREATE TABLE MFG (manufacturer_id INTEGER NOT NULL, name VARCHAR NOT NULL);
CREATE TABLE OS (operating_system_id INTEGER NOT NULL, name VARCHAR NOT NULL, version VARCHAR NOT NULL, manufacturer_id INTEGER NOT NULL);
CREATE TABLE PKG (package_id INTEGER NOT NULL, name VARCHAR NOT NULL, version VARCHAR NOT NULL, operating_system_id INTEGER NOT NULL,
  manufacturer_id INTEGER NOT NULL, language VARCHAR NOT NULL, application_type VARCHAR NOT NULL);
CREATE TABLE VERSION (version VARCHAR UNIQUE NOT NULL, build_set VARCHAR NOT NULL, build_date TIMESTAMP NOT NULL,
  release_date TIMESTAMP NOT NULL, description VARCHAR NOT NULL);
CREATE VIEW DISTINCT_HASH AS SELECT DISTINCT sha256, sha1, md5, crc32 FROM FILE;
"""


def rds_version(v: str) -> str:
    return f"INSERT INTO VERSION(version,build_set,build_date,release_date,description) VALUES('{v}','legacy','2026-01-01','2026-01-01','test');"


def rds_full(env, tmp_path, version: str, count: int) -> None:
    db = tmp_path / f"RDS_{version}_legacy_minimal.db"
    con = sqlite3.connect(db)
    con.executescript(RDS_SCHEMA + "".join(insert(i, 'a"b.dll' if i == 1 else None) for i in range(1, count + 1))
                      + "INSERT INTO MFG VALUES (1,'Microsoft'); INSERT INTO OS VALUES (1,'Windows','11',1);"
                      + "INSERT INTO PKG VALUES (0,'Office','2016',1,1,'English','Office'),(1,'Windows','11',1,1,'English','OS'),"
                      + "(2,'Linux','6',1,1,'English','OS');" + rds_version(version))
    con.commit()
    con.close()
    folder = f"RDS_{version}_legacy_minimal"
    (env.www / f"{folder}.zip").write_bytes(nsrl_zip(folder, {db.name: db.read_bytes()}))


def rds_delta(env, version: str, previous: str, statements: list[str]) -> None:
    folder = f"RDS_{version}_legacy_minimal_delta"
    (env.www / f"{folder}.zip").write_bytes(nsrl_zip(folder, {f"{folder}.sql": delta(
        statements + [f"DELETE FROM VERSION WHERE version='{previous}';", rds_version(version)])}))


def rds_page(env, port: int, fulls: list[str], deltas: list[str]) -> None:
    """Download page linking every publication, of several sets (only the chosen one is used)."""
    links = [f"RDS_{v}_{s}.zip" for v in fulls for s in ("legacy", "legacy_minimal", "modern_minimal")]
    links += [f"RDS_{v}_{s}_delta.zip" for v in deltas for s in ("legacy", "legacy_minimal", "modern_minimal")]
    # Full databases linked by absolute URLs, deltas by relative ones (both found)
    html = "".join(f'<li><a href="{"" if "delta" in link else f"http://127.0.0.1:{port}/"}{link}">{link}</a></li>' for link in links)
    (env.www / "nsrl.html").write_text(f"<html><body><ul>{html}</ul></body></html>")


def test_nsrl_publications_found_and_chained(env, http_dir, tmp_path):
    port = http_dir[1]
    rds_full(env, tmp_path, "2025.03.1", 5)
    rds_full(env, tmp_path, "2026.03.1", 100)
    rds_delta(env, "2026.03.1", "2025.12.1", [insert(i) for i in range(1, 50)])  # already in the full 2026.03.1
    rds_delta(env, "2026.06.1", "2026.03.1", [insert(i) for i in range(101, 121)] + [f"DELETE FROM FILE WHERE sha1 = '{sha(5, 40)}';"])
    rds_page(env, port, ["2025.03.1", "2026.03.1"], ["2025.06.1", "2026.03.1", "2026.06.1"])
    cfg = {"id": "nsrl", "name": "NSRL", "format": "sqlite", "source": {"urls": [f"http://127.0.0.1:{port}/nsrl.html"]},
           "options": {"nsrl": "legacy_minimal"}, "key": "SHA-1", "storage": {"sort_by": ["SHA-1"]}}
    r = env.post("/api/admin/referentials", json={"config": cfg, "pull": False})
    assert r.status_code == 201, r.text

    # Preview: the publications to download, nothing downloaded
    p = env.post("/api/admin/referentials/preview", json={"source": cfg["source"], "format": "sqlite", "options": cfg["options"]}).json()
    assert [f["name"] for f in p["files"]] == ["RDS_2026.03.1_legacy_minimal.zip", "RDS_2026.06.1_legacy_minimal_delta.zip"], p
    assert "2026.06.1" in p["unanalysed"] and p["records_path_candidates"][0] == "NSRLFile"

    # 1. Latest full database, then the deltas published after it (not the one published with it)
    run = refresh(env, "nsrl")
    assert run["status"] == "success" and run["rows"] == 119, (run, logs(env, "nsrl"))
    text = logs(env, "nsrl")
    assert "Full database 2026.03.1 needed" in text and "1 delta(s) to apply: 2026.06.1" in text and "release 2026.06.1" in text
    info = env.get("/api/referentials/nsrl").json()
    assert [c["name"] for c in info["columns"]] == ["SHA-1", "MD5", "CRC32", "FileName", "FileSize", "ProductCode", "OpSystemCode", "SpecialCode", "SourceFile"]
    row = env.get(f"/api/referentials/nsrl/lookup/{sha(1, 40)}").json()
    assert row["FileName"] == "ab.dll" and row["OpSystemCode"] == "0" and row["ProductCode"] == 1, row  # RDS 2.x conversion
    # Publication each row comes from: the full database, or the delta that inserted it
    assert row["SourceFile"] == "RDS_2026.03.1_legacy_minimal.db", row
    row = env.get(f"/api/referentials/nsrl/lookup/{sha(101, 40)}").json()
    assert row["SourceFile"] == "RDS_2026.06.1_legacy_minimal_delta.sql", row
    assert env.get(f"/api/referentials/nsrl/lookup/{sha(5, 40)}").status_code == 404
    state = json.loads((env.data_dir / "nsrl" / "meta.json").read_text())["incremental"]
    assert state["version"] == "2026.06.1" and state["base"]["set"] == "legacy_minimal"

    # 2. Nothing new published: nothing downloaded
    run = refresh(env, "nsrl")
    assert run["status"] == "unchanged", run
    assert "Downloading" not in logs(env, "nsrl")

    # 3. A new quarterly delta on the page: only this one is downloaded and applied
    rds_delta(env, "2026.09.1", "2026.06.1", [insert(i) for i in range(121, 131)])
    rds_page(env, port, ["2025.03.1", "2026.03.1"], ["2025.06.1", "2026.03.1", "2026.06.1", "2026.09.1"])
    run = refresh(env, "nsrl")
    assert run["status"] == "success" and run["rows"] == 129, run
    text = logs(env, "nsrl")
    assert "RDS_2026.03.1_legacy_minimal.zip" not in text and "1 delta(s) to apply: 2026.09.1" in text
    assert env.get(f"/api/referentials/nsrl/lookup/{sha(121, 40)}").json()["SourceFile"] == "RDS_2026.09.1_legacy_minimal_delta.sql"
    assert env.get(f"/api/referentials/nsrl/lookup/{sha(101, 40)}").json()["SourceFile"] == "RDS_2026.06.1_legacy_minimal_delta.sql"

    # 4. A delta made for another release (gap in the chain): rejected, the published version stays. The full database
    # is not downloaded again: older than the kept one, it would not help
    rds_delta(env, "2026.12.1", "2026.10.1", [insert(i) for i in range(131, 141)])
    rds_page(env, port, ["2026.03.1"], ["2026.06.1", "2026.09.1", "2026.12.1"])
    run = refresh(env, "nsrl")
    assert run["status"] == "rejected" and "the database is at release 2026.09.1" in run["message"], run
    assert "loaded again" not in logs(env, "nsrl")
    assert env.get("/api/referentials/nsrl").json()["row_count"] == 129

    # 5. Fixed publication: applied
    rds_delta(env, "2026.12.1", "2026.09.1", [insert(i) for i in range(131, 141)])
    run = refresh(env, "nsrl")
    assert run["status"] == "success" and run["rows"] == 139, run

    # 6. Deltas imported by hand: skipped when the database already holds the release, refused out of sequence
    folder = "RDS_2026.09.1_legacy_minimal_delta"
    r = env.post("/api/referentials/nsrl/import", files=[("files", (f"{folder}.zip", (env.www / f"{folder}.zip").read_bytes(), "application/zip"))])
    assert r.status_code == 200, r.text
    run = wait_idle(env, "nsrl")
    assert "skipped: the database is already at release 2026.12.1" in logs(env, "nsrl"), run
    env.delete("/api/referentials/nsrl/pin")
    sql = delta([insert(200)] + ["DELETE FROM VERSION WHERE version='2027.03.1';", rds_version("2027.06.1")])
    r = env.post("/api/referentials/nsrl/import", files=[("files", ("RDS_2027.06.1_legacy_minimal_delta.sql", sql, "application/sql"))])
    run = wait_idle(env, "nsrl")
    assert run["status"] == "rejected" and "updates release 2027.03.1" in run["message"], run
    env.delete("/api/referentials/nsrl/pin")
    sql = delta([insert(200)] + ["DELETE FROM VERSION WHERE version='2026.12.1';", rds_version("2027.03.1")])
    r = env.post("/api/referentials/nsrl/import", files=[("files", ("RDS_2027.03.1_legacy_minimal_delta.sql", sql, "application/sql"))])
    run = wait_idle(env, "nsrl")
    assert run["status"] == "success" and run["rows"] == 140, run
    assert env.get(f"/api/referentials/nsrl/lookup/{sha(200, 40)}").json()["SourceFile"] == "RDS_2027.03.1_legacy_minimal_delta.sql"
    env.delete("/api/referentials/nsrl/pin")

    # 7. The NIST tables as they are: same publication column
    cfg["options"], cfg["key"], cfg["storage"] = {"nsrl": "legacy_minimal", "table": "FILE"}, "sha1", {}
    assert env.put("/api/admin/referentials/nsrl", json={"config": cfg, "pull": False}).status_code == 200
    run = refresh(env, "nsrl")
    assert run["status"] == "success" and run["rows"] == 140, (run, logs(env, "nsrl"))
    assert env.get(f"/api/referentials/nsrl/lookup/{sha(2, 40)}").json()["source_file"] == "RDS_2026.03.1_legacy_minimal.db"
    assert env.get(f"/api/referentials/nsrl/lookup/{sha(125, 40)}").json()["source_file"] == "RDS_2026.09.1_legacy_minimal_delta.sql"


def test_listed_delta_already_in_the_full_database_is_skipped(env, http_dir, tmp_path):
    """The delta published with a full release (same version) is not applied twice: no UNIQUE constraint error."""
    base_url = f"http://127.0.0.1:{http_dir[1]}"
    rds_full(env, tmp_path, "2026.03.1", 50)
    rds_delta(env, "2026.03.1", "2025.12.1", [insert(i) for i in range(1, 50)])
    rds_delta(env, "2026.06.1", "2026.03.1", [insert(i) for i in range(51, 61)])
    rds_delta(env, "2026.09.1", "2026.06.1", [insert(i) for i in range(61, 66)])
    cfg = {"id": "rds", "name": "RDS", "format": "sqlite", "source": {"urls": [f"{base_url}/RDS_2026.03.1_legacy_minimal.zip"]},
           "options": {"table": "NSRLFile"}, "incremental": {"urls": [f"{base_url}/RDS_2026.03.1_legacy_minimal_delta.zip",
                                                                       f"{base_url}/RDS_2026.09.1_legacy_minimal_delta.zip"]}}
    assert env.post("/api/admin/referentials", json={"config": cfg, "pull": False}).status_code == 201
    run = refresh(env, "rds")
    # 2026.03.1 skipped (already in the full database), 2026.09.1 refused: 2026.06.1 is missing
    assert run["status"] == "rejected" and "the database is at release 2026.03.1" in run["message"], run
    assert "skipped: the database is already at release 2026.03.1" in logs(env, "rds")
    cfg["incremental"]["urls"].insert(1, f"{base_url}/RDS_2026.06.1_legacy_minimal_delta.zip")
    env.put("/api/admin/referentials/rds", json={"config": cfg, "pull": False})
    run = refresh(env, "rds")
    assert run["status"] == "success" and run["rows"] == 65, (run, logs(env, "rds"))


def test_nsrl_config_defaults():
    import pytest

    from app.config import validate_definition
    from app.nsrl import DOWNLOAD_PAGE

    ref = validate_definition({"id": "n", "name": "n", "format": "sqlite", "source": {}, "options": {"nsrl": "ios_minimal"}})
    assert ref.source.urls == [DOWNLOAD_PAGE] and ref.options["table"] == "NSRLFile" and ref.incremental is not None
    # A publication given as the source (full database of a release): the NIST page is read instead
    publication = "https://s3.amazonaws.com/rds.nsrl.nist.gov/RDS/rds_2026.03.1/RDS_2026.03.1_modern_minimal.zip"
    ref = validate_definition({"id": "n", "name": "n", "format": "sqlite", "source": {"type": "http", "urls": [publication]},
                               "options": {"nsrl": "modern_minimal", "table": "FILE"}})
    assert ref.source.urls == [DOWNLOAD_PAGE] and ref.options["table"] == "FILE"
    ref = validate_definition({"id": "n", "name": "n", "format": "sqlite", "source": {"urls": ["https://mirror.example/nsrl.html"]},
                               "options": {"nsrl": "ios"}})
    assert ref.source.urls == ["https://mirror.example/nsrl.html"]
    with pytest.raises(ValueError, match="one of"):
        validate_definition({"id": "n", "name": "n", "format": "sqlite", "source": {}, "options": {"nsrl": "nope"}})
    with pytest.raises(ValueError, match="do not list them"):
        validate_definition({"id": "n", "name": "n", "format": "sqlite", "source": {}, "options": {"nsrl": "ios"},
                             "incremental": {"urls": ["https://a/d.zip"]}})


def test_nsrl_plan():
    from app.nsrl import plan

    fulls = {"2025.03.1": "f25", "2026.03.1": "f26"}
    deltas = {"2025.12.1": "d1", "2026.03.1": "d2", "2026.06.1": "d3", "2026.09.1": "d4"}
    assert plan(fulls, deltas, None) == ("2026.03.1", [("2026.06.1", "d3"), ("2026.09.1", "d4")])
    assert plan(fulls, deltas, "2025.12.1") == (None, [("2026.03.1", "d2"), ("2026.06.1", "d3"), ("2026.09.1", "d4")])
    assert plan(fulls, deltas, "2026.09.1") == (None, [])


def test_nsrl_page_detected():
    from app.nsrl import DOWNLOAD_PAGE
    from app.preview import _is_nsrl_page

    assert _is_nsrl_page({"type": "http", "urls": [DOWNLOAD_PAGE]})
    assert not _is_nsrl_page({"type": "http", "urls": ["https://s3.amazonaws.com/rds.nsrl.nist.gov/RDS/rds_2026.03.1/RDS_2026.03.1_ios_minimal.zip"]})
    assert not _is_nsrl_page({"type": "http", "urls": ["https://example.org/nsrl"]})


def test_nsrl_discover_never_reads_a_publication():
    """A database URL given as the page (tens of GB) fails at once, instead of being read in memory."""
    import httpx
    import pytest

    from app import nsrl

    served = []

    def handler(request: httpx.Request) -> httpx.Response:
        served.append(request.url.path)
        if request.url.path.endswith(".bin"):
            return httpx.Response(200, headers={"content-type": "application/octet-stream"}, content=b"x" * 1000)
        if request.url.path.endswith("big.html"):
            return httpx.Response(200, headers={"content-type": "text/html"}, content=b"x" * (nsrl.PAGE_MAX_BYTES + 1))
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, content=(
            b'<a href="/RDS/rds_2026.03.1/RDS_2026.03.1_ios_minimal.zip">full</a>'
            b'<a href="rds_2026.06.1/RDS_2026.06.1_ios_minimal_delta.zip?x=1">delta</a>'
            b'<a href="RDS_2026.06.1_ios_delta.zip">other set</a>'))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(nsrl.NsrlError, match="publication, not the NIST download page"):
            nsrl.discover(client, "https://s3.example/RDS/rds_2026.03.1/RDS_2026.03.1_ios_minimal.zip", "ios_minimal")
        assert served == []
        with pytest.raises(nsrl.NsrlError, match="not a web page"):
            nsrl.discover(client, "https://nist.example/file.bin", "ios_minimal")
        with pytest.raises(nsrl.NsrlError, match="more than"):
            nsrl.discover(client, "https://nist.example/big.html", "ios_minimal")
        fulls, deltas = nsrl.discover(client, "https://nist.example/nsrl/page", "ios_minimal")
    assert fulls == {"2026.03.1": "https://nist.example/RDS/rds_2026.03.1/RDS_2026.03.1_ios_minimal.zip"}
    assert deltas == {"2026.06.1": "https://nist.example/nsrl/rds_2026.06.1/RDS_2026.06.1_ios_minimal_delta.zip"}
