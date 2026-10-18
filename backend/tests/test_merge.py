"""Merged referentials: each new data set (source URL, upload, import folder) is a delta added to the published version."""

import os
import time

import pytest

from conftest import refresh


def wait_idle(client, ref_id, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not client.get(f"/api/referentials/{ref_id}").json()["current_run"]:
            return client.get(f"/api/referentials/{ref_id}/runs", params={"limit": 1}).json()[0]
        time.sleep(0.1)
    raise TimeoutError(ref_id)


def rows(env, ref_id) -> dict[str, dict]:
    data = env.get(f"/api/referentials/{ref_id}/rows", params={"limit": 1000}).json()
    return {r["ioc"]: r for r in data["rows"]}


_tick = [0]


def serve(env, name: str, content: str) -> None:
    """New content of a served file, with a later date (the test server answers 304 within the same second)."""
    path = env.www / name
    path.write_text(content)
    _tick[0] += 10
    os.utime(path, (time.time() + _tick[0], time.time() + _tick[0]))


def logs(env, ref_id) -> str:
    return "\n".join(line["msg"] for line in env.get(f"/api/referentials/{ref_id}/runs", params={"limit": 1}).json()[0]["logs"])


def test_deltas_from_the_source_url_are_merged_by_key(env, http_dir):
    port = http_dir[1]
    serve(env, "iocs.csv", "ioc,family\n1.1.1.1,emotet\n2.2.2.2,qakbot\n3.3.3.3,dridex\n")
    cfg = {"id": "iocs", "name": "IOCs", "format": "csv", "key": "ioc", "source": {"urls": [f"http://127.0.0.1:{port}/iocs.csv"]},
           "merge": {"delete_column": "_deleted"}}
    assert env.post("/api/admin/referentials", json={"config": cfg, "pull": False}).status_code == 201

    # 1. First data set: the base
    run = refresh(env, "iocs")
    assert run["status"] == "success" and run["rows"] == 3, run
    assert "first data set" in logs(env, "iocs")

    # 2. A delta: one row replaced (same key), one added, the others kept
    serve(env, "iocs.csv", "ioc,family,score\n2.2.2.2,pikabot,90\n4.4.4.4,lumma,70\n")
    run = refresh(env, "iocs")
    assert run["status"] == "success" and run["rows"] == 4, (run, logs(env, "iocs"))
    assert "Delta merged into the published version: 2 row(s) (upsert on 'ioc')" in logs(env, "iocs")
    data = rows(env, "iocs")
    assert data["2.2.2.2"]["family"] == "pikabot" and data["2.2.2.2"]["score"] == 90
    assert data["1.1.1.1"]["family"] == "emotet" and data["1.1.1.1"]["score"] is None  # new column: empty for older rows
    assert run["changes"]["added"] == 1 and run["changes"]["removed"] == 0, run
    info = env.get("/api/referentials/iocs").json()
    assert info["merge"]["deltas"] == 1 and info["merge"]["last_delta_rows"] == 2, info["merge"]

    # 3. Rows flagged for deletion remove their key; the flag column is not published
    serve(env, "iocs.csv", "ioc,family,_deleted\n1.1.1.1,,true\n5.5.5.5,remcos,false\n")
    run = refresh(env, "iocs")
    assert run["status"] == "success" and run["rows"] == 4, (run, logs(env, "iocs"))
    data = rows(env, "iocs")
    assert "1.1.1.1" not in data and data["5.5.5.5"]["family"] == "remcos" and "_deleted" not in data["5.5.5.5"]
    assert "1 flagged for deletion" in logs(env, "iocs")

    # 4. The same delta again: nothing changes
    run = refresh(env, "iocs")
    assert run["status"] == "unchanged", run

    # 5. An empty delta keeps the published version
    serve(env, "iocs.csv", "ioc,family\n")
    run = refresh(env, "iocs")
    assert run["status"] in ("unchanged", "corrupted"), (run, logs(env, "iocs"))
    assert env.get("/api/referentials/iocs").json()["row_count"] == 4

    # 6. replace: the data set of the source alone becomes the new base
    serve(env, "iocs.csv", "ioc,family\n9.9.9.9,asyncrat\n")
    r = env.post("/api/referentials/iocs/refresh", params={"replace": True})
    assert r.status_code == 200 and r.json()["replace"] is True, r.text
    run = wait_idle(env, "iocs")
    assert run["status"] == "success" and run["rows"] == 1, (run, logs(env, "iocs"))
    assert list(rows(env, "iocs")) == ["9.9.9.9"]
    assert env.get("/api/referentials/iocs").json()["merge"]["deltas"] == 0


def test_pushed_deltas_are_merged(env):
    cfg = {"id": "pushed", "name": "Pushed", "format": "csv", "key": "ioc", "source": {"type": "local", "path": "raw/*.csv"},
           "merge": {}}
    (env.data_dir / "pushed" / "raw").mkdir(parents=True)
    (env.data_dir / "pushed" / "raw" / "base.csv").write_text("ioc,family\na,x\nb,y\n")
    assert env.post("/api/admin/referentials", json={"config": cfg, "pull": False}).status_code == 201
    assert refresh(env, "pushed")["rows"] == 2

    def push(content: str, **form):
        r = env.post("/api/referentials/pushed/import", files=[("files", ("delta.csv", content.encode(), "text/csv"))], data=form)
        assert r.status_code == 200, r.text
        run = wait_idle(env, "pushed")
        env.delete("/api/referentials/pushed/pin")
        return run

    run = push("ioc,family\nb,z\nc,w\n")
    assert run["status"] == "success" and run["rows"] == 3, (run, logs(env, "pushed"))
    assert rows(env, "pushed")["b"]["family"] == "z"
    run = push("ioc,family\nd,v\n")
    assert run["rows"] == 4
    run = push("ioc,family\nz,new base\n", mode="replace")
    assert run["status"] == "success" and run["rows"] == 1 and run["replace"] is True, run


def test_deltas_without_key_add_rows_once(env, http_dir):
    port = http_dir[1]
    serve(env, "list.csv", "ioc,family\na,x\nb,y\n")
    cfg = {"id": "plain", "name": "Plain", "format": "csv", "source": {"urls": [f"http://127.0.0.1:{port}/list.csv"]}, "merge": {}}
    assert env.post("/api/admin/referentials", json={"config": cfg, "pull": False}).status_code == 201
    assert refresh(env, "plain")["rows"] == 2
    serve(env, "list.csv", "ioc,family\nb,y\nc,z\n")
    run = refresh(env, "plain")
    assert run["status"] == "success" and run["rows"] == 3, (run, logs(env, "plain"))
    assert "identical rows kept once" in logs(env, "plain")


def test_merge_config_checks():
    from app.config import validate_definition

    base = {"id": "m", "name": "m", "source": {"urls": ["https://a/b"]}}
    with pytest.raises(ValueError, match="key"):
        validate_definition({**base, "format": "csv", "merge": {"delete_column": "_deleted"}})
    with pytest.raises(ValueError, match="'merge' applies to tables"):
        validate_definition({**base, "format": "bloom", "merge": {}})
    ref = validate_definition({**base, "format": "csv", "key": "k", "merge": {}})
    assert ref.is_merge and ref.config_hash != validate_definition({**base, "format": "csv", "key": "k"}).config_hash
    assert not validate_definition({**base, "format": "csv", "merge": {"enabled": False}}).is_merge
