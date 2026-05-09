from __future__ import annotations

import os
from contextlib import contextmanager
from collections.abc import Iterator

from sqlalchemy.engine import Engine
from sqlmodel import Session, create_engine


_DEFAULT_URL = "sqlite:///./data/book_alerter.db"


def get_database_url() -> str:
    return os.environ.get("BOOK_ALERTER_DATABASE_URL", _DEFAULT_URL)


def get_engine(url: str | None = None) -> Engine:
    resolved = url if url is not None else get_database_url()
    return create_engine(
        resolved,
        echo=False,
        connect_args={"check_same_thread": False},
    )


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
