"""Entity types: what a value *is*, so the same thing can be recognised across referentials.

The catalogue names the same identifier differently from one source to the next — a CVE is `cveID` in the CISA
KEV, `cve` in the EPSS scores, `id` in the NVD feed and in the GitHub advisories. Matching columns by name
therefore finds nothing, and declaring every pair by hand would mean configuring the whole catalogue before
seeing a single relation.

So a column is typed by **what its values look like**: `CVE-2024-3400` is a `cve` wherever it is stored. The
detection costs nothing, because it reads the `min` and `max` the profiling already recorded for every column
(ingest._profile) — two real values of that column, no scan. A column whose two ends both match a pattern
holds that kind of value; the name of the column only breaks ties and types the columns whose values carry no
prefix of their own (a `cwe` column holding `79` rather than `CWE-79`).

`entities` in a definition overrides all of it, for the column a pattern gets wrong or cannot see:

    entities:
      affected_product: cpe
      weakness: cwe

A **confidential** referential is never typed: its profiling keeps no values at all, which is the point, and
nothing here tries to read its data to make up for that.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class EntityType:
    """One kind of identifier the application recognises in a value."""

    name: str
    label: str
    description: str
    # Matched against the whole value, case-insensitively
    pattern: re.Pattern[str]
    # Fragments of a column name that point at this type: they break a tie between two patterns, and type a
    # column whose values carry no prefix (`bare`)
    hints: tuple[str, ...] = ()
    # How two spellings of the same thing are made one: a graph node is its normalised value
    case: str = "keep"
    # The values may come without the prefix the pattern expects (CWE-79 stored as 79), so such a column is
    # typed by its name alone — never by its values, which say nothing
    bare: re.Pattern[str] | None = None
    # Shown first in the interface: the identifiers an investigation usually starts from
    pivot: bool = True

    def matches(self, value: str) -> bool:
        return bool(self.pattern.fullmatch(value.strip()))

    def matches_bare(self, value: str) -> bool:
        return bool(self.bare and self.bare.fullmatch(value.strip()))


def _re(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


# Ordered: the first type whose pattern matches a value wins, so the specific ones come before the general
# ones (a CIDR before an IP, a URL before a domain)
TYPES: tuple[EntityType, ...] = (
    EntityType("cve", "CVE", "Vulnerability identifier of the CVE program",
               _re(r"CVE-\d{4}-\d{4,7}"), ("cve", "vulnerability", "vuln"), "upper"),
    EntityType("cwe", "CWE", "Weakness class of the CWE catalogue",
               _re(r"CWE-\d{1,5}"), ("cwe", "weakness"), "upper", bare=_re(r"\d{1,5}")),
    EntityType("capec", "CAPEC", "Attack pattern of the CAPEC catalogue",
               _re(r"CAPEC-\d{1,5}"), ("capec",), "upper", bare=_re(r"\d{1,5}")),
    EntityType("attack_technique", "ATT&CK", "Technique or sub-technique of MITRE ATT&CK",
               _re(r"T\d{4}(\.\d{3})?"), ("technique", "attack", "ttp"), "upper"),
    EntityType("cpe", "CPE", "Platform name identifying a vendor, a product and a version",
               _re(r"cpe:(2\.3:|/)[^\s]{3,}"), ("cpe", "platform"), "lower"),
    EntityType("cidr", "Network", "Network in CIDR notation",
               _re(r"[0-9a-f.:]{2,45}/\d{1,3}"), ("cidr", "prefix", "network", "range", "subnet"), "lower"),
    EntityType("ip", "IP address", "IPv4 or IPv6 address",
               _re(r"(\d{1,3}\.){3}\d{1,3}|[0-9a-f]{0,4}(:[0-9a-f]{0,4}){2,7}"),
               ("ip", "addr", "host"), "lower"),
    EntityType("asn", "AS number", "Autonomous system",
               _re(r"AS\d{1,10}"), ("asn", "as_number", "autonomous"), "upper", bare=_re(r"\d{1,10}")),
    EntityType("sha256", "SHA-256", "SHA-256 hash", _re(r"[0-9a-f]{64}"), ("sha256", "sha_256"), "lower"),
    EntityType("sha1", "SHA-1", "SHA-1 hash", _re(r"[0-9a-f]{40}"), ("sha1", "sha_1"), "lower"),
    EntityType("md5", "MD5", "MD5 hash", _re(r"[0-9a-f]{32}"), ("md5",), "lower"),
    EntityType("email", "E-mail", "E-mail address",
               _re(r"[^@\s]{1,64}@[a-z0-9.-]{3,253}\.[a-z]{2,24}"), ("email", "mail"), "lower"),
    EntityType("url", "URL", "http(s) address", _re(r"https?://[^\s]{3,}"), ("url", "link", "reference"), "keep"),
    EntityType("domain", "Domain", "Domain or host name",
               _re(r"(?=.{4,253}$)([a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?\.)+[a-z]{2,24}"),
               ("domain", "fqdn", "hostname", "site"), "lower"),
    EntityType("mac", "MAC address", "Hardware address",
               _re(r"([0-9a-f]{2}[:-]){5}[0-9a-f]{2}"), ("mac", "hwaddr"), "lower", pivot=False),
)

BY_NAME: dict[str, EntityType] = {t.name: t for t in TYPES}


def normalize(entity: str, value: str) -> str:
    """The value as a graph node holds it: two spellings of one thing have to land on the same node."""
    kind = BY_NAME.get(entity)
    text = str(value).strip()
    if kind is None:
        return text
    if entity in ("ip", "cidr"):
        try:  # 192.168.001.1 and 2001:DB8::0:1 have one canonical form each
            return str(ipaddress.ip_network(text, strict=False) if entity == "cidr" else ipaddress.ip_address(text))
        except ValueError:
            return text.lower()
    if kind.case == "upper":
        return text.upper()
    if kind.case == "lower":
        return text.lower()
    return text


def detect(value: str) -> list[str]:
    """Types a value could be, most specific first. Empty for something that is not an identifier."""
    text = str(value or "").strip()
    if not text or len(text) > 2048:
        return []
    found = [t.name for t in TYPES if t.matches(text)]
    if "ip" in found:  # a bare number is not an address, and the loose IPv6 pattern accepts odd things
        try:
            ipaddress.ip_address(text)
        except ValueError:
            found.remove("ip")
    if "cidr" in found:
        try:
            ipaddress.ip_network(text, strict=False)
        except ValueError:
            found.remove("cidr")
    return found


def _hinted(column: str) -> list[str]:
    """Types the name of a column points at, longest hint first (so `sha256` wins over `sha`).

    Deduplicated: several hints of one type can match the same name (`owner_email` matches both `email` and
    `mail`), and the type has to be named once.
    """
    name = re.sub(r"[^a-z0-9]+", "_", column.lower())
    scored = sorted(((len(h), t.name) for t in TYPES for h in t.hints if h in name), reverse=True)
    return list(dict.fromkeys(entity for _, entity in scored))


# A column is never claimed for more than this many types: past that it is free text that happens to match
MAX_TYPES_PER_COLUMN = 3


def column_entities(column: str, ctype: str, sample: Iterable[Any], declared: str | None = None) -> list[str]:
    """The kinds of identifier a column holds: declared, else read from its sample values, else from its name.

    `sample` is the handful of real values the profiling kept (the min and the max of the column). A column can
    legitimately hold **several** kinds — the `rdata` of a passive DNS carries addresses and host names side by
    side — so every type one of the samples matches is kept.

    What guards against claiming a column of free text is the other half of the rule: every sample has to be
    matched by one of the kept types. A column whose min is a sentence and whose max happens to look like a
    domain is left alone, because the sentence is covered by nothing.
    """
    if declared is not None:
        return [declared] if declared in BY_NAME else []
    values = [str(v).strip() for v in sample if v is not None and str(v).strip()]
    hints = _hinted(column)
    if values:
        matching = [t for t in TYPES if any(t.matches(v) for v in values)]
        if matching and all(any(t.matches(v) for t in matching) for v in values):
            names = [t.name for t in matching]
            # The name of the column comes first when it points at one of them: a 32 hex character string is
            # an MD5 or just an identifier, and only the name can say which
            ordered = [n for n in hints if n in names] + [n for n in names if n not in hints]
            return list(dict.fromkeys(ordered))[:MAX_TYPES_PER_COLUMN]
        # Values without the prefix of their type: only the name of the column can say what they are
        for name in hints:
            kind = BY_NAME[name]
            if kind.bare and all(kind.matches_bare(v) for v in values):
                return [name]
    # No sample at all (a column the profiling never covered): the name alone is too weak to claim a type,
    # except for a text column one of whose words *is* the name of a type
    if not values and ctype.upper().startswith(("VARCHAR", "CHAR", "TEXT", "STRING")):
        words = {w for w in re.split(r"[^a-z0-9]+", column.lower()) if w}
        for name in hints:
            if name in words:
                return [name]
    return []


def of_value(value: str, among: Iterable[str]) -> str | None:
    """Which of a column's types a single value actually is, so a mixed column types each row correctly."""
    candidates = list(among)
    if len(candidates) == 1:
        return candidates[0]
    found = detect(value)
    for name in candidates:
        if name in found:
            return name
    return None


def info(entity: str) -> dict[str, Any]:
    kind = BY_NAME[entity]
    return {"name": kind.name, "label": kind.label, "description": kind.description, "pivot": kind.pivot}
