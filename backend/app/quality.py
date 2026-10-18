"""Data quality: rules checked on every new version, a score and a status.

Rules (definition, `quality.rules`), each on a column (or an SQL expression), with a threshold and a severity:
- not_null    the value is present (not NULL, not blank)
- unique      no value repeated
- format      a known format: ipv4, ipv6, ip, cidr, domain, email, url, md5, sha1, sha256, sha512, cve, uuid, mac, asn,
              country (ISO 3166 alpha-2)
- pattern     a regular expression (RE2), matching the whole value
- values      among a list of allowed values
- range       between `min` and `max` (numbers, or ISO dates / text)
- length      between `min_length` and `max_length` characters
- expression  an SQL condition true for every row (e.g. "first_seen <= last_seen")

Empty values only fail `not_null` (the other checks ignore them). A rule passes when at least `threshold` % of the rows
pass (100 by default). Severity `error`: the version is rejected and the published one is kept (a quality gate);
`warning`: the version is published and marked. When a key is declared, its completeness is checked automatically.

Score: mean of the pass rates of the checks (0-100). Status: ok, warning (a warning rule fails) or failed.
All the checks are computed in one scan of the produced Parquet file; up to 5 failing values are kept per rule
(never for a confidential referential).
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any, Literal

import duckdb
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .sqlbuild import lit, qi

_IPV4 = r"((25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])\.){3}(25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])"
_IPV6 = r"(([0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}|([0-9A-Fa-f]{1,4}:){1,7}:|([0-9A-Fa-f]{1,4}:){1,6}:[0-9A-Fa-f]{1,4}|" \
        r"([0-9A-Fa-f]{1,4}:){1,5}(:[0-9A-Fa-f]{1,4}){1,2}|([0-9A-Fa-f]{1,4}:){1,4}(:[0-9A-Fa-f]{1,4}){1,3}|" \
        r"([0-9A-Fa-f]{1,4}:){1,3}(:[0-9A-Fa-f]{1,4}){1,4}|([0-9A-Fa-f]{1,4}:){1,2}(:[0-9A-Fa-f]{1,4}){1,5}|" \
        r"[0-9A-Fa-f]{1,4}:(:[0-9A-Fa-f]{1,4}){1,6}|:((:[0-9A-Fa-f]{1,4}){1,7}|:)|::(ffff(:0{1,4})?:)?" + _IPV4 + \
        r"|([0-9A-Fa-f]{1,4}:){1,4}:" + _IPV4 + ")"
FORMATS: dict[str, str] = {
    "ipv4": _IPV4,
    "ipv6": _IPV6,
    "ip": f"({_IPV4}|{_IPV6})",
    "cidr": rf"({_IPV4}/(3[0-2]|[12]?[0-9])|{_IPV6}/(12[0-8]|1[01][0-9]|[1-9]?[0-9]))",
    "domain": r"([A-Za-z0-9_]([A-Za-z0-9_-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z][A-Za-z0-9-]{0,62}\.?",
    "email": r"[^@\s]+@[^@\s]+\.[^@\s]+",
    "url": r"[A-Za-z][A-Za-z0-9+.-]*://[^\s]+",
    "md5": r"[0-9A-Fa-f]{32}",
    "sha1": r"[0-9A-Fa-f]{40}",
    "sha256": r"[0-9A-Fa-f]{64}",
    "sha512": r"[0-9A-Fa-f]{128}",
    "cve": r"CVE-[0-9]{4}-[0-9]{4,}",
    "uuid": r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}",
    "mac": r"([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}|([0-9A-Fa-f]{4}\.){2}[0-9A-Fa-f]{4}",
    "asn": r"([Aa][Ss])?[0-9]{1,10}",
    "country": r"[A-Z]{2}",
}
Check = Literal["not_null", "unique", "format", "pattern", "values", "range", "length", "expression"]


def _bound(v: float | str | None) -> str:
    if v is None:
        return "…"
    return str(int(v)) if isinstance(v, float) and v.is_integer() else str(v)


class QualityRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, max_length=120, description="Label shown (generated when empty)")
    column: str | None = None
    check: Check
    format: Literal[tuple(FORMATS)] | None = None  # type: ignore[valid-type]
    pattern: str | None = None
    values: list[Any] = Field(default_factory=list)
    min: float | str | None = None
    max: float | str | None = None
    min_length: int | None = Field(None, ge=0)
    max_length: int | None = Field(None, ge=0)
    expression: str | None = None
    threshold: float = Field(100.0, ge=0, le=100, description="% of the rows that must pass")
    severity: Literal["warning", "error"] = "warning"

    @model_validator(mode="after")
    def _check(self) -> "QualityRule":
        if self.check == "expression":
            if not (self.expression or "").strip():
                raise ValueError("an 'expression' rule needs an SQL condition")
            if ";" in self.expression:  # type: ignore[operator]
                raise ValueError("one SQL condition expected (no ';')")
        elif not self.column:
            raise ValueError(f"a '{self.check}' rule needs a column")
        needs = {"format": self.format, "pattern": self.pattern, "values": self.values,
                 "range": self.min is not None or self.max is not None,
                 "length": self.min_length is not None or self.max_length is not None}
        if self.check in needs and not needs[self.check]:
            raise ValueError({"format": "choose a format", "pattern": "give a regular expression", "values": "give the allowed values",
                              "range": "give min and / or max", "length": "give min_length and / or max_length"}[self.check])
        if self.pattern:
            try:
                re.compile(self.pattern)
            except re.error as e:
                raise ValueError(f"invalid regular expression: {e}") from e
        return self

    @property
    def label(self) -> str:
        if self.name:
            return self.name
        col = self.column or ""
        return {
            "not_null": f"{col} is filled",
            "unique": f"{col} is unique",
            "format": f"{col} is a valid {self.format}",
            "pattern": f"{col} matches {self.pattern}",
            "values": f"{col} among {len(self.values)} allowed value(s)",
            "range": f"{col} between {_bound(self.min)} and {_bound(self.max)}",
            "length": f"{col} length between {self.min_length or 0} and {self.max_length or '…'}",
            "expression": f"{self.expression}",
        }[self.check]


class QualityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rules: list[QualityRule] = Field(default_factory=list)


def rules_hash(cfg: QualityConfig, key: str | None) -> str | None:
    if not cfg.rules and not key:
        return None
    payload = json.dumps([r.model_dump(mode="json") for r in cfg.rules] + [key], sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


# --------------------------------------------------------------------------- evaluation

def _text(col: str) -> str:
    return f"CAST({qi(col)} AS VARCHAR)"


def _failing(rule: QualityRule) -> str | None:
    """SQL condition true for the rows failing the rule (None: unique, computed by aggregate)."""
    c = qi(rule.column) if rule.column else ""
    t = _text(rule.column) if rule.column else ""
    present = f"{c} IS NOT NULL AND trim({t}) <> ''"
    if rule.check == "not_null":
        return f"NOT ({present})"
    if rule.check == "unique":
        return None
    if rule.check == "expression":
        return f"NOT coalesce(({rule.expression}), true)"
    if rule.check in ("format", "pattern"):
        regex = FORMATS[rule.format] if rule.check == "format" else rule.pattern  # type: ignore[index]
        cond = f"regexp_full_match(trim({t}), {lit(regex)})"
    elif rule.check == "values":
        cond = f"{t} IN ({', '.join(lit(str(v)) for v in rule.values)})"
    elif rule.check == "range":
        numeric = all(isinstance(v, (int, float)) for v in (rule.min, rule.max) if v is not None)
        value = f"try_cast({c} AS DOUBLE)" if numeric else t
        parts = []
        if rule.min is not None:
            parts.append(f"{value} >= {lit(rule.min)}")
        if rule.max is not None:
            parts.append(f"{value} <= {lit(rule.max)}")
        cond = " AND ".join(parts)
    else:  # length
        parts = []
        if rule.min_length is not None:
            parts.append(f"length({t}) >= {rule.min_length}")
        if rule.max_length is not None:
            parts.append(f"length({t}) <= {rule.max_length}")
        cond = " AND ".join(parts)
    return f"({present}) AND NOT coalesce({cond}, false)"


def effective_rules(cfg: QualityConfig, key: str | None) -> list[tuple[QualityRule, bool]]:
    """(rule, automatic) to check: the configured ones, plus the completeness of the key when not already a rule."""
    rules = [(r, False) for r in cfg.rules]
    if key and not any(r.column == key and r.check == "not_null" for r in cfg.rules):
        rules.append((QualityRule(column=key, check="not_null", name=f"key {key} is filled"), True))
    return rules


# Large referentials: the rules are checked on blocks of rows picked at random, about this many rows
SAMPLE_ROWS = 10_000_000


def evaluate(con: duckdb.DuckDBPyConnection, rel: str, columns: set[str], cfg: QualityConfig, key: str | None,
             examples: bool = True, large: bool = False, sample: str | None = None) -> dict[str, Any] | None:
    """Check the rules on `rel` (a reader of the Parquet file). None when there is nothing to check.

    Large referential (`large`): the rules are checked on `sample` (blocks of about SAMPLE_ROWS rows, see
    sqlbuild.parquet_sample) instead of reading billions of rows at each update, and uniqueness (a hash table of
    every value) is not checked."""
    rules = effective_rules(cfg, key)
    if not rules:
        return None
    t0 = time.monotonic()
    sampled = bool(large and sample)
    if sampled:
        rel = sample  # type: ignore[assignment]
    checks: list[dict[str, Any]] = []
    aggregates = ["count(*)"]
    plan: list[tuple[QualityRule, bool, int | None]] = []
    for rule, auto in rules:
        if rule.column and rule.column not in columns:
            checks.append({"name": rule.label, "column": rule.column, "check": rule.check, "severity": rule.severity,
                           "threshold": rule.threshold, "automatic": auto, "ok": False, "pass_pct": 0.0,
                           "error": f"unknown column '{rule.column}'"})
            continue
        if large and rule.check == "unique":
            plan.append((rule, auto, None))
            continue
        fail = _failing(rule)
        start = len(aggregates)
        if fail is None:  # unique: repeated values among the filled ones
            aggregates += [f"count({qi(rule.column)})", f"count({qi(rule.column)}) - count(DISTINCT {qi(rule.column)})"]  # type: ignore[arg-type]
        elif rule.check in ("not_null", "expression"):
            aggregates += ["count(*)", f"count(*) FILTER (WHERE {fail})"]
        else:
            aggregates += [f"count(*) FILTER (WHERE {qi(rule.column)} IS NOT NULL AND trim({_text(rule.column)}) <> '')",  # type: ignore[arg-type]
                           f"count(*) FILTER (WHERE {fail})"]
        plan.append((rule, auto, start))
    values: tuple = ()
    error = None
    if any(start is not None for _, _, start in plan):
        try:
            values = con.execute(f"SELECT {', '.join(aggregates)} FROM {rel}").fetchone() or ()
        except duckdb.InterruptException:
            raise
        except duckdb.Error as e:  # e.g. an invalid expression: every rule is reported, none applies
            error = str(e).splitlines()[0][:300]
    rows = int(values[0]) if values else None
    for rule, auto, start in plan:
        entry: dict[str, Any] = {"name": rule.label, "column": rule.column, "check": rule.check, "severity": rule.severity,
                                 "threshold": rule.threshold, "automatic": auto}
        if start is None:
            entry.update(ok=True, skipped=True, error="not checked on a large referential (every value would be held in memory)")
            checks.append(entry)
            continue
        if error or not values:
            entry.update(ok=False, pass_pct=0.0, error=error or "not evaluated")
            checks.append(entry)
            continue
        applicable, failing = int(values[start] or 0), int(values[start + 1] or 0)
        pct = 100.0 if applicable == 0 else round((applicable - failing) / applicable * 100, 3)
        entry.update(checked=applicable, failing=failing, pass_pct=pct, ok=pct >= rule.threshold)
        if failing and examples:
            entry["examples"] = _examples(con, rel, rule)
        checks.append(entry)
    scored = [c["pass_pct"] for c in checks if not c.get("skipped")]
    failed = [c for c in checks if not c["ok"]]
    status = "failed" if any(c["severity"] == "error" for c in failed) else "warning" if failed else "ok"
    return {
        "score": round(sum(scored) / len(scored), 1) if scored else None,
        "status": status,
        "rows": rows,
        "checks": checks,
        "rules_hash": rules_hash(cfg, key),
        "sampled": sampled,
        "duration": round(time.monotonic() - t0, 2),
    }


def _examples(con: duckdb.DuckDBPyConnection, rel: str, rule: QualityRule, limit: int = 5) -> list[str]:
    try:
        if rule.check == "unique":
            sql = (f"SELECT {_text(rule.column)} FROM {rel} WHERE {qi(rule.column)} IS NOT NULL "  # type: ignore[arg-type]
                   f"GROUP BY 1 HAVING count(*) > 1 LIMIT {limit}")
        elif rule.check == "expression":
            sql = f"SELECT CAST(to_json(t) AS VARCHAR) FROM {rel} t WHERE {_failing(rule)} LIMIT {limit}"
        elif rule.check == "not_null":
            return []  # nothing to show: the values are empty
        else:
            sql = f"SELECT DISTINCT {_text(rule.column)} FROM {rel} WHERE {_failing(rule)} LIMIT {limit}"  # type: ignore[arg-type]
        return [str(r[0])[:200] for r in con.execute(sql).fetchall()]
    except duckdb.InterruptException:
        raise
    except duckdb.Error:
        return []


def summary(q: dict[str, Any] | None) -> dict[str, Any] | None:
    if not q:
        return None
    failed = [c["name"] for c in q.get("checks", []) if not c.get("ok")]
    return {"score": q.get("score"), "status": q.get("status"), "failed": failed[:5], "checked_at": q.get("checked_at")}
