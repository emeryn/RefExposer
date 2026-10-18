"""Memory and processors given to DuckDB, sized on what the container really has (cgroup limits).

Left to itself, DuckDB takes 80 % of the memory of the machine and every processor, in each connection: the
ingestion of a source of tens of GB (sort of billions of rows) then starves the API, the database and the host,
and the kernel kills the service when out of memory. Without REFEX_DUCKDB_MEMORY_LIMIT / REFEX_DUCKDB_THREADS,
each update gets a share of the memory (it spills to disk beyond: slower, never killed) and leaves processors free."""

from __future__ import annotations

import os
import shutil
import re
from pathlib import Path

from .settings import Settings

_GB = 1 << 30


def total_memory() -> int:
    """Memory of the container (cgroup v2 / v1 limit), else of the machine, in bytes."""
    for f in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            v = Path(f).read_text().strip()
            if v.isdigit() and int(v) < 1 << 60:
                return int(v)
        except OSError:
            pass
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) * 1024
    except OSError:
        pass
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        return 8 * _GB


def cpu_count() -> int:
    """Processors of the container (cgroup v2 cpu.max quota), else of the machine."""
    try:
        quota, period = Path("/sys/fs/cgroup/cpu.max").read_text().split()
        if quota != "max":
            return max(1, int(int(quota) / int(period)))
    except (OSError, ValueError):
        pass
    try:
        return max(1, len(os.sched_getaffinity(0)))
    except AttributeError:
        return os.cpu_count() or 1


def _size(n: int) -> str:
    return f"{max(n, 512 << 20) // (1 << 20)}MB"


def ingest_limits(settings: Settings) -> tuple[str, int]:
    """(memory_limit, threads) of an update connection: 60 % of the memory shared between the updates that may run
    at once, and half of the processors (the API, the database and the host stay responsive during a long sort)."""
    memory = settings.duckdb_memory_limit or _size(int(total_memory() * 0.6) // max(1, settings.max_concurrent_jobs))
    threads = settings.duckdb_threads or max(1, cpu_count() // 2)
    return memory, threads


def engine_limits(settings: Settings) -> tuple[str, int]:
    """(memory_limit, threads) of the query engine (API, SQL console): 25 % of the memory."""
    memory = settings.duckdb_memory_limit or _size(int(total_memory() * 0.25))
    threads = settings.duckdb_threads or max(1, cpu_count() - 1)
    return memory, threads


_UNITS = {"": 1, "B": 1, "K": 1 << 10, "KB": 1 << 10, "KIB": 1 << 10, "M": 1 << 20, "MB": 1 << 20, "MIB": 1 << 20,
          "G": 1 << 30, "GB": 1 << 30, "GIB": 1 << 30, "T": 1 << 40, "TB": 1 << 40, "TIB": 1 << 40}


def parse_size(value: str) -> int | None:
    """'8GB', '1126MB', '4 GiB' -> bytes; None when unreadable."""
    m = re.fullmatch(r"\s*([\d.]+)\s*([A-Za-z]*)\s*", value or "")
    if not m or m.group(2).upper() not in _UNITS:
        return None
    return int(float(m.group(1)) * _UNITS[m.group(2).upper()])


def sqlite_cache_kib(settings: Settings) -> int:
    """Page cache of SQLite while a delta is applied (NIST NSRL: millions of inserts in an index of tens of GB):
    the memory of an update, between 256 MB and 2 GB."""
    memory = parse_size(ingest_limits(settings)[0]) or (256 << 20)
    return max(256 << 10, min(2 << 20, memory // 1024))


# --------------------------------------------------------------------------- disk

class DiskFull(Exception):
    """Writing more would eat into the disk reserve (REFEX_MIN_FREE_DISK)."""


def min_free_disk(settings: Settings) -> int:
    return parse_size(settings.min_free_disk) or 0


def keep_raw_max(settings: Settings) -> int:
    """Largest source kept after the build when it is not the published file; 0: no limit."""
    return parse_size(settings.keep_raw_max) or 0


def free_disk(path: Path) -> int:
    """Free bytes of the file system holding `path` (its nearest existing parent)."""
    p = Path(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    try:
        return shutil.disk_usage(p).free
    except OSError:
        return 1 << 62


def fmt_gb(n: int) -> str:
    return f"{n / (1 << 30):.1f} GB"


class DiskGuard:
    """Checks the free space while a large file is written (every `every` bytes): stops before the reserve is reached
    instead of filling the disk."""

    def __init__(self, settings: Settings, path: Path, what: str, every: int = 256 << 20):
        self.reserve, self.path, self.what, self.every = min_free_disk(settings), path, what, every
        self._since = 0

    def check(self) -> None:
        free = free_disk(self.path)
        if self.reserve and free < self.reserve:
            raise DiskFull(f"{self.what}: disk almost full ({fmt_gb(free)} free, reserve {fmt_gb(self.reserve)}, "
                           "REFEX_MIN_FREE_DISK): stopped before filling it. Free some space (or a bigger volume for data/)")

    def wrote(self, n: int) -> None:
        self._since += n
        if self._since >= self.every:
            self._since = 0
            self.check()


def temp_limit(settings: Settings, path: Path) -> str | None:
    """max_temp_directory_size of DuckDB: what its spill files may take without eating into the reserve."""
    room = free_disk(path) - min_free_disk(settings)
    return f"{max(room, 1 << 30) // (1 << 20)}MB" if room < (1 << 62) else None
