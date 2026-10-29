"""Relational search: start from one value and find everything the catalogue knows that is tied to it.

A referential answers "is this value in here?". An investigation answers the question behind it: *what does
this value touch?* Searching a CVE should name the referentials that carry it, the products it affects, the
exploits and the techniques tied to it — and say where each of those statements comes from.

This is a **superset of the global search**, not a narrower mode beside it. There are two ways in, and both
run by default (`match: auto`):

- **Exact**, when the value is a recognised identifier ([entities.py](entities.py)): `CVE-2024-3400` is a
  `cve`, and every column of the catalogue holding that kind of value is looked up for it. The column names
  differ from one source to the next (`cveID`, `cve`, `id`), which is exactly why the type and not the name
  drives this.
- **Text**, for anything at all: the text of every referential in scope is searched the way the global search
  searches it, on the very same `TableQuery`. A product name, a vendor, a binary, a label — what is not an
  identifier is never refused, and the identifiers sitting in the matching rows become the first hop. Free
  text is a door into the graph.

From there it is one mechanism: each matching row is **evidence** — it puts the value in that referential, and
every *other* typed column of that row is a neighbour (a CPE, an address, a URL, a hash). Those neighbours are
looked up in turn, up to `depth`, which is what turns a flat multi-referential search into a graph. Past the
seed the walk follows identifiers **exactly**: text searching every value found along the way would cost a
full pass per value and drown the graph in coincidences.

**Several values at once.** `values` takes as many seeds as you like, and then the interesting question is not
what each one touches but *what ties them together*. Every node remembers which seeds reached it, so a node
reached from more than one is a **meeting point**, and the shortest path from each seed to it is the link —
"this CVE and that address meet in the advisory that names both". `connections` reports those links, shortest
first; `shared` reports every meeting point. Two seeds only meet if their expansions overlap, so a depth of 1
often finds nothing and the answer says so rather than implying there is no link.

What comes out is a graph of three kinds of node — the values (`entity`), the free text that was asked about
(`query`) and the referentials that evidence them (`source`) — plus the rows themselves, so the interface can
show the figures behind every edge.

**Rights and personal data are not re-implemented here.** Only the referentials this account may read are
visited, and the data is read through the same masked views as everywhere else: an investigation never shows a
personal data column in clear, whatever the account may do on `/rows`. A **confidential** referential is read
only if the account may read it, and it contributes nothing to the automatic typing because its profiling
keeps no values.

Every walk is bounded — nodes, rows per lookup, queries, and wall clock — because the fan-out of a popular
value is unbounded by nature. What a bound cut off is reported rather than silently dropped, so a partial
answer never passes for a complete one.
"""

from __future__ import annotations

import logging
import re
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Any, Iterable

from . import entities
from .auth import CurrentUser
from .config import ReferentialConfig
from .sqlbuild import lit, prefix_range, qi, text_expr

log = logging.getLogger(__name__)

# A walk is bounded on every axis: a value such as a popular domain is in a great many rows, and an
# investigation has to come back with something rather than read the catalogue through.
MAX_DEPTH = 3
MAX_NODES = 400
MAX_ROWS_PER_LOOKUP = 50
MAX_QUERIES = 600
BUDGET_SECONDS = 25.0
# Columns of one referential looked up for a single value: a wide table typed on many columns would
# otherwise cost one query per column
MAX_COLUMNS_PER_REF = 6
# Text matches counted per referential, as the global search counts them (referentials.SEARCH_CAP): past
# that the figure is reported as "more than", rather than reading the rest of the file to be exact
TEXT_CAP = 1000
# Seeds in one investigation. Every seed costs its own passes, and the budgets are shared between them
MAX_SEEDS = 8
# Meeting points reported in `shared`: past that the list stops being read
MAX_SHARED = 40


# --------------------------------------------------------------------------- the typed catalogue

@dataclass(frozen=True)
class TypedColumn:
    """One column of one referential, and the kind of identifier it holds."""

    ref_id: str
    ref_name: str
    category: str
    column: str
    ctype: str
    entity: str
    # The definition says so (`entities` in the configuration), rather than the values of the column
    declared: bool
    # A lookup here only reads the relevant blocks (sorted or indexed column)
    fast: bool
    # Why this column cannot be looked up, when it cannot: a referential too large to be read whole for a
    # column that is neither sorted nor indexed
    refused: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"referential_id": self.ref_id, "referential_name": self.ref_name, "category": self.category,
                "column": self.column, "type": self.ctype, "entity": self.entity,
                "declared": self.declared, "fast": self.fast, "refused": self.refused}


class _Cache:
    """Typed columns of a referential, computed once per published version (keyed like _ResultCache)."""

    def __init__(self, size: int = 512):
        self.size = size
        self._data: OrderedDict[tuple, list[TypedColumn]] = OrderedDict()

    def get(self, key: tuple, compute) -> list[TypedColumn]:
        if key in self._data:
            self._data.move_to_end(key)
            return self._data[key]
        value = compute()
        self._data[key] = value
        while len(self._data) > self.size:
            self._data.popitem(last=False)
        return value


_cache = _Cache()


def _columns(service: Any, ref: ReferentialConfig) -> dict[str, str]:
    cols = service.metas.get(ref.id, {}).get("columns")
    if not cols:
        cols = service.engine.describe(ref.table)
    return {c["name"]: c["type"] for c in cols}


def _fast_columns(service: Any, ref: ReferentialConfig) -> list[str]:
    """Columns a lookup can use without reading the whole file (mirrors the helper of the referentials API)."""
    cols = list(ref.storage.sort_by[:1])
    return cols + [c for c in service.engine.index_views.get(ref.table, {}) if c not in cols]


def _huge(service: Any, ref: ReferentialConfig) -> str | None:
    from .resources import fmt_gb, parse_size

    limit = parse_size(service.settings.full_scan_max) or 0
    size = int((service.metas.get(ref.id) or {}).get("parquet_size") or 0)
    return fmt_gb(size) if limit and size > limit else None


def typed_columns(service: Any, ref: ReferentialConfig) -> list[TypedColumn]:
    """Every column of one referential that holds a kind of identifier, declared or recognised."""
    meta = service.metas.get(ref.id, {})

    def compute() -> list[TypedColumn]:
        cols = _columns(service, ref)
        profile = {p["name"]: p for p in meta.get("profile") or []}
        fast = set(_fast_columns(service, ref))
        huge = _huge(service, ref)
        out: list[TypedColumn] = []
        for name, ctype in cols.items():
            declared = ref.entities.get(name)
            if declared == "":  # explicitly taken out of the relational search
                continue
            p = profile.get(name, {})
            found = entities.column_entities(name, ctype, [p.get("min"), p.get("max")], declared)
            quick = name in fast
            refused = (f"{ref.name} is too large ({huge}) to be read whole for a lookup on '{name}', which is "
                       "neither sorted nor indexed") if huge and not quick else None
            # A column holding several kinds of identifier (the rdata of a passive DNS carries addresses and
            # host names) is one entry per kind: each is looked up for its own kind of value
            for entity in found:
                out.append(TypedColumn(
                    ref_id=ref.id, ref_name=ref.name, category=ref.category, column=name, ctype=ctype,
                    entity=entity, declared=bool(declared), fast=quick, refused=refused))
        return out

    return _cache.get((ref.id, meta.get("version"), meta.get("fingerprint"), id(ref)), compute)


def catalogue(service: Any, user: CurrentUser, scope: Iterable[str] | None = None) -> list[TypedColumn]:
    """The typed columns of every referential this account may read, narrowed to `scope`.

    `scope` holds referential identifiers and `category:<name>` entries, like a grant does; empty means the
    whole readable catalogue.
    """
    wanted = {s.strip() for s in (scope or []) if s.strip()}
    cats = {s.split(":", 1)[1].lower() for s in wanted if s.startswith("category:")}
    ids = {s for s in wanted if not s.startswith("category:")}
    out: list[TypedColumn] = []
    for ref in service.refs.values():
        if not service.has_data(ref.id) or not user.can_read(ref.id) or ref.is_artifact:
            continue
        if wanted and ref.id not in ids and ref.category.lower() not in cats:
            continue
        out.extend(typed_columns(service, ref))
    return out


def where_entities(service: Any, user: CurrentUser) -> dict[str, list[dict[str, Any]]]:
    """{entity type: the columns of the catalogue that hold it} — what this account can pivot on."""
    out: dict[str, list[dict[str, Any]]] = {}
    for tc in catalogue(service, user):
        out.setdefault(tc.entity, []).append(tc.to_dict())
    return {k: sorted(v, key=lambda c: (c["referential_name"], c["column"])) for k, v in sorted(out.items())}


# --------------------------------------------------------------------------- the walk

@dataclass
class Spec:
    """What to investigate. Kept as plain data: this is what a saved search stores and replays."""

    # One or more seeds. `value` is the single-seed spelling kept for the searches saved before `values`.
    value: str = ""
    values: list[str] = field(default_factory=list)
    entity: str | None = None
    depth: int = 1
    scope: list[str] = field(default_factory=list)
    # Expansion restricted to these entity types (every type when empty)
    follow: list[str] = field(default_factory=list)
    rows_per_lookup: int = 5
    max_nodes: int = MAX_NODES
    # How the value is looked for. `auto` does both passes described in MATCH_MODES: it is what makes this a
    # superset of the global search rather than a narrower mode beside it.
    match: str = "auto"

    @property
    def seeds(self) -> list[str]:
        """The seeds, in order, without duplicates: `values` when given, else the single `value`."""
        raw = self.values or ([self.value] if self.value else [])
        return list(dict.fromkeys(v for v in (str(x or "").strip() for x in raw) if v))[:MAX_SEEDS]

    def bounded(self) -> "Spec":
        return Spec(
            value=str(self.value or "").strip(),
            values=[str(v or "").strip() for v in (self.values or []) if str(v or "").strip()][:MAX_SEEDS],
            entity=self.entity or None,
            depth=max(1, min(int(self.depth or 1), MAX_DEPTH)),
            scope=[s for s in self.scope if s.strip()][:200],
            follow=[f for f in self.follow if f in entities.BY_NAME][:40],
            rows_per_lookup=max(1, min(int(self.rows_per_lookup or 5), MAX_ROWS_PER_LOOKUP)),
            max_nodes=max(10, min(int(self.max_nodes or MAX_NODES), MAX_NODES)),
            match=self.match if self.match in MATCH_MODES else "auto",
        )


MATCH_MODES = {
    # Both passes: exact lookups wherever the value is a recognised identifier, and a text search everywhere,
    # so anything the global search finds is a way into the graph
    "auto": "exact lookups on the typed columns, and a text search of the catalogue",
    "exact": "exact lookups only, on the columns holding that kind of identifier",
    "text": "a text search only, like the global search",
}


class InvestigationError(ValueError):
    """The investigation cannot start: no value at all, or an entity type that does not exist."""


def _node_id(entity: str, value: str) -> str:
    return f"{entity}:{value}"


@dataclass
class _Walk:
    """State of one investigation, so the bounds are counted in one place."""

    spec: Spec
    deadline: float
    nodes: dict[str, dict[str, Any]] = field(default_factory=dict)
    edges: dict[tuple, dict[str, Any]] = field(default_factory=dict)
    hits: dict[str, dict[str, Any]] = field(default_factory=dict)
    # {node id: the seeds it was reached from}. A node reached from more than one seed is what ties them
    # together, which is the whole point of giving several.
    origins: dict[str, set[int]] = field(default_factory=dict)
    queries: int = 0
    warnings: list[str] = field(default_factory=list)
    truncated: bool = False

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    def out_of_budget(self) -> bool:
        if self.queries >= MAX_QUERIES:
            self.warn(f"stopped after {MAX_QUERIES} lookups: narrow the scope or the depth")
            self.truncated = True
            return True
        if time.monotonic() > self.deadline:
            self.warn(f"stopped after {BUDGET_SECONDS:.0f}s: narrow the scope or the depth")
            self.truncated = True
            return True
        return False

    def mark(self, node_id: str, seeds: Iterable[int]) -> None:
        """Record which seeds a node was reached from; provenance is inherited along the walk."""
        self.origins.setdefault(node_id, set()).update(seeds)

    def node(self, entity: str, value: str, depth: int) -> dict[str, Any] | None:
        """The node for a value, created at `depth` the first time it is seen. None once the graph is full."""
        key = _node_id(entity, value)
        found = self.nodes.get(key)
        if found is not None:
            return found
        if len(self.nodes) >= self.spec.max_nodes:
            self.warn(f"the graph was stopped at {self.spec.max_nodes} nodes: narrow the scope or the depth")
            self.truncated = True
            return None
        self.nodes[key] = {"id": key, "kind": "entity", "entity": entity, "value": value,
                           "label": value, "depth": depth, "rows": 0, "referentials": []}
        return self.nodes[key]

    def query_node(self, value: str) -> dict[str, Any]:
        """The free text itself as a node: a value that is not an identifier is still a question, and the
        referentials mentioning it hang off it exactly as they would off a typed value."""
        key = f"query:{value}"
        if key not in self.nodes:
            self.nodes[key] = {"id": key, "kind": "query", "entity": None, "value": value, "label": value,
                               "depth": 0, "rows": 0, "referentials": [], "seed": True}
        return self.nodes[key]

    def source_node(self, ref_id: str, name: str, category: str, depth: int) -> dict[str, Any]:
        """A referential is a node too: a value found nowhere else still has to show where it *is*."""
        key = f"source:{ref_id}"
        if key not in self.nodes:
            self.nodes[key] = {"id": key, "kind": "source", "entity": None, "value": ref_id,
                               "label": name, "category": category, "depth": depth, "rows": 0,
                               "referentials": [ref_id]}
        return self.nodes[key]

    def edge(self, source: str, target: str, kind: str, ref_id: str, label: str) -> None:
        key = (source, target, kind, ref_id, label)
        found = self.edges.get(key)
        if found is None:
            self.edges[key] = {"source": source, "target": target, "kind": kind,
                               "referential_id": ref_id, "label": label, "rows": 1}
        else:
            found["rows"] += 1


def _variants(entity: str, value: str) -> list[str]:
    """The spellings to look for: the data is stored as it came, the node holds a normalised form."""
    raw = str(value).strip()
    out = {raw, entities.normalize(entity, raw), raw.lower(), raw.upper()}
    kind = entities.BY_NAME.get(entity)
    if kind is not None and kind.bare:
        # A column holding the identifier without its prefix (CWE-79 stored as 79)
        stripped = re.sub(r"^[A-Za-z]+[-_]", "", raw)
        if stripped and stripped != raw:
            out.add(stripped)
    return sorted(v for v in out if v)


def _lookup_sql(service: Any, tc: TypedColumn, values: list[str], limit: int) -> str:
    ref = service.refs[tc.ref_id]
    # The sorted copy of that column when there is one: the lookup then only reads the relevant blocks
    view = service.engine.index_views.get(ref.table, {}).get(tc.column) or ref.table
    cols = _columns(service, ref)
    column = text_expr(tc.column, cols.get(tc.column, "VARCHAR"))
    exact = f"{column} IN ({', '.join(lit(v) for v in values)})"
    if tc.fast and tc.column in (ref.storage.sort_by[:1] or []):
        # A sorted column is also reachable as a range, which skips row groups on the original spelling
        exact = f"({exact} OR {prefix_range(tc.column, values[0])})" if len(values) == 1 else exact
    return f"SELECT * FROM {qi(view)} WHERE {exact} LIMIT {limit}"


def _rows(service: Any, walk: _Walk, tc: TypedColumn, values: list[str]) -> list[dict[str, Any]]:
    """Rows of one referential whose typed column carries one of `values`, as records."""
    from .engine import rows_to_records

    walk.queries += 1
    sql = _lookup_sql(service, tc, values, walk.spec.rows_per_lookup)
    try:
        names, rows = service.engine.fetch(sql)
    except Exception as e:  # noqa: BLE001 - one unusable referential must not sink the investigation
        log.info("investigation: lookup failed on %s.%s: %s", tc.ref_id, tc.column, e)
        walk.warn(f"{tc.ref_name}: lookup on '{tc.column}' failed ({type(e).__name__})")
        return []
    return rows_to_records(names, rows)


def _neighbours(service: Any, ref_id: str, record: dict[str, Any], skip: str) -> list[tuple[str, str]]:
    """(entity type, normalised value) of every other typed column of a row.

    A column holding several kinds of identifier types each value by what that value actually is, so an
    address in a passive DNS `rdata` becomes an `ip` node and a host name in the very same column a `domain`.
    """
    kinds: dict[str, list[str]] = {}
    declared: set[str] = set()
    for tc in typed_columns(service, service.refs[ref_id]):
        if tc.column == skip:
            continue
        kinds.setdefault(tc.column, []).append(tc.entity)
        if tc.declared:
            declared.add(tc.column)
    out: list[tuple[str, str]] = []
    for column, candidates in kinds.items():
        raw = record.get(column)
        if raw is None or str(raw).strip() == "":
            continue
        text = str(raw)
        # A declared type is trusted as it stands: its values may carry no prefix to recognise them by
        entity = candidates[0] if column in declared else entities.of_value(text, candidates)
        if entity is None:
            continue
        out.append((entity, entities.normalize(entity, text)))
    return out


def referentials_in_scope(service, user, scope=None) -> list:
    """Referentials this account may read, narrowed to `scope` — what a text pass searches."""
    wanted = {s.strip() for s in (scope or []) if s.strip()}
    cats = {s.split(":", 1)[1].lower() for s in wanted if s.startswith("category:")}
    ids = {s for s in wanted if not s.startswith("category:")}
    out = []
    for ref in service.refs.values():
        if not service.has_data(ref.id) or not user.can_read(ref.id) or ref.is_artifact:
            continue
        if wanted and ref.id not in ids and ref.category.lower() not in cats:
            continue
        out.append(ref)
    return out


def _text_rows(service, walk, ref, value: str) -> tuple[list[dict], int, str]:
    """Rows of one referential whose text matches `value`, the way the global search finds them.

    Built on the same TableQuery as /api/search, so what the global search finds, an investigation finds: the
    search columns of the referential (or its text columns), the prefix search on a large volume, and the
    refusal on a volume too large to be read whole all behave identically.
    """
    from .engine import rows_to_records
    from .sqlbuild import QueryError, TableQuery

    cols = _columns(service, ref)
    try:
        # An explicit `search_columns` is respected; without one, **every** text column is searched rather
        # than just the key. /api/search narrows to the key to keep a catalogue-wide fan-out cheap, which
        # would make an investigation blind to exactly what it is for: a vendor in `vendorProject`, a product
        # name, a label. The budgets below are what keeps the cost in hand here.
        tq = TableQuery(table=ref.table, columns=cols, search=value,
                        search_columns=ref.search_columns or None,
                        indexes=service.engine.index_views.get(ref.table, {}),
                        sorted_by=ref.storage.sort_by, large=service.is_large(ref.id),
                        huge=_huge(service, ref))
        where = tq.where()
        src = tq.source()
    except QueryError as e:
        # A volume too large to be scanned for this, or no searchable column: said once, not failed over
        walk.warn(f"{ref.name}: {e}")
        return [], 0, "text"
    walk.queries += 1
    quoted = qi(src)
    try:
        names, rows = service.engine.fetch(
            f"SELECT * FROM {quoted}{where} LIMIT {walk.spec.rows_per_lookup}")
        if not rows:
            return [], 0, "text"
        walk.queries += 1
        total = service.engine.scalar(
            f"SELECT count(*) FROM (SELECT 1 FROM {quoted}{where} LIMIT {TEXT_CAP + 1})") or 0
    except Exception as e:  # noqa: BLE001 - one unusable referential must not sink the investigation
        log.info("investigation: text search failed on %s: %s", ref.id, e)
        walk.warn(f"{ref.name}: text search failed ({type(e).__name__})")
        return [], 0, "text"
    if total > TEXT_CAP:
        walk.warn(f"{ref.name}: more than {TEXT_CAP} rows mention this, counted up to there")
    return rows_to_records(names, rows), min(total, TEXT_CAP), "text"


def _record_hit(walk, key: str, ref_id: str, ref_name: str, category: str, column: str,
                entity: str | None, value: str, records: list[dict], total: int) -> None:
    """Keep the rows one lookup brought back: they are what every edge of the graph is asserted on."""
    hit = walk.hits.setdefault(key, {
        "referential_id": ref_id, "referential_name": ref_name, "category": category, "column": column,
        "entity": entity, "rows": [], "row_count": 0, "values": [],
        # How the rows were found, so the interface can tell an exact match from a text mention
        "how": "text" if entity is None else "exact"})
    hit["row_count"] += total
    if value not in hit["values"]:
        hit["values"].append(value)
    for record in records:
        if len(hit["rows"]) < walk.spec.rows_per_lookup * 4:
            hit["rows"].append(record)


def _link_neighbours(service, walk, ref_id: str, records: list[dict], skip: str, origin: dict,
                     source: dict, depth: int, label: str,
                     seeds: frozenset[int] = frozenset()) -> list[tuple[str, str, int, frozenset[int]]]:
    """Turn the typed values of some rows into nodes tied to `origin`, and say which ones to expand next.

    `seeds` is which seeds this row was reached from; a neighbour inherits it, so provenance travels with the
    walk and a node reached from two seeds is recognisable at the end.
    """
    found: list[tuple[str, str, int, frozenset[int]]] = []
    for record in records:
        for n_entity, n_value in _neighbours(service, ref_id, record, skip):
            if walk.spec.follow and n_entity not in walk.spec.follow:
                continue
            if origin["kind"] == "entity" and (n_entity, n_value) == (origin["entity"], origin["value"]):
                continue
            neighbour = walk.node(n_entity, n_value, depth + 1)
            if neighbour is None:
                return found
            if ref_id not in neighbour["referentials"]:
                neighbour["referentials"].append(ref_id)
            walk.mark(neighbour["id"], seeds)
            # The two values share a row of this referential: that row is the evidence
            walk.edge(origin["id"], neighbour["id"], "same_row", ref_id, label + " \u2192 " + n_entity)
            walk.edge(neighbour["id"], source["id"], "found_in", ref_id, n_entity)
            found.append((n_entity, n_value, depth + 1, seeds))
    return found


def _expand_exact(service, walk, by_entity: dict, entity: str, value: str, depth: int,
                  seeds: frozenset[int] = frozenset()) -> list[tuple[str, str, int, frozenset[int]]]:
    """Look a typed value up in every column of the catalogue holding that kind of identifier."""
    found: list[tuple[str, str, int, frozenset[int]]] = []
    for tc in by_entity.get(entity, []):
        if tc.refused:
            walk.warn(tc.refused)
    usable = [tc for tc in by_entity.get(entity, []) if not tc.refused]
    # A table typed on many columns would cost one query per column: the sorted and indexed ones first,
    # since those are the lookups that stay cheap
    per_ref: dict[str, list[TypedColumn]] = {}
    for tc in usable:
        per_ref.setdefault(tc.ref_id, []).append(tc)
    for tcs in per_ref.values():
        tcs.sort(key=lambda c: (not c.fast, c.column))
        if len(tcs) > MAX_COLUMNS_PER_REF:
            walk.warn(f"{tcs[0].ref_name}: only {MAX_COLUMNS_PER_REF} of its {len(tcs)} "
                      f"'{entity}' columns were looked up")
        for tc in tcs[:MAX_COLUMNS_PER_REF]:
            if walk.out_of_budget():
                return found
            records = _rows(service, walk, tc, _variants(entity, value))
            if not records:
                continue
            node = walk.nodes[_node_id(entity, value)]
            node["rows"] += len(records)
            if tc.ref_id not in node["referentials"]:
                node["referentials"].append(tc.ref_id)
            source = walk.source_node(tc.ref_id, tc.ref_name, tc.category, depth)
            source["rows"] += len(records)
            walk.mark(source["id"], seeds)
            walk.edge(node["id"], source["id"], "found_in", tc.ref_id, tc.column)
            _record_hit(walk, f"{tc.ref_id}:{tc.column}", tc.ref_id, tc.ref_name, tc.category, tc.column,
                        entity, value, records, len(records))
            found += _link_neighbours(service, walk, tc.ref_id, records, tc.column, node, source, depth,
                                      tc.column, seeds)
    return found


def _expand_text(service, walk, refs: list, origin: dict, value: str, depth: int,
                 skip: frozenset[str] = frozenset(),
                 seeds: frozenset[int] = frozenset()) -> list[tuple[str, str, int, frozenset[int]]]:
    """Search the text of every referential in scope for the value, exactly as the global search does.

    This is what keeps an investigation from being narrower than the global search: a product name, a vendor,
    a binary, a label — anything that is not an identifier — still finds its rows, and the identifiers sitting
    in those rows become the first hop of the graph.

    It runs on the **seed only**. Text searching every value found along the way would cost a full pass per
    value and drown the graph in coincidences; past the seed the walk follows identifiers exactly.
    """
    found: list[tuple[str, str, int, frozenset[int]]] = []
    for ref in refs:
        if walk.out_of_budget():
            return found
        if ref.id in skip:
            # The seed was already found there by an exact lookup, which is the stronger statement about that
            # referential: searching its text for the same value would return the same rows and count them
            # twice. The text pass is here to reach the referentials a typed lookup cannot.
            continue
        records, total, column = _text_rows(service, walk, ref, value)
        if not records:
            continue
        origin["rows"] += total
        if ref.id not in origin["referentials"]:
            origin["referentials"].append(ref.id)
        source = walk.source_node(ref.id, ref.name, ref.category, depth)
        source["rows"] += total
        walk.mark(source["id"], seeds)
        walk.edge(origin["id"], source["id"], "found_in", ref.id, column)
        _record_hit(walk, f"{ref.id}:{column}", ref.id, ref.name, ref.category, column, None, value,
                    records, total)
        found += _link_neighbours(service, walk, ref.id, records, "", origin, source, depth, "text match",
                                  seeds)
    return found


def _adjacency(edges: list[dict]) -> tuple[dict[str, set[str]], dict[tuple[str, str], list[str]]]:
    """Neighbours and justification for the link analysis, over the `same_row` edges **only**.

    A chain of evidence runs through values that share a row, which is a statement about the data. Two values
    merely appearing in the same referential is not a link: every CVE is in the EPSS feed, so routing through
    a referential would tie any two CVEs together and report noise as a finding. Those `found_in` edges stay
    in the graph — they are how you see where a value lives — but they are not a path.
    """
    out: dict[str, set[str]] = {}
    why: dict[tuple[str, str], list[str]] = {}
    for edge in edges:
        if edge["kind"] != "same_row":
            continue
        a, b = edge["source"], edge["target"]
        out.setdefault(a, set()).add(b)
        out.setdefault(b, set()).add(a)
        for pair in ((a, b), (b, a)):
            if edge["referential_id"] not in why.setdefault(pair, []):
                why[pair].append(edge["referential_id"])
    return out, why


def _paths_from(start: str, adjacency: dict[str, set[str]], wanted: set[str]) -> dict[str, list[str]]:
    """Shortest path from `start` to each node of `wanted`, by breadth first search.

    Breadth first is what makes the answer meaningful: the *shortest* chain of evidence between two values is
    the one worth reading, and a longer one through the same nodes says nothing more.
    """
    previous: dict[str, str | None] = {start: None}
    queue: deque[str] = deque([start])
    found: dict[str, list[str]] = {}
    while queue and len(found) < len(wanted):
        current = queue.popleft()
        if current in wanted:
            path, step = [], current
            while step is not None:
                path.append(step)
                step = previous[step]
            found[current] = list(reversed(path))
        for neighbour in adjacency.get(current, ()):
            if neighbour not in previous:
                previous[neighbour] = current
                queue.append(neighbour)
    return found


def _connections(walk: "_Walk", seed_ids: list[str], nodes: list[dict], edges: list[dict]) -> dict[str, Any]:
    """What ties the seeds together: the values several of them reach, and the shortest chain to each.

    A **value** reached from two seeds is a meeting point, and the two shortest paths to it are the link. Only
    values count — see `_adjacency` for why sharing a referential is not a link. With one seed there is
    nothing to tie, so this is empty and the graph is read as before.
    """
    if len(seed_ids) < 2:
        return {"connections": [], "shared": []}
    by_id = {n["id"]: n for n in nodes}
    meeting = sorted(
        (node_id for node_id, seeds in walk.origins.items()
         if len(seeds) > 1 and node_id in by_id and node_id not in seed_ids
         and by_id[node_id]["kind"] == "entity"),
        # A value shared by more seeds first, then the one nearest the seeds
        key=lambda node_id: (-len(walk.origins[node_id]), by_id[node_id]["depth"], node_id))
    adjacency, why = _adjacency(edges)
    routes = {index: _paths_from(seed_id, adjacency, set(meeting))
              for index, seed_id in enumerate(seed_ids)}

    def step(node_id: str, previous: str | None) -> dict[str, Any]:
        """One hop of a chain, with the referentials whose rows justify it."""
        node = by_id[node_id]
        out = {"id": node_id, "label": node["label"], "kind": node["kind"], "entity": node["entity"]}
        if previous is not None:
            out["via"] = why.get((previous, node_id), [])
        return out

    shared = [{"id": node_id, "kind": by_id[node_id]["kind"], "entity": by_id[node_id]["entity"],
               "label": by_id[node_id]["label"], "seeds": sorted(walk.origins[node_id]),
               "referentials": by_id[node_id]["referentials"]}
              for node_id in meeting[:MAX_SHARED]]

    # One connection per pair of seeds, through the meeting point that links them most tightly
    out: list[dict[str, Any]] = []
    for left in range(len(seed_ids)):
        for right in range(left + 1, len(seed_ids)):
            best: dict[str, Any] | None = None
            for node_id in meeting:
                if not {left, right} <= walk.origins[node_id]:
                    continue
                first, second = routes[left].get(node_id), routes[right].get(node_id)
                if not first or not second:
                    continue
                hops = len(first) + len(second) - 2
                if best is None or hops < best["hops"]:
                    # The whole chain, seed to seed, reading through the meeting point
                    chain = [s for s in first + list(reversed(second[:-1])) if s in by_id]
                    best = {
                        "seeds": [left, right],
                        "through": {"id": node_id, "label": by_id[node_id]["label"],
                                    "kind": by_id[node_id]["kind"], "entity": by_id[node_id]["entity"]},
                        "hops": hops,
                        "path": [step(node, chain[i - 1] if i else None) for i, node in enumerate(chain)],
                        # Every meeting point of this pair, not only the closest one
                        "meeting_points": sum(1 for n in meeting if {left, right} <= walk.origins[n]),
                    }
            if best is not None:
                out.append(best)
    out.sort(key=lambda c: (c["hops"], c["seeds"]))
    return {"connections": out, "shared": shared}


def run(service: Any, user: CurrentUser, spec: Spec) -> dict[str, Any]:
    """Walk the catalogue from one or more values and return the graph, the evidence and what was left out.

    Two ways in, and `match: auto` uses both:

    - the value is a **recognised identifier**, and every column holding that kind of identifier is looked up
      for it exactly — precise, and index friendly;
    - the value is **anything at all**, and the text of the catalogue is searched for it the way the global
      search does. The identifiers found in the matching rows are the first hop, so free text leads *into* the
      graph instead of being refused.

    Past the seed the walk follows identifiers exactly, which is what keeps a deep walk meaningful.

    With **several seeds** the walk is the same, run from each of them, and every node remembers which seeds
    reached it: `connections` then reports what ties the seeds together and the shortest chain of evidence
    between each pair.
    """
    spec = spec.bounded()
    seeds = spec.seeds
    if not seeds:
        raise InvestigationError("give at least one value to investigate")
    if spec.entity and spec.entity not in entities.BY_NAME:
        raise InvestigationError(f"unknown entity type '{spec.entity}' "
                                 f"(available: {', '.join(sorted(entities.BY_NAME))})")

    columns = catalogue(service, user, spec.scope)
    by_entity: dict[str, list[TypedColumn]] = {}
    for tc in columns:
        by_entity.setdefault(tc.entity, []).append(tc)
    refs = referentials_in_scope(service, user, spec.scope)

    walk = _Walk(spec=spec, deadline=time.monotonic() + BUDGET_SECONDS)
    queue: deque[tuple[str, str, int, frozenset[int]]] = deque()
    described: list[dict[str, Any]] = []
    seed_ids: list[str] = []
    wants_text = spec.match in ("auto", "text")

    # Deduplicated on the **node**, not on the text: `CVE-2024-3400` and `cve-2024-3400` are one value, and
    # taking them for two would report that value as linked to itself.
    by_node: dict[str, int] = {}
    for value in seeds:
        index = len(described)
        mine = frozenset({index})
        # An explicit entity applies to every seed: it is there to override a detection, and giving one type
        # per seed would be a worse interface than simply spelling the values out properly
        kinds = [spec.entity] if spec.entity else entities.detect(value)
        typed = kinds[0] if kinds else None
        wants_exact = spec.match in ("auto", "exact") and typed is not None
        if spec.match == "exact" and typed is None:
            raise InvestigationError(
                f"'{value[:80]}' is not an identifier this instance recognises, so there is nothing to look "
                "up exactly: search it as text instead (match: auto or text)")
        if typed is not None:
            normalised = entities.normalize(typed, value)
            node = walk.node(typed, normalised, 0)
            if node is None:  # pragma: no cover - the graph cannot be full at the first seeds
                raise InvestigationError("the graph could not be started")
            node["seed"] = True
            node["seed_index"] = index
            if len(kinds) > 1:
                walk.warn(f"'{value}' could also be read as {', '.join(kinds[1:])}: "
                          f"it was investigated as a {typed}")
            if wants_exact and not by_entity.get(typed):
                walk.warn(f"no referential you may read has a '{typed}' column"
                          + (f", so '{value}' was only searched as text" if wants_text else ""))
        else:
            normalised = value
            node = walk.query_node(value)
            node["seed_index"] = index
        if node["id"] in by_node:
            # Another spelling of a value already given: one seed, walked once
            continue
        by_node[node["id"]] = index
        node["seed_index"] = index
        walk.mark(node["id"], mine)
        seed_ids.append(node["id"])
        described.append({"index": index, "entity": typed, "value": normalised, "id": node["id"],
                          "candidates": kinds, "type": entities.info(typed) if typed else None,
                          "searched": {"exact": bool(wants_exact), "text": bool(wants_text)}})
        if wants_exact:
            queue.extend(_expand_exact(service, walk, by_entity, typed, normalised, 0, mine))
        if wants_text:
            already = frozenset(h["referential_id"] for h in walk.hits.values() if h["how"] == "exact")
            queue.extend(_expand_text(service, walk, refs, node, normalised, 0, already, mine))

    # Provenance makes the frontier wider than a plain visited set: a value already seen from one seed has to
    # be walked again when a *second* seed reaches it, or the two would never be shown to meet
    seen: dict[tuple[str, str], set[int]] = {}
    while queue:
        entity, value, depth, origin_seeds = queue.popleft()
        if depth >= spec.depth or walk.out_of_budget():
            continue
        key = (entity, value)
        known = seen.setdefault(key, set())
        fresh = set(origin_seeds) - known
        if not fresh:
            continue
        known |= set(origin_seeds)
        for item in _expand_exact(service, walk, by_entity, entity, value, depth, frozenset(origin_seeds)):
            queue.append(item)

    hits = sorted(walk.hits.values(), key=lambda h: (-h["row_count"], h["referential_name"]))
    nodes = list(walk.nodes.values())
    edges = list(walk.edges.values())
    for node in nodes:  # the seeds each node was reached from, for the interface to colour them
        node["seeds"] = sorted(walk.origins.get(node["id"], ()))
    linked = _connections(walk, seed_ids, nodes, edges)
    if len(described) > 1 and not linked["connections"] and spec.depth < MAX_DEPTH and not walk.truncated:
        walk.warn(f"no link found between the {len(described)} values at depth {spec.depth}: two values only meet "
                  f"where their expansions overlap, so try a greater depth (up to {MAX_DEPTH})")
    return {
        "spec": {"value": spec.value, "values": [d["value"] for d in described], "entity": spec.entity,
                 "depth": spec.depth,
                 "scope": spec.scope, "follow": spec.follow, "rows_per_lookup": spec.rows_per_lookup,
                 "match": spec.match},
        # The first seed, kept for a caller written before several were possible
        "seed": described[0],
        "seeds": described,
        "nodes": nodes,
        "edges": edges,
        "hits": hits,
        "connections": linked["connections"],
        "shared": linked["shared"],
        "stats": {
            "seeds": len(described),
            "referentials_searched": len(refs) if wants_text else len(
                {tc.ref_id for tc in columns if tc.entity in {d["entity"] for d in described}}),
            "referentials_matched": len({h["referential_id"] for h in hits}),
            "columns_searched": len(columns),
            "entities": len([n for n in nodes if n["kind"] == "entity"]),
            "sources": len([n for n in nodes if n["kind"] == "source"]),
            "edges": len(edges),
            "rows": sum(h["row_count"] for h in hits),
            "queries": walk.queries,
            "connections": len(linked["connections"]),
            "shared": len(linked["shared"]),
            "truncated": walk.truncated,
        },
        "warnings": walk.warnings,
    }
