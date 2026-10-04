"""Conversion of sources DuckDB cannot read directly into JSON Lines (read with read_json).

- JSON objects whose values are the records: ``records_path`` with a ``*`` segment (``*``, ``executables.*``). Each value
  (or each item of a value that is a list) becomes a row, the object key goes to the ``records_key`` column.
- XML documents: ``records_path`` names the repeated element holding one record (detected when absent). Attributes and
  child elements become columns; repeated children become lists, mixed content (XHTML) becomes plain text.

Converted files are cached (keyed on path, size, mtime and options) so preview, import and transformations share them.
"""
from __future__ import annotations

import hashlib
import logging
import re
import tempfile
import time
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Any, Iterator

import orjson

log = logging.getLogger(__name__)

CACHE_DIR = Path(tempfile.gettempdir()) / "refexposer-records"
CACHE_MAX_AGE = 2 * 86400
DEFAULT_KEY = "_key"
_XHTML = {"p", "div", "br", "li", "ul", "ol", "b", "i", "em", "strong", "code", "pre", "span", "a", "table", "tr", "td", "th", "sub", "sup"}


class ConvertError(ValueError):
    pass


def needs_conversion(fmt: str, records_path: str | None) -> bool:
    return fmt == "xml" or (fmt == "json" and bool(records_path) and "*" in str(records_path).split("."))


# Several JSON documents above this total size are split into records one file at a time (bounded memory)
SPLIT_JSON_BYTES = 256 * 1024 * 1024


def needs_split(fmt: str, records_path: str | None, files: list[str]) -> bool:
    if fmt != "json" or not records_path or len(files) < 2:
        return False
    return sum(Path(f).stat().st_size for f in files) > SPLIT_JSON_BYTES


def split_records(path: Path, records_path: str) -> Path:
    """Extract the records array at ``records_path`` of one JSON document to JSON Lines with DuckDB (cached)."""
    import duckdb

    from .sqlbuild import lit, qi

    st = path.stat()
    sig = f"split|{path.resolve()}|{st.st_size}|{st.st_mtime_ns}|{records_path}"
    out = CACHE_DIR / f"{hashlib.sha1(sig.encode()).hexdigest()}.jsonl"
    if out.exists():
        return out
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cleanup()
    tmp = out.with_suffix(".part.jsonl")
    expr = ".".join(qi(p) for p in records_path.split("."))
    con = duckdb.connect()
    try:
        con.execute("SET preserve_insertion_order = false")
        con.execute(f"SET temp_directory = {lit(str(CACHE_DIR / 'duckdb'))}")
        con.execute(f"COPY (SELECT unnest({expr}) AS r FROM read_json({lit(str(path))}, maximum_object_size={1 << 31})) "
                    f"TO {lit(str(tmp))} (FORMAT json)")
    finally:
        con.close()
    tmp.replace(out)
    log.info("Extracted the records of %s (%s) to JSON Lines", path.name, records_path)
    return out


def to_jsonl(path: Path, fmt: str, records_path: str | None, key_column: str | None = None) -> Path:
    """Return a JSON Lines file with the records of ``path`` (converted once, then cached)."""
    st = path.stat()
    sig = f"{path.resolve()}|{st.st_size}|{st.st_mtime_ns}|{fmt}|{records_path}|{key_column}"
    out = CACHE_DIR / f"{hashlib.sha1(sig.encode()).hexdigest()}.jsonl"
    if out.exists():
        return out
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cleanup()
    tmp = out.with_suffix(".part")
    count = 0
    with tmp.open("wb") as f:
        records = _json_records(path, str(records_path), key_column or DEFAULT_KEY) if fmt == "json" else _xml_records(path, records_path)
        for rec in records:
            f.write(orjson.dumps(rec))
            f.write(b"\n")
            count += 1
    if not count:
        tmp.unlink(missing_ok=True)
        raise ConvertError(f"no record found in {path.name} at '{records_path or 'auto'}'")
    tmp.replace(out)
    log.info("Converted %s to %d JSON records (%s)", path.name, count, records_path or "auto")
    return out


def _cleanup() -> None:
    limit = time.time() - CACHE_MAX_AGE
    for f in CACHE_DIR.glob("*"):
        try:
            if f.stat().st_mtime < limit:
                f.unlink()
        except OSError:
            pass


# --------------------------------------------------------------------------- JSON

def _json_records(path: Path, records_path: str, key_column: str) -> Iterator[dict[str, Any]]:
    try:
        doc = orjson.loads(path.read_bytes())
    except orjson.JSONDecodeError as e:
        raise ConvertError(f"{path.name} is not a valid JSON document: {e}") from e
    yield from _walk(doc, [p for p in records_path.split(".") if p], [], key_column)


def _walk(node: Any, parts: list[str], keys: list[str], key_column: str) -> Iterator[dict[str, Any]]:
    if not parts:
        items = node if isinstance(node, list) else [node]
        for item in items:
            rec = item if isinstance(item, dict) else {"value": item}
            if keys:
                rec = {key_column: "/".join(keys), **{k: v for k, v in rec.items() if k != key_column}}
            yield rec
        return
    head, rest = parts[0], parts[1:]
    if head == "*":
        if isinstance(node, dict):
            for k, v in node.items():
                yield from _walk(v, rest, [*keys, str(k)], key_column)
        elif isinstance(node, list):
            for v in node:
                yield from _walk(v, rest, keys, key_column)
    elif isinstance(node, dict) and head in node:
        yield from _walk(node[head], rest, keys, key_column)


def json_candidates(path: Path, max_bytes: int = 64 * 1024 * 1024) -> list[str]:
    """Paths of record collections in a JSON document: arrays of objects, and objects of records (``path.*``)."""
    if path.stat().st_size > max_bytes:
        return []
    try:
        doc = orjson.loads(path.read_bytes())
    except orjson.JSONDecodeError:
        return []
    found: list[tuple[int, str]] = []

    def visit(node: Any, prefix: str, depth: int) -> None:
        if depth > 3:
            return
        if isinstance(node, list):
            dicts = sum(isinstance(v, dict) for v in node[:200])
            if prefix and node and dicts >= 0.8 * min(len(node), 200):
                found.append((len(node), prefix))
            return
        if not isinstance(node, dict):
            return
        values = list(node.values())
        recordish = sum(isinstance(v, dict) or (isinstance(v, list) and v and isinstance(v[0], dict)) for v in values[:500])
        if len(values) >= 10 and recordish >= 0.8 * min(len(values), 500):
            found.append((len(values), f"{prefix}.*" if prefix else "*"))
            return
        for k, v in node.items():
            if re.fullmatch(r"[A-Za-z_][\w-]*", str(k)):
                visit(v, f"{prefix}.{k}" if prefix else str(k), depth + 1)

    visit(doc, "", 0)
    return [p for _, p in sorted(found, key=lambda x: -x[0])][:10]


# --------------------------------------------------------------------------- XML

def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def xml_candidates(path: Path) -> list[tuple[str, int]]:
    """Repeated elements near the root, likeliest records first: [(tag, count)].

    Records are usually both frequent and rich: candidates are ranked on count × (1 + average number of attributes
    and child elements), so that <Weakness> wins over the more frequent but thin <Reference> of the CWE catalog."""
    counts: Counter[tuple[int, str]] = Counter()
    fields: Counter[tuple[int, str]] = Counter()
    depth = 0
    try:
        for event, el in ET.iterparse(path, events=("start", "end")):
            if event == "start":
                depth += 1
                if depth in (2, 3):
                    counts[(depth, _local(el.tag))] += 1
            else:
                if depth in (2, 3):
                    fields[(depth, _local(el.tag))] += len(el) + len(el.attrib)
                depth -= 1
                if depth >= 2:
                    el.clear()
    except ET.ParseError as e:
        raise ConvertError(f"{path.name} is not a valid XML document: {e}") from e
    best: dict[str, tuple[float, int]] = {}
    for key, n in counts.items():
        if n > 1:
            score = n * (1 + fields[key] / n)
            if score > best.get(key[1], (0, 0))[0]:
                best[key[1]] = (score, n)
    return [(tag, n) for tag, (_, n) in sorted(best.items(), key=lambda x: -x[1][0])]


def _xml_records(path: Path, records_path: str | None) -> Iterator[dict[str, Any]]:
    tag = records_path
    if not tag:
        cands = xml_candidates(path)
        if not cands:
            raise ConvertError(f"no repeated element in {path.name}: set records_path to the record element")
        tag = cands[0][0]
    inside = 0
    try:
        for event, el in ET.iterparse(path, events=("start", "end")):
            if _local(el.tag) != tag:
                continue
            if event == "start":
                inside += 1
            else:
                inside -= 1
                if inside == 0:  # outermost record element only
                    value = _element(el)
                    yield value if isinstance(value, dict) else {"value": value}
                    el.clear()
    except ET.ParseError as e:
        raise ConvertError(f"{path.name} is not a valid XML document: {e}") from e


def _text(el: ET.Element) -> str | None:
    text = re.sub(r"\s+", " ", "".join(el.itertext())).strip()
    return text or None


def _element(el: ET.Element) -> Any:
    attrs = {_local(k): v for k, v in el.attrib.items()}
    children = list(el)
    mixed = bool(children) and ((el.text or "").strip() != "" or all(_local(c.tag) in _XHTML for c in children))
    if not children or mixed:
        text = _text(el)
        return {**attrs, "text": text} if attrs else text
    repeated = {t for t, n in Counter(_local(c.tag) for c in children).items() if n > 1}
    out: dict[str, Any] = dict(attrs)
    for child in children:
        tag = _local(child.tag)
        name = f"{tag}_element" if tag in attrs else tag  # an attribute and a child element may share a name
        value = _element(child)
        if tag in repeated:
            out.setdefault(name, []).append(value)
        else:
            out[name] = value
    return out
