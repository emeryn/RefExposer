"""Database engine, sessions and schema migrations (Alembic)."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

log = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def _sqlite_pragmas(dbapi_conn, _record) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA journal_mode=WAL")
    cur.close()


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})
        event.listen(engine, "connect", _sqlite_pragmas)
        return engine
    return create_engine(url, pool_pre_ping=True, pool_size=10, max_overflow=20)


def alembic_config(url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def init_db(url: str) -> Engine:
    """Create the engine and bring the schema up to date."""
    global _engine, _SessionLocal
    if url.startswith("sqlite:///"):
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    _engine = make_engine(url)
    with _engine.begin() as conn:
        cfg = alembic_config(url)
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    log.info("Database ready (%s)", _engine.url.render_as_string(hide_password=True))
    return _engine


def dispose_db() -> None:
    if _engine is not None:
        _engine.dispose()


def session_factory() -> sessionmaker[Session]:
    if _SessionLocal is None:
        raise RuntimeError("database not initialised")
    return _SessionLocal


def get_db() -> Iterator[Session]:
    db = session_factory()()
    try:
        yield db
    finally:
        db.close()
