"""NIST NSRL RDSv3 publications (https://www.nist.gov/itl/csd/secure-systems-and-applications/national-software-reference-library-nsrl).

A full SQLite database is published once a year for each set (modern, legacy, android, ios, and their minimal
variants), then a delta every quarter. Each delta is a SQL file chained to the previous release: it ends with
DELETE FROM VERSION WHERE version='<previous>' and INSERT INTO VERSION ... VALUES('<new>', ...), so it only applies
to a database at the previous release. This module finds the publications on the NIST download page, reads the
versions of a database and of a delta, and holds the conversion to the RDS 2.x text files (NSRLFile.txt...)
described by NIST in "RDSv3 to RDS 2.XX Format Text Files"."""

from __future__ import annotations

import html
import re
import sqlite3
import time
from pathlib import Path
from typing import Callable
from urllib.parse import urljoin, urlparse

import httpx

DOWNLOAD_PAGE = "https://www.nist.gov/itl/csd/secure-systems-and-applications/national-software-reference-library-nsrl/nsrl-download-0"
SETS = ("modern", "modern_minimal", "legacy", "legacy_minimal", "android", "android_minimal", "ios", "ios_minimal")

# RDS 2.x text files, built from the tables of the database as NIST does it (double quotes removed from names).
# {FILE}, {PKG}... are the tables read, {source} the column of the publication of each row (NSRL mode, see read_sql)
CONVERSIONS: dict[str, str] = {
    "NSRLFile": """SELECT sha1 AS "SHA-1", md5 AS "MD5", crc32 AS "CRC32", replace(file_name, '"', '') AS "FileName",
        file_size AS "FileSize", package_id AS "ProductCode", '0' AS "OpSystemCode", '' AS "SpecialCode"{source} FROM {FILE}""",
    "NSRLProd": """SELECT package_id AS "ProductCode", replace(name, '"', '') AS "ProductName",
        replace(version, '"', '') AS "ProductVersion", operating_system_id AS "OpSystemCode", manufacturer_id AS "MfgCode",
        language AS "Language", application_type AS "ApplicationType"{source} FROM {PKG}""",
    "NSRLMfg": """SELECT manufacturer_id AS "MfgCode", replace(name, '"', '') AS "MfgName"{source} FROM {MFG}""",
    "NSRLOS": """SELECT operating_system_id AS "OpSystemCode", replace(name, '"', '') AS "OpSystemName",
        replace(version, '"', '') AS "OpSystemVersion", manufacturer_id AS "MfgCode"{source} FROM {OS}""",
}

# Publication each row of the kept database comes from: the delta (.sql) that inserted it. The rows without entry come
# from the full database itself. Kept in the database, next to the NIST tables (rowid of the row in its table).
ORIGIN_TABLE = "REFEX_ORIGIN"
TRACKED = ("FILE", "PKG", "OS", "MFG")
SOURCE_COLUMN = "source_file"  # "SourceFile" in the RDS 2.x conversions

# A publication listed on the download page (.../RDS_2026.03.1_modern_minimal.zip), not the page itself
_PUBLICATION = re.compile(r"/RDS_\d{4}\.\d{2}\.\d+_[a-z_]+\.zip([?#].*)?$", re.I)
PAGE_MAX_BYTES = 20 << 20  # the download page is a few hundred KB: a larger answer is not the page

_DELTA_FROM = re.compile(r"DELETE\s+FROM\s+VERSION\s+WHERE\s+version\s*=\s*'([^']+)'", re.I)
_DELTA_TO = re.compile(r"INSERT\s+INTO\s+VERSION\s*\([^)]*\)\s*VALUES\s*\(\s*'([^']+)'", re.I)


class NsrlError(Exception):
    pass


def version_key(version: str) -> tuple[int, ...]:
    """'2026.09.1' -> (2026, 9, 1), for ordering releases."""
    return tuple(int(p) for p in re.findall(r"\d+", version))


def discover(client: httpx.Client, page_url: str, nsrl_set: str, headers: dict[str, str] | None = None,
             retries: int = 5, log: Callable[[str, str], None] | None = None) -> tuple[dict[str, str], dict[str, str]]:
    """Publications of a set listed on the download page: ({version: full URL}, {version: delta URL}). The page is
    asked again when the site fails, like a download (see ingest.download). Links may be absolute or relative."""
    from .ingest import RETRY_STATUSES, retry_wait

    if _PUBLICATION.search(page_url):
        raise NsrlError(f"{page_url} is a NSRL publication, not the NIST download page ({DOWNLOAD_PAGE})")
    attempt = 0
    while True:
        try:
            with client.stream("GET", page_url, headers={"User-Agent": "Mozilla/5.0 (RefExposer)", **(headers or {})},
                               follow_redirects=True) as r:
                r.raise_for_status()
                ctype = r.headers.get("content-type", "").lower()
                if ctype and "html" not in ctype and not ctype.startswith("text/"):
                    raise NsrlError(f"{page_url} is not a web page ({ctype}): give the NIST download page ({DOWNLOAD_PAGE})")
                body = bytearray()
                for chunk in r.iter_bytes():
                    body += chunk
                    if len(body) > PAGE_MAX_BYTES:  # e.g. a database of tens of GB: never read in memory
                        raise NsrlError(f"{page_url} is not the NIST download page (more than {PAGE_MAX_BYTES >> 20} MB)")
                text, base_url = body.decode(r.encoding or "utf-8", "replace"), str(r.url)
            break
        except httpx.HTTPError as e:
            status = e.response.status_code if isinstance(e, httpx.HTTPStatusError) else None
            attempt += 1
            if attempt > retries or (status is not None and status not in RETRY_STATUSES):
                raise NsrlError(f"NSRL download page unavailable ({page_url}): {e}") from e
            wait = retry_wait(attempt, e.response.headers.get("retry-after") if isinstance(e, httpx.HTTPStatusError) else None)
            if log:
                log(f"NSRL download page unavailable ({e}): new attempt {attempt + 1}/{retries + 1} in {wait:.0f}s", "warn")
            time.sleep(wait)
    fulls: dict[str, str] = {}
    deltas: dict[str, str] = {}
    name = re.compile(r"/RDS_(\d{4}\.\d{2}\.\d+)_" + re.escape(nsrl_set) + r"(_delta)?\.zip$", re.I)
    for href in re.findall(r"""href\s*=\s*["']([^"']+?\.zip)(?:[?#][^"']*)?["']""", text, re.I):
        url = urljoin(base_url, html.unescape(href.strip()))
        m = name.search(urlparse(url).path)
        if m:
            (deltas if m.group(2) else fulls).setdefault(m.group(1), url)
    if not fulls:
        raise NsrlError(f"no full '{nsrl_set}' publication found on {page_url} (the page may have changed)")
    return fulls, deltas


def plan(fulls: dict[str, str], deltas: dict[str, str], current: str | None) -> tuple[str | None, list[tuple[str, str]]]:
    """What to download: (full release to load or None to keep the current database, [(version, delta URL)...] to
    apply in order). A delta of release V brings the previous release to V: the deltas newer than the database are
    applied; the delta published with a full release is not applied to that full release."""
    base = current
    full = None
    if base is None:
        full = max(fulls, key=version_key)
        base = full
    return full, [(v, deltas[v]) for v in sorted(deltas, key=version_key) if version_key(v) > version_key(base)]


def db_version(db: Path) -> str | None:
    """Release of a NSRL database (VERSION table), None for another database."""
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            rows = [r[0] for r in con.execute("SELECT version FROM VERSION").fetchall()]
        finally:
            con.close()
    except sqlite3.Error:
        return None
    return max(rows, key=version_key) if rows else None


def delta_versions(sql_file: Path) -> tuple[str | None, str | None]:
    """(release the delta applies to, release it brings) read at the end of a NSRL delta; (None, None) otherwise."""
    with sql_file.open("rb") as f:
        f.seek(0, 2)
        f.seek(max(0, f.tell() - 65536))
        tail = f.read().decode("utf-8", "replace")
    before, after = _DELTA_FROM.findall(tail), _DELTA_TO.findall(tail)
    return (before[-1] if before else None), (after[-1] if after else None)


def is_publication(url: str) -> bool:
    """URL of a NSRL publication (full database or delta zip) rather than of the download page."""
    return bool(_PUBLICATION.search(url))


def ensure_origin(con: sqlite3.Connection) -> None:
    """Table of the publication of the rows inserted by the deltas (see ORIGIN_TABLE)."""
    con.execute(f"CREATE TABLE IF NOT EXISTS {ORIGIN_TABLE} (tbl TEXT NOT NULL, rid INTEGER NOT NULL, source TEXT NOT NULL, "
                "PRIMARY KEY (tbl, rid)) WITHOUT ROWID")


def track_origin(con: sqlite3.Connection, source: str) -> None:
    """Record `source` as the publication of the rows the next statements insert (until the connection closes).
    TEMP triggers: nothing is added to the schema of the NIST tables. A deleted row loses its entry, so a rowid
    taken again by a later insert gets the publication of that insert."""
    ensure_origin(con)
    tables = {r[0] for r in con.execute("SELECT name FROM main.sqlite_master WHERE type = 'table'")}
    quoted = source.replace("'", "''")
    for t in TRACKED:
        if t not in tables:
            continue
        con.execute(f"DROP TRIGGER IF EXISTS temp.refex_origin_{t}_i")
        con.execute(f"DROP TRIGGER IF EXISTS temp.refex_origin_{t}_d")
        con.execute(f"CREATE TEMP TRIGGER refex_origin_{t}_i AFTER INSERT ON main.\"{t}\" BEGIN "
                    f"INSERT OR REPLACE INTO {ORIGIN_TABLE} VALUES ('{t}', new.rowid, '{quoted}'); END")
        con.execute(f"CREATE TEMP TRIGGER refex_origin_{t}_d AFTER DELETE ON main.\"{t}\" BEGIN "
                    f"DELETE FROM {ORIGIN_TABLE} WHERE tbl = '{t}' AND rid = old.rowid; END")


def read_sql(db: str, table: str, base_name: str | None = None) -> str | None:
    """SELECT reading a NIST table (FILE, PKG, OS, MFG) or a RDS 2.x conversion (NSRLFile...) of the attached database
    `db` (quoted DuckDB alias). With `base_name` (NSRL mode: kept database holding ORIGIN_TABLE), a last column gives
    the publication of each row: the delta that inserted it, else the full database `base_name`. None for another table."""
    def source(t: str) -> str:
        if base_name is None:
            return f"{db}.\"{t}\""
        fallback = "'" + base_name.replace("'", "''") + "'"
        return (f"(SELECT t.*, coalesce(o.source, {fallback}) AS {SOURCE_COLUMN} FROM {db}.\"{t}\" t "
                f"LEFT JOIN {db}.{ORIGIN_TABLE} o ON o.tbl = '{t}' AND o.rid = t.rowid) AS \"{t}\"")

    if table in CONVERSIONS:
        return CONVERSIONS[table].format(source=f', {SOURCE_COLUMN} AS "SourceFile"' if base_name else "",
                                         **{t: source(t) for t in TRACKED})
    if table in TRACKED and base_name is not None:
        return "SELECT * FROM " + source(table)
    return None
