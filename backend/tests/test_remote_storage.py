"""Referentials whose published data lives in an S3 bucket, with the local disk as a cache (remotestore.py).

The S3 server of test_s3_source.py checks the signature of every request, so a test passing here means the
uploads and downloads are signed as a real store expects.
"""

import time

import pytest

from conftest import refresh
from test_full_backup import wait_published
from test_s3_source import KEY_ID, SECRET, S3Server


@pytest.fixture()
def store(env):
    server = S3Server()
    server.objects["refdata"] = {}
    yield server
    server.stop()


def enable(env, server, **extra):
    body = {"enabled": True, "endpoint": server.endpoint, "bucket": "refdata", "prefix": "referentials/",
            "access_key_id": KEY_ID, "secret_access_key": SECRET, "path_style": True, **extra}
    r = env.put("/api/admin/settings/storage", json=body)
    assert r.status_code == 200, r.text
    return r


def create(env, ref_id, **extra):
    """A referential reading the local HTTP server, created from the interface (so it can be configured here)."""
    url = env.get("/api/admin/referentials/remote").json()["config"]["source"]["urls"][0]
    cfg = {"id": ref_id, "name": ref_id, "format": "csv", "key": "code",
           "source": {"type": "http", "urls": [url]}, **extra}
    r = env.post("/api/admin/referentials", json={"config": cfg, "pull": False})
    assert r.status_code == 201, r.text
    return refresh(env, ref_id)


def make_remote(env, ref_id="places", **storage):
    """A referential whose data lives in the bucket, with one version published."""
    return create(env, ref_id, storage={"location": "remote", **storage})


def keys(server):
    return sorted(server.objects["refdata"])


# --------------------------------------------------------------------------- publishing into the bucket

def test_a_published_version_goes_to_the_bucket_and_is_still_served(env, store):
    enable(env, store)
    run = make_remote(env)
    assert run["status"] == "success", run
    assert keys(store) == ["referentials/places/current.parquet"]

    detail = env.get("/api/referentials/places").json()
    assert detail["storage_location"] == "remote" and detail["storage_freed"] is False
    assert detail["remote_storage"]["bucket"] == "refdata"
    # Nothing changes for whoever reads it: the file is on the disk as well
    assert env.get("/api/referentials/places/lookup/FR").json()["label"] == "France"
    assert (env.data_dir / "places" / "current.parquet").is_file()


def test_a_local_referential_sends_nothing(env, store):
    enable(env, store)
    assert create(env, "places")["status"] == "success"
    assert keys(store) == []
    assert env.get("/api/referentials/places").json()["storage_location"] == "local"


def test_publishing_again_only_sends_what_changed(env, store):
    enable(env, store)
    make_remote(env)
    before = len(store.requests)
    env.post("/api/admin/referentials/places/storage/push")
    # Everything is already there with the same size: listed, and nothing uploaded
    puts = [r for r in store.requests[before:] if r[0] == "PUT"]
    assert puts == []


# --------------------------------------------------------------------------- freeing and bringing back

def test_freeing_the_disk_then_serving_it_again(env, store):
    enable(env, store)
    make_remote(env)
    parquet = env.data_dir / "places" / "current.parquet"
    size = parquet.stat().st_size

    r = env.post("/api/admin/referentials/places/storage/free")
    assert r.status_code == 200 and r.json()["released"] == size
    assert not parquet.exists()
    detail = env.get("/api/referentials/places").json()
    assert detail["storage_freed"] is True and detail["has_data"] is False
    # The table is no longer exposed, so the SQL console does not see it
    assert env.post("/api/sql", json={"sql": "SELECT count(*) FROM places"}).status_code >= 400

    # Asking for its data brings it back, and the answer is the same as before
    assert env.get("/api/referentials/places/lookup/FR").json()["label"] == "France"
    assert parquet.is_file() and parquet.stat().st_size == size
    detail = env.get("/api/referentials/places").json()
    assert detail["storage_freed"] is False and detail["has_data"] is True
    assert env.post("/api/sql", json={"sql": "SELECT count(*) FROM places"}).json()["rows"] == [[5]]


def test_bringing_back_by_hand(env, store):
    enable(env, store)
    make_remote(env)
    assert env.post("/api/admin/referentials/places/storage/free").status_code == 200
    r = env.post("/api/admin/referentials/places/storage/bring-back")
    assert r.status_code == 200 and r.json()["files"] == 1 and r.json()["downloaded"] > 0
    assert env.post("/api/admin/referentials/places/storage/bring-back").json()["already"] is True


def test_freeing_is_refused_when_the_bucket_does_not_hold_it(env, store):
    enable(env, store)
    make_remote(env)
    store.objects["refdata"].clear()  # the object is gone from the bucket, the meta still claims it
    (env.data_dir / "places" / "current.parquet").write_bytes(b"changed on disk")
    r = env.post("/api/admin/referentials/places/storage/free")
    assert r.status_code == 409 and "not what is on the disk" in r.json()["detail"]
    assert (env.data_dir / "places" / "current.parquet").is_file()


def test_freeing_a_local_referential_is_refused(env, store):
    enable(env, store)
    assert create(env, "places")["status"] == "success"
    r = env.post("/api/admin/referentials/places/storage/free")
    assert r.status_code == 409 and "not stored remotely" in r.json()["detail"]


def test_a_restart_does_not_download_everything(env, store):
    """A freed referential stays freed when the service reloads: that is the point of freeing it."""
    enable(env, store)
    make_remote(env)
    assert env.post("/api/admin/referentials/places/storage/free").status_code == 200
    before = len([r for r in store.requests if r[0] == "GET"])
    env.app_.state.service.reload()
    assert len([r for r in store.requests if r[0] == "GET"]) == before
    assert env.get("/api/referentials/places").json()["storage_freed"] is True


# --------------------------------------------------------------------------- the task and its quota

def run_task(env, timeout=60, **params):
    body = {"enabled": True, "params": params} if params else {"enabled": True}
    assert env.put("/api/admin/tasks/remote_storage", json=body).status_code == 200
    previous = next(t for t in env.get("/api/admin/tasks").json() if t["id"] == "remote_storage")["last_run"]
    assert env.post("/api/admin/tasks/remote_storage/run").status_code == 200
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = next(t for t in env.get("/api/admin/tasks").json() if t["id"] == "remote_storage")
        if not task["running"] and task["last_run"] and task["last_run"] != previous:
            return task["last_run"]
        time.sleep(0.05)
    raise TimeoutError("remote_storage")


def test_the_task_sends_what_the_bucket_is_missing(env, store):
    enable(env, store)
    make_remote(env)
    store.objects["refdata"].clear()  # as if the upload had failed during the update
    run = run_task(env, free=False)
    assert run["status"] == "success", run
    assert run["details"]["sent"] == ["places"]
    assert keys(store) == ["referentials/places/current.parquet"]


def test_the_task_frees_the_coldest_over_the_cache_limit(env, store):
    enable(env, store, cache_gb=0.0000001, keep_hours=0)  # about 100 bytes: anything is over it
    make_remote(env)
    run = run_task(env)
    assert run["details"]["freed"] == ["places"], run
    assert not (env.data_dir / "places" / "current.parquet").exists()
    assert env.get("/api/referentials/places").json()["storage_freed"] is True
    # And it comes back when read
    assert env.get("/api/referentials/places/lookup/FR").json()["label"] == "France"


def test_nothing_is_freed_without_a_limit_or_while_it_is_warm(env, store):
    enable(env, store, cache_gb=0, keep_hours=0)
    make_remote(env)
    assert run_task(env)["details"]["freed"] == []
    assert (env.data_dir / "places" / "current.parquet").is_file()

    # A limit, but the referential was read a moment ago: left alone
    enable(env, store, cache_gb=0.0000001, keep_hours=48)
    env.get("/api/referentials/places/lookup/FR")
    assert run_task(env)["details"]["freed"] == []
    assert (env.data_dir / "places" / "current.parquet").is_file()


def test_the_task_does_nothing_when_nothing_is_stored_remotely(env, store):
    enable(env, store)
    assert create(env, "places")["status"] == "success"
    assert run_task(env)["message"] == "No referential is stored remotely"


# --------------------------------------------------------------------------- the rest of the plumbing

def test_the_sorted_copies_travel_too(env, store):
    """An index is a second copy of the data, sorted: it goes to the bucket and comes back with the rest."""
    enable(env, store)
    assert make_remote(env, "places", indexes=["label"])["status"] == "success"
    assert keys(store) == ["referentials/places/current.by_label.parquet", "referentials/places/current.parquet"]
    assert env.post("/api/admin/referentials/places/storage/free").status_code == 200
    assert env.get("/api/referentials/places/rows", params={"limit": 1}).status_code == 200
    assert (env.data_dir / "places" / "current.by_label.parquet").is_file()
    assert env.get("/api/referentials/places").json()["index_views"]


def test_a_confidential_referential_goes_up_encrypted(env, store):
    import duckdb

    enable(env, store)
    url = env.get("/api/admin/referentials/remote").json()["config"]["source"]["urls"][0]
    cfg = {"id": "secret", "name": "Secret", "format": "csv", "key": "code", "confidential": True,
           "storage": {"location": "remote"}, "source": {"type": "http", "urls": [url]}}
    assert env.post("/api/admin/referentials", json={"config": cfg}).status_code == 201
    run = refresh(env, "secret")
    assert run["status"] == "success", run
    stored = store.objects["refdata"]["referentials/secret/current.parquet"]
    assert b"Allemagne" not in stored  # Parquet Modular Encryption: the bucket never sees the rows
    assert env.post("/api/admin/referentials/secret/storage/free").status_code == 200
    assert env.get("/api/referentials/secret/lookup/FR").json()["population"] == 68000000
    with pytest.raises(duckdb.Error):  # still encrypted after the round trip
        duckdb.connect().execute(
            f"SELECT * FROM read_parquet('{(env.data_dir / 'secret' / 'current.parquet').as_posix()}')").fetchall()


def test_deleting_the_referential_empties_the_bucket(env, store):
    enable(env, store)
    url = env.get("/api/admin/referentials/remote").json()["config"]["source"]["urls"][0]
    cfg = {"id": "temp", "name": "Temp", "format": "csv", "key": "code", "storage": {"location": "remote"},
           "source": {"type": "http", "urls": [url]}}
    assert env.post("/api/admin/referentials", json={"config": cfg}).status_code == 201
    assert refresh(env, "temp")["status"] == "success"
    assert keys(store) == ["referentials/temp/current.parquet"]
    assert env.delete("/api/admin/referentials/temp").status_code == 200
    assert keys(store) == []


def test_a_referential_whose_name_starts_like_another_is_left_alone(env, store):
    """Listing (and deleting) the objects of 'places' must not take those of 'places2' with them."""
    enable(env, store)
    assert make_remote(env, "places")["status"] == "success"
    assert make_remote(env, "places2")["status"] == "success"
    assert keys(store) == ["referentials/places/current.parquet", "referentials/places2/current.parquet"]

    # Sending one again leaves the other's object in place (the "extra objects" clean-up is per referential)
    assert env.post("/api/admin/referentials/places/storage/push").status_code == 200
    assert keys(store) == ["referentials/places/current.parquet", "referentials/places2/current.parquet"]

    assert env.delete("/api/admin/referentials/places").status_code == 200
    assert keys(store) == ["referentials/places2/current.parquet"]


def test_an_internal_referential_can_live_in_the_bucket(env, store):
    """Its rows are in the database; the published file is what goes to the bucket."""
    enable(env, store)
    schema = {"id": "notes", "name": "Notes", "key": "code", "storage_location": "remote",
              "columns": [{"name": "code", "type": "text"}, {"name": "label", "type": "text"}]}
    r = env.post("/api/internal-referentials", json=schema)
    assert r.status_code == 201, r.text
    assert r.json()["storage_location"] == "remote"
    assert env.post("/api/referentials/notes/records", json={"code": "X1", "label": "une note"}).status_code in (200, 201)
    wait_published(env, "notes")
    # The published version and the one it replaced (a first, empty version was published at creation)
    assert keys(store) == ["referentials/notes/current.parquet", "referentials/notes/previous.parquet"]

    # Editing the columns does not move the data back to the local disk
    r = env.put("/api/internal-referentials/notes", json={**schema, "storage_location": None,
                                                          "columns": [*schema["columns"], {"name": "note", "type": "text"}]})
    assert r.status_code == 200, r.text
    assert r.json()["storage_location"] == "remote"
    wait_published(env, "notes")  # the column change publishes a new version, which goes up too

    r = env.post("/api/admin/referentials/notes/storage/free")
    assert r.status_code == 200, r.text
    # Its rows come from the database, so they answer without any download...
    assert env.get("/api/referentials/notes/records/X1").json()["label"] == "une note"
    assert not (env.data_dir / "notes" / "current.parquet").exists()
    # ...while a query on the published version brings the file back
    assert env.get("/api/referentials/notes/lookup/X1").json()["label"] == "une note"
    assert (env.data_dir / "notes" / "current.parquet").is_file()


def test_usage_and_the_bucket_test(env, store):
    enable(env, store)
    make_remote(env)
    usage = env.get("/api/admin/referentials/storage/usage").json()
    assert usage["enabled"] is True and usage["bucket"] == "refdata"
    assert usage["referentials"] == ["places"] and usage["cached"] == ["places"] and usage["freed"] == []
    assert usage["cached_bytes"] > 0
    probe = env.post("/api/admin/referentials/storage-test", json={"write": True}).json()
    assert probe["ok"] is True and probe["writable"] is True and probe["anonymous"] is False


def test_the_settings_are_checked_and_the_key_is_never_returned(env, store):
    r = env.put("/api/admin/settings/storage", json={"enabled": True, "endpoint": "", "bucket": ""})
    assert r.status_code == 422 and "endpoint" in r.text
    r = env.put("/api/admin/settings/storage",
                json={"enabled": False, "endpoint": store.endpoint, "bucket": "refdata", "access_key_id": KEY_ID})
    assert r.status_code == 422 and "secret access key" in r.text
    enable(env, store)
    section = env.get("/api/admin/settings").json()["storage"]
    assert section["secret_access_key"] == "" and section["secret_access_key_set"] is True
    assert section["bucket"] == "refdata"


def dead_endpoint():
    """A port nothing listens on: a bucket that cannot be reached, without stopping the shared server."""
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return f"http://127.0.0.1:{port}"


def test_a_bucket_that_cannot_be_reached_does_not_fail_the_update(env, store):
    enable(env, store, endpoint=dead_endpoint())
    run = make_remote(env)
    assert run["status"] == "success", run  # the version is published on the disk
    assert any("could not be sent to the bucket" in line["msg"] for line in run["logs"])
    assert env.get("/api/referentials/places/lookup/FR").json()["label"] == "France"


def test_asking_for_data_that_cannot_be_brought_back_says_so(env, store):
    enable(env, store)
    make_remote(env)
    assert env.post("/api/admin/referentials/places/storage/free").status_code == 200
    enable(env, store, endpoint=dead_endpoint())  # the bucket is no longer reachable
    r = env.get("/api/referentials/places/lookup/FR")
    assert r.status_code == 503 and "could not be brought back" in r.json()["detail"]
