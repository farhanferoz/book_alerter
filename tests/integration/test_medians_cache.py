"""Tests for `stats.MediansCache` (plan task T3.4).

`get_or_compute` must return a cached value within the TTL, recompute once
it expires, and `invalidate()` must force a recompute on the very next call
regardless of TTL — the mechanism `scheduler._persist` uses so a fresh
scrape's shipping data doesn't wait out the 60s window.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Session

from book_alerter.db import models
from book_alerter.stats import _BOOK_SCHEMA, _PRODUCT_SCHEMA, MediansCache


def _seed_shipping_row(session: Session, book_id: int, shipping: int, seller: str) -> None:
    session.add(models.PriceObservation(
        book_id=book_id, source="amazon", seller=seller, condition="new",
        price_minor=1000, currency="GBP", shipping_minor=shipping,
        total_minor=1000 + shipping, url="https://x",
        observed_at=datetime.now(UTC), last_seen_at=datetime.now(UTC), raw={},
    ))
    session.commit()


def test_get_or_compute_caches_within_ttl(engine_with_view, make_book):
    with Session(engine_with_view) as s:
        book = make_book(s)
        _seed_shipping_row(s, book.id, 100, "Amazon")

        cache = MediansCache(ttl_seconds=60)
        first = cache.get_or_compute(s, schema=_BOOK_SCHEMA, min_observations=1)

        # A second shipping row landing after the first computation must NOT
        # be reflected while the cache is still fresh.
        _seed_shipping_row(s, book.id, 200, "Amazon")
        second = cache.get_or_compute(s, schema=_BOOK_SCHEMA, min_observations=1)

    assert first == second
    assert first == {("amazon", "amazon_fulfilled"): 100}


def test_get_or_compute_recomputes_after_ttl_expiry(engine_with_view, make_book):
    with Session(engine_with_view) as s:
        book = make_book(s)
        _seed_shipping_row(s, book.id, 100, "Amazon")

        cache = MediansCache(ttl_seconds=-1)  # already expired on the next check
        first = cache.get_or_compute(s, schema=_BOOK_SCHEMA, min_observations=1)

        _seed_shipping_row(s, book.id, 200, "Amazon")
        second = cache.get_or_compute(s, schema=_BOOK_SCHEMA, min_observations=1)

    assert first == {("amazon", "amazon_fulfilled"): 100}
    # median([100, 200]) == 150 — the recompute picked up the new row.
    assert second == {("amazon", "amazon_fulfilled"): 150}


def test_invalidate_forces_recompute_regardless_of_ttl(engine_with_view, make_book):
    with Session(engine_with_view) as s:
        book = make_book(s)
        _seed_shipping_row(s, book.id, 100, "Amazon")

        cache = MediansCache(ttl_seconds=60)
        cache.get_or_compute(s, schema=_BOOK_SCHEMA, min_observations=1)

        _seed_shipping_row(s, book.id, 200, "Amazon")
        cache.invalidate()
        after_invalidate = cache.get_or_compute(s, schema=_BOOK_SCHEMA, min_observations=1)

    assert after_invalidate == {("amazon", "amazon_fulfilled"): 150}


def test_min_observations_change_is_not_served_stale(engine_with_view, make_book):
    """F-E (Tier 4 review of Wave 3): entries used to key on
    `schema.observation_table` alone, so a second call on the same
    instance with a DIFFERENT `min_observations` — still within the TTL —
    silently returned the value computed under the first threshold.
    Reproduces the review's demonstration: 5 rows clear a threshold of 5
    but not 10; a threshold-10 call right after must recompute (excluding
    the bucket, since only 5 rows exist) rather than reuse the
    threshold-5 result."""
    with Session(engine_with_view) as s:
        book = make_book(s)
        for i in range(5):
            _seed_shipping_row(s, book.id, 100 + i, f"Amazon{i}" if i else "Amazon")

        cache = MediansCache(ttl_seconds=60)
        at_five = cache.get_or_compute(s, schema=_BOOK_SCHEMA, min_observations=5)
        at_ten = cache.get_or_compute(s, schema=_BOOK_SCHEMA, min_observations=10)

    assert at_five == {("amazon", "amazon_fulfilled"): 102}
    assert at_ten == {}, (
        "a different min_observations within the TTL must recompute, not "
        "reuse the other threshold's cached result"
    )


def test_book_and_product_schemas_cache_independently(engine_with_view, make_book, make_product):
    with Session(engine_with_view) as s:
        book = make_book(s)
        product = make_product(s)
        _seed_shipping_row(s, book.id, 100, "Amazon")
        session_product_obs = models.ProductObservation(
            product_id=product.id, source="amazon_uk_product", seller="Amazon",
            condition="new", price_minor=1000, currency="GBP", shipping_minor=300,
            total_minor=1300, url="https://y",
            observed_at=datetime.now(UTC), last_seen_at=datetime.now(UTC), raw={},
        )
        s.add(session_product_obs)
        s.commit()

        cache = MediansCache(ttl_seconds=60)
        book_medians = cache.get_or_compute(s, schema=_BOOK_SCHEMA, min_observations=1)
        product_medians = cache.get_or_compute(s, schema=_PRODUCT_SCHEMA, min_observations=1)

    assert book_medians == {("amazon", "amazon_fulfilled"): 100}
    assert product_medians == {("amazon_uk_product", "amazon_fulfilled"): 300}
