from __future__ import annotations

import json
import re

import httpx
from selectolax.parser import HTMLParser

from book_alerter.db.models import Book
from book_alerter.http_client import shared_or_fresh
from book_alerter.sources.base import (
    Condition,
    ObservationCandidate,
    SourceError,
    TrackedItem,
)
from book_alerter.sources.condition_normalizers import condition_from_token
from book_alerter.sources.inline_source import InlineSource

# WoB (a Shopify storefront) embeds product data as a JS object literal
# `var meta = {...};` containing a `product.variants` array. Each variant has
# - public_title like "GB / VERY_GOOD / INTERNAL" (region / condition / supplier)
# - price in minor units (pence) as an integer
# - sku (empty for the placeholder variant with price 0)
#
# This is far more stable than CSS selectors against the rendered HTML.
#
# Variant availability is NOT in the `meta` blob — `var meta` happily lists
# discontinued / out-of-stock SKUs with their last-known price. The user can't
# actually buy those, so treating them as live offers produces phantom low
# prices on the dashboard and false BUY signals. The schema.org JSON-LD
# block on the same page carries per-SKU `availability` (InStock / OutOfStock);
# we cross-reference and drop any SKU that isn't InStock.
#
# Both blobs sit INSIDE `<script>` elements; we locate the elements via
# selectolax (proper HTML parsing) and then parse the contents — that's
# robust against Shopify template tweaks that change script attribute
# ordering, whitespace, or HTML entity escapes that a flat regex would
# trip over.
_META_VAR_RE = re.compile(r"\bvar\s+meta\s*=\s*\{")

# Positive page markers that confirm we landed on a real WoB product page
# (rather than a maintenance page, a generic Shopify 404, or a CDN error
# served with HTTP 200). Used to distinguish "real page, no listings" from
# "page we can't recognise" — without this check, a layout reshuffle that
# strips the meta blob would silently report 0 offers and bias percentiles.
_PRODUCT_PAGE_MARKERS: tuple[str, ...] = (
    'script[type="application/ld+json"]',
    'meta[property="og:type"][content="product"]',
    "#shopify-section-product-template",
)


def _extract_sku_availability(tree: HTMLParser) -> dict[str, bool]:
    """Return {sku: in_stock} from the page's schema.org Product offers.

    Missing / unparseable JSON-LD returns {} — callers should treat an
    unknown SKU as available rather than dropping every variant.
    """
    out: dict[str, bool] = {}
    for script in tree.css('script[type="application/ld+json"]'):
        text = (script.text() or "").strip()
        if not text:
            continue
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        for item in (data if isinstance(data, list) else [data]):
            if not isinstance(item, dict) or item.get("@type") != "Product":
                continue
            offers = item.get("offers")
            if isinstance(offers, list):
                offer_list = offers
            elif isinstance(offers, dict):
                offer_list = [offers]
            else:
                offer_list = []
            for o in offer_list:
                sku = o.get("sku") if isinstance(o, dict) else None
                if not sku:
                    continue
                avail = (o.get("availability") or "").lower()
                out[sku] = "instock" in avail
    return out


def _extract_meta_json(tree: HTMLParser) -> dict | None:
    """Return the parsed `var meta = {...};` object, or None.

    Shopify emits this as a top-level statement inside an inline script
    near the head of the page. We iterate script elements rather than
    grepping the whole page text so a `</script>`-string embedded in
    some unrelated inline JSON can't confuse the closing-tag boundary.
    Bracket-matching is still needed because the meta blob's `}` closes
    the JS variable assignment, not a structured DOM element.
    """
    for script in tree.css("script"):
        text = script.text() or ""
        m = _META_VAR_RE.search(text)
        if m is None:
            continue
        start = m.end() - 1  # rewind onto the opening `{`
        parsed = _parse_braced_json(text, start)
        if parsed is not None:
            return parsed
    return None


def _parse_braced_json(text: str, start: int) -> dict | None:
    """Walk `text[start:]` and return the JSON object whose opening `{`
    sits at `start`. Returns None on parse failure or unterminated input."""
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if esc:
            esc = False
            continue
        if in_str:
            if ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _is_recognized_product_page(tree: HTMLParser) -> bool:
    """Return True if the page carries any marker we know WoB always emits
    on a real product page render."""
    return any(tree.css_first(sel) is not None for sel in _PRODUCT_PAGE_MARKERS)


def _condition_from_title(public_title: str) -> Condition:
    """public_title is `REGION / CONDITION / SUPPLIER` — extract the middle slug."""
    parts = public_title.split("/")
    if len(parts) < 2:
        return "unknown"
    return condition_from_token(parts[1])


class WobInlineSource(InlineSource):
    def __init__(
        self,
        name: str = "wob",
        region: str = "UK",
        timeout_s: float = 30.0,
        *,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self.name = name
        self.region = region
        self.timeout_s = timeout_s
        self._http = http
        self._user_agent = (
            "Mozilla/5.0 (compatible; BookAlerter/0.0; +https://github.com/local/book_alerter)"
        )

    async def fetch(self, item: TrackedItem) -> list[ObservationCandidate]:
        assert isinstance(item, Book), f"{self.name} only handles books"
        book = item
        url = f"https://www.wob.com/en-gb/books/{book.isbn13}"
        headers = {
            "User-Agent": self._user_agent,
            "Accept-Language": "en-GB,en;q=0.9",
        }
        try:
            async with shared_or_fresh(self._http) as client:
                resp = await client.get(
                    url, headers=headers, timeout=self.timeout_s,
                )
        except httpx.HTTPError as e:
            raise SourceError(self.name, f"http error at {url}: {e}") from e

        if resp.status_code == 404:
            return []
        if resp.status_code >= 400:
            raise SourceError(self.name, f"http {resp.status_code} at {url}")

        # The final URL after redirects is the canonical product page (Shopify
        # rewrites `/books/<isbn>` -> `/products/<slug>-<isbn>`). Prefer that
        # in the ObservationCandidate so downstream links go to the real page.
        final_url = str(resp.url)
        return self._parse(resp.text, final_url)

    def _parse(self, html: str, url: str) -> list[ObservationCandidate]:
        tree = HTMLParser(html)
        meta = _extract_meta_json(tree)
        if meta is None:
            # No `var meta` blob found. Distinguish "real product page that
            # genuinely has no variants" (unlikely on WoB; still possible
            # for some metadata-only ISBNs) from "we landed on a page we
            # don't recognise" (Shopify maintenance, CDN error served with
            # 200, layout reshuffle). The former returns []; the latter
            # must raise rather than silently bias percentiles upward.
            if not _is_recognized_product_page(tree):
                raise SourceError(
                    self.name,
                    f"WoB product page did not match any known layout at {url} "
                    "(no `var meta`, no JSON-LD, no Shopify section markers); "
                    "treating as unknown variant rather than reporting 0 offers",
                )
            return []
        product = meta.get("product") or {}
        variants = product.get("variants") or []
        availability = _extract_sku_availability(tree)

        offers: list[ObservationCandidate] = []
        for v in variants:
            sku = (v.get("sku") or "").strip()
            price_minor = v.get("price")
            public_title = v.get("public_title") or ""

            # Skip the placeholder/no-supplier variant (typically "- / - / -",
            # empty SKU, price 0). It is not a real offer.
            if not sku or not isinstance(price_minor, int) or price_minor <= 0:
                continue

            # If we have explicit per-SKU availability from JSON-LD, drop
            # OutOfStock variants. If the SKU isn't in the availability map
            # (no JSON-LD, schema changed, etc.), default to keeping the
            # variant — better to publish a stale price than nothing.
            if sku in availability and not availability[sku]:
                continue

            condition = _condition_from_title(public_title)
            offers.append(
                ObservationCandidate(
                    seller="World of Books",
                    condition=condition,
                    price_minor=price_minor,
                    shipping_minor=0,
                    currency="GBP",
                    url=url,
                )
            )
        return offers
