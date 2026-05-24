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

from book_alerter.enums import Condition
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


def test_parse_delivery_text_handles_amazon_canonical_values() -> None:
    """Direct unit tests for the delivery-text parser.

    The function is the single shipping-extraction point used by every
    AOD row, so each input shape gets a dedicated assertion.
    """
    from book_alerter.sources.amazon import _parse_delivery_text

    # Empty / unparseable
    assert _parse_delivery_text("") is None
    assert _parse_delivery_text("   ") is None
    assert _parse_delivery_text("Arrives Tuesday") is None

    # Free
    assert _parse_delivery_text("FREE") == 0
    assert _parse_delivery_text("free") == 0
    assert _parse_delivery_text("FREE delivery Wednesday, 27 May.") == 0
    assert _parse_delivery_text("Free delivery 28 – 29 May") == 0

    # Concrete numeric
    assert _parse_delivery_text("£2.80 delivery") == 280
    assert _parse_delivery_text("£3.49 delivery Friday") == 349
    assert _parse_delivery_text("£10 delivery") == 1000

    # Thousands separator — never seen on shipping in practice but the
    # regex should not silently truncate it.
    assert _parse_delivery_text("£1,200.00 delivery") == 120000
    assert _parse_delivery_text("£1,234.56 delivery") == 123456


def test_parse_delivery_text_conditional_free_promise_uses_concrete_charge() -> None:
    """Regression for the substring-`free` trap: a conditional promise
    like "£3.49 delivery. Free delivery on orders over £25" must yield
    the concrete £3.49 charge, NOT zero. The prior version accepted any
    "free" substring as zero shipping, silently dropping a real charge.
    """
    from book_alerter.sources.amazon import _parse_delivery_text

    assert _parse_delivery_text(
        "£3.49 delivery. Free delivery on orders over £25.",
    ) == 349
    assert _parse_delivery_text("£5.00 delivery (or FREE over £25)") == 500


def test_extract_dp_condition_non_resale_seller_defaults_to_new() -> None:
    """Substring "warehouse" appearing in a legitimate marketplace name
    must NOT trigger the Used classification — only the literal
    Amazon-owned resale brands should."""
    from selectolax.parser import HTMLParser

    from book_alerter.sources.amazon import _extract_dp_condition

    tree = HTMLParser("<html><body></body></html>")
    assert _extract_dp_condition(tree, "Warehouse Books Ltd") == Condition.NEW
    assert _extract_dp_condition(tree, "Wholesale Resale Group") == Condition.NEW
    assert _extract_dp_condition(tree, "Amazon") == Condition.NEW
    assert _extract_dp_condition(tree, "BookCurl") == Condition.NEW


def test_extract_dp_condition_amazon_resale_with_no_caption_defaults_to_used_vg() -> None:
    """Captureless edge case: when Amazon serves "Amazon Resale" but the
    grade caption is missing (rare A/B variant), fall back to USED_VG
    rather than NEW so the percentile distribution stays consistent."""
    from selectolax.parser import HTMLParser

    from book_alerter.sources.amazon import _extract_dp_condition

    tree = HTMLParser("<html><body></body></html>")
    assert _extract_dp_condition(tree, "Amazon Resale") == Condition.USED_VG
    assert _extract_dp_condition(tree, " amazon warehouse ") == Condition.USED_VG


def test_extract_dp_condition_amazon_resale_with_new_caption_text_still_used() -> None:
    """If the caption text reads as NEW for any reason (HTML drift,
    A/B test), we've already decided this is a Resale row by
    seller-name match — reporting NEW would be contradictory. Stay on
    the USED_VG default."""
    from selectolax.parser import HTMLParser

    from book_alerter.sources.amazon import _extract_dp_condition

    html = (
        '<div id="usedAccordionCaption_feature_div">'
        '<span class="a-text-bold">New</span></div>'
    )
    tree = HTMLParser(html)
    assert _extract_dp_condition(tree, "Amazon Resale") == Condition.USED_VG


def test_parse_dp_used_buybox_returns_used_condition() -> None:
    """Regression for `parse_dp` hardcoding `condition=NEW`.

    Live capture 2026-05-23 of ASIN 0241638194: Amazon's buy-box was
    served by "Amazon Resale" with a Used – Like New copy at £28.60.
    The previous implementation reported this as `condition=NEW`,
    silently inflating the percentile distribution of "new" prices.
    After the fix, `Amazon Resale` should resolve to a used grade.
    """
    html = _load("9780241638194-uk-dp-used-buybox-2026-05-23.html")
    offers = parse_dp(html, fallback_url="https://www.amazon.co.uk/dp/0241638194")
    assert len(offers) == 1
    o = offers[0]
    assert o.seller == "Amazon Resale"
    assert o.condition == Condition.USED_VG, (
        f"expected Used grade, got {o.condition!r}"
    )
    assert o.price_minor == 2860
    assert o.shipping_minor == 0


def test_parse_dp_returns_empty_when_no_price_block() -> None:
    html = _load("9780747532699-uk-dp-no-price.html")
    offers = parse_dp(html, fallback_url="https://www.amazon.co.uk/dp/9780747532699")
    assert offers == []


def test_parse_dp_priceamount_fallback_prefers_buybox_scope() -> None:
    """Defends against drift: if a `"priceAmount":XX` from an unrelated
    rail (frequently-bought-together, recommended titles) ever appears
    BEFORE the buy-box block in the raw HTML, the fallback regex must
    still pick the buy-box price — not the rail price. The
    `twister-plus-buying-options-price-data` div is the buy-box-specific
    JSON container; scoping the regex to it prevents the drift.
    """
    # No DOM-side `.a-offscreen` price (CSS path returns None) so the
    # fallback regex is what actually decides the answer. The stray
    # `"priceAmount":99.99` ahead of the buy-box data div is what the
    # un-scoped regex used to latch onto.
    html = (
        '<html><body>'
        '<div id="dp-container">'
        '<div id="productTitle">Some Book</div>'
        '<script>{"frequentlyBoughtTogether":{"priceAmount":99.99}}</script>'
        '<div class="a-section aok-hidden twister-plus-buying-options-price-data">'
        '{"desktop_buybox_group_1":[{"displayPrice":"£12.34","priceAmount":12.34}]}'
        '</div>'
        '</div></body></html>'
    )
    offers = parse_dp(html, fallback_url="https://www.amazon.co.uk/dp/X")
    assert len(offers) == 1
    assert offers[0].price_minor == 1234  # £12.34, the buy-box price


def test_parse_dp_priceamount_fallback_when_buybox_div_missing() -> None:
    """If the `twister-plus-buying-options-price-data` div is absent (older
    layout / A/B variant), the regex falls back to the full HTML — better
    to capture *some* price than to drop the path entirely. Covers the
    backward-compatibility branch in `_extract_priceamount_minor`.
    """
    html = (
        '<html><body>'
        '<div id="dp-container">'
        '<div id="productTitle">Some Book</div>'
        '<script>{"priceAmount":7.50}</script>'
        '</div></body></html>'
    )
    offers = parse_dp(html, fallback_url="https://www.amazon.co.uk/dp/X")
    assert len(offers) == 1
    assert offers[0].price_minor == 750


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


def test_parse_offer_listing_real_capture_shipping_extracted_for_all_rows() -> None:
    """Regression for the always-broken `#aod-offer-shipping` selector
    that left 80% of marketplace offers with shipping_minor=None.

    Real-Amazon-UK AOD HTML (captures from 2026-05-16 and 2026-05-23)
    renders the delivery cost as `<span data-csa-c-delivery-price>`
    inside `.aod-delivery-promise` — the legacy `#aod-offer-shipping`
    selector matches zero of these. After the fix, every offer on the
    real captures must yield a concrete `shipping_minor` (0 for FREE).
    Failing this test means a parser regression has reintroduced the
    NULL-shipping bug across the fleet.
    """
    for fname in (
        "9780241638194-uk-offer-listing-real.html",
        "9780241638194-uk-offer-listing-live-2026-05-23.html",
    ):
        html = _load(fname)
        offers = parse_offer_listing(
            html, fallback_url="https://www.amazon.co.uk/dp/0241638194"
        )
        assert offers, f"{fname} produced no offers"
        nulls = [o for o in offers if o.shipping_minor is None]
        assert not nulls, (
            f"{fname}: {len(nulls)}/{len(offers)} offers have shipping_minor=None; "
            f"first bad row seller={nulls[0].seller!r}"
        )


def test_parse_offer_listing_clickout_not_shipping_help() -> None:
    """Regression for the offer clickout returning Amazon's shipping-help
    page (`/gp/help/customer/display.html?nodeId=GQ6B6RH72AX8D2TD`)
    instead of the offer page. The shipping-help anchor sits first in
    every modern AOD row, so an earlier "first anchor wins" logic picked
    it up. No offer URL should contain `/gp/help/customer/`.
    """
    for fname in (
        "9780241638194-uk-offer-listing-real.html",
        "9780241638194-uk-offer-listing-live-2026-05-23.html",
    ):
        html = _load(fname)
        offers = parse_offer_listing(
            html, fallback_url="https://www.amazon.co.uk/dp/0241638194"
        )
        bad = [o for o in offers if "/gp/help/customer/" in o.url]
        assert not bad, (
            f"{fname}: {len(bad)}/{len(offers)} offers point at the shipping-help "
            f"page; first bad row seller={bad[0].seller!r} url={bad[0].url!r}"
        )


def test_parse_offer_listing_clickout_is_offer_listing_page() -> None:
    """Every AOD row's clickout is the offer-listing page, never the seller
    storefront.

    Regression for the live bug where the Amazon Resale current-best offer
    linked to `/Amazon-Warehouse-Deals/b?node=...` — the Resale "storefront"
    is a generic category landing page with no sign of the book. Amazon AOD
    rows carry no stable per-offer deep link, so we link to the offer-listing
    page, which shows this offer in context alongside the others.
    """
    listing_url = "https://www.amazon.co.uk/gp/offer-listing/0241638194?condition=all"
    html = _load("9780241638194-uk-offer-listing-real.html")
    offers = parse_offer_listing(html, fallback_url=listing_url)
    assert offers
    for o in offers:
        assert o.url == listing_url, (
            f"seller {o.seller!r} resolved to {o.url!r}, not the offer-listing page"
        )
    # The Resale row in particular must not leak the Warehouse-Deals storefront.
    assert not any("Warehouse-Deals" in o.url for o in offers)


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
