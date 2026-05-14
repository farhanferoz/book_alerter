import asyncio
from pathlib import Path

import vcr

from book_alerter.sources.wob import WobInlineSource


CASSETTE_DIR = Path(__file__).parent / "cassettes"
my_vcr = vcr.VCR(
    cassette_library_dir=str(CASSETTE_DIR),
    record_mode="once",
    match_on=("method", "scheme", "host", "port", "path"),
    decode_compressed_response=True,
)


# 9780241638194 — popular Penguin paperback, reliably carried by WoB.
# 9789693531374 — obscure ISBN; may or may not be carried.
ISBN_LIKELY_CARRIED = "9780241638194"
ISBN_MAYBE_NOT_CARRIED = "9789693531374"


def test_wob_extracts_at_least_one_offer_from_carried_isbn(transient_book):
    """The popular ISBN must return at least one parsed offer with a known
    condition. Guards against both broken selectors AND a silent WoB rename
    that would coerce real offers to `unknown`."""
    src = WobInlineSource(name="wob", region="UK")
    with my_vcr.use_cassette(f"wob_{ISBN_LIKELY_CARRIED}.yaml"):
        out = asyncio.run(src.fetch(transient_book(ISBN_LIKELY_CARRIED)))
    assert len(out) > 0, "expected >=1 offer for a popular ISBN; selectors may be wrong"
    for c in out:
        assert c.condition in {"new", "used_vg", "used_g", "used_acceptable"}, (
            f"unexpected condition {c.condition!r}; WoB may have renamed a token "
            f"and _CONDITION_MAP needs updating"
        )
        assert c.price_minor > 0
        assert c.currency == "GBP"
        assert c.url.startswith("https://")


def test_wob_handles_uncarried_isbn_gracefully(transient_book):
    src = WobInlineSource(name="wob", region="UK")
    with my_vcr.use_cassette(f"wob_{ISBN_MAYBE_NOT_CARRIED}.yaml"):
        out = asyncio.run(src.fetch(transient_book(ISBN_MAYBE_NOT_CARRIED)))
    assert isinstance(out, list)
