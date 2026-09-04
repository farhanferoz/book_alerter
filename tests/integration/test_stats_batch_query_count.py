"""Pins T3.1's query-count contract: `compute_stats_for_items` loads
candidates + window observations + history summaries for however many items
are requested in exactly three SELECTs, when the caller supplies the
cross-item shipping medians (as `list_books`/`list_products` do) so no
per-request full-table scan for that tier is needed either.

Verified with a SQLAlchemy `before_cursor_execute` counter rather than
assumed from reading the code — the whole point of T3.1 is the query count,
so it's the one property this suite must measure, not eyeball.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import event
from sqlmodel import Session

from book_alerter.config import RecommendationConfig
from book_alerter.db import models
from book_alerter.stats import (
    _BOOK_SCHEMA,
    compute_stats_for_items,
    source_seller_global_shipping_medians,
)

# T3.1's contract: candidates + window observations + history summaries, one
# SELECT each, regardless of how many items are in the batch.
_EXPECTED_SELECT_COUNT = 3


def _count_selects(engine, fn) -> int:
    count = 0

    def _on_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        nonlocal count
        if statement.strip().upper().startswith("SELECT"):
            count += 1

    event.listen(engine, "before_cursor_execute", _on_cursor_execute)
    try:
        fn()
    finally:
        event.remove(engine, "before_cursor_execute", _on_cursor_execute)
    return count


def test_compute_stats_for_items_issues_three_selects_for_a_batch(
    engine_with_view, make_book,
):
    now = datetime.now(UTC)
    with Session(engine_with_view) as s:
        books = [make_book(s, isbn13=f"978000000{n:04d}") for n in range(5)]
        for i, book in enumerate(books):
            s.add(models.PriceObservation(
                book_id=book.id, source="wob", condition="new",
                price_minor=1000 + i, currency="GBP", shipping_minor=0,
                total_minor=1000 + i, url="https://wob",
                observed_at=now, raw={},
            ))
        s.commit()

        ids = [b.id for b in books]
        medians = source_seller_global_shipping_medians(s)

        select_count = _count_selects(
            engine_with_view,
            lambda: compute_stats_for_items(
                ids, s,
                schema=_BOOK_SCHEMA,
                cfg=RecommendationConfig(),
                window_days=dict.fromkeys(ids, 90),
                medians=medians,
            ),
        )

    assert select_count == _EXPECTED_SELECT_COUNT, (
        f"expected 3 SELECTs (candidates, window observations, history "
        f"summaries) for a {len(ids)}-item batch with medians precomputed, "
        f"got {select_count}"
    )


def test_compute_stats_for_items_query_count_independent_of_item_count(
    engine_with_view, make_book,
):
    """The whole point of the batch: 3 queries for 1 item, 3 queries for 20."""
    now = datetime.now(UTC)
    with Session(engine_with_view) as s:
        books = [make_book(s, isbn13=f"978100000{n:04d}") for n in range(20)]
        for i, book in enumerate(books):
            s.add(models.PriceObservation(
                book_id=book.id, source="wob", condition="new",
                price_minor=1000 + i, currency="GBP", shipping_minor=0,
                total_minor=1000 + i, url="https://wob",
                observed_at=now, raw={},
            ))
        s.commit()

        ids = [b.id for b in books]
        medians = source_seller_global_shipping_medians(s)

        select_count = _count_selects(
            engine_with_view,
            lambda: compute_stats_for_items(
                ids, s,
                schema=_BOOK_SCHEMA,
                cfg=RecommendationConfig(),
                window_days=dict.fromkeys(ids, 90),
                medians=medians,
            ),
        )

    assert select_count == _EXPECTED_SELECT_COUNT
