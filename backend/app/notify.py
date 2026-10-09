"""Notifications of the referential events: e-mails (SMTP) and generic webhooks.

Events, decided from the state before and after an update:
- failure    the update ended in error, with a corrupted source or a rejected version (the published version is kept).
             Sent when the referential starts failing; again at every failed update only with `repeat_failures`;
- recovered  first successful update after a failure;
- published  a new version was published.

Channels (Administration › Notifications) choose their events and referentials (ids and / or categories). E-mails go
through the SMTP server of the settings; webhooks POST a JSON document (generic payload, or a template with
{{placeholders}} for Teams, Discord, PagerDuty...), signed with HMAC-SHA256 when a signing secret is set. Header values
and the signing secret may be ${secret:...} references of the secret manager (restricted to their hosts).

Deliveries run in the background (an update never waits for them), with 3 attempts, and are kept in
data/.notifications.jsonl (500 last).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import smtplib
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path
from typing import Any

from . import appsettings
from .appsettings import EMAIL_RE, NotificationChannel, SmtpSettings
from .config import expand_env
from .storage import iso, utcnow

log = logging.getLogger(__name__)

FAILED = {"error", "corrupted", "rejected"}
ATTEMPTS = 3
KEEP_DELIVERIES = 500
_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z0-9_.]+)\s*\}\}")
ICONS = {"failure": "❌", "recovered": "✅", "published": "🆕", "test": "🔔"}
LABELS = {"failure": "failed", "recovered": "recovered", "published": "new version published", "test": "test notification"}


def event_of(previous: str | None, status: str, run_status: str) -> tuple[list[str], bool]:
    """(candidate events, failing already) of an update ending with the referential `status` (ok, error...)."""
    if status in FAILED:
        return ["failure"], previous in FAILED
    events = []
    if status == "ok" and previous in FAILED:
        events.append("recovered")
    if run_status == "success":
        events.append("published")
    return events, False


def concerns(ch: NotificationChannel, ref_id: str, category: str | None) -> bool:
    if ch.referentials and ref_id not in ch.referentials:
        return False
    return not ch.categories or category in ch.categories


def payload(event: str, ref: dict[str, Any], run: dict[str, Any], previous: str | None, base_url: str | None) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/r/{ref['id']}" if base_url else None
    message = run.get("message") or ""
    text = f"{ICONS.get(event, '')} RefExposer: {ref['name']} ({ref['id']}) {LABELS.get(event, event)}"
    if event == "failure" and message:
        text += f": {message}"
    elif event == "published" and run.get("rows") is not None:
        text += f" ({run['rows']:,} rows)"
    if url:
        text += f" {url}"
    return {
        "event": event,
        "text": text,
        "referential": {**ref, "url": url},
        "run": run,
        "previous_status": previous,
        "instance": base_url,
        "sent_at": iso(utcnow()),
    }


def _lookup(data: Any, path: str) -> Any:
    for part in path.split("."):
        data = data.get(part) if isinstance(data, dict) else None
    return data


def render(template: str, data: dict[str, Any]) -> str:
    """JSON template: each {{path}} is replaced by the value escaped for a JSON string (place it between quotes)."""
    def repl(m: re.Match) -> str:
        value = _lookup(data, m.group(1))
        text = "" if value is None else value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
        return json.dumps(text, ensure_ascii=False)[1:-1]

    body = _PLACEHOLDER.sub(repl, template)
    json.loads(body)  # a template must give valid JSON
    return body


def check_template(template: str) -> None:
    sample = payload("failure", {"id": "x", "name": "X", "category": "C", "owner": None}, {"status": "error", "message": 'a "b"'}, "ok", None)
    try:
        render(template, sample)
    except ValueError as e:
        raise ValueError(f"the template does not give valid JSON: {e}") from e


# --------------------------------------------------------------------------- senders

def send_mail(smtp: SmtpSettings, to: list[str], subject: str, body: str, ca_bundle: str = "") -> None:
    if not smtp.configured:
        raise RuntimeError("no SMTP server configured (Settings › E-mail)")
    msg = EmailMessage()
    msg["From"] = smtp.from_address
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=smtp.from_address.rsplit("@", 1)[-1].rstrip(">"))
    msg["Auto-Submitted"] = "auto-generated"
    msg.set_content(body)
    ctx = ssl.create_default_context()
    if ca_bundle:
        ctx.load_verify_locations(cadata=ca_bundle)
    if not smtp.verify_tls:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    if smtp.security == "tls":
        server: smtplib.SMTP = smtplib.SMTP_SSL(smtp.host, smtp.port, timeout=smtp.timeout, context=ctx)
    else:
        server = smtplib.SMTP(smtp.host, smtp.port, timeout=smtp.timeout)
    try:
        server.ehlo()
        if smtp.security == "starttls":
            server.starttls(context=ctx)
            server.ehlo()
        if smtp.username:
            server.login(smtp.username, smtp.password)
        server.send_message(msg)
    finally:
        try:
            server.quit()
        except smtplib.SMTPException:
            server.close()


def mail_text(data: dict[str, Any], logs: list[dict[str, Any]]) -> tuple[str, str]:
    ref, run, event = data["referential"], data["run"], data["event"]
    subject = f"[RefExposer] {ref['name']} ({ref['id']}): {LABELS.get(event, event)}"
    lines = [
        f"Referential: {ref['name']} ({ref['id']})",
        f"Category: {ref.get('category') or '-'}",
        f"Event: {LABELS.get(event, event)}",
        f"Status of the update: {run.get('status')}",
    ]
    if run.get("message"):
        lines.append(f"Message: {run['message']}")
    if data.get("previous_status"):
        lines.append(f"Previous state: {data['previous_status']}")
    for k, label in (("trigger", "Trigger"), ("started_at", "Started"), ("finished_at", "Finished"), ("rows", "Rows")):
        if run.get(k) is not None:
            lines.append(f"{label}: {run[k]}")
    if event == "failure":
        lines.append("The published version is kept: users still query the previous data.")
    if ref.get("url"):
        lines += ["", f"Open: {ref['url']}"]
    if logs:
        lines += ["", "Last log lines:"] + [f"  {entry.get('t', '')[:19]} {entry.get('level', '')}: {entry.get('msg', '')}" for entry in logs[-12:]]
    lines += ["", "-- ", "Sent by RefExposer (Administration › Notifications)."]
    return subject, "\n".join(lines)


def post_webhook(ch: NotificationChannel, data: dict[str, Any], client: Any) -> int:
    body = render(ch.template, data) if ch.template else json.dumps(data, ensure_ascii=False, default=str)
    raw = body.encode()
    headers = {"Content-Type": "application/json", "User-Agent": "RefExposer-Notifications"}
    headers.update({k: expand_env(v, ch.url) for k, v in ch.headers.items()})  # ${secret:...}: restricted to their hosts
    if ch.signing_secret:
        key = expand_env(ch.signing_secret, ch.url).encode()
        headers["X-RefExposer-Signature"] = "sha256=" + hmac.new(key, raw, hashlib.sha256).hexdigest()
    headers["X-RefExposer-Event"] = data["event"]
    r = client.post(ch.url, content=raw, headers=headers)
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
    return r.status_code


# --------------------------------------------------------------------------- dispatcher

class Notifier:
    def __init__(self, settings: Any):
        self.settings = settings
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="notify")
        self._lock = threading.Lock()
        self.path: Path = settings.data_dir / ".notifications.jsonl"

    def _load(self) -> tuple[list[NotificationChannel], SmtpSettings, str]:
        from .db import session_factory

        with session_factory()() as db:
            channels = appsettings.load(db, "notifications").channels  # type: ignore[attr-defined]
            smtp = appsettings.load(db, "smtp")
            ca = appsettings.load(db, "proxy").ca_bundle  # type: ignore[attr-defined]
        return channels, smtp, ca  # type: ignore[return-value]

    def run_finished(self, ref: Any, run: dict[str, Any], previous: str | None, status: str, logs: list[dict[str, Any]]) -> None:
        """Called at the end of every update: sends the matching notifications in the background."""
        events, failing = event_of(previous, status, run.get("status", ""))
        if not events:
            return
        try:
            channels, smtp, ca = self._load()
        except Exception:  # noqa: BLE001
            log.exception("Cannot load the notification channels")
            return
        info = {"id": ref.id, "name": ref.name, "category": ref.category, "owner": ref.owner}
        for ch in channels:
            if not ch.enabled or not concerns(ch, ref.id, ref.category):
                continue
            event = next((e for e in events if e in ch.events), None)
            if event is None or (event == "failure" and failing and not ch.repeat_failures):
                continue
            data = payload(event, info, run, previous, self.settings.public_url)
            self.executor.submit(self._deliver, ch, data, logs, smtp, ca)

    def send_now(self, ch: NotificationChannel, data: dict[str, Any], logs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Synchronous delivery (test of a channel): one attempt, result returned."""
        _, smtp, ca = self._load()
        return self._deliver(ch, data, logs or [], smtp, ca, attempts=1)

    def _deliver(self, ch: NotificationChannel, data: dict[str, Any], logs: list[dict[str, Any]], smtp: SmtpSettings, ca: str,
                 attempts: int = ATTEMPTS) -> dict[str, Any]:
        from .network import client

        entry: dict[str, Any] = {"at": iso(utcnow()), "channel_id": ch.id, "channel": ch.name, "type": ch.type,
                                 "event": data["event"], "referential": data["referential"]["id"]}
        error = None
        for attempt in range(1, attempts + 1):
            try:
                if ch.type == "email":
                    to = list(ch.recipients)
                    owner = (data["referential"].get("owner") or "").strip()
                    if ch.notify_owner and EMAIL_RE.match(owner) and owner not in to:
                        to.append(owner)
                    if not to:
                        raise RuntimeError("no recipient")
                    subject, body = mail_text(data, logs)
                    send_mail(smtp, to, subject, body, ca)
                    entry["detail"] = f"sent to {', '.join(to)}"
                else:
                    with client(self.settings, timeout=15) as c:
                        entry["detail"] = f"HTTP {post_webhook(ch, data, c)}"
                entry.update({"ok": True, "attempts": attempt})
                error = None
                break
            except Exception as e:  # noqa: BLE001
                error = f"{type(e).__name__}: {e}"[:500]
                if attempt < attempts:
                    time.sleep(2 ** (attempt * 2 - 1))  # 2 s, then 8 s
        if error:
            entry.update({"ok": False, "attempts": attempts, "detail": error})
            log.warning("Notification '%s' (%s) of %s not delivered: %s", ch.name, data["event"], data["referential"]["id"], error)
        self._record(entry)
        return entry

    def _record(self, entry: dict[str, Any]) -> None:
        with self._lock:
            try:
                lines = self.path.read_text(encoding="utf-8").splitlines() if self.path.exists() else []
                lines = [*lines[-(KEEP_DELIVERIES - 1):], json.dumps(entry, ensure_ascii=False)]
                self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            except OSError as e:
                log.warning("Cannot record the notification: %s", e)

    def deliveries(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            if not self.path.exists():
                return []
            lines = self.path.read_text(encoding="utf-8").splitlines()
        out = []
        for line in reversed(lines[-limit:]):
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

    def stop(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=True)
