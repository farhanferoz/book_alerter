from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import vcr
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel

from book_alerter.db import models
from book_alerter.db.session import get_engine
from book_alerter.db.views import BOOK_STATS_VIEW_SQL

WOB_CASSETTE_DIR = Path(__file__).parent / "sources" / "cassettes"
WOB_CARRIED_ISBN = "9780241638194"
WOB_MAYBE_NOT_CARRIED_ISBN = "9789693531374"

METADATA_CASSETTE_DIR = Path(__file__).parent / "cassettes" / "metadata"


@pytest.fixture
def sqlite_engine(tmp_path) -> Engine:
    engine = get_engine(f"sqlite:///{tmp_path}/t.db")
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture
def engine_with_view(sqlite_engine):
    with sqlite_engine.begin() as conn:
        conn.exec_driver_sql(BOOK_STATS_VIEW_SQL)
    return sqlite_engine


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


def _vcr_factory(cassette_dir: Path, default_record_mode: str, *, match_query: bool = False):
    extra = ("query",) if match_query else ()

    def _make(record_mode: str = default_record_mode) -> vcr.VCR:
        return vcr.VCR(
            cassette_library_dir=str(cassette_dir),
            record_mode=record_mode,
            match_on=("method", "scheme", "host", "port", "path", *extra),
            decode_compressed_response=True,
        )

    return _make


@pytest.fixture
def wob_vcr():
    return _vcr_factory(WOB_CASSETTE_DIR, "once")


@pytest.fixture
def metadata_vcr():
    return _vcr_factory(METADATA_CASSETTE_DIR, "none", match_query=True)
