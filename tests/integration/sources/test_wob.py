import asyncio
from datetime import UTC, datetime
from pathlib import Path

import vcr

from book_alerter.db.models import Book
from book_alerter.sources.wob import WobInlineSource


CASSETTE_DIR = Path(__file__).parent / "cassettes"
my_vcr = vcr.VCR(
    cassette_library_dir=str(CASSETTE_DIR),
    record_mode="once",
    match_on=("method", "scheme", "host", "port", "path"),
    decode_compressed_response=True,
)


# Pick TWO ISBNs from the plan's fixtures so cassettes are deterministic.
# 9780241638194 is a real, popular Penguin paperback — almost certainly carried by WoB.
ISBN_LIKELY_CARRIED = "9780241638194"
ISBN_MAYBE_NOT_CARRIED = "9789693531374"


def _book(isbn: str) -> Book:
    now = datetime.now(UTC)
    return Book(isbn13=isbn, title="t", author="a", created_at=now, updated_at=now)


def test_wob_extracts_at_least_one_offer_from_carried_isbn():
    """The popular ISBN must return at least one parsed offer — guards against
    vacuous passes when selectors are wrong."""
    src = WobInlineSource(name="wob", region="UK")
    with my_vcr.use_cassette(f"wob_{ISBN_LIKELY_CARRIED}.yaml"):
        out = asyncio.run(src.fetch(_book(ISBN_LIKELY_CARRIED)))
    assert len(out) > 0, "expected >=1 offer for a popular ISBN; selectors may be wrong"
    for c in out:
        assert c.condition in {"new", "used_vg", "used_g", "used_acceptable", "unknown"}
        assert c.price_minor > 0
        assert c.currency == "GBP"
        assert c.url.startswith("https://")


def test_wob_handles_uncarried_isbn_gracefully():
    """An uncarried/obscure ISBN should return [] (or a parseable empty page),
    not raise."""
    src = WobInlineSource(name="wob", region="UK")
    with my_vcr.use_cassette(f"wob_{ISBN_MAYBE_NOT_CARRIED}.yaml"):
        out = asyncio.run(src.fetch(_book(ISBN_MAYBE_NOT_CARRIED)))
    # Empty is allowed for the uncarried-ISBN case; non-empty also fine.
    assert isinstance(out, list)
