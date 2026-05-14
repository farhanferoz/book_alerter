from datetime import UTC, datetime

from sqlmodel import SQLModel, Session, create_engine, select

from book_alerter.db import models


def test_book_round_trip(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        book = models.Book(
            isbn13="9780000000000",
            title="Test",
            author="Anon",
            currency="GBP",
            target_price_minor=1000,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        s.add(book)
        s.commit()
        s.refresh(book)
        assert book.id is not None
        loaded = s.exec(select(models.Book)).one()
        assert loaded.isbn13 == "9780000000000"
        assert loaded.target_price_minor == 1000
        assert loaded.alert_kinds_disabled == []
