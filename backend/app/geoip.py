"""Read-only access to MaxMind DB files (.mmdb): GeoIP2 / GeoLite2 (City, Country, ASN...), DB-IP, IPinfo...

The file is published as is, so that tools (Logstash, Suricata, Zeek, Graylog, nginx/geoip2...) fetch it raw,
and RefExposer answers IP lookups through its API (the network containing the address and its record).
"""

from __future__ import annotations

import ipaddress
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import maxminddb

# Metadata marker of the MaxMind DB format, near the end of every file
MARKER = b"\xab\xcd\xefMaxMind.com"


class MmdbError(ValueError):
    pass


def is_mmdb(path: Path) -> bool:
    """MaxMind DB signature: the metadata marker in the last 128 KiB."""
    size = path.stat().st_size
    with path.open("rb") as f:
        f.seek(max(0, size - 128 * 1024))
        return MARKER in f.read()


class MmdbDatabase:
    def __init__(self, path: Path):
        self.path = Path(path)
        if not is_mmdb(self.path):
            raise MmdbError("not a MaxMind DB file (metadata marker missing)")
        try:
            self._reader = maxminddb.open_database(str(self.path), maxminddb.MODE_MMAP)
        except (maxminddb.InvalidDatabaseError, OSError, ValueError) as e:
            raise MmdbError(str(e)) from e
        self._lock = threading.Lock()

    def info(self) -> dict[str, Any]:
        m = self._reader.metadata()
        return {
            "database_type": m.database_type,
            "description": (m.description or {}).get("en") or next(iter((m.description or {}).values()), None),
            "build_date": datetime.fromtimestamp(m.build_epoch, tz=timezone.utc).isoformat(),
            "build_epoch": m.build_epoch,
            "ip_version": m.ip_version,
            "languages": list(m.languages or []),
            "node_count": m.node_count,
            "record_size": m.record_size,
            "format_version": f"{m.binary_format_major_version}.{m.binary_format_minor_version}",
            "size": self.path.stat().st_size,
        }

    def lookup(self, value: str) -> dict[str, Any] | None:
        """Record of the network containing the address, or None (unknown network)."""
        ip = parse_ip(value)
        with self._lock:  # the C extension is thread safe, the pure Python reader is not guaranteed to be
            record, prefix = self._reader.get_with_prefix_len(ip)
        if record is None:
            return None
        max_len = 32 if ip.version == 4 else 128
        network = ipaddress.ip_network(f"{ip}/{min(prefix, max_len)}", strict=False)
        return {"ip": str(ip), "network": str(network), **(record if isinstance(record, dict) else {"value": record})}

    def close(self) -> None:
        self._reader.close()


def flatten(record: dict[str, Any], lang: str = "en") -> dict[str, Any]:
    """One level of dotted keys for tables and CSV exports: country.iso_code, city.name, subdivisions.0.name...
    Only the `lang` translation of the `names` maps is kept (as `name`)."""
    out: dict[str, Any] = {}

    def walk(prefix: str, value: Any) -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                if k == "names" and isinstance(v, dict):
                    if lang in v:
                        out[f"{prefix}name"] = v[lang]
                    continue
                walk(f"{prefix}{k}.", v)
        elif isinstance(value, list) and value and all(isinstance(x, dict) for x in value):
            for i, v in enumerate(value):
                walk(f"{prefix}{i}.", v)
        else:
            out[prefix[:-1]] = value

    walk("", record)
    return out


def parse_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    try:
        return ipaddress.ip_address(str(value).strip().strip("[]"))
    except ValueError as e:
        raise MmdbError(f"'{value}' is not an IPv4 or IPv6 address") from e


def looks_like_ip(value: str) -> bool:
    try:
        parse_ip(value)
        return True
    except MmdbError:
        return False
