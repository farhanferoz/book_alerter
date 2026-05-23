"""Unit tests for the WoB parser path (`WobInlineSource._parse`).

Constructs synthetic Shopify-shaped HTML inline rather than depending on
the VCR cassettes — the cassettes cover the live-HTTP integration, these
tests cover the parser branches (positive markers, raise vs return [],
SKU availability cross-referencing) without a network round trip.
"""
from __future__ import annotations

import pytest

from book_alerter.db.models import Book
from book_alerter.sources.base import SourceError
from book_alerter.sources.wob import WobInlineSource

_VAR_META = """
var meta = {
  "product": {
    "variants": [
      {"sku": "SKU-NEW",  "price": 1500, "public_title": "GB / NEW / SUPPLIER_A"},
      {"sku": "SKU-USED", "price": 800,  "public_title": "GB / GOOD / SUPPLIER_B"},
      {"sku": "",         "price": 0,    "public_title": "- / - / -"}
    ]
  }
};
"""

_LD_JSON_BOTH_IN_STOCK = """
{"@type":"Product","offers":[
  {"@type":"Offer","sku":"SKU-NEW","availability":"https://schema.org/InStock"},
  {"@type":"Offer","sku":"SKU-USED","availability":"https://schema.org/InStock"}
]}
"""

_LD_JSON_USED_OUT_OF_STOCK = """
{"@type":"Product","offers":[
  {"@type":"Offer","sku":"SKU-NEW","availability":"https://schema.org/InStock"},
  {"@type":"Offer","sku":"SKU-USED","availability":"https://schema.org/OutOfStock"}
]}
"""


def _wrap(body: str) -> str:
    return f"<!doctype html><html><head></head><body>{body}</body></html>"


def _book() -> Book:
    return Book(isbn13="9781800816015", title="Sparta", author="Andrew Bayliss")


def _parse(html: str):
    return WobInlineSource(region="UK")._parse(html, url="https://x/wob/")


def test_parse_returns_offers_for_valid_meta_with_both_in_stock() -> None:
    html = _wrap(
        f'<script type="application/ld+json">{_LD_JSON_BOTH_IN_STOCK}</script>'
        f'<script>{_VAR_META}</script>'
    )
    offers = _parse(html)
    assert len(offers) == 2
    by_condition = {o.condition: o for o in offers}
    assert by_condition["new"].price_minor == 1500
    assert by_condition["used_g"].price_minor == 800
    for o in offers:
        assert o.shipping_minor == 0
        assert o.currency == "GBP"
        assert o.seller == "World of Books"


def test_parse_drops_out_of_stock_skus() -> None:
    """The JSON-LD InStock/OutOfStock signal must filter `var meta` variants —
    `var meta` happily lists discontinued SKUs with their last-known price."""
    html = _wrap(
        f'<script type="application/ld+json">{_LD_JSON_USED_OUT_OF_STOCK}</script>'
        f'<script>{_VAR_META}</script>'
    )
    offers = _parse(html)
    assert len(offers) == 1
    assert offers[0].condition == "new"
    assert offers[0].price_minor == 1500


def test_parse_keeps_variant_when_availability_unknown() -> None:
    """If JSON-LD doesn't mention a SKU, we keep the variant rather than
    drop every offer — better to publish a stale price than nothing.

    Note: a page with NO JSON-LD at all would also fail the bot-marker
    guarantee unless another positive marker is present, so this test
    pins down the "JSON-LD missing this SKU but Shopify section present"
    branch by including the section marker explicitly.
    """
    html_with_marker = _wrap(
        '<div id="shopify-section-product-template"></div>'
        f'<script>{_VAR_META}</script>'
    )
    offers = _parse(html_with_marker)
    assert len(offers) == 2  # both kept; availability map was empty


def test_parse_raises_on_unrecognized_page() -> None:
    """A 200 OK response with NO `var meta`, NO JSON-LD, and NO Shopify
    section markers is almost certainly a maintenance page or CDN error
    served with HTTP 200 — raise rather than silently report 0 offers."""
    html = _wrap("<p>service unavailable</p>")
    with pytest.raises(SourceError, match="WoB product page did not match"):
        _parse(html)


def test_parse_returns_empty_when_recognized_page_has_no_meta() -> None:
    """A recognisable WoB layout (Shopify section markers present) with no
    `var meta` blob is "real page, just no variants" — return []."""
    html = _wrap('<div id="shopify-section-product-template"></div>')
    assert _parse(html) == []


def test_parse_handles_unparseable_ld_json_block() -> None:
    """A malformed JSON-LD block (Shopify template bug, partial render)
    must not break parsing — the empty availability map just means we
    fall back to keeping every variant."""
    html = _wrap(
        '<script type="application/ld+json">{this is not json</script>'
        f'<script>{_VAR_META}</script>'
    )
    offers = _parse(html)
    assert len(offers) == 2  # malformed LD-JSON → empty availability → keep all


def test_parse_handles_multiple_ld_json_blocks() -> None:
    """Shopify pages often emit several LD-JSON blocks (Breadcrumb, Organization,
    Product). The parser must scan all and pick out the Product entry — not
    just the first block."""
    breadcrumb = '{"@type":"BreadcrumbList","itemListElement":[]}'
    html = _wrap(
        f'<script type="application/ld+json">{breadcrumb}</script>'
        f'<script type="application/ld+json">{_LD_JSON_USED_OUT_OF_STOCK}</script>'
        f'<script>{_VAR_META}</script>'
    )
    offers = _parse(html)
    assert len(offers) == 1
    assert offers[0].condition == "new"


def test_parse_ignores_placeholder_zero_price_variant() -> None:
    """The `- / - / -` placeholder variant (empty sku, price 0) is not a
    real listing — it represents a Shopify product with no supplier yet."""
    html = _wrap(
        f'<script type="application/ld+json">{_LD_JSON_BOTH_IN_STOCK}</script>'
        f'<script>{_VAR_META}</script>'
    )
    offers = _parse(html)
    # Placeholder is dropped; only the 2 real variants remain.
    assert len(offers) == 2
    assert all(o.price_minor > 0 for o in offers)
