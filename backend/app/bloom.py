"""Read-only access to Bloom filters in the DCSO format (github.com/DCSO/bloom).

Used for instance by CIRCL hashlookup (hashlookup-full.bloom): membership tests of file
hashes without sending them to a third party. A Bloom filter answers "definitely absent"
or "probably present" (false positive rate p chosen when the filter was built).

File layout (little-endian): flags u64 | n u64 | p f64 | k u64 | m u64 | N u64 | bits (ceil(m/64) u64) | optional data
Bit positions: FNV-1 64 of the value, then a multiplicative sequence modulo a large prime,
exactly as in the Go implementation (64-bit products wrap around).
"""

from __future__ import annotations

import math
import mmap
import struct
import threading
from pathlib import Path
from typing import Any

HEADER_SIZE = 48
_MASK = (1 << 64) - 1
_FNV_OFFSET = 14695981039346656037
_FNV_PRIME = 1099511628211
_M = 18446744073709551557  # largest prime below 2^64
_G = 18446744073709550147


class BloomError(ValueError):
    pass


def fnv1_64(data: bytes) -> int:
    h = _FNV_OFFSET
    for b in data:
        h = (h * _FNV_PRIME) & _MASK
        h ^= b
    return h


class BloomFilter:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._file = self.path.open("rb")
        size = self.path.stat().st_size
        if size < HEADER_SIZE:
            raise BloomError("file too short for a Bloom filter")
        self._mm = mmap.mmap(self._file.fileno(), 0, access=mmap.ACCESS_READ)
        self.flags, self.n = struct.unpack_from("<QQ", self._mm, 0)
        (self.p,) = struct.unpack_from("<d", self._mm, 16)
        self.k, self.m, self.count = struct.unpack_from("<QQQ", self._mm, 24)
        if not (self.flags & 1):
            raise BloomError(f"unknown format version (flags {self.flags:#x})")
        if not (0 < self.k <= 64 and self.m > 0):
            raise BloomError("invalid Bloom filter header")
        self.words = math.ceil(self.m / 64)
        if size < HEADER_SIZE + self.words * 8:
            raise BloomError("truncated file: incomplete bit array")
        self.size = size
        self._lock = threading.Lock()

    def close(self) -> None:
        self._mm.close()
        self._file.close()

    def positions(self, value: bytes) -> list[int]:
        hn = fnv1_64(value) % _M
        out = []
        for _ in range(self.k):
            hn = ((hn * _G) & _MASK) % _M
            out.append(hn % self.m)
        return out

    def check(self, value: str) -> bool:
        mm = self._mm
        for pos in self.positions(value.encode()):
            (word,) = struct.unpack_from("<Q", mm, HEADER_SIZE + (pos >> 6) * 8)
            if not (word >> (pos & 63)) & 1:
                return False
        return True

    @property
    def estimated_fp_rate(self) -> float:
        """False positive probability for the actual number of inserted elements."""
        if not self.count:
            return 0.0
        return (1 - math.exp(-self.k * self.count / self.m)) ** self.k

    def info(self) -> dict[str, Any]:
        return {
            "capacity": self.n,
            "target_fp_rate": self.p,
            "hash_functions": self.k,
            "bits": self.m,
            "elements": self.count,
            "estimated_fp_rate": self.estimated_fp_rate,
            "size": self.size,
        }


def normalize(value: str, mode: str) -> str:
    value = value.strip()
    if mode == "upper":
        return value.upper()
    if mode == "lower":
        return value.lower()
    return value


def write_filter(path: Path, values: list[str], p: float = 0.001) -> None:
    """Build a filter in the same format (tests and small internal lists)."""
    n = max(len(values), 1)
    m = math.ceil(-n * math.log(p) / (math.log(2) ** 2))
    k = max(1, round(m / n * math.log(2)))
    words = [0] * math.ceil(m / 64)

    class _Tmp:
        pass

    tmp = _Tmp()
    tmp.k, tmp.m = k, m
    for v in values:
        for pos in BloomFilter.positions(tmp, v.encode()):  # type: ignore[arg-type]
            words[pos >> 6] |= 1 << (pos & 63)
    with Path(path).open("wb") as f:
        f.write(struct.pack("<QQdQQQ", 1, n, p, k, m, len(values)))
        f.write(struct.pack(f"<{len(words)}Q", *words))
