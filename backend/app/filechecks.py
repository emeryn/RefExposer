"""Structural checks on the files that come in, and on the spreadsheets that go out. No dependency, no daemon.

This is the other half of file safety, and for a service whose job is to **redistribute third-party data** it
is arguably the useful half. An antivirus looks for what it already knows; these look at what a data file is
allowed to be, which is a much narrower question and therefore answerable.

On the way in — three questions about a file a source just produced:

- **Is it an executable?** A feed that answers with a Windows or Linux binary is not a feed, whatever its
  extension says. Cheap to see (the first bytes), and it does not depend on a signature database being fresh.
- **Does it carry macros?** An `.xlsm`, or an Office file holding `vbaProject.bin`. Nothing in a referential
  needs a macro, and a workbook that has one was built to be opened, not to be read as data.
- **Is it what it claims to be?** A file declared CSV whose first bytes are those of a PDF, of an old
  Office document or of a database is something else entirely, and saying so is more use than the parser
  error that would follow. (An HTML error page answered with an HTTP 200 is **already** refused upstream by
  `ingest.check_source_file`, which knows how to say so: there is no second check for it here.)

On the way out — one question about the exports:

- **Will a spreadsheet treat a value as a formula?** A cell beginning with `=`, `+`, `-`, `@`, a tab or a
  carriage return is executed by Excel and LibreOffice when the file is opened. Data coming from a partner
  feed is attacker-controlled text, and RefExposer hands it to analysts as CSV and XLSX: that is a path from
  *hostile data in* to *code running on a colleague's machine*, and no antivirus sees it, because the file is
  a perfectly valid CSV. Neutralising it prefixes the value with a single quote, which spreadsheets read as
  "this is text".

  A plain number is left alone (`-5`, `+12.5`): a negative figure is not an attack, and quoting it would
  corrupt the data to no purpose. Everything else beginning with one of those characters is prefixed, which
  **does change the exported value** — which is exactly why this is a feature an administrator turns on
  knowingly, and why the Parquet, JSON and JSON Lines exports are left untouched: nothing executes them.
"""

from __future__ import annotations

import logging
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Literal

log = logging.getLogger(__name__)

Kind = Literal["executable", "macro", "type_mismatch"]

# First bytes -> what the file really is. Only signatures worth refusing or reporting on a data feed.
EXECUTABLES: tuple[tuple[bytes, str], ...] = (
    (b"MZ", "a Windows executable (PE/DOS)"),
    (b"\x7fELF", "a Linux executable (ELF)"),
    (b"\xca\xfe\xba\xbe", "a macOS universal binary"),
    (b"\xcf\xfa\xed\xfe", "a macOS executable (Mach-O)"),
    (b"\xce\xfa\xed\xfe", "a macOS executable (Mach-O)"),
    (b"dex\n", "an Android executable (DEX)"),
    (b"#!", "a script with a shebang"),
)
# Signatures of formats a data file may legitimately be, used to tell a mismatch from the real thing
SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"PK\x03\x04", "zip"),
    (b"\x1f\x8b", "gzip"),
    (b"PAR1", "parquet"),
    (b"SQLite format 3\x00", "sqlite"),
    (b"%PDF", "pdf"),
    (b"\xd0\xcf\x11\xe0", "ole"),  # .xls / .doc of before 2007, and a common macro carrier
    (b"BZh", "bzip2"),
    (b"\xfd7zXZ\x00", "xz"),
    (b"\x28\xb5\x2f\xfd", "zstd"),
    (b"7z\xbc\xaf\x27\x1c", "7z"),
    (b"Rar!", "rar"),
)
# Formats whose files are text: what a declared format is allowed to start with
TEXT_FORMATS = frozenset({"csv", "tsv", "txt", "json", "jsonl", "ndjson", "xml", "yaml", "sql", "values"})
# Formats that are a zip underneath
ZIP_FORMATS = frozenset({"xlsx", "xlsm", "ods", "docx"})
# Office members that mean a macro is in the file
MACRO_MEMBERS = ("vbaproject.bin", "vbamacros", "macros/vba")
MACRO_SUFFIXES = (".xlsm", ".xlsb", ".docm", ".pptm", ".xlm")
PROBE = 4096


class FileCheckError(ValueError):
    """A file the checks refuse."""


@dataclass
class Finding:
    path: Path
    kind: Kind
    detail: str
    blocking: bool = False

    def line(self) -> str:
        return f"{self.path.name}: {self.detail}"

    def as_dict(self) -> dict[str, Any]:
        return {"file": self.path.name, "kind": self.kind, "detail": self.detail, "blocking": self.blocking}


@dataclass
class Findings:
    findings: list[Finding] = field(default_factory=list)
    checked: int = 0

    @property
    def refused(self) -> list[Finding]:
        return [f for f in self.findings if f.blocking]

    def summary(self) -> str:
        if not self.findings:
            return f"{self.checked} file(s) checked: nothing to report"
        return (f"{self.checked} file(s) checked, {len(self.findings)} finding(s)"
                + (f", {len(self.refused)} refused" if self.refused else ""))

    def as_dict(self) -> dict[str, Any]:
        return {"checked": self.checked, "findings": [f.as_dict() for f in self.findings],
                "refused": [f.as_dict() for f in self.refused]}


# --------------------------------------------------------------------------- on the way in

def head(path: Path, size: int = PROBE) -> bytes:
    """The first bytes of a file, read **through** gzip when it is one.

    A line-based source is often left compressed (DuckDB reads it as it decompresses), so looking at the
    gzip header would answer "this is a gzip file" and see nothing of what it carries.
    """
    import gzip
    import zlib

    if path.name.lower().endswith(".gz"):
        try:
            with gzip.open(path, "rb") as f:
                return f.read(size)
        except (OSError, EOFError, zlib.error):
            return b""
    with path.open("rb") as f:
        return f.read(size)


def looks_executable(start: bytes) -> str:
    """What executable this is, or an empty string. `#!` only counts at the very first byte."""
    for magic, what in EXECUTABLES:
        if start.startswith(magic):
            return what
    return ""


def real_format(start: bytes) -> str:
    """The format the first bytes say it is, or an empty string when they say nothing (plain text)."""
    for magic, name in SIGNATURES:
        if start.startswith(magic):
            return name
    return ""


def has_macros(path: Path) -> str:
    """Whether an Office file carries a macro, read from the archive's own list of members.

    An `.ole` workbook (before 2007) cannot be read this way; its extension and its signature are what is
    reported, since a macro inside one cannot be ruled out.
    """
    if path.suffix.lower() in MACRO_SUFFIXES:
        return f"its extension ({path.suffix}) is a macro-enabled Office format"
    try:
        with zipfile.ZipFile(path) as zf:
            members = [m.lower() for m in zf.namelist()]
    except (OSError, zipfile.BadZipFile):
        return ""
    found = [m for m in members if any(marker in m for marker in MACRO_MEMBERS)]
    return f"it holds {found[0]}" if found else ""


def inspect_one(path: Path, declared: str | None, cfg: Any) -> list[Finding]:
    """The findings about one file. `declared`: the format of the referential, when it is known."""
    out: list[Finding] = []
    try:
        start = head(path)
    except OSError as e:  # pragma: no cover - a file that went away between the two steps
        return [Finding(path, "type_mismatch", f"unreadable ({e})", blocking=False)]
    if not start:
        return out

    binary = looks_executable(start)
    if binary:
        out.append(Finding(path, "executable", f"this is {binary}, not data",
                           blocking=cfg.on_executable == "block"))

    actual = real_format(start)
    if actual in ("zip", "ole") or path.suffix.lower() in MACRO_SUFFIXES:
        why = has_macros(path)
        if why:
            out.append(Finding(path, "macro", f"Office file carrying a macro: {why}",
                               blocking=cfg.on_macro == "block"))

    fmt = (declared or "").lower()
    if fmt and not binary:
        mismatch = _mismatch(fmt, actual, start)
        if mismatch:
            out.append(Finding(path, "type_mismatch", mismatch, blocking=cfg.on_type_mismatch == "block"))
    return out


def _mismatch(fmt: str, actual: str, start: bytes) -> str:  # noqa: ARG001 - `start` kept for the next check
    """Why the first bytes contradict the declared format, or an empty string when they do not.

    Only the cases that are certain are reported: a compressed or packed file is normal (the import unpacks
    archives itself), so what is left is a file that is plainly something else.
    """
    if fmt in TEXT_FORMATS:
        if actual in ("pdf", "ole", "sqlite", "parquet"):
            return f"declared {fmt}, but the first bytes are those of a {actual} file"
        return ""
    if fmt in ZIP_FORMATS and actual and actual not in ("zip", "gzip"):
        return f"declared {fmt} (a zip underneath), but the first bytes are those of a {actual} file"
    if fmt == "parquet" and actual and actual != "parquet":
        return f"declared parquet, but the first bytes are those of a {actual} file"
    return ""


def inspect(paths: Iterable[Path], declared: str | None, cfg: Any,
            log_line: Callable[[str], None] = lambda m: None) -> Findings:
    """Check every file and report. Nothing is deleted or moved here: the caller decides what to do."""
    out = Findings()
    for path in paths:
        if not path.is_file():
            continue
        out.checked += 1
        for finding in inspect_one(path, declared, cfg):
            out.findings.append(finding)
            log_line(finding.line() + (" — refused" if finding.blocking else " — reported"))
    if out.findings:
        log_line(out.summary())
    return out


# --------------------------------------------------------------------------- on the way out

# A value a spreadsheet would execute. A plain number is excluded: '-5' and '+12.5' are data, not formulas.
DANGEROUS_START = "=+@\t\r"
NUMBER = r"^[-+][0-9]+([.,][0-9]+)?$"
PREFIX = "'"
# Export formats a spreadsheet opens, and therefore the only ones worth neutralising
SPREADSHEET_FORMATS = frozenset({"csv", "csv.gz", "tsv", "xlsx"})


def wanted(fmt: str, settings_reader: Callable[[], tuple[bool, Any]] | None = None) -> bool:
    """Whether the export of `fmt` is neutralised, asking the settings through `settings_reader`.

    The reader is given rather than a session: this is called from the query engine and from the download
    cache, neither of which has any business knowing how settings are stored.
    """
    if fmt not in SPREADSHEET_FORMATS or settings_reader is None:
        return False
    try:
        enabled, cfg = settings_reader()
    except Exception:  # noqa: BLE001 - no database (unit usage), or a settings table that is not there yet
        return False
    return bool(enabled and getattr(cfg, "escape_exports", False))


def read_settings() -> tuple[bool, Any]:
    """(the experimental feature is on, the checks settings). Its own short session: see `wanted`."""
    from . import appsettings
    from .db import session_factory

    with session_factory()() as db:
        experimental = appsettings.load(db, "experimental")
        return bool(getattr(experimental, "file_checks", False)), appsettings.load(db, "checks")


def escape_sql(column: str) -> str:
    """SQL neutralising one text column: a value a spreadsheet would read as a formula is prefixed.

    Written as DuckDB SQL so it costs one expression inside the COPY that writes the export, instead of a
    second pass over what can be hundreds of millions of rows.
    """
    from .sqlbuild import lit, qi

    c = qi(column)
    return (f"CASE WHEN {c} IS NULL OR length({c}) = 0 THEN {c} "
            f"WHEN regexp_matches({c}, {lit(NUMBER)}) THEN {c} "
            f"WHEN strpos({lit(DANGEROUS_START)}, substr({c}, 1, 1)) > 0 THEN {lit(PREFIX)} || {c} "
            f"ELSE {c} END")


def neutralise(sql: str, columns: list[tuple[str, str]]) -> str:
    """`sql` with its text columns neutralised.

    `columns`: (name, type) as DuckDB describes them. Only the text ones are touched, so a number stays a
    number and a date stays a date in the exported file.
    """
    from .sqlbuild import qi

    text = [name for name, kind in columns if "VARCHAR" in kind.upper() or "CHAR" in kind.upper()]
    if not text:
        return sql
    replaced = ", ".join(f"{escape_sql(name)} AS {qi(name)}" for name in text)
    return f"SELECT * REPLACE ({replaced}) FROM ({sql})"


def describe(con: Any, sql: str) -> list[tuple[str, str]]:
    """(name, type) of what a query returns, asked of DuckDB rather than guessed."""
    return [(row[0], row[1]) for row in con.execute(f"DESCRIBE {sql}").fetchall()]


def export_sql(con: Any, sql: str, fmt: str, escape: bool) -> str:
    """What a download or an export actually writes: `sql`, neutralised when it has to be.

    One function for both writers (the engine and the download cache), so a CSV produced on the fly and one
    generated once and kept are never neutralised differently.
    """
    if not escape or fmt not in SPREADSHEET_FORMATS:
        return sql
    try:
        columns = describe(con, sql)
    except Exception as e:  # noqa: BLE001 - a query DuckDB cannot describe is left as it is
        log.warning("Cannot describe the export query, values are not neutralised: %s", e)
        return sql
    return neutralise(sql, columns)
