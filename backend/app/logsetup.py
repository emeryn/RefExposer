"""Logging configuration: level of the container output (REFEX_LOG_LEVEL) and the syslog connector.

Levels: trace < debug < info < warning < error < critical. `trace` adds the generated SQL queries and
the HTTP / scheduler library details to `debug`.
"""

from __future__ import annotations

import logging
import sys

TRACE = 5
logging.addLevelName(TRACE, "TRACE")

LEVELS = {
    "trace": TRACE,
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
}
FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
# Libraries that are only verbose at the trace level
NOISY = ("httpx", "httpcore", "apscheduler", "multipart", "urllib3")
# Uvicorn installs its own handlers on these loggers (they do not propagate to the root logger)
UVICORN = ("uvicorn", "uvicorn.error", "uvicorn.access")

_stdout_level = logging.INFO
_extra: logging.Handler | None = None  # syslog handler, see syslog.install()


class _OwnHandler(logging.StreamHandler):
    """Container output (stdout)."""


def level_value(name: str) -> int:
    return LEVELS[name.lower()]


def setup(level: str) -> None:
    """Install the container output at `level` (called once at startup)."""
    global _stdout_level
    _stdout_level = level_value(level)
    root = logging.getLogger()
    for h in list(root.handlers):
        if isinstance(h, _OwnHandler) or type(h) is logging.StreamHandler:
            root.removeHandler(h)
    handler = _OwnHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(FORMAT))
    root.addHandler(handler)
    apply()


def set_extra_handler(handler: logging.Handler | None, level: int | None) -> None:
    """Attach (or remove, with None) the syslog handler, which wants the records from `level` up."""
    global _extra
    targets = [logging.getLogger(), *(logging.getLogger(n) for n in ("uvicorn", "uvicorn.access"))]
    if _extra is not None:
        for lg in targets:
            lg.removeHandler(_extra)
    _extra = handler
    if handler is not None:
        handler._wanted_level = level  # type: ignore[attr-defined]
        for lg in targets:
            lg.addHandler(handler)
    apply()


def apply() -> None:
    """Logger levels: low enough for every destination; each output then filters on its own level."""
    wanted = [_stdout_level]
    if _extra is not None and getattr(_extra, "_wanted_level", None) is not None:
        wanted.append(_extra._wanted_level)  # type: ignore[attr-defined]
    lowest = min(wanted)
    root = logging.getLogger()
    root.setLevel(lowest)
    for h in root.handlers:
        if isinstance(h, _OwnHandler):
            h.setLevel(_stdout_level)
    for name in NOISY:
        logging.getLogger(name).setLevel(logging.DEBUG if lowest <= TRACE else logging.WARNING)
    for name in UVICORN:
        lg = logging.getLogger(name)
        lg.setLevel(lowest)
        for h in lg.handlers:
            if h is not _extra:
                h.setLevel(_stdout_level)
    # The audit trail always reaches the syslog connector, whatever the level of the container output
    logging.getLogger("refexposer.audit").setLevel(min(logging.INFO, lowest))


def stdout_level_name() -> str:
    return next((k for k, v in LEVELS.items() if v == _stdout_level), "info")
