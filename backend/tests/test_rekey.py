"""Restoring a full backup onto a *second* instance: its own key is given, and everything the archive carries
is re-encrypted with the key of the instance doing the restore (app/rekey.py)."""

import json

import duckdb
import pytest

from conftest import ADMIN_PASSWORD, new_client
from test_full_backup import make_internal, wait_published, write_backup

OTHER_KEY = "second-instance-key-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
STAFF = {"id": "staff", "name": "Staff", "key": "email", "confidential": True,
         "columns": [{"name": "email", "type": "text"}, {"name": "salary", "type": "integer"}]}


def switch_key(env, monkeypatch, key: str = OTHER_KEY) -> str:
    """Make the instance use another master secret, as a second instance would, and return the previous one.

    What is on disk and in the database stays encrypted with the previous key: exactly the state a restore of
    a foreign archive lands in.
    """
    from app import crypto
    from app.settings import get_settings

    previous = crypto.material().decode()
    monkeypatch.setenv("REFEX_SECRET_KEY", key)
    get_settings.cache_clear()
    crypto.reset_cache()
    assert crypto.material().decode() == key != previous
    return previous


def confidential_instance(env):
    """An instance holding everything a key protects: confidential files, confidential rows, secrets."""
    from app.db import session_factory
    from app.models import User
    from sqlalchemy import select

    url = env.get("/api/admin/referentials/remote").json()["config"]["source"]["urls"][0]
    cfg = {"id": "secret", "name": "Secret countries", "format": "csv", "key": "code", "confidential": True,
           "source": {"type": "http", "urls": [url]}}
    assert env.post("/api/admin/referentials", json={"config": cfg}).status_code == 201
    wait_published(env, "secret")

    assert env.post("/api/internal-referentials", json=STAFF).status_code == 201
    assert env.post("/api/referentials/staff/records/_bulk",
                    json={"upsert": [{"email": "alice@example.com", "salary": 51000}]}).json()["created"] == 1
    wait_published(env, "staff")

    assert env.post("/api/admin/secrets", json={"name": "api_token", "value": "s3cr3t-value"}).status_code in (200, 201)
    assert env.put("/api/admin/settings/ldap",
                   json={"enabled": False, "server_url": "ldaps://dc.example.com", "bind_dn": "cn=refex",
                         "user_base_dn": "dc=example,dc=com", "bind_password": "bind-pass"}).status_code == 200
    # A TOTP secret without going through the enrolment dance: the stored form is all the re-key cares about
    from app import crypto

    with session_factory()() as db:
        user = db.scalar(select(User).where(User.username == "admin"))
        user.totp_secret = crypto.encrypt("JBSWY3DPEHPK3PXP")
        db.commit()
    return env


def restore(client, name, **body):
    return client.post(f"/api/admin/full-backups/{name}/restore",
                       json={"confirm": "RESTORE EVERYTHING", **body})


# --------------------------------------------------------------------------- the whole round trip

def test_restore_onto_an_instance_with_another_key(env, monkeypatch):
    """The point of the option: a second instance ends up serving the data under its own key."""
    confidential_instance(env)
    name = write_backup(env)["details"]["file"]
    old_key = switch_key(env, monkeypatch)

    admin = new_client(env, "admin", ADMIN_PASSWORD)
    r = restore(admin, name, secret_key=old_key)
    assert r.status_code == 200, r.text
    done = r.json()["rekeyed"]
    assert done["failed"] == [] and done["failed_count"] == 0
    assert done["settings"] == 1 and done["totp"] == 1 and done["source_secrets"] == 1
    assert done["records"] == 1 and done["profiles"] >= 1 and done["parquet_files"] >= 1

    # Served again, through the key of this instance
    admin = new_client(env, "admin", ADMIN_PASSWORD)
    assert admin.get("/api/referentials/secret/lookup/FR").json()["population"] == 68000000
    assert admin.post("/api/sql", json={"sql": "SELECT count(*) FROM secret"}).json()["rows"] == [[5]]
    # The keyed fingerprint of the row key was computed again, so a lookup by key still finds the row
    assert admin.get("/api/referentials/staff/records/alice@example.com").json()["salary"] == 51000
    assert admin.get("/api/referentials/staff/lookup/alice@example.com").json()["salary"] == 51000

    from app import appsettings, crypto, secretstore
    from app.db import session_factory
    from app.models import SourceSecret, User
    from sqlalchemy import select

    with session_factory()() as db:
        assert appsettings.load(db, "ldap").bind_password == "bind-pass"
        assert secretstore.unseal(db.scalar(select(SourceSecret))) == "s3cr3t-value"
        assert crypto.decrypt(db.scalar(select(User.totp_secret).where(User.username == "admin"))) == "JBSWY3DPEHPK3PXP"

    # On disk: the files now read with this instance's key, and no longer with the one the backup came from
    path = (env.data_dir / "secret" / "current.parquet").as_posix()
    assert _rows(path, crypto.parquet_key("secret")) == 5
    with pytest.raises(duckdb.Error):
        _rows(path, crypto.parquet_key_with(old_key.encode(), "secret"))


def _rows(path, key):
    con = duckdb.connect()
    con.execute(f"PRAGMA add_parquet_key('{key[0]}', '{key[1]}')")
    return con.execute(f"SELECT count(*) FROM read_parquet('{path}', "
                       f"encryption_config = {{footer_key: '{key[0]}'}})").fetchone()[0]


# --------------------------------------------------------------------------- what is refused

def test_a_foreign_backup_is_refused_without_its_key(env, monkeypatch):
    make_internal(env, "notes", "Notes")
    name = write_backup(env)["details"]["file"]
    plain = write_backup(env, encrypt="no")["details"]["file"]
    switch_key(env, monkeypatch)

    admin = new_client(env, "admin", ADMIN_PASSWORD)
    # Encrypted: it does not even open
    r = restore(admin, name)
    assert r.status_code == 400 and "another REFEX_SECRET_KEY" in r.json()["detail"]
    # Unencrypted: it opens anywhere, so what refuses it is the fingerprint the manifest carries
    r = restore(admin, plain)
    assert r.status_code == 400 and "another encryption key" in r.json()["detail"]
    # The listing says so too, before anything is attempted
    listing = admin.get("/api/admin/full-backups").json()
    by_name = {b["name"]: b for b in listing["backups"]}
    assert by_name[name]["readable"] is False and by_name[name]["foreign"] is True
    assert by_name[plain]["readable"] is True and by_name[plain]["foreign"] is True
    assert len(listing["key_fingerprint"]) == 16


def test_a_wrong_key_touches_nothing(env, monkeypatch):
    make_internal(env, "notes", "Notes")
    assert env.post("/api/referentials/notes/records", json={"code": "X1", "label": "une note"}).status_code in (200, 201)
    wait_published(env, "notes")
    name = write_backup(env, encrypt="no")["details"]["file"]
    switch_key(env, monkeypatch)

    admin = new_client(env, "admin", ADMIN_PASSWORD)
    r = restore(admin, name, secret_key="not-the-key-of-that-instance-at-all-xxxxxxxx")
    assert r.status_code == 400
    assert "not the one this backup was made with" in r.json()["detail"] and "nothing was touched" in r.json()["detail"]
    # Still signed in, and the data is still there: the restore stopped before replacing anything
    assert admin.get("/api/referentials/notes/records/X1").status_code == 200
    # And the copy the archive was unpacked into went with the refusal, instead of sitting on the disk
    assert not list(env.data_dir.glob(".restore-*"))


def test_a_wrong_key_on_an_encrypted_archive_says_which_case_it_is(env, monkeypatch):
    """An encrypted archive is refused at the first chunk, before its manifest: the message says so."""
    make_internal(env, "notes", "Notes")
    name = write_backup(env)["details"]["file"]
    switch_key(env, monkeypatch)
    admin = new_client(env, "admin", ADMIN_PASSWORD)
    r = restore(admin, name, secret_key="not-the-key-of-that-instance-at-all-xxxxxxxx")
    assert r.status_code == 400
    assert "the key given does not open this backup" in r.json()["detail"]
    assert "Nothing was touched" in r.json()["detail"]
    assert not list(env.data_dir.glob(".restore-*"))


def test_the_instances_own_key_given_by_hand_changes_nothing(env, monkeypatch):
    make_internal(env, "notes", "Notes")
    from app import crypto

    name = write_backup(env)["details"]["file"]
    own = crypto.material().decode()

    r = restore(env, name, secret_key=own)
    assert r.status_code == 200, r.text
    assert r.json()["rekeyed"] is None  # recognised as our own key: nothing to re-encrypt


# --------------------------------------------------------------------------- the re-key itself

def test_re_keying_twice_changes_nothing_more(env, monkeypatch):
    """A re-key interrupted halfway can be run again: what already reads with the new key is left alone."""
    confidential_instance(env)
    name = write_backup(env)["details"]["file"]
    old_key = switch_key(env, monkeypatch)
    admin = new_client(env, "admin", ADMIN_PASSWORD)
    assert restore(admin, name, secret_key=old_key).status_code == 200

    from app import rekey
    from app.db import session_factory

    lines: list[str] = []
    with session_factory()() as db:
        again = rekey.rekey(db, env.app_.state.service.settings, old_key.encode(), log_line=lines.append)
    assert again.total == 0 and again.failed == [] and again.skipped > 0
    admin = new_client(env, "admin", ADMIN_PASSWORD)
    assert admin.get("/api/referentials/secret/lookup/FR").status_code == 200


def test_the_key_given_is_never_written_down(env, monkeypatch):
    confidential_instance(env)
    name = write_backup(env)["details"]["file"]
    old_key = switch_key(env, monkeypatch)
    admin = new_client(env, "admin", ADMIN_PASSWORD)
    assert restore(admin, name, secret_key=old_key).status_code == 200

    from app.db import session_factory
    from app.models import AuditLog
    from sqlalchemy import select

    with session_factory()() as db:
        rows = list(db.scalars(select(AuditLog).where(AuditLog.action == "fullbackup.restore")))
    assert rows, "the restore was not audited"
    dumped = json.dumps([r.detail for r in rows])
    assert old_key not in dumped and OTHER_KEY not in dumped
    assert '"rekeyed":' in dumped  # how much was re-encrypted, not with what


def test_a_re_key_reports_what_it_could_not_read(env, monkeypatch):
    """A value sealed with a third key is counted as a failure, and the rest is still re-encrypted."""
    from app import crypto, rekey
    from app.db import session_factory
    from app.models import SourceSecret
    from sqlalchemy import select

    assert env.post("/api/admin/secrets", json={"name": "api_token", "value": "s3cr3t-value"}).status_code in (200, 201)
    assert env.post("/api/admin/secrets", json={"name": "other", "value": "another"}).status_code in (200, 201)
    old = crypto.material()
    with session_factory()() as db:
        lost = db.scalar(select(SourceSecret).where(SourceSecret.name == "other"))
        lost.value = crypto.seal_with(crypto.record_key(b"a-third-key-nobody-has-any-more!!"), "x", "source-secret:other")
        db.commit()
    switch_key(env, monkeypatch)

    with session_factory()() as db:
        done = rekey.rekey(db, env.app_.state.service.settings, old)
    assert done.source_secrets == 1 and done.failed == ["secret 'other'"]
    with session_factory()() as db:
        from app import secretstore

        assert secretstore.unseal(db.scalar(select(SourceSecret).where(SourceSecret.name == "api_token"))) == "s3cr3t-value"


def test_a_key_that_reads_nothing_is_called_out(env, monkeypatch):
    from app import rekey
    from app.db import session_factory

    assert env.post("/api/admin/secrets", json={"name": "api_token", "value": "s3cr3t-value"}).status_code in (200, 201)
    switch_key(env, monkeypatch)
    with session_factory()() as db:
        with pytest.raises(rekey.RekeyError, match="does not read the backup"):
            rekey.rekey(db, env.app_.state.service.settings, b"a-key-that-was-never-used-here!!")
