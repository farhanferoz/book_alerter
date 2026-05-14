"""Unit tests for the bookfinder.com HTML parser.

Runs against the captured fixture (no network, no browser). Parser changes
that drop offers, mis-grade conditions, or fail to dedupe should be caught here.
"""

from pathlib import Path

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
    assert ebay.price_minor == 791  # £7.91
    assert ebay.shipping_minor == 270  # £2.70
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
        assert b.price_minor == 5427  # £54.27
        assert b.shipping_minor == 1490  # £14.90


def test_empty_html_returns_empty_list() -> None:
    assert parse_offers("", fallback_url="https://x.example/") == []
    assert parse_offers("<html></html>", fallback_url="https://x.example/") == []


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
