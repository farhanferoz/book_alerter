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

import book_alerter.sources.browser as browser_mod
from book_alerter.enums import Condition
from book_alerter.sources.amazon import _merge_offers, parse_dp, parse_offer_listing
from book_alerter.sources.base import ObservationCandidate, SourceError

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "amazon"
PRODUCT_FIXTURES = FIXTURES / "products"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_parse_dp_returns_buybox_offer() -> None:
    html = _load("9780747532699-uk-dp-2026-05-14.html")
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
    html = _load("9780747532699-uk-dp-2026-05-14.html")
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


def test_parse_delivery_text_spend_threshold_is_not_read_as_a_charge() -> None:
    """S7 regression: "FREE delivery ... over £35" used to read the £35
    THRESHOLD as a £35.00 delivery CHARGE — `_DELIVERY_PRICE_GBP_RE` has no
    way to tell "the charge is £X" from "free once you spend over £X" on
    its own; both are just a bare £-amount to it. Verbatim wording from
    tests/fixtures/amazon/products/B0CYT8WL1G-uk-dp-2026-09-04.html.

    Masked in production today because `_extract_shipping_minor` reads the
    short `data-csa-c-delivery-price` attribute (just "FREE" or "£X.XX",
    never a threshold mention) before ever calling this function on the
    long sentence — this protects the fallback path for when that
    attribute is absent (the legacy `#aod-offer-shipping`-only layout, or
    a future template variant), which no capture on file exercises today.
    """
    from book_alerter.sources.amazon import _parse_delivery_text

    assert _parse_delivery_text(
        "FREE delivery Tuesday, 8 September on orders dispatched by Amazon over £35"
    ) == 0
    assert _parse_delivery_text("FREE delivery on orders over £10") == 0
    assert _parse_delivery_text("Free delivery ON ORDERS OVER  £ 25.00") == 0
    # A genuine charge earlier in the same sentence must still win over a
    # LATER threshold mention — the exclusion is per-match, not "give up
    # entirely if the word 'over' appears anywhere in the text".
    assert _parse_delivery_text(
        "£3.49 delivery. Free delivery on orders over £35."
    ) == 349


def test_dp_and_aod_paths_agree_on_free_vs_charge_precedence() -> None:
    """F-B9/D36: `_extract_dp_shipping_minor` (the dp path) used to check
    "FREE delivery" BEFORE the £-amount — the opposite of D36's ratified
    numeric-first precedence, and the opposite of what `_parse_delivery_
    text` (the AOD path) already did correctly. On a combined sentence
    like "£3.49 delivery. Free delivery over £25.", the dp path collapsed
    to 0 (a real charge masked as free, then converted to `None` by the
    conditional-shipping rule — a cascade estimate instead of the known
    £3.49) while the AOD path correctly read 349. This asserts both paths
    now return 349 for the identical sentence."""
    from selectolax.parser import HTMLParser

    from book_alerter.sources.amazon import _extract_dp_shipping_minor, _parse_delivery_text

    sentence = "£3.49 delivery. Free delivery over £25."

    html = (
        '<div id="mir-layout-DELIVERY_BLOCK-slot-PRIMARY_DELIVERY_MESSAGE_LARGE">'
        + sentence
        + "</div>"
    )
    tree = HTMLParser(html)
    assert _extract_dp_shipping_minor(tree) == 349
    assert _parse_delivery_text(sentence) == 349


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


def test_extract_dp_condition_forwards_source_name_to_grade_unmapped_log() -> None:
    """F-B8: `condition_normalizers.grade_unmapped`'s `source` field is
    the diagnostic D26 exists for ("log the raw grade whenever mapping
    fails, so the real distribution becomes visible") — every call site
    defaulting to "unspecified" makes it useless for telling which
    scraper produced an unmapped grade. `_extract_dp_condition` (the dp
    used-buybox caption path) must forward its own `source_name`."""
    from selectolax.parser import HTMLParser
    from structlog.testing import capture_logs

    from book_alerter.sources.amazon import _extract_dp_condition

    html = (
        '<div id="usedAccordionCaption_feature_div">'
        '<span class="a-text-bold">Ex-Library</span></div>'
    )
    tree = HTMLParser(html)
    with capture_logs() as logs:
        result = _extract_dp_condition(tree, "Amazon Resale", source_name="amazon_uk_product")

    # "Ex-Library" doesn't map to a used grade, so the USED_VG default
    # applies — the log line, not the return value, is what this tests.
    assert result == Condition.USED_VG
    warnings = [e for e in logs if e["log_level"] == "warning"]
    assert len(warnings) == 1, logs
    assert warnings[0]["event"] == "condition_normalizers.grade_unmapped"
    assert warnings[0]["source"] == "amazon_uk_product"


def test_extract_condition_forwards_source_name_to_grade_unmapped_log() -> None:
    """F-B8: same as the dp test above, for the AOD offer-row heading
    path (`_extract_condition`)."""
    from selectolax.parser import HTMLParser
    from structlog.testing import capture_logs

    from book_alerter.sources.amazon import _extract_condition

    row = HTMLParser(
        '<div><div id="aod-offer-heading"><h5>Ex-Library</h5></div></div>'
    ).css_first("div")
    with capture_logs() as logs:
        result = _extract_condition(row, source_name="amazon_uk_product")

    assert result == "unknown"
    warnings = [e for e in logs if e["log_level"] == "warning"]
    assert len(warnings) == 1, logs
    assert warnings[0]["event"] == "condition_normalizers.grade_unmapped"
    assert warnings[0]["source"] == "amazon_uk_product"


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
        "9780241638194-uk-offer-listing-2026-05-16.html",
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
        "9780241638194-uk-offer-listing-2026-05-16.html",
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
    html = _load("9780241638194-uk-offer-listing-2026-05-16.html")
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
    dp_html = _load("9780747532699-uk-dp-2026-05-14.html")
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


def test_merge_dedups_overlapping_offer_prefers_unknown_over_a_disagreeing_zero() -> None:
    """S8 (2026-09-04): this test used to assert the OPPOSITE outcome
    ("the merged result must collapse the pair to a single row carrying
    the concrete shipping value", i.e. 0) — that was correct pre-T2.5, when
    a `None` only ever meant "we didn't observe shipping" and a concrete
    `0` was strictly more informative. Since T2.5/D33/D35, `None` can also
    mean "we positively confirmed this promise is conditional", and
    preferring a disagreeing `0` in that case would silently discard
    exactly the finding those fixes exist to surface — the `0` might
    itself just be a miss on this particular render. Flipped deliberately,
    reported rather than silently adjusted: see the D35 follow-up
    (S6/S7/S8) commit for the full reasoning."""
    dp_row = _cand("Amazon", "new", 799, shipping_minor=None)
    ol_amazon = _cand("Amazon", "new", 799, shipping_minor=0)
    ol_wob = _cand("WorldOfBooks Ltd", "used_g", 399, shipping_minor=280)

    merged = _merge_offers([dp_row, ol_amazon, ol_wob])

    assert len(merged) == 2
    by_seller = {o.seller: o for o in merged}
    assert by_seller["Amazon"].shipping_minor is None
    assert by_seller["WorldOfBooks Ltd"].price_minor == 399


def test_merge_still_prefers_a_genuine_nonzero_charge_over_unknown() -> None:
    """S8's OTHER half, unchanged from before: a real non-zero charge
    (e.g. from a hydration-skeletoned dp slot's sibling AOD row) is still
    strictly more informative than an honest "we don't know" — T2.5/D33/
    D35 never null out a non-zero shipping value, only exactly 0, so a
    disagreement against a non-zero value was never the case S8 is about."""
    dp_row = _cand("Amazon", "new", 799, shipping_minor=None)
    ol_amazon = _cand("Amazon", "new", 799, shipping_minor=280)

    merged = _merge_offers([dp_row, ol_amazon])

    assert len(merged) == 1
    assert merged[0].shipping_minor == 280


def test_merge_treats_different_conditions_as_distinct() -> None:
    """Same seller + same price but different conditions = different offers.
    Don't collapse them."""
    new = _cand("X", "new", 799, shipping_minor=0)
    used = _cand("X", "used_g", 799, shipping_minor=280)

    merged = _merge_offers([new, used])

    assert len(merged) == 2


def test_merge_seller_match_is_case_and_whitespace_insensitive() -> None:
    """Amazon sometimes returns the seller as " Amazon " or "amazon" across
    page templates. Treat them as the same seller for dedup purposes.

    The shipping assertion below only proves the two rows actually merged
    into one (len==1); WHICH shipping value survives is S8's concern, not
    this test's — see test_merge_dedups_overlapping_offer_prefers_unknown_
    over_a_disagreeing_zero for that. Updated from `== 0` to `is None` in
    the same S8 commit since this fixture happens to hit that exact
    None-vs-0 boundary too.
    """
    dp_row = _cand(" Amazon ", "new", 799, shipping_minor=None)
    ol_row = _cand("amazon", "new", 799, shipping_minor=0)

    merged = _merge_offers([dp_row, ol_row])

    assert len(merged) == 1
    assert merged[0].shipping_minor is None


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

REAL_OL = "9780241638194-uk-offer-listing-2026-05-16.html"


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


# --- T2.7: dp seller attribution --------------------------------------------


def test_extract_dp_seller_returns_none_when_merchant_info_absent() -> None:
    """Previously defaulted to "Amazon" whenever #merchant-info was
    missing — that credited an unattributed buy-box to Amazon on no
    evidence at all."""
    from selectolax.parser import HTMLParser

    from book_alerter.sources.amazon import _extract_dp_seller

    tree = HTMLParser('<html><body><div id="dp-container"></div></body></html>')
    assert _extract_dp_seller(tree) is None


def test_extract_dp_seller_returns_none_when_merchant_info_textless() -> None:
    from selectolax.parser import HTMLParser

    from book_alerter.sources.amazon import _extract_dp_seller

    tree = HTMLParser('<html><body><div id="merchant-info">   </div></body></html>')
    assert _extract_dp_seller(tree) is None


def test_extract_dp_seller_reads_merchant_info_when_present() -> None:
    from selectolax.parser import HTMLParser

    from book_alerter.sources.amazon import _extract_dp_seller

    tree = HTMLParser(
        '<html><body><div id="merchant-info"><a>BookCurl</a></div></body></html>'
    )
    assert _extract_dp_seller(tree) == "BookCurl"


def test_parse_dp_seller_still_amazon_when_merchant_info_present() -> None:
    """Positive-control regression guard: the ordinary Harry Potter dp
    fixture DOES render #merchant-info as "Amazon" — the T2.7 fix must
    only change the absent/textless case, not this one."""
    html = _load("9780747532699-uk-dp-2026-05-14.html")
    offers = parse_dp(html, fallback_url="https://www.amazon.co.uk/dp/9780747532699")
    assert len(offers) == 1
    assert offers[0].seller == "Amazon"


def test_parse_dp_seller_none_on_echo_dot_fixture_with_no_merchant_info() -> None:
    """T2.7 regression on real captured markup: the Echo Dot dp page (an
    actual Amazon-brand device — evidence T2.7 was landed against) has
    zero #merchant-info nodes. Before this fix the buy-box was wrongly
    attributed to "Amazon"; now it's None, and unattributed sellers must
    never be classified as an Amazon resale brand either."""
    html = (PRODUCT_FIXTURES / "B09B96TG33-uk-dp-2026-09-04.html").read_text(
        encoding="utf-8"
    )
    offers = parse_dp(html, fallback_url="https://www.amazon.co.uk/dp/B09B96TG33")
    assert len(offers) == 1
    assert offers[0].seller is None
    assert offers[0].condition == Condition.NEW


def test_parse_dp_item_title_and_image_populated_on_real_product_fixture() -> None:
    """T4.1: parse_dp extracts #productTitle / a cover image so
    scheduler._persist can resolve a still-PENDING product's placeholder
    title without waiting on the metadata_refresh job. Same Echo Dot
    fixture as the T2.7 test above — it has a real #productTitle and
    #landingImage regardless of the #merchant-info gap that test covers."""
    html = (PRODUCT_FIXTURES / "B09B96TG33-uk-dp-2026-09-04.html").read_text(
        encoding="utf-8"
    )
    offers = parse_dp(html, fallback_url="https://www.amazon.co.uk/dp/B09B96TG33")
    assert len(offers) == 1
    assert offers[0].item_title is not None
    assert "Echo Dot" in offers[0].item_title
    assert offers[0].item_image_url is not None
    assert offers[0].item_image_url.startswith("https://")


def test_parse_offer_listing_never_sets_item_title() -> None:
    """AOD rows never carry a title/image — only the dp buy-box page does.
    Loads a fixture with real offer rows so this isn't vacuously true over
    an empty list."""
    html = _load("9780241638194-uk-offer-listing-2026-05-16.html")
    offers = parse_offer_listing(
        html, fallback_url="https://www.amazon.co.uk/gp/offer-listing/9780241638194"
    )
    assert offers, "fixture produced no offers to assert over"
    assert all(o.item_title is None and o.item_image_url is None for o in offers)


# --- T1.5: delivery_text diagnostic capture ---------------------------------


def test_parse_dp_delivery_text_populated_on_free_and_paid_fixtures() -> None:
    free_html = _load("9780747532699-uk-dp-free-delivery.html")
    free_offers = parse_dp(free_html, fallback_url="https://www.amazon.co.uk/dp/x")
    assert free_offers[0].delivery_text is not None
    assert "FREE delivery" in free_offers[0].delivery_text

    paid_html = _load("9780747532699-uk-dp-paid-delivery.html")
    paid_offers = parse_dp(paid_html, fallback_url="https://www.amazon.co.uk/dp/x")
    assert paid_offers[0].delivery_text is not None
    assert "£2.80 delivery" in paid_offers[0].delivery_text


def test_parse_dp_delivery_text_none_when_no_delivery_block() -> None:
    html = _load("9780747532699-uk-dp-2026-05-14.html")
    offers = parse_dp(html, fallback_url="https://www.amazon.co.uk/dp/x")
    assert offers[0].delivery_text is None


def test_parse_offer_listing_delivery_text_populated() -> None:
    """Every row on the real offer-listing fixture must carry the raw
    delivery-promise text alongside the parsed shipping_minor — this is
    the field T2.5's future conditional-promo rule keys on."""
    html = _load(REAL_OL)
    offers = parse_offer_listing(html, fallback_url="https://x.example/")
    assert offers
    for o in offers:
        assert o.delivery_text is not None, f"{o.seller} row has no delivery_text"


# --- T1.5: debug capture on bot-challenge / unrecognised layout ------------


def test_parse_dp_raises_and_writes_debug_capture_on_unrecognized_page(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(browser_mod, "_DEBUG_ROOT", tmp_path)
    html = "<html><body><p>nope</p></body></html>"

    with pytest.raises(SourceError, match="dp page did not match"):
        parse_dp(html, fallback_url="https://x", source_name="amazon")

    dumps = list((tmp_path / "amazon").glob("*.html"))
    assert len(dumps) == 1
    assert dumps[0].read_text(encoding="utf-8") == html


def test_parse_offer_listing_raises_and_writes_debug_capture_on_unrecognized_page(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(browser_mod, "_DEBUG_ROOT", tmp_path)
    html = "<html><body><p>nope</p></body></html>"

    with pytest.raises(SourceError, match="offer-listing page did not match"):
        parse_offer_listing(html, fallback_url="https://x", source_name="amazon_uk_product")

    dumps = list((tmp_path / "amazon_uk_product").glob("*.html"))
    assert len(dumps) == 1
    assert dumps[0].read_text(encoding="utf-8") == html


# --- T2.5/D33: conditional delivery promises (first-order + spend-threshold)


def test_conditional_free_shipping_to_unknown_matches_the_first_order_marker() -> None:
    from book_alerter.sources.amazon import _conditional_free_shipping_to_unknown

    # The exact verbatim wording confirmed live (wave0 probe + the
    # B0F3NVWM37 fixture below) — must become unknown, not stay free.
    assert _conditional_free_shipping_to_unknown(
        0, "FREE delivery 19 - 23 October on your first order to UK or Ireland."
    ) is None
    # Case-insensitive, and tolerant of the surrounding wording varying.
    assert _conditional_free_shipping_to_unknown(
        0, "Free Delivery Monday, 18 May ON YOUR FIRST ORDER to UK or Ireland."
    ) is None
    assert _conditional_free_shipping_to_unknown(
        0, "on your first order"
    ) is None


def test_conditional_free_shipping_to_unknown_matches_the_spend_threshold_marker() -> None:
    """D33: a second conditional-promo pattern, found incidentally while
    covering a fixture for T4.2 and confirmed as the same defect class as
    F1/T2.5, not folded in silently. The rule keys on conditionality
    ("over £<anything>"), not on the £35 value itself — Q2 (whether the
    real threshold is £35 general or £10 of books) stays open and is
    deliberately not load-bearing here."""
    from book_alerter.sources.amazon import _conditional_free_shipping_to_unknown

    # Verbatim wording confirmed live (B0CYT8WL1G-uk-dp-2026-09-04.html).
    assert _conditional_free_shipping_to_unknown(
        0, "FREE delivery Tuesday, 8 September on orders dispatched by Amazon over £35"
    ) is None
    # A different threshold value must match just as well — the rule is
    # "conditional on a spend threshold", not "conditional on £35".
    assert _conditional_free_shipping_to_unknown(
        0, "FREE delivery on orders over £10"
    ) is None
    # Case-insensitive, tolerant of spacing around the £ sign.
    assert _conditional_free_shipping_to_unknown(
        0, "Free delivery ON ORDERS OVER  £ 25.00"
    ) is None


def test_conditional_free_shipping_to_unknown_leaves_genuine_cases_alone() -> None:
    """The other half of the fix: conflating "free" with "unknown" would
    be a different bug of the same size. A genuinely unconditional free
    offer must stay 0, and a paid charge must never be touched regardless
    of what the text says (the function only ever acts on shipping==0).
    Covers both evidenced markers, not just the first one."""
    from book_alerter.sources.amazon import _conditional_free_shipping_to_unknown

    assert _conditional_free_shipping_to_unknown(0, "FREE delivery Monday, 18 May.") == 0
    assert _conditional_free_shipping_to_unknown(0, None) == 0
    assert _conditional_free_shipping_to_unknown(None, None) is None
    # A paid charge mentioning "over £X" (e.g. "£2.80 delivery, free over
    # £25") must stay untouched — the guard is "shipping_minor == 0", not
    # "text contains a marker", on purpose.
    assert _conditional_free_shipping_to_unknown(
        280, "£2.80 delivery on your first order to UK or Ireland."
    ) == 280
    assert _conditional_free_shipping_to_unknown(
        280, "£2.80 delivery. Free delivery on orders over £25."
    ) == 280


def test_parse_offer_listing_first_order_promo_becomes_unknown_shipping() -> None:
    """THE T2.5 acceptance test — this is the proof the S1 bug (finding F1,
    8 of 9 offers on a live page recorded as free shipping when the promise
    was a first-order-only promo) is fixed.

    Real capture, B0F3NVWM37, 10 genuine AOD offer rows: 8 read "FREE
    delivery ... on your first order to UK or Ireland" and must come back
    with shipping_minor=None (unknown -> effective_shipping falls back to
    the cascade estimate). The remaining 2 rows are the control: one
    genuinely unconditional "FREE delivery" (no marker) must stay 0, and
    one real paid charge ("£2.99 delivery") must stay 299 untouched.
    """
    html = (PRODUCT_FIXTURES / "B0F3NVWM37-uk-aod-2026-09-04.html").read_text(
        encoding="utf-8"
    )
    offers = parse_offer_listing(
        html, fallback_url="https://x.example/", source_name="amazon_uk_product"
    )
    assert len(offers) == 10

    conditional = [
        o for o in offers
        if o.delivery_text and "on your first order" in o.delivery_text.lower()
    ]
    assert len(conditional) == 8, "expected 8 of 10 real rows to carry the promo"
    assert all(o.shipping_minor is None for o in conditional), (
        "every first-order-promo row must report unknown shipping, not free"
    )

    unconditional_free = [
        o for o in offers
        if o.delivery_text
        and o.delivery_text.lower().startswith("free delivery")
        and "on your first order" not in o.delivery_text.lower()
    ]
    assert len(unconditional_free) == 1
    assert unconditional_free[0].shipping_minor == 0, (
        "a genuinely unconditional free offer must NOT be conflated with unknown"
    )

    paid = [o for o in offers if o.delivery_text and o.delivery_text.startswith("£")]
    assert len(paid) == 1
    assert paid[0].shipping_minor == 299, "a real paid charge must be untouched"


def test_parse_dp_first_order_promo_becomes_unknown_shipping() -> None:
    """T2.5 applies wherever delivery_text is captured, not just the
    offer-listing path — the dp buy-box is exposed to the exact same
    promo."""
    html = _load("9780747532699-uk-dp-conditional-delivery.html")
    offers = parse_dp(html, fallback_url="https://www.amazon.co.uk/dp/9780747532699")
    assert len(offers) == 1
    o = offers[0]
    assert o.delivery_text is not None
    assert "on your first order" in o.delivery_text.lower()
    assert o.shipping_minor is None

    # Control: the pre-existing free/paid dp fixtures (no conditional
    # wording) must be completely unaffected by this change.
    free_html = _load("9780747532699-uk-dp-free-delivery.html")
    free_offers = parse_dp(free_html, fallback_url="https://www.amazon.co.uk/dp/x")
    assert free_offers[0].shipping_minor == 0

    paid_html = _load("9780747532699-uk-dp-paid-delivery.html")
    paid_offers = parse_dp(paid_html, fallback_url="https://www.amazon.co.uk/dp/x")
    assert paid_offers[0].shipping_minor == 280


def test_parse_dp_spend_threshold_promo_becomes_unknown_shipping() -> None:
    """D33, books side: the spend-threshold conditional promo (found on
    the products side, B0CYT8WL1G) applies equally to books' dp buy-box —
    a single sub-threshold book doesn't qualify for "free over £35" either."""
    html = _load("9780747532699-uk-dp-spend-threshold-delivery.html")
    offers = parse_dp(html, fallback_url="https://www.amazon.co.uk/dp/9780747532699")
    assert len(offers) == 1
    o = offers[0]
    assert o.delivery_text is not None
    assert "over £35" in o.delivery_text.lower()
    assert o.shipping_minor is None

    # Same controls as the first-order test above — this marker addition
    # must not touch the genuinely free/paid fixtures either.
    free_html = _load("9780747532699-uk-dp-free-delivery.html")
    free_offers = parse_dp(free_html, fallback_url="https://www.amazon.co.uk/dp/x")
    assert free_offers[0].shipping_minor == 0

    paid_html = _load("9780747532699-uk-dp-paid-delivery.html")
    paid_offers = parse_dp(paid_html, fallback_url="https://www.amazon.co.uk/dp/x")
    assert paid_offers[0].shipping_minor == 280


def test_parse_offer_listing_spend_threshold_promo_becomes_unknown_shipping() -> None:
    """D33 on the AOD/offer-listing path: no real capture on file has this
    exact wording on an offer-listing row (B0CYT8WL1G's aod fetch renders
    dp-shaped content, not a real AOD row — see
    test_amazon_product_fixtures.py), so this is a synthetic row built to
    the same DOM contract as the other AOD parser tests, exercising
    _extract_offer_delivery_text + _conditional_free_shipping_to_unknown
    on the row path specifically (parse_dp's test above already covers the
    dp path)."""
    html = """
    <html><body><div id="aod-container"><div id="aod-offer-list">
      <div id="aod-offer">
        <div id="aod-offer-heading"><h5>New</h5></div>
        <span class="a-price"><span class="a-offscreen">£12.00</span></span>
        <span data-csa-c-delivery-price="FREE"></span>
        <div id="aod-offer-shipping" class="aod-delivery-promise">
          FREE delivery Tuesday, 8 September on orders dispatched by Amazon over £35
        </div>
        <div id="aod-offer-soldBy"><a aria-label="BookCurl. Opens a new page">BookCurl</a></div>
      </div>
    </div></div></body></html>
    """
    offers = parse_offer_listing(
        html, fallback_url="https://x.example/", source_name="amazon_uk_product"
    )
    assert len(offers) == 1
    o = offers[0]
    assert o.seller == "BookCurl"
    assert o.delivery_text is not None
    assert "over £35" in o.delivery_text.lower()
    assert o.shipping_minor is None


# --- D35: machine-readable data-csa-c-mir-sub-type as the primary signal --


def _delivery_price_span(
    *, price: str = "FREE", sub_type: str = "", condition: str = ""
) -> str:
    """A minimal but structurally faithful copy of the real
    `data-csa-c-delivery-price` span (see
    tests/fixtures/amazon/products/B0F3NVWM37-uk-aod-2026-09-04.html) —
    same attributes D35's extractors read, everything else omitted."""
    return (
        f'<span data-csa-c-delivery-price="{price}" '
        f'data-csa-c-delivery-condition="{condition}" '
        f'data-csa-c-mir-sub-type="{sub_type}">{price} delivery</span>'
    )


def test_extract_dp_delivery_condition_attrs_reads_conditional_marker() -> None:
    from selectolax.parser import HTMLParser

    from book_alerter.sources.amazon import _extract_dp_delivery_condition_attrs

    html = (
        '<div id="mir-layout-DELIVERY_BLOCK-slot-PRIMARY_DELIVERY_MESSAGE_LARGE">'
        + _delivery_price_span(
            sub_type="CONDITIONALLY_FREE",
            condition="on orders dispatched by Amazon over £35",
        )
        + "</div>"
    )
    tree = HTMLParser(html)
    conditional, condition_text = _extract_dp_delivery_condition_attrs(tree)
    assert conditional is True
    assert condition_text == "on orders dispatched by Amazon over £35"


def test_extract_dp_delivery_condition_attrs_reads_unconditional_marker() -> None:
    """The attribute is PRESENT (empty value) on a genuine unconditional
    promise, per every real capture on file — this must resolve to a real
    False verdict, not None (which means "attribute absent entirely")."""
    from selectolax.parser import HTMLParser

    from book_alerter.sources.amazon import _extract_dp_delivery_condition_attrs

    html = (
        '<div id="mir-layout-DELIVERY_BLOCK-slot-PRIMARY_DELIVERY_MESSAGE_LARGE">'
        + _delivery_price_span(sub_type="", condition="")
        + "</div>"
    )
    tree = HTMLParser(html)
    conditional, condition_text = _extract_dp_delivery_condition_attrs(tree)
    assert conditional is False
    assert condition_text is None


def test_extract_dp_delivery_condition_attrs_none_when_attribute_absent_entirely() -> None:
    """Every synthetic test fixture (and any legacy layout) has no
    data-csa-c-* attributes at all — must be None (attribute layer
    unavailable), not False (attribute layer says unconditional)."""
    from selectolax.parser import HTMLParser

    from book_alerter.sources.amazon import _extract_dp_delivery_condition_attrs

    html = (
        '<div id="mir-layout-DELIVERY_BLOCK-slot-PRIMARY_DELIVERY_MESSAGE_LARGE">'
        "FREE delivery Monday, 18 May."
        "</div>"
    )
    tree = HTMLParser(html)
    assert _extract_dp_delivery_condition_attrs(tree) == (None, None)


def test_extract_offer_delivery_condition_attrs_reads_conditional_marker() -> None:
    from selectolax.parser import HTMLParser

    from book_alerter.sources.amazon import _extract_offer_delivery_condition_attrs

    tree = HTMLParser(
        "<div>"
        + _delivery_price_span(
            sub_type="CONDITIONALLY_FREE", condition="on your first order to UK or Ireland"
        )
        + "</div>"
    )
    row = tree.css_first("div")
    conditional, condition_text = _extract_offer_delivery_condition_attrs(row)
    assert conditional is True
    assert condition_text == "on your first order to UK or Ireland"


def test_extract_offer_delivery_condition_attrs_reads_unconditional_marker() -> None:
    from selectolax.parser import HTMLParser

    from book_alerter.sources.amazon import _extract_offer_delivery_condition_attrs

    tree = HTMLParser("<div>" + _delivery_price_span(sub_type="", condition="") + "</div>")
    row = tree.css_first("div")
    conditional, condition_text = _extract_offer_delivery_condition_attrs(row)
    assert conditional is False
    assert condition_text is None


def test_extract_offer_delivery_condition_attrs_none_when_node_absent() -> None:
    from selectolax.parser import HTMLParser

    from book_alerter.sources.amazon import _extract_offer_delivery_condition_attrs

    tree = HTMLParser('<div id="aod-offer-shipping">FREE delivery</div>')
    row = tree.css_first("div")
    assert _extract_offer_delivery_condition_attrs(row) == (None, None)


def test_conditional_free_shipping_to_unknown_attribute_alone_is_enough() -> None:
    """The attribute can flag "conditional" even when the English text
    wouldn't have matched on its own — this is the whole point of D35: it
    doesn't depend on guessing the right phrase. F-B6: the two layers are
    OR-combined (either can raise the verdict), not "attribute overrides
    text" — this case doesn't distinguish the two since the attribute is
    True either way, see
    `test_conditional_free_shipping_to_unknown_attribute_alone_cannot_clear_it`
    below for the case that does."""
    from book_alerter.sources.amazon import _conditional_free_shipping_to_unknown

    result = _conditional_free_shipping_to_unknown(
        0,
        "FREE delivery — some future wording the regex has never seen",
        attribute_conditional=True,
        attribute_condition_text="a new condition Amazon hasn't used before",
    )
    assert result is None


def test_conditional_free_shipping_to_unknown_falls_back_when_attribute_unavailable() -> None:
    """attribute_conditional=None (the default) means "no signal" — the
    existing English-marker behaviour from T2.5/D33 must be completely
    unchanged when the attribute isn't present at all."""
    from book_alerter.sources.amazon import _conditional_free_shipping_to_unknown

    assert _conditional_free_shipping_to_unknown(
        0, "FREE delivery on your first order to UK or Ireland."
    ) is None
    assert _conditional_free_shipping_to_unknown(0, "FREE delivery Monday, 18 May.") == 0


def test_conditional_free_shipping_to_unknown_logs_disagreement() -> None:
    """D35's load-bearing diagnostic: when the attribute and the text
    layer disagree, F-B6's OR-combine means the result follows whichever
    layer says "conditional" (here, the attribute), but the disagreement
    itself must still be logged with the raw condition text — same
    reasoning as condition_normalizers.grade_unmapped (D26): the fallback
    is best-effort, the log line is what makes drift visible."""
    from structlog.testing import capture_logs

    from book_alerter.sources.amazon import _conditional_free_shipping_to_unknown

    with capture_logs() as logs:
        result = _conditional_free_shipping_to_unknown(
            0,
            "FREE delivery Monday, 18 May.",  # text layer: unconditional
            attribute_conditional=True,  # attribute layer: conditional
            attribute_condition_text="some new condition wording",
            source_name="amazon_uk_product",
        )

    assert result is None, "either layer flagging conditional is enough"
    warnings = [entry for entry in logs if entry["log_level"] == "warning"]
    assert len(warnings) == 1, logs
    assert warnings[0]["event"] == "amazon.conditional_delivery.layers_disagree"
    assert warnings[0]["source"] == "amazon_uk_product"
    assert warnings[0]["attribute_conditional"] is True
    assert warnings[0]["text_conditional"] is False
    assert warnings[0]["delivery_condition"] == "some new condition wording"


def test_conditional_free_shipping_to_unknown_no_log_when_layers_agree() -> None:
    from structlog.testing import capture_logs

    from book_alerter.sources.amazon import _conditional_free_shipping_to_unknown

    with capture_logs() as logs:
        _conditional_free_shipping_to_unknown(
            0,
            "FREE delivery on your first order to UK or Ireland.",
            attribute_conditional=True,
            attribute_condition_text="on your first order to UK or Ireland",
        )
        _conditional_free_shipping_to_unknown(
            0,
            "FREE delivery Monday, 18 May.",
            attribute_conditional=False,
            attribute_condition_text=None,
        )

    assert logs == []


def test_conditional_free_shipping_to_unknown_unrecognised_sub_type_still_flags_conditional() -> (
    None
):
    """F-B6 regression: `_extract_offer_delivery_condition_attrs` computes
    `attribute_conditional = (sub_type == "CONDITIONALLY_FREE")` — any
    OTHER non-empty `mir-sub-type` value (not just the empty/absent case)
    therefore comes back `False`, not `None`. Before this fix, `False`
    meant "the attribute overrides the text layer to unconditional",
    which reinstated F1 the moment Amazon used a `mir-sub-type` value this
    code doesn't recognise, even while the English text still read a
    verbatim D20 first-order promise. Reproduces the reviewer's exact
    three-case table using the real AOD-row extractor
    (`_extract_offer_delivery_condition_attrs`), not hand-picked booleans,
    so this exercises the same code `_parse_offer_row` runs."""
    from selectolax.parser import HTMLParser

    from book_alerter.sources.amazon import (
        _conditional_free_shipping_to_unknown,
        _extract_offer_delivery_condition_attrs,
    )

    first_order_text = "FREE delivery Monday, 18 May on your first order to UK or Ireland."

    # Case 1: attribute absent entirely -> pre-D35 fallback (unknown).
    row_absent = HTMLParser("<div>FREE delivery</div>").css_first("div")
    attr_conditional, attr_text = _extract_offer_delivery_condition_attrs(row_absent)
    assert attr_conditional is None
    assert (
        _conditional_free_shipping_to_unknown(
            0,
            first_order_text,
            attribute_conditional=attr_conditional,
            attribute_condition_text=attr_text,
        )
        is None
    )

    # Case 2: attribute present but carrying a value this code doesn't
    # recognise as conditional ("FREE_WITH_PRIME", not "CONDITIONALLY_FREE")
    # -> computes False, exactly the shape that reinstated F1 pre-fix.
    row_new_value = HTMLParser(
        "<div>" + _delivery_price_span(sub_type="FREE_WITH_PRIME", condition="") + "</div>"
    ).css_first("div")
    attr_conditional, attr_text = _extract_offer_delivery_condition_attrs(row_new_value)
    assert attr_conditional is False, "an unrecognised sub_type must not compute as conditional"
    assert (
        _conditional_free_shipping_to_unknown(
            0,
            first_order_text,
            attribute_conditional=attr_conditional,
            attribute_condition_text=attr_text,
        )
        is None
    ), "the text layer alone must still be able to raise the verdict to conditional"

    # Case 3: attribute agrees with the text layer (both conditional).
    row_agrees = HTMLParser(
        "<div>"
        + _delivery_price_span(
            sub_type="CONDITIONALLY_FREE",
            condition="on your first order to UK or Ireland",
        )
        + "</div>"
    ).css_first("div")
    attr_conditional, attr_text = _extract_offer_delivery_condition_attrs(row_agrees)
    assert attr_conditional is True
    assert (
        _conditional_free_shipping_to_unknown(
            0,
            first_order_text,
            attribute_conditional=attr_conditional,
            attribute_condition_text=attr_text,
        )
        is None
    )


def test_parse_dp_spend_threshold_promo_detected_via_attribute_on_real_fixture() -> None:
    """D35 regression on the real B0CYT8WL1G fixture: confirms the
    attribute layer (not just the D33 text regex) independently drives
    this result — both layers agree on this real capture, so this test
    doesn't distinguish which one fired, but it pins that the combined
    function still gives the right answer on real markup post-D35."""
    from book_alerter.sources.amazon import _extract_dp_delivery_condition_attrs

    html = (PRODUCT_FIXTURES / "B0CYT8WL1G-uk-dp-2026-09-04.html").read_text(encoding="utf-8")
    offers = parse_dp(html, fallback_url="https://www.amazon.co.uk/dp/B0CYT8WL1G")
    assert offers[0].shipping_minor is None

    from selectolax.parser import HTMLParser

    tree = HTMLParser(html)
    conditional, condition_text = _extract_dp_delivery_condition_attrs(tree)
    assert conditional is True
    assert condition_text == "on orders dispatched by Amazon over £35"
