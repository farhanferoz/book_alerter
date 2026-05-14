import asyncio
from datetime import datetime

import pytest

from book_alerter.db.models import Book
from book_alerter.sources.base import (
    ObservationCandidate,
    Source,
    SourceError,
)


class _Stub(Source):
    name = "stub"

    async def fetch(self, book):
        if book.isbn13 == "fail":
            raise SourceError(self.name, "boom")
        return [
            ObservationCandidate(
                seller=None,
                condition="new",
                price_minor=100,
                shipping_minor=None,
                currency="GBP",
                url="https://x",
            )
        ]


def test_stub_source_returns_candidates():
    src = _Stub()
    book = Book(
        isbn13="9780000000000",
        title="t",
        author="a",
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    out = asyncio.run(src.fetch(book))
    assert len(out) == 1
    assert out[0].condition == "new"


def test_stub_source_raises_source_error():
    src = _Stub()
    book = Book(
        isbn13="fail",
        title="t",
        author="a",
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    with pytest.raises(SourceError):
        asyncio.run(src.fetch(book))
