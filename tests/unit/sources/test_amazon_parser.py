"""Unit tests for the Amazon UK HTML parsers.

Runs against synthetic fixtures hand-crafted from Amazon's public dp +
AOD (offer-listing) markup. Live capture was blocked by Amazon's
anti-bot (verified 2026-05-14), so the fixture HTML reflects the DOM
contract documented in `src/book_alerter/sources/amazon.py` rather than
a live snapshot. When the live capture path works in the future the
fixtures should be regenerated and these tests revisited.
"""

from pathlib import Path

from book_alerter.sources.amazon import parse_dp, parse_offer_listing

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
    assert parse_dp("", fallback_url="https://x.example/") == []
    assert parse_dp("<html></html>", fallback_url="https://x.example/") == []
    assert parse_offer_listing("", fallback_url="https://x.example/") == []
    assert parse_offer_listing("<html></html>", fallback_url="https://x.example/") == []


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
