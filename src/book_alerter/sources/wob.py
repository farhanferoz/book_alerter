from __future__ import annotations

import json
import re

import httpx

from book_alerter.db.models import Book
from book_alerter.sources.base import (
    Condition,
    ObservationCandidate,
    SourceError,
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
# prices on the dashboard and false BUY signals.  The schema.org JSON-LD
# block on the same page carries per-SKU `availability` (InStock / OutOfStock);
# we cross-reference and drop any SKU that isn't InStock.
_META_RE = re.compile(r"var\s+meta\s*=\s*(\{)", re.DOTALL)
_LDJSON_RE = re.compile(
    r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)


def _extract_sku_availability(html: str) -> dict[str, bool]:
    """Return {sku: in_stock} from the page's schema.org Product offers.

    Missing / unparseable JSON-LD returns {} — callers should treat an
    unknown SKU as available rather than dropping every variant.
    """
    out: dict[str, bool] = {}
    for raw in _LDJSON_RE.findall(html):
        try:
            data = json.loads(raw.strip())
        except json.JSONDecodeError:
            continue
        for item in (data if isinstance(data, list) else [data]):
            if not isinstance(item, dict) or item.get("@type") != "Product":
                continue
            offers = item.get("offers")
            offer_list = offers if isinstance(offers, list) else [offers] if isinstance(offers, dict) else []
            for o in offer_list:
                sku = o.get("sku") if isinstance(o, dict) else None
                if not sku:
                    continue
                avail = (o.get("availability") or "").lower()
                out[sku] = "instock" in avail
    return out


def _extract_meta_json(html: str) -> dict | None:
    """Find `var meta = {...};` and return the parsed object, or None."""
    m = _META_RE.search(html)
    if m is None:
        return None
    start = m.end() - 1
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(html)):
        ch = html[i]
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
                raw = html[start : i + 1]
                try:
                    return json.loads(raw)
                except json.JSONDecodeError:
                    return None
    return None


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

    async def fetch(self, book: Book) -> list[ObservationCandidate]:
        url = f"https://www.wob.com/en-gb/books/{book.isbn13}"
        headers = {
            "User-Agent": self._user_agent,
            "Accept-Language": "en-GB,en;q=0.9",
        }
        try:
            if self._http is not None:
                # Lifespan-scoped shared client — reuse the connection pool.
                # Per-call timeout/headers override the client defaults.
                resp = await self._http.get(
                    url, headers=headers, timeout=self.timeout_s,
                )
            else:
                async with httpx.AsyncClient(
                    timeout=self.timeout_s,
                    follow_redirects=True,
                    headers=headers,
                ) as client:
                    resp = await client.get(url)
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
        meta = _extract_meta_json(html)
        if meta is None:
            return []
        product = meta.get("product") or {}
        variants = product.get("variants") or []
        availability = _extract_sku_availability(html)

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
