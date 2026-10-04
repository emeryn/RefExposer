import functools
import gzip
import http.server
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

ADMIN_PASSWORD = "Adm1n-Passw0rd!"
USER_PASSWORD = "Us3r-Passw0rd!"
CSRF = {"X-Requested-With": "RefExposer"}

CSV = """code,label,population,created
FR,France,68000000,1958-10-04
DE,Allemagne,84000000,1949-05-23
IT,Italie,59000000,1946-06-02
ES,Espagne,48000000,1978-12-29
BE,"Belgique, royaume",11700000,1831-07-21
"""

PDNS = "rrname,rrtype,rdata,count\n" + "".join(
    f"host{i}.example{i % 7}.com,A,10.0.{i % 50}.{i % 200},{i}\n" for i in range(3000)
) + "mail.circl.lu,MX,mx.circl.lu,42\nwww.circl.lu,A,185.194.93.14,7\n"

CONFIG = """
referentials:
  - id: countries
    name: Pays
    category: Test
    source: {type: local, path: "raw/*.csv"}
    format: csv
    key: code
    validation: {max_drop_pct: 50}
  - id: words
    name: Mots
    source: {type: local, path: "raw/*.txt"}
    format: txt
    key: value
  - id: nested
    name: JSON imbriqué
    source: {type: local, path: "raw/*.json"}
    format: json
    options: {records_path: data.items}
    key: id
  - id: remote
    name: Distant
    source: {urls: ["http://127.0.0.1:%(port)d/remote.csv.gz"]}
    format: csv
    key: code
  - id: pdns
    name: Passive DNS
    source: {type: local, path: "raw/*.csv"}
    format: csv
    key: rrname
    search_columns: [rrname]
    storage:
      sort_by: [rrname]
      indexes: [rdata]
      keep_previous: false
      row_group_size: 10000
  - id: broken
    name: Invalide
    source: {type: http}
    format: csv
"""


@pytest.fixture()
def http_dir(tmp_path):
    root = tmp_path / "www"
    root.mkdir()
    class Handler(http.server.SimpleHTTPRequestHandler):
        """Also accepts absolute-form request targets, so the server can play an HTTP proxy."""

        def translate_path(self, path):
            if path.startswith(("http://", "https://")):
                path = urlsplit(path).path
            return super().translate_path(path)

    handler = functools.partial(Handler, directory=str(root))
    handler.log_message = lambda *a, **k: None  # type: ignore[assignment]
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield root, server.server_address[1]
    server.shutdown()


@pytest.fixture()
def env(tmp_path, monkeypatch, http_dir):
    www, port = http_dir
    data, config = tmp_path / "data", tmp_path / "config"
    config.mkdir()
    (config / "refs.yml").write_text(CONFIG % {"port": port}, encoding="utf-8")
    for ref, name, content in [
        ("countries", "a.csv", CSV),
        ("words", "w.txt", "# commentaire\nalpha\n\nbeta\ngamma\n"),
        ("pdns", "p.csv", PDNS),
        ("nested", "n.json", '{"data": {"items": [{"id": 1, "name": "un", "meta": {"lvl": 2}}, {"id": 2, "name": "deux", "meta": {"lvl": 3}}]}}'),
    ]:
        (data / ref / "raw").mkdir(parents=True)
        (data / ref / "raw" / name).write_text(content, encoding="utf-8")
    with gzip.open(www / "remote.csv.gz", "wt", encoding="utf-8") as f:
        f.write(CSV)

    monkeypatch.setenv("REFEX_DATA_DIR", str(data))
    monkeypatch.setenv("REFEX_CONFIG_DIR", str(config))
    monkeypatch.setenv("REFEX_REFRESH_ON_STARTUP", "none")
    monkeypatch.setenv("REFEX_SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("REFEX_DATABASE_URL", f"sqlite:///{(tmp_path / 'auth.db').as_posix()}")
    monkeypatch.setenv("REFEX_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("REFEX_ADMIN_PASSWORD", ADMIN_PASSWORD)
    monkeypatch.setenv("REFEX_ADMIN_MUST_CHANGE_PASSWORD", "false")
    monkeypatch.setenv("REFEX_LOGIN_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("REFEX_INTERNAL_PUBLISH_DELAY", "0.3")
    # Cheap Argon2id parameters to keep the test suite fast
    monkeypatch.setenv("REFEX_ARGON2_TIME_COST", "1")
    monkeypatch.setenv("REFEX_ARGON2_MEMORY_KIB", "1024")
    monkeypatch.setenv("REFEX_ARGON2_PARALLELISM", "1")
    from app import security
    from app.settings import get_settings

    get_settings.cache_clear()
    security.hasher.cache_clear()
    security._dummy_hash.cache_clear()
    from app.main import app

    with TestClient(app, headers=CSRF) as client:
        r = client.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD})
        assert r.status_code == 200, r.text
        client.data_dir = data  # type: ignore[attr-defined]
        client.www = www  # type: ignore[attr-defined]
        client.app_ = app  # type: ignore[attr-defined]
        yield client
    get_settings.cache_clear()
    security.hasher.cache_clear()
    security._dummy_hash.cache_clear()


def new_client(env, username: str | None = None, password: str | None = None) -> TestClient:
    """Second client sharing the running app, optionally logged in."""
    c = TestClient(env.app_, headers=CSRF)
    if username:
        r = c.post("/api/auth/login", json={"username": username, "password": password})
        assert r.status_code == 200, r.text
    return c


def create_user(env, username: str, role: str = "user", must_change: bool = False, group_ids=None) -> dict:
    r = env.post("/api/admin/users", json={
        "username": username, "password": USER_PASSWORD, "role": role,
        "must_change_password": must_change, "group_ids": group_ids or [],
    })
    assert r.status_code == 201, r.text
    return r.json()


def refresh(client, ref_id: str, force: bool = False, timeout: float = 30) -> dict:
    r = client.post(f"/api/referentials/{ref_id}/refresh", params={"force": force})
    assert r.status_code == 200, r.text
    deadline = time.time() + timeout
    while time.time() < deadline:
        detail = client.get(f"/api/referentials/{ref_id}").json()
        if not detail["current_run"]:
            return client.get(f"/api/referentials/{ref_id}/runs", params={"limit": 1}).json()[0]
        time.sleep(0.1)
    raise TimeoutError(ref_id)


def write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
