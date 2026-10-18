"""Disk space view: administrators only, read only, hard links counted once, no symbolic link followed."""

import os

from conftest import create_user, new_client, refresh, USER_PASSWORD


def test_storage_report(env, tmp_path):
    from app import diskusage

    refresh(env, "countries")
    root = env.data_dir
    # A Parquet source published as is: the same file twice (hard link), counted once
    (root / "linked" / "raw").mkdir(parents=True)
    (root / "linked" / "raw" / "s.parquet").write_bytes(b"x" * 300_000)
    os.link(root / "linked" / "raw" / "s.parquet", root / "linked" / "current.parquet")
    (root / "linked" / "raw" / ".partial").mkdir()
    (root / "linked" / "raw" / ".partial" / "a.part").write_bytes(b"y" * 100_000)
    # A symbolic link to something outside data/: never followed
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "big.bin").write_bytes(b"z" * 2_000_000)
    os.symlink(outside, root / "linked" / "escape")
    diskusage._cache = None

    r = env.get("/api/system/storage")
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["volumes"] and doc["volumes"][0]["name"].startswith("data") and doc["volumes"][0]["free"] > 0
    by_id = {x["id"]: x for x in doc["referentials"]}
    linked = by_id["linked"]
    assert linked["orphan"] is True and linked["name"] is None  # no referential with this id: left by a deleted one
    assert linked["total"] < 600_000, linked  # hard link once, symlink not followed
    assert set(linked["categories"]) <= {"published", "sources", "downloading"} and linked["categories"]["downloading"] > 0
    countries = by_id["countries"]
    assert countries["orphan"] is False and countries["categories"].get("published", 0) > 0 and countries["rows"]
    assert all("outside" not in f["path"] for f in doc["largest_files"])

    # Read only and administrators only
    create_user(env, "bob")
    bob = new_client(env, "bob", USER_PASSWORD)
    assert bob.get("/api/system/storage").status_code == 403
    assert env.post("/api/system/storage").status_code == 405
    assert env.delete("/api/system/storage").status_code == 405
