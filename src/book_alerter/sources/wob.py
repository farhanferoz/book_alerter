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
from book_alerter.sources.inline_source import InlineSource


# WoB (a Shopify storefront) embeds product data as a JS object literal
# `var meta = {...};` containing a `product.variants` array. Each variant has
# - public_title like "GB / VERY_GOOD / INTERNAL" (region / condition / supplier)
# - price in minor units (pence) as an integer
# - sku (empty for the placeholder variant with price 0)
#
# This is far more stable than CSS selectors against the rendered HTML.
_META_RE = re.compile(r"var\s+meta\s*=\s*(\{)", re.DOTALL)

# Maps WoB's internal condition tokens (uppercased, underscore-separated) to
# our Condition literal. Discovered empirically from the captured cassette.
_CONDITION_MAP: dict[str, Condition] = {
    "NEW": "new",
    "LIKE_NEW": "used_vg",
    "VERY_GOOD": "used_vg",
    "GOOD": "used_g",
    "WELL_READ": "used_acceptable",
    "ACCEPTABLE": "used_acceptable",
}


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
    parts = [p.strip() for p in public_title.split("/")]
    if len(parts) < 2:
        return "unknown"
    token = parts[1].upper()
    return _CONDITION_MAP.get(token, "unknown")


class WobInlineSource(InlineSource):
    def __init__(self, name: str = "wob", region: str = "UK", timeout_s: float = 30.0) -> None:
        self.name = name
        self.region = region
        self.timeout_s = timeout_s
        self._user_agent = (
            "Mozilla/5.0 (compatible; BookAlerter/0.0; +https://github.com/local/book_alerter)"
        )

    async def fetch(self, book: Book) -> list[ObservationCandidate]:
        url = f"https://www.wob.com/en-gb/books/{book.isbn13}"
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_s,
                follow_redirects=True,
                headers={
                    "User-Agent": self._user_agent,
                    "Accept-Language": "en-GB,en;q=0.9",
                },
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

        offers: list[ObservationCandidate] = []
        for v in variants:
            sku = (v.get("sku") or "").strip()
            price_minor = v.get("price")
            public_title = v.get("public_title") or ""

            # Skip the placeholder/no-supplier variant (typically "- / - / -",
            # empty SKU, price 0). It is not a real offer.
            if not sku or not isinstance(price_minor, int) or price_minor <= 0:
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
