from datetime import UTC, datetime

from sqlmodel import SQLModel, Session, create_engine, select

from book_alerter.db import models


def test_observation_with_duplicate_link(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        book = models.Book(
            isbn13="9780000000000", title="t", author="a",
            created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
        )
        s.add(book); s.commit(); s.refresh(book)

        primary = models.PriceObservation(
            book_id=book.id, source="bookfinder", condition="new",
            price_minor=1000, currency="GBP", total_minor=1000,
            url="https://x", observed_at=datetime.now(UTC), raw={"hi": 1},
        )
        s.add(primary); s.commit(); s.refresh(primary)

        dupe = models.PriceObservation(
            book_id=book.id, source="amazon", condition="new",
            price_minor=1000, currency="GBP", total_minor=1000,
            url="https://x", observed_at=datetime.now(UTC), raw={},
            is_duplicate_of=primary.id,
        )
        s.add(dupe); s.commit(); s.refresh(dupe)

        non_dupes = s.exec(
            select(models.PriceObservation).where(
                models.PriceObservation.is_duplicate_of.is_(None)
            )
        ).all()
        assert len(non_dupes) == 1
        assert non_dupes[0].id == primary.id
