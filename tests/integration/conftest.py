from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlmodel import Session, SQLModel
from sqlalchemy.engine import Engine

from book_alerter.db import models
from book_alerter.db.session import get_engine


@pytest.fixture
def sqlite_engine(tmp_path) -> Engine:
    engine = get_engine(f"sqlite:///{tmp_path}/t.db")
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture
def make_book():
    def _make(session: Session, *, isbn13: str = "9780000000000") -> models.Book:
        now = datetime.now(UTC)
        book = models.Book(
            isbn13=isbn13, title="t", author="a",
            created_at=now, updated_at=now,
        )
        session.add(book)
        session.commit()
        session.refresh(book)
        return book

    return _make
