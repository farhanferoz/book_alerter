"""Unit tests for compute_signal — pure logic, no DB."""

from __future__ import annotations

from book_alerter.config import RecommendationConfig
from book_alerter.stats import compute_signal


def test_insufficient_data_when_below_days_gate(
    transient_book, transient_stats,
):
    # Primary gate: even with many observations, < min_days_of_history yields
    # INSUFFICIENT_DATA because percentile distributions over same-day repeats
    # are meaningless.
    cfg = RecommendationConfig()  # min_days_of_history=7
    book = transient_book()
    stats = transient_stats(
        observation_count=200, current_best_total_minor=100, days_of_history=3
    )
    assert compute_signal(book, stats, cfg) == "INSUFFICIENT_DATA"


def test_insufficient_data_when_count_below_legacy_threshold(
    transient_book, transient_stats,
):
    # Legacy count gate still works if config sets it explicitly.
    cfg = RecommendationConfig(min_observations_for_signal=14)
    book = transient_book()
    stats = transient_stats(
        observation_count=5, current_best_total_minor=100, days_of_history=30
    )
    assert compute_signal(book, stats, cfg) == "INSUFFICIENT_DATA"


def test_insufficient_data_when_no_current_best(transient_book, transient_stats):
    cfg = RecommendationConfig()
    book = transient_book()
    stats = transient_stats(observation_count=20, current_best_total_minor=None)
    assert compute_signal(book, stats, cfg) == "INSUFFICIENT_DATA"


def test_target_hit_when_current_at_or_below_target(
    transient_book, transient_stats,
):
    cfg = RecommendationConfig()
    book = transient_book(target_price_minor=1000)
    stats_eq = transient_stats(
        observation_count=20,
        current_best_total_minor=1000,
        sorted_totals=[100, 200, 300, 400, 500],
    )
    assert compute_signal(book, stats_eq, cfg) == "TARGET_HIT"

    stats_below = transient_stats(
        observation_count=20,
        current_best_total_minor=999,
        sorted_totals=[100, 200, 300, 400, 500],
    )
    assert compute_signal(book, stats_below, cfg) == "TARGET_HIT"


def test_buy_when_within_tolerance_above_target(transient_book, transient_stats):
    # target=1000, tolerance_pct=5 → tolerance = 1050
    cfg = RecommendationConfig()
    book = transient_book(target_price_minor=1000)

    # current=1050 → within tolerance → BUY
    stats_at = transient_stats(
        observation_count=20,
        current_best_total_minor=1050,
        sorted_totals=[100, 200, 300, 400, 500],
    )
    assert compute_signal(book, stats_at, cfg) == "BUY"

    # current=1051 → above tolerance → falls through to percentile path.
    # sorted_totals=[100..500], p25=200, current=1051 > 200, p50=300, current>p50 → WAIT
    stats_above = transient_stats(
        observation_count=20,
        current_best_total_minor=1051,
        p50_total_minor=300,
        sorted_totals=[100, 200, 300, 400, 500],
    )
    assert compute_signal(book, stats_above, cfg) == "WAIT"


def test_buy_when_no_target_and_current_le_p25(transient_book, transient_stats):
    # sorted_totals=[100,200,300,400,500] → p25=200
    cfg = RecommendationConfig(buy_percentile=25)
    book = transient_book()
    stats = transient_stats(
        observation_count=20,
        current_best_total_minor=200,
        p50_total_minor=300,
        sorted_totals=[100, 200, 300, 400, 500],
    )
    assert compute_signal(book, stats, cfg) == "BUY"


def test_watch_when_no_target_and_current_between_p25_and_p50(
    transient_book, transient_stats,
):
    cfg = RecommendationConfig()
    book = transient_book()
    # current=300 == p50 → WATCH (current <= p50 branch)
    stats_at_p50 = transient_stats(
        observation_count=20,
        current_best_total_minor=300,
        p50_total_minor=300,
        sorted_totals=[100, 200, 300, 400, 500],
    )
    assert compute_signal(book, stats_at_p50, cfg) == "WATCH"

    # current=250 → > p25 (200) but <= p50 (300) → WATCH
    stats_mid = transient_stats(
        observation_count=20,
        current_best_total_minor=250,
        p50_total_minor=300,
        sorted_totals=[100, 200, 300, 400, 500],
    )
    assert compute_signal(book, stats_mid, cfg) == "WATCH"


def test_wait_when_no_target_and_current_above_p50(
    transient_book, transient_stats,
):
    cfg = RecommendationConfig()
    book = transient_book()
    stats = transient_stats(
        observation_count=20,
        current_best_total_minor=400,
        p50_total_minor=300,
        sorted_totals=[100, 200, 300, 400, 500],
    )
    assert compute_signal(book, stats, cfg) == "WAIT"


def test_book_percentile_threshold_override(transient_book, transient_stats):
    # book.percentile_threshold=50, p50=300, current=300 → BUY
    cfg = RecommendationConfig()
    book = transient_book(percentile_threshold=50)
    stats = transient_stats(
        observation_count=20,
        current_best_total_minor=300,
        p50_total_minor=300,
        sorted_totals=[100, 200, 300, 400, 500],
    )
    assert compute_signal(book, stats, cfg) == "BUY"


def test_target_then_percentile_fall_through(transient_book, transient_stats):
    # target=500, tolerance_pct=5 → tolerance=525, current=600 > 525 → fall through.
    # sorted=[100..500], p25=200, current=600 > p25, p50=300, current > p50 → WAIT.
    cfg = RecommendationConfig()
    book = transient_book(target_price_minor=500)
    stats = transient_stats(
        observation_count=20,
        current_best_total_minor=600,
        p50_total_minor=300,
        sorted_totals=[100, 200, 300, 400, 500],
    )
    assert compute_signal(book, stats, cfg) == "WAIT"


def test_insufficient_data_when_sorted_totals_empty_but_count_high(
    transient_book, transient_stats,
):
    # observation_count=20 (lying), sorted_totals=[] → percentile_at returns None
    # → INSUFFICIENT_DATA via p_field guard.
    cfg = RecommendationConfig()
    book = transient_book()
    stats = transient_stats(
        observation_count=20,
        current_best_total_minor=100,
        p50_total_minor=None,
        sorted_totals=[],
    )
    assert compute_signal(book, stats, cfg) == "INSUFFICIENT_DATA"
