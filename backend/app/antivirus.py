"""Antivirus scan of the files a referential brings in, before anything reads them.

Why this exists, and what it is honestly worth. RefExposer never *executes* a source file: DuckDB parses it.
So a scan here protects two things — the people who download the exports built from that data, and the
obligation many organisations have to scan whatever enters them. It is not what keeps the server standing;
that is the job of the rootless container, the disk guard and the resource limits.

Where it runs: every source, whatever its kind, lands its files in `data/<id>/raw/` before the import reads
them, so one hook covers HTTP, S3, Git, an upload and the import folder. The files of a manual import and of
an upload are scanned where they arrive, which is earlier and matters more: a human chose to put them there.

**Entirely offline.** Two backends, neither of which reaches the internet:

- `clamd`: the ClamAV daemon, over a TCP or Unix socket, talked to with its own protocol (written here with
  the standard library — no dependency to add to a hash-pinned requirements file). Run it as a container
  beside the application, sharing the data volume, and the daemon reads the file by path: nothing is copied
  over the socket. A daemon that cannot see the path falls back to streaming the file to it (`INSTREAM`),
  which its own `StreamMaxLength` bounds.
- `command`: any scanner already present on the host or in the image — `clamscan`, a corporate agent, a YARA
  binary. Called as `<command> <path>`, read with the exit code ClamAV established: 0 clean, 1 infected,
  anything else an error.

**What is never claimed.** A file the scanner skipped is reported `unscanned`, never `clean`. That distinction
is the whole point: ClamAV stops at `MaxFileSize` / `MaxScanSize` (100 MB by default), so a 40 GB feed would
otherwise come back "clean" having never been read, and an instance would believe itself protected. The policy
for `unscanned` and for a scanner that cannot be reached is the administrator's to set, and defaults to
refusing the version rather than publishing data nobody looked at.

**Signature freshness** is reported, never fetched: in an air-gapped network `freshclam` needs a local mirror,
and a scanner whose database is six months old is a comfort, not a control. The `antivirus_check` task says
how old the database is and warns past a threshold.
"""

from __future__ import annotations

import logging
import os
import re
import shlex
import socket
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Literal

from .settings import Settings

log = logging.getLogger(__name__)

Status = Literal["clean", "infected", "unscanned", "error"]

CHUNK = 1 << 20
# Longest answer a daemon sends for one command: a verdict line, not a payload
MAX_ANSWER = 64 << 10
# What ClamAV's exit codes mean (clamscan(1)); any other code is a failure of the scanner itself
EXIT_CLEAN, EXIT_INFECTED = 0, 1


# The EICAR test file: 68 bytes every scanner agrees to report, and which is not malware. Built from pieces on
# purpose — written out in full, a copy of this source would itself be quarantined by the antivirus of whoever
# checks out the repository, which is a famously annoying way to learn that the string works.
EICAR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$" + b"EICAR-STANDARD-ANTIVIRUS-TEST-" + b"FILE!$H+H*"


class AntivirusError(Exception):
    """The scanner could not be reached, or answered something that is not a verdict."""


@dataclass
class Verdict:
    """What the scanner said about one file.

    `status` is deliberately four-valued: a file nobody read is `unscanned`, which is not `clean`.
    """

    path: Path
    status: Status
    signature: str = ""  # what matched, for an infected file
    detail: str = ""
    size: int = 0

    @property
    def blocking(self) -> bool:
        return self.status == "infected"

    def line(self) -> str:
        """One line for the run log."""
        name = self.path.name
        if self.status == "clean":
            return f"{name}: clean"
        if self.status == "infected":
            return f"{name}: INFECTED — {self.signature}"
        if self.status == "unscanned":
            return f"{name}: not scanned ({self.detail})"
        return f"{name}: the scan failed ({self.detail})"

    def as_dict(self) -> dict[str, Any]:
        return {"file": self.path.name, "status": self.status, "signature": self.signature,
                "detail": self.detail, "size": self.size}


@dataclass
class Report:
    """Every verdict of one scan, and what the configured policy makes of them."""

    verdicts: list[Verdict] = field(default_factory=list)
    refused: list[Verdict] = field(default_factory=list)
    scanner: str = ""

    @property
    def infected(self) -> list[Verdict]:
        return [v for v in self.verdicts if v.status == "infected"]

    @property
    def unscanned(self) -> list[Verdict]:
        return [v for v in self.verdicts if v.status == "unscanned"]

    @property
    def errors(self) -> list[Verdict]:
        return [v for v in self.verdicts if v.status == "error"]

    def summary(self) -> str:
        parts = [f"{len(self.verdicts)} file(s) scanned by {self.scanner or 'the scanner'}"]
        if self.infected:
            parts.append(f"{len(self.infected)} INFECTED")
        if self.unscanned:
            parts.append(f"{len(self.unscanned)} not scanned")
        if self.errors:
            parts.append(f"{len(self.errors)} failed")
        return ", ".join(parts)

    def as_dict(self) -> dict[str, Any]:
        return {"scanner": self.scanner, "files": len(self.verdicts),
                "infected": [v.as_dict() for v in self.infected],
                "unscanned": [v.as_dict() for v in self.unscanned],
                "errors": [v.as_dict() for v in self.errors],
                "refused": [v.as_dict() for v in self.refused]}


# --------------------------------------------------------------------------- the ClamAV daemon

class Clamd:
    """The few clamd commands this needs: PING, VERSION, SCAN and INSTREAM.

    The protocol: a command is sent prefixed with `z` and terminated by a NUL byte, and the answer comes back
    NUL-terminated as well. One connection per command is what clamd expects.
    """

    def __init__(self, address: str, timeout: float = 120.0):
        self.address = address.strip()
        self.timeout = timeout

    def _connect(self) -> socket.socket:
        if self.address.startswith(("unix:", "/")):
            path = self.address[5:] if self.address.startswith("unix:") else self.address
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(self.timeout)
            s.connect(path)
            return s
        host, _, port = self.address.rpartition(":")
        if not host:
            host, port = self.address, "3310"
        s = socket.create_connection((host, int(port or 3310)), timeout=self.timeout)
        s.settimeout(self.timeout)
        return s

    def _command(self, command: str, payload: Iterable[bytes] | None = None) -> str:
        try:
            with self._connect() as s:
                s.sendall(b"z" + command.encode() + b"\0")
                for block in payload or ():
                    s.sendall(block)
                return self._answer(s)
        except (OSError, ValueError) as e:
            raise AntivirusError(f"clamd at {self.address}: {type(e).__name__}: {e}") from e

    @staticmethod
    def _answer(s: socket.socket) -> str:
        chunks: list[bytes] = []
        total = 0
        while True:
            block = s.recv(4096)
            if not block:
                break
            chunks.append(block)
            total += len(block)
            if b"\0" in block or total > MAX_ANSWER:
                break
        return b"".join(chunks).split(b"\0")[0].decode(errors="replace").strip()

    def ping(self) -> bool:
        return self._command("PING") == "PONG"

    def version(self) -> str:
        return self._command("VERSION")

    def scan_path(self, path: Path) -> str:
        """Verdict line for a file the daemon reads itself (shared volume): nothing crosses the socket."""
        return self._command(f"SCAN {path}")

    def scan_stream(self, path: Path) -> str:
        """Verdict line for a file streamed to the daemon, for a daemon that cannot see the path.

        `INSTREAM` frames the file as [4-byte length][data]…, ended by a zero length. The daemon refuses a
        stream longer than its own `StreamMaxLength` (25 MB by default), which comes back as an error and is
        reported as *not scanned*.
        """
        def blocks() -> Iterable[bytes]:
            with path.open("rb") as f:
                while chunk := f.read(CHUNK):
                    yield len(chunk).to_bytes(4, "big") + chunk
            yield (0).to_bytes(4, "big")

        return self._command("INSTREAM", blocks())


# `<path>: OK` · `<path>: Eicar-Test-Signature FOUND` · `<path>: <reason> ERROR`
_VERDICT = re.compile(r"^(?P<path>.*?):\s*(?P<body>.*)$", re.S)
# ClamAV 1.0.1/27000/Tue Jan 02 09:00:00 2024
_VERSION = re.compile(r"^(?P<product>[^/]+)(?:/(?P<sigs>\d+)/(?P<date>.+))?$")


def parse_verdict(path: Path, answer: str) -> Verdict:
    """Turn a clamd answer line into a verdict. An answer that is not one is an error, never a clean file."""
    m = _VERDICT.match(answer.strip())
    body = (m.group("body") if m else answer).strip()
    size = path.stat().st_size if path.exists() else 0
    if body.endswith("FOUND"):
        return Verdict(path, "infected", signature=body[: -len("FOUND")].strip(), size=size)
    if body == "OK":
        return Verdict(path, "clean", size=size)
    if body.endswith("ERROR"):
        reason = body[: -len("ERROR")].strip()
        # A file the daemon would not read (too large, excluded, unreadable) is *not* a clean file
        return Verdict(path, "unscanned", detail=reason or "the scanner refused the file", size=size)
    return Verdict(path, "error", detail=body or "unreadable answer", size=size)


def signature_age(version: str) -> tuple[str, int | None]:
    """(database date as written by the scanner, days since then). The date is reported, never fetched."""
    m = _VERSION.match(version.strip())
    raw = (m.group("date") or "").strip() if m else ""
    if not raw:
        return "", None
    for fmt in ("%a %b %d %H:%M:%S %Y", "%a %b  %d %H:%M:%S %Y", "%d %b %Y %H-%M %z"):
        try:
            when = datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        return raw, max(0, (datetime.now(timezone.utc) - when).days)
    return raw, None


# --------------------------------------------------------------------------- an external command

def run_command(command: str, path: Path, timeout: float) -> Verdict:
    """Scan with any local binary. 0 clean, 1 infected (clamscan's convention), anything else an error.

    The file is passed as an argument, never through a shell: a file name is attacker-controlled data.
    """
    argv = shlex.split(command) + [str(path)]
    size = path.stat().st_size if path.exists() else 0
    try:
        done = subprocess.run(argv, capture_output=True, timeout=timeout, check=False,  # noqa: S603
                              env={**os.environ, "LC_ALL": "C"})
    except FileNotFoundError as e:
        raise AntivirusError(f"scanner '{argv[0]}' not found") from e
    except subprocess.TimeoutExpired:
        return Verdict(path, "unscanned", detail=f"the scanner did not answer within {timeout:.0f}s", size=size)
    except OSError as e:
        raise AntivirusError(f"scanner '{argv[0]}': {e}") from e
    output = (done.stdout or b"").decode(errors="replace").strip()
    if done.returncode == EXIT_CLEAN:
        return Verdict(path, "clean", size=size)
    if done.returncode == EXIT_INFECTED:
        found = next((line for line in output.splitlines() if "FOUND" in line), output.splitlines()[0] if output else "")
        signature = found.split(":", 1)[-1].replace("FOUND", "").strip() or "reported by the scanner"
        return Verdict(path, "infected", signature=signature, size=size)
    detail = ((done.stderr or b"").decode(errors="replace").strip() or output or
              f"exit code {done.returncode}")
    return Verdict(path, "error", detail=detail[:300], size=size)


# --------------------------------------------------------------------------- what the application calls

def scanner_of(cfg: Any) -> str:
    return f"clamd ({cfg.address})" if cfg.backend == "clamd" else f"command ({shlex.split(cfg.command)[0]})" \
        if cfg.command.strip() else "command"


def check(settings: Settings, cfg: Any) -> dict[str, Any]:
    """Is the scanner there, and how old is its database? What the Test button and the task show."""
    if cfg.backend == "clamd":
        client = Clamd(cfg.address, cfg.timeout)
        if not client.ping():
            raise AntivirusError(f"clamd at {cfg.address} did not answer PONG")
        version = client.version()
        date, days = signature_age(version)
        return {"ok": True, "backend": "clamd", "scanner": version, "signatures_date": date,
                "signatures_age_days": days, "stale": days is not None and days > cfg.max_signature_age_days}
    if not cfg.command.strip():
        raise AntivirusError("no scanner command configured")
    argv = shlex.split(cfg.command)
    try:
        done = subprocess.run([*argv, "--version"], capture_output=True, timeout=min(cfg.timeout, 30),  # noqa: S603
                              check=False)
    except (OSError, subprocess.SubprocessError) as e:
        raise AntivirusError(f"scanner '{argv[0]}': {type(e).__name__}: {e}") from e
    version = ((done.stdout or b"") + (done.stderr or b"")).decode(errors="replace").strip().splitlines()
    date, days = signature_age(version[0] if version else "")
    return {"ok": True, "backend": "command", "scanner": version[0] if version else argv[0],
            "signatures_date": date, "signatures_age_days": days,
            "stale": days is not None and days > cfg.max_signature_age_days}


def scan_one(settings: Settings, cfg: Any, path: Path) -> Verdict:
    """Scan one file. A file over the configured ceiling is reported *not scanned*, never clean."""
    size = path.stat().st_size
    ceiling = cfg.max_file_mb * (1 << 20)
    if ceiling and size > ceiling:
        return Verdict(path, "unscanned", size=size,
                       detail=f"{size:,} bytes, over the {cfg.max_file_mb} MB the scan is limited to")
    if cfg.backend == "command":
        return run_command(cfg.command, path, cfg.timeout)
    client = Clamd(cfg.address, cfg.timeout)
    answer = client.scan_path(path) if cfg.shared_volume else client.scan_stream(path)
    verdict = parse_verdict(path, answer)
    if verdict.status == "error" and cfg.shared_volume and "No such file" in verdict.detail:
        # The daemon does not see the volume: stream it instead, and say so once
        log.warning("clamd cannot read %s by path: streaming it instead (check the shared volume)", path)
        verdict = parse_verdict(path, client.scan_stream(path))
    return verdict


def scan(settings: Settings, cfg: Any, paths: Iterable[Path],
         log_line: Callable[[str], None] = lambda m: None) -> Report:
    """Scan files and apply the policy. Returns what was found and what has to be refused.

    A scanner that cannot be reached at all raises: the caller decides, since "the antivirus is down" is a
    different question from "this file is infected", and answering it silently is how a control stops being one.
    """
    report = Report(scanner=scanner_of(cfg))
    for path in paths:
        if not path.is_file():
            continue
        verdict = scan_one(settings, cfg, path)
        report.verdicts.append(verdict)
        log_line(verdict.line())
        if verdict.status == "infected":
            report.refused.append(verdict)
        elif verdict.status == "unscanned" and cfg.on_unscanned == "block":
            report.refused.append(verdict)
        elif verdict.status == "error" and cfg.on_error == "block":
            report.refused.append(verdict)
    log_line(report.summary())
    return report


# --------------------------------------------------------------------------- quarantine

def quarantine_dir(data_dir: Path) -> Path:
    return data_dir / ".quarantine"


def quarantine(settings: Settings, ref_id: str, run_id: str, verdicts: list[Verdict],
               log_line: Callable[[str], None] = lambda m: None) -> list[str]:
    """Move the refused files out of the way, with a note saying why.

    Kept rather than deleted: an administrator has to be able to look at what was refused, and a false
    positive on a partner feed is the usual reason to want it. The folder is left out of the backups and
    never read by an import.
    """
    import json

    folder = quarantine_dir(settings.data_dir) / ref_id / run_id
    folder.mkdir(parents=True, exist_ok=True)
    moved: list[str] = []
    for v in verdicts:
        if not v.path.is_file():
            continue
        target = folder / v.path.name
        try:
            v.path.replace(target)
        except OSError as e:  # pragma: no cover - a different filesystem
            log.error("[%s] cannot quarantine %s: %s", ref_id, v.path.name, e)
            continue
        moved.append(v.path.name)
    if moved:
        (folder / "verdicts.json").write_text(
            json.dumps({"referential": ref_id, "run": run_id, "at": datetime.now(timezone.utc).isoformat(),
                        "files": [v.as_dict() for v in verdicts]}, indent=2), encoding="utf-8")
        log_line(f"{len(moved)} file(s) moved to quarantine ({folder})")
    return moved


def quarantined(data_dir: Path, limit: int = 200) -> list[dict[str, Any]]:
    """What is in quarantine, newest first: what the administration page lists."""
    import json

    root = quarantine_dir(data_dir)
    if not root.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for note in sorted(root.glob("*/*/verdicts.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
        try:
            data = json.loads(note.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        files = [p for p in note.parent.iterdir() if p.is_file() and p.name != "verdicts.json"]
        out.append({**data, "folder": str(note.parent.relative_to(root)),
                    "bytes": sum(p.stat().st_size for p in files), "kept": len(files)})
    return out
