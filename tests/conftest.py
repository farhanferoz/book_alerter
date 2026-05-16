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
    # Default days_of_history past the gate (config default is 7) so tests
    # that don't care about the time gate stay focused on signal logic.
    # Tests that DO want to exercise the gate pass `days_of_history=0`.
    _USE_BEST: object = object()

    def _make(
        *,
        observation_count: int,
        current_best_total_minor: int | None,
        p50_total_minor: int | None = None,
        sorted_totals: list[int] | None = None,
        days_of_history: int = 30,
        current_percentile_rank: int | None = None,
        current_best_shipping_minor: int | None = 0,
        current_effective_total_minor: int | None | object = _USE_BEST,
    ) -> BookStats:
        """The p50_total_minor / current_percentile_rank kwargs are legacy
        shape hooks for the dispatcher message body and signal-percentile
        readout — they're synthesized into windows["3m"] so tests targeting
        the old flat fields keep working without rewriting their setup."""
        if current_effective_total_minor is _USE_BEST:
            current_effective_total_minor = current_best_total_minor
        totals = sorted_totals if sorted_totals is not None else []
        rank = current_percentile_rank
        if rank is None and totals and current_best_total_minor is not None:
            below = sum(1 for t in totals if t <= current_best_total_minor)
            rank = int(round((below / len(totals)) * 100))
        # Synthesize the "3m" window so callers that exercise the dispatcher
        # alert-message path (reads windows[cfg_label].p50) get the median
        # they passed in. Other windows stay empty.
        from book_alerter.stats import WindowStats
        windows = {
            "1m": WindowStats(),
            "3m": WindowStats(count=len(totals), rank=rank, p50=p50_total_minor),
            "12m": WindowStats(),
        }
        return BookStats(
            book_id=1,
            current_best_total_minor=current_best_total_minor,
            current_best_price_minor=current_best_total_minor,
            current_best_shipping_minor=current_best_shipping_minor,
            current_best_source=None,
            current_best_seller=None,
            current_best_condition=None,
            current_best_url=None,
            all_time_min_total_minor=None,
            all_time_max_total_minor=None,
            observation_count=observation_count,
            days_of_history=days_of_history,
            last_observed_at=None,
            percentile_window_days=90,
            current_effective_total_minor=current_effective_total_minor,
            shipping_estimate_minor=None,
            sorted_totals=totals,
            windows=windows,
        )

    return _make
