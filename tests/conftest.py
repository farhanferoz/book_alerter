from __future__ import annotations

from datetime import UTC, datetime

import pytest

from book_alerter.db import models
from book_alerter.stats import BookStats


@pytest.fixture
def transient_book():
    def _make(
        isbn: str = "9780000000000",
        *,
        title: str = "t",
        author: str = "a",
        target_price_minor: int | None = None,
        percentile_threshold: int | None = None,
    ) -> models.Book:
        now = datetime.now(UTC)
        return models.Book(
            isbn13=isbn, title=title, author=author,
            created_at=now, updated_at=now,
            target_price_minor=target_price_minor,
            percentile_threshold=percentile_threshold,
        )

    return _make


@pytest.fixture
def transient_stats():
    def _make(
        *,
        observation_count: int,
        current_best_total_minor: int | None,
        p50_total_minor: int | None = None,
        sorted_totals: list[int] | None = None,
    ) -> BookStats:
        return BookStats(
            book_id=1,
            current_best_total_minor=current_best_total_minor,
            current_best_source=None,
            current_best_seller=None,
            current_best_condition=None,
            current_best_url=None,
            p25_total_minor=None,
            p50_total_minor=p50_total_minor,
            p75_total_minor=None,
            all_time_min_total_minor=None,
            all_time_max_total_minor=None,
            observation_count=observation_count,
            days_of_history=0,
            last_observed_at=None,
            sorted_totals=sorted_totals if sorted_totals is not None else [],
        )

    return _make
