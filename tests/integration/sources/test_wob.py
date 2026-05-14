import asyncio

from tests.integration.conftest import WOB_CARRIED_ISBN, WOB_MAYBE_NOT_CARRIED_ISBN

from book_alerter.sources.wob import WobInlineSource


def test_wob_extracts_at_least_one_offer_from_carried_isbn(transient_book, wob_vcr):
    """The popular ISBN must return at least one parsed offer with a known
    condition. Guards against both broken selectors AND a silent WoB rename
    that would coerce real offers to `unknown`."""
    src = WobInlineSource(name="wob", region="UK")
    with wob_vcr("once").use_cassette(f"wob_{WOB_CARRIED_ISBN}.yaml"):
        out = asyncio.run(src.fetch(transient_book(WOB_CARRIED_ISBN)))
    assert len(out) > 0, "expected >=1 offer for a popular ISBN; selectors may be wrong"
    for c in out:
        assert c.condition in {"new", "used_vg", "used_g", "used_acceptable"}, (
            f"unexpected condition {c.condition!r}; WoB may have renamed a token "
            f"and _CONDITION_MAP needs updating"
        )
        assert c.price_minor > 0
        assert c.currency == "GBP"
        assert c.url.startswith("https://")


def test_wob_handles_uncarried_isbn_gracefully(transient_book, wob_vcr):
    src = WobInlineSource(name="wob", region="UK")
    with wob_vcr("once").use_cassette(f"wob_{WOB_MAYBE_NOT_CARRIED_ISBN}.yaml"):
        out = asyncio.run(src.fetch(transient_book(WOB_MAYBE_NOT_CARRIED_ISBN)))
    assert isinstance(out, list)
