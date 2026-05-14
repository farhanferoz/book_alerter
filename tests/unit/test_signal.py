"""Unit tests for compute_signal — pure logic, no DB."""

from __future__ import annotations

from datetime import UTC, datetime

from book_alerter.config import RecommendationConfig
from book_alerter.db import models
from book_alerter.stats import BookStats, compute_signal


def _book(
    *,
    target_price_minor: int | None = None,
    percentile_threshold: int | None = None,
) -> models.Book:
    now = datetime.now(UTC)
    return models.Book(
        isbn13="9780000000000",
        title="t",
        author="a",
        created_at=now,
        updated_at=now,
        target_price_minor=target_price_minor,
        percentile_threshold=percentile_threshold,
    )


def _stats(
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


def test_insufficient_data_when_count_below_threshold():
    cfg = RecommendationConfig()  # min_observations_for_signal=14
    book = _book()
    stats = _stats(observation_count=5, current_best_total_minor=100)
    assert compute_signal(book, stats, cfg) == "INSUFFICIENT_DATA"


def test_insufficient_data_when_no_current_best():
    cfg = RecommendationConfig()
    book = _book()
    stats = _stats(observation_count=20, current_best_total_minor=None)
    assert compute_signal(book, stats, cfg) == "INSUFFICIENT_DATA"


def test_target_hit_when_current_at_or_below_target():
    cfg = RecommendationConfig()
    book = _book(target_price_minor=1000)
    stats_eq = _stats(
        observation_count=20,
        current_best_total_minor=1000,
        sorted_totals=[100, 200, 300, 400, 500],
    )
    assert compute_signal(book, stats_eq, cfg) == "TARGET_HIT"

    stats_below = _stats(
        observation_count=20,
        current_best_total_minor=999,
        sorted_totals=[100, 200, 300, 400, 500],
    )
    assert compute_signal(book, stats_below, cfg) == "TARGET_HIT"


def test_buy_when_within_tolerance_above_target():
    # target=1000, tolerance_pct=5 → tolerance = 1050
    cfg = RecommendationConfig()
    book = _book(target_price_minor=1000)

    # current=1050 → within tolerance → BUY
    stats_at = _stats(
        observation_count=20,
        current_best_total_minor=1050,
        sorted_totals=[100, 200, 300, 400, 500],
    )
    assert compute_signal(book, stats_at, cfg) == "BUY"

    # current=1051 → above tolerance → falls through to percentile path.
    # sorted_totals=[100..500], p25=200, current=1051 > 200, p50=300, current>p50 → WAIT
    stats_above = _stats(
        observation_count=20,
        current_best_total_minor=1051,
        p50_total_minor=300,
        sorted_totals=[100, 200, 300, 400, 500],
    )
    assert compute_signal(book, stats_above, cfg) == "WAIT"


def test_buy_when_no_target_and_current_le_p25():
    # sorted_totals=[100,200,300,400,500] → p25=200
    cfg = RecommendationConfig()  # buy_percentile=25
    book = _book()
    stats = _stats(
        observation_count=20,
        current_best_total_minor=200,
        p50_total_minor=300,
        sorted_totals=[100, 200, 300, 400, 500],
    )
    assert compute_signal(book, stats, cfg) == "BUY"


def test_watch_when_no_target_and_current_between_p25_and_p50():
    cfg = RecommendationConfig()
    book = _book()
    # current=300 == p50 → WATCH (current <= p50 branch)
    stats_at_p50 = _stats(
        observation_count=20,
        current_best_total_minor=300,
        p50_total_minor=300,
        sorted_totals=[100, 200, 300, 400, 500],
    )
    assert compute_signal(book, stats_at_p50, cfg) == "WATCH"

    # current=250 → > p25 (200) but <= p50 (300) → WATCH
    stats_mid = _stats(
        observation_count=20,
        current_best_total_minor=250,
        p50_total_minor=300,
        sorted_totals=[100, 200, 300, 400, 500],
    )
    assert compute_signal(book, stats_mid, cfg) == "WATCH"


def test_wait_when_no_target_and_current_above_p50():
    cfg = RecommendationConfig()
    book = _book()
    stats = _stats(
        observation_count=20,
        current_best_total_minor=400,
        p50_total_minor=300,
        sorted_totals=[100, 200, 300, 400, 500],
    )
    assert compute_signal(book, stats, cfg) == "WAIT"


def test_book_percentile_threshold_override():
    # book.percentile_threshold=50, p50=300, current=300 → BUY
    cfg = RecommendationConfig()
    book = _book(percentile_threshold=50)
    stats = _stats(
        observation_count=20,
        current_best_total_minor=300,
        p50_total_minor=300,
        sorted_totals=[100, 200, 300, 400, 500],
    )
    assert compute_signal(book, stats, cfg) == "BUY"


def test_target_then_percentile_fall_through():
    # target=500, tolerance_pct=5 → tolerance=525, current=600 > 525 → fall through.
    # sorted=[100..500], p25=200, current=600 > p25, p50=300, current > p50 → WAIT.
    cfg = RecommendationConfig()
    book = _book(target_price_minor=500)
    stats = _stats(
        observation_count=20,
        current_best_total_minor=600,
        p50_total_minor=300,
        sorted_totals=[100, 200, 300, 400, 500],
    )
    assert compute_signal(book, stats, cfg) == "WAIT"


def test_insufficient_data_when_sorted_totals_empty_but_count_high():
    # observation_count=20 (lying), sorted_totals=[] → percentile_at returns None
    # → INSUFFICIENT_DATA via p_field guard.
    cfg = RecommendationConfig()
    book = _book()
    stats = _stats(
        observation_count=20,
        current_best_total_minor=100,
        p50_total_minor=None,
        sorted_totals=[],
    )
    assert compute_signal(book, stats, cfg) == "INSUFFICIENT_DATA"
