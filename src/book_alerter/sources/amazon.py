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
from book_alerter.sources.condition_normalizers import condition_from_grade_text
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

# Tolerates "£7.99" / "&pound;7.99" / "7.99" — whichever DOM branch we end
# up reading.
_PRICE_NUMERIC_RE = re.compile(r"(\d+(?:\.\d{1,2})?)")

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
        ol = self.offer_listing_url(book.isbn13)
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
            )
            try:
                context = await browser.new_context(
                    viewport={"width": 1366, "height": 768},
                    locale="en-GB",
                )
                dp_html = await self._render_page(
                    context,
                    dp,
                    wait_selector=(
                        "#corePriceDisplay_desktop_feature_div, "
                        "#corePrice_feature_div, "
                        ".a-price .a-offscreen"
                    ),
                    # Buy-box renders fast; missing selector means no buy-box
                    # here, fall back rather than burn the full timeout.
                    wait_ms=min(10_000, int(self.timeout_s * 1000)),
                )
                offers = parse_dp(dp_html, dp)
                if offers:
                    return offers
                ol_html = await self._render_page(
                    context,
                    ol,
                    wait_selector="#aod-offer-list, .olpOfferList",
                    wait_ms=int(self.timeout_s * 1000),
                )
                return parse_offer_listing(ol_html, ol)
            finally:
                await browser.close()

    async def _render_page(
        self,
        context,
        url: str,
        *,
        wait_selector: str,
        wait_ms: int,
    ) -> str:
        """Open a page in `context`, navigate, return HTML after wait.

        Selector-timeout is fine — capture content and let the parser decide
        whether to fall back / report empty.
        """
        page = await context.new_page()
        try:
            await page.goto(
                url, wait_until="domcontentloaded", timeout=self.timeout_s * 1000
            )
        except PlaywrightTimeoutError as e:
            raise SourceError(self.name, f"navigation timed out: {e}") from e
        try:
            await page.wait_for_selector(wait_selector, timeout=wait_ms, state="attached")
        except PlaywrightTimeoutError:
            pass
        html = await page.content()
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

    price_node: Node | None = None
    for sel in (
        "#corePriceDisplay_desktop_feature_div",
        "#corePrice_feature_div",
        "#priceblock_ourprice",
        "#price",
    ):
        price_node = tree.css_first(sel)
        if price_node is not None:
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
    """Pull the price in pence from a `.a-price .a-offscreen` node under `scope`.

    Amazon's price markup always pairs the visible `.a-price-whole/.a-price-fraction`
    spans with a screen-reader `.a-offscreen` carrying the full "£7.99" string;
    we read the latter.
    """
    for off in scope.css(".a-price .a-offscreen"):
        minor = _parse_gbp_to_minor((off.text() or "").strip())
        if minor is not None:
            return minor
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
    heading = row.css_first("#aod-offer-heading h5") or row.css_first("#aod-offer-heading")
    return condition_from_grade_text(heading.text() or "") if heading else "unknown"


def _extract_offer_seller(row: Node) -> str:
    node = row.css_first("#aod-offer-soldBy a") or row.css_first("#aod-offer-soldBy")
    return _node_text(node) or "?"


def _extract_clickout(row: Node, fallback_url: str) -> str:
    for anchor in row.css("a[href]"):
        href = anchor.attributes.get("href") or ""
        if href.startswith("http"):
            return href
        if href.startswith("/"):
            return "https://www.amazon.co.uk" + href
    return fallback_url


def _extract_dp_seller(tree: HTMLParser) -> str:
    return (
        _node_text(tree.css_first("#merchant-info a"))
        or _node_text(tree.css_first("#merchant-info"))
        or "Amazon"
    )


def _node_text(node: Node | None) -> str:
    """Stripped inner text from a node, or '' if the node is None / empty."""
    return (node.text() or "").strip() if node is not None else ""
