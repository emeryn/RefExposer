"""Sources of tens of GB (THC passive DNS: a .parquet.gz of 53 GB, 91 GB decompressed): decompressed while downloading,
the disk never filled, barely compressed Parquet rewritten with zstd, large sources not kept twice."""

import gzip
import hashlib
import time

import duckdb
import httpx
import pytest

from conftest import refresh
from test_parquet_large import _RangeHandler, _Server, add, logs  # noqa: F401  (fixtures below)


def rdns_parquet(path, rows: int = 300_000, compression: str = "uncompressed") -> None:
    """Reverse DNS like rows, stored like the THC dump (no compression)."""
    duckdb.execute(
        f"COPY (SELECT concat_ws('.', (i * 7919) % 223 + 1, (i * 104729) % 256, (i * 31) % 256, i % 256) AS ip, "
        f"'host-' || (i % 50000)::VARCHAR || '.isp' || (i % 97)::VARCHAR || '.example.net' AS name "
        f"FROM range({rows}) t(i)) TO '{path.as_posix()}' (FORMAT parquet, COMPRESSION {compression}, ROW_GROUP_SIZE 50000)"
    )


@pytest.fixture()
def gz_server(tmp_path):
    import threading

    raw = tmp_path / "src.parquet"
    rdns_parquet(raw, rows=100_000)
    srv = _Server(("127.0.0.1", 0), _RangeHandler)
    srv.data, srv.cuts, srv.requests, srv.etag = gzip.compress(raw.read_bytes()), [], [], '"gz1"'
    srv.plain = raw.read_bytes()  # type: ignore[attr-defined]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, f"http://127.0.0.1:{srv.server_address[1]}/rdns-sept.parquet.gz"
    srv.shutdown()


@pytest.fixture()
def no_wait(monkeypatch):
    from app import ingest

    monkeypatch.setattr(ingest.time, "sleep", lambda s: None)
    monkeypatch.setattr(ingest, "retry_wait", lambda attempt, retry_after=None: 0.0)


def test_parquet_gz_is_decompressed_while_downloading(gz_server, tmp_path, no_wait):
    from app.ingest import Run, download

    srv, url = gz_server
    srv.cuts = [len(srv.data) // 3]  # the connection drops: resumed (Range) into the same decompression
    dest = tmp_path / "raw"
    run = Run("t", "manual")
    with httpx.Client() as client:
        entry = download(client, url, dest, {}, None, None, run, fmt="parquet")
    assert entry["files"] == ["rdns-sept.parquet"] and (dest / "rdns-sept.parquet").read_bytes() == srv.plain
    assert entry["downloaded_bytes"] == len(srv.data) and entry["sha256"] == hashlib.sha256(srv.data).hexdigest()
    assert srv.requests[1]["range"] == f"bytes={len(srv.data) // 3}-"
    text = "\n".join(line["msg"] for line in run.logs)
    assert "decompressed while it is downloaded" in text and "once decompressed" in text and "Decompressed " not in text
    assert not list(dest.rglob("*.gz"))  # the compressed file never reached the disk


def test_truncated_gzip_is_corrupted(gz_server, tmp_path, no_wait):
    from app.ingest import Run, SourceCorrupted, download

    srv, url = gz_server
    srv.data = srv.data[: len(srv.data) - 100]
    with httpx.Client() as client, pytest.raises(SourceCorrupted, match="truncated"):
        download(client, url, tmp_path / "raw", {}, None, None, Run("t", "manual"), fmt="parquet")
    assert not list((tmp_path / "raw").rglob("*.parquet"))


def test_download_never_fills_the_disk(gz_server, tmp_path, no_wait, monkeypatch):
    from app import resources
    from app.ingest import Run, RunRejected, download
    from app.settings import Settings

    srv, url = gz_server
    settings = Settings(min_free_disk="1GB")
    dest = tmp_path / "raw"
    # Not enough room for what is announced: not started
    monkeypatch.setattr(resources, "free_disk", lambda path: (1 << 30) + 1000)
    with httpx.Client() as client, pytest.raises(RunRejected, match="not started"):
        download(client, url, dest, {}, None, None, Run("t", "manual"), fmt="parquet", settings=settings)
    # The disk fills up while writing: stopped before the reserve, nothing left behind
    free = iter([10 << 30] + [(1 << 30) - 1] * 1000)
    monkeypatch.setattr(resources, "free_disk", lambda path: next(free))
    monkeypatch.setattr(resources.DiskGuard, "__init__", lambda self, s, p, w, every=1: (
        setattr(self, "reserve", 1 << 30), setattr(self, "path", p), setattr(self, "what", w),
        setattr(self, "every", 1), setattr(self, "_since", 0))[0])
    with httpx.Client() as client, pytest.raises(RunRejected, match="disk almost full"):
        download(client, url, dest, {}, None, None, Run("t", "manual"), fmt="parquet", settings=settings)
    assert not [p for p in dest.rglob("*") if p.is_file()]


def test_duckdb_write_stopped_before_the_disk_is_full(tmp_path, monkeypatch):
    from app import resources
    from app.ingest import _interrupt_on_cancel

    monkeypatch.setattr(resources, "free_disk", lambda path: 100)
    con = duckdb.connect()
    watch = _interrupt_on_cancel(con, lambda: False, (tmp_path, 1 << 30))
    started = time.monotonic()
    with pytest.raises(duckdb.InterruptException):
        con.execute(f"COPY (SELECT i, md5(i::VARCHAR) FROM range(5000000000) t(i)) TO '{(tmp_path / 'x.parquet').as_posix()}'")
    watch.set()
    assert watch.disk_full and "disk almost full" in watch.disk_full and time.monotonic() - started < 30


def test_uncompressed_parquet_is_rewritten_and_not_kept_twice(env, http_dir, monkeypatch):
    from app import ingest

    monkeypatch.setattr(ingest, "LARGE_SOURCE_BYTES", 1 << 20)  # "large" from 1 MB
    monkeypatch.setattr(env.app_.state.service.settings, "keep_raw_max", "1MB")
    src = env.www / "rdns-sept.parquet"
    rdns_parquet(src, rows=300_000)
    (env.www / "rdns-sept.parquet.gz").write_bytes(gzip.compress(src.read_bytes()))
    add(env, {"id": "rdns", "name": "rDNS", "format": "parquet", "key": "ip",
              "source": {"urls": [f"http://127.0.0.1:{http_dir[1]}/rdns-sept.parquet.gz"]}})
    run = refresh(env, "rdns")
    text = logs(run)
    assert run["status"] == "success" and run["rows"] == 300_000, text
    assert "stored with UNCOMPRESSED" in text and "rewritten with zstd" in text, text
    assert "more than REFEX_KEEP_RAW_MAX" in text, text
    published = env.data_dir / "rdns" / "current.parquet"
    assert published.stat().st_size < src.stat().st_size * 0.8
    codecs = {r[0] for r in duckdb.execute(f"SELECT compression FROM parquet_metadata('{published.as_posix()}')").fetchall()}
    assert codecs == {"ZSTD"}
    assert not [p for p in (env.data_dir / "rdns" / "raw").rglob("*") if p.is_file()]  # the 2nd copy is gone
    assert not (env.data_dir / "rdns" / ".work").exists()
    # Unchanged: answered by the ETag, nothing downloaded although the source was not kept
    run = refresh(env, "rdns")
    assert run["status"] == "unchanged", logs(run)


def test_compressed_parquet_still_published_as_is(env, monkeypatch):
    from app import ingest

    monkeypatch.setattr(ingest, "LARGE_SOURCE_BYTES", 1 << 20)
    raw = env.data_dir / "zrdns" / "raw"
    raw.mkdir(parents=True)
    rdns_parquet(raw / "z.parquet", rows=300_000, compression="zstd")
    add(env, {"id": "zrdns", "name": "z", "format": "parquet", "source": {"type": "local", "path": "raw/*.parquet"}})
    run = refresh(env, "zrdns")
    assert run["status"] == "success" and "published as is" in logs(run) and "rewritten" not in logs(run), logs(run)


def test_failed_build_leaves_no_work_files(env, monkeypatch):
    raw = env.data_dir / "bad" / "raw"
    raw.mkdir(parents=True)
    rdns_parquet(raw / "b.parquet", rows=1000)
    add(env, {"id": "bad", "name": "bad", "format": "parquet", "source": {"type": "local", "path": "raw/*.parquet"},
              "transform": "SELECT *, 1 / 0 AS boom, nope FROM {source}"})
    run = refresh(env, "bad")
    assert run["status"] == "error", run
    assert not (env.data_dir / "bad" / ".work").exists()


def test_previous_version_removed_when_the_disk_is_short(tmp_path, monkeypatch):
    from app import resources
    from app.ingest import Run, _make_room
    from app.settings import Settings
    from app.storage import RefPaths

    paths = RefPaths(tmp_path, "r").ensure()
    paths.previous.write_bytes(b"x" * 1000)
    meta = {"raw_size": 60 << 30, "parquet_size": 25 << 30, "has_previous": True}
    monkeypatch.setattr(resources, "free_disk", lambda path: 70 << 30)
    run = Run("r", "manual")
    _make_room(Settings(min_free_disk="5GB"), run, paths, meta)
    assert not paths.previous.exists() and meta["has_previous"] is False
    assert "previous version removed" in run.logs[-1]["msg"]
    paths.previous.write_bytes(b"x")
    monkeypatch.setattr(resources, "free_disk", lambda path: 500 << 30)
    _make_room(Settings(min_free_disk="5GB"), Run("r", "manual"), paths, meta)
    assert paths.previous.exists()


def test_orphan_partial_of_a_killed_service_is_removed(gz_server, tmp_path, no_wait):
    """The service killed while decompressing a download leaves a .part without state: the next run removes it."""
    from app.ingest import Run, _partial, download

    srv, url = gz_server
    dest = tmp_path / "raw"
    part, state_file = _partial(dest, url)
    part.parent.mkdir(parents=True)
    part.write_bytes(b"\0" * 5_000_000)  # decompressed bytes of the killed run, no state file
    with httpx.Client() as client:
        entry = download(client, url, dest, {}, None, None, Run("t", "manual"), fmt="parquet")
    assert (dest / "rdns-sept.parquet").read_bytes() == srv.plain and entry["files"] == ["rdns-sept.parquet"]
    assert srv.requests[0]["range"] is None  # started from the beginning, not "resumed" into garbage
    assert not list((dest / ".partial").glob("*"))


def sync_flushed_gzip(data: bytes, every: int = 20_000) -> bytes:
    """gzip with a sync flush every `every` bytes, like pigz or Python's gzip.flush() (the THC dump has them)."""
    import struct
    import zlib

    co = zlib.compressobj(6, zlib.DEFLATED, -zlib.MAX_WBITS)
    body = b"".join(co.compress(data[i:i + every]) + co.flush(zlib.Z_SYNC_FLUSH) for i in range(0, len(data), every)) + co.flush()
    header = b"\x1f\x8b\x08\x08\x00\x00\x00\x00\x02\x03rdns.parquet\x00"
    return header + body + struct.pack("<II", zlib.crc32(data), len(data) % (1 << 32))


@pytest.fixture()
def sync_gz_server(tmp_path, monkeypatch):
    import threading

    from app import ingest

    monkeypatch.setattr(ingest, "RESUME_POINT_EVERY", 50_000)
    raw = tmp_path / "src.parquet"
    rdns_parquet(raw, rows=100_000)
    srv = _Server(("127.0.0.1", 0), _RangeHandler)
    srv.plain = raw.read_bytes()  # type: ignore[attr-defined]
    srv.data, srv.cuts, srv.requests, srv.etag = sync_flushed_gzip(srv.plain), [], [], '"gz1"'
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, f"http://127.0.0.1:{srv.server_address[1]}/rdns-sept.parquet.gz"
    srv.shutdown()


def interrupted_run(client, url, dest, cut_at, srv):
    """A first run stopped in the middle (the service restarted): what it leaves for the next one."""
    from app.ingest import Run, download

    srv.cuts = [cut_at]
    first = Run("t", "manual")
    first.retries = 0
    with pytest.raises(httpx.TransportError):
        download(client, url, dest, {}, None, None, first, fmt="parquet")


def test_decompressed_download_resumed_by_the_next_run(sync_gz_server, tmp_path, no_wait):
    import orjson

    from app.ingest import Run, _partial, download

    srv, url = sync_gz_server
    dest = tmp_path / "raw"
    cut = int(len(srv.data) * 0.7)
    with httpx.Client() as client:
        interrupted_run(client, url, dest, cut, srv)
        part, state_file = _partial(dest, url)
        state = orjson.loads(state_file.read_bytes())
        assert part.exists() and state["inflate"] and state["points"], state
        last_c = state["points"][-1][0]
        run = Run("t", "manual")
        entry = download(client, url, dest, {}, None, None, run, fmt="parquet")
    assert (dest / "rdns-sept.parquet").read_bytes() == srv.plain
    text = "\n".join(line["msg"] for line in run.logs)
    assert "Download resumed after a restart" in text and "Checking the CRC32" in text, text
    assert srv.requests[1]["range"] == f"bytes={last_c}-" and srv.requests[-1]["range"] is not None  # never from zero
    assert entry["sha256"] and not list(dest.glob(".partial/*"))


def test_wrong_resume_point_falls_back(sync_gz_server, tmp_path, no_wait):
    from app.ingest import Run, _partial, download

    srv, url = sync_gz_server
    dest = tmp_path / "raw"
    with httpx.Client() as client:
        interrupted_run(client, url, dest, int(len(srv.data) * 0.8), srv)
        part, _ = _partial(dest, url)
        with part.open("r+b") as f:  # the bytes after the last point no longer match the stream
            f.seek(part.stat().st_size - 10)
            f.write(b"X" * 10)
        run = Run("t", "manual")
        download(client, url, dest, {}, None, None, run, fmt="parquet")
    assert (dest / "rdns-sept.parquet").read_bytes() == srv.plain  # an earlier point, or from zero: never a wrong file


def test_gzip_without_sync_points_starts_again(gz_server, tmp_path, no_wait):
    from app.ingest import Run, _partial, download

    srv, url = gz_server  # plain gzip.compress: no sync flush
    dest = tmp_path / "raw"
    with httpx.Client() as client:
        interrupted_run(client, url, dest, len(srv.data) // 2, srv)
        part, _ = _partial(dest, url)
        assert not part.exists()  # nothing to resume from: nothing kept
        download(client, url, dest, {}, None, None, Run("t", "manual"), fmt="parquet")
    assert (dest / "rdns-sept.parquet").read_bytes() == srv.plain


def test_only_failures_without_progress_count(sync_gz_server, tmp_path, no_wait):
    """HuggingFace cuts long transfers every few GB: each cut that brought data starts the count of attempts again."""
    from app.ingest import Run, download

    srv, url = sync_gz_server
    step = len(srv.data) // 8
    srv.cuts = [step] * 7  # 7 cuts, each after some progress
    run = Run("t", "manual")
    run.retries = 2
    with httpx.Client() as client:
        download(client, url, tmp_path / "raw", {}, None, None, run, fmt="parquet")
    assert (tmp_path / "raw" / "rdns-sept.parquet").read_bytes() == srv.plain
    assert sum("failed (" in line["msg"] for line in run.logs) == 7


def test_stop_of_the_service_is_not_a_crash(env):
    import orjson

    from app.storage import RefPaths, read_meta

    service = env.app_.state.service
    paths = RefPaths(env.data_dir, "countries")
    journal = {"id": "r1", "ref_id": "countries", "trigger": "manual", "phase": "download", "status": "running",
               "shutdown": True, "logs": [{"t": "x", "level": "info", "msg": "Downloading"}]}
    paths.running.write_bytes(orjson.dumps(journal))
    service.reload()
    meta = read_meta(paths)
    assert meta["status"] == "cancelled" and "stop of the service" in meta["last_error"] and "crashed" not in meta, meta
    assert meta["resume_after_restart"] is True
    last = env.get("/api/referentials/countries/runs", params={"limit": 1}).json()[0]
    assert last["status"] == "cancelled" and "out of memory" not in last["message"]

    # A journal without the flag, but a clean stop recorded by the previous process: same thing
    journal.pop("shutdown")
    paths.running.write_bytes(orjson.dumps(journal))
    service._stop_marker.parent.mkdir(parents=True, exist_ok=True)
    service._stop_marker.write_text("{}")
    service.start()
    assert not service._stop_marker.exists()
    deadline = time.time() + 20
    while time.time() < deadline and env.get("/api/referentials/countries").json()["current_run"]:
        time.sleep(0.1)
    runs = env.get("/api/referentials/countries/runs", params={"limit": 2}).json()
    assert runs[0]["trigger"] == "restart" and runs[0]["status"] == "success", runs  # started again by itself
    assert "crashed" not in read_meta(paths) and "resume_after_restart" not in read_meta(paths)

    # Killed without any stop asked (no flag, no marker): still reported as a crash
    journal["id"] = "r3"
    paths.running.write_bytes(orjson.dumps(journal))
    service.reload()
    meta = read_meta(paths)
    assert meta["status"] == "error" and "without being asked" in meta["last_error"] and meta["crashed"]["count"] == 1


def test_filters_that_would_read_a_huge_file_are_refused_at_once(env, monkeypatch):
    """6 billion rows of passive DNS without sort on the column looked up: about 2 hours per lookup. Refused at once,
    with what to configure, instead of reading tens of GB until the query timeout."""
    raw = env.data_dir / "bigdns" / "raw"
    raw.mkdir(parents=True)
    rdns_parquet(raw / "p.parquet", rows=20_000, compression="zstd")
    add(env, {"id": "bigdns", "name": "pDNS", "format": "parquet", "key": "name", "source": {"type": "local", "path": "raw/*.parquet"},
              "storage": {"sort_by": ["name"]}})
    assert refresh(env, "bigdns")["status"] == "success"
    monkeypatch.setattr(env.app_.state.service.settings, "full_scan_max", "1KB")  # "huge" from 1 KB

    r = env.get("/api/referentials/bigdns/rows", params={"ip": "1.2.3.4"})
    assert r.status_code == 400 and "too large" in r.json()["detail"] and "sort_by" in r.json()["detail"], r.text
    r = env.post("/api/referentials/bigdns/lookup", json={"values": ["1.2.3.4"], "column": "ip"})
    assert r.status_code == 400 and "too large" in r.json()["detail"], r.text
    # On the sorted column: only the relevant blocks are read, allowed
    name = env.get("/api/referentials/bigdns/rows", params={"limit": 1}).json()["rows"][0]["name"]
    assert env.get("/api/referentials/bigdns/rows", params={"name": name}).status_code == 200
    assert env.get(f"/api/referentials/bigdns/lookup/{name}").status_code == 200
    assert env.post("/api/referentials/bigdns/lookup", json={"values": [name]}).json()["found"] == 1
    # Both filters: the sorted one narrows the read, allowed
    assert env.get("/api/referentials/bigdns/rows", params={"name": name, "ip": "1.2.3.4"}).status_code == 200
