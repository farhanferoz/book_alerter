"""AmazonUKProductInlineSource — fetch dispatching + track_used filtering.

Uses the same fixtures + fake-playwright pattern as the books-side tests in
`test_amazon.py`. Asserts:
- Source.item_kinds == {PRODUCT}
- fetch() passes the product's ASIN directly (no ISBN conversion)
- track_used=False filters used-grade offers out of the merged result
- track_used=True keeps the full multi-condition merged result
- a Book item is rejected (defence-in-depth assertion)
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from book_alerter.db.models import Book, Product
from book_alerter.enums import Condition, ItemKind
from book_alerter.sources.amazon import AmazonUKProductInlineSource
from tests.integration.sources.test_amazon import (
    _install_fake_render_page,  # type: ignore[attr-defined]
    _prepared,  # type: ignore[attr-defined]
)

FIXTURE_DIR = Path(__file__).parent.parent.parent / "fixtures" / "amazon"
# Fall back to the book fixture if no product-specific capture yet — the dp
# format is identical for both, only the seller/title strings change.
FIXTURE_DP_FALLBACK = FIXTURE_DIR / "9780747532699-uk-dp-2026-05-14.html"
FIXTURE_OFFER_LISTING_FALLBACK = FIXTURE_DIR / "9780747532699-uk-offer-listing.html"


def _hp_product(*, track_used: bool = False) -> Product:
    now = datetime.now(UTC)
    return Product(
        id=1,
        asin="B07XYZ1234",
        title="HP USB-C Adapter",
        track_used=track_used,
        created_at=now,
        updated_at=now,
    )


def _hp_book() -> Book:
    now = datetime.now(UTC)
    return Book(
        id=999,
        isbn13="9780747532699",
        title="Philosopher's Stone",
        author="J. K. Rowling",
        created_at=now,
        updated_at=now,
    )


def test_item_kinds_is_product_only() -> None:
    assert AmazonUKProductInlineSource.item_kinds == frozenset({ItemKind.PRODUCT})


def test_dp_url_uses_asin_directly() -> None:
    src = AmazonUKProductInlineSource(region="UK")
    assert src.dp_url("B07XYZ1234") == "https://www.amazon.co.uk/dp/B07XYZ1234"


def test_offer_listing_url_uses_asin_directly() -> None:
    src = AmazonUKProductInlineSource(region="UK")
    assert (
        src.offer_listing_url("B07XYZ1234")
        == "https://www.amazon.co.uk/gp/offer-listing/B07XYZ1234?condition=all"
    )


def test_non_uk_region_rejected() -> None:
    with pytest.raises(ValueError, match="UK"):
        AmazonUKProductInlineSource(region="US")


def test_fetch_rejects_book_item() -> None:
    """A Book passed to the product source should fail loudly (assertion in
    fetch). Scheduler item_kinds intersection prevents this in practice,
    but defence-in-depth: the wrong item kind is a programming bug, not a
    user error."""
    src = AmazonUKProductInlineSource(region="UK")
    with pytest.raises(AssertionError, match="only handles products"):
        asyncio.run(src.fetch(_hp_book()))


def test_fetch_with_track_used_false_filters_used_grades(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """track_used=False (the product default) must drop every non-NEW row
    from the merged offer set. The fixture has Amazon (new) + 3 used
    sellers; we should see Amazon only."""
    calls = _install_fake_render_page(
        monkeypatch,
        {
            "/dp/": FIXTURE_DP_FALLBACK.read_text(encoding="utf-8"),
            "/gp/offer-listing/": FIXTURE_OFFER_LISTING_FALLBACK.read_text(
                encoding="utf-8"
            ),
        },
    )

    src = _prepared(AmazonUKProductInlineSource(region="UK"))
    offers = asyncio.run(src.fetch(_hp_product(track_used=False)))

    # Both pages still rendered — track_used filtering happens AFTER merge,
    # not by skipping a render call.
    assert len(calls) == 2
    # The fixture has 1 Amazon-new row and 3 used rows. Filtering leaves
    # the Amazon-new row only.
    assert all(o.condition == Condition.NEW for o in offers)
    assert len(offers) >= 1
    assert any(o.seller == "Amazon" for o in offers)


def test_fetch_with_track_used_true_returns_all_conditions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """track_used=True (opt-in) preserves the full multi-condition merge —
    same behaviour as the books-side source."""
    calls = _install_fake_render_page(
        monkeypatch,
        {
            "/dp/": FIXTURE_DP_FALLBACK.read_text(encoding="utf-8"),
            "/gp/offer-listing/": FIXTURE_OFFER_LISTING_FALLBACK.read_text(
                encoding="utf-8"
            ),
        },
    )

    src = _prepared(AmazonUKProductInlineSource(region="UK"))
    offers = asyncio.run(src.fetch(_hp_product(track_used=True)))

    assert len(calls) == 2
    sellers = {o.seller for o in offers}
    conditions = {o.condition for o in offers}
    assert "Amazon" in sellers
    # At least one used grade present when track_used=True.
    assert conditions - {Condition.NEW}


def test_fetch_uses_product_asin_directly_in_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The product source must NOT convert ASIN→ISBN-anything. URLs should
    contain the raw ASIN."""
    calls = _install_fake_render_page(
        monkeypatch,
        {
            "/dp/": FIXTURE_DP_FALLBACK.read_text(encoding="utf-8"),
            "/gp/offer-listing/": FIXTURE_OFFER_LISTING_FALLBACK.read_text(
                encoding="utf-8"
            ),
        },
    )

    src = _prepared(AmazonUKProductInlineSource(region="UK"))
    asyncio.run(src.fetch(_hp_product()))

    # Both calls should have B07XYZ1234 in the URL, not a derived ISBN-10.
    assert all("B07XYZ1234" in url for url in calls), calls
