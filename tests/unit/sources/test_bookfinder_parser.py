"""Unit tests for the bookfinder.com HTML parser.

Runs against the captured fixture (no network, no browser). Parser changes
that drop offers, mis-grade conditions, or fail to dedupe should be caught here.
"""

from pathlib import Path

import pytest

from book_alerter.sources.base import SourceError
from book_alerter.sources.bookfinder import parse_offers

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "bookfinder"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_parses_three_deduped_offers_from_gb_fixture() -> None:
    """Each offer card appears twice in the DOM (mobile + desktop). The parser
    must dedupe by data-test-id; the GB capture contains 3 unique used offers."""
    html = _load("9780747532699-gb-all.html")
    offers = parse_offers(html, fallback_url="https://www.bookfinder.com/search/")
    assert len(offers) == 3, [o.seller for o in offers]


def test_offer_fields_populated_correctly() -> None:
    html = _load("9780747532699-gb-all.html")
    offers = parse_offers(html, fallback_url="https://www.bookfinder.com/search/")

    # The EBAY offer is rank-1 (cheapest). Asserts on it pin both price extraction
    # (from inner text) and merchant attribution (from data-csa-c-affiliate +
    # data-csa-c-seller).
    ebay = next(o for o in offers if "EBAY" in o.seller)
    assert ebay.seller == "EBAY (alex.henderson7)"
    assert ebay.condition == "used_vg"  # "Condition: Used - Very Good" in card text
    # The card shows "£7.91 Includes shipping: £2.70" — the £7.91 is the
    # all-in total, not item-only. We split via the USD data attrs
    # (usdprice=7.04, usdshipping=3.65) so price + shipping reconstructs £7.91.
    assert ebay.price_minor == 521  # £5.21 item
    assert ebay.shipping_minor == 270  # £2.70 shipping
    assert ebay.price_minor + (ebay.shipping_minor or 0) == 791  # £7.91 total
    assert ebay.currency == "GBP"
    assert ebay.url.startswith("https://www.ebay.co.uk/itm/")


def test_biblio_offers_are_distinct_per_marketplace() -> None:
    """biblio.es and biblio.co.uk are separate clickout targets even though the
    underlying seller is the same. Dedup by data-test-id keeps them distinct."""
    html = _load("9780747532699-gb-all.html")
    offers = parse_offers(html, fallback_url="https://www.bookfinder.com/search/")
    biblios = [o for o in offers if "BIBLIO" in o.seller]
    assert len(biblios) == 2
    urls = {o.url for o in biblios}
    assert any("biblio.es" in u for u in urls)
    assert any("biblio.co.uk" in u for u in urls)
    for b in biblios:
        assert b.condition == "used_g"  # "Condition: Used - Good"
        # "£54.27 Includes shipping: £14.90" — split via usdprice=53.2,
        # usdshipping=20.14 at the same USD→GBP rate (0.7400).
        assert b.price_minor == 3937  # £39.37 item
        assert b.shipping_minor == 1490  # £14.90 shipping
        assert b.price_minor + (b.shipping_minor or 0) == 5427


def test_empty_html_returns_empty_list() -> None:
    """Truly empty input is degenerate and returns [] — only non-empty HTML
    that doesn't match the known search-page layout is treated as a WAF
    variant we couldn't recognise."""
    assert parse_offers("", fallback_url="https://x.example/") == []


def test_parse_offers_raises_on_unrecognized_page() -> None:
    """A non-empty page that lacks the search-form / nav / title markers is
    almost certainly an unknown WAF challenge variant whose substring isn't
    in `awsWafCookieDomainList` / `gokuProps` — raise rather than silently
    report 0 listings."""
    with pytest.raises(SourceError, match="search-results page did not match"):
        parse_offers("<html><body><p>nope</p></body></html>", fallback_url="https://x")
    with pytest.raises(SourceError, match="search-results page did not match"):
        parse_offers("<html></html>", fallback_url="https://x")


def test_parse_offers_returns_empty_when_recognized_page_has_no_cards() -> None:
    """A real Bookfinder page that genuinely has 0 listings still emits the
    nav / search-form markers. That case is "no copies found," NOT WAF —
    must return [] rather than raise."""
    html = (
        "<html><head><title>BookFinder.com: Search Results</title></head>"
        '<body><input id="book-search-input-desktop" /></body></html>'
    )
    assert parse_offers(html, fallback_url="https://x") == []


def test_free_shipping_card_without_usd_attrs_zeroes_shipping() -> None:
    """Bookfinder cards that say "Free shipping" without the USD data attributes
    (older/legacy DOM) should land as shipping_minor=0, not None, so totals
    don't silently drop the shipping signal."""
    html = """
    <div data-test-id="search-offer-card-NON-RENTAL-NEW-1"
         data-csa-c-affiliate="EXAMPLE"
         data-csa-c-seller="legacy-shop"
         data-csa-c-condition="NEW">
      <a href="https://example.com/listing">go</a>
      <span>£12.99</span><span>Free shipping</span>
    </div>
    """
    offers = parse_offers(html, fallback_url="https://x.example/")
    assert len(offers) == 1
    assert offers[0].price_minor == 1299
    assert offers[0].shipping_minor == 0


def _card(
    *,
    test_id: str = "search-offer-card-NON-RENTAL-USED-1",
    affiliate: str = "EXAMPLE",
    seller: str = "shop",
    condition: str = "USED",
    visible_total: str = "&pound;10.00",
    usd_price: str | None = None,
    usd_shipping: str | None = None,
    body_extra: str = "",
    href: str = "https://example.com/listing",
) -> str:
    """Construct a single bookfinder offer-card matching the real DOM contract.

    Live cards carry the data-csa-c-* attributes on the wrapping div; the
    visible total is rendered as bold inner text. Letting tests build minimal
    cards directly is faster + clearer than maintaining one large fixture for
    every code path.
    """
    attrs = [
        f'data-test-id="{test_id}"',
        f'data-csa-c-affiliate="{affiliate}"',
        f'data-csa-c-seller="{seller}"',
        f'data-csa-c-condition="{condition}"',
    ]
    if usd_price is not None:
        attrs.append(f'data-csa-c-usdprice="{usd_price}"')
    if usd_shipping is not None:
        attrs.append(f'data-csa-c-usdshipping="{usd_shipping}"')
    return (
        f'<div {" ".join(attrs)}>'
        f'<a href="{href}">go</a>'
        f'<span class="font-bold">{visible_total}</span>'
        f'{body_extra}'
        f'</div>'
    )


def test_paid_usd_shipping_splits_visible_total_via_ratio() -> None:
    """Paid USD shipping → split the visible local-currency total by the USD
    ratio so item + shipping reconstructs the user-visible total."""
    # usdprice=$53.20, usdshipping=$20.14, visible £54.27 total
    # rate = 5427 / 7334 = 0.7400 → shipping=£14.90, item=£39.37
    html = _card(
        affiliate="ABEBOOKS",
        seller="Some AbeBooks Store",
        usd_price="53.20",
        usd_shipping="20.14",
        visible_total="&pound;54.27",
        body_extra="<span>Includes shipping: &pound;14.90</span>",
    )
    offer = parse_offers(html, fallback_url="https://x.example/")[0]
    assert offer.seller == "ABEBOOKS (Some AbeBooks Store)"
    assert offer.price_minor == 3937  # £39.37 item
    assert offer.shipping_minor == 1490  # £14.90 shipping
    assert offer.price_minor + offer.shipping_minor == 5427


def test_zero_usd_shipping_yields_zero_shipping_minor() -> None:
    """usdshipping="0" → shipping_minor=0 (free) even without "Free shipping"
    text. This is the typical eBay-UK-with-free-postage shape."""
    html = _card(
        affiliate="EBAY",
        seller="baham_books",
        usd_price="29.09",
        usd_shipping="0",
        visible_total="&pound;29.09",
    )
    offer = parse_offers(html, fallback_url="https://x.example/")[0]
    assert offer.price_minor == 2909
    assert offer.shipping_minor == 0


def test_zero_usd_price_falls_back_to_text_path() -> None:
    """usdprice="0" is degenerate (no item to anchor the ratio) — we must NOT
    attribute the whole visible total to shipping. Fall through to text
    fallbacks instead. With "Free shipping" in the card body, that's 0."""
    html = _card(
        affiliate="ODDMARKET",
        seller="weird",
        usd_price="0",
        usd_shipping="5.00",
        visible_total="&pound;9.99",
        body_extra="<span>Free shipping</span>",
    )
    offer = parse_offers(html, fallback_url="https://x.example/")[0]
    assert offer.price_minor == 999  # visible total stays as item
    assert offer.shipping_minor == 0  # via free-shipping text fallback


def test_malformed_usd_attrs_falls_back_to_text_path() -> None:
    """Non-numeric data attrs should not crash the parser — fall back to
    the same text-extraction path used when the attrs are absent entirely."""
    html = _card(
        affiliate="ABEBOOKS",
        seller="quirky-shop",
        usd_price="not-a-number",
        usd_shipping="??",
        visible_total="&pound;15.00",
        body_extra="<span>Free shipping</span>",
    )
    offer = parse_offers(html, fallback_url="https://x.example/")[0]
    assert offer.price_minor == 1500
    assert offer.shipping_minor == 0


def test_includes_shipping_text_without_usd_attrs_splits_visible_total() -> None:
    """Legacy bookfinder cards without USD attrs but with the
    "Includes shipping: £X.XX" text should still split the total — otherwise
    we'd silently double-count shipping when scheduler.total computes
    price + shipping."""
    html = _card(
        affiliate="BIBLIO_CO_UK",
        seller="Legacy Books",
        visible_total="&pound;25.00",
        body_extra="<span>Includes shipping: &pound;3.50</span>",
    )
    offer = parse_offers(html, fallback_url="https://x.example/")[0]
    assert offer.price_minor == 2150  # £21.50 item
    assert offer.shipping_minor == 350  # £3.50 shipping
    assert offer.price_minor + offer.shipping_minor == 2500


def test_no_shipping_signal_leaves_shipping_minor_none() -> None:
    """Card with no USD attrs, no "Free shipping" text, and no
    "Includes shipping" line — we cannot fabricate; shipping must be None
    so the dashboard surfaces "—" and downstream knows we don't know."""
    html = _card(
        affiliate="MYSTERY",
        seller="unknown",
        visible_total="&pound;42.00",
    )
    offer = parse_offers(html, fallback_url="https://x.example/")[0]
    assert offer.price_minor == 4200
    assert offer.shipping_minor is None


def test_non_gbp_currency_split_still_balances() -> None:
    """Region=US fetches give $-denominated visible totals — the same ratio
    machinery applies (rate is 1.0 because both sides are USD). Verifies we
    don't accidentally hard-code GBP semantics in the split."""
    html = _card(
        affiliate="ABEBOOKS",
        seller="us-shop",
        usd_price="7.04",
        usd_shipping="3.65",
        visible_total="$10.69",
    )
    offer = parse_offers(html, fallback_url="https://x.example/")[0]
    assert offer.currency == "USD"
    assert offer.price_minor == 704
    assert offer.shipping_minor == 365
    assert offer.price_minor + offer.shipping_minor == 1069


def test_paid_shipping_does_not_match_free_shipping_text_in_other_card() -> None:
    """Regression: two cards in the same HTML — one with paid USD shipping,
    another with "Free shipping" text. Each must be parsed independently;
    the free-shipping text from card B must NOT bleed into card A's regex
    fallback path."""
    html = (
        _card(test_id="search-offer-card-NON-RENTAL-USED-A",
              affiliate="BIBLIO_ES", seller="paid-shop",
              usd_price="20.00", usd_shipping="5.00", visible_total="&pound;25.00")
        + _card(test_id="search-offer-card-NON-RENTAL-USED-B",
                affiliate="EBAY", seller="free-shop",
                visible_total="&pound;9.99", body_extra="<span>Free shipping</span>")
    )
    offers = parse_offers(html, fallback_url="https://x.example/")
    assert len(offers) == 2
    by_seller = {o.seller: o for o in offers}
    paid = by_seller["BIBLIO_ES (paid-shop)"]
    # rate = 2500 / 2500 = 1.0 → shipping=500, item=2000
    assert paid.shipping_minor == 500
    assert paid.price_minor == 2000
    free = by_seller["EBAY (free-shop)"]
    assert free.shipping_minor == 0
    assert free.price_minor == 999


def test_all_observations_use_valid_condition_enum() -> None:
    """Defends against drift: any new condition string bookfinder introduces
    must be mapped to one of the five Condition values; otherwise the parser
    must fall back to 'unknown' rather than emitting an out-of-enum literal."""
    html = _load("9780747532699-gb-all.html")
    offers = parse_offers(html, fallback_url="https://www.bookfinder.com/search/")
    valid = {"new", "used_vg", "used_g", "used_acceptable", "unknown"}
    for o in offers:
        assert o.condition in valid, f"unexpected condition {o.condition!r}"
        assert o.price_minor > 0
        assert o.currency in {"GBP", "USD", "EUR", "JPY"}
