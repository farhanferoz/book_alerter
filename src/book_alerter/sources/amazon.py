from __future__ import annotations

import re

from playwright.async_api import (
    TimeoutError as PlaywrightTimeoutError,
)
from playwright.async_api import (
    async_playwright,
)
from selectolax.parser import HTMLParser, Node

from book_alerter.db.models import Book, Condition
from book_alerter.sources.base import (
    ObservationCandidate,
    SourceError,
)
from book_alerter.sources.inline_source import InlineSource

# Amazon UK is fronted by aggressive bot-protection that defeats any client
# that doesn't render JS in a real browser (verified 2026-05-14: headless
# Chromium + stealth flags + cookie warm-up still got served the
# "To discuss automated access to Amazon" interstitial). So this source
# drives headless Chromium via Playwright for every lookup.
#
# Strategy: try /dp/<ISBN> first to extract the buy-box price. If the dp
# page has no usable buy-box (out of stock, marketplace-only listing,
# etc.), fall back to /gp/offer-listing/<ISBN>?condition=all and enumerate
# the marketplace offers.
#
# Region: UK only at MVP. Pass region="UK" or accept the default.
#
# DOM contract:
#   dp page          - buy-box price under #corePriceDisplay_desktop_feature_div
#                      OR #corePrice_feature_div. Within, .a-price wraps the
#                      number; .a-offscreen carries the screen-reader-friendly
#                      full price ("£7.99"). Prefer .a-offscreen — it's the
#                      most stable selector.
#   offer-listing    - rows under #aod-offer-list, each `#aod-offer` (modern
#                      AOD layout). Legacy fallback: `.olpOffer`. Within each
#                      row: #aod-offer-price (.a-offscreen), #aod-offer-shipping,
#                      #aod-offer-heading (h5 text with the condition),
#                      #aod-offer-soldBy (seller link text).

_PRICE_RE = re.compile(r"&pound;|£\s*(\d+(?:\.\d{1,2})?)")
# A simpler pure-pence extractor: tolerates "£7.99" or "&pound;7.99" (raw HTML)
# or just "7.99" depending on which DOM branch served the text.
_PRICE_NUMERIC_RE = re.compile(r"(\d+(?:\.\d{1,2})?)")

# Granular condition strings, in priority order. Matched against the lowercased
# heading text from offer rows ("Used - Very Good", "New", etc.).
# Mirrors bookfinder.py's _GRADE_TO_CONDITION. Will be unified across sources
# in the post-8.3 simplify pass (3 sources now exist).
_GRADE_TO_CONDITION: list[tuple[str, Condition]] = [
    ("like new", "used_vg"),
    ("very good", "used_vg"),
    ("good", "used_g"),
    ("acceptable", "used_acceptable"),
    ("fair", "used_acceptable"),
    ("poor", "used_acceptable"),
]

_BOT_MARKERS: tuple[str, ...] = (
    "Type the characters you see",
    "Robot Check",
    "To discuss automated access to Amazon",
    "validateCaptcha",
)


class AmazonUKInlineSource(InlineSource):
    """Amazon UK scraper backed by headless Chromium (Playwright).

    Tries the dp page first; falls back to the offer-listing page if the dp
    page has no usable buy-box. See module docstring for protocol details.
    """

    def __init__(
        self,
        name: str = "amazon",
        region: str = "UK",
        timeout_s: float = 30.0,
    ) -> None:
        if region.upper() != "UK":
            raise ValueError(
                f"AmazonUKInlineSource only supports region='UK' at MVP, got {region!r}"
            )
        self.name = name
        self.region = region
        self.timeout_s = timeout_s

    def dp_url(self, isbn13: str) -> str:
        return f"https://www.amazon.co.uk/dp/{isbn13}"

    def offer_listing_url(self, isbn13: str) -> str:
        return f"https://www.amazon.co.uk/gp/offer-listing/{isbn13}?condition=all"

    async def fetch(self, book: Book) -> list[ObservationCandidate]:
        dp = self.dp_url(book.isbn13)
        dp_html = await self._render(async_playwright, dp)
        offers = parse_dp(dp_html, dp)
        if offers:
            return offers

        ol = self.offer_listing_url(book.isbn13)
        ol_html = await self._render(async_playwright, ol)
        return parse_offer_listing(ol_html, ol)

    async def _render(self, playwright_factory, url: str) -> str:
        """Open headless Chromium, navigate to url, return rendered HTML.

        Split out so tests can monkeypatch the playwright_factory.
        """
        async with playwright_factory() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
            )
            try:
                context = await browser.new_context(
                    viewport={"width": 1366, "height": 768},
                    locale="en-GB",
                )
                page = await context.new_page()
                try:
                    await page.goto(
                        url, wait_until="domcontentloaded", timeout=self.timeout_s * 1000
                    )
                except PlaywrightTimeoutError as e:
                    raise SourceError(self.name, f"navigation timed out: {e}") from e
                # Wait for either a buy-box price or an AOD offer to appear.
                # Selector-timeout is fine — capture content and let the parser
                # decide whether to fall back / report empty.
                try:
                    await page.wait_for_selector(
                        "#corePriceDisplay_desktop_feature_div, "
                        "#corePrice_feature_div, "
                        "#aod-offer-list, "
                        ".olpOfferList, "
                        ".a-price .a-offscreen",
                        timeout=self.timeout_s * 1000,
                        state="attached",
                    )
                except PlaywrightTimeoutError:
                    pass
                html = await page.content()
            finally:
                await browser.close()

        for marker in _BOT_MARKERS:
            if marker in html:
                raise SourceError(
                    self.name,
                    "Amazon bot-protection challenge persisted; "
                    "Playwright was unable to clear it",
                )
        return html


def parse_dp(html: str, fallback_url: str) -> list[ObservationCandidate]:
    """Parse Amazon UK dp-page HTML into ObservationCandidates.

    Returns at most one offer (the buy-box). Empty list means no usable
    price was found — the caller should fall back to the offer-listing page.
    """
    if not html:
        return []
    tree = HTMLParser(html)

    # Hunt for the buy-box price container.
    price_node: Node | None = None
    for sel in (
        "#corePriceDisplay_desktop_feature_div",
        "#corePrice_feature_div",
        "#priceblock_ourprice",
        "#price",
    ):
        node = tree.css_first(sel)
        if node is not None:
            price_node = node
            break
    if price_node is None:
        return []

    price_minor = _extract_price_minor(price_node)
    if price_minor is None:
        return []

    seller = _extract_dp_seller(tree)

    return [
        ObservationCandidate(
            seller=seller,
            condition="new",  # Amazon dp buy-box defaults to new
            price_minor=price_minor,
            shipping_minor=None,
            currency="GBP",
            url=fallback_url,
        )
    ]


def parse_offer_listing(html: str, fallback_url: str) -> list[ObservationCandidate]:
    """Parse Amazon UK offer-listing-page HTML into ObservationCandidates."""
    if not html:
        return []
    tree = HTMLParser(html)

    # Modern AOD layout: #aod-offer rows under #aod-offer-list.
    # Legacy fallback: .olpOffer rows.
    rows = tree.css("#aod-offer-list #aod-offer")
    if not rows:
        rows = tree.css(".olpOffer")

    offers: list[ObservationCandidate] = []
    for row in rows:
        offer = _parse_offer_row(row, fallback_url)
        if offer is not None:
            offers.append(offer)
    return offers


def _parse_offer_row(row: Node, fallback_url: str) -> ObservationCandidate | None:
    price_minor = _extract_price_minor(row)
    if price_minor is None:
        return None

    shipping_minor = _extract_shipping_minor(row)
    condition = _extract_condition(row)
    seller = _extract_offer_seller(row)
    clickout = _extract_clickout(row, fallback_url)

    return ObservationCandidate(
        seller=seller,
        condition=condition,
        price_minor=price_minor,
        shipping_minor=shipping_minor,
        currency="GBP",
        url=clickout,
    )


def _extract_price_minor(scope: Node) -> int | None:
    """Pull the price in pence from a scope node.

    Prefers `.a-offscreen` (screen-reader-friendly full price like "£7.99").
    Falls back to combining `.a-price-whole` + `.a-price-fraction` if the
    offscreen text is missing or empty.
    """
    for off in scope.css(".a-price .a-offscreen"):
        text = (off.text() or "").strip()
        minor = _parse_gbp_to_minor(text)
        if minor is not None:
            return minor

    whole = scope.css_first(".a-price-whole")
    fraction = scope.css_first(".a-price-fraction")
    if whole is not None:
        try:
            whole_n = int(re.sub(r"[^0-9]", "", whole.text() or ""))
        except ValueError:
            return None
        frac_n = 0
        if fraction is not None:
            try:
                frac_n = int(re.sub(r"[^0-9]", "", fraction.text() or "") or "0")
            except ValueError:
                frac_n = 0
        # Normalise single-digit fractions ("9" -> 90 pence).
        if frac_n < 10:
            frac_n *= 10
        return whole_n * 100 + frac_n
    return None


def _parse_gbp_to_minor(text: str) -> int | None:
    """Parse "£7.99" / "&pound;7.99" / "7.99" → 799 (pence)."""
    if not text:
        return None
    m = _PRICE_NUMERIC_RE.search(text)
    if m is None:
        return None
    try:
        return round(float(m.group(1)) * 100)
    except ValueError:
        return None


def _extract_shipping_minor(row: Node) -> int | None:
    """Extract shipping cost in pence from an offer row; 0 for FREE."""
    ship_node = row.css_first("#aod-offer-shipping")
    if ship_node is None:
        return None
    text = (ship_node.text() or "").strip()
    if not text:
        return None
    if "free" in text.lower():
        return 0
    return _parse_gbp_to_minor(text)


def _extract_condition(row: Node) -> Condition:
    """Map the row's heading text ("Used - Very Good" etc.) to our enum."""
    heading = row.css_first("#aod-offer-heading h5")
    if heading is None:
        heading = row.css_first("#aod-offer-heading")
    if heading is None:
        return "unknown"
    text = (heading.text() or "").strip().lower()
    if not text:
        return "unknown"
    if "new" in text and "like new" not in text and "used" not in text:
        return "new"
    for needle, mapped in _GRADE_TO_CONDITION:
        if needle in text:
            return mapped
    return "unknown"


def _extract_offer_seller(row: Node) -> str:
    node = row.css_first("#aod-offer-soldBy a")
    if node is None:
        node = row.css_first("#aod-offer-soldBy")
    if node is None:
        return "?"
    return (node.text() or "").strip() or "?"


def _extract_clickout(row: Node, fallback_url: str) -> str:
    for anchor in row.css("a[href]"):
        href = anchor.attributes.get("href") or ""
        if not href:
            continue
        if href.startswith("http"):
            return href
        if href.startswith("/"):
            return "https://www.amazon.co.uk" + href
    return fallback_url


def _extract_dp_seller(tree: HTMLParser) -> str:
    node = tree.css_first("#merchant-info a")
    if node is not None:
        text = (node.text() or "").strip()
        if text:
            return text
    node = tree.css_first("#merchant-info")
    if node is not None:
        text = (node.text() or "").strip()
        if text:
            return text
    return "Amazon"
