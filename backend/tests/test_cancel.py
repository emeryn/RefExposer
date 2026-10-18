import time


def wait_phase(env, ref_id, phase, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = env.get(f"/api/referentials/{ref_id}").json()["current_run"]
        if run and run["phase"] == phase:
            return run
        time.sleep(0.05)
    raise TimeoutError(phase)


def test_cancel_interrupts_a_long_transformation(env):
    # A transformation producing billions of rows: only a cancellation can stop it
    cfg = {"id": "huge", "name": "Huge", "format": "txt", "source": {"type": "local", "path": "raw/*.txt"},
           "transform": "SELECT value, i FROM {source}, range(5000000000) t(i)"}
    (env.data_dir / "huge" / "raw").mkdir(parents=True)
    (env.data_dir / "huge" / "raw" / "w.txt").write_text("alpha\nbeta\n", encoding="utf-8")
    assert env.post("/api/admin/referentials", json={"config": cfg, "pull": False}).status_code == 201
    assert env.post("/api/referentials/huge/refresh").status_code == 200
    wait_phase(env, "huge", "transform")
    time.sleep(1)
    t0 = time.time()
    assert env.delete("/api/referentials/huge/run").json() == {"cancelled": True}
    deadline = time.time() + 15
    while env.get("/api/referentials/huge").json()["current_run"] and time.time() < deadline:
        time.sleep(0.1)
    assert time.time() - t0 < 10, "the transformation was not interrupted"
    last = env.get("/api/referentials/huge/runs", params={"limit": 1}).json()[0]
    assert last["status"] == "cancelled", last
    detail = env.get("/api/referentials/huge").json()
    assert detail["status"] == "cancelled" and not detail["has_data"]
    assert not (env.data_dir / "huge" / ".work" / "next.parquet").exists()


def test_cancel_without_running_update(env):
    assert env.delete("/api/referentials/countries/run").status_code == 404
