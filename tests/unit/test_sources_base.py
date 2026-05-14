import asyncio

import pytest

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


def test_stub_source_returns_candidates(transient_book):
    src = _Stub()
    out = asyncio.run(src.fetch(transient_book()))
    assert len(out) == 1
    assert out[0].condition == "new"


def test_stub_source_raises_source_error(transient_book):
    src = _Stub()
    with pytest.raises(SourceError):
        asyncio.run(src.fetch(transient_book("fail")))
