"""compute_product_stats end-to-end: insert product + observations, read
stats, assert windowed totals + percentile rank surface the same way they
do for books.

The shipping cascade, percentile machinery, and `BookStats` dataclass shape
are exercised by the much larger book-stats test suite; this file's job is
to verify the schema-parameterised path against the product tables works
at all, not to retest the cascade.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlmodel import Session

from book_alerter.db import models
from book_alerter.enums import Condition
from book_alerter.stats import compute_book_stats, compute_product_stats


def test_compute_product_stats_no_rows_returns_empty_shape(
    engine_with_view, make_product,
) -> None:
    with Session(engine_with_view) as session:
        product = make_product(session, asin="B070000001")
        s = compute_product_stats(product.id, session, window_days=90)
        assert s.book_id == product.id  # field reused; documented in plan doc
        assert s.observation_count == 0
        assert s.current_best_total_minor is None
        assert s.windows["3m"].count == 0


def test_compute_product_stats_window_percentile_matches_book_path(
    engine_with_view, make_product, make_book,
) -> None:
    """Identical observation streams against book and product schemas yield
    identical stats — proves the schema parameterisation didn't introduce
    skew."""
    now = datetime.now(UTC)
    with Session(engine_with_view) as session:
        book = make_book(session, isbn13="9780000000099")
        product = make_product(session, asin="B070000002")

        for i in range(10):
            ts = now - timedelta(days=i)
            session.add(
                models.PriceObservation(
                    book_id=book.id,
                    source="amazon",
                    seller="Amazon",
                    condition=Condition.NEW,
                    price_minor=1000 + i * 100,
                    currency="GBP",
                    shipping_minor=0,
                    total_minor=1000 + i * 100,
                    url="https://example",
                    observed_at=ts,
                ),
            )
            session.add(
                models.ProductObservation(
                    product_id=product.id,
                    source="amazon_uk_product",
                    seller="Amazon",
                    condition=Condition.NEW,
                    price_minor=1000 + i * 100,
                    currency="GBP",
                    shipping_minor=0,
                    total_minor=1000 + i * 100,
                    url="https://example",
                    observed_at=ts,
                ),
            )
        session.commit()

        book_stats = compute_book_stats(book.id, session, window_days=90)
        product_stats = compute_product_stats(product.id, session, window_days=90)

        assert book_stats.observation_count == product_stats.observation_count == 10
        assert (
            book_stats.current_best_total_minor
            == product_stats.current_best_total_minor
            == 1000
        )
        # Same shape — windows + sorted_totals + percentile rank.
        assert book_stats.sorted_totals == product_stats.sorted_totals
        for label in ("1m", "3m", "12m"):
            assert (
                book_stats.windows[label].p50
                == product_stats.windows[label].p50
            )
