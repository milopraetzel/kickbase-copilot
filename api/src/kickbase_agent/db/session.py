"""SQLAlchemy engine and session factory."""

from __future__ import annotations

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from kickbase_agent.config import get_settings


class Base(DeclarativeBase):
    pass


#: SQLite-Schreibsperren (Review-Fund 23.08.): Snapshot (22:05) haelt eine
#: Schreibtransaktion ueber viele Netzaufrufe; ohne WAL + busy_timeout blockiert das
#: API-, Intent- und Audit-Writes ("database is locked"). WAL erlaubt Leser neben
#: einem Schreiber, busy_timeout laesst Schreiber bis 30 s warten statt zu scheitern.
SQLITE_BUSY_TIMEOUT_S = 30


def build_engine():
    url = get_settings().database_url
    if url.startswith("sqlite"):
        engine = create_engine(
            url,
            future=True,
            pool_pre_ping=True,
            connect_args={"timeout": SQLITE_BUSY_TIMEOUT_S},
        )

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            try:
                if ":memory:" not in url:
                    cur.execute("PRAGMA journal_mode=WAL")
                cur.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_S * 1000}")
                cur.execute("PRAGMA synchronous=NORMAL")
            finally:
                cur.close()

        return engine
    return create_engine(url, future=True, pool_pre_ping=True)


_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        _engine = build_engine()
    return _engine


class _LazySessionLocal:
    def __call__(self, **kwargs):
        return sessionmaker(bind=_get_engine(), autoflush=False, autocommit=False)(**kwargs)


SessionLocal = _LazySessionLocal()
