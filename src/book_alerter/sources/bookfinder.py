from __future__ import annotations

import re
import urllib.parse

from playwright.async_api import (
    BrowserContext,
)
from playwright.async_api import (
    TimeoutError as PlaywrightTimeoutError,
)
from selectolax.parser import HTMLParser, Node

from book_alerter.db.models import Book, Condition
from book_alerter.sources.base import (
    ObservationCandidate,
    SourceError,
    TrackedItem,
)
from book_alerter.sources.browser import BrowserSessionMixin
from book_alerter.sources.condition_normalizers import condition_from_grade_text
from book_alerter.sources.inline_source import InlineSource

# Bookfinder.com is fronted by AWS WAF and uses the `mp_verify` challenge variant
# which no static-cookie replay clears: the `aws-waf-token` cookie is bound to the
# originating TLS session, so exports from a real browser get rejected by the WAF
# the moment they're used from any other HTTP client (verified empirically against
# both stdlib Go and bogdanfinn/tls-client Chrome_133 profile). The only path that
# reliably reaches the price page is a real Chromium navigation, so this source
# drives headless Chromium via Playwright for every lookup.
#
# DOM contract (verified against tests/fixtures/bookfinder/*.html, captured
# 2026-05-14): each offer row is a div with
# `data-test-id="search-offer-card-{NON-RENTAL|RENTAL}-{NEW|USED}-{rank}"`
# and `data-csa-c-*` attributes carrying merchant / condition / USD price data.
# The same card appears twice in the DOM (mobile + desktop layouts) so we dedupe
# by data-test-id. The local-currency price is in the inner text (first
# `£X.XX` / `$X.XX` token).

_PRICE_RE = re.compile(r"([£$€¥])\s*(\d+(?:\.\d{1,2})?)")
_CONDITION_RE = re.compile(
    r"Condition:\s*(Used|New)(?:\s*-\s*([A-Za-z][A-Za-z\s]*))?",
    re.IGNORECASE,
)
_SHIPPING_LABEL_RE = re.compile(r"shipping:\s*", re.IGNORECASE)
_FREE_SHIPPING_RE = re.compile(r"free shipping", re.IGNORECASE)

_CURRENCY_FROM_SYMBOL: dict[str, str] = {"£": "GBP", "$": "USD", "€": "EUR", "¥": "JPY"}

# Positive selectors confirming we're looking at the expected Bookfinder
# search-results layout rather than an unknown variant. Used by parse_offers
# to distinguish "real page, just no matching listings" (return []) from
# "AWS WAF served a challenge variant we don't recognise" (raise) — the
# existing `awsWafCookieDomainList` / `gokuProps` markers in `_render`
# only catch the specific WAF challenges we've seen so far. Without a
# positive page check, any new WAF variant would silently report 0 offers.
_SEARCH_PAGE_MARKERS: tuple[str, ...] = (
    "#book-search-input-desktop",
    "#book-search-criteria",
    "#desktop-nav",
)
# Backup: the page <title> on every Bookfinder search-results render.
_SEARCH_PAGE_TITLE_FRAGMENT = "BookFinder.com:"


class BookfinderInlineSource(BrowserSessionMixin, InlineSource):
    """Bookfinder.com scraper backed by headless Chromium (Playwright).

    See module docstring for why a real browser is required (AWS WAF mp_verify).
    Region selects destination/currency: UK→GB/GBP, anything else→US/USD.
    Browser lifecycle comes from `BrowserSessionMixin` — the scheduler calls
    `prepare()`/`cleanup()` around a run; `fetch()` reuses the context
    `prepare()` opened rather than launching its own.
    """

    def __init__(
        self,
        name: str = "bookfinder",
        region: str = "UK",
        timeout_s: float = 30.0,
    ) -> None:
        self.name = name
        self.region = region
        self.timeout_s = timeout_s

    @property
    def _destination(self) -> str:
        return "GB" if self.region.upper() == "UK" else "US"

    @property
    def _currency(self) -> str:
        return "GBP" if self.region.upper() == "UK" else "USD"

    def search_url(self, isbn13: str) -> str:
        params = {
            "author": "",
            "binding": "ANY",
            "condition": "ANY",
            "currency": self._currency,
            "destination": self._destination,
            "firstEdition": "false",
            "isbn": "",
            "keywords": isbn13,
            "language": "EN",
            "maxPrice": "",
            "minPrice": "",
            "noIsbn": "false",
            "noPrintOnDemand": "false",
            "publicationMaxYear": "",
            "publicationMinYear": "",
            "publisher": "",
            "signed": "false",
            "title": "",
            "viewAll": "true",
            "searchOffersType": "*",
        }
        return "https://www.bookfinder.com/search/?" + urllib.parse.urlencode(params)

    async def fetch(self, item: TrackedItem) -> list[ObservationCandidate]:
        assert isinstance(item, Book), f"{self.name} only handles books"
        assert self._context is not None, (
            f"{self.name}.prepare() must run before fetch()"
        )
        book = item
        url = self.search_url(book.isbn13)
        html = await self._render(self._context, url)
        return parse_offers(html, url)

    async def _render(self, context: BrowserContext, url: str) -> str:
        """Open a page in `context`, navigate to url, return rendered HTML.

        Split out so tests can monkeypatch `_render` directly. `context` is
        the source's shared `BrowserSession` context (opened once per
        scheduler run by `BrowserSessionMixin.prepare()`), not a per-call
        browser launch.
        """
        page = await context.new_page()
        try:
            await page.goto(
                url, wait_until="domcontentloaded", timeout=self.timeout_s * 1000
            )
        except PlaywrightTimeoutError as e:
            raise SourceError(self.name, f"navigation timed out: {e}") from e
        # Wait for offers to render. Selector-timeout means the book has
        # no listings; capture content and let the parser report empty.
        try:
            await page.wait_for_selector(
                '[data-test-id^="search-offer-card-"]',
                timeout=self.timeout_s * 1000,
                state="attached",
            )
        except PlaywrightTimeoutError:
            pass
        html = await page.content()

        if "awsWafCookieDomainList" in html or "gokuProps" in html:
            raise SourceError(
                self.name,
                "AWS WAF challenge persisted; Playwright was unable to clear it",
            )
        return html


def parse_offers(html: str, fallback_url: str) -> list[ObservationCandidate]:
    """Parse bookfinder.com search-results HTML into ObservationCandidates.

    Public so tests can call it against fixture HTML without a browser.
    Cards appearing twice (mobile/desktop layouts) are deduped by data-test-id.
    """
    if not html:
        return []
    tree = HTMLParser(html)
    seen: set[str] = set()
    offers: list[ObservationCandidate] = []
    for card in tree.css('[data-test-id^="search-offer-card-"]'):
        tid = card.attributes.get("data-test-id") or ""
        if tid in seen:
            continue
        seen.add(tid)
        offer = _parse_card(card, fallback_url)
        if offer is not None:
            offers.append(offer)
    if not offers and not _is_recognized_search_page(tree, html):
        # No cards AND no recognised page structure — bookfinder.com almost
        # certainly served an unknown WAF challenge variant. Raise rather
        # than silently report 0 listings; see `_SEARCH_PAGE_MARKERS`.
        raise SourceError(
            "bookfinder",
            "search-results page did not match any known Bookfinder layout "
            "(no nav / search-form / title) — treating as WAF variant rather "
            "than reporting 0 offers",
        )
    return offers


def _is_recognized_search_page(tree: HTMLParser, html: str) -> bool:
    """Return True if `tree` / `html` carry any of the stable markers we
    know a real Bookfinder search-results page always emits."""
    if any(tree.css_first(sel) is not None for sel in _SEARCH_PAGE_MARKERS):
        return True
    return _SEARCH_PAGE_TITLE_FRAGMENT in html


def _parse_card(card: Node, fallback_url: str) -> ObservationCandidate | None:
    attrs = card.attributes
    base_condition = (attrs.get("data-csa-c-condition") or "").upper()
    affiliate = (attrs.get("data-csa-c-affiliate") or "").strip()
    seller_specific = (attrs.get("data-csa-c-seller") or "").strip()

    text = card.text()
    condition = _resolve_condition(text, base_condition)

    price_match = _PRICE_RE.search(text)
    if price_match is None:
        return None
    currency = _CURRENCY_FROM_SYMBOL.get(price_match.group(1), "USD")
    try:
        visible_total_minor = round(float(price_match.group(2)) * 100)
    except ValueError:
        return None

    # Bookfinder's prominent price is the all-in total — every card we've seen
    # either says "Includes shipping: £X.XX" or "Free shipping". The card div
    # carries authoritative USD splits in data-csa-c-usd{price,shipping};
    # convert them into local currency by re-using the visible total's exchange
    # rate so item-only + shipping equals the user-visible total.
    usd_price_raw = (attrs.get("data-csa-c-usdprice") or "").strip()
    usd_ship_raw = (attrs.get("data-csa-c-usdshipping") or "").strip()
    price_minor = visible_total_minor
    shipping_minor: int | None = None
    try:
        usd_price = float(usd_price_raw) if usd_price_raw else None
        usd_ship = float(usd_ship_raw) if usd_ship_raw else None
    except ValueError:
        usd_price = usd_ship = None
    if usd_price is not None and usd_price > 0 and usd_ship is not None and usd_ship >= 0:
        rate = visible_total_minor / ((usd_price + usd_ship) * 100)
        shipping_minor = round(usd_ship * 100 * rate)
        price_minor = visible_total_minor - shipping_minor
    elif _FREE_SHIPPING_RE.search(text):
        shipping_minor = 0
    else:
        ship_match = _SHIPPING_LABEL_RE.search(text)
        if ship_match is not None:
            tail = text[ship_match.end():]
            ship_price_match = _PRICE_RE.match(tail.lstrip())
            if ship_price_match is not None:
                try:
                    shipping_minor = round(float(ship_price_match.group(2)) * 100)
                    price_minor = visible_total_minor - shipping_minor
                except ValueError:
                    shipping_minor = None

    # Guard against a parser quirk that would persist a negative item price.
    # If shipping ended up larger than the visible total (rounding amplification
    # in the USD-split branch, or a misread tail price in the text branch),
    # roll back to "we don't know the split" rather than emitting an absurd
    # negative item price downstream.
    if shipping_minor is not None and price_minor < 0:
        price_minor = visible_total_minor
        shipping_minor = None

    clickout = fallback_url
    for anchor in card.css("a[href]"):
        href = anchor.attributes.get("href") or ""
        if href.startswith("http"):
            clickout = href
            break

    return ObservationCandidate(
        seller=_format_seller(affiliate, seller_specific),
        condition=condition,
        price_minor=price_minor,
        shipping_minor=shipping_minor,
        delivery_text=_extract_delivery_text(text),
        currency=currency,
        url=clickout,
    )


def _extract_delivery_text(card_text: str) -> str | None:
    """The card's raw shipping-label text fragment (T1.5 diagnostic
    capture), independent of the shipping_minor computation above.

    There's no separate DOM node for this on Bookfinder cards — the label
    lives inline in the card's flattened text — so this reuses the exact
    same `_FREE_SHIPPING_RE` / `_SHIPPING_LABEL_RE` patterns `_parse_card`
    matches shipping_minor with, so whatever gets captured always matches
    what got parsed. Returns None when neither pattern matches (e.g. the
    USD-split branch, which computes shipping_minor from data attributes
    rather than reading a label at all).
    """
    free_match = _FREE_SHIPPING_RE.search(card_text)
    if free_match is not None:
        return free_match.group(0)
    label_match = _SHIPPING_LABEL_RE.search(card_text)
    if label_match is not None:
        tail = card_text[label_match.end():].lstrip()
        price_match = _PRICE_RE.match(tail)
        if price_match is not None:
            return f"{label_match.group(0)}{price_match.group(0)}"
    return None


def _resolve_condition(card_text: str, base: str) -> Condition:
    """Map bookfinder's `Condition: <base> - <grade>` text to our enum.

    Falls back to the data-csa-c-condition attribute when grade is absent.
    """
    m = _CONDITION_RE.search(card_text)
    if m is None:
        return "new" if base == "NEW" else "unknown"
    base_word = (m.group(1) or "").lower()
    grade = (m.group(2) or "")
    if base_word == "new":
        return "new"
    return condition_from_grade_text(grade)


def _format_seller(affiliate: str, specific: str) -> str:
    if affiliate and specific:
        return f"{affiliate} ({specific})"
    return affiliate or specific or "?"
