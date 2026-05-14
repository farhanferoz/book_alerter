from datetime import UTC, datetime

from sqlmodel import Session, select

from book_alerter.db import models


def test_book_round_trip(sqlite_engine):
    now = datetime.now(UTC)
    with Session(sqlite_engine) as s:
        book = models.Book(
            isbn13="9780000000000",
            title="Test",
            author="Anon",
            currency="GBP",
            target_price_minor=1000,
            created_at=now,
            updated_at=now,
        )
        s.add(book)
        s.commit()
        s.refresh(book)
        assert book.id is not None
        loaded = s.exec(select(models.Book)).one()
        assert loaded.isbn13 == "9780000000000"
        assert loaded.target_price_minor == 1000
        assert loaded.alert_kinds_disabled == []
