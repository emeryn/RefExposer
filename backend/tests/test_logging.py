"""Container log level and the RFC 5424 syslog connector (UDP, TCP, TLS with a private CA)."""

import datetime as dt
import logging
import re
import socket
import ssl
import threading
import time

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from app import logsetup, syslog
from app.appsettings import SyslogSettings

# RFC 5424: <PRI>VERSION SP TIMESTAMP SP HOSTNAME SP APP-NAME SP PROCID SP MSGID SP STRUCTURED-DATA [SP BOM MSG]
RFC5424 = re.compile(
    r'^<(?P<pri>\d{1,3})>1 (?P<ts>\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z) (?P<host>[!-~]{1,255}) (?P<app>[!-~]{1,48}) '
    r'(?P<procid>[!-~]{1,128}) (?P<msgid>[!-~]{1,32}) (?P<sd>-|(\[[^\]\\]*(?:\\.[^\]\\]*)*\])+)'
    r'(?: \ufeff(?P<msg>.*))?$',
    re.S,
)


def record(msg="hello", level=logging.WARNING, name="app.test", **extra):
    r = logging.LogRecord(name, level, __file__, 1, msg, None, None)
    r.__dict__.update(extra)
    return r


def parse(data: bytes) -> re.Match:
    m = RFC5424.match(data.decode("utf-8"))
    assert m, data
    return m


# --------------------------------------------------------------------------- format

def test_rfc5424_format_and_framing():
    cfg = SyslogSettings(enabled=True, host="collector", facility="local3", hostname="refex host", app_name="refexposer")
    f = syslog.Rfc5424Formatter(cfg)
    m = parse(f.build(record("disk almost full")))
    assert int(m["pri"]) == 19 * 8 + 4  # local3.warning
    assert m["host"] == "refexhost" and m["app"] == "refexposer" and m["msgid"] == "-"
    assert '[log@32473 logger="app.test" level="WARNING"][origin software="RefExposer"' in m["sd"]
    assert m["msg"] == "disk almost full"

    audit = {"action": "auth.login", "user": 'a"b]c\\', "target": None, "success": False, "ip": "10.0.0.5", "detail": {"reason": "bad password"}}
    m = parse(f.build(record("x", logging.INFO, "refexposer.audit", audit=audit)))
    assert int(m["pri"]) == 19 * 8 + 4 and m["msgid"] == "auth.login"  # failure: warning
    assert 'user="a\\"b\\]c\\\\"' in m["sd"] and 'target="-"' in m["sd"] and 'success="false"' in m["sd"]
    assert m["msg"] == 'auth.login {"reason":"bad password"}'
    ok = parse(f.build(record("x", logging.INFO, "refexposer.audit", audit={**audit, "success": True})))
    assert int(ok["pri"]) == 19 * 8 + 5  # notice

    msg = f.build(record("line1\nline2"))
    assert syslog.frame(cfg, msg) == msg  # UDP: one datagram
    tcp = cfg.model_copy(update={"protocol": "tcp"})
    assert syslog.frame(tcp, msg) == f"{len(msg)} ".encode() + msg  # octet counting
    legacy = tcp.model_copy(update={"framing": "non-transparent"})
    framed = syslog.frame(legacy, syslog.Rfc5424Formatter(legacy).build(record("line1\nline2")))
    assert framed.endswith(b"line1#012line2\n") and framed.count(b"\n") == 1


# --------------------------------------------------------------------------- transports

class Collector:
    """Minimal syslog collector: UDP datagrams, or TCP / TLS streams with octet counting."""

    def __init__(self, protocol="udp", context: ssl.SSLContext | None = None):
        self.messages: list[bytes] = []
        self.protocol = protocol
        if protocol == "udp":
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        else:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.context = context
        if protocol != "udp":
            self.sock.listen(5)
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        if self.protocol == "udp":
            while True:
                data, _ = self.sock.recvfrom(65535)
                self.messages.append(data)
        while True:
            conn, _ = self.sock.accept()
            threading.Thread(target=self._read, args=(conn,), daemon=True).start()

    def _read(self, conn):
        try:
            if self.context:
                conn = self.context.wrap_socket(conn, server_side=True)
            buf = b""
            while True:
                chunk = conn.recv(65535)
                if not chunk:
                    return
                buf += chunk
                while b" " in buf:
                    size, rest = buf.split(b" ", 1)
                    if len(rest) < int(size):
                        break
                    self.messages.append(rest[: int(size)])
                    buf = rest[int(size):]
        except (OSError, ssl.SSLError):
            return

    def wait(self, count=1, timeout=5.0):
        end = time.time() + timeout
        while len(self.messages) < count and time.time() < end:
            time.sleep(0.02)
        return self.messages


@pytest.fixture()
def cleanup():
    yield
    syslog.shutdown()


@pytest.mark.parametrize("protocol", ["udp", "tcp"])
def test_handler_sends_logs_and_audit(protocol, cleanup):
    collector = Collector(protocol)
    syslog.install(SyslogSettings(enabled=True, host="127.0.0.1", port=collector.port, protocol=protocol, level="warning"))
    logging.getLogger("app.ingest").info("below the level: not sent")
    logging.getLogger("app.ingest").error("download failed")
    syslog.audit_event("user.create", "admin", "user:bob", True, "127.0.0.1", {"role": "user"})
    msgs = [parse(m) for m in collector.wait(2)]
    assert [m["msg"] for m in msgs] == ["download failed", 'user.create {"role":"user"}']
    assert msgs[1]["msgid"] == "user.create" and 'action="user.create"' in msgs[1]["sd"]
    assert syslog.status()["sent"] == 2


def _pki(tmp_path):
    """Private root CA and a server certificate for 127.0.0.1 / localhost."""
    now = dt.datetime.now(dt.timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Root CA")])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=30))
          .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
          .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
          .sign(ca_key, hashes.SHA256()))
    key = ec.generate_private_key(ec.SECP256R1())
    import ipaddress

    cert = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])).issuer_name(ca_name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=30))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
            .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))
    pem = lambda c: c.public_bytes(serialization.Encoding.PEM).decode()  # noqa: E731
    server = tmp_path / "server.pem"
    server.write_text(pem(cert) + key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                    serialization.NoEncryption()).decode())
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(server)
    return pem(ca), ctx


def test_tls_with_private_root_ca(tmp_path, cleanup):
    ca_pem, server_ctx = _pki(tmp_path)
    collector = Collector("tls", server_ctx)
    base = SyslogSettings(enabled=True, host="localhost", port=collector.port, protocol="tls")

    # Unknown CA: refused at the handshake
    res = syslog.test(base.model_copy(update={"use_company_ca": False}))
    assert not res["ok"] and res["steps"][-1]["step"] == "TLS handshake"

    # Root CA given: verified handshake, then a test message
    cfg = base.model_copy(update={"ca_bundle": ca_pem})
    res = syslog.test(cfg)
    assert res["ok"], res
    assert "Test Root CA" in next(s["detail"] for s in res["steps"] if s["step"] == "TLS handshake")
    assert parse(collector.wait(1)[0])["msg"] == "RefExposer syslog connector test"

    # The root CA may also come from the company CAs of the network settings
    from app.appsettings import ProxySettings

    assert syslog.test(base, ProxySettings(ca_bundle=ca_pem))["ok"]

    syslog.install(cfg)
    logging.getLogger("app.service").warning("over TLS")
    assert parse(collector.wait(3)[-1])["msg"] == "over TLS"


# --------------------------------------------------------------------------- API and levels

def test_settings_api_and_audit_forwarding(env, cleanup):
    collector = Collector("udp")
    bad = env.put("/api/admin/settings/syslog", json={"enabled": True, "host": "127.0.0.1", "facility": "nope"})
    assert bad.status_code == 422
    r = env.put("/api/admin/settings/syslog", json={"enabled": True, "host": "127.0.0.1", "port": collector.port, "facility": "auth",
                                                     "send_logs": False, "client_key": ""})
    assert r.status_code == 200, r.text
    assert env.post("/api/admin/settings/syslog/test", json={"config": {}}).json()["ok"] is True
    env.post("/api/auth/logout")
    env.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    msgs = [parse(m) for m in collector.wait(4)]
    login = [m for m in msgs if m["msgid"] == "auth.login"]
    assert login and int(login[-1]["pri"]) == 4 * 8 + 4 and 'success="false"' in login[-1]["sd"]
    assert all("log@" not in m["sd"] or "refexposer.test" in m["sd"] for m in msgs)  # application logs not sent


def test_log_levels():
    logsetup.setup("warning")
    root = logging.getLogger()
    own = [h for h in root.handlers if isinstance(h, logsetup._OwnHandler)]
    assert len(own) == 1 and own[0].level == logging.WARNING and root.level == logging.WARNING
    assert logging.getLogger("httpx").level == logging.WARNING
    logsetup.setup("trace")
    assert root.level == logsetup.TRACE and logging.getLogger("httpx").level == logging.DEBUG
    assert logging.getLevelName(logsetup.TRACE) == "TRACE"
    logsetup.setup("info")
