"""Git repository sources, against a real smart-HTTP Git server (git http-backend) with a private repository."""

import base64
import http.server
import json
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest

from conftest import refresh

TOKEN = "glpat-SecretToken123"
CSV = "code,label\nFR,France\nDE,Allemagne\n"


def git(*args, cwd=None):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    return subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout.strip()


class GitServer:
    """Smart HTTP Git server: <root>/public.git, and <root>/private.git that needs the token (any user name)."""

    def __init__(self, root: Path):
        self.root = root
        self.requests: list[str] = []
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _serve(self):
                path, _, query = self.path.partition("?")
                auth = self.headers.get("Authorization", "")
                server.requests.append(auth)
                if path.startswith("/private.git"):
                    ok = auth.startswith("Basic ") and base64.b64decode(auth[6:]).decode().partition(":")[2] == TOKEN
                    if not ok:
                        self.send_response(401)
                        self.send_header("WWW-Authenticate", 'Basic realm="git"')
                        self.end_headers()
                        return
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0)) if self.command == "POST" else b""
                env = {**os.environ, "GIT_PROJECT_ROOT": str(server.root), "GIT_HTTP_EXPORT_ALL": "1", "PATH_INFO": path,
                       "QUERY_STRING": query, "REQUEST_METHOD": self.command, "CONTENT_TYPE": self.headers.get("Content-Type", ""),
                       "CONTENT_LENGTH": str(len(body)), "REMOTE_ADDR": "127.0.0.1", "GIT_PROTOCOL": self.headers.get("Git-Protocol", "")}
                out = subprocess.run(["git", "http-backend"], input=body, env=env, capture_output=True).stdout
                head, _, payload = out.partition(b"\r\n\r\n")
                status = 200
                headers = []
                for line in head.decode().split("\r\n"):
                    k, _, v = line.partition(": ")
                    if k.lower() == "status":
                        status = int(v.split()[0])
                    elif k:
                        headers.append((k, v))
                self.send_response(status)
                for k, v in headers:
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_GET = do_POST = _serve

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def url(self, name):
        return f"http://127.0.0.1:{self.port}/{name}.git"


@pytest.fixture()
def repos(tmp_path, env, monkeypatch):
    work = tmp_path / "work"
    (work / "data").mkdir(parents=True)
    git("init", "-q", "-b", "main", str(work))
    (work / "data" / "countries.csv").write_text(CSV, encoding="utf-8")
    (work / "data" / "notes.txt").write_text("not data", encoding="utf-8")
    (work / "README.md").write_text("# test", encoding="utf-8")
    (work / "big.bin").write_bytes(os.urandom(200_000))  # outside the path: never downloaded (sparse, partial fetch)
    git("add", ".", cwd=work)
    git("commit", "-q", "-m", "first", cwd=work)
    git("tag", "v1", cwd=work)
    root = tmp_path / "srv"
    root.mkdir()
    for name in ("public", "private"):
        git("clone", "-q", "--bare", str(work), str(root / f"{name}.git"))
        git("config", "uploadpack.allowFilter", "true", cwd=root / f"{name}.git")
        git("config", "uploadpack.allowAnySHA1InWant", "true", cwd=root / f"{name}.git")
        git("config", "http.receivepack", "true", cwd=root / f"{name}.git")
    server = GitServer(root)
    # The test server speaks plain http; production allows https only
    env.app_.state.service.settings.git_protocols = "https,http"
    yield server, work
    server.httpd.shutdown()


def create(env, ref_id, source, **extra):
    cfg = {"id": ref_id, "name": ref_id, "format": "csv", "key": "code", "source": {"type": "git", **source}, **extra}
    r = env.post("/api/admin/referentials", json={"config": cfg, "pull": False})
    assert r.status_code == 201, r.text
    return refresh(env, ref_id)


def push(server, work, name, csv):
    (work / "data" / "countries.csv").write_text(csv, encoding="utf-8")
    git("commit", "-q", "-am", "update", cwd=work)
    git("push", "-q", server.url(name).replace("http://", f"http://x:{TOKEN}@"), "main", cwd=work)


def test_public_repository(env, repos):
    server, work = repos
    run = create(env, "gitpub", {"repository": server.url("public"), "path": "data/*.csv"})
    assert run["status"] == "success", run
    assert env.get("/api/referentials/gitpub/lookup/DE").json()["label"] == "Allemagne"
    d = env.get("/api/referentials/gitpub").json()
    assert d["sources"][0]["commit"] == git("rev-parse", "HEAD", cwd=work)
    assert not (env.data_dir / "gitpub" / "raw" / "git" / "big.bin").exists()

    # Same commit: nothing fetched (ls-remote); new commit: new version
    assert refresh(env, "gitpub")["status"] == "unchanged"
    push(server, work, "public", CSV + "IT,Italie\n")
    run = refresh(env, "gitpub")
    assert run["status"] == "success" and run["rows"] == 3

    # Tag and commit id
    sha = git("rev-parse", "v1", cwd=work)
    assert create(env, "gittag", {"repository": server.url("public"), "path": "data/countries.csv", "ref": "v1"})["rows"] == 2
    assert create(env, "gitsha", {"repository": server.url("public"), "path": "data/*.csv", "ref": sha})["rows"] == 2

    # Nothing matches the path
    run = create(env, "gitnone", {"repository": server.url("public"), "path": "nope/*.csv"})
    assert run["status"] == "corrupted" and "matches" in run["message"]


def test_private_repository_with_token(env, repos, monkeypatch):
    server, work = repos
    run = create(env, "gitpriv", {"repository": server.url("private"), "path": "data/*.csv"})
    assert run["status"] in ("error", "corrupted") and "access token" in run["message"]

    monkeypatch.setenv("REFEX_SOURCE_GIT_TOKEN", TOKEN)
    cfg = env.get("/api/admin/referentials/gitpriv").json()["config"]
    cfg["source"]["token"] = "${REFEX_SOURCE_GIT_TOKEN}"
    assert env.put("/api/admin/referentials/gitpriv", json={"config": cfg, "pull": False}).status_code == 200
    run = refresh(env, "gitpriv")
    assert run["status"] == "success", run
    assert any(a.startswith("Basic ") for a in server.requests)
    # Users never see the token, nor the logs
    d = env.get("/api/referentials/gitpriv").json()
    assert d["config"]["source"]["token"] == "***"
    everything = json.dumps(env.get("/api/referentials/gitpriv/runs").json()) + json.dumps(d)
    assert TOKEN not in everything

    # A wrong token: refused, and its value is never shown in the error
    monkeypatch.setenv("REFEX_SOURCE_GIT_TOKEN", "wrong-token-value")
    run = refresh(env, "gitpriv", force=True)
    assert run["status"] in ("error", "corrupted") and "wrong-token-value" not in json.dumps(run)


def test_preview_and_safety(env, repos):
    server, work = repos
    p = env.post("/api/admin/referentials/preview", json={"source": {"type": "git", "repository": server.url("public"), "path": "data/*.csv"}}).json()
    assert p["error"] is None and p["detected_format"] == "csv" and p["total_rows"] == 2

    # Credentials in the URL, other transports, http when not allowed
    base = {"id": "gitbad", "name": "x", "format": "csv"}
    bad_url = server.url("public").replace("http://", "http://u:p@")
    assert env.post("/api/admin/referentials", json={"config": {**base, "source": {"type": "git", "repository": bad_url, "path": "a"}}}).status_code == 422
    assert env.post("/api/admin/referentials", json={"config": {**base, "source": {"type": "git", "repository": "file:///etc", "path": "a"}}}).status_code == 422
    env.app_.state.service.settings.git_protocols = "https"
    run = create(env, "gitplain", {"repository": server.url("public"), "path": "data/*.csv"})
    assert run["status"] in ("error", "corrupted") and "not allowed" in run["message"]
    env.app_.state.service.settings.git_protocols = "https,http"

    # Git LFS pointers are refused with an explanation
    (work / "data" / "countries.csv").write_text("version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 12\n", encoding="utf-8")
    git("commit", "-q", "-am", "lfs", cwd=work)
    git("push", "-q", server.url("public").replace("http://", f"http://x:{TOKEN}@"), "main", cwd=work)
    run = create(env, "gitlfs", {"repository": server.url("public"), "path": "data/*.csv"})
    assert run["status"] == "corrupted" and "Git LFS" in run["message"]
