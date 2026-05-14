"""Unit tests for detect_alert_kinds — pure logic, no DB."""

from __future__ import annotations

from datetime import UTC, datetime

from book_alerter.alerts import detect_alert_kinds
from book_alerter.config import RecommendationConfig
from book_alerter.db import models
from book_alerter.stats import BookStats


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


def test_target_hit_fires_when_current_at_or_below_target():
    cfg = RecommendationConfig()
    book = _book(target_price_minor=1000)
    # Use observation_count below threshold so compute_signal -> INSUFFICIENT_DATA,
    # which prevents percentile_cross from firing and isolates the target_hit path.
    stats_eq = _stats(observation_count=5, current_best_total_minor=1000)
    out = detect_alert_kinds(book, stats_eq, prev_signal=None, prev_all_time_min=None, cfg=cfg)
    assert "target_hit" in out
    assert "percentile_cross" not in out

    stats_below = _stats(observation_count=5, current_best_total_minor=500)
    out2 = detect_alert_kinds(book, stats_below, prev_signal=None, prev_all_time_min=None, cfg=cfg)
    assert "target_hit" in out2
    assert "percentile_cross" not in out2


def test_target_hit_does_not_double_fire():
    cfg = RecommendationConfig()
    book = _book(target_price_minor=1000)
    stats = _stats(observation_count=5, current_best_total_minor=900)
    out = detect_alert_kinds(
        book, stats, prev_signal="TARGET_HIT", prev_all_time_min=None, cfg=cfg
    )
    assert "target_hit" not in out


def test_percentile_cross_fires_on_buy_transition():
    cfg = RecommendationConfig()
    book = _book()  # no target
    # sorted_totals=[100,200,300,400,500] -> p25=200; current=200 -> BUY
    stats = _stats(
        observation_count=20,
        current_best_total_minor=200,
        p50_total_minor=300,
        sorted_totals=[100, 200, 300, 400, 500],
    )
    out = detect_alert_kinds(
        book, stats, prev_signal="WATCH", prev_all_time_min=None, cfg=cfg
    )
    assert "percentile_cross" in out


def test_percentile_cross_does_not_fire_when_already_in_buy():
    cfg = RecommendationConfig()
    book = _book()
    stats = _stats(
        observation_count=20,
        current_best_total_minor=200,
        p50_total_minor=300,
        sorted_totals=[100, 200, 300, 400, 500],
    )
    out = detect_alert_kinds(
        book, stats, prev_signal="BUY", prev_all_time_min=None, cfg=cfg
    )
    assert "percentile_cross" not in out


def test_new_low_fires_when_current_below_prev_all_time_min():
    cfg = RecommendationConfig()
    book = _book()
    # observation_count below threshold -> INSUFFICIENT_DATA -> percentile_cross suppressed
    stats = _stats(observation_count=5, current_best_total_minor=800)
    out = detect_alert_kinds(
        book, stats, prev_signal="WAIT", prev_all_time_min=1000, cfg=cfg
    )
    assert "new_low" in out


def test_new_low_does_not_fire_when_no_prev_min():
    cfg = RecommendationConfig()
    book = _book()
    stats = _stats(observation_count=5, current_best_total_minor=800)
    out = detect_alert_kinds(
        book, stats, prev_signal="WAIT", prev_all_time_min=None, cfg=cfg
    )
    assert "new_low" not in out


def test_new_low_does_not_fire_when_current_equals_prev_min():
    cfg = RecommendationConfig()
    book = _book()
    stats = _stats(observation_count=5, current_best_total_minor=1000)
    out = detect_alert_kinds(
        book, stats, prev_signal="WAIT", prev_all_time_min=1000, cfg=cfg
    )
    assert "new_low" not in out


def test_no_alerts_when_current_best_none():
    cfg = RecommendationConfig()
    book = _book(target_price_minor=1000)
    stats = _stats(observation_count=20, current_best_total_minor=None)
    out = detect_alert_kinds(
        book, stats, prev_signal=None, prev_all_time_min=500, cfg=cfg
    )
    assert out == []


def test_multiple_kinds_can_fire_together():
    cfg = RecommendationConfig()
    book = _book(target_price_minor=1500)
    # current=800 <= target=1500 -> target_hit
    # prev_all_time_min=1000, current=800 < 1000 -> new_low
    # observation_count=5 -> INSUFFICIENT_DATA -> percentile_cross suppressed
    stats = _stats(observation_count=5, current_best_total_minor=800)
    out = detect_alert_kinds(
        book, stats, prev_signal="WAIT", prev_all_time_min=1000, cfg=cfg
    )
    assert {"target_hit", "new_low"} <= set(out)
    assert "percentile_cross" not in out


def test_target_hit_with_no_target_set():
    cfg = RecommendationConfig()
    book = _book(target_price_minor=None)
    stats = _stats(observation_count=5, current_best_total_minor=10)
    out = detect_alert_kinds(
        book, stats, prev_signal=None, prev_all_time_min=None, cfg=cfg
    )
    assert "target_hit" not in out
