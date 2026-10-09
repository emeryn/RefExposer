import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import duckdb
import httpx
import pytest

from conftest import refresh


def make_parquet(path, rows: int = 5000, order: str = "i", row_group_size: int = 1000) -> None:
    """Passive DNS like rows; `order`: physical order of the rows (sorted on the domain by default)."""
    duckdb.execute(
        f"COPY (SELECT 'host' || lpad(i::VARCHAR, 6, '0') || '.example.com' AS domain, "
        f"(i * 7919) % 100000 AS ip, i % 13 AS seen FROM range({rows}) t(i) ORDER BY {order}) "
        f"TO '{path.as_posix()}' (FORMAT parquet, ROW_GROUP_SIZE {row_group_size})"
    )


def logs(run) -> str:
    return "\n".join(line["msg"] for line in run["logs"])


def add(env, cfg) -> None:
    r = env.post("/api/admin/referentials", json={"config": cfg, "pull": False})
    assert r.status_code == 201, r.text


def test_single_parquet_published_as_is(env):
    raw = env.data_dir / "rdns" / "raw"
    raw.mkdir(parents=True)
    make_parquet(raw / "rdns.parquet", order="ip")
    add(env, {"id": "rdns", "name": "rDNS", "format": "parquet", "source": {"type": "local", "path": "raw/*.parquet"}})
    run = refresh(env, "rdns")
    assert run["status"] == "success" and run["rows"] == 5000, run
    # A file of the user is copied (it may be rewritten in place), not linked nor rewritten
    assert "Parquet source published as is (copy)" in logs(run)
    assert "Transforming and writing parquet" not in logs(run)
    current = env.data_dir / "rdns" / "current.parquet"
    assert current.read_bytes() == (raw / "rdns.parquet").read_bytes()
    r = env.get("/api/referentials/rdns/rows", params={"domain": "host000042.example.com"}).json()
    assert r["total"] == 1 and r["rows"][0]["ip"] == 42 * 7919 % 100000

    # A transform needs the rewrite
    cfg = {"id": "rdns", "name": "rDNS", "format": "parquet", "source": {"type": "local", "path": "raw/*.parquet"},
           "transform": "SELECT domain, ip FROM {source}"}
    assert env.put("/api/admin/referentials/rdns", json={"config": cfg, "pull": False}).status_code == 200
    run = refresh(env, "rdns")
    assert run["status"] == "success" and "Transforming and writing parquet" in logs(run)


def test_sorted_parquet_source_is_not_sorted_again(env):
    raw = env.data_dir / "rdns" / "raw"
    raw.mkdir(parents=True)
    make_parquet(raw / "rdns.parquet", order="domain")
    cfg = {"id": "rdns", "name": "rDNS", "format": "parquet", "source": {"type": "local", "path": "raw/*.parquet"},
           "storage": {"sort_by": ["domain"]}}
    add(env, cfg)
    run = refresh(env, "rdns")
    assert run["status"] == "success" and "already sorted on 'domain'" in logs(run), logs(run)

    # Row groups overlapping on the sort column: rewritten sorted
    cfg["storage"] = {"sort_by": ["ip"], "row_group_size": 10000}
    assert env.put("/api/admin/referentials/rdns", json={"config": cfg, "pull": False}).status_code == 200
    run = refresh(env, "rdns")
    assert run["status"] == "success", run
    assert "is not sorted on 'ip'" in logs(run) and "Transforming and writing parquet (sorted on ip)" in logs(run)
    ips = [r[0] for r in duckdb.execute(f"SELECT ip FROM '{(env.data_dir / 'rdns' / 'current.parquet').as_posix()}'").fetchall()]
    assert ips == sorted(ips)


def test_downloaded_parquet_linked_and_fingerprinted_while_downloading(env, http_dir):
    make_parquet(env.www / "rdns.parquet")
    url = f"http://127.0.0.1:{http_dir[1]}/rdns.parquet"
    add(env, {"id": "rdns", "name": "rDNS", "format": "parquet", "source": {"type": "http", "urls": [url]},
              "storage": {"keep_raw": False}})
    run = refresh(env, "rdns")
    assert run["status"] == "success" and run["rows"] == 5000, run
    assert "Parquet source published as is (hard link)" in logs(run)
    meta = json.loads((env.data_dir / "rdns" / "meta.json").read_text(encoding="utf-8"))
    assert meta["sources"][0]["sha256"] == hashlib.sha256((env.www / "rdns.parquet").read_bytes()).hexdigest()
    # keep_raw: false removes the downloaded file, the published one (same inode) stays
    assert not (env.data_dir / "rdns" / "raw" / "rdns.parquet").exists()
    assert (env.data_dir / "rdns" / "current.parquet").stat().st_size == (env.www / "rdns.parquet").stat().st_size


def test_profile_reads_a_sample_of_blocks(env, monkeypatch):
    from app import ingest

    monkeypatch.setattr(ingest, "PROFILE_SAMPLE_ROWS", 20000)
    raw = env.data_dir / "rdns" / "raw"
    raw.mkdir(parents=True)
    make_parquet(raw / "rdns.parquet", rows=200000, row_group_size=50000)
    add(env, {"id": "rdns", "name": "rDNS", "format": "parquet", "source": {"type": "local", "path": "raw/*.parquet"},
              "storage": {"profile": "sample"}})
    run = refresh(env, "rdns")
    assert run["status"] == "success" and "on a sample of about 20,000 rows" in logs(run), logs(run)
    profile = {c["name"]: c for c in env.get("/api/referentials/rdns").json()["profile"]}
    assert 0 < profile["domain"]["count"] < 200000  # blocks of 2,048 rows drawn at random


# --------------------------------------------------------------------------- resumed downloads

class _Server(ThreadingHTTPServer):
    data = b""
    etag = '"v1"'
    cuts: list[int] = []  # bytes sent by the next responses before the connection is dropped
    requests: list[dict] = []


class _RangeHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        srv: _Server = self.server  # type: ignore[assignment]
        srv.requests.append({"range": self.headers.get("Range"), "if_range": self.headers.get("If-Range")})
        start = 0
        rng, if_range = self.headers.get("Range"), self.headers.get("If-Range")
        if rng and if_range in (None, srv.etag):
            start = int(rng.split("=")[1].rstrip("-"))
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(srv.data) - 1}/{len(srv.data)}")
        else:
            self.send_response(200)
        body = srv.data[start:]
        self.send_header("Content-Length", str(len(body)))
        self.send_header("ETag", srv.etag)
        self.send_header("Content-Type", "application/octet-stream")
        self.end_headers()
        if srv.cuts:
            self.wfile.write(body[:srv.cuts.pop(0)])
            self.wfile.flush()
            self.close_connection = True
            return
        self.wfile.write(body)


@pytest.fixture()
def range_server():
    srv = _Server(("127.0.0.1", 0), _RangeHandler)
    srv.data, srv.cuts, srv.requests, srv.etag = bytes(range(256)) * 4000, [], [], '"v1"'
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, f"http://127.0.0.1:{srv.server_address[1]}/rdns.bin"
    srv.shutdown()


@pytest.fixture()
def no_wait(monkeypatch):
    from app import ingest

    monkeypatch.setattr(ingest.time, "sleep", lambda s: None)


def test_interrupted_download_is_resumed(range_server, tmp_path, no_wait):
    from app.ingest import Run, download

    srv, url = range_server
    srv.cuts = [100_000, 300_000]
    run = Run("t", "manual")
    with httpx.Client() as client:
        entry = download(client, url, tmp_path, {}, None, None, run, fmt="whole")
    assert (tmp_path / "rdns.bin").read_bytes() == srv.data
    assert entry["sha256"] == hashlib.sha256(srv.data).hexdigest()
    assert [r["range"] for r in srv.requests] == [None, "bytes=100000-", "bytes=400000-"]
    assert all(r["if_range"] == '"v1"' for r in srv.requests[1:])
    assert sum("Download interrupted" in line["msg"] for line in run.logs) == 2
    assert not (tmp_path / ".partial").exists() or not any((tmp_path / ".partial").iterdir())


def test_download_resumed_by_the_next_run(range_server, tmp_path, no_wait, monkeypatch):
    from app import ingest
    from app.ingest import Run, download

    srv, url = range_server
    monkeypatch.setattr(ingest, "DOWNLOAD_ATTEMPTS", 1)
    srv.cuts = [250_000]
    with httpx.Client() as client, pytest.raises(httpx.TransportError):
        download(client, url, tmp_path, {}, None, None, Run("t", "manual"), fmt="whole")
    assert sum(p.stat().st_size for p in (tmp_path / ".partial").glob("*.part")) == 250_000
    run = Run("t", "manual")
    with httpx.Client() as client:
        entry = download(client, url, tmp_path, {}, None, None, run, fmt="whole")
    assert srv.requests[-1]["range"] == "bytes=250000-"
    assert (tmp_path / "rdns.bin").read_bytes() == srv.data
    assert entry["sha256"] == hashlib.sha256(srv.data).hexdigest()  # bytes of the previous run hashed too


def test_changed_source_is_downloaded_again(range_server, tmp_path, no_wait, monkeypatch):
    from app import ingest
    from app.ingest import Run, download

    srv, url = range_server
    monkeypatch.setattr(ingest, "DOWNLOAD_ATTEMPTS", 1)
    srv.cuts = [250_000]
    with httpx.Client() as client, pytest.raises(httpx.TransportError):
        download(client, url, tmp_path, {}, None, None, Run("t", "manual"), fmt="whole")
    # New version published meanwhile: If-Range no longer matches, the server sends the whole new file
    srv.data, srv.etag = bytes(reversed(range(256))) * 3000, '"v2"'
    run = Run("t", "manual")
    with httpx.Client() as client:
        entry = download(client, url, tmp_path, {}, None, None, run, fmt="whole")
    assert (tmp_path / "rdns.bin").read_bytes() == srv.data
    assert entry["sha256"] == hashlib.sha256(srv.data).hexdigest() and entry["etag"] == '"v2"'
    assert any("downloading it again" in line["msg"] for line in run.logs)
