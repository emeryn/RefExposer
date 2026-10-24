"""Column-level masking of personal data, with an explicit opt-in per user.

A referential names the columns holding personal data (`pii.columns`) and how they are served
(`pii.strategy`). Masking is applied **where the data is read**, not where it is stored: the DuckDB view
of the referential selects the masked expression instead of the column, so every read goes through it —
rows, lookups, exports, facets, statistics, the global search, the generated downloads, the SQL console,
the MCP servers and the assistant. Nothing has to remember to mask.

Seeing the real values takes two things, and both are needed:

1. the right: the account is named in `pii.reveal_users`, belongs to one of `pii.reveal_groups`, or is an
   administrator;
2. the opt-in: the request asks for it (`reveal=true`), which is audited. Masked is always the default, so
   a tool, a script, a download or the assistant never gets personal data by accident.

The published Parquet file keeps the real values: masking is a serving rule, not an encryption. For data
that must not be readable on disk either, make the referential confidential as well.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Iterable

from .sqlbuild import lit, qi

if TYPE_CHECKING:  # pragma: no cover
    from .auth import CurrentUser
    from .config import ReferentialConfig

# Shown instead of a value too short to keep a hint of
HIDDEN = "***"
# Suffix of the unmasked view kept beside the masked one, in the main database only (never in a sandbox)
RAW_SUFFIX = "__pii"
COLUMN_NAME = re.compile(r"^[^\x00-\x1f\"]{1,200}$")


def _salt() -> str:
    """Salt of the pseudonyms: derived from the instance secret, so the same value gives the same pseudonym
    here and nowhere else (a rainbow table of e-mail addresses does not match)."""
    from .crypto import derive

    return derive("pii-pseudonyms").hex()


def expression(column: str, strategy: str) -> str:
    """SQL expression serving `column` masked. The result is text whatever the column held: a masked column
    is not a number or a date any more, and saying so is better than serving a plausible wrong value."""
    c = qi(column)
    text = f"CAST({c} AS VARCHAR)"
    if strategy == "hide":
        return f"CASE WHEN {c} IS NULL THEN NULL ELSE {lit(HIDDEN)} END"
    if strategy == "hash":
        # Stable pseudonym: rows can still be counted, grouped and joined on it without the value
        return f"CASE WHEN {c} IS NULL THEN NULL ELSE substr(sha256({lit(_salt())} || {text}), 1, 16) END"
    # partial: enough to recognise a value you already know, not enough to learn it
    return (f"CASE WHEN {c} IS NULL THEN NULL WHEN length({text}) < 4 THEN {lit(HIDDEN)} "
            f"ELSE substr({text}, 1, 1) || {lit(HIDDEN)} || substr({text}, length({text}), 1) END")


def projection(ref: "ReferentialConfig", available: Iterable[str] | None = None) -> str | None:
    """`* REPLACE (...)` serving every masked column of the referential, or None when it has none.

    `available`: the columns the published version actually has. A configured column that is not among them
    is left out — DuckDB refuses a REPLACE of a column that does not exist, which would take the whole
    referential offline. Those columns are reported as `missing` in the interface instead, so a renamed
    source column is seen rather than silently unmasked.
    """
    cfg = ref.pii
    if not cfg or not cfg.columns:
        return None
    wanted = list(cfg.columns)
    if available is not None:
        actual = {c.lower(): c for c in available}
        wanted = [actual[c.lower()] for c in wanted if c.lower() in actual]
    if not wanted:
        return None
    parts = [f"{expression(c, cfg.strategy)} AS {qi(c)}" for c in wanted]
    return "* REPLACE (" + ", ".join(parts) + ")"


def columns_of(ref: "ReferentialConfig") -> list[str]:
    return list(ref.pii.columns) if ref.pii else []


def missing(ref: "ReferentialConfig", columns: Iterable[str]) -> list[str]:
    """Configured columns the published version does not have (renamed source, typo)."""
    have = {c.lower() for c in columns}
    return [c for c in columns_of(ref) if c.lower() not in have]


def may_reveal(ref: "ReferentialConfig", user: "CurrentUser") -> bool:
    """Whether this account is entitled to ask for the real values.

    It still has to ask: `reveal=true` on the request. An agent call never asks for it, so the model only
    ever sees masked values.
    """
    cfg = ref.pii
    if not cfg or not cfg.columns:
        return True  # nothing is masked
    if user.via in ("mcp", "agent"):
        # The MCP servers and the assistant act for a tool or a model, which cannot give the consent
        return False
    if user.is_admin:
        return True
    return user.id in cfg.reveal_users or bool(set(cfg.reveal_groups) & set(user.groups))


def revealed(service, user: "CurrentUser", wanted: Iterable[str] | None = None) -> frozenset[str]:
    """Tables whose masking is lifted for this request: the referentials with masked columns that the
    account may reveal (restricted to `wanted` referential ids when given)."""
    ids = None if wanted is None else set(wanted)
    return frozenset(
        r.table for r in service.refs.values()
        if columns_of(r) and (ids is None or r.id in ids) and user.can_read(r.id) and may_reveal(r, user)
    )


def state(ref: "ReferentialConfig", user: "CurrentUser | None", columns: Iterable[str] | None = None) -> dict | None:
    """What the interface shows about the masking of a referential, or None when nothing is masked."""
    cfg = ref.pii
    if not cfg or not cfg.columns:
        return None
    return {
        "columns": list(cfg.columns),
        "strategy": cfg.strategy,
        "note": cfg.note,
        "can_reveal": bool(user and may_reveal(ref, user)),
        "missing": missing(ref, columns or []),
    }
