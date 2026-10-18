import http.server
import json
import threading

import pytest

from conftest import refresh


@pytest.fixture()
def flaky(monkeypatch):
    """Server failing the first requests of each path (503 with Retry-After, or connection cut), then serving."""
    from app import ingest

    monkeypatch.setattr(ingest, "retry_wait", lambda attempt, retry_after=None: 0.05 if retry_after is None else float(retry_after))
    state = {"fail": {}, "hits": {}}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits = state["hits"][self.path] = state["hits"].get(self.path, 0) + 1
            mode, count = state["fail"].get(self.path, ("", 0))
            if hits <= count and mode == "503":
                self.send_response(503)
                self.send_header("Retry-After", "0")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if hits <= count and mode == "cut":
                self.wfile.write(b"HTTP/1.1 200 OK\r\nContent-Length: 1000\r\n\r\nid,na")
                self.wfile.flush()
                self.connection.close()
                return
            body = b"id,name\n1,a\n2,b\n"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    state["base"] = f"http://127.0.0.1:{server.server_address[1]}"
    yield state
    server.shutdown()


def logs(env, ref_id) -> str:
    return "\n".join(line["msg"] for line in env.get(f"/api/referentials/{ref_id}/runs", params={"limit": 1}).json()[0]["logs"])


def test_download_retried_when_the_site_fails(env, flaky):
    flaky["fail"] = {"/a.csv": ("503", 2), "/b.csv": ("cut", 2)}
    for ref_id in ("a", "b"):
        cfg = {"id": ref_id, "name": ref_id, "format": "csv", "source": {"urls": [f"{flaky['base']}/{ref_id}.csv"]}}
        assert env.post("/api/admin/referentials", json={"config": cfg, "pull": False}).status_code == 201
        run = refresh(env, ref_id)
        assert run["status"] == "success" and run["rows"] == 2, (run, logs(env, ref_id))
        assert flaky["hits"][f"/{ref_id}.csv"] == 3
        assert "attempt 3/6" in logs(env, ref_id)
    assert "HTTP 503" in logs(env, "a")


def test_download_retries_of_the_source(env, flaky):
    flaky["fail"] = {"/c.csv": ("503", 10)}
    cfg = {"id": "c", "name": "c", "format": "csv", "source": {"urls": [f"{flaky['base']}/c.csv"], "retries": 1}}
    assert env.post("/api/admin/referentials", json={"config": cfg, "pull": False}).status_code == 201
    run = refresh(env, "c")
    assert run["status"] == "error" and "503" in run["message"], run
    assert flaky["hits"]["/c.csv"] == 2  # the first attempt and one more


def test_retry_wait():
    from app.ingest import retry_wait

    assert [retry_wait(n) for n in (1, 2, 3, 8)] == [1, 2, 4, 60]
    assert retry_wait(1, "7") == 7 and retry_wait(1, "100000") == 300


def test_update_that_killed_the_service_is_not_started_again(env):
    """An update going on when the service died (out of memory): recorded with its logs, and the automatic
    updates of the referential stop until one is started by hand, instead of killing the service at each restart."""
    service = env.app_.state.service
    run = refresh(env, "countries")
    assert run["status"] == "success", run
    data = env.data_dir / "countries"
    (data / "running.json").write_text(json.dumps({
        "id": "dead", "ref_id": "countries", "trigger": "sync", "status": "running", "phase": "transform",
        "logs": [{"t": "2026-10-14T20:00:00Z", "level": "info", "msg": "Transforming and writing parquet (sorted on rrname)"}]}))
    service.reload()
    detail = env.get("/api/referentials/countries").json()
    last = env.get("/api/referentials/countries/runs", params={"limit": 1}).json()[0]
    assert last["id"] == "dead" and last["status"] == "error" and "'transform' phase" in last["message"], last
    assert last["logs"][0]["msg"].startswith("Transforming")
    assert not (data / "running.json").exists()
    assert "out of memory" in (detail.get("last_error") or json.loads((data / "meta.json").read_text())["last_error"])
    # Automatic updates skipped, an update started by hand runs and lifts the guard
    assert service.submit("countries", "startup") is None and service.submit("countries", "schedule") is None
    run = refresh(env, "countries", force=True)
    assert run["status"] == "success", run
    assert "crashed" not in json.loads((data / "meta.json").read_text())
    assert service.submit("countries", "schedule") is not None


def test_resource_limits(monkeypatch):
    from app import resources
    from app.settings import Settings

    monkeypatch.setattr(resources, "total_memory", lambda: 32 << 30)
    monkeypatch.setattr(resources, "cpu_count", lambda: 16)
    s = Settings(max_concurrent_jobs=2)
    assert resources.ingest_limits(s) == (f"{int((32 << 30) * 0.6) // 2 // (1 << 20)}MB", 8)
    assert resources.engine_limits(s)[0] == f"{(8 << 30) // (1 << 20)}MB"
    s = Settings(duckdb_memory_limit="4GB", duckdb_threads=3)
    assert resources.ingest_limits(s) == ("4GB", 3) and resources.engine_limits(s) == ("4GB", 3)
    assert Settings(duckdb_threads="", duckdb_memory_limit=" ").duckdb_threads is None


def test_quality_of_a_large_referential_on_a_sample(tmp_path, monkeypatch):
    import duckdb

    from app.quality import QualityConfig, evaluate
    from app.sqlbuild import parquet_sample

    path = tmp_path / "big.parquet"
    con = duckdb.connect()
    con.execute(f"COPY (SELECT i % 1000 AS k, CASE WHEN i % 10 = 0 THEN NULL ELSE 'x' END AS v FROM range(2000000) t(i)) "
                f"TO '{path}' (FORMAT parquet, ROW_GROUP_SIZE 10000)")
    cfg = QualityConfig(rules=[{"column": "v", "check": "not_null"}, {"column": "k", "check": "unique"}])
    rel = f"read_parquet('{path}')"
    sample = parquet_sample(str(path), None, 2_000_000, 100_000, 10_000)
    full = evaluate(con, rel, {"k", "v"}, cfg, "k", sample=sample)
    assert not full["sampled"] and full["checks"][0]["checked"] == 2_000_000
    assert not full["checks"][1]["ok"]  # k repeats
    big = evaluate(con, rel, {"k", "v"}, cfg, "k", large=True, sample=sample)
    assert big["sampled"] and big["checks"][0]["checked"] == 100_000  # one block of 10,000 rows in 20
    assert [c["column"] for c in big["checks"]] == ["v", "k", "k"]  # order of the rules kept
    assert abs(big["checks"][0]["pass_pct"] - 90) < 2
    assert big["checks"][1]["skipped"] and big["checks"][1]["ok"]


def test_sql_delta_applied_in_wal_mode(tmp_path):
    import sqlite3

    import pytest

    from app.ingest import Run, RunRejected, apply_sql_delta
    from app.resources import parse_size

    db = tmp_path / "base.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE t (k TEXT PRIMARY KEY)")
    con.commit()
    con.close()
    (tmp_path / "d1.sql").write_text("BEGIN TRANSACTION;\n" + "".join(f"INSERT INTO t VALUES ('{i}');\n" for i in range(1000)) + "COMMIT;\n")
    assert apply_sql_delta(db, tmp_path / "d1.sql", Run("t", "manual"), cache_kib=1 << 20) == 1000
    (tmp_path / "d2.sql").write_text("INSERT INTO t VALUES ('x');\nINSERT INTO t VALUES (NULL, 'y');\n")  # error
    with pytest.raises(RunRejected):
        apply_sql_delta(db, tmp_path / "d2.sql", Run("t", "manual"))
    con = sqlite3.connect(db)
    assert con.execute("SELECT count(*) FROM t").fetchone()[0] == 1000  # the broken delta changed nothing
    con.close()
    # A row already in the database is skipped, like the NIST deltas re-inserting an existing row
    (tmp_path / "d3.sql").write_text("INSERT INTO t VALUES ('x');\nINSERT INTO t VALUES ('1');\n")
    run = Run("t", "manual")
    assert apply_sql_delta(db, tmp_path / "d3.sql", run) == 2
    assert "1 rows already in the database, skipped" in run.logs[-1]["msg"]
    con = sqlite3.connect(db)
    assert con.execute("SELECT count(*) FROM t").fetchone()[0] == 1001
    assert con.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    con.close()
    assert not (tmp_path / "base.db-wal").exists()
    assert parse_size("8GB") == 8 << 30 and parse_size("1126MB") == 1126 << 20 and parse_size("4 GiB") == 4 << 30 and parse_size("x") is None
