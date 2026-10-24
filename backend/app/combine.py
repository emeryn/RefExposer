"""Unified referentials: a referential built from the published data of other referentials.

Two ways of combining them (``combine.mode``):

- ``union``: the rows of every referential are put together, columns matched by name (a column missing from one of
  them is empty for its rows). Two teams publishing the same kind of data get one referential covering both.
- ``enrich``: the first referential gives the rows, the others add their columns, joined on a shared value (a left
  join: a row without a match keeps empty values).

The sources are read from their published Parquet files, so a unified referential is rebuilt from data that already
passed the validation and quality rules of each source.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import CombineConfig, ReferentialConfig
from .crypto import parquet_key
from .sqlbuild import apply_transform, lit, parquet_reader, qi
from .storage import RefPaths


class CombineError(ValueError):
    """A source referential is unusable (missing, never published, confidential, unknown join column)."""


@dataclass
class Part:
    """One source referential of a unified referential, as published when the build starts."""

    ref: ReferentialConfig
    path: Path
    columns: list[str]
    key: tuple[str, str] | None  # Parquet encryption key when the source is confidential
    version: int | None = None
    row_count: int | None = None
    updated_at: str | None = None

    @property
    def id(self) -> str:
        return self.ref.id

    @property
    def masked(self) -> list[str]:
        """Personal data columns of this source, which the published file still holds in clear."""
        from .pii import columns_of

        have = {c.lower() for c in self.columns}
        return [c for c in columns_of(self.ref) if c.lower() in have]

    @property
    def reader(self) -> str:
        """How the rows of this source are read. A source with masked columns is read **masked**: a unified
        referential is a publication of its own, so personal data never spreads into one by being combined.
        To keep the real values downstream, mask them on the unified referential instead of on its source."""
        from .pii import projection

        reader = parquet_reader(str(self.path), self.key[0] if self.key else None)
        mask = projection(self.ref, self.columns)
        return f"(SELECT {mask} FROM {reader})" if mask else reader


def alias(ref_id: str) -> str:
    """Prefix given to the columns a referential adds (its identifier as an SQL name)."""
    return ref_id.replace("-", "_")


def dependencies(refs: dict[str, ReferentialConfig], ref_id: str, seen: frozenset[str] = frozenset()) -> set[str]:
    """Referentials a unified referential reads, directly or through other unified referentials."""
    ref = refs.get(ref_id)
    if ref is None or not ref.is_combined or ref_id in seen:
        return set()
    out: set[str] = set()
    for source in ref.source.referentials:
        out.add(source)
        out |= dependencies(refs, source, seen | {ref_id})
    return out


def check_sources(refs: dict[str, ReferentialConfig], ref: ReferentialConfig) -> None:
    """Refuse a definition whose sources do not exist, form a cycle, or would expose confidential data."""
    missing = [r for r in ref.source.referentials if r not in refs]
    if missing:
        raise CombineError(f"unknown referential(s): {', '.join(missing)}")
    if ref.id in dependencies({**refs, ref.id: ref}, ref.id):
        raise CombineError(f"'{ref.id}' would read itself, directly or through another unified referential")
    for source in (refs[r] for r in ref.source.referentials):
        if source.is_artifact:
            raise CombineError(f"'{source.id}' is a {'Bloom filter' if source.is_bloom else 'MaxMind DB'}: "
                               "it has no rows to combine")
        if source.confidential and not ref.confidential:
            raise CombineError(f"'{source.id}' is confidential: a referential combining it must be confidential too")


def parts(settings: Any, refs: dict[str, ReferentialConfig], metas: dict[str, dict[str, Any]],
          ref: ReferentialConfig) -> list[Part]:
    """The source referentials with their published file and columns (raises CombineError when one is unusable)."""
    check_sources(refs, ref)
    out: list[Part] = []
    for ref_id in ref.source.referentials:
        source = refs[ref_id]
        path = RefPaths(settings.data_dir, ref_id).current
        if not path.exists():
            raise CombineError(f"'{ref_id}' has no published version yet: update it first")
        meta = metas.get(ref_id) or {}
        columns = [c["name"] for c in meta.get("columns") or []]
        if not columns:
            raise CombineError(f"the columns of '{ref_id}' are unknown: update it first")
        out.append(Part(source, path, columns, parquet_key(ref_id) if source.confidential else None,
                        meta.get("version"), meta.get("row_count"), meta.get("data_updated_at")))
    return out


def _wanted(cfg: CombineConfig, part: Part, exclude: str | None = None) -> list[str]:
    chosen = cfg.columns.get(part.id)
    if chosen:
        unknown = [c for c in chosen if c not in part.columns]
        if unknown:
            raise CombineError(f"'{part.id}' has no column(s) {', '.join(unknown)} (columns: {', '.join(part.columns)})")
        return [c for c in chosen if c != exclude]
    return [c for c in part.columns if c != exclude]


def _union_sql(cfg: CombineConfig, items: list[Part]) -> str:
    selects = []
    for part in items:
        projection = ", ".join(qi(c) for c in _wanted(cfg, part))
        origin = f"{lit(part.id)} AS {qi(cfg.source_column)}, " if cfg.source_column else ""
        selects.append(f"SELECT {origin}{projection} FROM {part.reader}")
    # BY NAME: the same column keeps its place whatever the order of the columns of each referential, and a column
    # only one of them has is empty for the rows of the others
    return "(" + " UNION ALL BY NAME ".join(selects) + ")"


def _enrich_sql(cfg: CombineConfig, key: str | None, items: list[Part]) -> str:
    base, others = items[0], items[1:]
    on = cfg.on or key
    if not on:
        raise CombineError("enrichment joins the referentials on a column: set 'on', or the key of the referential")
    if on not in base.columns:
        raise CombineError(f"'{base.id}' has no column '{on}' (columns: {', '.join(base.columns)})")
    added: list[str] = []
    joins: list[str] = []
    names = set(base.columns)
    for i, part in enumerate(others):
        their = cfg.join_on.get(part.id, on)
        if their not in part.columns:
            raise CombineError(f"'{part.id}' has no column '{their}' to join on (columns: {', '.join(part.columns)})")
        table = f"j{i}"
        for column in _wanted(cfg, part, exclude=their):
            name = f"{alias(part.id)}_{column}" if cfg.prefix else column
            if name in names:
                raise CombineError(f"column '{name}' of '{part.id}' already exists: switch the prefix on, "
                                   "or choose the columns to add")
            names.add(name)
            added.append(f"{qi(table)}.{qi(column)} AS {qi(name)}")
        joins.append(f"LEFT JOIN {part.reader} AS {qi(table)} ON {qi(table)}.{qi(their)} = {qi('base')}.{qi(on)}")
    projection = ", ".join([f'{qi("base")}.*', *added])
    return f"(SELECT {projection} FROM {base.reader} AS {qi('base')} " + " ".join(joins) + ")"


def build_sql(ref: ReferentialConfig, items: list[Part]) -> str:
    """The SELECT producing the rows of a unified referential, transformation applied."""
    cfg = ref.combine or CombineConfig()
    combined = _enrich_sql(cfg, ref.key, items) if cfg.mode == "enrich" else _union_sql(cfg, items)
    if not ref.transform:
        return f"SELECT * FROM {combined}"
    groups = [[str(p.path)] for p in items]
    return apply_transform(ref.transform, lambda i: combined if i is None else items[i].reader, groups)


PLACEHOLDER = re.compile(r"\{(?:source|files)\d*\}")


def transform_from_sql(sql: str, names: list[list[str]]) -> str:
    """Turn a query written in the SQL console into the transformation of a unified referential.

    The query names the referentials by their table, as the console exposes them. Each of those names becomes a
    CTE over the published file of its referential, so the query runs unchanged every time the referential is
    rebuilt — and keeps reading the data its sources have just published.
    """
    body = sql.strip().rstrip(";").strip()
    if not body:
        raise CombineError("the query is empty")
    if PLACEHOLDER.search(body):
        raise CombineError("the query must not contain a {source} or {files} placeholder: they are what binds "
                           "the referentials to the transformation")
    if not any(names):
        raise CombineError("the query reads no referential")
    head, keyword = "WITH ", re.match(r"(?is)^with\s+(recursive\s+)?", body)
    if keyword:
        # The query brings its own CTEs: ours are put in front of theirs, in the same WITH
        head = "WITH RECURSIVE " if keyword.group(1) else "WITH "
        body = body[keyword.end():].lstrip()
    ctes = ", ".join(f"{qi(name)} AS (SELECT * FROM {{source{i}}})"
                     for i, group in enumerate(names) for name in group)
    return f"{head}{ctes}{',' if keyword else ''}\n{body}"


def describe(items: list[Part]) -> list[dict[str, Any]]:
    """Source entries of the run: which version of each referential the data was built from."""
    return [{
        "url": None,
        "referential": p.id,
        "name": p.ref.name,
        "version": p.version,
        "row_count": p.row_count,
        "data_updated_at": p.updated_at,
        "masked_columns": p.masked or None,
        "files": [],
    } for p in items]
