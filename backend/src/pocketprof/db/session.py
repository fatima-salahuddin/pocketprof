"""Engine and session handling. PostgreSQL in Compose; SQLite (WAL) in development."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ..config import get_settings

_engine: Engine | None = None
_Session: sessionmaker[Session] | None = None


def init_engine(url: str | None = None) -> Engine:
    global _engine, _Session
    url = url or get_settings().sqlalchemy_url
    if url.startswith("sqlite"):
        engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(conn, _):
            cur = conn.cursor()
            cur.execute("PRAGMA foreign_keys = ON")
            cur.execute("PRAGMA journal_mode = WAL")
            cur.execute("PRAGMA busy_timeout = 30000")
            cur.close()
    else:
        engine = create_engine(url, pool_pre_ping=True, pool_size=10, max_overflow=10)
    _engine = engine
    _Session = sessionmaker(engine, expire_on_commit=False)
    return engine


def engine() -> Engine:
    return _engine or init_engine()


def _factory() -> sessionmaker[Session]:
    if _Session is None:
        init_engine()
    assert _Session is not None
    return _Session


@contextmanager
def session_scope() -> Iterator[Session]:
    """Unit of work for background jobs: commit on success, roll back on error."""
    s = _factory()()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request."""
    s = _factory()()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
