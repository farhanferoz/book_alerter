"""Integration tests for AmazonUKInlineSource.

Playwright/Chromium fetch is mocked at the `_render` boundary so the test
exercises the full Source contract (fetch -> parse -> ObservationCandidate
list) without launching a browser. The dp -> offer-listing fallback path
gets its own test.

Live capture against amazon.co.uk is gated by `AMAZON_LIVE=1`. Note: as of
2026-05-14 Amazon's anti-bot reliably defeats headless Chromium for the
target dp/offer-listing endpoints, so the live test is more of a
canary-on-protection-change than something expected to pass — see
docs/CHANGELOG.md Phase 8.3 entry.
"""

import asyncio
import os
from pathlib import Path

import pytest

from book_alerter.db.models import Book
from book_alerter.sources.amazon import AmazonUKInlineSource

FIXTURE_DIR = (
    Path(__file__).resolve().parents[2] / "fixtures" / "amazon"
)
FIXTURE_DP = FIXTURE_DIR / "9780747532699-uk-dp.html"
FIXTURE_DP_NO_PRICE = FIXTURE_DIR / "9780747532699-uk-dp-no-price.html"
FIXTURE_OFFER_LISTING = FIXTURE_DIR / "9780747532699-uk-offer-listing.html"


def _hp_book() -> Book:
    return Book(
        isbn13="9780747532699",
        title="Harry Potter and the Philosopher's Stone",
        region="UK",
    )


def test_dp_url_and_offer_listing_url() -> None:
    src = AmazonUKInlineSource(region="UK")
    assert src.dp_url("9780747532699") == "https://www.amazon.co.uk/dp/9780747532699"
    assert (
        src.offer_listing_url("9780747532699")
        == "https://www.amazon.co.uk/gp/offer-listing/9780747532699?condition=all"
    )


def test_non_uk_region_rejected() -> None:
    with pytest.raises(ValueError, match="UK"):
        AmazonUKInlineSource(region="US")


def test_fetch_returns_observation_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock the dp render and verify Source contract end-to-end."""
    html = FIXTURE_DP.read_text(encoding="utf-8")

    async def fake_render(self, playwright_factory, url: str) -> str:
        assert "9780747532699" in url
        assert "amazon.co.uk/dp/" in url
        return html

    monkeypatch.setattr(AmazonUKInlineSource, "_render", fake_render)
    src = AmazonUKInlineSource(region="UK")
    offers = asyncio.run(src.fetch(_hp_book()))
    assert len(offers) == 1
    o = offers[0]
    assert o.price_minor == 799
    assert o.currency == "GBP"
    assert o.condition == "new"


def test_fetch_falls_back_to_offer_listing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty buy-box on dp -> source must follow up with offer-listing fetch."""
    dp_no_price = FIXTURE_DP_NO_PRICE.read_text(encoding="utf-8")
    ol_html = FIXTURE_OFFER_LISTING.read_text(encoding="utf-8")

    calls: list[str] = []

    async def fake_render(self, playwright_factory, url: str) -> str:
        calls.append(url)
        if "/dp/" in url:
            return dp_no_price
        if "/gp/offer-listing/" in url:
            return ol_html
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(AmazonUKInlineSource, "_render", fake_render)
    src = AmazonUKInlineSource(region="UK")
    offers = asyncio.run(src.fetch(_hp_book()))

    assert len(calls) == 2
    assert "/dp/" in calls[0]
    assert "/gp/offer-listing/" in calls[1]
    assert len(offers) == 4
    # All four rows from the offer-listing fixture should be present.
    sellers = {o.seller for o in offers}
    assert sellers == {"Amazon", "BetterWorldBooksUK", "WorldOfBooks Ltd", "MusicMagpie"}


def test_fetch_does_not_fall_back_when_dp_has_price(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sanity: if dp has a usable buy-box, the offer-listing page is never hit."""
    dp_html = FIXTURE_DP.read_text(encoding="utf-8")
    calls: list[str] = []

    async def fake_render(self, playwright_factory, url: str) -> str:
        calls.append(url)
        return dp_html

    monkeypatch.setattr(AmazonUKInlineSource, "_render", fake_render)
    src = AmazonUKInlineSource(region="UK")
    offers = asyncio.run(src.fetch(_hp_book()))

    assert len(calls) == 1
    assert "/dp/" in calls[0]
    assert len(offers) == 1


def test_bot_check_raises_source_error() -> None:
    """If Playwright fails to clear Amazon's anti-bot, the rendered HTML
    contains a known marker — `_render` must raise SourceError so the caller
    can alert rather than silently emit zero offers (indistinguishable from
    'no listings')."""
    from book_alerter.sources.base import SourceError

    bot_html = (
        "<html><head><title>Robot Check</title></head>"
        "<body>To discuss automated access to Amazon please contact "
        "api-services-support@amazon.com.</body></html>"
    )

    src = AmazonUKInlineSource(region="UK")
    fake_factory = _make_fake_playwright_factory(bot_html)

    with pytest.raises(SourceError, match="bot-protection challenge persisted"):
        asyncio.run(src._render(fake_factory, "https://www.amazon.co.uk/dp/x"))


def _make_fake_playwright_factory(html_to_return: str):
    """Adapted from tests/integration/sources/test_bookfinder.py."""
    class _Page:
        async def goto(self, *a, **kw): return None
        async def wait_for_selector(self, *a, **kw): return None
        async def content(self): return html_to_return

    class _Context:
        async def new_page(self): return _Page()

    class _Browser:
        async def new_context(self, **kw): return _Context()
        async def close(self): return None

    class _Chromium:
        async def launch(self, **kw): return _Browser()

    class _PW:
        chromium = _Chromium()

    class _Factory:
        async def __aenter__(self): return _PW()
        async def __aexit__(self, *a): return None

    return lambda: _Factory()


@pytest.mark.skipif(
    os.environ.get("AMAZON_LIVE") != "1",
    reason="set AMAZON_LIVE=1 to run live Playwright fetch against amazon.co.uk",
)
def test_live_fetch_against_amazon() -> None:
    """Real Playwright + real network. As of 2026-05-14 Amazon's anti-bot
    consistently blocks this path — kept as a canary for if/when the
    protection eases or our stealth game improves. Skipped unless
    AMAZON_LIVE=1."""
    src = AmazonUKInlineSource(region="UK", timeout_s=45.0)
    offers = asyncio.run(src.fetch(_hp_book()))
    assert len(offers) >= 1, "live amazon returned no offers — bot-protection or DOM change"
    for o in offers:
        assert o.price_minor > 0
        assert o.currency == "GBP"
