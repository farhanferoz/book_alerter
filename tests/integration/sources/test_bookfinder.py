"""Integration tests for BookfinderInlineSource.

The Playwright/Chromium fetch is mocked at the `_render` boundary so the test
exercises the full Source contract (fetch → parse → ObservationCandidate list)
without touching the network or launching a browser.

An opt-in live test against bookfinder.com is gated by `BOOKFINDER_LIVE=1` —
slow (~3-5s), needs network + Chromium installed, intentionally not in CI.
"""

import asyncio
import os
from pathlib import Path

import pytest

from book_alerter.db.models import Book
from book_alerter.sources.bookfinder import BookfinderInlineSource
from tests.integration.sources.helpers import make_fake_playwright_factory

FIXTURE_GB = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "bookfinder"
    / "9780747532699-gb-all.html"
)


def _hp_book() -> Book:
    return Book(
        isbn13="9780747532699",
        title="Harry Potter and the Philosopher's Stone",
        region="UK",
    )


def test_fetch_returns_observation_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock the Playwright render and verify the Source contract end-to-end."""
    html = FIXTURE_GB.read_text(encoding="utf-8")

    async def fake_render(self, context, url: str) -> str:
        # Smoke-check the URL we'd hit so a regressed URL builder fails this test.
        assert "keywords=9780747532699" in url
        assert "destination=GB" in url
        assert "currency=GBP" in url
        return html

    monkeypatch.setattr(BookfinderInlineSource, "_render", fake_render)
    src = BookfinderInlineSource(region="UK")
    # `_render` is fully replaced above, so the context it would have used
    # is never touched — `fetch()` still asserts `prepare()` ran before it
    # does anything else, so inject a sentinel.
    src._context = object()
    offers = asyncio.run(src.fetch(_hp_book()))
    assert len(offers) == 3
    assert all(o.price_minor > 0 for o in offers)
    assert all(o.currency == "GBP" for o in offers)


def test_waf_challenge_raises_source_error() -> None:
    """If Playwright fails to clear the WAF, the rendered HTML still contains
    the challenge markers — `_render` must raise SourceError so the caller can
    alert, not silently emit zero offers (indistinguishable from 'no listings').

    Drives the real `_render` against a fake Playwright context that returns
    WAF HTML, so the production WAF-detection branch is exercised.
    """
    from book_alerter.sources.base import SourceError

    waf_html = (
        "<html><head><script>window.awsWafCookieDomainList = [];"
        "window.gokuProps = {'key':'x'};</script></head></html>"
    )

    src = BookfinderInlineSource(region="UK")
    fake_factory = make_fake_playwright_factory(waf_html)

    async def _drive() -> None:
        async with fake_factory() as pw:
            browser = await pw.chromium.launch()
            context = await browser.new_context()
            await src._render(context, "https://www.bookfinder.com/")

    with pytest.raises(SourceError, match="WAF challenge persisted"):
        asyncio.run(_drive())


def test_search_url_includes_required_params() -> None:
    src = BookfinderInlineSource(region="UK")
    url = src.search_url("9780747532699")
    assert "keywords=9780747532699" in url
    assert "destination=GB" in url
    assert "currency=GBP" in url
    assert "viewAll=true" in url
    assert "searchOffersType=" in url

    us_src = BookfinderInlineSource(region="US")
    us_url = us_src.search_url("9780747532699")
    assert "destination=US" in us_url
    assert "currency=USD" in us_url


@pytest.mark.skipif(
    os.environ.get("BOOKFINDER_LIVE") != "1",
    reason="set BOOKFINDER_LIVE=1 to run live Playwright fetch against bookfinder.com",
)
def test_live_fetch_against_bookfinder() -> None:
    """Real Playwright + real network. Confirms the AWS WAF actually lets a
    headless Chromium through. Skipped unless BOOKFINDER_LIVE=1 because it's
    slow (~3-5s), needs Chromium installed, and is network-flakey."""

    async def _drive() -> list:
        src = BookfinderInlineSource(region="UK", timeout_s=45.0)
        await src.prepare()
        try:
            return await src.fetch(_hp_book())
        finally:
            await src.cleanup()

    offers = asyncio.run(_drive())
    assert len(offers) >= 1, "live bookfinder returned no offers — WAF blocked or DOM changed"
    for o in offers:
        assert o.price_minor > 0
        assert o.currency in {"GBP", "USD", "EUR"}
