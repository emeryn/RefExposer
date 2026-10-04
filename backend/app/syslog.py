"""Syslog connector: RFC 5424 messages over UDP (RFC 5426), TCP (RFC 6587) or TLS (RFC 5425).

Records are formatted in the calling thread, then queued and sent by a background thread, so that a slow
or unreachable collector never slows the application down (the oldest messages are dropped when the
queue is full, and the connection is retried with a growing delay).

Audit events are sent with their fields as structured data:
    <85>1 2026-10-03T08:15:02.123456Z refex-host refexposer 7 auth.login [audit@32473 user="alice"
    action="auth.login" target="-" success="true" ip="10.0.0.5"] \ufeffauth.login {"provider": "ldap"}
"""

from __future__ import annotations

import collections
import logging
import os
import socket
import ssl
import tempfile
import threading
import time
from datetime import datetime, timezone
from typing import Any

import certifi
import orjson

from . import __version__, logsetup
from .appsettings import ProxySettings, SyslogSettings

log = logging.getLogger(__name__)
audit_log = logging.getLogger("refexposer.audit")

FACILITIES = {
    "kern": 0, "user": 1, "mail": 2, "daemon": 3, "auth": 4, "syslog": 5, "lpr": 6, "news": 7, "uucp": 8, "cron": 9,
    "authpriv": 10, "ftp": 11, "ntp": 12, "security": 13, "console": 14, "solaris-cron": 15,
    **{f"local{i}": 16 + i for i in range(8)},
}
BOM = b"\xef\xbb\xbf"
NIL = "-"
UDP_MAX = 8192  # bytes per datagram (RFC 5426: at least 2048 should be accepted by receivers)
QUEUE_SIZE = 10_000


def severity(levelno: int) -> int:
    """RFC 5424 severity of a Python logging level."""
    if levelno >= logging.CRITICAL:
        return 2
    if levelno >= logging.ERROR:
        return 3
    if levelno >= logging.WARNING:
        return 4
    if levelno >= logging.INFO:
        return 6
    return 7


def _header_field(value: str | None, max_len: int) -> str:
    """HOSTNAME, APP-NAME, PROCID, MSGID: printable US-ASCII without spaces (33-126), or NILVALUE."""
    if not value:
        return NIL
    cleaned = "".join(c for c in str(value) if 33 <= ord(c) <= 126)[:max_len]
    return cleaned or NIL


def _sd_name(value: str) -> str:
    """SD-ID and PARAM-NAME: printable US-ASCII except '=', ' ', ']', '"' (at most 32 characters)."""
    return "".join(c for c in value if 33 <= ord(c) <= 126 and c not in '= ]"')[:32]


def _sd_value(value: Any) -> str:
    """PARAM-VALUE: UTF-8, with '"', '\\' and ']' escaped."""
    s = "" if value is None else str(value)
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("]", "\\]")


def structured_data(elements: list[tuple[str, dict[str, Any]]]) -> str:
    out = []
    for sd_id, params in elements:
        body = "".join(f' {_sd_name(k)}="{_sd_value(v)}"' for k, v in params.items() if v is not None)
        out.append(f"[{_sd_name(sd_id)}{body}]")
    return "".join(out) or NIL


class Rfc5424Formatter(logging.Formatter):
    def __init__(self, cfg: SyslogSettings):
        super().__init__()
        self.cfg = cfg
        self.facility = FACILITIES[cfg.facility]
        self.hostname = _header_field(cfg.hostname or socket.gethostname(), 255)
        self.app_name = _header_field(cfg.app_name, 48)
        self.procid = _header_field(str(os.getpid()), 128)
        self.pen = cfg.enterprise_id

    def build(self, record: logging.LogRecord) -> bytes:
        audit = getattr(record, "audit", None)
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        if audit:
            sev = 5 if audit.get("success", True) else 4  # notice, or warning for a failure
            msgid = audit.get("action")
            elements = [(f"audit@{self.pen}", {
                "user": audit.get("user") or NIL,
                "action": audit.get("action"),
                "target": audit.get("target") or NIL,
                "success": "true" if audit.get("success", True) else "false",
                "ip": audit.get("ip") or NIL,
            })]
            text = audit.get("action") or ""
            if audit.get("detail"):
                text += " " + orjson.dumps(audit["detail"], default=str).decode()
        else:
            sev = severity(record.levelno)
            msgid = "-"
            elements = [(f"log@{self.pen}", {"logger": record.name, "level": record.levelname})]
            text = record.getMessage()
            if record.exc_info:
                text += "\n" + self.formatException(record.exc_info)
        elements.append(("origin", {"software": "RefExposer", "swVersion": __version__}))
        pri = self.facility * 8 + sev
        head = f"<{pri}>1 {ts} {self.hostname} {self.app_name} {self.procid} {_header_field(msgid, 32)} {structured_data(elements)}"
        if self.cfg.protocol != "udp" and self.cfg.framing == "non-transparent":
            text = text.replace("\r", "").replace("\n", "#012")  # the line feed ends the message in this framing
        return head.encode("utf-8") + b" " + BOM + text.encode("utf-8")


def frame(cfg: SyslogSettings, message: bytes) -> bytes:
    """Transport framing: one datagram (UDP), octet counting (TLS, TCP) or a trailing line feed (TCP)."""
    if cfg.protocol == "udp":
        return message[:UDP_MAX]
    if cfg.protocol == "tcp" and cfg.framing == "non-transparent":
        return message + b"\n"
    return str(len(message)).encode() + b" " + message


def tls_context(cfg: SyslogSettings, company: ProxySettings | None = None) -> ssl.SSLContext:
    """Trust: public CAs, the company CAs of the network settings (option) and the CA of the collector."""
    ctx = ssl.create_default_context(cafile=certifi.where())
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    if company is not None and cfg.use_company_ca and company.ca_bundle:
        ctx.load_verify_locations(cadata=company.ca_bundle)
    if cfg.ca_bundle:
        ctx.load_verify_locations(cadata=cfg.ca_bundle)
    if not cfg.verify_tls:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    if cfg.client_cert and cfg.client_key:
        # load_cert_chain only reads files: written to a private temporary file, removed at once
        fd, path = tempfile.mkstemp(suffix=".pem")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(cfg.client_cert.strip() + "\n" + cfg.client_key.strip() + "\n")
            ctx.load_cert_chain(path)
        finally:
            os.unlink(path)
    return ctx


class Connection:
    def __init__(self, cfg: SyslogSettings, company: ProxySettings | None):
        self.cfg, self.company = cfg, company
        self.sock: socket.socket | None = None
        self.peer: str | None = None

    def open(self) -> None:
        cfg = self.cfg
        if cfg.protocol == "udp":
            info = socket.getaddrinfo(cfg.host, cfg.port, type=socket.SOCK_DGRAM)[0]
            self.sock = socket.socket(info[0], socket.SOCK_DGRAM)
            self.sock.connect(info[4])
            self.peer = f"{info[4][0]}:{info[4][1]}"
            return
        raw = socket.create_connection((cfg.host, cfg.port), timeout=cfg.timeout)
        try:
            if cfg.protocol == "tls":
                raw = tls_context(cfg, self.company).wrap_socket(raw, server_hostname=cfg.host)
        except Exception:
            raw.close()
            raise
        raw.settimeout(cfg.timeout)
        self.sock = raw
        self.peer = "%s:%s" % raw.getpeername()[:2]

    def send(self, data: bytes) -> None:
        if self.sock is None:
            self.open()
        assert self.sock is not None
        if self.cfg.protocol == "udp":
            self.sock.send(data)
        else:
            self.sock.sendall(data)

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None


class SyslogHandler(logging.Handler):
    """Queues formatted messages; a background thread sends them, reconnecting when needed."""

    def __init__(self, cfg: SyslogSettings, company: ProxySettings | None = None):
        super().__init__(level=0)
        self.cfg = cfg
        self.formatter_5424 = Rfc5424Formatter(cfg)
        self.min_level = logsetup.level_value(cfg.level)
        self.queue: collections.deque[bytes] = collections.deque(maxlen=QUEUE_SIZE)
        self.dropped = 0
        self.sent = 0
        self.last_error: str | None = None
        self._event = threading.Event()
        self._stop = False
        self._conn = Connection(cfg, company)
        self._thread = threading.Thread(target=self._run, name="syslog", daemon=True)
        self._thread.start()

    def wants(self, record: logging.LogRecord) -> bool:
        if record.name.startswith(__name__):  # never send its own errors (loop)
            return False
        if getattr(record, "audit", None) is not None:
            return self.cfg.send_audit
        return self.cfg.send_logs and record.levelno >= self.min_level

    def emit(self, record: logging.LogRecord) -> None:
        if not self.wants(record):
            return
        try:
            data = frame(self.cfg, self.formatter_5424.build(record))
        except Exception:  # noqa: BLE001
            self.handleError(record)
            return
        if len(self.queue) == self.queue.maxlen:
            self.dropped += 1
        self.queue.append(data)
        self._event.set()

    def _run(self) -> None:
        delay = 1.0
        while not self._stop:
            self._event.wait(1.0)
            self._event.clear()
            while self.queue and not self._stop:
                data = self.queue[0]
                try:
                    self._conn.send(data)
                except Exception as e:  # noqa: BLE001
                    self._conn.close()
                    error = f"{type(e).__name__}: {e}"
                    if error != self.last_error:
                        log.warning("Syslog %s:%s unreachable (%s): retry in %.0fs", self.cfg.host, self.cfg.port, error, delay)
                    self.last_error = error
                    self._sleep(delay)
                    delay = min(delay * 2, 60.0)
                    break
                self.queue.popleft()
                self.sent += 1
                if self.last_error:
                    log.info("Syslog %s:%s reachable again", self.cfg.host, self.cfg.port)
                self.last_error, delay = None, 1.0

    def _sleep(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while not self._stop and time.monotonic() < end:
            time.sleep(0.1)

    def flush(self, timeout: float = 2.0) -> None:
        end = time.monotonic() + timeout
        self._event.set()
        while self.queue and time.monotonic() < end and not self.last_error:
            time.sleep(0.02)

    def close(self) -> None:
        self.flush(1.0)
        self._stop = True
        self._event.set()
        self._thread.join(timeout=2)
        self._conn.close()
        super().close()

    def status(self) -> dict[str, Any]:
        return {"queued": len(self.queue), "sent": self.sent, "dropped": self.dropped, "last_error": self.last_error}


# --------------------------------------------------------------------------- installation

_handler: SyslogHandler | None = None
_lock = threading.Lock()


def install(cfg: SyslogSettings, company: ProxySettings | None = None) -> None:
    """(Re)configure the connector from the settings; disabled settings remove it."""
    global _handler
    with _lock:
        old, _handler = _handler, None
        if old is not None:
            logsetup.set_extra_handler(None, None)
            old.close()
        if cfg.enabled and cfg.host:
            try:
                _handler = SyslogHandler(cfg, company)
            except Exception as e:  # noqa: BLE001  (e.g. unreadable client certificate)
                log.error("Syslog connector not started: %s", e)
                return
            logsetup.set_extra_handler(_handler, logsetup.level_value(cfg.level) if cfg.send_logs else None)
            log.info("Syslog connector: %s://%s:%s (RFC 5424, facility %s, level %s, audit %s)", cfg.protocol, cfg.host, cfg.port,
                     cfg.facility, cfg.level, "on" if cfg.send_audit else "off")


def status() -> dict[str, Any] | None:
    h = _handler
    return h.status() if h is not None else None


def shutdown() -> None:
    install(SyslogSettings())


def audit_event(action: str, user: str | None, target: str | None, success: bool, ip: str | None, detail: dict[str, Any] | None) -> None:
    """Audit trail entry, sent to syslog as structured data (and to the container output at the info level)."""
    audit_log.info("%s user=%s target=%s success=%s", action, user or "-", target or "-", success,
                   extra={"audit": {"action": action, "user": user, "target": target, "success": success, "ip": ip, "detail": detail}})


def test(cfg: SyslogSettings, company: ProxySettings | None = None) -> dict[str, Any]:
    """Connect with these settings and send one test message, step by step."""
    steps: list[dict[str, Any]] = []
    conn = Connection(cfg, company)
    try:
        try:
            addrs = sorted({a[4][0] for a in socket.getaddrinfo(cfg.host, cfg.port)})
            steps.append({"ok": True, "step": f"Name resolution of {cfg.host}", "detail": ", ".join(addrs)})
        except OSError as e:
            steps.append({"ok": False, "step": f"Name resolution of {cfg.host}", "detail": str(e)})
            return {"ok": False, "steps": steps}
        try:
            conn.open()
        except ssl.SSLError as e:
            steps.append({"ok": False, "step": "TLS handshake", "detail": f"{e} (check the CA certificate and the host name)"})
            return {"ok": False, "steps": steps}
        except OSError as e:
            steps.append({"ok": False, "step": f"Connection ({cfg.protocol.upper()} {cfg.port})", "detail": str(e)})
            return {"ok": False, "steps": steps}
        steps.append({"ok": True, "step": f"Connection ({cfg.protocol.upper()})", "detail": conn.peer})
        if cfg.protocol == "tls" and isinstance(conn.sock, ssl.SSLSocket):
            cert = conn.sock.getpeercert() or {}
            subject = ", ".join("=".join(x) for rdn in cert.get("subject", ()) for x in rdn)
            issuer = ", ".join("=".join(x) for rdn in cert.get("issuer", ()) for x in rdn)
            detail = f"{conn.sock.version()}, {conn.sock.cipher()[0]}"
            if subject:
                detail += f" · certificate: {subject} · issued by: {issuer}"
            elif not cfg.verify_tls:
                detail += " · certificate not verified"
            steps.append({"ok": True, "step": "TLS handshake", "detail": detail})
        record = logging.LogRecord(__name__, logging.INFO, __file__, 0, "RefExposer syslog connector test", None, None)
        record.name = "refexposer.test"
        message = Rfc5424Formatter(cfg).build(record)
        conn.send(frame(cfg, message))
        steps.append({"ok": True, "step": "Test message sent", "detail": message.decode("utf-8", "replace").replace("\ufeff", "")})
        if cfg.protocol == "udp":
            steps.append({"ok": True, "step": "UDP has no acknowledgement", "detail": "check that the message reached the collector"})
        return {"ok": True, "steps": steps}
    except Exception as e:  # noqa: BLE001
        steps.append({"ok": False, "step": "Sending", "detail": f"{type(e).__name__}: {e}"})
        return {"ok": False, "steps": steps}
    finally:
        conn.close()
