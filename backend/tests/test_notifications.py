import email
import hashlib
import hmac
import http.server
import json
import socket
import threading
import time

import pytest

from conftest import CSV, refresh


class SmtpServer:
    """Minimal SMTP server keeping the messages received (no TLS, no authentication)."""

    def __init__(self):
        self.messages: list[email.message.Message] = []
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen()
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn):
        f = conn.makefile("rwb")

        def send(text):
            f.write(text.encode() + b"\r\n")
            f.flush()

        send("220 test ESMTP")
        data, buf = False, []
        for line in f:
            if data:
                if line == b".\r\n":
                    self.messages.append(email.message_from_bytes(b"".join(buf)))
                    data, buf = False, []
                    send("250 OK")
                else:
                    buf.append(line[1:] if line.startswith(b"..") else line)
                continue
            cmd = line.strip().upper()
            if cmd.startswith(b"EHLO"):
                send("250-test")
                send("250 8BITMIME")
            elif cmd.startswith(b"DATA"):
                send("354 go")
                data = True
            elif cmd.startswith(b"QUIT"):
                send("221 bye")
                break
            else:
                send("250 OK")
        conn.close()

    def close(self):
        self.sock.close()


@pytest.fixture()
def smtp():
    srv = SmtpServer()
    yield srv
    srv.close()


@pytest.fixture()
def hook():
    received: list[dict] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            received.append({"path": self.path, "headers": dict(self.headers), "raw": body, "json": json.loads(body)})
            self.send_response(204 if self.path != "/fail" else 500)
            self.end_headers()

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", received
    srv.shutdown()


def wait_for(predicate, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise TimeoutError


def channel(env, **body):
    r = env.post("/api/admin/notifications", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_failure_then_recovery(env, http_dir, smtp, hook):
    base, received = hook
    assert env.put("/api/admin/settings/smtp", json={"host": "127.0.0.1", "port": smtp.port, "security": "none",
                                                     "from_address": "RefExposer <alerts@example.com>"}).status_code == 200
    assert env.post("/api/admin/secrets", json={"name": "hook-token", "value": "tok-123", "hosts": ["127.0.0.1"]}).status_code == 201
    assert env.post("/api/admin/secrets", json={"name": "hook-sign", "value": "sign-key"}).status_code == 201
    channel(env, name="SOC", type="email", recipients=["soc@example.com"], notify_owner=True)
    channel(env, name="Webhook", type="webhook", url=base + "/hook", events=["failure", "recovered", "published"],
            headers={"Authorization": "Bearer ${secret:hook-token}"}, signing_secret="${secret:hook-sign}")
    channel(env, name="Teams", type="webhook", url=base + "/teams", template='{"title": "{{referential.name}}", "text": "{{text}}"}')
    channel(env, name="Other referentials", type="webhook", url=base + "/other", referentials=["countries"])

    url = f"http://127.0.0.1:{http_dir[1]}/feed.csv"
    r = env.post("/api/admin/referentials", json={"config": {"id": "feed", "name": "Feed", "category": "Intel", "owner": "owner@example.com",
                                                             "format": "csv", "source": {"type": "http", "urls": [url]}}, "pull": False})
    assert r.status_code == 201, r.text
    assert refresh(env, "feed")["status"] == "error"  # 404

    wait_for(lambda: len(smtp.messages) == 1 and len(received) == 2)
    msg = smtp.messages[0]
    assert msg["Subject"] == "[RefExposer] Feed (feed): failed"
    assert "soc@example.com" in msg["To"] and "owner@example.com" in msg["To"]
    assert "404" in msg.get_payload() and "The published version is kept" in msg.get_payload()
    hooks = {h["path"]: h for h in received}
    generic = hooks["/hook"]
    assert generic["json"]["event"] == "failure" and generic["json"]["referential"]["id"] == "feed"
    assert generic["json"]["run"]["status"] == "error" and generic["headers"]["Authorization"] == "Bearer tok-123"
    expected = "sha256=" + hmac.new(b"sign-key", generic["raw"], hashlib.sha256).hexdigest()
    assert generic["headers"]["X-RefExposer-Signature"] == expected
    assert hooks["/teams"]["json"] == {"title": "Feed", "text": hooks["/teams"]["json"]["text"]}
    assert hooks["/teams"]["json"]["text"].startswith("❌ RefExposer: Feed (feed) failed: ")

    # Failing again: nothing new (notified when it starts failing)
    assert refresh(env, "feed")["status"] == "error"
    time.sleep(0.5)
    assert len(smtp.messages) == 1 and len(received) == 2

    # Fixed: recovered (and published for the channel listening to it, once)
    (env.www / "feed.csv").write_text(CSV, encoding="utf-8")
    assert refresh(env, "feed")["status"] == "success"
    wait_for(lambda: len(smtp.messages) == 2 and len(received) == 4)
    assert smtp.messages[1]["Subject"] == "[RefExposer] Feed (feed): recovered"
    assert [h["json"]["event"] for h in received[2:] if h["path"] == "/hook"] == ["recovered"]

    deliveries = env.get("/api/admin/notifications/deliveries").json()
    assert len(deliveries) == 6 and all(d["ok"] for d in deliveries)
    assert {d["channel"] for d in deliveries} == {"SOC", "Webhook", "Teams"}


def test_channels_api(env, hook, smtp):
    base, received = hook
    ch = channel(env, name="Hook", type="webhook", url=base + "/hook", headers={"X-Key": "literal-key"})
    listed = env.get("/api/admin/notifications").json()
    assert listed["channels"][0]["headers"] == {"X-Key": "********"} and listed["smtp_configured"] is False
    assert "literal-key" not in json.dumps(listed)
    # Sent back masked: the stored value is kept
    r = env.put(f"/api/admin/notifications/{ch['id']}", json={**ch, "name": "Hook 2", "headers": {"X-Key": "********"}})
    assert r.status_code == 200, r.text
    res = env.post(f"/api/admin/notifications/{ch['id']}/test").json()
    assert res["ok"] is True, res
    assert received[-1]["headers"]["X-Key"] == "literal-key" and received[-1]["json"]["event"] == "test"

    # Validation
    for body, error in [
        ({"name": "x", "type": "email"}, "needs recipients"),
        ({"name": "x", "type": "email", "recipients": ["nope"]}, "invalid e-mail address"),
        ({"name": "x", "type": "webhook"}, "needs a URL"),
        ({"name": "x", "type": "webhook", "url": base, "signing_secret": "plain"}, "${secret:"),
        ({"name": "x", "type": "webhook", "url": base, "headers": {"A": "${secret:missing}"}}, "unknown secret"),
        ({"name": "x", "type": "webhook", "url": base, "template": "{\"a\": {{text}}"}, "valid JSON"),
        ({"name": "x", "type": "webhook", "url": base, "referentials": ["nope"]}, "unknown referential"),
    ]:
        r = env.post("/api/admin/notifications", json=body)
        assert r.status_code == 422 and error in r.text, (body, r.text)

    # A failing endpoint: recorded as not delivered
    bad = channel(env, name="Bad", type="webhook", url=base + "/fail")
    res = env.post(f"/api/admin/notifications/{bad['id']}/test").json()
    assert res["ok"] is False and "HTTP 500" in res["detail"]

    # SMTP test
    r = env.post("/api/admin/settings/smtp/test", json={"to": "me@example.com", "config": {"host": "127.0.0.1", "port": smtp.port,
                                                                                          "security": "none", "from_address": "a@example.com"}})
    assert r.json()["ok"] is True, r.text
    wait_for(lambda: len(smtp.messages) == 1)
    assert env.delete(f"/api/admin/notifications/{bad['id']}").status_code == 200
    assert len(env.get("/api/admin/notifications").json()["channels"]) == 1
