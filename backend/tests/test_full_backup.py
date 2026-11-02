"""Full backups: the archive, its encryption, the restore, and the copy to an S3 bucket."""

import json
import pathlib
import tarfile

import pytest

from conftest import ADMIN_PASSWORD, create_user, new_client, refresh
from test_s3_source import KEY_ID, SECRET, S3Server

CONFIRM = "RESTORE EVERYTHING"


@pytest.fixture()
def ready(env):
    """An instance with published data, an extra account and an internal referential to find again afterwards."""
    assert refresh(env, "countries")["status"] == "success"
    create_user(env, "alice", role="advanced")
    make_internal(env, "notes", "Notes")
    assert env.post("/api/referentials/notes/records", json={"code": "X1", "label": "une note"}).status_code in (200, 201)
    wait_published(env, "notes")
    return env


def make_internal(client, ref_id, name):
    r = client.post("/api/internal-referentials", json={
        "id": ref_id, "name": name, "key": "code",
        "columns": [{"name": "code", "type": "text"}, {"name": "label", "type": "text"}]})
    assert r.status_code == 201, r.text
    wait_published(client, ref_id)


def wait_published(client, ref_id, timeout=30):
    """An internal referential publishes a moment after the last edit (internal_publish_delay)."""
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        detail = client.get(f"/api/referentials/{ref_id}").json()
        if not detail["current_run"] and detail.get("version"):
            return
        time.sleep(0.1)
    raise TimeoutError(ref_id)


def write_backup(env, timeout=60, **params):
    """Write a full backup through the task and return its finished run."""
    import time

    body = {"enabled": True, "params": params} if params else {"enabled": True}
    assert env.put("/api/admin/tasks/full_backup", json=body).status_code == 200
    previous = next(t for t in env.get("/api/admin/tasks").json() if t["id"] == "full_backup")["last_run"]
    assert env.post("/api/admin/full-backups/run").status_code == 200
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = next(t for t in env.get("/api/admin/tasks").json() if t["id"] == "full_backup")
        if not task["running"] and task["last_run"] and task["last_run"] != previous:
            return task["last_run"]
        time.sleep(0.05)
    raise TimeoutError("full_backup")


# --------------------------------------------------------------------------- the archive

def test_archive_holds_everything_and_leaves_the_key_out(ready, env):
    run = write_backup(env)
    assert run["status"] == "success", run
    backups = env.get("/api/admin/full-backups").json()["backups"]
    assert len(backups) == 1
    name = backups[0]["name"]
    assert name.startswith("refexposer-full-") and name.endswith(".tar.gz.enc")
    assert backups[0]["encrypted"] is True

    manifest = env.get(f"/api/admin/full-backups/{name}/manifest").json()
    assert manifest["format"] == "refexposer-full-backup"
    assert "countries" in manifest["referentials"] and "notes" in manifest["referentials"]
    assert manifest["counts"]["users"] >= 2 and manifest["counts"]["internal_records"] == 1
    assert "sessions" not in manifest["counts"]  # never carried over
    assert manifest["include_raw"] is False

    # The key that protects the archive is never inside it, and neither are the regenerable folders
    from app import fullbackup

    path = fullbackup.path_of(env.data_dir, name)
    names = _members(path)
    assert "manifest.json" == names[0] and "database.json.gz" in names
    assert any(n.startswith("data/countries/") for n in names)
    assert not any(".secret_key" in n for n in names)
    assert not any("/raw/" in n or "/exports/" in n or "/.tmp/" in n for n in names)
    assert any(n.startswith("config/") for n in names)


def _members(path):
    from app import fullbackup

    tar, raw = fullbackup._open(path)
    try:
        return [m.name for m in tar]
    finally:
        tar.close()
        raw.close()


def test_raw_sources_on_demand(ready, env):
    assert "countries/raw/a.csv" not in " ".join(_local_members(env, write_backup(env)))
    names = _local_members(env, write_backup(env, include_raw="yes"))
    assert any(n.endswith("countries/raw/a.csv") for n in names), names


def _local_members(env, run):
    from app import fullbackup

    return _members(fullbackup.path_of(env.data_dir, run["details"]["file"]))


def test_unencrypted_archive_is_a_plain_tar(ready, env):
    run = write_backup(env, encrypt="no")
    name = run["details"]["file"]
    assert name.endswith(".tar.gz") and not name.endswith(".enc")
    from app import fullbackup

    with tarfile.open(fullbackup.path_of(env.data_dir, name), "r:gz") as tar:
        assert tar.getnames()[0] == "manifest.json"


def test_a_damaged_or_foreign_archive_is_refused(ready, env):
    from app import fullbackup

    path = fullbackup.path_of(env.data_dir, write_backup(env)["details"]["file"])
    raw = bytearray(path.read_bytes())

    # Truncated: the end marker is gone
    short = path.with_name(path.name.replace("-full-", "-full-99999999-999998"))
    short = path.parent / "refexposer-full-20990101-000001.tar.gz.enc"
    short.write_bytes(raw[: len(raw) // 2])
    with pytest.raises(fullbackup.BackupError, match="truncated"):
        fullbackup.read_manifest(short)

    # One byte changed in the middle of a chunk: the authentication tag no longer matches
    raw[len(raw) // 2] ^= 0xFF
    altered = path.parent / "refexposer-full-20990101-000002.tar.gz.enc"
    altered.write_bytes(bytes(raw))
    with pytest.raises(fullbackup.BackupError, match="another REFEX_SECRET_KEY, or is damaged"):
        fullbackup.read_manifest(altered)

    # Something else entirely
    other = path.parent / "refexposer-full-20990101-000003.tar.gz"
    other.write_bytes(b"not an archive at all")
    with pytest.raises(fullbackup.BackupError):
        fullbackup.read_manifest(other)


# --------------------------------------------------------------------------- restoring

def test_restore_puts_back_the_data_the_accounts_and_the_definitions(ready, env):
    name = write_backup(env)["details"]["file"]

    # Everything changes after the backup: a row dropped, a referential deleted, an account gone
    assert env.delete("/api/referentials/notes/records/X1").status_code in (200, 204)
    assert env.post("/api/admin/referentials/delete", json={"ids": ["notes"], "purge": True}).status_code == 200
    users = {u["username"]: u["id"] for u in env.get("/api/admin/users").json()}
    assert env.delete(f"/api/admin/users/{users['alice']}").status_code in (200, 204)
    (env.data_dir / "countries" / "current.parquet").unlink()

    r = env.post(f"/api/admin/full-backups/{name}/restore", json={"confirm": CONFIRM})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["signed_out"] is True and "countries" in out["referentials"]

    # The deleted account, the deleted referential, its row and the published file are all back
    client = new_client(env, "admin", ADMIN_PASSWORD)
    assert "alice" in {u["username"] for u in client.get("/api/admin/users").json()}
    assert client.get("/api/referentials/notes/records").json()["total"] == 1
    assert (env.data_dir / "countries" / "current.parquet").exists()
    assert client.get("/api/referentials/countries/lookup/FR").json()["label"] == "France"


def test_restore_needs_the_confirmation_word(ready, env):
    name = write_backup(env)["details"]["file"]
    assert env.post(f"/api/admin/full-backups/{name}/restore", json={"confirm": "RESTORE"}).status_code == 400
    assert env.post(f"/api/admin/full-backups/{name}/restore", json={"confirm": ""}).status_code == 400
    assert env.post("/api/admin/full-backups/unknown.tar.gz/restore", json={"confirm": CONFIRM}).status_code == 404


def test_restore_keeps_what_came_later_unless_asked(ready, env):
    name = write_backup(env)["details"]["file"]
    make_internal(env, "after", "Après")

    # By default a referential created after the backup keeps its data on disk
    assert env.post(f"/api/admin/full-backups/{name}/restore", json={"confirm": CONFIRM}).status_code == 200
    assert (env.data_dir / "after").exists()

    client = new_client(env, "admin", ADMIN_PASSWORD)  # the restore closed every session
    make_internal(client, "after", "Après")
    r = client.post(f"/api/admin/full-backups/{name}/restore", json={"confirm": CONFIRM, "remove_extra": True})
    assert r.status_code == 200 and "after" in r.json()["removed"]
    assert not (env.data_dir / "after").exists()


def test_a_failed_restore_puts_the_data_back(ready, env, monkeypatch):
    """A move that fails halfway rolls back: the data found before the restore is still there."""
    from app import fullbackup

    name = write_backup(env)["details"]["file"]
    before = (env.data_dir / "countries" / "current.parquet").read_bytes()

    # The second folder moved in fails: the first one must be put back
    real = pathlib.Path.replace
    calls = {"n": 0}

    def flaky(self, target):
        if self.parent.name == "data" and self.parent.parent.name.startswith(".restore-"):
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("disk went away")
        return real(self, target)

    monkeypatch.setattr(pathlib.Path, "replace", flaky)
    with pytest.raises(OSError):
        with __import__("app.db", fromlist=["session_factory"]).session_factory()() as db:
            fullbackup.restore(db, env.app_.state.service.settings, fullbackup.path_of(env.data_dir, name))
    monkeypatch.undo()
    assert calls["n"] >= 2  # the rollback was actually reached
    assert (env.data_dir / "countries" / "current.parquet").read_bytes() == before
    assert not any(p.name.endswith("-replaced") for p in env.data_dir.iterdir())


def test_exact_restore_also_drops_definition_files(ready, env):
    """With remove_extra, a YAML file the backup does not carry goes too: it would bring its referentials back."""
    name = write_backup(env)["details"]["file"]
    config_dir = env.app_.state.service.settings.config_dir
    (config_dir / "added-later.yml").write_text(
        "referentials:\n  - id: later\n    name: Later\n    source: {type: local, path: 'raw/*.csv'}\n"
        "    format: csv\n    key: code\n",
        encoding="utf-8",
    )

    # Without remove_extra the file stays
    assert env.post(f"/api/admin/full-backups/{name}/restore", json={"confirm": CONFIRM}).status_code == 200
    assert (config_dir / "added-later.yml").exists()

    client = new_client(env, "admin", ADMIN_PASSWORD)
    r = client.post(f"/api/admin/full-backups/{name}/restore", json={"confirm": CONFIRM, "remove_extra": True})
    assert r.status_code == 200, r.text
    assert not (config_dir / "added-later.yml").exists()
    assert (config_dir / "refs.yml").exists()  # the one the backup carries


def test_back_up_now_works_while_the_task_is_scheduled_off(ready, env):
    """The enabled flag only governs the schedule: "Back up now" still writes one."""
    import time

    assert env.put("/api/admin/tasks/full_backup", json={"enabled": False}).status_code == 200
    assert env.post("/api/admin/full-backups/run").status_code == 200
    deadline = time.time() + 60
    while time.time() < deadline:
        if env.get("/api/admin/full-backups").json()["backups"]:
            return
        time.sleep(0.1)
    raise AssertionError("no backup written")


def test_restore_refuses_a_backup_of_another_schema_version(ready, env):
    """A dump made on an older schema is refused with what to do about it, rather than restored into it."""
    from app import fullbackup
    from app.db import session_factory

    path = fullbackup.path_of(env.data_dir, write_backup(env)["details"]["file"])
    staging = env.data_dir / ".tmp" / "altered"
    _, database = fullbackup.extract(path, staging)
    database["alembic_revision"] = "0001"
    with session_factory()() as db:
        with pytest.raises(fullbackup.BackupError, match="another schema version"):
            fullbackup.restore_database(db, database)
    # Nothing was emptied: the check happens before any deletion
    assert env.get("/api/admin/users").json()


def test_upload_and_restore(ready, env):
    from app import fullbackup

    name = write_backup(env)["details"]["file"]
    payload = fullbackup.path_of(env.data_dir, name).read_bytes()
    assert env.post("/api/admin/referentials/delete", json={"ids": ["notes"], "purge": True}).status_code == 200

    r = env.post("/api/admin/full-backups/restore-upload",
                 files={"file": (name, payload, "application/octet-stream")},
                 data={"confirm": CONFIRM})
    assert r.status_code == 200, r.text
    client = new_client(env, "admin", ADMIN_PASSWORD)
    assert client.get("/api/referentials/notes/records").json()["total"] == 1

    # A file that is not a full backup
    bad = client.post("/api/admin/full-backups/restore-upload",
                      files={"file": ("something.zip", b"x", "application/zip")}, data={"confirm": CONFIRM})
    assert bad.status_code == 400 and "full backup" in bad.text


def test_only_administrators(ready, env):
    name = write_backup(env)["details"]["file"]
    create_user(env, "bob")
    from conftest import USER_PASSWORD

    bob = new_client(env, "bob", USER_PASSWORD)
    assert bob.get("/api/admin/full-backups").status_code == 403
    assert bob.post("/api/admin/full-backups/run").status_code == 403
    assert bob.post(f"/api/admin/full-backups/{name}/restore", json={"confirm": CONFIRM}).status_code == 403
    assert bob.get(f"/api/admin/full-backups/{name}").status_code == 403


def test_delete_and_retention(ready, env):
    env.put("/api/admin/settings/backup", json={"keep_local": 1, "encrypt": True, "include_raw": False})
    write_backup(env)
    write_backup(env)
    backups = env.get("/api/admin/full-backups").json()["backups"]
    assert len(backups) == 1, backups  # only the newest is kept
    assert env.delete(f"/api/admin/full-backups/{backups[0]['name']}").status_code == 200
    assert env.get("/api/admin/full-backups").json()["backups"] == []


# --------------------------------------------------------------------------- the bucket copy

@pytest.fixture()
def bucket(env):
    server = S3Server()
    server.objects["backups"] = {}
    yield server
    server.stop()


def enable_bucket(env, server, **extra):
    body = {"s3_enabled": True, "endpoint": server.endpoint, "bucket": "backups", "prefix": "refexposer/",
            "access_key_id": KEY_ID, "secret_access_key": SECRET, "path_style": True, "keep_s3": 2, **extra}
    r = env.put("/api/admin/settings/backup", json=body)
    assert r.status_code == 200, r.text
    return r


def test_copy_to_the_bucket_and_back(ready, env, bucket):
    enable_bucket(env, bucket)
    r = env.post("/api/admin/full-backups/remote/test")
    assert r.status_code == 200 and r.json()["writable"] is True

    run = write_backup(env)
    assert run["status"] == "success", run
    name = run["details"]["file"]
    assert run["details"]["s3_key"] == f"refexposer/{name}"
    assert f"refexposer/{name}" in bucket.objects["backups"]

    remote = env.get("/api/admin/full-backups/remote/list").json()
    assert [b["name"] for b in remote] == [name] and remote[0]["encrypted"] is True

    # Brought back onto the server, then restored like a local one
    env.delete(f"/api/admin/full-backups/{name}")
    assert env.get("/api/admin/full-backups").json()["backups"] == []
    assert env.post(f"/api/admin/full-backups/remote/{name}/fetch").status_code == 200
    assert [b["name"] for b in env.get("/api/admin/full-backups").json()["backups"]] == [name]
    assert env.post(f"/api/admin/full-backups/{name}/restore", json={"confirm": CONFIRM}).status_code == 200

    client = new_client(env, "admin", ADMIN_PASSWORD)
    assert client.delete(f"/api/admin/full-backups/remote/{name}").status_code == 200
    assert client.get("/api/admin/full-backups/remote/list").json() == []


def test_bucket_retention_and_local_deletion(ready, env, bucket):
    enable_bucket(env, bucket, keep_s3=2, delete_local_after_upload=True, keep_local=10)
    names = [write_backup(env)["details"]["file"] for _ in range(3)]
    assert len(set(names)) == 3, names
    # Only the last two remain in the bucket, and nothing is left on the server
    kept = sorted(k.rsplit("/", 1)[-1] for k in bucket.objects["backups"])
    assert kept == sorted(names[-2:]), kept
    assert env.get("/api/admin/full-backups").json()["backups"] == []


def test_a_bucket_that_fails_does_not_lose_the_backup(ready, env, bucket):
    enable_bucket(env, bucket, secret_access_key="wrong-secret-value")
    run = write_backup(env)
    # The archive is on the server: only the off-site copy failed
    assert run["status"] == "warning"
    assert "AccessDenied" in json.dumps(run["details"])
    assert "wrong-secret-value" not in json.dumps(run)
    assert len(env.get("/api/admin/full-backups").json()["backups"]) == 1


def test_bucket_routes_need_the_copy_enabled(ready, env):
    assert env.get("/api/admin/full-backups/remote/list").status_code == 409
    assert env.post("/api/admin/full-backups/remote/x/fetch").status_code == 409


def test_settings_are_checked_and_the_key_is_never_returned(env, bucket):
    assert env.put("/api/admin/settings/backup", json={"s3_enabled": True, "bucket": "backups"}).status_code == 422
    assert env.put("/api/admin/settings/backup", json={"s3_enabled": True, "endpoint": bucket.endpoint,
                                                       "bucket": "Not Valid"}).status_code == 422
    assert env.put("/api/admin/settings/backup", json={"endpoint": bucket.endpoint, "bucket": "backups",
                                                       "access_key_id": KEY_ID}).status_code == 422
    assert env.put("/api/admin/settings/backup", json={"delete_local_after_upload": True}).status_code == 422

    enable_bucket(env, bucket)
    shown = env.get("/api/admin/settings").json()["backup"]
    assert shown["secret_access_key"] == "" and shown["secret_access_key_set"] is True
    assert SECRET not in json.dumps(shown)


def test_generated_downloads_and_temporary_files_stay_out(ready, env):
    """What the archive leaves out is rebuilt or worthless: generated downloads, temporary files, old backups."""
    # Generating a CSV download puts a file in exports/
    assert env.get("/api/referentials/countries/download/csv").status_code == 200
    assert list((env.data_dir / "countries" / "exports").glob("*.csv"))
    (env.data_dir / ".tmp").mkdir(exist_ok=True)
    (env.data_dir / ".tmp" / "leftover.bin").write_bytes(b"x")

    names = _local_members(env, write_backup(env))
    assert not any("/exports/" in n for n in names), [n for n in names if "exports" in n]
    assert not any("/.tmp/" in n or n.endswith("leftover.bin") for n in names)
    assert not any("/.backups/" in n for n in names)
    assert not any(n.endswith(("refexposer.db", "refexposer.db-wal", "refexposer.db-shm")) for n in names)
    # What it does carry: the published file, its metadata and the run history
    assert "data/countries/current.parquet" in names
    assert "data/countries/meta.json" in names
