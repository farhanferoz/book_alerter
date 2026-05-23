"""Unit tests for the Amazon UK HTML parsers.

Runs against synthetic fixtures hand-crafted from Amazon's public dp +
AOD (offer-listing) markup. Live capture was blocked by Amazon's
anti-bot (verified 2026-05-14), so the fixture HTML reflects the DOM
contract documented in `src/book_alerter/sources/amazon.py` rather than
a live snapshot. When the live capture path works in the future the
fixtures should be regenerated and these tests revisited.
"""

from pathlib import Path

import pytest

from book_alerter.sources.amazon import _merge_offers, parse_dp, parse_offer_listing
from book_alerter.sources.base import ObservationCandidate, SourceError

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "amazon"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_parse_dp_returns_buybox_offer() -> None:
    html = _load("9780747532699-uk-dp.html")
    offers = parse_dp(html, fallback_url="https://www.amazon.co.uk/dp/9780747532699")
    assert len(offers) == 1
    o = offers[0]
    assert o.price_minor == 799  # £7.99
    assert o.currency == "GBP"
    assert o.condition == "new"
    assert "amazon.co.uk" in o.url
    assert o.seller and o.seller != "?"


def test_parse_dp_no_delivery_block_yields_shipping_none() -> None:
    """The base synthetic fixture has no delivery markup — the parser must
    return shipping_minor=None rather than fabricating a value."""
    html = _load("9780747532699-uk-dp.html")
    offers = parse_dp(html, fallback_url="https://www.amazon.co.uk/dp/9780747532699")
    assert len(offers) == 1
    assert offers[0].shipping_minor is None


def test_parse_dp_free_delivery_block_yields_zero_shipping() -> None:
    """"FREE delivery Monday, 18 May" → shipping_minor=0. Markup shape
    captured from a real Amazon UK dp page 2026-05-15."""
    html = _load("9780747532699-uk-dp-free-delivery.html")
    offers = parse_dp(html, fallback_url="https://www.amazon.co.uk/dp/9780747532699")
    assert len(offers) == 1
    assert offers[0].shipping_minor == 0


def test_parse_dp_paid_delivery_block_yields_numeric_shipping() -> None:
    """"£2.80 delivery Tuesday, 19 May" → shipping_minor=280."""
    html = _load("9780747532699-uk-dp-paid-delivery.html")
    offers = parse_dp(html, fallback_url="https://www.amazon.co.uk/dp/9780747532699")
    assert len(offers) == 1
    assert offers[0].shipping_minor == 280


def test_parse_dp_returns_empty_when_no_price_block() -> None:
    html = _load("9780747532699-uk-dp-no-price.html")
    offers = parse_dp(html, fallback_url="https://www.amazon.co.uk/dp/9780747532699")
    assert offers == []


def test_parse_offer_listing_returns_all_rows() -> None:
    html = _load("9780747532699-uk-offer-listing.html")
    offers = parse_offer_listing(
        html, fallback_url="https://www.amazon.co.uk/gp/offer-listing/9780747532699"
    )
    assert len(offers) == 4
    assert all(o.currency == "GBP" for o in offers)
    assert all(o.price_minor > 0 for o in offers)


def test_parse_offer_listing_spot_check_used_good_row() -> None:
    html = _load("9780747532699-uk-offer-listing.html")
    offers = parse_offer_listing(
        html, fallback_url="https://www.amazon.co.uk/gp/offer-listing/9780747532699"
    )
    wob = next(o for o in offers if o.seller and "WorldOfBooks" in o.seller)
    assert wob.condition == "used_g"
    assert wob.price_minor == 399  # £3.99
    assert wob.shipping_minor == 280  # £2.80
    assert wob.currency == "GBP"
    assert "amazon.co.uk" in wob.url


def test_parse_offer_listing_condition_mapping() -> None:
    html = _load("9780747532699-uk-offer-listing.html")
    offers = parse_offer_listing(
        html, fallback_url="https://www.amazon.co.uk/gp/offer-listing/9780747532699"
    )
    by_seller = {o.seller: o for o in offers}
    assert by_seller["Amazon"].condition == "new"
    assert by_seller["BetterWorldBooksUK"].condition == "used_vg"  # Used - Like New
    assert by_seller["WorldOfBooks Ltd"].condition == "used_g"
    assert by_seller["MusicMagpie"].condition == "used_acceptable"


def test_parse_offer_listing_shipping_free_is_zero() -> None:
    html = _load("9780747532699-uk-offer-listing.html")
    offers = parse_offer_listing(
        html, fallback_url="https://www.amazon.co.uk/gp/offer-listing/9780747532699"
    )
    amazon_row = next(o for o in offers if o.seller == "Amazon")
    assert amazon_row.shipping_minor == 0


def test_empty_html_returns_empty_list() -> None:
    """Truly empty input is degenerate and returns [] — only non-empty HTML
    that doesn't match any known Amazon layout is treated as anti-bot."""
    assert parse_dp("", fallback_url="https://x.example/") == []
    assert parse_offer_listing("", fallback_url="https://x.example/") == []


def test_parse_dp_raises_on_unrecognized_page() -> None:
    """A non-empty page that lacks both `#dp-container` and `#productTitle`
    is almost certainly an anti-bot variant whose substring isn't in
    BOT_MARKERS — raise rather than silently report 0 offers."""
    with pytest.raises(SourceError, match="dp page did not match"):
        parse_dp("<html><body><p>nope</p></body></html>", fallback_url="https://x")
    with pytest.raises(SourceError, match="dp page did not match"):
        parse_dp("<html></html>", fallback_url="https://x")


def test_parse_offer_listing_raises_on_unrecognized_page() -> None:
    """Same guarantee for the offer-listing path: missing both AOD and OLP
    containers means we did not reach the expected Amazon layout."""
    with pytest.raises(SourceError, match="offer-listing page did not match"):
        parse_offer_listing("<html><body><p>nope</p></body></html>", fallback_url="https://x")
    with pytest.raises(SourceError, match="offer-listing page did not match"):
        parse_offer_listing("<html></html>", fallback_url="https://x")


def test_parse_dp_returns_empty_when_recognized_page_has_no_price() -> None:
    """The dp-no-price fixture has `#productTitle` but no price block —
    that's a legitimate "out of stock / no buy-box" state, NOT an anti-bot
    page. Must return [] (no raise)."""
    html = _load("9780747532699-uk-dp-no-price.html")
    assert parse_dp(html, fallback_url="https://www.amazon.co.uk/dp/9780747532699") == []


def test_parse_offer_listing_returns_empty_when_recognized_page_has_no_rows() -> None:
    """A page that has `#aod-container` (or any other listing marker) but
    no `#aod-offer` rows is a genuine "no listings" state — return []
    rather than raise."""
    html = (
        "<html><body>"
        '<div id="aod-container">'
        '<div id="aod-offer-list"></div>'
        "</div></body></html>"
    )
    assert parse_offer_listing(html, fallback_url="https://x") == []


def test_all_observations_use_valid_condition_enum() -> None:
    """Defends against drift: any condition string we don't recognise should
    map to 'unknown' rather than emitting an out-of-enum literal."""
    valid = {"new", "used_vg", "used_g", "used_acceptable", "unknown"}
    dp_html = _load("9780747532699-uk-dp.html")
    for o in parse_dp(dp_html, fallback_url="https://x.example/"):
        assert o.condition in valid
    ol_html = _load("9780747532699-uk-offer-listing.html")
    for o in parse_offer_listing(ol_html, fallback_url="https://x.example/"):
        assert o.condition in valid


def _cand(
    seller: str,
    condition: str,
    price_minor: int,
    shipping_minor: int | None = None,
) -> ObservationCandidate:
    return ObservationCandidate(
        seller=seller,
        condition=condition,  # type: ignore[arg-type]
        price_minor=price_minor,
        shipping_minor=shipping_minor,
        currency="GBP",
        url="https://x.example/",
    )


def test_merge_dedups_overlapping_offer_preferring_concrete_shipping() -> None:
    """The dp buy-box (shipping_minor=None) duplicates a row on the
    offer-listing page (shipping_minor=0). The merged result must collapse
    the pair to a single row carrying the concrete shipping value."""
    dp_row = _cand("Amazon", "new", 799, shipping_minor=None)
    ol_amazon = _cand("Amazon", "new", 799, shipping_minor=0)
    ol_wob = _cand("WorldOfBooks Ltd", "used_g", 399, shipping_minor=280)

    merged = _merge_offers([dp_row, ol_amazon, ol_wob])

    assert len(merged) == 2
    by_seller = {o.seller: o for o in merged}
    assert by_seller["Amazon"].shipping_minor == 0
    assert by_seller["WorldOfBooks Ltd"].price_minor == 399


def test_merge_treats_different_conditions_as_distinct() -> None:
    """Same seller + same price but different conditions = different offers.
    Don't collapse them."""
    new = _cand("X", "new", 799, shipping_minor=0)
    used = _cand("X", "used_g", 799, shipping_minor=280)

    merged = _merge_offers([new, used])

    assert len(merged) == 2


def test_merge_seller_match_is_case_and_whitespace_insensitive() -> None:
    """Amazon sometimes returns the seller as " Amazon " or "amazon" across
    page templates. Treat them as the same seller for dedup purposes."""
    dp_row = _cand(" Amazon ", "new", 799, shipping_minor=None)
    ol_row = _cand("amazon", "new", 799, shipping_minor=0)

    merged = _merge_offers([dp_row, ol_row])

    assert len(merged) == 1
    assert merged[0].shipping_minor == 0


def test_merge_preserves_dp_row_when_no_offer_listing_match() -> None:
    """dp row with no counterpart on offer-listing survives the merge — the
    "Amazon's offer doesn't appear in the marketplace AOD" case."""
    dp_row = _cand("Amazon", "new", 1234, shipping_minor=None)
    ol_other = _cand("BookCurl", "used_vg", 4074, shipping_minor=0)

    merged = _merge_offers([dp_row, ol_other])

    assert len(merged) == 2
    sellers = {o.seller for o in merged}
    assert sellers == {"Amazon", "BookCurl"}


def test_merge_keeps_first_when_both_shipping_values_concrete() -> None:
    """Two concrete shipping values for the same (seller, condition, price)
    means the same offer reported twice with the same level of detail —
    preserve insertion order rather than ping-ponging."""
    first = _cand("X", "new", 799, shipping_minor=0)
    second = _cand("X", "new", 799, shipping_minor=280)

    merged = _merge_offers([first, second])

    assert len(merged) == 1
    assert merged[0].shipping_minor == 0


def test_merge_empty_input_returns_empty() -> None:
    assert _merge_offers([]) == []


# --- Real-HTML offer-listing fixture (captured 2026-05-16) -------------------
# Guards the modern apex pricing template: offer price in
# `.apex-pricetopay-accessibility-label` (sibling of `.a-price`), strike-through
# RRP in `.a-price.apex-basisprice-value`, seller on `aria-label="X. Opens a
# new page"`, heading in a `<span>` (not `<h5>`).

REAL_OL = "9780241638194-uk-offer-listing-real.html"


def test_real_offer_listing_parses_offer_price_not_rrp() -> None:
    """The page renders £50.00 as the strike-through RRP on every offer row;
    the actual offer prices range £25.66 – £40.74. A naive `.a-price
    .a-offscreen` scan would lock onto the £50 strike-through and miss the
    real price entirely. This test pins us to the offer price."""
    html = _load(REAL_OL)
    offers = parse_offer_listing(html, fallback_url="https://x.example/")
    assert len(offers) == 10
    totals = sorted(o.price_minor for o in offers)
    assert totals[0] == 2566  # cheapest = £25.66 Used - Like New
    assert totals[-1] == 4074  # most expensive = £40.74
    # The £50 RRP must NOT appear as any offer's price.
    assert 5000 not in totals


def test_real_offer_listing_surfaces_used_grades() -> None:
    """Two of the ten rows on this page are 'Used - Like New'. The previous
    parser collapsed every row to condition=new because its heading
    selector targeted `<h5>` while real Amazon ships a `<span>`."""
    html = _load(REAL_OL)
    offers = parse_offer_listing(html, fallback_url="https://x.example/")
    by_cond: dict[str, int] = {}
    for o in offers:
        by_cond[o.condition] = by_cond.get(o.condition, 0) + 1
    assert by_cond.get("used_vg", 0) == 2
    assert by_cond.get("new", 0) == 8


def test_real_offer_listing_seller_has_no_sold_by_prefix() -> None:
    """Amazon-direct rows render the seller as a `<span>` (no `<a>`),
    which used to fall through to the whole-div text and emit
    'Sold by      Amazon' with a string of internal whitespace. The
    aria-label-based selector returns the seller's name cleanly."""
    html = _load(REAL_OL)
    offers = parse_offer_listing(html, fallback_url="https://x.example/")
    sellers = [o.seller for o in offers]
    for s in sellers:
        assert s is not None
        assert not s.lower().startswith("sold by"), (
            f"seller {s!r} leaks the 'Sold by' label"
        )
        # No internal multi-space whitespace from a div-text extraction.
        assert "  " not in s, f"seller {s!r} has runs of internal whitespace"
    assert "Amazon" in sellers  # Amazon-direct row resolved to bare "Amazon"


def test_real_offer_listing_amazon_resale_used_row_has_correct_price() -> None:
    """Spot check on the row that the user's screenshot called out: the
    Amazon Resale 'Used - Like New' offer at £25.66 — the cheapest row
    on the page and exactly the kind of offer the prior dp-first scraper
    flow missed entirely."""
    html = _load(REAL_OL)
    offers = parse_offer_listing(html, fallback_url="https://x.example/")
    amazon_resale = [o for o in offers if o.seller == "Amazon Resale"]
    assert len(amazon_resale) == 1
    o = amazon_resale[0]
    assert o.condition == "used_vg"
    assert o.price_minor == 2566
