"""Integration tests for AmazonUKInlineSource.

Playwright/Chromium fetch is mocked at the `_render_page` boundary so the
test exercises the full Source contract (fetch -> parse -> ObservationCandidate
list) without launching a browser. The source renders both the dp and the
offer-listing pages on every fetch and merges/dedups the results; the tests
cover the all-three combinations (both populated / dp only / offer-listing
only).

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
from book_alerter.sources.amazon import (
    AmazonUKInlineSource,
    _render_amazon_page,
    parse_offer_listing,
)
from tests.integration.sources.helpers import make_fake_playwright_factory

FIXTURE_DIR = (
    Path(__file__).resolve().parents[2] / "fixtures" / "amazon"
)
FIXTURE_DP = FIXTURE_DIR / "9780747532699-uk-dp-2026-05-14.html"
FIXTURE_DP_NO_PRICE = FIXTURE_DIR / "9780747532699-uk-dp-no-price.html"
FIXTURE_OFFER_LISTING = FIXTURE_DIR / "9780747532699-uk-offer-listing.html"


def _hp_book() -> Book:
    return Book(
        isbn13="9780747532699",
        title="Harry Potter and the Philosopher's Stone",
        region="UK",
    )


def _install_fake_render_page(monkeypatch: pytest.MonkeyPatch, by_url: dict[str, str]) -> list[str]:
    """Patch `_render_amazon_page` to return canned HTML keyed by url-substring;
    return the call log. Patches the module-level helper that both the book and
    product sources route through, so a single patch covers both fetch paths.

    `_render_amazon_page` is fully replaced, so the `context` it would have
    used is never touched here — callers still need `src._context` set to
    any non-None sentinel (see `_prepared`) because `fetch()` asserts
    `prepare()` ran before it does anything else.
    """
    calls: list[str] = []

    async def fake_render_amazon_page(
        context,
        url: str,
        *,
        wait_selector,
        wait_ms,
        navigation_timeout_s,
        source_name,
    ):
        calls.append(url)
        for marker, html in by_url.items():
            if marker in url:
                return html
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(
        "book_alerter.sources.amazon._render_amazon_page",
        fake_render_amazon_page,
    )
    return calls


def _prepared(src):
    """Inject a fake `BrowserSession` context so `fetch()`'s
    `prepare() must run before fetch()` assertion passes without a real
    browser. Only a sentinel — the tests that use this always also patch
    `_render_amazon_page` (see `_install_fake_render_page`), which never
    touches the context itself.
    """
    src._context = object()
    return src


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


def test_fetch_renders_both_pages_and_merges_with_dedup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both dp + offer-listing populated. The Amazon buy-box appears on both
    pages; the merged result must collapse it to a single row.

    S8 (2026-09-04): this used to assert the offer-listing's concrete
    shipping_minor=0 wins over the dp's None ("a known value beats no
    information"). Since T2.5/D33/D35, a disagreeing 0 is not more
    informative than an honest None — the 0 might itself just be a miss on
    that render — so None now wins a None-vs-0 disagreement specifically
    (a real non-zero charge still beats None, unchanged). Flipped
    deliberately here for the same reason as the two `_merge_offers` unit
    tests in test_amazon_parser.py; see that file's S8 tests for the full
    both-directions reasoning.
    """
    calls = _install_fake_render_page(
        monkeypatch,
        {
            "/dp/": FIXTURE_DP.read_text(encoding="utf-8"),
            "/gp/offer-listing/": FIXTURE_OFFER_LISTING.read_text(encoding="utf-8"),
        },
    )

    src = _prepared(AmazonUKInlineSource(region="UK"))
    offers = asyncio.run(src.fetch(_hp_book()))

    assert len(calls) == 2
    assert "/dp/" in calls[0]
    assert "/gp/offer-listing/" in calls[1]
    # dp yields 1 Amazon/new/799 row (shipping=None) + offer-listing yields 4
    # rows incl. an Amazon/new/799 row (shipping=0). Dedup collapses the
    # overlapping Amazon row, so the union is exactly 4.
    assert len(offers) == 4
    sellers = {o.seller for o in offers}
    assert sellers == {"Amazon", "BetterWorldBooksUK", "WorldOfBooks Ltd", "MusicMagpie"}
    amazon_row = next(o for o in offers if o.seller == "Amazon")
    assert amazon_row.shipping_minor is None  # S8: None wins a None-vs-0 disagreement
    used_conditions = {o.condition for o in offers if o.seller != "Amazon"}
    assert used_conditions == {"used_vg", "used_g", "used_acceptable"}


def test_fetch_returns_dp_only_when_offer_listing_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """dp has a buy-box, offer-listing renders but has no #aod-offer rows.
    Merge returns the dp row untouched — guards the "Amazon-fulfilled
    new-only" path where the AOD page is empty."""
    # The offer-listing page MUST carry the `#aod-container` marker even
    # when empty — otherwise the parser correctly raises SourceError
    # treating it as an unknown anti-bot variant. Real Amazon AOD pages
    # always render the container shell regardless of whether any offer
    # rows exist.
    calls = _install_fake_render_page(
        monkeypatch,
        {
            "/dp/": FIXTURE_DP.read_text(encoding="utf-8"),
            "/gp/offer-listing/": (
                '<html><body><div id="aod-container">'
                '<div id="aod-offer-list"></div>'
                "</div></body></html>"
            ),
        },
    )

    src = _prepared(AmazonUKInlineSource(region="UK"))
    offers = asyncio.run(src.fetch(_hp_book()))

    assert len(calls) == 2
    assert len(offers) == 1
    o = offers[0]
    assert o.price_minor == 799
    assert o.currency == "GBP"
    assert o.condition == "new"


def test_fetch_returns_offer_listing_only_when_dp_has_no_buybox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No buy-box on dp (parser returns empty) -> all offers come from
    offer-listing."""
    calls = _install_fake_render_page(
        monkeypatch,
        {
            "/dp/": FIXTURE_DP_NO_PRICE.read_text(encoding="utf-8"),
            "/gp/offer-listing/": FIXTURE_OFFER_LISTING.read_text(encoding="utf-8"),
        },
    )

    src = _prepared(AmazonUKInlineSource(region="UK"))
    offers = asyncio.run(src.fetch(_hp_book()))

    assert len(calls) == 2
    assert "/dp/" in calls[0]
    assert "/gp/offer-listing/" in calls[1]
    assert len(offers) == 4
    sellers = {o.seller for o in offers}
    assert sellers == {"Amazon", "BetterWorldBooksUK", "WorldOfBooks Ltd", "MusicMagpie"}


def test_bot_check_raises_source_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """If Playwright fails to clear Amazon's anti-bot, the rendered HTML
    contains a known marker — `_render_page` must raise SourceError so the
    caller can alert rather than silently emit zero offers. T1.5: it must
    also dump the challenge HTML via `write_debug_capture` so a human can
    see what Amazon actually served."""
    import book_alerter.sources.browser as browser_mod
    from book_alerter.sources.base import SourceError

    monkeypatch.setattr(browser_mod, "_DEBUG_ROOT", tmp_path)

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

    dumps = list((tmp_path / "amazon").glob("*.html"))
    assert len(dumps) == 1
    assert dumps[0].read_text(encoding="utf-8") == bot_html


async def _render_via_fake(html: str, url: str, *, source_name: str) -> str:
    """Drive the real `_render_amazon_page` against `html` returned by a
    fake Playwright context — the F26 canonical-ASIN check lives inside
    `_render_amazon_page`, so exercising it means driving that function
    directly, not just the parser."""
    fake_factory = make_fake_playwright_factory(html)
    async with fake_factory() as pw:
        browser = await pw.chromium.launch()
        context = await browser.new_context()
        return await _render_amazon_page(
            context,
            url,
            wait_selector="x",
            wait_ms=1000,
            navigation_timeout_s=30.0,
            source_name=source_name,
        )


def test_render_amazon_page_echo_dot_aod_genuinely_empty_does_not_raise() -> None:
    """F26 regression, the "don't break the genuine-empty case" side: the
    Echo Dot's real offer-listing page is a correctly-attributed empty
    listing (0 third-party rows, 1 pinned Amazon offer; canonicalises to
    its own requested ASIN). The canonical-ASIN guard must not mistake an
    empty result for a wrong-product response."""
    html = (FIXTURE_DIR / "products" / "B09B96TG33-uk-aod-2026-09-04.html").read_text(
        encoding="utf-8"
    )
    url = "https://www.amazon.co.uk/gp/offer-listing/B09B96TG33?condition=all"

    result = asyncio.run(_render_via_fake(html, url, source_name="amazon_uk_product"))

    assert result == html
    offers = parse_offer_listing(result, fallback_url=url, source_name="amazon_uk_product")
    assert offers == []


def test_render_amazon_page_raises_and_writes_debug_capture_on_canonical_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """F26 regression, the "catch the actual bug" side: this real capture's
    own `<link rel="canonical">` points at a different ASIN (B0DLSB1WWK)
    than the one requested (B0CYT8WL1G) — Amazon silently served the wrong
    product's page. Must raise, and must dump the HTML like any other
    unrecognised-response case."""
    import book_alerter.sources.browser as browser_mod
    from book_alerter.sources.base import SourceError

    monkeypatch.setattr(browser_mod, "_DEBUG_ROOT", tmp_path)

    html = (FIXTURE_DIR / "products" / "B0CYT8WL1G-uk-aod-2026-09-04.html").read_text(
        encoding="utf-8"
    )
    url = "https://www.amazon.co.uk/gp/offer-listing/B0CYT8WL1G?condition=all"

    with pytest.raises(SourceError, match="canonical URL mismatch"):
        asyncio.run(_render_via_fake(html, url, source_name="amazon_uk_product"))

    dumps = list((tmp_path / "amazon_uk_product").glob("*.html"))
    assert len(dumps) == 1
    assert dumps[0].read_text(encoding="utf-8") == html


@pytest.mark.skipif(
    os.environ.get("AMAZON_LIVE") != "1",
    reason="set AMAZON_LIVE=1 to run live Playwright fetch against amazon.co.uk",
)
def test_live_fetch_against_amazon() -> None:
    """Real Playwright + real network. As of 2026-05-14 Amazon's anti-bot
    consistently blocks this path — kept as a canary for if/when the
    protection eases or our stealth game improves. Skipped unless
    AMAZON_LIVE=1."""

    async def _drive() -> list:
        src = AmazonUKInlineSource(region="UK", timeout_s=45.0)
        await src.prepare()
        try:
            return await src.fetch(_hp_book())
        finally:
            await src.cleanup()

    offers = asyncio.run(_drive())
    assert len(offers) >= 1, "live amazon returned no offers — bot-protection or DOM change"
    for o in offers:
        assert o.price_minor > 0
        assert o.currency == "GBP"
