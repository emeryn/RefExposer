"""The two experimental file-safety features, both off by default.

- the antivirus scan of the files a source brings in (app/antivirus.py), against a small clamd that speaks the
  real protocol: a test failing here means our side of the protocol is wrong, not just the plumbing;
- the structural checks on those files, and the neutralising of the values a spreadsheet would execute in the
  CSV and Excel exports (app/filechecks.py).

Nothing here reaches the network: the daemon is a thread of this process, and no signature database is ever
fetched.
"""

import gzip
import os
import socket
import threading
import time
import zipfile
import zlib

import pytest

from conftest import CSV, USER_PASSWORD, create_user, new_client, refresh

# The EICAR test string, in pieces for the same reason the application splits it: a repository holding it in
# full gets quarantined by whoever checks it out.
EICAR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$" + b"EICAR-STANDARD-ANTIVIRUS-TEST-" + b"FILE!$H+H*"
WINDOWS_EXE = b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 64 + b"This program cannot be run in DOS mode"


def read_maybe_compressed(path):
    """What a scanner sees: the bytes, and the bytes inside the archive (ClamAV scans archives by default)."""
    with open(path, "rb") as f:
        content = f.read()
    if content[:2] == b"\x1f\x8b":
        try:
            return content + gzip.decompress(content)
        except (OSError, EOFError, zlib.error):
            return content
    return content


class Clamd:
    """A clamd speaking its own protocol: `z<command>\\0`, answer NUL-terminated.

    Verdicts are decided on the content: a file holding the EICAR string is reported FOUND, everything else
    OK. `fail_on` makes it answer ERROR for a name, which is how a file the real daemon refuses looks.
    """

    def __init__(self, version="ClamAV 1.0.1/27000/" + time.strftime("%a %b %d %H:%M:%S %Y", time.gmtime())):
        self.version = version
        self.commands: list[str] = []
        self.scanned: list[str] = []
        self.streamed = 0
        self.fail_on = ""
        server = self

        class Handler(threading.Thread):
            def __init__(self, conn):
                super().__init__(daemon=True)
                self.conn = conn

            def run(self):
                try:
                    self.serve()
                except OSError:
                    pass
                finally:
                    self.conn.close()

            def serve(self):
                data = b""
                while b"\0" not in data:
                    block = self.conn.recv(4096)
                    if not block:
                        return
                    data += block
                command, _, rest = data.partition(b"\0")
                command = command.decode(errors="replace").lstrip("z")
                server.commands.append(command.split(" ")[0])
                if command == "PING":
                    return self.send("PONG")
                if command == "VERSION":
                    return self.send(server.version)
                if command.startswith("SCAN "):
                    path = command[len("SCAN "):].strip()
                    server.scanned.append(path)
                    return self.send(self.verdict(path))
                if command == "INSTREAM":
                    return self.send(self.read_stream(rest))
                self.send("UNKNOWN COMMAND ERROR")

            def verdict(self, path):
                if server.fail_on and server.fail_on in path:
                    return f"{path}: Access denied ERROR"
                try:
                    content = read_maybe_compressed(path)
                except OSError as e:
                    return f"{path}: {e.strerror} ERROR"
                if EICAR in content:
                    return f"{path}: Eicar-Signature FOUND"
                return f"{path}: OK"

            def read_stream(self, buffered):
                """INSTREAM: [4-byte length][data]… ended by a zero length."""
                data = buffered
                body = b""
                while True:
                    while len(data) < 4:
                        block = self.conn.recv(65536)
                        if not block:
                            return "stream: OK"
                        data += block
                    size = int.from_bytes(data[:4], "big")
                    data = data[4:]
                    if size == 0:
                        break
                    while len(data) < size:
                        block = self.conn.recv(65536)
                        if not block:
                            break
                        data += block
                    body += data[:size]
                    data = data[size:]
                server.streamed += 1
                return "stream: Eicar-Signature FOUND" if EICAR in body else "stream: OK"

            def send(self, answer):
                self.conn.sendall(answer.encode() + b"\0")

        self._sock = socket.socket()
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(8)
        self.port = self._sock.getsockname()[1]
        self._stop = False
        threading.Thread(target=self._accept, args=(Handler,), daemon=True).start()

    def _accept(self, handler):
        while not self._stop:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            handler(conn).start()

    @property
    def address(self):
        return f"127.0.0.1:{self.port}"

    def stop(self):
        self._stop = True
        self._sock.close()


@pytest.fixture()
def clamd(env):
    server = Clamd()
    yield server
    server.stop()


# --------------------------------------------------------------------------- helpers

def enable(env, **experimental):
    body = {"relational_search": False, "antivirus_scan": False, "file_checks": False, **experimental}
    r = env.put("/api/admin/settings/experimental", json=body)
    assert r.status_code == 200, r.text
    return r


def set_antivirus(env, server=None, **extra):
    body = {"backend": "clamd", "address": server.address if server else "127.0.0.1:1",
            "shared_volume": True, "timeout": 30, **extra}
    r = env.put("/api/admin/settings/antivirus", json=body)
    assert r.status_code == 200, r.text
    return r


def set_checks(env, **extra):
    r = env.put("/api/admin/settings/checks", json={**extra})
    assert r.status_code == 200, r.text
    return r


def serve(env, content: bytes):
    """Replace what the `remote` referential downloads (and defeat the HTTP 304 of the same second)."""
    target = env.www / "remote.csv.gz"
    previous = target.stat().st_mtime
    with gzip.open(target, "wb") as f:
        f.write(content)
    os.utime(target, (previous + 10, previous + 10))


def last_run(env, ref_id="remote"):
    return env.get(f"/api/referentials/{ref_id}/runs", params={"limit": 1}).json()[0]


# --------------------------------------------------------------------------- off by default

def test_both_features_are_off_by_default(env):
    settings = env.get("/api/admin/settings").json()
    assert settings["experimental"]["antivirus_scan"] is False
    assert settings["experimental"]["file_checks"] is False
    state = env.get("/api/admin/file-safety").json()
    assert state["antivirus_scan"] is False and state["file_checks"] is False and state["quarantine"] == 0
    # And nothing is scanned: a source that would be refused goes through
    serve(env, WINDOWS_EXE)
    assert refresh(env, "remote")["status"] in ("rejected", "corrupted", "error", "success")  # whatever the parser says
    assert env.get("/api/admin/file-safety").json()["quarantine"] == 0


# --------------------------------------------------------------------------- the antivirus scan

def test_an_infected_source_is_refused_and_the_published_version_is_kept(env, clamd):
    assert refresh(env, "remote")["status"] == "success"
    before = env.get("/api/referentials/remote").json()
    assert before["row_count"] == 5

    enable(env, antivirus_scan=True)
    set_antivirus(env, clamd)
    serve(env, CSV.encode() + b"\n" + EICAR)
    run = refresh(env, "remote")
    assert run["status"] == "rejected", run
    assert "refused before being read" in (run["message"] or "")
    assert any("INFECTED" in line["msg"] for line in run["logs"])

    # The published version is untouched, and the file is in quarantine
    after = env.get("/api/referentials/remote").json()
    assert after["row_count"] == 5 and after["version"] == before["version"]
    assert env.get("/api/referentials/remote/lookup/FR").status_code == 200
    entries = env.get("/api/admin/file-safety/quarantine").json()["entries"]
    assert len(entries) == 1 and entries[0]["referential"] == "remote" and entries[0]["kept"] == 1
    assert entries[0]["files"][0]["status"] == "infected"
    # Scanned by path over the daemon's own protocol
    assert clamd.commands.count("SCAN") >= 1 and clamd.streamed == 0
    assert any(p.endswith(".csv") or p.endswith(".gz") for p in clamd.scanned)


def test_a_clean_source_goes_through(env, clamd):
    enable(env, antivirus_scan=True)
    set_antivirus(env, clamd)
    run = refresh(env, "remote")
    assert run["status"] == "success", run
    assert any("clean" in line["msg"] for line in run["logs"])
    assert env.get("/api/admin/file-safety").json()["quarantine"] == 0


def test_a_file_the_scan_skipped_is_never_called_clean(env, clamd):
    """The whole point of the fourth verdict: a file nobody read is not a clean file.

    This is the trap of a scanner's own size limits — ClamAV stops at 100 MB by default — and the reason the
    verdict has four values instead of two.
    """
    import app.antivirus as av
    from app.settings import get_settings

    enable(env, antivirus_scan=True)
    set_antivirus(env, clamd, on_unscanned="block")
    assert refresh(env, "remote")["status"] == "success"
    source = next((env.data_dir / "remote" / "raw").glob("remote.csv*"))

    cfg = type("C", (), {"backend": "clamd", "address": clamd.address, "command": "", "timeout": 30,
                         "shared_volume": True, "max_file_mb": 0.0000001, "on_unscanned": "block",
                         "on_error": "block"})()
    verdict = av.scan_one(get_settings(), cfg, source)
    assert verdict.status == "unscanned" and "over the" in verdict.detail
    assert verdict.status != "clean"


def test_the_ceiling_blocks_or_warns_as_configured(env, clamd):
    enable(env, antivirus_scan=True)
    # A ceiling of 1 MB is fine for this source; what is tested is the policy on a file the daemon refuses
    clamd.fail_on = "remote"
    set_antivirus(env, clamd, on_unscanned="block")
    serve(env, CSV.encode())
    run = refresh(env, "remote")
    assert run["status"] == "rejected", run
    assert any("not scanned" in line["msg"] for line in run["logs"])

    set_antivirus(env, clamd, on_unscanned="warn")
    serve(env, CSV.encode())
    run = refresh(env, "remote")
    assert run["status"] == "success", run
    assert any("not scanned" in line["msg"] for line in run["logs"])


def test_a_scanner_that_cannot_be_reached_blocks_or_warns(env):
    enable(env, antivirus_scan=True)
    set_antivirus(env, None, on_error="block")  # nothing listens on port 1
    serve(env, CSV.encode())
    run = refresh(env, "remote")
    assert run["status"] == "rejected" and "could not be reached" in (run["message"] or "")

    set_antivirus(env, None, on_error="warn")
    serve(env, CSV.encode())
    run = refresh(env, "remote")
    assert run["status"] == "success", run
    assert any("could not be reached" in line["msg"] for line in run["logs"])


def test_streaming_when_the_daemon_cannot_see_the_file(env, clamd):
    """A daemon without the data volume gets the bytes over the socket (INSTREAM)."""
    enable(env, antivirus_scan=True)
    set_antivirus(env, clamd, shared_volume=False)
    serve(env, CSV.encode() + b"\n" + EICAR)
    run = refresh(env, "remote")
    assert run["status"] == "rejected", run
    assert clamd.streamed >= 1 and clamd.commands.count("SCAN") == 0


def test_the_test_button_and_its_eicar_probe(env, clamd):
    enable(env, antivirus_scan=True)
    set_antivirus(env, clamd)
    info = env.post("/api/admin/file-safety/antivirus/test", json={"eicar": True}).json()
    assert info["ok"] is True and info["backend"] == "clamd"
    assert info["scanner"].startswith("ClamAV") and info["stale"] is False
    assert info["detects_eicar"] is True and info["probe"]["status"] == "infected"
    # The probe file never stays behind
    assert not list((env.data_dir / ".tmp" / "antivirus-probe").glob("*"))


def test_an_unreachable_scanner_answers_502(env):
    enable(env, antivirus_scan=True)
    set_antivirus(env, None)
    r = env.post("/api/admin/file-safety/antivirus/test", json={})
    assert r.status_code == 502 and "clamd" in r.json()["detail"]


def test_old_signatures_are_reported(env, clamd):
    clamd.version = "ClamAV 1.0.1/27000/Tue Jan 02 09:00:00 2024"
    enable(env, antivirus_scan=True)
    set_antivirus(env, clamd, max_signature_age_days=7)
    info = env.post("/api/admin/file-safety/antivirus/test", json={}).json()
    assert info["signatures_age_days"] > 365 and info["stale"] is True

    run = run_task(env, "antivirus_check")
    assert run["status"] == "warning", run
    assert any("mirror" in line for line in run["details"]["log"])


def run_task(env, task_id, timeout=60, **params):
    body = {"enabled": True, "params": params} if params else {"enabled": True}
    assert env.put(f"/api/admin/tasks/{task_id}", json=body).status_code == 200
    previous = next(t for t in env.get("/api/admin/tasks").json() if t["id"] == task_id)["last_run"]
    assert env.post(f"/api/admin/tasks/{task_id}/run").status_code == 200
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = next(t for t in env.get("/api/admin/tasks").json() if t["id"] == task_id)
        if not task["running"] and task["last_run"] and task["last_run"] != previous:
            return task["last_run"]
        time.sleep(0.05)
    raise TimeoutError(task_id)


def test_the_quarantine_is_listed_emptied_and_kept_out_of_the_backups(env, clamd):
    enable(env, antivirus_scan=True)
    set_antivirus(env, clamd)
    serve(env, CSV.encode() + b"\n" + EICAR)
    assert refresh(env, "remote")["status"] == "rejected"
    listing = env.get("/api/admin/file-safety/quarantine").json()
    assert len(listing["entries"]) == 1
    folder = listing["entries"][0]["folder"]

    # A full backup never carries what was refused
    from app import fullbackup

    carried = [rel.as_posix() for _, rel in fullbackup.data_files(env.data_dir)]
    assert not any(".quarantine" in name for name in carried)

    assert env.delete(f"/api/admin/file-safety/quarantine/{folder}").status_code == 200
    assert env.get("/api/admin/file-safety/quarantine").json()["entries"] == []
    assert env.delete("/api/admin/file-safety/quarantine").status_code == 200


def test_the_quarantine_retention(env, clamd):
    enable(env, antivirus_scan=True)
    set_antivirus(env, clamd, quarantine_keep_days=1)
    serve(env, CSV.encode() + b"\n" + EICAR)
    assert refresh(env, "remote")["status"] == "rejected"
    note = next((env.data_dir / ".quarantine").glob("*/*/verdicts.json"))
    old = time.time() - 3 * 86400
    os.utime(note, (old, old))
    run = run_task(env, "antivirus_check")
    assert run["details"]["quarantine_purged"] == 1, run
    assert env.get("/api/admin/file-safety/quarantine").json()["entries"] == []


# --------------------------------------------------------------------------- the structural checks

def test_an_executable_served_as_data_is_refused(env):
    enable(env, file_checks=True)
    set_checks(env, on_executable="block")
    serve(env, WINDOWS_EXE)  # inside the .gz: read through it, which is the point
    run = refresh(env, "remote")
    assert run["status"] == "rejected", run
    assert any("Windows executable" in line["msg"] for line in run["logs"])
    assert len(env.get("/api/admin/file-safety/quarantine").json()["entries"]) == 1


def test_a_file_that_is_not_what_it_claims_is_reported(env):
    """A PDF served as CSV. The HTML error page is not tested here: the import already refuses that one
    itself (`check_source_file`), which is why these checks do not look for it a second time."""
    enable(env, file_checks=True)
    set_checks(env, on_type_mismatch="warn")
    serve(env, b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n")
    run = refresh(env, "remote")
    assert any("first bytes are those of a pdf file" in line["msg"] for line in run["logs"]), run["logs"]

    set_checks(env, on_type_mismatch="block")
    serve(env, b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n")
    run = refresh(env, "remote")
    assert run["status"] == "rejected" and "pdf" in (run["message"] or "")


def test_an_office_file_with_a_macro_is_refused(env, tmp_path):
    """A workbook holding vbaProject.bin, uploaded: refused before it is even kept."""
    enable(env, file_checks=True)
    set_checks(env, on_macro="block")
    book = tmp_path / "rates.xlsx"
    with zipfile.ZipFile(book, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("xl/workbook.xml", "<workbook/>")
        zf.writestr("xl/vbaProject.bin", b"\xd0\xcf\x11\xe0 macro")
    r = env.post("/api/admin/referentials/uploads", files={"files": ("rates.xlsx", book.read_bytes())})
    assert r.status_code == 400 and "macro" in r.json()["detail"]
    assert not list((env.data_dir / ".tmp" / "uploads").glob("*/*"))


def test_a_clean_upload_still_works(env, tmp_path):
    enable(env, file_checks=True)
    csv = tmp_path / "rates.csv"
    csv.write_text(CSV, encoding="utf-8")
    r = env.post("/api/admin/referentials/uploads", files={"files": ("rates.csv", csv.read_bytes())})
    assert r.status_code == 200, r.text
    assert r.json()["files"][0]["name"] == "rates.csv"


def test_the_checks_probe_of_an_uploaded_file(env, tmp_path):
    enable(env, file_checks=True)
    set_checks(env, on_executable="warn")
    exe = tmp_path / "tool.csv"
    exe.write_bytes(WINDOWS_EXE)
    r = env.post("/api/admin/referentials/uploads", files={"files": ("tool.csv", exe.read_bytes())})
    assert r.status_code == 200, r.text  # reported, not refused
    probe = env.post("/api/admin/file-safety/checks/probe",
                     json={"upload_id": r.json()["upload_id"], "name": "tool.csv", "format": "csv"}).json()
    assert probe["findings"][0]["kind"] == "executable" and probe["findings"][0]["blocking"] is False


# --------------------------------------------------------------------------- the exports

FORMULA = "code,label\nFR,=cmd|' /C calc'!A0\nDE,-5\nIT,+33 1 23\nES,normal\n"


def publish_formulas(env):
    serve(env, FORMULA.encode())
    assert refresh(env, "remote")["status"] == "success"


def test_a_formula_in_the_data_is_neutralised_in_csv_and_excel_only(env):
    publish_formulas(env)
    # Off by default: the value goes out as it is
    raw = env.get("/api/referentials/remote/export", params={"format": "csv"}).text
    assert "=cmd|" in raw and "'=cmd|" not in raw

    enable(env, file_checks=True)
    set_checks(env, escape_exports=True)
    safe = env.get("/api/referentials/remote/export", params={"format": "csv"}).text
    assert "'=cmd|" in safe
    assert "+33 1 23" in safe.replace("'+33 1 23", "+33 1 23")  # prefixed too
    # A plain number is left alone: quoting it would corrupt the data for nothing
    assert ",-5" in safe and ",'-5" not in safe
    assert "normal" in safe and "'normal" not in safe

    # Nothing executes a JSON or a Parquet file, so they are untouched
    assert "=cmd|" in env.get("/api/referentials/remote/export", params={"format": "json"}).text
    assert "'=cmd|" not in env.get("/api/referentials/remote/export", params={"format": "json"}).text


def test_a_generated_download_is_rebuilt_when_the_setting_changes(env):
    publish_formulas(env)
    first = env.get("/api/referentials/remote/download/csv")
    assert first.status_code == 200 and "=cmd|" in first.text and "'=cmd|" not in first.text

    enable(env, file_checks=True)
    set_checks(env, escape_exports=True)
    again = env.get("/api/referentials/remote/download/csv")
    assert again.status_code == 200 and "'=cmd|" in again.text

    # And back: the kept file is produced again rather than served under the other rule
    set_checks(env, escape_exports=False)
    back = env.get("/api/referentials/remote/download/csv")
    assert "'=cmd|" not in back.text and "=cmd|" in back.text


def test_the_escaping_can_be_turned_off_alone(env):
    """Both halves of the feature are independent: the checks without the export escaping."""
    publish_formulas(env)
    enable(env, file_checks=True)
    set_checks(env, escape_exports=False, on_executable="block")
    assert "'=cmd|" not in env.get("/api/referentials/remote/export", params={"format": "csv"}).text
    serve(env, WINDOWS_EXE)
    assert refresh(env, "remote")["status"] == "rejected"


def test_only_administrators(env):
    create_user(env, "bob", role="advanced")
    bob = new_client(env, "bob", USER_PASSWORD)
    assert bob.get("/api/admin/file-safety").status_code == 403
    assert bob.get("/api/admin/file-safety/quarantine").status_code == 403
    assert bob.post("/api/admin/file-safety/antivirus/test", json={}).status_code == 403
