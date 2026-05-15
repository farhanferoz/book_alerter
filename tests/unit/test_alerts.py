"""Unit tests for detect_alert_kinds — pure logic, no DB."""

from __future__ import annotations

from book_alerter.alerts import detect_alert_kinds
from book_alerter.config import RecommendationConfig


def test_target_hit_fires_when_current_at_or_below_target(
    transient_book, transient_stats,
):
    cfg = RecommendationConfig()
    book = transient_book(target_price_minor=1000)
    # Use observation_count below threshold so compute_signal -> INSUFFICIENT_DATA,
    # which prevents percentile_cross from firing and isolates the target_hit path.
    stats_eq = transient_stats(observation_count=5, current_best_total_minor=1000)
    out, _ = detect_alert_kinds(book, stats_eq, prev_signal=None, prev_all_time_min=None, cfg=cfg)
    assert "target_hit" in out
    assert "percentile_cross" not in out

    stats_below = transient_stats(observation_count=5, current_best_total_minor=500)
    out2, _ = detect_alert_kinds(book, stats_below, prev_signal=None, prev_all_time_min=None, cfg=cfg)
    assert "target_hit" in out2
    assert "percentile_cross" not in out2


def test_target_hit_does_not_double_fire(transient_book, transient_stats):
    cfg = RecommendationConfig()
    book = transient_book(target_price_minor=1000)
    stats = transient_stats(observation_count=5, current_best_total_minor=900)
    out, _ = detect_alert_kinds(
        book, stats, prev_signal="TARGET_HIT", prev_all_time_min=None, cfg=cfg
    )
    assert "target_hit" not in out


def test_percentile_cross_fires_on_buy_transition(transient_book, transient_stats):
    cfg = RecommendationConfig(buy_percentile=25)
    book = transient_book()  # no target
    # sorted_totals=[100,200,300,400,500] -> p25=200; current=200 -> BUY
    stats = transient_stats(
        observation_count=20,
        current_best_total_minor=200,
        p50_total_minor=300,
        sorted_totals=[100, 200, 300, 400, 500],
    )
    out, _ = detect_alert_kinds(
        book, stats, prev_signal="WATCH", prev_all_time_min=None, cfg=cfg
    )
    assert "percentile_cross" in out


def test_percentile_cross_does_not_fire_when_already_in_buy(
    transient_book, transient_stats,
):
    cfg = RecommendationConfig()
    book = transient_book()
    stats = transient_stats(
        observation_count=20,
        current_best_total_minor=200,
        p50_total_minor=300,
        sorted_totals=[100, 200, 300, 400, 500],
    )
    out, _ = detect_alert_kinds(
        book, stats, prev_signal="BUY", prev_all_time_min=None, cfg=cfg
    )
    assert "percentile_cross" not in out


def test_new_low_fires_when_current_below_prev_all_time_min(
    transient_book, transient_stats,
):
    cfg = RecommendationConfig()
    book = transient_book()
    # observation_count below threshold -> INSUFFICIENT_DATA -> percentile_cross suppressed
    stats = transient_stats(observation_count=5, current_best_total_minor=800)
    out, _ = detect_alert_kinds(
        book, stats, prev_signal="WAIT", prev_all_time_min=1000, cfg=cfg
    )
    assert "new_low" in out


def test_new_low_does_not_fire_when_no_prev_min(transient_book, transient_stats):
    cfg = RecommendationConfig()
    book = transient_book()
    stats = transient_stats(observation_count=5, current_best_total_minor=800)
    out, _ = detect_alert_kinds(
        book, stats, prev_signal="WAIT", prev_all_time_min=None, cfg=cfg
    )
    assert "new_low" not in out


def test_new_low_does_not_fire_when_current_equals_prev_min(
    transient_book, transient_stats,
):
    cfg = RecommendationConfig()
    book = transient_book()
    stats = transient_stats(observation_count=5, current_best_total_minor=1000)
    out, _ = detect_alert_kinds(
        book, stats, prev_signal="WAIT", prev_all_time_min=1000, cfg=cfg
    )
    assert "new_low" not in out


def test_no_alerts_when_current_best_none(transient_book, transient_stats):
    cfg = RecommendationConfig()
    book = transient_book(target_price_minor=1000)
    stats = transient_stats(observation_count=20, current_best_total_minor=None)
    out, _ = detect_alert_kinds(
        book, stats, prev_signal=None, prev_all_time_min=500, cfg=cfg
    )
    assert out == []


def test_multiple_kinds_can_fire_together(transient_book, transient_stats):
    cfg = RecommendationConfig()
    book = transient_book(target_price_minor=1500)
    # current=800 <= target=1500 -> target_hit
    # prev_all_time_min=1000, current=800 < 1000 -> new_low
    # observation_count=5 -> INSUFFICIENT_DATA -> percentile_cross suppressed
    stats = transient_stats(observation_count=5, current_best_total_minor=800)
    out, _ = detect_alert_kinds(
        book, stats, prev_signal="WAIT", prev_all_time_min=1000, cfg=cfg
    )
    assert {"target_hit", "new_low"} <= set(out)
    assert "percentile_cross" not in out


def test_target_hit_with_no_target_set(transient_book, transient_stats):
    cfg = RecommendationConfig()
    book = transient_book(target_price_minor=None)
    stats = transient_stats(observation_count=5, current_best_total_minor=10)
    out, _ = detect_alert_kinds(
        book, stats, prev_signal=None, prev_all_time_min=None, cfg=cfg
    )
    assert "target_hit" not in out


def test_detect_alert_kinds_returns_current_signal(
    transient_book, transient_stats,
):
    cfg = RecommendationConfig()
    book = transient_book(target_price_minor=1000)
    stats = transient_stats(observation_count=20, current_best_total_minor=900)
    _, cur = detect_alert_kinds(
        book, stats, prev_signal=None, prev_all_time_min=None, cfg=cfg,
    )
    assert cur == "TARGET_HIT"
