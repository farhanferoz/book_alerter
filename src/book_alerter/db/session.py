from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, create_engine

_DEFAULT_URL = "sqlite:///./data/book_alerter.db"


def get_database_url() -> str:
    return os.environ.get("BOOK_ALERTER_DATABASE_URL", _DEFAULT_URL)


def _enable_foreign_keys(dbapi_conn, _connection_record) -> None:
    """Turn on FK enforcement for every SQLite connection.

    SQLite ships with `PRAGMA foreign_keys=OFF` by default — the FK
    constraints declared in the schema (including the ON DELETE CASCADE
    relationships set up in migration 0013) are SILENT unless this pragma
    is on. We want it on everywhere: live app, tests, scripts. Cheap to
    apply, no on-disk impact, so no need to skip `:memory:`.
    """
    cur = dbapi_conn.cursor()
    try:
        cur.execute("PRAGMA foreign_keys=ON")
    finally:
        cur.close()


def _configure_sqlite_disk_connection(dbapi_conn, _connection_record) -> None:
    """Apply on-disk-only PRAGMAs to every new SQLite connection.

    - ``journal_mode=WAL`` lets readers (dashboard GETs) proceed while a
      scraper writes — without it, the default DELETE journal serializes
      every reader behind the writer's exclusive lock and the UI stalls
      whenever a scrape commits.
    - ``synchronous=NORMAL`` is the recommended pairing with WAL — durability
      is unchanged across crashes inside a transaction, only the
      durability-vs-fsync trade-off at the journal level changes.
    - ``busy_timeout=5000`` makes the rare write/write contention block for
      up to 5 s rather than failing fast with ``database is locked`` — the
      schedulers' per-source lock means real contention is bounded to
      backup VACUUM + scrape writes.
    - ``temp_store=MEMORY`` keeps the percentile-scan sort tmps off disk.

    Skipped for ``:memory:`` URLs (no on-disk journal anyway). FK
    enforcement is set up separately, regardless of URL.
    """
    cur = dbapi_conn.cursor()
    try:
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.execute("PRAGMA temp_store=MEMORY")
    finally:
        cur.close()


def get_engine(url: str | None = None) -> Engine:
    resolved = url if url is not None else get_database_url()
    engine = create_engine(
        resolved,
        echo=False,
        connect_args={"check_same_thread": False},
    )
    if resolved.startswith("sqlite"):
        event.listen(engine, "connect", _enable_foreign_keys)
        if ":memory:" not in resolved:
            event.listen(engine, "connect", _configure_sqlite_disk_connection)
    return engine


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    session = Session(engine)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
