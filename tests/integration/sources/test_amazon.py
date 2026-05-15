"""Integration tests for AmazonUKInlineSource.

Playwright/Chromium fetch is mocked at the `_render_page` boundary so the
test exercises the full Source contract (fetch -> parse -> ObservationCandidate
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
from tests.integration.sources.helpers import make_fake_playwright_factory

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


def _install_fake_render_page(monkeypatch: pytest.MonkeyPatch, by_url: dict[str, str]) -> list[str]:
    """Patch `_render_page` to return canned HTML keyed by url-substring; return the call log."""
    calls: list[str] = []

    async def fake_render_page(self, context, url: str, *, wait_selector, wait_ms):
        calls.append(url)
        for marker, html in by_url.items():
            if marker in url:
                return html
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(AmazonUKInlineSource, "_render_page", fake_render_page)
    # Also bypass the real browser launch — `fetch` opens a chromium context;
    # we replace `async_playwright` with the fake factory so no binary is needed.
    monkeypatch.setattr(
        "book_alerter.sources.amazon.async_playwright",
        make_fake_playwright_factory(""),
    )
    return calls


def test_dp_url_and_offer_listing_url() -> None:
    # Amazon UK indexes books by ISBN-10 (the ASIN). /dp/{ISBN-13} silently
    # serves a 2 KB soft-404 for any 978-prefixed book, so the source must
    # convert ISBN-13 → ISBN-10 before constructing the URL.
    src = AmazonUKInlineSource(region="UK")
    assert src.dp_url("9780747532699") == "https://www.amazon.co.uk/dp/0747532699"
    assert (
        src.offer_listing_url("9780747532699")
        == "https://www.amazon.co.uk/gp/offer-listing/0747532699?condition=all"
    )


def test_dp_url_falls_back_to_isbn13_for_979_prefix() -> None:
    # 979-prefixed ISBN-13s (post-2007) have no ISBN-10 form. Fall back to
    # the ISBN-13 path rather than fail.
    src = AmazonUKInlineSource(region="UK")
    assert src.dp_url("9791234567896") == "https://www.amazon.co.uk/dp/9791234567896"


def test_non_uk_region_rejected() -> None:
    with pytest.raises(ValueError, match="UK"):
        AmazonUKInlineSource(region="US")


def test_fetch_returns_dp_offer_when_buybox_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """dp page with a usable buy-box: return that, never hit offer-listing."""
    calls = _install_fake_render_page(
        monkeypatch, {"/dp/": FIXTURE_DP.read_text(encoding="utf-8")}
    )

    src = AmazonUKInlineSource(region="UK")
    offers = asyncio.run(src.fetch(_hp_book()))

    assert len(calls) == 1
    assert "/dp/" in calls[0]
    assert len(offers) == 1
    o = offers[0]
    assert o.price_minor == 799
    assert o.currency == "GBP"
    assert o.condition == "new"


def test_fetch_falls_back_to_offer_listing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty buy-box on dp -> source must follow up with offer-listing fetch."""
    calls = _install_fake_render_page(
        monkeypatch,
        {
            "/dp/": FIXTURE_DP_NO_PRICE.read_text(encoding="utf-8"),
            "/gp/offer-listing/": FIXTURE_OFFER_LISTING.read_text(encoding="utf-8"),
        },
    )

    src = AmazonUKInlineSource(region="UK")
    offers = asyncio.run(src.fetch(_hp_book()))

    assert len(calls) == 2
    assert "/dp/" in calls[0]
    assert "/gp/offer-listing/" in calls[1]
    assert len(offers) == 4
    sellers = {o.seller for o in offers}
    assert sellers == {"Amazon", "BetterWorldBooksUK", "WorldOfBooks Ltd", "MusicMagpie"}


def test_bot_check_raises_source_error() -> None:
    """If Playwright fails to clear Amazon's anti-bot, the rendered HTML
    contains a known marker — `_render_page` must raise SourceError so the
    caller can alert rather than silently emit zero offers."""
    from book_alerter.sources.base import SourceError

    bot_html = (
        "<html><head><title>Robot Check</title></head>"
        "<body>To discuss automated access to Amazon please contact "
        "api-services-support@amazon.com.</body></html>"
    )

    src = AmazonUKInlineSource(region="UK")
    fake_factory = make_fake_playwright_factory(bot_html)

    async def _drive() -> None:
        async with fake_factory() as pw:
            browser = await pw.chromium.launch()
            context = await browser.new_context()
            await src._render_page(
                context, "https://www.amazon.co.uk/dp/x",
                wait_selector="x", wait_ms=1000,
            )

    with pytest.raises(SourceError, match="bot-protection challenge persisted"):
        asyncio.run(_drive())


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
